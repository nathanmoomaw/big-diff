"""Git plumbing: changed files and hunks between a base ref and a head ref (or the working tree)."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # git's well-known empty tree
MAX_NEW_FILE_BYTES = 400_000  # untracked files bigger than this are listed, not inlined

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    lines: list[str] = field(default_factory=list)  # each starts with "+", "-" or " "

    def header(self) -> str:
        return f"@@ -{self.old_start},{self.old_len} +{self.new_start},{self.new_len} @@"

    def text(self) -> str:
        return "\n".join([self.header(), *self.lines])

    def _changed(self, mark: str, start: int, other: str) -> set[int]:
        out, n = set(), start
        for line in self.lines:
            if line.startswith(mark):
                out.add(n)
            if not line.startswith(other):
                n += 1
        return out

    def new_changed(self) -> set[int]:
        """Line numbers in the new file that this hunk adds."""
        return self._changed("+", self.new_start, "-")

    def old_changed(self) -> set[int]:
        """Line numbers in the old file that this hunk removes."""
        return self._changed("-", self.old_start, "+")


@dataclass
class FileDiff:
    path: str
    status: str = "M"  # A added, M modified, D deleted, R renamed
    old_path: str | None = None
    binary: bool = False
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def added(self) -> int:
        return sum(line.startswith("+") for h in self.hunks for line in h.lines)

    @property
    def removed(self) -> int:
        return sum(line.startswith("-") for h in self.hunks for line in h.lines)

    def new_changed(self) -> set[int]:
        return set().union(*(h.new_changed() for h in self.hunks))

    def old_changed(self) -> set[int]:
        return set().union(*(h.old_changed() for h in self.hunks))


def run(repo: str | Path, *args: str, ok: tuple[int, ...] = (0,), text: bool = True) -> subprocess.CompletedProcess:
    """Run git in repo; raise unless the exit code is in `ok`."""
    r = subprocess.run(
        ["git", "-c", "core.quotePath=false", "-C", str(repo), *args],
        capture_output=True,
        text=text,
        check=False,
        **({"errors": "replace"} if text else {}),
    )
    if r.returncode not in ok:
        err = r.stderr if text else r.stderr.decode("utf-8", "replace")
        raise RuntimeError(f"git {' '.join(args)}: {err.strip()}")
    return r


# Prose, lockfiles and build output mention names without using them.
GREP_EXCLUDE = (":!*.md", ":!*.txt", ":!*.lock", ":!package-lock.json", ":!*.min.js", ":!*.map")


def grep(
    repo: str | Path, ref: str | None, *args: str, exclude: tuple[str, ...] = GREP_EXCLUDE
) -> Iterator[tuple[str, int, str]]:
    """(path, line, text) for each `git grep -n -I` match at ref (None = working tree, untracked included)."""
    out = run(
        repo, "grep", "-n", "-I", *([] if ref else ["--untracked"]), *args, *([ref] if ref else []), "--", ".", *exclude,
        ok=(0, 1),  # 1 = no matches; anything else is a real failure, not "nothing found"
    ).stdout  # fmt: skip
    for line in out.splitlines():
        if ref and line.startswith(ref + ":"):
            line = line[len(ref) + 1 :]
        path, _, rest = line.partition(":")
        num, _, text = rest.partition(":")
        if num.isdigit():
            yield path, int(num), text


def git(repo: str | Path, *args: str) -> str:
    return run(repo, *args).stdout


def resolve(repo: str | Path, ref: str) -> str:
    """A ref as given, or git's empty tree when it's HEAD of a repo with no commits yet."""
    if ref == "HEAD" and run(repo, "rev-parse", "--verify", "-q", "HEAD^{commit}", ok=(0, 1)).returncode:
        return EMPTY_TREE
    return ref


def parse_unified(text: str) -> list[FileDiff]:
    """Parse `git diff` output (with --no-color, -M) into files and hunks."""
    files: list[FileDiff] = []
    cur: FileDiff | None = None
    hunk: Hunk | None = None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.*) b/(.*)$", line)
            cur = FileDiff(path=m.group(2) if m else line.split()[-1])
            files.append(cur)
            hunk = None
        elif cur is None:
            continue
        elif hunk is None and line.startswith("new file mode"):
            cur.status = "A"
        elif hunk is None and line.startswith("deleted file mode"):
            cur.status = "D"
        elif hunk is None and line.startswith("rename from "):
            cur.status, cur.old_path = "R", line[len("rename from ") :]
        elif hunk is None and line.startswith("rename to "):
            cur.path = line[len("rename to ") :]
        elif hunk is None and line.startswith("Binary files "):
            cur.binary = True
        elif hunk is None and line.startswith("+++ "):
            if line != "+++ /dev/null":
                cur.path = line[len("+++ b/") :].rstrip("\t")  # git ends paths with spaces in a TAB
        elif (m := HUNK.match(line)) is not None:
            a, b, c, d = m.groups()
            hunk = Hunk(int(a), int(b if b is not None else 1), int(c), int(d if d is not None else 1))
            cur.hunks.append(hunk)
        elif hunk is not None and line[:1] in ("+", "-", " "):
            hunk.lines.append(line)
        elif hunk is not None and line == "":
            hunk.lines.append(" ")  # git can emit a bare empty context line
    return files


def diff_files(
    repo: str | Path, base: str = "HEAD", head: str | None = None, paths: tuple[str, ...] = ()
) -> list[FileDiff]:
    """Files changed from base to head; head=None means the working tree, untracked files included."""
    rng = [base] if head is None else [base, head]
    files = parse_unified(git(repo, "diff", "--no-color", "--no-ext-diff", "-M", "-U3", *rng, "--", *paths))
    if head is None:
        known = {f.path for f in files}
        for p in git(repo, "ls-files", "--others", "--exclude-standard", "--", *paths).splitlines():
            if p not in known:
                files.append(_untracked(Path(repo), p))
    return files


def _untracked(repo: Path, path: str) -> FileDiff:
    f = FileDiff(path=path, status="A")
    full = repo / path
    if not full.is_file() or full.stat().st_size > MAX_NEW_FILE_BYTES:
        return f  # listed, not inlined
    data = full.read_bytes()
    if b"\0" in data[:8000]:
        f.binary = True
        return f
    lines = data.decode("utf-8", "replace").splitlines()
    f.hunks.append(Hunk(0, 0, 1, len(lines), ["+" + x for x in lines]))
    return f


def read_at(repo: str | Path, ref: str | None, path: str) -> str | None:
    """File text at ref (None = working tree), or None if it doesn't exist there."""
    if ref is None:
        full = Path(repo) / path
        return full.read_text("utf-8", "replace") if full.is_file() else None
    r = run(repo, "show", f"{ref}:{path}", ok=(0, 128), text=False)
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None
