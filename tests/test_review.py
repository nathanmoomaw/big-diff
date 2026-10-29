import pytest

review = pytest.importorskip("bigdiff.review")  # the `review` extra


def test_prompt_numbers_lines_and_leads_with_context():
    p = review.prompt({"a.py": "x = 1\ny = 2\n"}, "Spec: s")
    assert p.startswith("Context:\nSpec: s") and "=== a.py ===\n   1  x = 1\n   2  y = 2" in p


def test_check_without_reviewable_changes_makes_no_call(repo, monkeypatch):
    monkeypatch.setattr(review, "review", lambda *a, **k: pytest.fail("no API call expected"))
    repo.write("notes.md", "hi\n")
    repo.commit()
    repo.write("notes.md", "hello\n")
    assert review.check(repo.root) is None


def test_check_sends_changed_source_with_packet(repo, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        review, "review", lambda files, ctx, model, effort: seen.update(files=files, ctx=ctx, model=model)
    )
    repo.write("a.py", "def f():\n    return 1\n")
    repo.commit()
    repo.write("a.py", "def f(x):\n    return x\n")
    review.check(repo.root, model="sonnet")
    assert list(seen["files"]) == ["a.py"] and "function `f` modified" in seen["ctx"] and seen["model"] == "sonnet"


def test_cost_uses_the_answering_model_and_haiku_cliff():
    from bigdiff.models import cost

    assert cost("claude-opus-5-5", 1_000_000, 0) == 4.0
    assert cost("claude-haiku-5-5", 200_000, 0) == 200_000 * 0.10 * 5 / 1e6
    assert cost("some-fallback-model", 1000, 1000) is None  # unknown, not free
