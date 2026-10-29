# big diff

A diff tool for code review. `git diff` shows changed lines. big diff shows changed functions and classes, where else in the repo each one is used, and puts the riskiest ones first. It can also send that to a Claude model for a review, and it diffs JSON, CSV and images.

## What it does

- **Code changes** (`bigdiff review`): lists every changed function, class and method, with its diff and the lines in other files that use it. A removed function that's still called comes first. New code comes last. Output is cut to a size limit, and it says what got cut. Python, JS, JSX, TS and TSX are parsed into symbols; other files show as plain diffs.
- **Model review** (`bigdiff check`): sends the changed files plus that report to Haiku, Sonnet or Opus and prints the bugs it finds, with the cost.
- **Data** (`bigdiff data`): compares two JSON or CSV files by value, not by line. Lists of records can be matched by a field like `id`, and numbers can have a tolerance.
- **Images** (`bigdiff image`): counts changed pixels, gives the changed area and can write a heatmap.
- **Web page** (`bigdiff serve`): all of the above in a browser. A hosted version without code review lives in `site/`.

## Install

Needs Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/nathanmoomaw/big-diff
cd big-diff && uv sync
```

To run it from another repo, point uv at this one. It diffs the repo you're in:

```bash
uv run --project /path/to/big-diff bigdiff review
```

## Review a code change

```bash
uv run bigdiff review                    # uncommitted changes vs HEAD
uv run bigdiff review main               # working tree vs main
uv run bigdiff review main --head dev    # main vs dev, no working tree
uv run bigdiff review HEAD~3 src/        # only paths under src/
uv run bigdiff review main --format html -o review.html
uv run bigdiff review main --format json --budget 30000
```

Options: `--head REF` compares against a ref instead of the working tree, `--budget N` caps the output at N characters (default 60000), `--format md|json|html`, `-o FILE`.

Each changed symbol looks like this:

````
### function `diff_text` modified: `def diff_text(…, kind: str, …)` → `def diff_text(…, kind: str | None, …)`  [signature changed, used in other files]
`bigdiff/data.py`
```diff
-def diff_text(a_text: str, b_text: str, kind: str, key: str | None = None, tol: float = 0.0) -> list[Op]:
+def diff_text(a_text: str, b_text: str, kind: str | None, key: str | None = None, tol: float = 0.0) -> list[Op]:
```
Used in 10 place(s):
- `bigdiff/web.py:62` ops = data.diff_text(a, b, k, key or None, tol)
- `tests/test_data_image.py:44` ops = data.diff_text(a, b, "csv", key="id")
````

### Order

| first → last | change |
| --- | --- |
| 0 | symbol removed but still used somewhere |
| 1 | signature changed and it has callers |
| 2 | body changed and it's used in other files |
| 3 | body changed; a new module nothing imports except tests; changed files that can't be parsed (shell, config) |
| 4 | removed and unused; edits outside any symbol (imports, top level) |
| 5 | new symbols; docs |
| 6–7 | binary files, lockfiles (one line each) |

For browser code it also lists other code that listens for the same events. If you change a knob's pointer handlers, a `click` handler on `document` in another file shows up too, with its code.

## Ask a model to review it

Needs an Anthropic API key (`ANTHROPIC_API_KEY`, or `ant auth login`).

```bash
uv run --extra review bigdiff check              # Haiku 5.5, about $0.004
uv run --extra review bigdiff check main --model sonnet   # about $0.03
```

Options are the same as `review`, plus `--model haiku|sonnet|opus` and `--effort low|medium|high|xhigh|max`.

Which model: on six real bugs, three runs each, Sonnet with the report caught 12 of 18, Haiku with it 10, Haiku without it 9, and Opus at default effort 8. None of them flagged the fixed code. Sonnet was the only one that caught the bug spread across two files every time. Details in [research/pr-gate-replay.md](research/pr-gate-replay.md). In practice: Haiku while working, Sonnet before merging.

## Diff data

```bash
uv run bigdiff data old.json new.json --key id --tol 0.001
uv run bigdiff data a.csv b.csv --key id
```

`--key` matches list items or CSV rows by that field, so reordering isn't a change. `--tol` ignores number changes up to that size. `--kind json|csv` overrides the file extension. `true` and `1` count as different.

## Diff images

```bash
uv run bigdiff image before.png after.png --heatmap diff.png
```

`--threshold N` (0–255) is how far a color channel has to move before a pixel counts as changed, so anti-aliasing noise doesn't.

## Web page

```bash
uv run bigdiff serve            # opens http://127.0.0.1:8780
uv run bigdiff serve --port 9000 --no-open
```

Three tabs:

- **Text & data**: paste or drop two files. Plain text gets a side-by-side diff. JSON and CSV get a value diff, with match-by-field and tolerance.
- **Images**: drop two images, get the pixel diff and heatmap.
- **Git repo**: a repo path and two refs, and the review report opens in a new tab.

It only listens on 127.0.0.1 and doesn't save anything.

## Hosted site

Live at **https://big-diff.obfusco.us**. `site/` is the same page as a static site (Vite + React) for people who don't have big diff installed: the Text & data and Images tabs, running big diff's own Python in the browser with [Pyodide](https://pyodide.org). Files aren't uploaded anywhere. The first visit downloads about 10 MB (Python and Pillow); after that the browser caches it. Code review isn't on the site, since it needs your repo.

```bash
cd site && npm install
npm run dev        # http://localhost:5173
npm run build      # static files in site/dist
```

Deploys go to S3 + CloudFront through `.github/workflows/site.yml`, which does nothing until the repo has the `SITE_BUCKET` and `SITE_DISTRIBUTION` variables and the AWS secrets. `scripts/setup-site-aws.sh` sets all of that up once for big-diff.obfusco.us.

## MCP server

Gives Claude the tools `review_packet`, `review_changes`, `data_diff` and `image_diff`:

```bash
claude mcp add -s user bigdiff -- uv run -q --project /path/to/big-diff --extra mcp --extra review python -m bigdiff.mcp_server
```

## How it works

- `git diff` for the files and hunks, including renames and untracked files.
- Python's `ast` and tree-sitter (JS, JSX, TS, TSX) for symbols. A class only counts as changed if its own lines changed, not just one of its methods.
- One `git grep -w` over the repo for every changed name. It can over-report (a common name used for something else) but won't miss a use written in text, including in HTML, templates and shell scripts.
- A second grep for event listeners and dispatches (`addEventListener`, `onClick=`, `.emit`, `.on`), matched by event name.
- Pillow for images.

Tests: `uv run pytest -q`. What's next: [ROADMAP.md](ROADMAP.md).
