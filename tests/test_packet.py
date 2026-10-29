import json

import pytest

from bigdiff import blast, packet
from bigdiff.gitdiff import parse_unified
from bigdiff.symbols import compare

LIB = "def total(items):\n    return sum(items)\n\n\ndef unused():\n    return 0\n"
APP = "from lib import total\n\nprint(total([1, 2]))\n"


def _setup(repo):
    repo.write("lib.py", LIB)
    repo.write("app.py", APP)
    repo.commit()


def test_signature_change_ranks_first_and_lists_callers(repo):
    _setup(repo)
    repo.write(
        "lib.py",
        LIB.replace("def total(items):", "def total(items, start=0):").replace("sum(items)", "sum(items, start)"),
    )
    p = packet.build(repo.root)
    first = p.items[0]
    assert "total" in first.title and "signature changed" in first.tags and "used in other files" in first.tags
    assert first.uses and any(s.path == "app.py" for s in first.uses.sites)
    assert p.files == [{"path": "lib.py", "status": "M", "added": 2, "removed": 2, "kind": "source"}]


def test_removed_symbol_still_referenced_is_priority_zero(repo):
    _setup(repo)
    repo.write("lib.py", "def unused():\n    return 0\n")
    p = packet.build(repo.root)
    assert p.items[0].priority == 0 and "still referenced" in p.items[0].tags


def test_untracked_file_and_non_code_hunks(repo):
    _setup(repo)
    repo.write("notes.md", "hello\n")
    repo.write("new.py", "def fresh():\n    return 1\n")
    p = packet.build(repo.root)
    titles = [i.title for i in p.items]
    assert any("fresh" in t and "added" in t for t in titles) and "notes.md" in titles


def test_ref_to_ref_and_budget(repo):
    _setup(repo)
    repo.write("lib.py", LIB + "".join(f"\n\ndef f{i}():\n    return {i}\n" for i in range(40)))
    repo.commit()
    p = packet.build(repo.root, "HEAD~1", "HEAD", budget=2000)
    assert p.head == "HEAD" and p.omitted and len(p.markdown()) < 6000
    json.loads(p.json())


def test_import_change_outside_symbols_is_kept(repo):
    _setup(repo)
    repo.write("app.py", "import os\n" + APP)
    p = packet.build(repo.root)
    assert any("app.py" in i.title for i in p.items)


def test_parse_unified_rename_and_hunk_lines():
    text = (
        "diff --git a/old.py b/new.py\nsimilarity index 90%\nrename from old.py\nrename to new.py\n"
        "--- a/old.py\n+++ b/new.py\n@@ -2,2 +2,3 @@ ctx\n a\n-b\n+c\n+d\n"
    )
    (f,) = parse_unified(text)
    assert (f.path, f.old_path, f.status) == ("new.py", "old.py", "R")
    assert f.new_changed() == {3, 4} and f.old_changed() == {3}


def test_repo_without_commits(repo):
    repo.write("a.py", "def first():\n    return 1\n")
    p = packet.build(repo.root)
    assert [i.title for i in p.items] == ["function `first` added: nothing uses it"]


def test_removed_symbol_keeps_callers_at_same_lines(repo):
    repo.write("lib.py", "def gone():\n    return 1\n")
    repo.commit()
    repo.write("lib.py", "x = gone()\n")  # the caller now sits where the definition was
    first = packet.build(repo.root).items[0]
    assert "still referenced" in first.tags


def test_path_with_space_keeps_symbols(repo):
    repo.write("a b.py", "def frob(a):\n    return a\n")
    repo.commit()
    repo.write("a b.py", "def frob(a, b):\n    return a\n")
    p = packet.build(repo.root)
    assert p.files[0]["path"] == "a b.py" and "signature changed" in p.items[0].tags


def test_oversized_first_item_is_truncated(repo):
    repo.write("big.sql", "".join(f"select {i};\n" for i in range(20000)))
    p = packet.build(repo.root, budget=5000)
    assert len(p.markdown()) < 6000 and "truncated" in p.items[0].body


def test_uses_found_in_html_and_grep_errors_raise(repo):
    repo.write("lib.js", "export function launchIt() {}\n")
    repo.write("index.html", '<button onclick="launchIt()">go</button>\n')
    repo.commit()
    repo.write("lib.js", "")
    first = packet.build(repo.root).items[0]
    assert "still referenced" in first.tags and first.uses.sites[0].path == "index.html"
    with pytest.raises(RuntimeError):
        blast.uses_many(repo.root, compare("lib.js", "function launchIt() {}\n", None), ref="no-such-ref")


def test_new_module_only_tests_import_is_an_orphan(repo):
    repo.write("app.py", "print('hi')\n")
    repo.commit()
    repo.write("pkg/helpers.py", "def slugify(s):\n    return s.lower()\n")
    repo.write("tests/test_helpers.py", "from pkg.helpers import slugify\n")
    repo.write("pkg/lonely.py", "def alone():\n    return 1\n")
    repo.write("pkg/used.py", "def shared():\n    return 2\n")
    repo.write("app.py", "from pkg.used import shared\nprint(shared())\n")
    repo.write("tool.py", "#!/usr/bin/env python3\nprint('script')\n")
    repo.write("cli2.py", "def go():\n    pass\n\n\nif __name__ == \"__main__\":\n    go()\n")
    repo.write("pkg/quoted.py", "# run me: python -m pkg.quoted  (no __main__ guard here)\nX = 1\n")
    repo.write("web/widget/index.js", "export const w = 1\n")
    repo.write("lambda/preset-store/index.mjs", "export const handler = async () => ({})\n")
    repo.write("svc/worker_fn.py", "def lambda_handler(event, context):\n    return {}\n")
    repo.write("web/main.js", "import { w } from './widget/index.js'\n")
    p = packet.build(repo.root)
    orphans = {i.path: i.title for i in p.items if "orphan" in i.tags}
    assert orphans == {
        "pkg/helpers.py": "new module nothing imports: only tests mention it (tests/test_helpers.py)",
        "pkg/lonely.py": "new module nothing imports: nothing else in the repo mentions it",
        "pkg/quoted.py": "new module nothing imports: nothing else in the repo mentions it",
    }
    assert "```" not in next(i for i in p.items if i.path == "pkg/lonely.py" and "orphan" in i.tags).render()
    assert all(i.priority == packet.Prio.MODIFIED for i in p.items if "orphan" in i.tags)


def test_new_function_only_tests_call_is_an_orphan(repo):
    repo.write("lib.py", "def old():\n    return 0\n")
    repo.write("app.py", "from lib import old\nprint(old())\n")
    repo.commit()
    repo.write("lib.py", (
        "import functools\n\n\ndef old():\n    return 0\n\n\ndef helper_for_tests():\n    return 1\n\n\n"
        "def forgotten():\n    return 2\n\n\n@functools.cache\ndef cached_thing():\n    return 3\n\n\n"
        "class Box:\n    def fresh_method(self):\n        return 4\n"
    ))
    repo.write("app.py", "from lib import old, Box\nprint(old(), Box())\n")
    repo.write("tests/test_lib.py", "from lib import helper_for_tests, cached_thing\n")
    p = packet.build(repo.root)
    titles = {i.title for i in p.items if "orphan" in i.tags}
    assert titles == {
        "function `helper_for_tests` added: only tests use it (tests/test_lib.py)",
        "function `forgotten` added: nothing uses it",
    }
    assert all(i.priority == packet.Prio.MODIFIED for i in p.items if "orphan" in i.tags)
