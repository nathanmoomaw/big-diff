from bigdiff import events, packet


def test_events_in_listeners_props_attrs_and_custom():
    src = "el.addEventListener('pointerup', f)\n<div onPointerDown={g} onClickCapture={h} />\n<a onclick=\"x()\">\nbus.emit('saved')"
    assert events.events_in(src) == {"pointerup", "pointerdown", "click", "saved"}
    # callback props and handler variables aren't events
    assert events.events_in("<Knob onShake={f} onSuccess={g} />\nconst onPointerUp = useCallback(f)") == set()


def test_widen_links_pointer_to_click():
    assert "click" in events.widen({"pointerup"}) and "keydown" not in events.widen({"pointerup"})


def test_knob_release_links_to_window_click_listener(repo):
    repo.write("src/useShake.js", "export function useShake() {\n  window.addEventListener('click', onShake)\n}\n")
    repo.write("src/Help.jsx", "export function Help() {\n  el.dispatchEvent(new KeyboardEvent('keydown'))\n}\n")
    repo.commit()
    repo.write(
        "src/Knob.jsx",
        "export const Knob = memo(function Knob() {\n"
        "  const onPointerUp = useCallback((e) => {}, [])\n"
        "  return <div onPointerUp={onPointerUp} />\n"
        "})\n",
    )
    p = packet.build(repo.root)
    knob = next(i for i in p.items if i.title.startswith("function `Knob` "))
    first = knob.events.edges[0]
    assert "event-coupled" in knob.tags and first.path == "src/useShake.js" and first.is_global
    assert first.owner == "useShake" and "onShake" in first.snippet
    assert all(e.path != "src/Help.jsx" for e in knob.events.edges)  # keydown isn't in the pointer family
    assert "compatibility events (click)" in knob.events.note
    inner = next(i for i in p.items if "Knob.onPointerUp" in i.title)
    assert inner.events is None  # the JSX prop is the Knob's line, not the handler's


def test_page_level_is_decided_per_receiver():
    assert events.events_in("$(document).on('click', f)") == {"click"}
    assert events.events_in("<div onDoubleClick={f} />") == {"dblclick"}
    for line in (
        "document.body.addEventListener('click', f)",
        "$(window).on('click', f)",
        "addEventListener('click', f)",
        "ref.current?.ownerDocument.addEventListener('click', f)",
    ):
        assert events.parse(line) == {"click": True}, line
    assert events.parse("el.addEventListener('click', f); bus.on('click', g)") == {"click": False}
    assert events.parse("el.addEventListener('click', f); window.addEventListener('click', g)") == {"click": True}


def test_explain_only_claims_compatibility_events_for_pointer_family():
    e = events.Edge("x.js", 1, ("keydown",), True, "window.addEventListener('keydown', f)")
    note = events.explain({"pointerup", "keydown"}, [e])
    assert note and "compatibility" not in note


def test_other_files_jsx_props_are_not_edges(repo):
    repo.write("src/Fader.jsx", "export function Fader() {\n  return <div onPointerDown={f} />\n}\n")
    repo.commit()
    repo.write("src/Knob.jsx", "export function Knob() {\n  return <div onPointerUp={g} />\n}\n")
    assert packet.build(repo.root).items[0].events is None
