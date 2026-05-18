"""Data adapters — pluggable sources that feed normalized snapshots to the
Pax daemon. See `base.py` for the `DataAdapter` Protocol."""

from .base import (
    AdapterHealth,
    DataAdapter,
    Snapshot,
)
from .csv_replay import CsvReplayAdapter

__all__ = ["AdapterHealth", "DataAdapter", "Snapshot", "CsvReplayAdapter"]
