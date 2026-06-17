"""
Send demo /slice/set OSC messages to blender_osc_slice.py.

The input is a tab-separated file with these columns:

    object_index radial_sample_count normal_x normal_y normal_z distance

Each row is sent as:

    /slice/set object_index radial_sample_count normal_x normal_y normal_z distance
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from oscpy.client import OSCClient
from oscpy.server import OSCThreadServer


DEFAULT_TSV = Path(__file__).with_name("slice_demo.tsv")
DEFAULT_BLENDER_HOST = "127.0.0.1"
DEFAULT_BLENDER_PORT = 9000
DEFAULT_LISTEN_HOST = "0.0.0.0"
DEFAULT_LISTEN_PORT = 9001
DEFAULT_INTERVAL_SECONDS = 0.1
DEFAULT_LINGER_SECONDS = 1.0


@dataclass(frozen=True)
class SliceMessage:
    object_index: int
    radial_sample_count: int
    normal: tuple[float, float, float]
    distance: float

    def osc_payload(self) -> list[float | int]:
        return [
            self.object_index,
            self.radial_sample_count,
            self.normal[0],
            self.normal[1],
            self.normal[2],
            self.distance,
        ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send tab-separated /slice/set messages to Blender."
    )
    parser.add_argument(
        "tsv",
        nargs="?",
        type=Path,
        default=DEFAULT_TSV,
        help=f"TSV path. Defaults to {DEFAULT_TSV.name}.",
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
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_SECONDS,
        help=f"Seconds between messages. Defaults to {DEFAULT_INTERVAL_SECONDS}.",
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

    messages = load_slice_messages(args.tsv)
    if not messages:
        print(f"No slice messages found in {args.tsv}", file=sys.stderr)
        return 1

    server = OSCThreadServer()
    server.listen(address=args.listen_host, port=args.listen_port, default=True)
    server.bind(b"/slice/radii", print_slice_radii)
    server.bind(b"/osc/error", print_osc_error)

    client = OSCClient(args.host, args.port)
    print(
        f"Listening on {args.listen_host}:{args.listen_port} for "
        "/slice/radii and /osc/error"
    )
    print(
        f"Sending {len(messages)} /slice/set messages to "
        f"{args.host}:{args.port} every {args.interval:g}s"
    )

    try:
        for index, message in enumerate(messages, start=1):
            payload = message.osc_payload()
            client.send_message(b"/slice/set", payload)
            print_sent_message(index, len(messages), message)

            if index < len(messages):
                time.sleep(args.interval)

        if args.linger > 0.0:
            time.sleep(args.linger)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.stop_all()

    return 0


def print_sent_message(index: int, total: int, message: SliceMessage) -> None:
    print(
        f"{index:02d}/{total:02d} "
        f"sent /slice/set object={message.object_index} "
        f"samples={message.radial_sample_count} "
        f"normal=({message.normal[0]:.6f}, "
        f"{message.normal[1]:.6f}, {message.normal[2]:.6f}) "
        f"distance={message.distance:.6f}",
        flush=True,
    )


def print_slice_radii(*args: Any) -> None:
    if len(args) < 2:
        print(f"Unexpected /slice/radii payload: {args}", file=sys.stderr)
        return

    object_index = _as_int(args[0])
    radii = [_as_float(value) for value in args[1:]]
    formatted_radii = "\t".join(f"{radius:.6f}" for radius in radii)
    print(
        f"received /slice/radii object={object_index} count={len(radii)}\t"
        f"{formatted_radii}",
        flush=True,
    )


def print_osc_error(*args: Any) -> None:
    message = " ".join(_as_text(arg) for arg in args)
    print(f"received /osc/error {message}", file=sys.stderr, flush=True)


def _as_int(value: Any) -> int:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    return int(value)


def _as_float(value: Any) -> float:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    return float(value)


def _as_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def load_slice_messages(path: Path) -> list[SliceMessage]:
    with path.open("r", encoding="utf-8", newline="") as tsv_file:
        rows = csv.reader(tsv_file, delimiter="\t")
        return [message for message in _parse_rows(rows, path)]


def _parse_rows(
    rows: Iterable[list[str]],
    path: Path,
) -> Iterable[SliceMessage]:
    for line_number, row in enumerate(rows, start=1):
        if not row or _is_ignored_row(row):
            continue

        if _is_header_row(row):
            continue

        if len(row) != 6:
            raise ValueError(
                f"{path}:{line_number}: expected 6 tab-separated values, got {len(row)}"
            )

        try:
            radial_sample_count = int(row[1])
            if radial_sample_count <= 0:
                raise ValueError("radial_sample_count must be positive")

            yield SliceMessage(
                object_index=int(row[0]),
                radial_sample_count=radial_sample_count,
                normal=(float(row[2]), float(row[3]), float(row[4])),
                distance=float(row[5]),
            )
        except ValueError as exc:
            raise ValueError(f"{path}:{line_number}: invalid numeric value") from exc


def _is_ignored_row(row: list[str]) -> bool:
    return len(row) == 1 and (not row[0].strip() or row[0].lstrip().startswith("#"))


def _is_header_row(row: list[str]) -> bool:
    return [value.strip().lower() for value in row] == [
        "object_index",
        "radial_sample_count",
        "normal_x",
        "normal_y",
        "normal_z",
        "distance",
    ]


if __name__ == "__main__":
    raise SystemExit(main())
