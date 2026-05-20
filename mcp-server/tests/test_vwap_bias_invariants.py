"""v20 production invariant: compute_vwap_bias must NOT use the bridge's
ETH overlay (vwap_obj.eth) for any weighted decision score. The overlay
is anchored at hard-coded 17:00 CT, which has nothing to do with the
operator's OR session. It may be exposed in components as display-only
but must have zero decision weight."""
from __future__ import annotations

from bookmap_mcp import dashboard


def _base_vwap_snap(eth_vwap=None):
    """Build a snapshot with a known OR-session VWAP and optional ETH
    overlay so we can measure whether the bias score depends on ETH."""
    vwap_obj = {
        "vwap":          17000.0,
        "stddev":        2.0,
        "lastTradePrice": 17000.0,
        "samples":       500,
    }
    if eth_vwap is not None:
        vwap_obj["eth"] = {"vwap": eth_vwap, "stddev": 2.0, "samples": 500}
    return {
        "vwap_obj":       vwap_obj,
        "book":           {"mid": 17001.0},
        "volume_profile": {},   # no VP → vwd path bypassed
    }


def test_vwap_bias_score_unaffected_by_eth_overlay():
    """The bias score must be identical whether or not vwap_obj.eth exists,
    and regardless of the eth.vwap value."""
    no_eth   = dashboard.compute_vwap_bias(_base_vwap_snap(eth_vwap=None))
    eth_low  = dashboard.compute_vwap_bias(_base_vwap_snap(eth_vwap=16900.0))
    eth_high = dashboard.compute_vwap_bias(_base_vwap_snap(eth_vwap=17100.0))
    assert no_eth["score"]   == eth_low["score"] == eth_high["score"], (
        f"ETH overlay must not influence weighted score; got "
        f"none={no_eth['score']} low={eth_low['score']} high={eth_high['score']}"
    )


def test_vwap_bias_components_carry_overlay_for_display_with_zero_weight():
    """When the ETH overlay is present, components.overlay_eth_div_sigma
    is populated for display BUT components.divDecisionWeight is 0."""
    res = dashboard.compute_vwap_bias(_base_vwap_snap(eth_vwap=16980.0))
    comps = res["components"]
    # Display-only divergence carried.
    assert "overlay_eth_div_sigma" in comps
    # Explicit zero decision weight.
    assert comps["divDecisionWeight"] == 0.0
    # Legacy "rth_eth" key must NOT be present in v20 components.
    assert "rth_eth" not in comps
    assert "rth_eth_div_sigma" not in comps


def test_vwap_bias_reasons_do_not_mention_rth_eth():
    """Reason text must not include legacy 'RTH-ETH' label — it implies
    the ETH overlay is influencing the decision, which would mislead the
    operator."""
    res = dashboard.compute_vwap_bias(_base_vwap_snap(eth_vwap=16980.0))
    joined = " ".join(res.get("reasons") or [])
    assert "RTH-ETH" not in joined
    assert "rth_eth" not in joined


def test_vwap_bias_weights_sum_to_one():
    """Sanity: the post-removal weights must still sum to 1.0 so the score
    range is bounded the same way as before."""
    import inspect
    src = inspect.getsource(dashboard.compute_vwap_bias)
    # The literal weight dict in the function body must sum to 1.0.
    # Find the line and parse the four weights.
    import re
    m = re.search(r'w = \{([^}]+)\}', src)
    assert m is not None, "could not locate weight dict in compute_vwap_bias"
    weights = re.findall(r'"\w+":\s*([0-9.]+)', m.group(1))
    weights = [float(w) for w in weights]
    assert sum(weights) == 1.0, f"weights {weights} sum to {sum(weights)}"
