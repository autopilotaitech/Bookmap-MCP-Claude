"""Server-side jargon normalizer for voice transcripts.

Web Speech API frequently mangles trading jargon: "NQ" -> "in queue",
"VWAP" -> "v whap", "OR-Low" -> "or low". This module is a pure-function
post-processor that fixes the most common errors before the text reaches
Claude.

The same regex list lives in pax_ai/static/app.js as a defense-in-depth
copy; the server is authoritative.
"""

from __future__ import annotations

import re
from typing import List, Tuple


# Order matters: longer / more-specific patterns come first.
# Each pattern is (compiled_regex, replacement).
# `(?i)` = case-insensitive. `\b` word boundaries prevent over-eager
# matches inside larger words.
_RULES: List[Tuple[re.Pattern, str]] = [
    # Tickers heard as words
    (re.compile(r"(?i)\bin\s+queue\b"),     "NQ"),
    (re.compile(r"(?i)\bmicro\s+queue\b"),  "MNQ"),
    (re.compile(r"(?i)\bv\s*whap\b"),       "VWAP"),
    (re.compile(r"(?i)\bv-whap\b"),         "VWAP"),
    (re.compile(r"(?i)\bvee\s*whap\b"),     "VWAP"),

    # OR level vocabulary
    (re.compile(r"(?i)\bor\s+low\b"),       "OR-Low"),
    (re.compile(r"(?i)\bor\s+high\b"),      "OR-High"),
    (re.compile(r"(?i)\bor\s+mid\b"),       "OR-Mid"),
    (re.compile(r"(?i)\bopening\s+range\b"), "Opening Range"),

    # Extension rungs (signed numeric word -> token)
    (re.compile(r"(?i)\bplus\s+one\b"),     "+1"),
    (re.compile(r"(?i)\bplus\s+two\b"),     "+2"),
    (re.compile(r"(?i)\bplus\s+three\b"),   "+3"),
    (re.compile(r"(?i)\bminus\s+one\b"),    "-1"),
    (re.compile(r"(?i)\bminus\s+two\b"),    "-2"),
    (re.compile(r"(?i)\bminus\s+three\b"),  "-3"),

    # Order-flow vocabulary
    (re.compile(r"(?i)\bbuying\s+pressure\b"),  "bid-side pressure"),
    (re.compile(r"(?i)\bselling\s+pressure\b"), "ask-side pressure"),
    (re.compile(r"(?i)\bstop\s+sweep\b"),       "STOP_SWEEP"),
    # `(?:ing)?` groups the whole suffix so this matches 'spoof'/'spoofing'
    # only. Do NOT write `spoofing?` -- that parses as 'spoof' + 'in' + an
    # optional 'g', which wrongly rewrites the mis-transcription 'spoofin'.
    (re.compile(r"(?i)\bspoof(?:ing)?\b"),      "SPOOF"),
    (re.compile(r"(?i)\bice\s*berg\b"),         "ICEBERG"),

    # Verdict words
    (re.compile(r"(?i)\bfollow\s+long\b"),  "FOLLOW long"),
    (re.compile(r"(?i)\bfollow\s+short\b"), "FOLLOW short"),
    (re.compile(r"(?i)\bfade\s+long\b"),    "FADE long"),
    (re.compile(r"(?i)\bfade\s+short\b"),   "FADE short"),
]


def normalize(transcript: str) -> str:
    """Run the regex list over the transcript. Empty/whitespace input -> "".

    Idempotent: running normalize(normalize(x)) == normalize(x) for any x.
    """
    if not transcript:
        return ""
    out = transcript.strip()
    for pat, repl in _RULES:
        out = pat.sub(repl, out)
    return out
