"""
Shared per-log caches for metric group DataFrame lookups.

Group modules repeatedly query df[df["Key"] == key] for the same keys.
On very large logs, that becomes the dominant cost. This module memoizes those
lookups per DataFrame so each key is filtered once and reused across groups.
"""

from __future__ import annotations

import threading
import weakref
from typing import Any, Dict, List

import numpy as np
import pandas as pd


_CACHE_LOCK = threading.Lock()
_DF_CACHES: Dict[int, Dict[str, Any]] = {}
_DF_FINALIZERS: Dict[int, weakref.finalize] = {}


def _drop_cache(df_id: int):
    with _CACHE_LOCK:
        _DF_CACHES.pop(df_id, None)
        _DF_FINALIZERS.pop(df_id, None)


def _get_cache(df: pd.DataFrame) -> Dict[str, Any]:
    # Do not mutate df.attrs here; pandas deep-copies attrs during many
    # frame operations and concurrent mutation can raise runtime errors.
    df_id = id(df)
    with _CACHE_LOCK:
        cache = _DF_CACHES.get(df_id)
        if cache is None:
            cache = {"numeric": {}, "arrays": {}}
            _DF_CACHES[df_id] = cache
            _DF_FINALIZERS[df_id] = weakref.finalize(df, _drop_cache, df_id)
        return cache


def get_numeric_cached(df: pd.DataFrame, key: str) -> pd.Series:
    """Return numeric series for a Key, cached per DataFrame+key."""
    cache = _get_cache(df)

    with _CACHE_LOCK:
        found = cache["numeric"].get(key)
    if found is not None:
        return found

    subset = df[df["Key"] == key]
    if subset.empty:
        series = pd.Series(dtype=float)
    else:
        series = pd.to_numeric(subset["Value"], errors="coerce").dropna()
        series.index = subset.index[: len(series)]

    with _CACHE_LOCK:
        cache["numeric"].setdefault(key, series)
        return cache["numeric"][key]


def get_array_cached(df: pd.DataFrame, key: str) -> List[np.ndarray]:
    """Return ndarray values for a Key, cached per DataFrame+key."""
    cache = _get_cache(df)

    with _CACHE_LOCK:
        found = cache["arrays"].get(key)
    if found is not None:
        return found

    subset = df[df["Key"] == key]
    if subset.empty:
        arrays: List[np.ndarray] = []
    else:
        arrays = [v for v in subset["Value"] if isinstance(v, np.ndarray)]

    with _CACHE_LOCK:
        cache["arrays"].setdefault(key, arrays)
        return cache["arrays"][key]
