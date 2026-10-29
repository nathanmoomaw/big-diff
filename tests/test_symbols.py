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
