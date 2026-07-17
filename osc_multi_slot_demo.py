"""Shared runtime for the multi-slot OSC demonstration scripts."""

from __future__ import annotations

import argparse
import math
import queue
import sys
import time
from dataclasses import dataclass
from typing import Any, Iterable

from oscpy.client import OSCClient
from oscpy.server import OSCThreadServer


DEFAULT_HOST = "127.0.0.1"
DEFAULT_SLICER_PORT = 9000
DEFAULT_REPLY_HOST = "0.0.0.0"
DEFAULT_REPLY_PORT = 9001
DEFAULT_VISUALIZER_PORT = 9005
DEFAULT_SAMPLE_COUNT = 256
DEFAULT_SERVER_RATE = 50.0
DEFAULT_VISUAL_RATE = 20.0
DEFAULT_SLOT_INTERVAL = 1.0
DEFAULT_REPLY_TIMEOUT = 2.0
TIMING_EPSILON = 1.0e-9

Vector3 = tuple[float, float, float]


@dataclass(frozen=True)
class Modulation:
    """Rotation of a base normal plus an independent distance oscillation."""

    base_normal: Vector3
    axis: Vector3
    rotation_hz: float
    phase_cycles: float = 0.0
    distance_center: float = 0.0
    distance_amplitude: float = 0.0
    distance_hz: float = 0.0
    distance_phase_cycles: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_normal", normalize(self.base_normal))
        object.__setattr__(self, "axis", normalize(self.axis))

    def plane_at(self, elapsed: float) -> tuple[Vector3, float]:
        angle = math.tau * (self.phase_cycles + self.rotation_hz * elapsed)
        normal = rotate_vector(self.base_normal, self.axis, angle)
        distance_phase = math.tau * (
            self.distance_phase_cycles + self.distance_hz * elapsed
        )
        distance = self.distance_center + (
            self.distance_amplitude * math.sin(distance_phase)
        )
        return normal, distance


@dataclass(frozen=True)
class Track:
    slot_id: int
    object_id: int
    label: str
    modulation: Modulation


@dataclass(frozen=True)
class SliceReply:
    message_id: int
    object_id: int
    radii: tuple[float, ...]
    received_at: float


class ReplyInbox:
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
                    message_id=as_int(args[0]),
                    object_id=as_int(args[1]),
                    radii=tuple(as_float(value) for value in args[2:]),
                    received_at=time.monotonic(),
                )
            )
        except (TypeError, ValueError) as exc:
            self._items.put(ValueError(f"invalid /slice/radii reply: {exc}"))

    def wait_for(
        self,
        expected: dict[int, int],
        sample_count: int,
        timeout: float,
    ) -> list[SliceReply]:
        pending = dict(expected)
        replies: list[SliceReply] = []
        deadline = time.monotonic() + timeout

        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                raise TimeoutError(_timeout_message(pending, timeout))
            try:
                item = self._items.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError(_timeout_message(pending, timeout)) from exc

            if isinstance(item, ValueError):
                raise item
            expected_object = pending.get(item.message_id)
            if expected_object is None:
                print(
                    f"Ignoring unrelated /slice/radii id={item.message_id}",
                    file=sys.stderr,
                    flush=True,
                )
                continue

            validate_reply(item, expected_object, sample_count)
            del pending[item.message_id]
            replies.append(item)

        return replies


def add_runtime_arguments(
    parser: argparse.ArgumentParser,
    *,
    default_cycles: int,
) -> None:
    parser.formatter_class = argparse.ArgumentDefaultsHelpFormatter
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="Host of both Blender processes",
    )
    parser.add_argument("--slicer-port", type=int, default=DEFAULT_SLICER_PORT)
    parser.add_argument("--visualizer-port", type=int, default=DEFAULT_VISUALIZER_PORT)
    parser.add_argument("--reply-host", default=DEFAULT_REPLY_HOST)
    parser.add_argument("--reply-port", type=int, default=DEFAULT_REPLY_PORT)
    parser.add_argument(
        "--samples",
        type=int,
        default=DEFAULT_SAMPLE_COUNT,
        help="Radii requested for every active slot",
    )
    parser.add_argument(
        "--server-rate",
        type=float,
        default=DEFAULT_SERVER_RATE,
        help="Sonification server request ticks per second",
    )
    parser.add_argument(
        "--visual-rate",
        "--frame-rate",
        dest="visual_rate",
        type=float,
        default=DEFAULT_VISUAL_RATE,
        help="Visualizer update ticks per second",
    )
    parser.add_argument(
        "--slot-interval",
        type=float,
        default=DEFAULT_SLOT_INTERVAL,
        help="Seconds between occupying or freeing consecutive slots",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=default_cycles,
        help="Number of complete occupy/free cycles",
    )
    parser.add_argument(
        "--reply-timeout",
        type=float,
        default=DEFAULT_REPLY_TIMEOUT,
        help="Seconds to wait for all replies in one server request tick",
    )


def validate_runtime_arguments(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    if args.samples <= 0:
        parser.error("--samples must be greater than zero")
    if args.server_rate <= 0.0:
        parser.error("--server-rate must be greater than zero")
    if args.visual_rate <= 0.0:
        parser.error("--visual-rate must be greater than zero")
    if args.slot_interval <= 0.0:
        parser.error("--slot-interval must be greater than zero")
    if args.cycles <= 0:
        parser.error("--cycles must be greater than zero")
    if args.reply_timeout <= 0.0:
        parser.error("--reply-timeout must be greater than zero")


def run_demo(
    title: str,
    tracks: tuple[Track, ...],
    args: argparse.Namespace,
) -> int:
    validate_tracks(tracks)
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
            f"Cannot listen on {args.reply_host}:{args.reply_port}: {exc}",
            file=sys.stderr,
        )
        return 1

    slicer = OSCClient(args.host, args.slicer_port)
    visualizer = OSCClient(args.host, args.visualizer_port)
    total_steps = len(tracks) * 2
    duration = total_steps * args.slot_interval * args.cycles
    scheduler_rate = max(args.server_rate, args.visual_rate)
    total_frames = math.ceil((duration * scheduler_rate) - TIMING_EPSILON)
    total_server_ticks = math.ceil((duration * args.server_rate) - TIMING_EPSILON)
    total_visual_ticks = math.ceil((duration * args.visual_rate) - TIMING_EPSILON)
    message_id = 0
    reply_count = 0
    latencies: list[float] = []
    visible_slots: set[int] = set()
    previous_step = -1
    previous_server_tick = -1
    previous_visual_tick = -1

    _print_configuration(title, tracks, args, duration)

    try:
        object_ids = sorted({track.object_id for track in tracks})
        visualizer.send_message(b"/preload", object_ids)
        for track in tracks:
            visualizer.send_message(b"/slice/show", [track.slot_id, 0])

        next_frame_at = time.monotonic()
        for frame_index in range(total_frames):
            wait_until(next_frame_at)
            elapsed = frame_index / scheduler_rate
            cycle_duration = total_steps * args.slot_interval
            cycle_step = min(
                int(((elapsed % cycle_duration) / args.slot_interval) + TIMING_EPSILON),
                total_steps - 1,
            )
            active_tracks = tracks_for_step(tracks, cycle_step)
            active_slots = {track.slot_id for track in active_tracks}
            newly_active_slots = active_slots - visible_slots
            inactive_slots = visible_slots - active_slots
            server_tick = int((elapsed * args.server_rate) + TIMING_EPSILON)
            visual_tick = int((elapsed * args.visual_rate) + TIMING_EPSILON)
            send_to_server = (
                server_tick != previous_server_tick
                and server_tick < total_server_ticks
            )
            send_to_visualizer = (
                visual_tick != previous_visual_tick
                and visual_tick < total_visual_ticks
            )
            expected: dict[int, int] = {}
            sent_at: dict[int, float] = {}

            if send_to_visualizer:
                for track in active_tracks:
                    send_visual_slice(visualizer, track, elapsed)
                previous_visual_tick = visual_tick

            for slot_id in sorted(inactive_slots):
                visualizer.send_message(b"/slice/show", [slot_id, 0])

            if newly_active_slots and not send_to_visualizer:
                for track in active_tracks:
                    if track.slot_id in newly_active_slots:
                        send_visual_slice(visualizer, track, elapsed)

            for slot_id in sorted(newly_active_slots):
                visualizer.send_message(b"/slice/show", [slot_id, 1])
            visible_slots = active_slots

            if send_to_server:
                for track in active_tracks:
                    normal, distance = track.modulation.plane_at(elapsed)
                    message_id += 1
                    expected[message_id] = track.object_id
                    sent_at[message_id] = time.monotonic()
                    slicer.send_message(
                        b"/slice/get",
                        [message_id, track.object_id, args.samples, *normal, distance],
                    )
                previous_server_tick = server_tick

            absolute_step = int((elapsed / args.slot_interval) + TIMING_EPSILON)
            if absolute_step != previous_step:
                _print_occupancy(
                    absolute_step * args.slot_interval,
                    active_tracks,
                )
                previous_step = absolute_step

            if expected:
                replies = inbox.wait_for(
                    expected,
                    args.samples,
                    args.reply_timeout,
                )
                reply_count += len(replies)
                latencies.extend(
                    reply.received_at - sent_at[reply.message_id]
                    for reply in replies
                )

            next_frame_at = max(
                next_frame_at + (1.0 / scheduler_rate),
                time.monotonic(),
            )
    except KeyboardInterrupt:
        print("\nTest interrupted.", file=sys.stderr)
        return_code = 130
    except (OSError, TimeoutError, ValueError) as exc:
        print(f"TEST FAILED: {exc}", file=sys.stderr)
        return_code = 1
    else:
        minimum = min(latencies) * 1000.0 if latencies else 0.0
        maximum = max(latencies) * 1000.0 if latencies else 0.0
        print(
            f"TEST PASSED: {reply_count} matching replies; "
            f"latency min={minimum:.1f}ms max={maximum:.1f}ms"
        )
        return_code = 0
    finally:
        for track in tracks:
            try:
                visualizer.send_message(b"/slice/show", [track.slot_id, 0])
            except OSError:
                pass
        listener.stop_all()

    return return_code


def send_visual_slice(
    visualizer: OSCClient,
    track: Track,
    elapsed: float,
) -> None:
    normal, distance = track.modulation.plane_at(elapsed)
    visualizer.send_message(
        b"/slice/set",
        [track.slot_id, track.object_id, *normal, distance],
    )


def tracks_for_step(tracks: tuple[Track, ...], step: int) -> tuple[Track, ...]:
    """Add one slot per step, then remove one slot per step."""
    track_count = len(tracks)
    if step < track_count:
        return tracks[: step + 1]
    first_active = step - track_count + 1
    return tracks[first_active:]


def validate_tracks(tracks: tuple[Track, ...]) -> None:
    if not tracks:
        raise ValueError("the demo needs at least one track")
    slot_ids = [track.slot_id for track in tracks]
    if len(set(slot_ids)) != len(slot_ids):
        raise ValueError("every track must use a different slot")
    if any(slot_id < 0 or slot_id >= 20 for slot_id in slot_ids):
        raise ValueError("slot IDs must be between 0 and 19")


def validate_reply(
    reply: SliceReply,
    expected_object: int,
    sample_count: int,
) -> None:
    if reply.object_id != expected_object:
        raise ValueError(
            f"message {reply.message_id} returned object {reply.object_id}, "
            f"expected {expected_object}"
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


def normalize(vector: Iterable[float]) -> Vector3:
    values = tuple(float(value) for value in vector)
    if len(values) != 3:
        raise ValueError("vectors must contain exactly three values")
    length = math.sqrt(sum(value * value for value in values))
    if length == 0.0:
        raise ValueError("vectors must be non-zero")
    return tuple(value / length for value in values)  # type: ignore[return-value]


def rotate_vector(vector: Vector3, axis: Vector3, angle: float) -> Vector3:
    cosine = math.cos(angle)
    sine = math.sin(angle)
    dot = sum(vector[index] * axis[index] for index in range(3))
    cross = (
        axis[1] * vector[2] - axis[2] * vector[1],
        axis[2] * vector[0] - axis[0] * vector[2],
        axis[0] * vector[1] - axis[1] * vector[0],
    )
    return tuple(
        vector[index] * cosine
        + cross[index] * sine
        + axis[index] * dot * (1.0 - cosine)
        for index in range(3)
    )  # type: ignore[return-value]


def wait_until(deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining > 0.0:
        time.sleep(remaining)


def as_int(value: Any) -> int:
    return int(value.decode("utf-8") if isinstance(value, bytes) else value)


def as_float(value: Any) -> float:
    return float(value.decode("utf-8") if isinstance(value, bytes) else value)


def _timeout_message(pending: dict[int, int], timeout: float) -> str:
    message_ids = ", ".join(str(message_id) for message_id in sorted(pending))
    return f"no /slice/radii for message IDs [{message_ids}] within {timeout:g}s"


def _print_configuration(
    title: str,
    tracks: tuple[Track, ...],
    args: argparse.Namespace,
    duration: float,
) -> None:
    print(title)
    print(
        f"  duration={duration:g}s server_rate={args.server_rate:g}Hz "
        f"visual_rate={args.visual_rate:g}Hz "
        f"slot_interval={args.slot_interval:g}s samples={args.samples}"
    )
    for track in tracks:
        modulation = track.modulation
        print(
            f"  slot={track.slot_id} object={track.object_id} {track.label}: "
            f"rotation={modulation.rotation_hz:g}Hz "
            f"distance={modulation.distance_center:+g}"
            f"{modulation.distance_amplitude:+g}sin({modulation.distance_hz:g}Hz)"
        )


def _print_occupancy(
    elapsed: float,
    active_tracks: tuple[Track, ...],
) -> None:
    slots = [track.slot_id for track in active_tracks]
    objects = [track.object_id for track in active_tracks]
    print(
        f"t={elapsed:05.1f}s occupied slots={slots or 'none'} "
        f"objects={objects or 'none'}",
        flush=True,
    )
