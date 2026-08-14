"""v4 数据模型解析容错测试（v4model.py）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT.parent))

from astrbot_plugin_worlditor.world.v4model import (  # noqa: E402
    Effect,
    Entity,
    InteractionResult,
    ItemDef,
    check_count,
    entity_db_row,
    entity_from_row,
    item_db_row,
    item_from_row,
)


def test_entity_from_dict_ok():
    e = Entity.from_dict(
        {
            "id": "abc",
            "map_id": "default",
            "row": 1,
            "col": 2,
            "kind": "merchant",
            "name": "阿福",
            "desc": "老商贩",
            "attrs": {"gold": 5},
            "state": {"open": True},
            "user_id": "u1",
            "last_active_ts": 123.4,
        }
    )
    assert e is not None
    assert e.pos_key() == ("default", 1, 2)
    assert e.attrs == {"gold": 5}
    assert e.is_identity() is False


def test_entity_from_dict_tolerant():
    """缺字段/坏类型/坏坐标 → None；attrs 非 dict → 空 dict。"""
    assert Entity.from_dict(None) is None
    assert Entity.from_dict({}) is None
    assert Entity.from_dict({"id": "a"}) is None  # 缺 name/map_id/kind
    assert (
        Entity.from_dict({"id": "a", "map_id": "m", "kind": "k", "name": "n"}) is None
    )  # 缺 row/col
    assert (
        Entity.from_dict(
            {"id": "a", "map_id": "m", "kind": "k", "name": "n", "row": "1", "col": 0}
        )
        is None
    )  # row 非 int
    e = Entity.from_dict(
        {
            "id": "a",
            "map_id": "m",
            "kind": "k",
            "name": "n",
            "row": 1,
            "col": 0,
            "attrs": "bad",
            "state": None,
        }
    )
    assert e is not None and e.attrs == {} and e.state == {}


def test_entity_db_roundtrip():
    e = Entity(
        id="x", map_id="m", row=1, col=2, kind="player", name="小明", attrs={"a": 1}
    )
    row = entity_db_row(e)
    assert row[8] == '{"a": 1}'


def test_entity_from_row_fake():
    class FakeRow:
        def __getitem__(self, key):
            return {
                "id": "x",
                "map_id": "m",
                "row": 1,
                "col": 2,
                "kind": "player",
                "name": "小明",
                "desc": "",
                "user_id": None,
                "attrs_json": "not-json",
                "state_json": "",
                "last_active_ts": 0.0,
            }[key]

    e = entity_from_row(FakeRow())
    assert e is not None and e.attrs == {} and e.state == {}


def test_item_def_roundtrip():
    item = ItemDef(
        id="sword", name="木剑", desc="练习用。", stackable=False, use_action="equip"
    )
    row = item_db_row(item)
    assert row[4] == 0
    parsed = item_from_row(
        type(
            "R",
            (),
            {
                "__getitem__": lambda self, k: {
                    "id": "sword",
                    "name": "木剑",
                    "desc": "练习用。",
                    "icon": "",
                    "stackable": 0,
                    "use_action": "equip",
                    "attrs_json": "{}",
                }[k]
            },
        )()
    )
    assert parsed is not None and parsed.stackable is False
    assert ItemDef.from_dict({"id": "a"}) is None  # 缺 name
    assert (
        ItemDef.from_dict({"id": "a", "name": "n", "stackable": "yes"}).stackable
        is True
    )


def test_effect_and_result_parse():
    assert Effect.from_dict({"op": "give_item", "args": {"item_id": "a"}}) is not None
    assert Effect.from_dict({"op": "give_item"}) is not None  # args 缺省
    assert Effect.from_dict({}) is None
    assert Effect.from_dict({"op": ""}) is None
    result = InteractionResult.from_dict(
        {"text": "hi", "effects": [{"op": "give_item"}, {"op": 123}, "junk", None]}
    )
    assert result is not None and result.text == "hi"
    assert len(result.effects) == 1 and result.effects[0].op == "give_item"
    assert InteractionResult.from_dict(None) is None


def test_check_count():
    assert check_count(1) == 1
    assert check_count(100) == 100
    with pytest.raises(ValueError):
        check_count(0)
    with pytest.raises(ValueError):
        check_count(-1)
    with pytest.raises(ValueError):
        check_count(True)
    with pytest.raises(ValueError):
        check_count(1.5)
