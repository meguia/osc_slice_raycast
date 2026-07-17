"""Animate seven objects with independent slot modulations."""

from __future__ import annotations

import argparse

from osc_multi_slot_demo import (
    Modulation,
    Track,
    add_runtime_arguments,
    run_demo,
    validate_runtime_arguments,
)


DEFAULT_OBJECT_IDS = (0, 2, 3, 6, 9, 12, 18)


MODULATIONS = (
    ("slow X rotation", (0, 1, 0), (1, 0, 0), 0.07, 0.00, 0.05, 0.09),
    ("Y rotation", (0, 0, 1), (0, 1, 0), 0.10, 0.10, 0.08, 0.13),
    ("Z rotation", (1, 0, 0), (0, 0, 1), -0.13, 0.20, 0.10, 0.17),
    ("diagonal A", (0, 1, 0), (1, 0, 1), 0.16, 0.30, 0.12, 0.08),
    ("diagonal B", (1, 0, 0), (0, 1, 1), -0.19, 0.40, 0.14, 0.11),
    ("diagonal C", (0, 0, 1), (1, 1, 0), 0.22, 0.50, 0.09, 0.19),
    ("oblique fast", (1, 1, 0), (-0.2, 0.3, 1), 0.25, 0.60, 0.16, 0.06),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Test seven source objects in seven slots with independent normal "
            "and distance modulation."
        )
    )
    parser.add_argument(
        "--objects",
        default=",".join(str(object_id) for object_id in DEFAULT_OBJECT_IDS),
        help="Seven comma-separated object slice_index values",
    )
    add_runtime_arguments(parser, default_cycles=1)
    args = parser.parse_args()
    validate_runtime_arguments(parser, args)
    args.object_ids = parse_object_ids(parser, args.objects)
    return args


def parse_object_ids(
    parser: argparse.ArgumentParser,
    value: str,
) -> tuple[int, ...]:
    try:
        object_ids = tuple(int(part.strip()) for part in value.split(",") if part.strip())
    except ValueError:
        parser.error("--objects must contain comma-separated integers")
    if len(object_ids) != 7:
        parser.error("--objects must contain exactly seven object IDs")
    if len(set(object_ids)) != 7:
        parser.error("--objects must contain seven different object IDs")
    return object_ids


def make_tracks(object_ids: tuple[int, ...]) -> tuple[Track, ...]:
    tracks = []
    for slot_id, (object_id, values) in enumerate(zip(object_ids, MODULATIONS)):
        label, base_normal, axis, rotation_hz, phase, distance_amplitude, distance_hz = values
        tracks.append(
            Track(
                slot_id=slot_id,
                object_id=object_id,
                label=label,
                modulation=Modulation(
                    base_normal=base_normal,
                    axis=axis,
                    rotation_hz=rotation_hz,
                    phase_cycles=phase,
                    distance_center=(slot_id - 3) * 0.0125,
                    distance_amplitude=distance_amplitude,
                    distance_hz=distance_hz,
                    distance_phase_cycles=phase + 0.25,
                ),
            )
        )
    return tuple(tracks)


def main() -> int:
    args = parse_args()
    return run_demo(
        "Seven-object OSC demo",
        make_tracks(args.object_ids),
        args,
    )


if __name__ == "__main__":
    raise SystemExit(main())
