"""Popup trigger chart bridge.

Popup triggers are intentionally NOT plotted on the Bookmap chart. They are
short-horizon alerts (trend/regime/conviction/micro chips), not anchored
institutional order-flow theses. Chart markers must come from anchored
OR/extension evidence or a validated Pax AI chart block tied to a snapshot
level.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional


def record_trigger_chart_events(triggers: Iterable[Dict[str, Any]],
                                snap: Optional[Dict[str, Any]]) -> int:
    return 0


def trigger_to_signal(trig: Any, snap: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return None
