"""Voice jargon normalizer tests - regex-based, idempotent."""

from __future__ import annotations

import pytest

from pax_ai import voice


@pytest.mark.parametrize("raw,expected", [
    # ticker mishearings
    ("buy in queue at the open",            "buy NQ at the open"),
    ("is micro queue rotating",             "is MNQ rotating"),
    ("v whap is bullish",                   "VWAP is bullish"),
    ("vee whap support",                    "VWAP support"),

    # OR vocabulary
    ("is or low holding",                   "is OR-Low holding"),
    ("price tagged or high",                "price tagged OR-High"),
    ("watching the opening range",          "watching the Opening Range"),

    # extension rungs
    ("approaching plus one",                "approaching +1"),
    ("we hit minus two with momentum",      "we hit -2 with momentum"),
    ("rotation back to plus three",         "rotation back to +3"),

    # order flow vocabulary
    ("buying pressure into the level",      "bid-side pressure into the level"),
    ("selling pressure on the offer",       "ask-side pressure on the offer"),
    ("ice berg defending the ask",          "ICEBERG defending the ask"),
    ("stop sweep printed",                  "STOP_SWEEP printed"),
    ("spoofing at the bid",                 "SPOOF at the bid"),
    ("spoof at the bid",                    "SPOOF at the bid"),
    # 'spoofin' is a mis-transcription artifact, not the jargon word:
    # the rule must NOT eagerly rewrite it.
    ("spoofin around the level",            "spoofin around the level"),

    # verdict words
    ("call it a follow long",               "call it a FOLLOW long"),
    ("scratch the fade short",              "scratch the FADE short"),

    # empty / whitespace
    ("",                                    ""),
    ("   ",                                 ""),

    # no-op passthrough
    ("standard text with no jargon",        "standard text with no jargon"),
])
def test_normalize(raw, expected):
    assert voice.normalize(raw) == expected


def test_normalize_is_idempotent():
    samples = [
        "buy in queue at or low",
        "v whap stretched on plus one with buying pressure",
        "ice berg on the offer near or high",
    ]
    for s in samples:
        once = voice.normalize(s)
        twice = voice.normalize(once)
        assert once == twice, f"normalize not idempotent for {s!r}: {once!r} -> {twice!r}"


def test_normalize_case_insensitive():
    assert voice.normalize("BUY In Queue Now") == "BUY NQ Now"
    assert voice.normalize("FADE SHORT setup") == "FADE short setup"
