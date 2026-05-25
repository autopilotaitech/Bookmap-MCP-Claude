"""Pin extract_block + validate_against_snapshot.

Contract:
1. extract_block reads the LAST well-formed <<PAX_AI_CHART_SIGNAL>>...<<END>>
   block from a Pax AI response. Malformed JSON / missing required field /
   bad enum / non-finite price / out-of-range confidence -> None.
2. validate_against_snapshot enriches an extracted block with id/alias/side
   by anchoring the label/price against snap['or_levels'] -- and rejects
   anything that can't be grounded.
"""
from __future__ import annotations

from pax_ai.ai_chart_signal import extract_block, validate_against_snapshot


# --- extract_block ----------------------------------------------------------

def test_extracts_well_formed_block():
    pax_text = (
        "OR-H acceptance with WITH flow.\n"
        "<<PAX_AI_CHART_SIGNAL>>\n"
        '{"action":"PAY_FOR_TRADE","direction":"LONG","label":"OR-H",'
        '"price":20000.0,"confidence":0.72,"reason":"acceptance + WITH"}\n'
        "<<END>>\n"
    )
    blk = extract_block(pax_text)
    assert blk is not None
    assert blk["action"] == "PAY_FOR_TRADE"
    assert blk["direction"] == "LONG"
    assert blk["label"] == "OR-H"
    assert blk["price"] == 20000.0
    assert blk["confidence"] == 0.72
    assert blk["reason"] == "acceptance + WITH"


def test_no_block_returns_none():
    assert extract_block("plain prose with no block") is None


def test_empty_input_returns_none():
    assert extract_block("") is None
    assert extract_block(None) is None  # type: ignore[arg-type]


def test_malformed_json_returns_none():
    assert extract_block("<<PAX_AI_CHART_SIGNAL>>\n{not json\n<<END>>") is None


def test_only_last_well_formed_block_wins():
    txt = (
        "<<PAX_AI_CHART_SIGNAL>>\n"
        '{"action":"WAIT_FOR_CONFIRM","direction":"NONE","label":"OR-H",'
        '"price":20000.0,"confidence":0.40,"reason":"early"}\n'
        "<<END>>\n"
        "Update: ACCEPTED.\n"
        "<<PAX_AI_CHART_SIGNAL>>\n"
        '{"action":"PAY_FOR_TRADE","direction":"LONG","label":"OR-H",'
        '"price":20000.0,"confidence":0.72,"reason":"confirmed"}\n'
        "<<END>>"
    )
    blk = extract_block(txt)
    assert blk is not None
    assert blk["action"] == "PAY_FOR_TRADE"
    assert blk["confidence"] == 0.72


def test_missing_required_field_returns_none():
    txt = (
        "<<PAX_AI_CHART_SIGNAL>>\n"
        '{"action":"PAY_FOR_TRADE","direction":"LONG"}\n'
        "<<END>>"
    )
    assert extract_block(txt) is None


def test_bad_action_enum_returns_none():
    txt = (
        "<<PAX_AI_CHART_SIGNAL>>\n"
        '{"action":"BUY_NOW","direction":"LONG","label":"OR-H",'
        '"price":20000.0,"confidence":0.5,"reason":"r"}\n'
        "<<END>>"
    )
    assert extract_block(txt) is None


def test_bad_direction_enum_returns_none():
    txt = (
        "<<PAX_AI_CHART_SIGNAL>>\n"
        '{"action":"PAY_FOR_TRADE","direction":"UP","label":"OR-H",'
        '"price":20000.0,"confidence":0.5,"reason":"r"}\n'
        "<<END>>"
    )
    assert extract_block(txt) is None


def test_non_finite_price_returns_none():
    txt = (
        "<<PAX_AI_CHART_SIGNAL>>\n"
        '{"action":"PAY_FOR_TRADE","direction":"LONG","label":"OR-H",'
        '"price":-1.0,"confidence":0.5,"reason":"r"}\n'
        "<<END>>"
    )
    assert extract_block(txt) is None


def test_out_of_range_confidence_returns_none():
    txt = (
        "<<PAX_AI_CHART_SIGNAL>>\n"
        '{"action":"PAY_FOR_TRADE","direction":"LONG","label":"OR-H",'
        '"price":20000.0,"confidence":1.5,"reason":"r"}\n'
        "<<END>>"
    )
    assert extract_block(txt) is None


def test_partial_last_block_ignored_when_prior_well_formed_present():
    txt = (
        "<<PAX_AI_CHART_SIGNAL>>\n"
        '{"action":"PAY_FOR_TRADE","direction":"LONG","label":"OR-H",'
        '"price":20000.0,"confidence":0.72,"reason":"r"}\n'
        "<<END>>\n"
        "<<PAX_AI_CHART_SIGNAL>>\n"
        '{partial json no end'
    )
    blk = extract_block(txt)
    assert blk is not None
    assert blk["action"] == "PAY_FOR_TRADE"


# --- validate_against_snapshot ---------------------------------------------

def _snap(or_h=20000.0, or_l=19950.0, mid=20002.0, alias="NQM6.CME@RITHMIC"):
    return {
        "alias": alias,
        "health": "ok",
        "book": {"mid": mid},
        "or_levels": {
            "orHigh": or_h, "orLow": or_l,
            "levels": [
                {"label": "OR-H", "price": or_h, "side": "above"},
                {"label": "OR-L", "price": or_l, "side": "below"},
                {"label": "+1",   "price": or_h + 50.0, "side": "above"},
            ],
        },
    }


def test_validate_accepts_matching_level_and_price():
    blk = {"action": "PAY_FOR_TRADE", "direction": "LONG", "label": "OR-H",
           "price": 20000.0, "confidence": 0.72, "reason": "x"}
    out = validate_against_snapshot(blk, _snap())
    assert out is not None
    assert out["source"] == "pax_ai"


def test_validate_rejects_unknown_label():
    blk = {"action": "PAY_FOR_TRADE", "direction": "LONG", "label": "+9",
           "price": 20450.0, "confidence": 0.50, "reason": "x"}
    assert validate_against_snapshot(blk, _snap()) is None


def test_validate_rejects_price_far_from_level():
    blk = {"action": "PAY_FOR_TRADE", "direction": "LONG", "label": "OR-H",
           "price": 20100.0, "confidence": 0.50, "reason": "x"}
    # OR-H = 20000; > 5 ticks (1.25p) away
    assert validate_against_snapshot(blk, _snap()) is None


def test_validate_accepts_within_tick_tolerance():
    blk = {"action": "PAY_FOR_TRADE", "direction": "LONG", "label": "OR-H",
           "price": 20000.75, "confidence": 0.50, "reason": "x"}
    # 3 ticks above; within 5-tick tolerance
    assert validate_against_snapshot(blk, _snap()) is not None


def test_validate_returns_enriched_fields():
    blk = {"action": "PAY_FOR_TRADE", "direction": "LONG", "label": "OR-H",
           "price": 20000.0, "confidence": 0.72, "reason": "x"}
    out = validate_against_snapshot(blk, _snap())
    assert out["alias"] == "NQM6.CME@RITHMIC"
    assert out["side"] == "above"
    assert out["id"]
    assert out["id"].startswith("pax_ai|")
    assert isinstance(out["timestamp_ms"], int) and out["timestamp_ms"] > 0


def test_validate_rejects_offline_snapshot():
    blk = {"action": "PAY_FOR_TRADE", "direction": "LONG", "label": "OR-H",
           "price": 20000.0, "confidence": 0.72, "reason": "x"}
    snap = _snap(); snap["health"] = "offline"
    assert validate_against_snapshot(blk, snap) is None


def test_validate_rejects_missing_or_levels():
    blk = {"action": "PAY_FOR_TRADE", "direction": "LONG", "label": "OR-H",
           "price": 20000.0, "confidence": 0.72, "reason": "x"}
    snap = _snap(); snap["or_levels"] = None
    assert validate_against_snapshot(blk, snap) is None


def test_validate_truncates_long_reason():
    blk = {"action": "PAY_FOR_TRADE", "direction": "LONG", "label": "OR-H",
           "price": 20000.0, "confidence": 0.72, "reason": "x" * 500}
    out = validate_against_snapshot(blk, _snap())
    assert out is not None
    assert len(out["reason"]) <= 240


def test_validate_rejects_non_dict_block():
    assert validate_against_snapshot("not a dict", _snap()) is None  # type: ignore[arg-type]
    assert validate_against_snapshot(None, _snap()) is None  # type: ignore[arg-type]


# --- Strict (action, direction) combo rules -------------------------------
#
# Audit fix: the validator must reject combinations that the prompt
# already forbids:
#   - PAY_FOR_TRADE    -> direction MUST be LONG or SHORT
#   - WAIT_FOR_CONFIRM -> direction MUST be NONE
#   - STAND_DOWN       -> direction MUST be NONE
#   - SCRATCH_READY    -> direction MUST be NONE
# Enforcement happens at BOTH layers (writer + reader) so a hand-edited
# JSONL row never sneaks an invalid combo onto the chart.


def _block(action, direction, label="OR-H", price=20000.0, conf=0.5):
    return (
        "<<PAX_AI_CHART_SIGNAL>>\n"
        f'{{"action":"{action}","direction":"{direction}","label":"{label}",'
        f'"price":{price},"confidence":{conf},"reason":"r"}}\n'
        "<<END>>"
    )


def test_extract_rejects_pay_for_trade_with_none():
    assert extract_block(_block("PAY_FOR_TRADE", "NONE")) is None


def test_extract_rejects_wait_for_confirm_with_long():
    assert extract_block(_block("WAIT_FOR_CONFIRM", "LONG")) is None


def test_extract_rejects_wait_for_confirm_with_short():
    assert extract_block(_block("WAIT_FOR_CONFIRM", "SHORT")) is None


def test_extract_rejects_stand_down_with_long():
    assert extract_block(_block("STAND_DOWN", "LONG")) is None


def test_extract_rejects_stand_down_with_short():
    assert extract_block(_block("STAND_DOWN", "SHORT")) is None


def test_extract_rejects_scratch_ready_with_long():
    assert extract_block(_block("SCRATCH_READY", "LONG")) is None


def test_extract_rejects_scratch_ready_with_short():
    assert extract_block(_block("SCRATCH_READY", "SHORT")) is None


def test_extract_accepts_pay_for_trade_long():
    blk = extract_block(_block("PAY_FOR_TRADE", "LONG"))
    assert blk is not None
    assert (blk["action"], blk["direction"]) == ("PAY_FOR_TRADE", "LONG")


def test_extract_accepts_pay_for_trade_short():
    blk = extract_block(_block("PAY_FOR_TRADE", "SHORT", label="OR-L",
                                price=19950.0))
    assert blk is not None
    assert (blk["action"], blk["direction"]) == ("PAY_FOR_TRADE", "SHORT")


def test_extract_accepts_wait_for_confirm_none():
    blk = extract_block(_block("WAIT_FOR_CONFIRM", "NONE"))
    assert blk is not None


def test_extract_accepts_stand_down_none():
    blk = extract_block(_block("STAND_DOWN", "NONE"))
    assert blk is not None


def test_extract_accepts_scratch_ready_none():
    blk = extract_block(_block("SCRATCH_READY", "NONE"))
    assert blk is not None


def test_validate_rejects_pay_for_trade_with_none_defense_in_depth():
    """Reader-side defense: even if a malformed JSONL row arrives with a
    bad combo, validate_against_snapshot must reject it."""
    bad = {"action": "PAY_FOR_TRADE", "direction": "NONE", "label": "OR-H",
           "price": 20000.0, "confidence": 0.5, "reason": "r"}
    assert validate_against_snapshot(bad, _snap()) is None


def test_validate_rejects_wait_with_long_defense_in_depth():
    bad = {"action": "WAIT_FOR_CONFIRM", "direction": "LONG", "label": "OR-H",
           "price": 20000.0, "confidence": 0.5, "reason": "r"}
    assert validate_against_snapshot(bad, _snap()) is None


def test_validate_rejects_stand_down_with_short_defense_in_depth():
    bad = {"action": "STAND_DOWN", "direction": "SHORT", "label": "OR-H",
           "price": 20000.0, "confidence": 0.5, "reason": "r"}
    assert validate_against_snapshot(bad, _snap()) is None
