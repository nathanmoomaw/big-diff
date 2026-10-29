# Does a big diff packet help a Haiku review?

## Round 3 (2026-10-26): which model reads the packet best, and false alarms

**Sonnet 5.5 + the file packet catches the most: 12/18, including the cross-file bug #5 in 3 of 3 runs, for about $0.03 a review. No variant raised a false alarm on the fixed files.** The reviewer is big diff's own `bigdiff/review.py`.

| # | bug | A: Haiku, message | C: Haiku + packet | S: Sonnet 5.5 + packet | O: Opus 5.5 + packet |
|---|---|---|---|---|---|
| 1 | React keys collide | 3 | 3 | 3 | 3 |
| 2 | music player never wired up | 0 | 0 | 0 | 0 |
| 3 | TDZ crash | 3 | 3 | 3 | 3 |
| 4 | Safari click retarget → shake | 0 | 0 | 0 | 0 |
| 5 | click after knob drag → shake (other file) | 0 | 1 | **3** | 0 |
| 6 | `lastStopRef` re-created | 3 | 3 | 3 | 2 |
| | **total** | **9/18** | **10/18** | **12/18** | **8/18** |
| | cost per review | $0.0013 | $0.0021 | $0.031 | $0.069 |

- **False alarms** (`--clean`: the same files after the fix, where a hit would mean the review claims the fixed bug is still there): C 0/12, S 0/12.
- **Opus 5.5 at its default effort (medium) was terse**: 0–1 findings per file, and it missed #5 every time while spending most on thinking. Not tried at higher effort; Sonnet already wins on catches and costs less than half as much.
- Sonnet's #5 findings name the mechanism exactly: the knob doesn't stop the compatibility click, which reaches `useShake`'s window click handler, whose exemptions don't cover knobs outside the controls container.

Use: `bigdiff check` (Haiku, ~$0.002–0.004) as the routine pass, `--model sonnet` as the final pass before merging.

## Round 2 (2026-10-22): event-flow edges, 3 repeats per variant

**The packet's real gain is the cross-file bug: #5 caught in 5 of 9 runs with the file packet, 0 in every run without it.** Across all six cases the totals sit within run-to-run noise: message-only scored 7–9/18 over three full runs, file packet 8–11/18.

What changed in the packet: symbols wrapped in `memo(…)`/`useCallback(…)` are recognised (RotaryKnob had none before), and each changed symbol lists **other code sharing its events**. Event names are widened to the browser's families (a pointer press also yields `click`). Page-level listeners (`document`/`window`, decided per call from its receiver) come first, with the function that owns them and that function's code, so the reviewer can see what the handler does.

Final full run, 3 runs each:

| # | bug | A: message | C: + file packet |
|---|---|---|---|
| 1 | React keys collide | 3 | 3 |
| 2 | music player never wired up | 0 | 0 |
| 3 | TDZ crash | 3 | 3 |
| 4 | Safari click retarget → shake | 0 | 0 |
| 5 | click after knob drag → shake (other file) | 0 | 1 |
| 6 | `lastStopRef` re-created | 3 | 3 |
| | **total** | **9/18** | **10/18** |

Plus a 6-run check of #5 alone with the same packet: C 4/6. Earlier full runs: A 8, C 11; then A 7, C 8 (that run's packet listed other components' JSX props as edges, 52 of them, and #5 fell to 0/3; props are no longer indexed).

Scoring: a regex per case (bench/pr_gate.py), checked by hand; `--rescore` re-scores saved outputs. A C review costs ~$0.0016.

What didn't work along the way: the edge list alone (listener lines, no handler code) and a one-line note on compatibility clicks both left #5 at 0/6. Only the handler's code got it caught, and noisy edges (other elements' props) cancelled the gain. #4 is the same family, but the changed file is the listener itself, and Haiku doesn't connect Safari's retargeting. #2 (a feature never wired up) is out of reach for any diff context.

## Round 1 (2026-10-21)

**Not yet, on this test.** Adding the packet didn't raise Haiku 5.5's catch rate on six real bugs: 2–3 of 6 with or without it, and repeat runs of the same input moved by one case. A packet covering the whole commit pulled the review toward other files and cost it one bug.

## Setup

Six real bug-fix commits: flyerz 43c820b, de6eacb; ribbon a30b400, 5cd6da2, c46e3e0, fd912c0. For each case, the buggy file is the fix's parent version. The "intro" commit is the one git blame names for most lines the fix removed. Haiku 5.5 (effort medium) reviewed the buggy file under four inputs. Each finding was scored by hand: did it name the bug the fix names? Script: [bench/pr_gate.py](../bench/pr_gate.py) (~$0.06 a full run).

| # | bug (fixed later) | A: message | A2: message, repeat | B: + whole-commit packet | C: + this-file packet |
|---|---|---|---|---|---|
| 1 | React keys collide (padding cards reuse `flyerIndex`) | caught | missed (0 findings) | caught (high) | missed |
| 2 | music player never wired up on direct URLs | missed | missed | missed | missed |
| 3 | `showMilestone` in deps before its `const` (TDZ) | caught | caught | caught | caught |
| 4 | Safari retargets the click, so a ribbon press shakes | missed | missed | missed | missed |
| 5 | the click after a knob drag triggers `useShake` (other file) | missed | missed | missed | missed |
| 6 | `lastStopRef` re-created every render | caught | caught | **missed** | caught |
| | **total** | **3/6** | **2/6** | **2/6** | **2/6** |

Cost per review: A ~$0.001, B/C ~$0.0035 (about 20K input tokens instead of 3K).

## What it shows

- **Noise is as big as the effect.** A and A2 got the same input and differ on #1. One run per case can't show a one-case improvement. Future runs need 3+ repeats per variant.
- **A whole-commit packet distracts a file review.** In B, Haiku spent its findings on other files in the commit (auth timing leak, GoopOverlay positioning, a removed Keys toggle) and dropped #6. Some of those look real, so the packet works for reviewing the *commit*. It doesn't help a review that's meant to focus on one file.
- **Callers don't reach these cross-file bugs.** #5 (and #4) are event interactions: a document-level click listener in `useShake` reacting to a click that `RotaryKnob` didn't stop. Neither file calls the other, so a callers list can't link them. That needs event-flow edges (`addEventListener`/`on*` handlers matched by event name), now on the roadmap.
- **A missing feature (#2) can't show up in a diff.** No context packet will catch it. A spec check might.
- **A stronger final pass still matters.** In the original test, Opus caught #1 every time it was asked; Haiku catches it about half the time.

## Next

- Event-flow edges in the blast radius (fixes the #4/#5 class).
- 3 repeats per variant, plus clean files to measure false alarms.
- Packet → Opus (or Haiku first, Opus on what Haiku flags), scored the same way.
