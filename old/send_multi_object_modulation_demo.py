"""
Send the first normal-modulation section of slice_demo.tsv to several objects.

For each source row with distance == 0, this takes the angle between that
source normal and the initial normal, then rotates the initial normal around a
different perpendicular axis for each target object.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oscpy.client import OSCClient
from oscpy.server import OSCThreadServer


DEFAULT_TSV = Path(__file__).with_name("slice_demo.tsv")
DEFAULT_OBJECTS = (0, 2, 3, 6, 9, 12, 18)
DEFAULT_BLENDER_HOST = "127.0.0.1"
DEFAULT_BLENDER_PORT = 9000
DEFAULT_LISTEN_HOST = "0.0.0.0"
DEFAULT_LISTEN_PORT = 9001
DEFAULT_FRAME_INTERVAL_SECONDS = 0.05
DEFAULT_OBJECT_INTERVAL_SECONDS = 0.0
DEFAULT_LINGER_SECONDS = 1.0
DEFAULT_RATE_MULTIPLIERS = (0.5, 0.7, 0.9, 1.0, 1.25, 1.5, 2.0)
DEFAULT_MODULATION_SPEED = 0.25
DEFAULT_CYCLES = 4
DEFAULT_SAMPLE_COUNT = 128


@dataclass(frozen=True)
class ModulationFrame:
    radial_sample_count: int
    normal: tuple[float, float, float]
    distance: float


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send a normal-vector modulation to multiple slice objects."
    )
    parser.add_argument(
        "--source-tsv",
        type=Path,
        default=DEFAULT_TSV,
        help=f"Source TSV. Defaults to {DEFAULT_TSV.name}.",
    )
    parser.add_argument(
        "--objects",
        default=",".join(str(index) for index in DEFAULT_OBJECTS),
        help="Comma-separated object indices. Defaults to 0,2,3,6,9,12,18.",
    )
    parser.add_argument(
        "--rate-multipliers",
        default=",".join(str(rate) for rate in DEFAULT_RATE_MULTIPLIERS),
        help=(
            "Comma-separated modulation-rate multipliers, one per object. "
            "Defaults to 0.5,0.7,0.9,1.0,1.25,1.5,2.0."
        ),
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_BLENDER_HOST,
        help=f"Blender OSC host. Defaults to {DEFAULT_BLENDER_HOST}.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_BLENDER_PORT,
        help=f"Blender OSC port. Defaults to {DEFAULT_BLENDER_PORT}.",
    )
    parser.add_argument(
        "--frame-interval",
        type=float,
        default=DEFAULT_FRAME_INTERVAL_SECONDS,
        help=(
            "Seconds between modulation frames. "
            f"Defaults to {DEFAULT_FRAME_INTERVAL_SECONDS}."
        ),
    )
    parser.add_argument(
        "--modulation-speed",
        type=float,
        default=DEFAULT_MODULATION_SPEED,
        help=(
            "Source modulation frames advanced per output frame before "
            f"per-object rate multipliers. Defaults to {DEFAULT_MODULATION_SPEED}."
        ),
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=DEFAULT_CYCLES,
        help=(
            "Number of base modulation cycles to send. "
            f"Defaults to {DEFAULT_CYCLES}."
        ),
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=DEFAULT_SAMPLE_COUNT,
        help=f"Radial sample count to request. Defaults to {DEFAULT_SAMPLE_COUNT}.",
    )
    parser.add_argument(
        "--object-interval",
        type=float,
        default=DEFAULT_OBJECT_INTERVAL_SECONDS,
        help=(
            "Seconds between objects inside one frame. "
            f"Defaults to {DEFAULT_OBJECT_INTERVAL_SECONDS}."
        ),
    )
    parser.add_argument(
        "--listen-host",
        default=DEFAULT_LISTEN_HOST,
        help=f"Host for replies from Blender. Defaults to {DEFAULT_LISTEN_HOST}.",
    )
    parser.add_argument(
        "--listen-port",
        type=int,
        default=DEFAULT_LISTEN_PORT,
        help=f"Port for replies from Blender. Defaults to {DEFAULT_LISTEN_PORT}.",
    )
    parser.add_argument(
        "--linger",
        type=float,
        default=DEFAULT_LINGER_SECONDS,
        help=(
            "Seconds to keep listening after the last message. "
            f"Defaults to {DEFAULT_LINGER_SECONDS}."
        ),
    )
    args = parser.parse_args()

    object_indices = parse_object_indices(args.objects)
    rate_multipliers = parse_rate_multipliers(args.rate_multipliers, len(object_indices))
    frames = load_first_normal_modulation(args.source_tsv)

    if not frames:
        print(f"No distance=0 modulation frames found in {args.source_tsv}", file=sys.stderr)
        return 1
    if args.cycles <= 0:
        print("--cycles must be positive", file=sys.stderr)
        return 1
    if args.modulation_speed <= 0.0:
        print("--modulation-speed must be positive", file=sys.stderr)
        return 1
    if args.samples <= 0:
        print("--samples must be positive", file=sys.stderr)
        return 1

    server = OSCThreadServer()
    server.listen(address=args.listen_host, port=args.listen_port, default=True)
    server.bind(b"/slice/radii", print_slice_radii)
    server.bind(b"/osc/error", print_osc_error)

    client = OSCClient(args.host, args.port)
    base_normal = normalize(frames[0].normal)
    axes = modulation_axes(len(object_indices), base_normal)
    total_frames = math.ceil((len(frames) * args.cycles) / args.modulation_speed)
    total_messages = total_frames * len(object_indices)

    print(
        f"Listening on {args.listen_host}:{args.listen_port} for "
        "/slice/radii and /osc/error"
    )
    print(
        f"Sending {total_frames} frames ({args.cycles} cycles) to objects {object_indices} "
        f"({total_messages} /slice/set messages total)"
    )
    print(
        f"  frame interval={args.frame_interval:g}s, "
        f"base modulation speed={args.modulation_speed:g} source-frames/frame"
    )
    for object_index, axis, rate in zip(object_indices, axes, rate_multipliers):
        print(
            f"  object {object_index}: rotation axis="
            f"({axis[0]:.6f}, {axis[1]:.6f}, {axis[2]:.6f}), "
            f"rate={rate:g}x"
        )

    try:
        for frame_index in range(1, total_frames + 1):
            for object_position, (object_index, axis, rate) in enumerate(
                zip(object_indices, axes, rate_multipliers),
                start=1,
            ):
                source_position = (frame_index - 1) * args.modulation_speed * rate
                mod_frame = modulation_frame_at_phase(frames, source_position)
                angle = modulation_angle_at_phase(frames, base_normal, source_position)
                normal = rotate_vector(base_normal, axis, angle)
                payload = [
                    object_index,
                    args.samples,
                    normal[0],
                    normal[1],
                    normal[2],
                    mod_frame.distance,
                ]
                client.send_message(b"/slice/set", payload)
                print_sent_message(
                    frame_index,
                    total_frames,
                    object_position,
                    object_index,
                    rate,
                    normal,
                    mod_frame,
                    args.samples,
                )

                if object_position < len(object_indices) and args.object_interval > 0.0:
                    time.sleep(args.object_interval)

            if frame_index < total_frames and args.frame_interval > 0.0:
                time.sleep(args.frame_interval)

        if args.linger > 0.0:
            time.sleep(args.linger)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.stop_all()

    return 0


def parse_object_indices(text: str) -> list[int]:
    indices = [int(part.strip()) for part in text.split(",") if part.strip()]
    if not indices:
        raise ValueError("at least one object index is required")
    return indices


def parse_rate_multipliers(text: str, object_count: int) -> list[float]:
    rates = [float(part.strip()) for part in text.split(",") if part.strip()]
    if len(rates) == 1:
        rates = rates * object_count
    if len(rates) != object_count:
        raise ValueError(
            f"expected one rate multiplier or {object_count} rate multipliers, "
            f"got {len(rates)}"
        )
    if any(rate <= 0.0 for rate in rates):
        raise ValueError("rate multipliers must be positive")
    return rates


def load_first_normal_modulation(path: Path) -> list[ModulationFrame]:
    frames: list[ModulationFrame] = []

    with path.open("r", encoding="utf-8", newline="") as tsv_file:
        rows = csv.DictReader(tsv_file, delimiter="\t")
        for row in rows:
            distance = float(row["distance"])

            if frames and abs(distance) > 1.0e-12:
                break
            if abs(distance) > 1.0e-12:
                continue

            frames.append(
                ModulationFrame(
                    radial_sample_count=int(row["radial_sample_count"]),
                    normal=(
                        float(row["normal_x"]),
                        float(row["normal_y"]),
                        float(row["normal_z"]),
                    ),
                    distance=distance,
                )
            )

    return frames


def modulation_frame_at_phase(
    frames: list[ModulationFrame],
    phase: float,
) -> ModulationFrame:
    source_index = int(math.floor(phase)) % len(frames)
    return frames[source_index]


def modulation_angle_at_phase(
    frames: list[ModulationFrame],
    base_normal: tuple[float, float, float],
    phase: float,
) -> float:
    lower_index = int(math.floor(phase)) % len(frames)
    upper_index = (lower_index + 1) % len(frames)
    fraction = phase - math.floor(phase)

    lower_angle = angle_between(base_normal, frames[lower_index].normal)
    upper_angle = angle_between(base_normal, frames[upper_index].normal)
    return lower_angle + ((upper_angle - lower_angle) * fraction)


def modulation_axes(
    count: int,
    base_normal: tuple[float, float, float],
) -> list[tuple[float, float, float]]:
    first_axis = perpendicular_axis(base_normal)
    second_axis = cross(base_normal, first_axis)
    axes = []

    for index in range(count):
        angle = math.tau * index / count
        axis = add(
            scale(first_axis, math.cos(angle)),
            scale(second_axis, math.sin(angle)),
        )
        axes.append(normalize(axis))

    return axes


def rotate_vector(
    vector: tuple[float, float, float],
    axis: tuple[float, float, float],
    angle: float,
) -> tuple[float, float, float]:
    axis = normalize(axis)
    c = math.cos(angle)
    s = math.sin(angle)

    return add(
        add(scale(vector, c), scale(cross(axis, vector), s)),
        scale(axis, dot(axis, vector) * (1.0 - c)),
    )


def angle_between(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
) -> float:
    a = normalize(a)
    b = normalize(b)
    return math.acos(max(-1.0, min(1.0, dot(a, b))))


def perpendicular_axis(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    vector = normalize(vector)
    reference = (1.0, 0.0, 0.0)
    if abs(dot(vector, reference)) > 0.95:
        reference = (0.0, 1.0, 0.0)
    return normalize(cross(vector, reference))


def normalize(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(dot(vector, vector))
    if length <= 1.0e-12:
        raise ValueError("zero-length vector")
    return scale(vector, 1.0 / length)


def dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return (a[0] * b[0]) + (a[1] * b[1]) + (a[2] * b[2])


def cross(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        (a[1] * b[2]) - (a[2] * b[1]),
        (a[2] * b[0]) - (a[0] * b[2]),
        (a[0] * b[1]) - (a[1] * b[0]),
    )


def add(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def scale(vector: tuple[float, float, float], amount: float) -> tuple[float, float, float]:
    return (vector[0] * amount, vector[1] * amount, vector[2] * amount)


def print_sent_message(
    frame_index: int,
    frame_count: int,
    object_position: int,
    object_index: int,
    rate: float,
    normal: tuple[float, float, float],
    frame: ModulationFrame,
    sample_count: int,
) -> None:
    print(
        f"frame {frame_index:03d}/{frame_count:03d} "
        f"object[{object_position}]={object_index} "
        f"rate={rate:g}x "
        f"samples={sample_count} "
        f"normal=({normal[0]:.6f}, {normal[1]:.6f}, {normal[2]:.6f}) "
        f"distance={frame.distance:.6f}",
        flush=True,
    )


def print_slice_radii(*args: Any) -> None:
    if len(args) < 2:
        print(f"Unexpected /slice/radii payload: {args}", file=sys.stderr)
        return

    object_index = _as_int(args[0])
    radii_count = len(args) - 1
    print(f"received /slice/radii object={object_index} count={radii_count}", flush=True)


def print_osc_error(*args: Any) -> None:
    message = " ".join(_as_text(arg) for arg in args)
    print(f"received /osc/error {message}", file=sys.stderr, flush=True)


def _as_int(value: Any) -> int:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    return int(value)


def _as_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
