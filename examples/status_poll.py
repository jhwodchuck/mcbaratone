"""
Demonstration script that polls Baritone status in a loop.
"""

import os
import time

from baritone_client import BaritoneClient, BaritoneController


def main() -> None:
    base_url = os.getenv("BARITONE_BASE_URL", "http://localhost:4567")
    token = os.getenv("BARITONE_TOKEN")

    client = BaritoneClient(base_url, token=token)
    controller = BaritoneController(client)

    for status in controller.poll_status(interval_seconds=1.0, max_polls=3):
        print(status)
        time.sleep(0.5)


if __name__ == "__main__":
    main()
