from engine.core.memory.memory_formatter import MemoryFormatter


def test_keeps_only_the_most_recent_requested_turns():
    records = [
        {"q": "first", "a": "one"},
        {"q": "second", "a": "two"},
        {"q": "third", "a": "three"},
    ]

    messages = MemoryFormatter().format(records, last_n=2)

    assert messages == [
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "two"},
        {"role": "user", "content": "third"},
        {"role": "assistant", "content": "three"},
    ]


def test_preserves_question_and_answer_at_their_exact_limits():
    question = "q" * 200
    answer = "a" * 500

    messages = MemoryFormatter().format([{"q": question, "a": answer}])

    assert messages == [
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer},
    ]


def test_truncates_question_and_answer_one_character_past_their_limits():
    messages = MemoryFormatter().format([{"q": "q" * 201, "a": "a" * 501}])

    assert messages == [
        {"role": "user", "content": "q" * 197 + "..."},
        {"role": "assistant", "content": "a" * 497 + "..."},
    ]


def test_preserves_messages_at_the_exact_total_content_limit():
    formatter = MemoryFormatter(max_question_len=2000, max_answer_len=2000)

    messages = formatter.format([{"q": "q" * 2000, "a": ""}])

    assert sum(len(message["content"]) for message in messages) == 2000


def test_format_as_text_never_exceeds_the_total_content_limit():
    formatter = MemoryFormatter(max_question_len=2000, max_answer_len=2000)

    text = formatter.format_as_text([{"q": "q" * 2000, "a": ""}])

    assert len(text) <= 2000


def test_zero_requested_turns_returns_no_history():
    records = [{"q": "first", "a": "one"}]

    assert MemoryFormatter().format(records, last_n=0) == []
