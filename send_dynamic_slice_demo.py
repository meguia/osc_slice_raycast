"""
Send a dynamic /show + /slice/set demo to blender_slice_osc_visual_dynamical.py.

The TSV is an event schedule with these actions:

    show        object_index visible
    start_slice object_index duration_seconds frame_interval_seconds frequency_hz
                radial_sample_count base_normal_xyz axis_xyz distance
    wait        duration_seconds

During each wait, all currently active slice streams send /slice/set frames.
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


DEFAULT_TSV = Path(__file__).with_name("slice_dynamic_four_objects.tsv")
DEFAULT_BLENDER_HOST = "127.0.0.1"
DEFAULT_BLENDER_PORT = 9000
DEFAULT_LISTEN_HOST = "0.0.0.0"
DEFAULT_LISTEN_PORT = 9001
DEFAULT_FRAME_INTERVAL_SECONDS = 0.05
DEFAULT_LINGER_SECONDS = 1.0
_message_id = 0


@dataclass(frozen=True)
class ShowEvent:
    object_index: int
    visible: bool


@dataclass(frozen=True)
class WaitEvent:
    duration_seconds: float


@dataclass(frozen=True)
class StartSliceEvent:
    object_index: int
    duration_seconds: float
    frame_interval_seconds: float
    frequency_hz: float
    radial_sample_count: int
    base_normal: tuple[float, float, float]
    axis: tuple[float, float, float]
    distance: float


@dataclass
class ActiveSlice:
    object_index: int
    frame_interval_seconds: float
    frequency_hz: float
    radial_sample_count: int
    base_normal: tuple[float, float, float]
    axis: tuple[float, float, float]
    distance: float
    elapsed_seconds: float = 0.0

    def osc_payload(self, message_id: int) -> list[float | int]:
        angle = math.tau * self.frequency_hz * self.elapsed_seconds
        normal = rotate_vector(self.base_normal, self.axis, angle)
        return [
            message_id,
            self.object_index,
            self.radial_sample_count,
            normal[0],
            normal[1],
            normal[2],
            self.distance,
        ]


Event = ShowEvent | WaitEvent | StartSliceEvent


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send /show and animated /slice/set messages to Blender."
    )
    parser.add_argument(
        "tsv",
        nargs="?",
        type=Path,
        default=DEFAULT_TSV,
        help=f"Dynamic demo TSV. Defaults to {DEFAULT_TSV.name}.",
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
            "Fallback seconds between /slice/set frames during wait events. "
            f"Defaults to {DEFAULT_FRAME_INTERVAL_SECONDS}."
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
            "Seconds to keep listening after the final event. "
            f"Defaults to {DEFAULT_LINGER_SECONDS}."
        ),
    )
    parser.add_argument(
        "--no-listen",
        action="store_true",
        help="Only send messages; do not listen for /slice/radii replies.",
    )
    parser.add_argument(
        "--preload",
        action="store_true",
        help="Send /preload for all object indices used by the TSV before starting.",
    )
    parser.add_argument(
        "--preload-wait",
        type=float,
        default=5.0,
        help="Seconds to wait after /preload before the first event. Defaults to 5.",
    )
    args = parser.parse_args()

    events = load_events(args.tsv)
    if not events:
        print(f"No events found in {args.tsv}", file=sys.stderr)
        return 1
    if args.frame_interval <= 0.0:
        print("--frame-interval must be positive", file=sys.stderr)
        return 1

    server = None
    if not args.no_listen:
        server = OSCThreadServer()
        server.listen(address=args.listen_host, port=args.listen_port, default=True)
        server.bind(b"/slice/radii", print_slice_radii)
        server.bind(b"/osc/error", print_osc_error)

    client = OSCClient(args.host, args.port)
    active_slices: dict[int, ActiveSlice] = {}

    if server is None:
        print("Reply listener disabled")
    else:
        print(
            f"Listening on {args.listen_host}:{args.listen_port} for "
            "/slice/radii and /osc/error"
        )
    print(f"Sending {len(events)} dynamic events from {args.tsv} to {args.host}:{args.port}")


    if args.preload:
        indices = event_object_indices(events)
        client.send_message(b"/preload", indices)
        print(f"Sent /preload for objects {indices}; waiting {args.preload_wait:g}s")
        if args.preload_wait > 0.0:
            time.sleep(args.preload_wait)
    try:
        for index, event in enumerate(events, start=1):
            if isinstance(event, ShowEvent):
                send_show(client, event, active_slices, index, len(events))
            elif isinstance(event, StartSliceEvent):
                start_slice(event, active_slices, index, len(events))
                run_wait(
                    client,
                    WaitEvent(event.duration_seconds),
                    active_slices,
                    event.frame_interval_seconds,
                    index,
                    len(events),
                )
            elif isinstance(event, WaitEvent):
                run_wait(
                    client,
                    event,
                    active_slices,
                    args.frame_interval,
                    index,
                    len(events),
                )

        if args.linger > 0.0:
            time.sleep(args.linger)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        if server is not None:
            server.stop_all()

    return 0


def next_message_id() -> int:
    global _message_id
    _message_id += 1
    return _message_id
def event_object_indices(events: list[Event]) -> list[int]:
    return sorted(
        {event.object_index for event in events if hasattr(event, "object_index")}
    )


def send_show(
    client: OSCClient,
    event: ShowEvent,
    active_slices: dict[int, ActiveSlice],
    event_index: int,
    event_count: int,
) -> None:
    visible_int = 1 if event.visible else 0
    client.send_message(b"/show", [event.object_index, visible_int])
    if not event.visible:
        active_slices.pop(event.object_index, None)
    print(
        f"event {event_index:02d}/{event_count:02d} "
        f"sent /show object={event.object_index} visible={visible_int}",
        flush=True,
    )


def start_slice(
    event: StartSliceEvent,
    active_slices: dict[int, ActiveSlice],
    event_index: int,
    event_count: int,
) -> None:
    active_slices[event.object_index] = ActiveSlice(
        object_index=event.object_index,
        frame_interval_seconds=event.frame_interval_seconds,
        frequency_hz=event.frequency_hz,
        radial_sample_count=event.radial_sample_count,
        base_normal=normalize(event.base_normal),
        axis=normalize(event.axis),
        distance=event.distance,
    )
    print(
        f"event {event_index:02d}/{event_count:02d} "
        f"started slice object={event.object_index} "
        f"duration={event.duration_seconds:g}s "
        f"freq={event.frequency_hz:g}Hz samples={event.radial_sample_count} "
        f"axis=({event.axis[0]:.6f}, {event.axis[1]:.6f}, {event.axis[2]:.6f}) "
        f"distance={event.distance:.6f}",
        flush=True,
    )


def run_wait(
    client: OSCClient,
    event: WaitEvent,
    active_slices: dict[int, ActiveSlice],
    fallback_frame_interval: float,
    event_index: int,
    event_count: int,
) -> None:
    if event.duration_seconds <= 0.0:
        return

    frame_interval = min(
        [
            interval
            for interval in (
                fallback_frame_interval,
                *(slice_event_frame_intervals(active_slices.values())),
            )
            if interval > 0.0
        ]
    )
    frame_count = max(1, math.ceil(event.duration_seconds / frame_interval))
    actual_interval = event.duration_seconds / frame_count

    print(
        f"event {event_index:02d}/{event_count:02d} "
        f"wait {event.duration_seconds:g}s with {len(active_slices)} active object(s), "
        f"{frame_count} frame(s)",
        flush=True,
    )

    for frame_index in range(frame_count):
        frame_start = time.perf_counter()
        for active_slice in list(active_slices.values()):
            payload = active_slice.osc_payload(next_message_id())
            client.send_message(b"/slice/set", payload)
            active_slice.elapsed_seconds += actual_interval

        if frame_index < frame_count - 1:
            sleep_remaining(actual_interval, frame_start)


def slice_event_frame_intervals(active_slices: Any) -> list[float]:
    return [active_slice.frame_interval_seconds for active_slice in active_slices]


def sleep_remaining(interval: float, start_time: float) -> None:
    elapsed = time.perf_counter() - start_time
    remaining = interval - elapsed
    if remaining > 0.0:
        time.sleep(remaining)


def load_events(path: Path) -> list[Event]:
    with path.open("r", encoding="utf-8", newline="") as tsv_file:
        reader = csv.DictReader(tsv_file, delimiter="\t")
        return [parse_event(row, path, line_number) for line_number, row in enumerate(reader, 2)]


def parse_event(row: dict[str, str], path: Path, line_number: int) -> Event:
    action = required_text(row, "action", path, line_number).lower()

    if action == "show":
        return ShowEvent(
            object_index=required_int(row, "object_index", path, line_number),
            visible=bool(required_int(row, "visible", path, line_number)),
        )

    if action == "wait":
        return WaitEvent(
            duration_seconds=required_float(row, "duration_seconds", path, line_number)
        )

    if action == "start_slice":
        duration_seconds = required_float(row, "duration_seconds", path, line_number)
        frequency_hz = required_float(row, "frequency_hz", path, line_number)
        radial_sample_count = required_int(row, "radial_sample_count", path, line_number)
        if duration_seconds <= 0.0:
            raise ValueError(f"{path}:{line_number}: duration_seconds must be positive")
        if frequency_hz <= 0.0:
            raise ValueError(f"{path}:{line_number}: frequency_hz must be positive")
        if radial_sample_count <= 0:
            raise ValueError(f"{path}:{line_number}: radial_sample_count must be positive")

        frame_interval = optional_float(
            row,
            "frame_interval_seconds",
            DEFAULT_FRAME_INTERVAL_SECONDS,
        )
        if frame_interval <= 0.0:
            raise ValueError(f"{path}:{line_number}: frame_interval_seconds must be positive")


        return StartSliceEvent(
            object_index=required_int(row, "object_index", path, line_number),
            duration_seconds=duration_seconds,
            frame_interval_seconds=frame_interval,
            frequency_hz=frequency_hz,
            radial_sample_count=radial_sample_count,
            base_normal=(
                required_float(row, "base_normal_x", path, line_number),
                required_float(row, "base_normal_y", path, line_number),
                required_float(row, "base_normal_z", path, line_number),
            ),
            axis=(
                required_float(row, "axis_x", path, line_number),
                required_float(row, "axis_y", path, line_number),
                required_float(row, "axis_z", path, line_number),
            ),
            distance=required_float(row, "distance", path, line_number),
        )

    raise ValueError(f"{path}:{line_number}: unknown action {action!r}")


def required_text(row: dict[str, str], key: str, path: Path, line_number: int) -> str:
    value = row.get(key, "").strip()
    if not value:
        raise ValueError(f"{path}:{line_number}: missing {key}")
    return value


def required_int(row: dict[str, str], key: str, path: Path, line_number: int) -> int:
    try:
        return int(required_text(row, key, path, line_number))
    except ValueError as exc:
        raise ValueError(f"{path}:{line_number}: invalid integer for {key}") from exc


def required_float(row: dict[str, str], key: str, path: Path, line_number: int) -> float:
    try:
        return float(required_text(row, key, path, line_number))
    except ValueError as exc:
        raise ValueError(f"{path}:{line_number}: invalid float for {key}") from exc


def optional_float(row: dict[str, str], key: str, default: float) -> float:
    value = row.get(key, "").strip()
    return default if not value else float(value)


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


def print_slice_radii(*args: Any) -> None:
    if len(args) < 2:
        print(f"Unexpected /slice/radii payload: {args}", file=sys.stderr)
        return

    message_id = _as_int(args[0])
    object_index = _as_int(args[1])
    radii_count = len(args) - 2
    print(
        f"received /slice/radii id={message_id} object={object_index} count={radii_count}",
        flush=True,
    )


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
