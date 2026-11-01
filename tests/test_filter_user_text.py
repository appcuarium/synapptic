"""When boosted turns overflow the budget, the user's own words survive whole and assistant text is trimmed."""

from synapptic.filter import Turn, truncate_to_budget


def test_user_turns_kept_whole_when_they_fit_the_budget():
    turns = []
    for i in range(20):
        turns.append(Turn(role="assistant", text="A" * 2000))
        turns.append(Turn(role="user", text=f"correct the thing number {i} please", boosted=True))
    user_texts = [t.text for t in turns if t.role == "user"]

    result = truncate_to_budget(turns, max_chars=5000)

    assert [t.text for t in result if t.role == "user"] == user_texts
    assert sum(len(t.text) for t in result) <= 5000 + 20 * 4
