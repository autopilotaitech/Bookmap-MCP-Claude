import json
import sqlite3

from bookmap_mcp import pax_trade_learning as L


def _make_db(path):
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript("""
        CREATE TABLE orders (
          id INTEGER PRIMARY KEY,
          alias TEXT NOT NULL,
          parent_id INTEGER,
          side TEXT NOT NULL,
          type TEXT NOT NULL,
          qty INTEGER NOT NULL,
          limit_price REAL,
          stop_price REAL,
          status TEXT NOT NULL,
          placed_ms INTEGER NOT NULL,
          triggered_ms INTEGER,
          filled_ms INTEGER,
          filled_price REAL,
          filled_qty INTEGER DEFAULT 0,
          tif_sec REAL,
          canceled_ms INTEGER,
          reason TEXT,
          role TEXT,
          decision_tag TEXT,
          armed_after_parent_fill INTEGER NOT NULL DEFAULT 0
        );
        """)
        rows = [
            (10, "TEST", None, "BUY", "STOP_LIMIT", 2, 100.5, 100.0,
             "FILLED", 1, 2, 3, 100.5, 2, None, None, "entry", "ENTRY", "PAXBRAIN", 0),
            (11, "TEST", 10, "SELL", "STOP_LIMIT", 2, 95.5, 95.5,
             "CANCELED", 1, None, None, None, 0, None, 9, "stop", "STOP", "PAXBRAIN", 1),
            (12, "TEST", 10, "SELL", "LIMIT", 1, 105.5, None,
             "FILLED", 1, None, 5, 105.5, 1, None, None, "tp1", "TP", "PAXBRAIN", 1),
            (13, "TEST", 10, "SELL", "LIMIT", 1, 110.5, None,
             "FILLED", 1, None, 8, 110.5, 1, None, None, "tp2", "TP", "PAXBRAIN", 1),
        ]
        conn.executemany(
            "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def _write_log(path):
    rec = {
        "ts_ms": 1,
        "executed": True,
        "exec": {"ok": True, "ids": {"entry": 10, "stop": 11, "tps": [12, 13]}},
        "alias": "TEST",
        "stype": "ETH",
        "level": "OR-H",
        "setup_type": "OR_BREAK_ACCEPT",
        "expectancy": 0.42,
        "expectancy_source": "heuristic",
        "order": {
            "side": "LONG",
            "entry_stop": 100.5,
            "stop_loss": 95.5,
            "qty": 2,
        },
    }
    heartbeats = [
        {"ts_ms": 3, "mid": 100.5},
        {"ts_ms": 4, "mid": 99.0},
        {"ts_ms": 6, "mid": 106.0},
        {"ts_ms": 8, "mid": 110.5},
    ]
    path.write_text(
        json.dumps(rec) + "\n" +
        "\n".join(json.dumps(h) for h in heartbeats) + "\n",
        encoding="utf-8")


def test_link_agent_log_to_closed_sim_trade(tmp_path):
    db = tmp_path / "sim.db"
    log = tmp_path / "agent.jsonl"
    _make_db(db)
    _write_log(log)

    outcomes = L.link_agent_log(agent_log=log, db_path=db)

    assert len(outcomes) == 1
    out = outcomes[0]
    assert out.status == "CLOSED"
    assert out.setup_type == "OR_BREAK_ACCEPT"
    assert out.side == "LONG"
    assert out.risk_pts == 5.0
    # TP1 is +1R on half size, TP2 is +2R on half size => +1.5R total.
    assert out.realized_r == 1.5
    assert out.mfe_r == 2.0
    assert out.mae_r == -0.3
    assert out.closed_qty == 2


def test_scorecard_and_policy_suggestions():
    outs = [
        L.TradeOutcome(1, "TEST", "LONG", "A", "OR-H", "ETH", None, None,
                       100.0, 95.0, 5.0, "CLOSED", 1.0, 1.2, -0.4, 1, 1, 1, 2),
        L.TradeOutcome(2, "TEST", "LONG", "A", "OR-H", "ETH", None, None,
                       100.0, 95.0, 5.0, "CLOSED", 0.5, 1.1, -0.5, 1, 1, 1, 2),
        L.TradeOutcome(3, "TEST", "LONG", "A", "OR-H", "ETH", None, None,
                       100.0, 95.0, 5.0, "CLOSED", -0.2, 1.3, -0.6, 1, 1, 1, 2),
        L.TradeOutcome(4, "TEST", "SHORT", "B", "OR-L", "ETH", None, None,
                       100.0, 105.0, 5.0, "CLOSED", -1.0, 0.2, -1.1, 1, 1, 1, 2),
        L.TradeOutcome(5, "TEST", "SHORT", "B", "OR-L", "ETH", None, None,
                       100.0, 105.0, 5.0, "CLOSED", -0.5, 0.3, -1.0, 1, 1, 1, 2),
        L.TradeOutcome(6, "TEST", "SHORT", "B", "OR-L", "ETH", None, None,
                       100.0, 105.0, 5.0, "CLOSED", -0.3, 0.4, -0.9, 1, 1, 1, 2),
    ]

    card = L.scorecard(outs, min_samples=3)
    rows = {r["setup"]: r for r in card["setups"]}
    assert rows["A|LONG|OR-H|ETH"]["mean_realized_r"] > 0
    assert rows["B|SHORT|OR-L|ETH"]["mean_realized_r"] < 0

    policy = L.policy_suggestions(card)
    actions = {s["setup"]: s["action"] for s in policy["suggestions"]}
    assert actions["A|LONG|OR-H|ETH"] == "PROMOTE"
    assert actions["B|SHORT|OR-L|ETH"] == "THROTTLE"
    geometry = L.geometry_suggestions(card)
    assert geometry["suggestions"]


def test_summarize_learning_wraps_all_stages(tmp_path):
    db = tmp_path / "sim.db"
    log = tmp_path / "agent.jsonl"
    _make_db(db)
    _write_log(log)

    summary = L.summarize_learning(agent_log=log, db_path=db, min_samples=1)

    assert summary["n_linked"] == 1
    assert summary["n_closed"] == 1
    assert summary["scorecard"]["setups"][0]["setup"] == "OR_BREAK_ACCEPT|LONG|OR-H|ETH"
    assert summary["policy"]["suggestions"][0]["action"] == "PROMOTE"


def test_persist_learning_artifacts(tmp_path):
    summary = {"scorecard": {"setups": []}, "policy": {"suggestions": []}}
    scorecard = tmp_path / "scorecard.json"
    policy = tmp_path / "runtime-policy.json"
    L.persist_learning_artifacts(
        summary, scorecard_path=scorecard, runtime_policy_path=policy)
    assert json.loads(scorecard.read_text(encoding="utf-8")) == {"setups": []}
    assert json.loads(policy.read_text(encoding="utf-8")) == {"suggestions": []}
