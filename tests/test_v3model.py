"""v3 目标数据模型单测：TextSchedule 时段/加权、序列化往返、坐标与槽位解析。"""

from __future__ import annotations

from datetime import datetime

import pytest

from world.v3model import (
    DIR_OFFSETS,
    DIRECTIONS,
    OPPOSITE_DIR,
    ConnectionPath,
    ConnectionSlot,
    TextSchedule,
    default_connections,
    location_to_dict,
    map_to_dict,
    parse_location,
    parse_map,
    parse_path,
    parse_slot,
    parse_target,
    parse_text_schedule,
    path_to_dict,
)

NOW = datetime(2026, 8, 13, 12, 0)


# ---------- TextSchedule ----------


def test_default_schedule_resolves_to_empty():
    assert TextSchedule().resolve(NOW) == ""


def test_plain_string_schedule():
    s = parse_text_schedule("迷雾笼罩，看不真切")
    assert s.resolve(datetime(2026, 8, 13, 3, 0)) == "迷雾笼罩，看不真切"
    assert s.resolve(NOW) == "迷雾笼罩，看不真切"


def test_none_schedule_is_empty():
    assert parse_text_schedule(None).resolve(NOW) == ""


def test_period_selection():
    s = parse_text_schedule(
        {
            "periods": [
                {
                    "start": "06:00",
                    "end": "18:00",
                    "items": [{"text": "白天", "weight": 1}],
                },
                {
                    "start": "18:00",
                    "end": "06:00",
                    "items": [{"text": "夜晚", "weight": 1}],
                },
            ]
        }
    )
    assert s.resolve(datetime(2026, 8, 13, 9, 0)) == "白天"
    assert s.resolve(datetime(2026, 8, 13, 20, 0)) == "夜晚"


def test_cross_midnight_period():
    s = parse_text_schedule(
        {
            "periods": [
                {
                    "start": "22:00",
                    "end": "02:00",
                    "items": [{"text": "深夜", "weight": 1}],
                },
                {
                    "start": "02:00",
                    "end": "22:00",
                    "items": [{"text": "白天", "weight": 1}],
                },
            ]
        }
    )
    assert s.resolve(datetime(2026, 8, 13, 1, 0)) == "深夜"
    assert s.resolve(datetime(2026, 8, 13, 12, 0)) == "白天"
    assert s.resolve(datetime(2026, 8, 13, 23, 0)) == "深夜"
    assert s.resolve(datetime(2026, 8, 13, 2, 0)) == "白天"


def test_end_0000_means_midnight():
    s = parse_text_schedule(
        {
            "periods": [
                {
                    "start": "08:00",
                    "end": "00:00",
                    "items": [{"text": "白天", "weight": 1}],
                }
            ]
        }
    )
    assert s.resolve(datetime(2026, 8, 13, 23, 0)) == "白天"
    assert s.resolve(datetime(2026, 8, 13, 7, 0)) == ""


def test_no_period_matches_returns_empty():
    s = parse_text_schedule(
        {
            "periods": [
                {
                    "start": "08:00",
                    "end": "09:00",
                    "items": [{"text": "X", "weight": 1}],
                }
            ]
        }
    )
    assert s.resolve(datetime(2026, 8, 13, 12, 0)) == ""


def test_weighted_pick_with_injected_rand():
    s = parse_text_schedule(
        {
            "periods": [
                {
                    "start": "00:00",
                    "end": "24:00",
                    "items": [
                        {"text": "A", "weight": 1.0},
                        {"text": "B", "weight": 3.0},
                    ],
                }
            ]
        }
    )
    # total=4：rand 0.1 → r=0.4 ≤ 1 → A；rand 0.5 → r=2.0 ≤ 4 → B；rand 0.99 → B
    assert s.resolve(NOW, rand=lambda: 0.1) == "A"
    assert s.resolve(NOW, rand=lambda: 0.5) == "B"
    assert s.resolve(NOW, rand=lambda: 0.99) == "B"


def test_empty_item_text_filtered():
    s = parse_text_schedule(
        {
            "periods": [
                {
                    "start": "00:00",
                    "end": "24:00",
                    "items": [{"text": "", "weight": 1}, {"text": "B", "weight": 1}],
                }
            ]
        }
    )
    assert s.resolve(NOW) == "B"


def test_invalid_weights_dropped():
    s = parse_text_schedule(
        {
            "periods": [
                {
                    "start": "00:00",
                    "end": "24:00",
                    "items": [
                        {"text": "A", "weight": 0},
                        {"text": "B", "weight": -1},
                        {"text": "C", "weight": "x"},
                        {"text": "D", "weight": True},
                    ],
                }
            ]
        }
    )
    assert s.resolve(NOW) == ""


def test_invalid_period_dropped():
    s = parse_text_schedule(
        {
            "periods": [
                {
                    "start": "25:00",
                    "end": "24:00",
                    "items": [{"text": "X", "weight": 1}],
                },
                {
                    "start": "00:00",
                    "end": "24:00",
                    "items": [{"text": "好", "weight": 1}],
                },
            ]
        }
    )
    assert s.resolve(NOW) == "好"


def test_text_schedule_round_trip():
    s = parse_text_schedule(
        {
            "periods": [
                {
                    "start": "06:00",
                    "end": "18:00",
                    "items": [
                        {"text": "白天", "weight": 2.0},
                        {"text": "阴天", "weight": 1.0},
                    ],
                }
            ]
        }
    )
    s2 = parse_text_schedule(s.to_dict())
    assert s == s2


def test_overlap_first_match_wins():
    s = parse_text_schedule(
        {
            "periods": [
                {
                    "start": "00:00",
                    "end": "24:00",
                    "items": [{"text": "全天", "weight": 1}],
                },
                {
                    "start": "08:00",
                    "end": "09:00",
                    "items": [{"text": "早高峰", "weight": 1}],
                },
            ]
        }
    )
    assert s.resolve(datetime(2026, 8, 13, 8, 30)) == "全天"  # 列表先命中者优先
    assert s.resolve(datetime(2026, 8, 13, 3, 0)) == "全天"


# ---------- 方向与坐标 ----------


def test_direction_offsets():
    assert DIR_OFFSETS == {
        "up": (-1, 0),
        "down": (1, 0),
        "left": (0, -1),
        "right": (0, 1),
    }
    assert OPPOSITE_DIR == {
        "up": "down",
        "down": "up",
        "left": "right",
        "right": "left",
    }


def test_location_offset():
    loc = parse_location({"row": 3, "col": 5, "name": "铁匠铺"})
    t = loc.offset("right")
    assert (t.row, t.col, t.map_id) == (3, 6, "")
    t = loc.offset("up")
    assert (t.row, t.col) == (2, 5)


# ---------- 地块 / 槽位 / 目标 ----------


def test_default_connections_all_disabled():
    conns = default_connections()
    assert list(conns) == list(DIRECTIONS)
    assert all(not s.enabled and s.paths == [] for s in conns.values())


def test_parse_location_full():
    loc = parse_location(
        {
            "map_id": "",
            "row": 3,
            "col": 5,
            "name": "铁匠铺",
            "description": "炉火终日不熄。",
            "connections": {
                "right": {
                    "enabled": True,
                    "paths": [
                        {
                            "label": {
                                "periods": [
                                    {
                                        "start": "00:00",
                                        "end": "24:00",
                                        "items": [{"text": "通往集市", "weight": 1}],
                                    }
                                ]
                            },
                            "reveal_target": False,
                            "targets": [{"row": 3, "col": 6, "weight": 1}],
                        }
                    ],
                }
            },
        }
    )
    assert (loc.map_id, loc.row, loc.col) == ("", 3, 5)
    assert loc.name == "铁匠铺"
    assert loc.description.resolve(NOW) == "炉火终日不熄。"
    slot = loc.connections["right"]
    assert slot.enabled and slot.direction == "right"
    assert len(slot.paths) == 1
    path = slot.paths[0]
    assert path.reveal_target is False
    assert path.label.resolve(NOW) == "通往集市"
    assert path.targets[0].row == 3 and path.targets[0].col == 6


def test_parse_target():
    t = parse_target({"row": 4, "col": 9, "weight": 0.1, "map_id": "other"})
    assert t is not None and (t.map_id, t.row, t.col, t.weight) == ("other", 4, 9, 0.1)
    assert parse_target({"row": 1, "col": "x"}) is None
    assert parse_target("nope") is None


def test_parse_slot_invalid_direction_raises():
    with pytest.raises(ValueError):
        parse_slot("north", {})


def test_location_rejects_bad_input():
    with pytest.raises(ValueError):
        parse_location({"row": "a", "col": 5, "name": "X"})
    with pytest.raises(ValueError):
        parse_location({"row": 1, "col": True, "name": "X"})
    with pytest.raises(ValueError):
        parse_location({"row": 1, "col": 2, "name": "   "})
    with pytest.raises(ValueError):
        parse_location(None)


def test_location_round_trip():
    loc = parse_location(
        {
            "row": 3,
            "col": 5,
            "name": "铁匠铺",
            "description": "炉火终日不熄。",
            "connections": {
                "right": {
                    "enabled": True,
                    "paths": [
                        {
                            "reveal_target": True,
                            "targets": [{"row": 3, "col": 6, "weight": 1}],
                        }
                    ],
                },
                "up": {
                    "enabled": True,
                    "paths": [
                        {
                            "label": {
                                "periods": [
                                    {
                                        "start": "00:00",
                                        "end": "24:00",
                                        "items": [{"text": "山路", "weight": 1}],
                                    }
                                ]
                            },
                            "reveal_target": False,
                            "targets": [{"row": 2, "col": 5, "weight": 1}],
                        }
                    ],
                },
            },
        }
    )
    loc2 = parse_location(location_to_dict(loc))
    assert loc == loc2


def test_location_to_dict_omits_empty_map_id():
    loc = parse_location({"row": 3, "col": 5, "name": "X"})
    d = location_to_dict(loc)
    assert d["map_id"] == ""
    right = d["connections"]["right"]
    assert right["enabled"] is False and right["paths"] == []


def test_path_round_trip():
    p = parse_path(
        {
            "label": {
                "periods": [
                    {
                        "start": "00:00",
                        "end": "24:00",
                        "items": [{"text": "小路", "weight": 1}],
                    }
                ]
            },
            "reveal_target": False,
            "targets": [{"row": 1, "col": 2, "weight": 1}],
        }
    )
    p2 = parse_path(path_to_dict(p))
    assert (p.reveal_target, p.targets[0].row, p.label.resolve(NOW)) == (
        False,
        1,
        "小路",
    )
    assert p2 == p


# ---------- 地图 ----------


def test_map_parse_and_round_trip():
    m = parse_map(
        {
            "id": "default",
            "name": "主世界",
            "description": "示例小镇。",
            "timezone": "Asia/Shanghai",
            "spawn_row": 1,
            "spawn_col": 2,
        }
    )
    assert (m.id, m.name, m.timezone, m.spawn_row, m.spawn_col) == (
        "default",
        "主世界",
        "Asia/Shanghai",
        1,
        2,
    )
    m2 = parse_map(map_to_dict(m))
    assert m == m2


def test_map_defaults():
    m = parse_map({"id": "default", "name": "主世界"})
    assert m.timezone is None and (m.spawn_row, m.spawn_col) == (0, 0)


def test_map_rejects_bad_input():
    with pytest.raises(ValueError):
        parse_map({"id": "", "name": "主世界"})
    with pytest.raises(ValueError):
        parse_map({"id": "default", "name": "  "})
    with pytest.raises(ValueError):
        parse_map({"id": "default", "name": "主世界", "spawn_row": "x", "spawn_col": 0})


def test_construct_slot_defaults():
    slot = ConnectionSlot(
        direction="down", enabled=True, paths=[ConnectionPath(targets=[])]
    )
    assert slot.direction == "down" and slot.enabled
    assert slot.paths[0].reveal_target is True and slot.paths[0].label is None
