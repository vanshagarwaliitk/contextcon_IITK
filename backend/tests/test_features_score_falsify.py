from datetime import date, timedelta

import pytest

from app.models import Signal
from app.pipeline import falsify, features
from app.pipeline.normalise import seniority_tier
from app.pipeline.score import label_for, score

TODAY = date(2026, 6, 30)


def test_velocity_math():
    assert features.velocity_ratio(30, 30, 90, 90) == 1.0
    assert features.velocity_ratio(20, 30, 30, 90) == 2.0
    assert features.velocity_ratio(5, 30, 0, 90) is None


def test_evergreen_ratio():
    rows = [{"norm_title": "x", "city": c} for c in ("a", "b", "c")] + [{"norm_title": "y", "city": "a", "reposted": False}]
    assert features.evergreen_ratio(rows) == 0.75
    assert features.evergreen_ratio([]) is None
    assert features.evergreen_ratio([{"desc_hash": "h"}, {"desc_hash": "h", "norm_title": "z"}]) == 1.0


@pytest.mark.parametrize("title,tier", [("Robotics Intern", 0), ("Engineer", 1), ("Senior Engineer", 2),
                                        ("Staff Engineer", 3), ("Head of Robotics", 4), ("VP Engineering", 4)])
def test_seniority_regex(title, tier):
    assert seniority_tier(title) == tier


def _sig(i, typ="hiring_cluster", days=5, source="greenhouse", tier=1, **f):
    return Signal(id=i, investigation_id="inv", signal_type=typ, title=i, summary=i, source_name=source,
                  observed_at=(TODAY - timedelta(days=days)).isoformat(), features={"seniority_tier": tier, **f})


def _hyp(ids, matrix=None):
    return {"id": "hyp_001", "theme_id": "t1", "hypothesis_type": "capability_buildout", "supporting_signal_ids": ids,
            "contradicting_signal_ids": [], "matrix": matrix or []}


def test_score_high_and_caps():
    sigs = {"s1": _sig("s1", tier=3), "s2": _sig("s2", source="lever"), "s3": _sig("s3"),
            "w1": _sig("w1", typ="web_evidence", source="primary", source_class="primary")}
    feats = {"velocity_ratio": 3.0, "theme_postings_recent": 12}
    r = score(_hyp(list(sigs)), sigs, [], feats, TODAY)
    assert r["label"] == "High" and r["evidence_status"] == "corroborated"
    # one contradicting novelty test drops points deterministically
    r2 = score(_hyp(list(sigs)), sigs, [{"test_id": "novelty_test", "outcome": "contradicts", "explanation": ""}], feats, TODAY)
    assert r2["points"] == r["points"] - 2 and r2["evidence_status"] == "conflicting"


def test_cap_only_one_type():
    sigs = {f"s{i}": _sig(f"s{i}", tier=3, source=["a", "b"][i % 2]) for i in range(4)}
    r = score(_hyp(list(sigs)), sigs, [], {"velocity_ratio": 3.0, "theme_postings_recent": 12}, TODAY)
    assert r["points"] >= 7 and r["label"] == "Medium" and "only one signal type" in r["caps_applied"]


def test_cap_alternative_consistent_with_all():
    sigs = {"s1": _sig("s1", tier=3), "s2": _sig("s2", source="lever"), "s3": _sig("s3"),
            "w1": _sig("w1", typ="web_evidence", source="primary", source_class="primary")}
    matrix = [{"signal_id": s, "hypothesis_ref": "H2", "relation": "consistent"} for s in sigs]
    r = score(_hyp(list(sigs), matrix), sigs, [], {"velocity_ratio": 3.0, "theme_postings_recent": 12}, TODAY)
    assert r["label"] == "Medium" and any("H2" in c for c in r["caps_applied"])


def test_label_mapping():
    assert [label_for(p) for p in (8, 7, 6, 4, 3, -2)] == ["High", "High", "Medium", "Medium", "Low", "Low"]


def test_falsify_tests_synthetic():
    f = {"concentration_recent": 0.06, "concentration_older": 0.05, "evergreen_ratio": 0.5, "theme_postings_recent": 2,
         "peer_share_gap": 0.0, "peer_mean_share": 0.06, "seniority_share": 0.0, "geo_new_countries": ["DE"], "_ids": {}}
    h = _hyp([]) | {"hypothesis_type": "new_geography"}
    assert falsify.novelty_test(h, {}, f).outcome == "contradicts"
    assert falsify.evergreen_test(h, {}, f).outcome == "contradicts"
    assert falsify.scale_test(h, {}, f).outcome == "contradicts"
    assert falsify.peer_test(h, {}, f).outcome == "contradicts"
    assert falsify.seniority_test(h, {}, f).outcome == "neutral"
    assert falsify.geo_test(h, {}, f).outcome == "supports"
    f2 = f | {"concentration_older": 0.0, "evergreen_ratio": 0.1, "theme_postings_recent": 10, "peer_mean_share": 0.01}
    assert falsify.novelty_test(h, {}, f2).outcome == "supports"
    assert falsify.peer_test(h, {}, f2).outcome == "supports"


def test_unknown_test_dropped_and_mandatory_added():
    sel = falsify.select_tests(["bogus", "seniority_test"], "capability_buildout")
    assert "bogus" not in sel and set(falsify.MANDATORY) <= set(sel) and "geo_test" not in sel
