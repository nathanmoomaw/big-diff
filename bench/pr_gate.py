"""Replay a PR-gate test (2026-10-21): six real bugs from flyerz and ribbon, reviewed with and
without a big diff packet, by Haiku and by stronger models.

Each case is a real bug-fix commit. The buggy file is the fix's parent version; the "intro" commit is the one
git blame names for most lines the fix removed. Variants (model + context):
  A  Haiku, intro commit message only          C  Haiku + this file's packet (symbols, uses, event edges)
  B  Haiku + the whole commit's packet         S  Sonnet 5.5 + file packet      O  Opus 5.5 + file packet
--clean reviews the fixed version instead: a regex hit there is a false alarm. Scoring is a regex per case
(spot-check the quoted lines); --rescore re-scores saved outputs. Needs big diff's `review` extra.

  uv run --extra review python bench/pr_gate.py [--variants A,C,O] [--repeats 3] [--only 1,5] [--clean]
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bigdiff import packet
from bigdiff.gitdiff import diff_files, git, parse_unified, read_at
from bigdiff.review import review as model_review

SITES = Path.home() / "Sites"
CASES = [  # (n, repo, fix commit, file, the bug the fix names, regex a finding must match to count)
    (
        1,
        "flyerz",
        "43c820b",
        "useCanvasLayout.js",
        "React key from flyerIndex; padding cards reuse it, keys collide, flyers stuck",
        r"(?i)key.{0,80}(collid|duplicat|clash|not unique)|(collid|duplicat|clash).{0,80}key",
    ),
    (
        2,
        "flyerz",
        "de6eacb",
        "FlyerPage.js",
        "music player never wired up on direct flyer URLs",
        r"(?i)(music|audio) player.{0,120}(never|not|missing|isn.t|doesn.t).{0,60}(render|wire|mount|show|appear|load)|direct (flyer )?(url|link|route).{0,120}(music|player)",
    ),
    (
        3,
        "ribbon",
        "a30b400",
        "App.jsx",
        "showMilestone used in a deps array before its const (TDZ crash)",
        r"(?i)temporal[- ]dead|TDZ|before (it is |its )?(declar|initiali)",
    ),
    (
        4,
        "ribbon",
        "5cd6da2",
        "useShake.js",
        "Safari retargets the click outside the ribbon, so a ribbon press triggers shake",
        r"(?i)retarget|Safari.{0,120}(click|target)|(ribbon|pointer|drag).{0,120}(trigger|fire|cause|start)s? .{0,40}shake",
    ),
    (
        5,
        "ribbon",
        "c46e3e0",
        "RotaryKnob.jsx",
        "the click after a knob drag ends triggers useShake in another file",
        r"(?i)click.{0,160}(shake|useShake)|(shake|useShake).{0,160}click",
    ),
    (
        6,
        "ribbon",
        "fd912c0",
        "ActivationMode.jsx",
        "lastStopRef = { current: 0 } re-created every render, double-tap kill-all never fires",
        r"(?i)lastStopRef.{0,200}(recreat|re-creat|every render|useRef)",
    ),
]


def full_path(repo: Path, fix: str, name: str) -> str:
    changed = [f.path for f in diff_files(repo, f"{fix}^", fix)]
    hits = [p for p in changed if p.endswith("/" + name) or p == name]
    if not hits:
        raise SystemExit(f"{name} not in {fix}: {changed}")
    return hits[0]


def intro_commit(repo: Path, fix: str, path: str) -> str:
    """Commit that git blame names for most of the lines the fix removed (or changed) in path."""
    (f,) = parse_unified(git(repo, "diff", "-U0", f"{fix}^", fix, "--", path))
    wanted = f.old_changed() | {
        max(h.old_start, 1) for h in f.hunks if h.old_len == 0
    }  # pure additions: the line above
    # one whole-file blame instead of one per line: porcelain headers are "<sha> <orig> <final> [<n>]"
    owner, votes = {}, collections.Counter()
    for line in git(repo, "blame", "--porcelain", f"{fix}^", "--", path).splitlines():
        parts = line.split()
        if len(parts) >= 3 and len(parts[0]) == 40 and parts[2].isdigit():
            owner[int(parts[2])] = parts[0]
    for n in sorted(wanted):  # line order, so a tied vote resolves as before
        if n in owner:
            votes[owner[n]] += 1
    if not votes:
        raise SystemExit(f"blame found no owner for the lines {fix} changed in {path}")
    sha = votes.most_common(1)[0][0]
    if not git(repo, "rev-list", "--parents", "-n", "1", sha).split()[1:]:
        raise SystemExit(f"intro commit {sha[:8]} is a root commit: no parent to diff against")
    return sha


# variant → (model, context kind)
VARIANTS = {"A": ("haiku", "A"), "B": ("haiku", "B"), "C": ("haiku", "C"), "S": ("sonnet", "C"), "O": ("opus", "C")}


def review(path: str, text: str, context: str, model: str) -> tuple[str, float | None]:
    """Report text and USD cost; a failed review is recorded, not raised (RuntimeError is review()'s contract)."""
    try:
        r = model_review({path: text}, context, model)
    except RuntimeError as e:
        return f"review failed: {e}", 0.0
    return r.report(), r.cost  # None: answered by a model models.py doesn't price


RESULTS = Path(__file__).parent / "pr_gate_results.json"
REGEX = {n: hit_re for n, *_, hit_re in CASES}


def hit(case: int, out: str) -> str | None:
    """The first finding line that names the case's bug, or None."""
    return next((line for line in out.splitlines() if re.search(REGEX[case], line)), None)


def report(rows: list[dict], clean: bool = False) -> None:
    """Per-case run marks and per-variant totals, scored from the saved outputs."""
    score: dict[str, list[int]] = {}
    for row in rows:
        for v, outs in row["runs"].items():
            hits = [hit(row["case"], out) for out in outs]
            got = score.setdefault(v, [0, 0])
            got[0] += sum(map(bool, hits))
            got[1] += len(hits)
            print(
                f"#{row['case']} {v}: {''.join('x' if h else '.' for h in hits)}"
                + next((f"  {h[:200]}" for h in hits if h), "")
            )
    what = "FALSE ALARMS (runs claiming the fixed bug is still there)" if clean else "score (runs that named the bug"
    print(f"{what}, by regex; spot-check the quoted lines):")
    for v, (got, total) in score.items():
        print(f"  {v}: {got}/{total}")


def contexts(n: int, name: str, fix: str, fname: str, variants: list[str], budget: int) -> tuple[Path, str, str, dict]:
    repo = SITES / name
    path = full_path(repo, fix, fname)
    intro = intro_commit(repo, fix, path)
    spec = "Spec (message of the commit that wrote this code):\n" + git(repo, "log", "-1", "--format=%B", intro).strip()
    ctx = {"A": spec}
    kinds = {VARIANTS[v][1] for v in variants}
    if "B" in kinds:
        whole = packet.build(repo, f"{intro}^", intro, budget).markdown()
        ctx["B"] = (
            f"{spec}\n\nReview packet for that commit (what changed across the repo, ranked, with uses elsewhere):\n\n{whole}"
        )
    if "C" in kinds:
        one = packet.build(repo, f"{intro}^", intro, budget, paths=(path,)).markdown()
        ctx["C"] = (
            f"{spec}\n\nReview packet for this file in that commit (its changed symbols, where they are used, "
            f"and other code sharing their events):\n\n{one}"
        )
    return repo, path, intro, ctx


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--budget", type=int, default=40_000)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--variants", default="A,C", help=", ".join(f"{v}={m}+{c}" for v, (m, c) in VARIANTS.items()))
    ap.add_argument("--clean", action="store_true", help="review the fixed version: hits are false alarms")
    ap.add_argument(
        "--rescore", action="store_true", help="re-score the saved outputs with the current regexes, no API calls"
    )
    a = ap.parse_args()
    results = RESULTS.with_stem(RESULTS.stem + "_clean") if a.clean else RESULTS
    if a.rescore:
        report(json.loads(results.read_text()), a.clean)
        return 0
    only = {int(x) for x in a.only.split(",") if x}
    variants = a.variants.split(",")
    rows, jobs = [], []
    for n, name, fix, fname, *_ in CASES:
        if only and n not in only:
            continue
        repo, path, intro, ctx = contexts(n, name, fix, fname, variants, a.budget)
        text = read_at(repo, fix if a.clean else f"{fix}^", path)  # fixed, or the buggy parent
        rows.append({"case": n, "intro": intro[:8], "runs": {v: [] for v in variants}})
        for v in variants:
            model, kind = VARIANTS[v]
            jobs += [(rows[-1], v, partial(review, path, text, ctx[kind], model)) for _ in range(a.repeats)]
    spent, unpriced = 0.0, 0
    with ThreadPoolExecutor(8) as pool:  # every review of every case: independent and network-bound
        futures = {pool.submit(job[2]): job for job in jobs}
        for fut in as_completed(futures):
            row, v, _ = futures[fut]
            out, usd = fut.result()
            row["runs"][v].append(out)
            if usd is None:
                unpriced += 1
            else:
                spent += usd
            results.write_text(json.dumps(rows, indent=2))  # after each review: a crash keeps what's paid for
    report(rows, a.clean)
    print(
        f"cost ${spent:.3f} over {len(jobs)} reviews"
        + (f" ({unpriced} answered by an unpriced model)" if unpriced else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
