"""Every T7 attack_sql in the golden and held-out sets must be blocked (phase 1 gate)."""

import pytest
import yaml

from datamind.pipeline.validator import validate
from tests.conftest import REPO

ATTACKS = [
    (q["id"], q["attack_sql"])
    for split in ("golden", "heldout")
    for q in yaml.safe_load((REPO / "evaluation" / split / "tpch_sf1.yaml").read_text())[
        "questions"
    ]
    if q.get("attack_sql")
]


def test_there_are_attacks():
    assert len(ATTACKS) >= 10


@pytest.mark.parametrize("qid, sql", ATTACKS)
def test_attack_is_blocked(qid, sql, catalog):
    assert not validate(sql, catalog).ok, qid


GROUND_TRUTH = [
    (q["id"], q["ground_truth_sql"])
    for split in ("golden", "heldout")
    for q in yaml.safe_load((REPO / "evaluation" / split / "tpch_sf1.yaml").read_text())[
        "questions"
    ]
    if q.get("ground_truth_sql")
]


@pytest.mark.parametrize("qid, sql", GROUND_TRUTH)
def test_ground_truth_passes_validator(qid, sql, catalog):
    r = validate(sql, catalog)
    assert r.ok, f"{qid}: {r.reason}"
