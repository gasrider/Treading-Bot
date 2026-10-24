"""Docker-Healthcheck: gesund, wenn der Bot in den letzten 15 Minuten ein Lebenszeichen gab."""
import sys

import yaml

from .dashboard import STALE_SECONDS, heartbeat_age
from .state import StateStore


def main() -> int:
    with open("config.yaml", encoding="utf-8") as f:
        mode = yaml.safe_load(f)["mode"]
    age = heartbeat_age(StateStore(f"state/{mode}_state.json").load())
    return 0 if age is not None and age < STALE_SECONDS else 1


if __name__ == "__main__":
    sys.exit(main())
