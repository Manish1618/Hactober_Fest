from datamind.pipeline.answer import (
    fill_placeholders,
    format_value,
    stray_numbers,
    template_answer,
)
from datamind.pipeline.context import MAX_TURNS, Conversation, Turn


def test_format_value():
    assert format_value(1234567) == "1,234,567"
    assert format_value(1234.5) == "1,234.50"
    assert format_value(3.0) == "3"
    assert format_value("ASIA") == "ASIA"


def test_fill_placeholders():
    text, missing = fill_placeholders(
        "{r1.nation} leads with {r1.REVENUE}; {r2.nation} is second. {r9.nation}",
        ["nation", "revenue"],
        [["INDIA", 1000.0], ["CHINA", 900.5]],
    )
    assert text.startswith("INDIA leads with 1,000; CHINA is second.")
    assert missing == ["{r9.nation}"]


def test_stray_numbers_ignore_placeholders_and_question():
    assert stray_numbers("In 1995 it was {r1.revenue}.", "revenue in 1995?") == []
    assert stray_numbers("It was 42.", "revenue in 1995?") == ["42"]


def test_template_answer():
    assert template_answer(["n"], [[5]], 1) == "n: 5"
    assert template_answer(["a", "b"], [[1, 2]], 1) == "a: 1; b: 2"
    assert "first 5 of 7" in template_answer(["a"], [[i] for i in range(7)], 7)


def test_conversation_keeps_the_last_turns():
    c = Conversation()
    for i in range(MAX_TURNS + 2):
        c.add(
            Turn(question=f"q{i}", standalone_question=f"q{i}", status="ANSWERED", sql="SELECT\n1")
        )
    assert len(c.turns) == MAX_TURNS
    text = c.history_text()
    assert text.startswith("Turn 1 question: q2")
    assert "Turn 1 SQL: SELECT 1" in text
