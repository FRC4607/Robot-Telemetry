import json
import os
from functools import lru_cache
from typing import Any


_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "metric_thresholds.json")


@lru_cache(maxsize=1)
def _load_thresholds() -> dict:
    with open(_CONFIG_PATH, "r") as f:
        return json.load(f)


def get_threshold(path: str, default: Any = None) -> Any:
    """Read a threshold value from config/metric_thresholds.json using dot paths."""
    node = _load_thresholds()
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def high_is_bad(value: float, warning: float, critical: float) -> int:
    if value > critical:
        return 2
    if value > warning:
        return 1
    return 0


def low_is_bad(value: float, warning: float, critical: float) -> int:
    if value < critical:
        return 2
    if value < warning:
        return 1
    return 0
