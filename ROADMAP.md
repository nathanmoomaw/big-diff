# ROADMAP

## Active

- [ ] jCodeMunch-backed uses (`find_references` / `find_importers`) instead of `git grep`, when the repo is indexed; keep grep as the fallback
- [ ] Orphan symbols: a new function in an existing file that only tests call (model a module as a synthetic symbol so `uses_many` + `_rank` cover both)
- [ ] Bench: more cases from flyerz/ribbon/NOW history (6 is a direction, not a benchmark); Opus at high effort
- [ ] Code review on the site: a public GitHub compare URL → packet (needs a server or Lambda: git + tree-sitter don't run in Pyodide)
- [ ] Viewer v1: packet view in site/ (symbol tree, callers panel, image diffs inline); v0 is `--format html`
- [ ] File identity from `git diff --raw -z -M` (exact paths, no `+++`/rename text parsing), patch text only for hunks
- [ ] Read file versions through one `git cat-file --batch` process instead of a `git show` per file; precompute symbol nesting once per file for `_own_hash`
- [ ] Semantic no-op detection: formatting-only and rename-only changes collapse to one line
- [ ] More data formats: YAML, TOML, SQLite schema diff, Parquet
- [ ] More languages via tree-sitter: Go, Rust, Swift, CSS (selectors), HTML (ids/components)

## Completed

- [x] Orphan check (2026-10-28): a new module nothing outside tests mentions gets a ranked item
- [x] CI (2026-10-28): pytest + site browser smoke on PRs and main; deploy ships the tested dist/
- [x] Site live (2026-10-28): https://big-diff.obfusco.us via `scripts/setup-site-aws.sh` (private S3 + CloudFront OAC, wildcard cert, Route 53, deploy-only IAM user `big-diff-deploy`)
- [x] Hosted site v0 (2026-10-28): `site/`, Vite + React, big diff's Python under Pyodide in a worker (text, JSON/CSV, images; nothing uploaded); gated S3 deploy workflow
- [x] Web UI (2026-10-28): `bigdiff serve`: text/JSON/CSV/image/git-ref diffs in a local page, stdlib only, light + dark, phone width

- [x] Model review + use (2026-10-26): `bigdiff/review.py` (haiku/sonnet/opus), `bigdiff check`, MCP `review_changes`; bench round 3: Sonnet + packet 12/18, no false alarms on fixed files

- [x] Event-flow edges (2026-10-22): `bigdiff/events.py`. Listeners/props/HTML attrs/dispatch/emit by event name, browser event families, document/window listeners first with their owner's code; `memo`/`useCallback`/`forwardRef` unwrapped in symbols. Bench: #5 (cross-file) caught 5/9 with the packet vs 0 without; totals within noise

- [x] v0 engine (2026-10-21): git hunks, Python `ast` + tree-sitter JS/TS/TSX symbols, own-lines change test, `git grep` uses, ranked packet with budget (md / json / html), JSON/CSV value diff (keyed lists, tolerance), pixel diff with heatmap, CLI, MCP server, 20 tests (incl. Hypothesis)
- [x] PR-gate replay bench (2026-10-21): packet doesn't raise single-file Haiku catch rate (2–3/6 either way); whole-commit packet distracts a file review
