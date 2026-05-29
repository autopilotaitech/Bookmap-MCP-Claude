from bookmap_mcp import pax_runtime_policy as P


def test_setup_signature_is_stable_uppercase():
    assert P.setup_signature("or_break_accept", "long", "or-h", "eth") == \
        "OR_BREAK_ACCEPT|LONG|OR-H|ETH"


def test_lookup_policy_defaults_to_keep():
    p = P.lookup_policy(None, setup_type="A", side="LONG", level="OR-H",
                        session_type="ETH")
    assert p["action"] == "KEEP"


def test_lookup_policy_returns_exact_suggestion():
    policy = {"suggestions": [
        {"setup": "A|LONG|OR-H|ETH", "action": "THROTTLE",
         "reason": "bad bucket"}
    ]}
    p = P.lookup_policy(policy, setup_type="A", side="LONG", level="OR-H",
                        session_type="ETH")
    assert p["action"] == "THROTTLE"
    assert p["reason"] == "bad bucket"


def test_promote_modestly_lowers_floor():
    assert P.adjusted_floor(0.30, "PROMOTE") == 0.25
    assert P.adjusted_floor(0.16, "PROMOTE") == 0.15
    assert P.adjusted_floor(0.30, "KEEP") == 0.30


def test_guard_runtime_policy_requires_scorecard_evidence():
    policy = {"suggestions": [
        {"setup": "A|LONG|OR-H|ETH", "action": "THROTTLE", "reason": "bad"},
        {"setup": "B|SHORT|OR-L|ETH", "action": "PROMOTE", "reason": "good"},
    ]}
    scorecard = {"setups": [
        {"setup": "A|LONG|OR-H|ETH", "n": 3, "warning": None},
        {"setup": "B|SHORT|OR-L|ETH", "n": 2,
         "warning": "insufficient_sample_count"},
    ]}
    guarded = P.guard_runtime_policy(policy, scorecard, min_samples=3)
    assert guarded["suggestions"] == [
        {"setup": "A|LONG|OR-H|ETH", "action": "THROTTLE", "reason": "bad"}
    ]
