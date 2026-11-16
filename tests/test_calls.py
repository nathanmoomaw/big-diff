from bigdiff.calls import Call, broken, calls, fits, params


def test_python_params_and_fits():
    p = params("a.py", "def f(a, b=1, *, c, d=2)", "function")
    assert fits(p, Call(1, 1, frozenset({"c"}))) and fits(p, Call(1, 0, frozenset({"a", "c"})))
    assert not fits(p, Call(1, 1, frozenset()))  # c missing
    assert not fits(p, Call(1, 3, frozenset({"c"})))  # too many positional
    assert not fits(p, Call(1, 1, frozenset({"a", "c"})))  # a twice
    assert not fits(p, Call(1, 1, frozenset({"c", "zz"})))  # unknown keyword
    m = params("a.py", "def m(self, x)", "method")
    assert fits(m, Call(1, 1)) and not fits(m, Call(1, 2))
    assert fits(params("a.py", "def g(*a, **k)", "function"), Call(1, 9, frozenset({"q"})))


def test_ts_params_and_fits():
    p = params("a.ts", "function f(a: number, b?: string, c = 1)", "function")
    assert fits(p, Call(1, 1)) and fits(p, Call(1, 3)) and not fits(p, Call(1, 0)) and not fits(p, Call(1, 4))
    assert fits(params("a.js", "f = (a, ...rest) =>", "function"), Call(1, 5))
    m = params("a.ts", "save(this: Store, k: string)", "method")
    assert fits(m, Call(1, 1)) and not fits(m, Call(1, 2))
    assert fits(params("a.js", "g = x =>", "function"), Call(1, 1))


def test_calls_skip_spreads_and_other_names():
    py = "f(1, 2)\nobj.f(x=1)\nf(*xs)\ng(1)\n"
    assert {k: (c.args, set(c.keywords)) for k, c in calls("a.py", py, "f", {1, 2, 3, 4}).items()} == {
        1: (2, set()),
        2: (0, {"x"}),
    }
    js = "f(1)\nthis.store.f(1, 2)\nf(...xs)\nf`tpl`\n"
    assert {k: c.args for k, c in calls("a.js", js, "f", {1, 2, 3, 4}).items()} == {1: 1, 2: 2}


def test_broken_only_flags_calls_that_used_to_fit():
    src = {"app.js": "load(u)\nload(u, opts)\nload()\n"}
    bad = broken("lib.js", "function", "load", "function load(url, opts = {})", "function load(url, opts)",
                 {"app.js": {1, 2, 3}}, src.get)
    assert bad == [("app.js", 1)]  # load() never fit; load(u, opts) still fits
