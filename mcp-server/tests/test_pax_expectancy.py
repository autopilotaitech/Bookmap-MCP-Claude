import csv

from bookmap_mcp import pax_expectancy as E


def test_verdict_to_r_scores_operator_outcomes():
    assert E.verdict_to_r("SUSTAINED_HIT") == 1.5
    assert E.verdict_to_r("HIT") == 1.0
    assert E.verdict_to_r("PARTIAL_HIT") == 0.35
    assert E.verdict_to_r("STALE") == 0.0
    assert E.verdict_to_r("MISS") == -1.0
    assert E.verdict_to_r("NOPE") is None


def test_summarize_ifl_rows_builds_side_level_stats():
    rows = [
        {"regime": "ACCUMULATION", "commit_level": "OR-H", "verdict": "HIT"},
        {"regime": "ACCUMULATION", "commit_level": "OR-H", "verdict": "MISS"},
        {"regime": "ACCUMULATION", "commit_level": "OR-H", "verdict": "PARTIAL_HIT"},
    ]
    stats = E.summarize_ifl_rows(rows)
    found = E.stats_for_setup(
        stats, kind="OR_BREAK_ACCEPT", side="LONG",
        level="OR-H", session_type="RTH")
    assert found is not None
    assert found.n == 3
    assert found.avg_r == round((1.0 - 1.0 + 0.35) / 3, 4)
    assert found.hit_rate == round(1 / 3, 4)
    assert found.partial_rate == round(1 / 3, 4)
    assert found.miss_rate == round(1 / 3, 4)


def test_blend_expectancy_waits_for_min_sample_size():
    assert E.blend_expectancy(0.40, -1.0, 2) == 0.40
    assert E.blend_expectancy(0.40, -1.0, 3) < 0.40
    assert E.blend_expectancy(0.40, -1.0, 10) < E.blend_expectancy(0.40, -1.0, 3)


def test_load_ifl_stats_from_csv(tmp_path):
    path = tmp_path / "ifl.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["regime", "commit_level", "verdict"])
        writer.writeheader()
        writer.writerow({"regime": "DISTRIBUTION", "commit_level": "OR-L", "verdict": "MISS"})
        writer.writerow({"regime": "DISTRIBUTION", "commit_level": "OR-L", "verdict": "HIT"})
        writer.writerow({"regime": "DISTRIBUTION", "commit_level": "OR-L", "verdict": "HIT"})
    stats = E.load_ifl_stats(path)
    found = E.stats_for_setup(
        stats, kind="OR_BREAK_ACCEPT", side="SHORT",
        level="OR-L", session_type="ETH")
    assert found is not None
    assert found.n == 3
    assert found.avg_r == round((-1.0 + 1.0 + 1.0) / 3, 4)
