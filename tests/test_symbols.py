from bigdiff.symbols import compare, symbols

PY = """import os

class Box:
    def size(self):
        return 1

    @property
    def area(self):
        return 2

def helper(x):
    def inner():
        return x
    return inner
"""

JS = """export function load(url) {
  return fetch(url)
}
const parse = (s) => JSON.parse(s)
class Store {
  get(k) { return this.m[k] }
  save(k, v) { this.m[k] = v }
}
"""


def test_python_symbols_with_nesting_and_decorators():
    s = {x.qualname: x for x in symbols("a.py", PY)}
    assert set(s) == {"Box", "Box.size", "Box.area", "helper", "helper.inner"}
    assert s["Box.size"].kind == "method" and s["helper"].kind == "function" and s["Box"].kind == "class"
    assert s["Box.area"].start == 7  # decorator line included
    assert s["helper"].signature == "def helper(x)"


def test_js_symbols_functions_arrows_methods():
    s = {x.qualname: x for x in symbols("a.js", JS)}
    assert {"load", "parse", "Store", "Store.get", "Store.save"} <= set(s)
    assert s["parse"].start == 4 and s["Store.save"].kind == "method"


def test_ts_and_tsx_parse():
    assert [x.qualname for x in symbols("a.ts", "function f(a: number): number { return a }\n")] == ["f"]
    assert [x.qualname for x in symbols("a.tsx", "export const App = () => <div>hi</div>\n")] == ["App"]


def test_unknown_language_and_broken_python_give_no_symbols():
    assert symbols("a.md", "# hi") == []
    assert symbols("a.py", "def broken(:\n") == []


def test_method_edit_reports_method_not_class():
    new = PY.replace("return 1", "return 3")
    changes = compare("a.py", PY, new)
    assert [(c.sym.qualname, c.status) for c in changes] == [("Box.size", "modified")]


def test_inner_function_edit_reports_inner_only():
    new = PY.replace("        return x", "        return x * 2")
    assert [c.sym.qualname for c in compare("a.py", PY, new)] == ["helper.inner"]


def test_added_removed_and_signature_change():
    new = (
        PY.replace("def helper(x):", "def helper(x, y=0):").replace(
            "    @property\n    def area(self):\n        return 2\n", ""
        )
        + "\ndef extra():\n    pass\n"
    )
    got = {(c.sym.qualname, c.status) for c in compare("a.py", PY, new)}
    assert got == {("helper", "modified"), ("Box.area", "removed"), ("extra", "added")}
    (h,) = [c for c in compare("a.py", PY, new) if c.sym.qualname == "helper"]
    assert h.signature_changed


def test_whole_file_added_or_deleted():
    assert {c.status for c in compare("a.py", None, PY)} == {"added"}
    assert {c.status for c in compare("a.py", PY, None)} == {"removed"}


def test_defs_under_if_and_try_are_found():
    src = "try:\n    import ujson as json\nexcept ImportError:\n    def loads(s):\n        return s\nif True:\n    class K:\n        def m(self):\n            pass\n"
    assert {s.qualname for s in symbols("a.py", src)} == {"loads", "K", "K.m"}


def test_signature_skips_decorators_and_spans_wrapped_params():
    old = "@deco\ndef frob(a):\n    return a\n\ndef wide(\n    a,\n    b,\n):\n    return a\n"
    new = old.replace("def frob(a):", "def frob(a, b):").replace("    b,\n):", "    b,\n    c,\n):")
    by = {c.sym.qualname: c for c in compare("a.py", old, new)}
    assert by["frob"].signature_changed and by["wide"].signature_changed
    assert by["frob"].new.signature == "def frob(a, b)"
    deco_only = compare("a.py", old, old.replace("@deco", "@other"))
    assert [c.signature_changed for c in deco_only] == [False]


def test_js_arrow_signature():
    (c,) = compare("a.js", "const f = (a) => a\n", "const f = (a, b) => a\n")
    assert c.signature_changed and c.new.signature == "f = (a, b) =>"


def test_wrapped_react_components_and_handlers():
    src = (
        "export const Knob = memo(function Knob({ v }) {\n"
        "  const onUp = useCallback((e) => { e.stopPropagation() }, [])\n"
        "  return <div onPointerUp={onUp} />\n"
        "})\n"
        "const Box = forwardRef((props, ref) => <div ref={ref} />)\n"
    )
    assert {s.qualname for s in symbols("a.jsx", src)} == {"Knob", "Knob.onUp", "Box"}


def test_only_known_wrappers_unwrap():
    src = "const doubled = items.map((x) => x * 2)\nconst [a] = useState(() => 0)\nconst v = useMemo(() => 1, [])\nconst X = memo(Inner, (p, n) => p === n)\n"
    assert symbols("a.js", src) == []


def test_layout_only_change_is_reformatted():
    old = "def f(a, b):\n    # sum\n    return g(a,b)\n"
    new = "def f(a, b):\n    return g(\n        a, b,\n    )\n"
    assert [c.status for c in compare("a.py", old, new)] == ["reformatted"]
    assert [c.status for c in compare("a.py", old, old.replace("a,b", "b,a"))] == ["modified"]
    js_old = "function load(url) {\n  return fetch(url, {a: 1,});  // hi\n}\n"
    js_new = "function load(url) {\n  return fetch(\n    url,\n    { a: 1 }\n  )\n}\n"
    (c,) = compare("a.js", js_old, js_new)
    assert c.status == "reformatted" and not c.signature_changed and c.cosmetic
    assert compare("a.js", js_old, js_old.replace("url, {", "url2, {"))[0].status == "modified"
    assert compare("a.js", js_old, "export " + js_old)[0].status == "modified"  # new API, same body
    asi = compare("a.js", "function f(x) {\n  return x\n}\n", "function f(x) {\n  return\n  x\n}\n")
    assert [c.status for c in asi] == ["modified"]  # ASI: returns undefined now


def test_rename_with_same_code_pairs_up():
    (c,) = compare("a.py", "def old(x):\n    return x\n", "def new(x):\n    return x\n")
    assert (c.status, c.old.name, c.new.name, c.lookup_name) == ("renamed", "old", "new", "old")
    (c,) = compare("a.ts", "class K { a() { return 1 } }\n", "class K { b() { return 1 } }\n")
    assert c.status == "renamed" and c.new.qualname == "K.b"
    # body edited too, or two candidates: no guess
    edited = compare("a.py", "def old(x):\n    return x\n", "def new(x):\n    return x + 1\n")
    assert {c.status for c in edited} == {"added", "removed"}
    two = compare("a.py", "def old(x):\n    return x\n", "def a(x):\n    return x\n\ndef b(x):\n    return x\n")
    assert sorted(c.status for c in two) == ["added", "added", "removed"]


def test_decorated_and_exported_flags():
    src = (
        "@Comp() export class A { @Input() m(x) { return 1 } n() {} }\n"
        "export default function f() {}\nexport const g = () => 1\n"
        "function h() {}\nconst k = () => 2\nexport { h as hh }\nfunction d() {}\nexport default d\n"
    )
    s = {x.qualname: (x.decorated, x.exported) for x in symbols("a.ts", src)}
    assert s == {
        "A": (True, True), "A.m": (True, False), "A.n": (False, False),
        "f": (False, True), "g": (False, True), "h": (False, True), "k": (False, False), "d": (False, True),
    }  # fmt: skip
    py = {x.qualname: x.decorated for x in symbols("a.py", PY)}
    assert py["Box.area"] and not py["Box.size"]
