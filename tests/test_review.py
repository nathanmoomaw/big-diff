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


def _fake_claude(monkeypatch, out, seen=None):
    import json
    import subprocess

    def run(cmd, **kw):
        if seen is not None:
            seen.update(cmd=cmd, **kw)
        return subprocess.CompletedProcess(cmd, 0, json.dumps(out), "")

    monkeypatch.setenv("BIGDIFF_REVIEW_BACKEND", "claude")
    monkeypatch.setattr(review.subprocess, "run", run)


def test_claude_backend_parses_structured_output_without_api_key(monkeypatch):
    seen = {}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    out = {
        "subtype": "success",
        "is_error": False,
        "total_cost_usd": 0.0012,
        "structured_output": {"findings": [{"file": "a.py", "line": 2, "severity": "high", "summary": "off by one"}]},
        "modelUsage": {"claude-haiku-5-5": {"inputTokens": 10, "cacheCreationInputTokens": 90, "outputTokens": 5}},
    }
    _fake_claude(monkeypatch, out, seen)
    r = review.review({"a.py": "x = 1\n"})
    assert [f.summary for f in r.findings] == ["off by one"] and r.backend == "claude"
    assert (r.model, r.tokens_in, r.tokens_out) == ("claude-haiku-5-5", 100, 5) and "subscription" in r.report()
    assert "ANTHROPIC_API_KEY" not in seen["env"] and seen["cmd"][seen["cmd"].index("--tools") + 1] == ""


def test_claude_backend_error_raises(monkeypatch):
    _fake_claude(monkeypatch, {"subtype": "error_max_turns", "is_error": True, "result": "nope"})
    with pytest.raises(RuntimeError, match="error_max_turns"):
        review.review({"a.py": "x = 1\n"})


def test_unknown_backend_raises(monkeypatch):
    monkeypatch.setenv("BIGDIFF_REVIEW_BACKEND", "bogus")
    with pytest.raises(RuntimeError, match="claude or api"):
        review.review({"a.py": "x = 1\n"})
