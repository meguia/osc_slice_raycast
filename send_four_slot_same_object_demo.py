"""Animate four independently modulated copies of one object."""

from __future__ import annotations

import argparse

from osc_multi_slot_demo import (
    Modulation,
    Track,
    add_runtime_arguments,
    run_demo,
    validate_runtime_arguments,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Test one source object copied into four visual slots with four "
            "independent slice modulations."
        )
    )
    parser.add_argument(
        "--object-id",
        type=int,
        default=0,
        help="Object slice_index copied into all four slots",
    )
    add_runtime_arguments(parser, default_cycles=2)
    args = parser.parse_args()
    validate_runtime_arguments(parser, args)
    return args


def make_tracks(object_id: int) -> tuple[Track, ...]:
    return (
        Track(
            slot_id=0,
            object_id=object_id,
            label="X-axis rotation",
            modulation=Modulation(
                base_normal=(0.0, 1.0, 0.0),
                axis=(1.0, 0.0, 0.0),
                rotation_hz=0.10,
                distance_amplitude=0.08,
                distance_hz=0.17,
            ),
        ),
        Track(
            slot_id=1,
            object_id=object_id,
            label="Z-axis rotation",
            modulation=Modulation(
                base_normal=(0.0, 1.0, 0.0),
                axis=(0.0, 0.0, 1.0),
                rotation_hz=0.16,
                phase_cycles=0.25,
                distance_center=0.06,
                distance_amplitude=0.06,
                distance_hz=0.11,
                distance_phase_cycles=0.25,
            ),
        ),
        Track(
            slot_id=2,
            object_id=object_id,
            label="Y-axis reverse rotation",
            modulation=Modulation(
                base_normal=(0.0, 0.0, 1.0),
                axis=(0.0, 1.0, 0.0),
                rotation_hz=-0.21,
                phase_cycles=0.10,
                distance_center=-0.04,
                distance_amplitude=0.10,
                distance_hz=0.23,
            ),
        ),
        Track(
            slot_id=3,
            object_id=object_id,
            label="diagonal-axis rotation",
            modulation=Modulation(
                base_normal=(1.0, 1.0, 0.0),
                axis=(0.25, -0.40, 1.0),
                rotation_hz=0.27,
                phase_cycles=0.40,
                distance_center=0.02,
                distance_amplitude=0.14,
                distance_hz=0.07,
                distance_phase_cycles=0.50,
            ),
        ),
    )


def main() -> int:
    args = parse_args()
    return run_demo(
        "Four-slot same-object OSC demo",
        make_tracks(args.object_id),
        args,
    )


if __name__ == "__main__":
    raise SystemExit(main())
