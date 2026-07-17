"""Minimal end-to-end OSC test for the slicer and visualizer.

The interaction mirrors the useful part of ``concert-paleolithics.scd``:
one channel/slot requests a loop for one object while the same cutting plane is
shown in Blender.  This script deliberately does not reproduce the concert.

For every plane in a short rotation it sends:

    slicer:     /slice/get message_id object_id samples nx ny nz distance
    visualizer: /slice/set slot_id object_id nx ny nz distance

It listens for and validates:

    /slice/radii message_id object_id radius_0 ... radius_N_minus_1
"""

from __future__ import annotations

import argparse
import math
import queue
import sys
import time
from dataclasses import dataclass
from typing import Any

from oscpy.client import OSCClient
from oscpy.server import OSCThreadServer


DEFAULT_HOST = "127.0.0.1"
DEFAULT_SLICER_PORT = 9000
DEFAULT_REPLY_HOST = "0.0.0.0"
DEFAULT_REPLY_PORT = 9001
DEFAULT_VISUALIZER_PORT = 9005


@dataclass(frozen=True)
class SliceReply:
    message_id: int
    object_id: int
    radii: tuple[float, ...]


class ReplyInbox:
    """Thread-safe handoff from the OSC callback to the demo loop."""

    def __init__(self) -> None:
        self._items: "queue.Queue[SliceReply | ValueError]" = queue.Queue()

    def receive(self, *args: Any) -> None:
        try:
            if len(args) < 2:
                raise ValueError(
                    f"/slice/radii needs message_id and object_id; received {args!r}"
                )
            self._items.put(
                SliceReply(
                    message_id=_as_int(args[0]),
                    object_id=_as_int(args[1]),
                    radii=tuple(_as_float(value) for value in args[2:]),
                )
            )
        except (TypeError, ValueError) as exc:
            self._items.put(ValueError(f"invalid /slice/radii reply: {exc}"))

    def wait_for(self, message_id: int, timeout: float) -> SliceReply:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                raise TimeoutError(
                    f"no /slice/radii reply for message {message_id} within {timeout:g}s"
                )
            try:
                item = self._items.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError(
                    f"no /slice/radii reply for message {message_id} within {timeout:g}s"
                ) from exc

            if isinstance(item, ValueError):
                raise item
            if item.message_id == message_id:
                return item

            print(
                f"Ignoring unrelated /slice/radii message id={item.message_id}",
                file=sys.stderr,
                flush=True,
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Test the headless slice server and proxy visualizer with one rotating "
            "cutting plane."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="Host of both Blender processes",
    )
    parser.add_argument(
        "--slicer-port",
        type=int,
        default=DEFAULT_SLICER_PORT,
        help="Port receiving /slice/get",
    )
    parser.add_argument(
        "--visualizer-port",
        type=int,
        default=DEFAULT_VISUALIZER_PORT,
        help="Port receiving visualizer messages",
    )
    parser.add_argument(
        "--reply-host",
        default=DEFAULT_REPLY_HOST,
        help="Local interface on which to receive /slice/radii",
    )
    parser.add_argument(
        "--reply-port",
        type=int,
        default=DEFAULT_REPLY_PORT,
        help="Local port on which to receive /slice/radii",
    )
    parser.add_argument(
        "--object-id",
        type=int,
        default=0,
        help="Object slice_index",
    )
    parser.add_argument(
        "--slot-id",
        type=int,
        default=0,
        help="Visualizer slot/channel",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=1024,
        help="Radii requested from the sonification server",
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=16,
        help="Number of cutting planes in one full rotation",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.1,
        help="Minimum seconds between requests",
    )
    parser.add_argument(
        "--reply-timeout",
        type=float,
        default=2.0,
        help="Seconds to wait for each server reply",
    )
    parser.add_argument(
        "--distance",
        type=float,
        default=0.0,
        help="Signed cutting-plane distance from the object origin",
    )
    parser.add_argument(
        "--print-radii",
        action="store_true",
        help="Print every returned radius instead of summary statistics",
    )
    parser.add_argument(
        "--hide-at-end",
        action="store_true",
        help="Hide the visualizer slot after a successful test",
    )
    args = parser.parse_args()

    if args.samples <= 0:
        parser.error("--samples must be greater than zero")
    if args.frames <= 0:
        parser.error("--frames must be greater than zero")
    if args.interval < 0.0:
        parser.error("--interval cannot be negative")
    if args.reply_timeout <= 0.0:
        parser.error("--reply-timeout must be greater than zero")
    if not 0 <= args.slot_id < 20:
        parser.error("--slot-id must be between 0 and 19")
    return args


def main() -> int:
    args = parse_args()
    inbox = ReplyInbox()
    listener = OSCThreadServer()

    try:
        listener.listen(
            address=args.reply_host,
            port=args.reply_port,
            default=True,
        )
        listener.bind(b"/slice/radii", inbox.receive)
    except OSError as exc:
        print(
            f"Cannot listen for replies on {args.reply_host}:{args.reply_port}: {exc}",
            file=sys.stderr,
        )
        return 1

    slicer = OSCClient(args.host, args.slicer_port)
    visualizer = OSCClient(args.host, args.visualizer_port)
    latencies: list[float] = []

    print(
        f"Listening for /slice/radii on {args.reply_host}:{args.reply_port}\n"
        f"Testing object {args.object_id} in visual slot {args.slot_id}: "
        f"{args.frames} frames, {args.samples} radii per frame"
    )

    try:
        visualizer.send_message(b"/preload", [args.object_id])
        next_frame_at = time.monotonic()

        for frame_index in range(args.frames):
            _wait_until(next_frame_at)
            normal = _rotation_normal(frame_index, args.frames)
            message_id = frame_index + 1

            visualizer.send_message(
                b"/slice/set",
                [args.slot_id, args.object_id, *normal, args.distance],
            )
            if frame_index == 0:
                visualizer.send_message(b"/slice/show", [args.slot_id, 1])

            started = time.monotonic()
            slicer.send_message(
                b"/slice/get",
                [message_id, args.object_id, args.samples, *normal, args.distance],
            )
            reply = inbox.wait_for(message_id, args.reply_timeout)
            latency = time.monotonic() - started
            _validate_reply(reply, args.object_id, args.samples)
            latencies.append(latency)
            _print_reply(
                frame_index,
                args.frames,
                reply,
                normal,
                latency,
                args.print_radii,
            )
            next_frame_at = max(next_frame_at + args.interval, time.monotonic())

        if args.hide_at_end:
            visualizer.send_message(b"/slice/show", [args.slot_id, 0])
    except KeyboardInterrupt:
        print("\nTest interrupted.", file=sys.stderr)
        return 130
    except (OSError, TimeoutError, ValueError) as exc:
        print(f"TEST FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        listener.stop_all()

    print(
        f"TEST PASSED: received {len(latencies)}/{args.frames} matching replies; "
        f"latency min={min(latencies) * 1000:.1f}ms "
        f"max={max(latencies) * 1000:.1f}ms"
    )
    return 0


def _rotation_normal(
    frame_index: int,
    frame_count: int,
) -> tuple[float, float, float]:
    """Rotate [0, 1, 0] once around X, as in the small SC scan examples."""
    phase_divisor = max(frame_count - 1, 1)
    angle = math.tau * frame_index / phase_divisor
    return (0.0, math.cos(angle), math.sin(angle))


def _wait_until(deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining > 0.0:
        time.sleep(remaining)


def _validate_reply(reply: SliceReply, object_id: int, sample_count: int) -> None:
    if reply.object_id != object_id:
        raise ValueError(
            f"message {reply.message_id} returned object {reply.object_id}, "
            f"expected {object_id}"
        )
    if len(reply.radii) != sample_count:
        raise ValueError(
            f"message {reply.message_id} returned {len(reply.radii)} radii, "
            f"expected {sample_count}"
        )
    if not all(math.isfinite(radius) and radius >= 0.0 for radius in reply.radii):
        raise ValueError(
            f"message {reply.message_id} returned a negative or non-finite radius"
        )


def _print_reply(
    frame_index: int,
    frame_count: int,
    reply: SliceReply,
    normal: tuple[float, float, float],
    latency: float,
    print_radii: bool,
) -> None:
    prefix = (
        f"{frame_index + 1:02d}/{frame_count:02d} "
        f"id={reply.message_id} normal=({normal[0]:+.3f}, "
        f"{normal[1]:+.3f}, {normal[2]:+.3f}) "
        f"radii={len(reply.radii)} latency={latency * 1000:.1f}ms"
    )
    if print_radii:
        values = " ".join(f"{radius:.6f}" for radius in reply.radii)
        print(f"{prefix}\n  {values}", flush=True)
    else:
        print(
            f"{prefix} min={min(reply.radii):.6f} max={max(reply.radii):.6f}",
            flush=True,
        )


def _as_int(value: Any) -> int:
    return int(value.decode("utf-8") if isinstance(value, bytes) else value)


def _as_float(value: Any) -> float:
    return float(value.decode("utf-8") if isinstance(value, bytes) else value)


if __name__ == "__main__":
    raise SystemExit(main())
