"""MCP server definition. Wires Bookmap bridge calls to MCP tools.

Defensive on import: if the optional ``Image`` helper isn't in the installed
mcp version, we still register the other 10 tools — the screenshot tool
gracefully falls back to base64-encoded PNG bytes.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any, Dict, Mapping, Optional

from mcp.server.fastmcp import FastMCP

from .bridge_client import BridgeClient, BridgeError
from .config import BridgeConfig, MissingTokenError


def _require_live_trade_allowed(confirm: bool, op: str) -> None:
    """Belt-and-suspenders gate on live broker calls. Two independent checks:

    1. Caller must pass `confirm=True` explicitly — a typo or accidental
       autocomplete cannot fire a real order.
    2. The Bookmap process environment must have `BOOKMAP_ALLOW_TRADING=1`.
       The Java bridge has its own check on this var; we replicate it here in
       Python so a hypothetical bridge regression cannot silently route an
       order through.

    Raises RuntimeError if either gate fails. Both gates must be open for the
    tool to call through to /place_limit_order or /cancel_order.
    """
    if not confirm:
        raise RuntimeError(
            f"{op} requires confirm=True. This is a LIVE broker action — "
            "verify alias, side, size, price/orderId before calling."
        )
    if os.environ.get("BOOKMAP_ALLOW_TRADING") != "1":
        raise RuntimeError(
            f"BOOKMAP_ALLOW_TRADING is not set to '1' in the Bookmap process "
            f"environment. Live {op} is disabled."
        )

log = logging.getLogger("bookmap_mcp")

# Optional Image helper — only present in newer mcp versions. If unavailable,
# the screenshot tool returns a JSON payload with base64-encoded PNG instead.
try:
    from mcp.server.fastmcp.utilities.types import Image as _McpImage  # type: ignore
    _HAS_IMAGE = True
except Exception:  # pragma: no cover — depends on local mcp version
    _McpImage = None  # type: ignore
    _HAS_IMAGE = False


def build_server() -> FastMCP:
    server = FastMCP("bookmap-mcp")

    @server.tool()
    def bookmap_ping() -> Dict[str, Any]:
        """Health-check the Bookmap MCP bridge."""
        return _call("/ping")

    @server.tool()
    def bookmap_list_instruments() -> Dict[str, Any]:
        """List every Bookmap instrument the MCP Bridge add-on is attached to."""
        return _call("/instruments")

    @server.tool()
    def bookmap_orderbook(alias: str, depth: int = 10) -> Dict[str, Any]:
        """Snapshot the order book for one attached instrument.

        Returns the top `depth` bid and ask levels (default 10, max 500), plus
        bestBid, bestAsk, mid, and spread.
        """
        return _call("/orderbook", {"alias": alias, "depth": depth})

    @server.tool()
    def bookmap_recent_trades(alias: str, count: int = 20) -> Dict[str, Any]:
        """Last `count` printed trades, newest first.

        Side convention matches Bookmap's TradeInfo.isBidAggressor:
        'buy'  = the bid was the aggressor (lifted offer);
        'sell' = the ask was the aggressor (hit bid).
        """
        return _call("/recent_trades", {"alias": alias, "count": count})

    @server.tool()
    def bookmap_working_orders(alias: str) -> Dict[str, Any]:
        """Currently working / in-flight orders on this instrument."""
        return _call("/working_orders", {"alias": alias})

    @server.tool()
    def bookmap_position(alias: str) -> Dict[str, Any]:
        """Broker-reported position + PnL for one instrument."""
        return _call("/position", {"alias": alias})

    @server.tool()
    def bookmap_recent_fills(alias: str, count: int = 20) -> Dict[str, Any]:
        """Last `count` order fills (executions) on this instrument, newest first."""
        return _call("/recent_fills", {"alias": alias, "count": count})

    @server.tool()
    def bookmap_balance(alias: str) -> Dict[str, Any]:
        """Account balance + per-currency PnL as last reported by the broker."""
        return _call("/balance", {"alias": alias})

    @server.tool()
    def bookmap_vwap(alias: str) -> Dict[str, Any]:
        """Session-anchored VWAP plus +/- 1, 2, 3 sigma extension bands.

        Session anchor is the operator's OpenRange UI setting (published via
        or_session.effective_session_anchor() and pushed to the bridge on
        every snapshot poll). Accumulators reset across that boundary.

        Returns: vwap, stddev, upper1/lower1, upper2/lower2, upper3/lower3,
        samples (trades counted this session), sessionStartUtc, sessionStartCt,
        and lastTradePrice for quick band-relative reads.

        When no trades have been seen yet this session, samples=0 and the band
        fields are null.
        """
        return _call("/vwap", {"alias": alias})

    @server.tool()
    def bookmap_momentum(alias: str, windows: str = "30,120,600") -> Dict[str, Any]:
        """Rolling-window momentum / aggressor-imbalance over the last N seconds.

        `windows` is a comma-separated list of window sizes in seconds; the
        bridge returns one buy/sell volume ratio and aggressor count per
        window so callers can spot alignment or inflection across time scales.
        """
        return _call("/momentum", {"alias": alias, "windows": windows})

    @server.tool()
    def bookmap_volume_profile(alias: str) -> Dict[str, Any]:
        """Session volume profile — POC, VAH/VAL, HVNs, LVNs.

        Accumulates traded volume per price bin across the active RTH session,
        same anchor as bookmap_vwap.
        """
        return _call("/volume_profile", {"alias": alias})

    @server.tool()
    def bookmap_tape_buckets(alias: str) -> Dict[str, Any]:
        """Recent prints grouped into trade-size buckets (retail vs sweeps vs
        institutional blocks). Useful for spotting where size is participating.
        """
        return _call("/tape_buckets", {"alias": alias})

    @server.tool()
    def bookmap_lt_liquidity(alias: str) -> Dict[str, Any]:
        """Long-term resting liquidity around the inside market — order ages,
        survival rates, and where the heavy persistent quotes are sitting.
        Identifies real liquidity vs flicker.
        """
        return _call("/lt_liquidity", {"alias": alias})

    @server.tool()
    def bookmap_book_dynamics(alias: str, top: int = 12) -> Dict[str, Any]:
        """Recent book churn — adds, cancels, replaces — on the top N levels
        per side (default 12). Surfaces stacking, pulling, and refresh
        patterns that depth snapshots alone don't show.
        """
        return _call("/book_dynamics", {"alias": alias, "top": top})

    @server.tool()
    def bookmap_pull_stack(alias: str) -> Dict[str, Any]:
        """Recent pull / stack events — large quotes that flickered in then
        cancelled, or appeared then got hit. Classic spoof / iceberg tells.
        """
        return _call("/pull_stack", {"alias": alias})

    @server.tool()
    def bookmap_microstructure_events(alias: str, max: int = 50) -> Dict[str, Any]:
        """Recent microstructure events (sweeps, absorptions, regime flips,
        etc.) detected by the bridge, newest first, capped at `max`.
        """
        return _call("/microstructure_events", {"alias": alias, "max": max})

    @server.tool()
    def bookmap_set_magnet_levels(alias: str, levels: str = "") -> Dict[str, Any]:
        """Configure stop-sweep magnet levels for one alias.

        `levels` is a comma-separated string of display-currency prices, e.g.
        "20100.25,20123.50". Pass an empty string to clear all levels.

        Required before STOP_SWEEP microstructure events can fire: the bridge
        only emits a sweep when an aggressor-volume burst crosses one of these
        configured levels. Typical inputs are OR-H/OR-L, prior-day H/L, or
        VWAP +/- 2 sigma — caller is responsible for picking levels.
        """
        return _post("/magnet_levels", {"alias": alias, "levels": levels})

    if _HAS_IMAGE:
        @server.tool()
        def bookmap_screenshot():  # type: ignore[return-type]
            """Capture a PNG of the primary display.

            Bookmap is typically maximized so this captures the chart and
            levels. UI control (instrument switch, timeframe change) is not
            implemented in this build.
            """
            png = _fetch_screenshot_bytes()
            return _McpImage(data=png, format="png")
    else:
        @server.tool()
        def bookmap_screenshot() -> Dict[str, Any]:
            """Capture a PNG of the primary display (base64 fallback for older mcp).

            Returns the PNG as base64-encoded text in {"format":"png","data": "<b64>"}.
            """
            png = _fetch_screenshot_bytes()
            return {"format": "png", "data": base64.b64encode(png).decode("ascii")}

    @server.tool()
    def bookmap_place_limit_order(
        alias: str,
        side: str,
        size: int,
        price: float,
        duration: str = "DAY",
        confirm: bool = False,
    ) -> Dict[str, Any]:
        """Place a LIMIT order on the broker Bookmap is connected to.

        DANGEROUS — sends a real order. TWO independent gates must be open:
          1. `confirm=True` must be passed explicitly.
          2. `BOOKMAP_ALLOW_TRADING=1` must be set in the Bookmap process
             environment.

        Whether the order is paper or live depends on the broker login used
        at Bookmap startup. Always confirm with the user before calling.
        """
        _require_live_trade_allowed(confirm, "place_limit_order")
        return _post("/place_limit_order", {
            "alias": alias, "side": side, "size": size,
            "price": price, "duration": duration,
        })

    @server.tool()
    def bookmap_cancel_order(alias: str, orderId: str,
                              confirm: bool = False) -> Dict[str, Any]:
        """Cancel a working order by orderId. Same two-gate guard as
        place_limit_order: requires `confirm=True` AND
        `BOOKMAP_ALLOW_TRADING=1`."""
        _require_live_trade_allowed(confirm, "cancel_order")
        return _post("/cancel_order", {"alias": alias, "orderId": orderId})

    return server


def _call(path: str, params: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    try:
        config = BridgeConfig.load()
    except MissingTokenError as exc:
        raise RuntimeError(str(exc)) from exc
    try:
        with BridgeClient(config) as client:
            return client.get_json(path, params=params)
    except BridgeError as exc:
        raise RuntimeError(str(exc)) from exc


def _post(path: str, params: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    try:
        config = BridgeConfig.load()
    except MissingTokenError as exc:
        raise RuntimeError(str(exc)) from exc
    try:
        with BridgeClient(config) as client:
            return client.post_json(path, params=params)
    except BridgeError as exc:
        raise RuntimeError(str(exc)) from exc


def _fetch_screenshot_bytes() -> bytes:
    try:
        config = BridgeConfig.load()
    except MissingTokenError as exc:
        raise RuntimeError(str(exc)) from exc
    try:
        with BridgeClient(config, timeout_s=15.0) as client:
            return client.get_bytes("/screenshot")
    except BridgeError as exc:
        raise RuntimeError(str(exc)) from exc


__all__ = ["build_server"]
