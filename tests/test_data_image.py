import json

import pytest
from hypothesis import given
from hypothesis import strategies as st
from PIL import Image

from bigdiff import data, image

json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=True) | st.text(max_size=5),
    lambda c: st.lists(c, max_size=4) | st.dictionaries(st.text(max_size=4), c, max_size=4),
    max_leaves=20,
)


@given(json_values)
def test_value_equals_itself(x):
    assert data.diff(x, x) == []


@given(json_values, json_values)
def test_no_ops_means_equal_json(a, b):
    if not data.diff(a, b):
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True) or a == b


def test_keyed_lists_ignore_order_and_report_paths():
    a = {"items": [{"id": 1, "p": 2.0}, {"id": 2, "p": 3.0}]}
    b = {"items": [{"id": 2, "p": 3.0}, {"id": 1, "p": 2.5}, {"id": 3, "p": 1.0}]}
    got = [o.render() for o in data.diff(a, b, key="id")]
    assert got == ["~ $.items[id=1].p: 2.0 → 2.5", '+ $.items[id=3] = {"id": 3, "p": 1.0}']


def test_tolerance_and_type_change():
    assert data.diff({"x": 1.0}, {"x": 1.0005}, tol=0.001) == []
    assert data.diff({"x": 1}, {"x": "1"})[0].op == "type"
    assert data.diff(True, 1)[0].op == "type"  # bool is not a number here


def test_csv_rows_by_key_and_columns():
    a = "id,name,qty\n1,a,2\n2,b,3\n"
    b = "id,name,qty,note\n2,b,4,x\n1,a,2,\n"
    ops = data.diff_text(a, b, "csv", key="id")
    paths = [o.path for o in ops]
    assert "$columns.note" in paths and "$[id=2].qty" in paths and "$[id=1].note" in paths


def test_image_diff_counts_box_and_heatmap(tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    Image.new("RGB", (100, 50), "white").save(a)
    im = Image.new("RGB", (100, 50), "white")
    im.paste((0, 0, 0), (10, 10, 20, 20))
    im.putpixel((90, 40), (250, 250, 250))  # under the threshold: not change
    im.save(b)
    d = image.diff(a, b, heatmap=tmp_path / "h.png")
    assert d.changed_px == 100 and d.bbox == (10, 10, 20, 20) and (tmp_path / "h.png").is_file()
    assert image.diff(a, a).changed_px == 0


def test_ragged_csv_and_unhashable_keys():
    ops = data.diff_text("a,b\n1,2\n", "a,b\n1\n", "csv")
    assert [o.path for o in ops] == ["$[0].b"]
    assert data.diff([{"id": [1]}], [{"id": [2]}], key="id")[0].path == "$[0].id[0]"


def test_csv_keeps_text_exact_and_ignores_column_reorder():
    assert [o.path for o in data.diff_text("id,zip\n1,00501\n", "id,zip\n1,501\n", "csv")] == ["$[0].zip"]
    assert data.diff_text("a,b\n1,2\n", "b,a\n2,1\n", "csv") == []
    assert [o.path for o in data.diff_text("a,b\n", "a,c\n", "csv")] == ["$columns.b", "$columns.c"]
    assert data.diff_text("x\n1.0\n", "x\n1.0004\n", "csv", tol=0.001) == []


def test_diff_text_sniffs_json_when_kind_unknown():
    assert [o.op for o in data.diff_text('{"a": 1}', '{"a": 2}', None)] == ["changed"]
    with pytest.raises(data.UnknownKind):
        data.diff_text("a,b", "a,c", None)


def test_image_diff_takes_streams():
    import io

    def png(c):
        b = io.BytesIO()
        Image.new("RGB", (4, 4), c).save(b, "PNG")
        b.seek(0)
        return b

    heat = io.BytesIO()
    d = image.diff(png("white"), png("black"), heatmap=heat)
    assert d.fraction == 1.0 and heat.getvalue().startswith(b"\x89PNG")
