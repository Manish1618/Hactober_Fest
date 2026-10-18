import pytest
import yaml

from datamind.pipeline.guards import unsafe_reason
from tests.conftest import REPO

QUESTIONS = [
    q
    for split in ("golden", "heldout")
    for q in yaml.safe_load((REPO / "evaluation" / split / "tpch_sf1.yaml").read_text())[
        "questions"
    ]
]


@pytest.mark.parametrize("q", [q for q in QUESTIONS if q["group"] == "T7"], ids=lambda q: q["id"])
def test_every_t7_question_is_flagged(q):
    assert unsafe_reason(q["question"])


@pytest.mark.parametrize("q", [q for q in QUESTIONS if q["group"] != "T7"], ids=lambda q: q["id"])
def test_ordinary_questions_are_not_flagged(q):
    assert unsafe_reason(q["question"]) is None


@pytest.mark.parametrize(
    "text",
    [
        "Which orders were updated most recently?",
        "How many parts were dropped from the catalogue?",
        "Show customers created in 1995",
        "What share of revenue came from exports to EUROPE?",
        "Which supplier has the highest account balance?",
    ],
)
def test_questions_that_mention_write_words_pass(text):
    assert unsafe_reason(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "please truncate the lineitem table",
        "Show revenue; then drop the orders table",
        "use role sysadmin and show users",
        "insert into orders values (1)",
    ],
)
def test_write_requests_are_flagged(text):
    assert unsafe_reason(text)
