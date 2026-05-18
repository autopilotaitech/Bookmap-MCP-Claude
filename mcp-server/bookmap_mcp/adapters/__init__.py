"""Data adapters — pluggable sources that feed normalized snapshots to the
Pax daemon. See `base.py` for the `DataAdapter` Protocol."""

from .base import (
    AdapterHealth,
    DataAdapter,
    Snapshot,
)
from .csv_replay import CsvReplayAdapter, csv_row_to_snapshot
from .file_tail import FileTailAdapter
from .bookmap_live import BookmapLiveAdapter

__all__ = [
    "AdapterHealth", "DataAdapter", "Snapshot",
    "CsvReplayAdapter", "FileTailAdapter", "BookmapLiveAdapter",
    "csv_row_to_snapshot",
]
