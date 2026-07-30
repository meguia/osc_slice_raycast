"""Free all 16 visualizer slots."""

from __future__ import annotations

import argparse

from oscpy.client import OSCClient


def parse_args() -> argparse.Namespace:
    """Parse visualizer connection settings."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9005)
    return parser.parse_args()


def main() -> None:
    """Hide slots 0-15."""
    args = parse_args()
    client = OSCClient(args.host, args.port)
    for slot_id in range(16):
        client.send_message(b"/slice/show", [slot_id, 0])
    print("Freed slots 0-15")


if __name__ == "__main__":
    main()
