"""玩法包元数据（play.yaml）解析与版本校验（DESIGN_V4.md「玩法包规范」）。

v4.0 只校验 worlditor 版本；``plays`` 依赖声明保留（解析与加载顺序 v4.2 实现）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

PLAY_PREFIX = "worlditor_play_"


@dataclass
class PlaySpec:
    """玩法包元数据（play.yaml）。name 即 play id（worlditor_play_* 前缀）。"""

    name: str
    display_name: str
    version: str
    author: str = ""
    desc: str = ""
    requires_worlditor: str = "*"
    requires_plays: list[str] = field(default_factory=list)  # 声明保留，v4.2 解析
    icon: str = ""

    @property
    def play_id(self) -> str:
        return self.name


def load_play_spec(path: Path) -> PlaySpec | None:
    """读取并校验 play.yaml；缺失 / 非法返回 None（调用方记日志跳过）。"""
    yaml_path = path / "play.yaml"
    if not yaml_path.is_file():
        return None
    try:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not name.startswith(PLAY_PREFIX):
        return None
    display_name = raw.get("display_name")
    if not isinstance(display_name, str) or not display_name.strip():
        display_name = name
    version = raw.get("version")
    if not isinstance(version, str) or not version.strip():
        return None
    requires = raw.get("requires") or {}
    if not isinstance(requires, dict):
        requires = {}
    plays = requires.get("plays")
    if not isinstance(plays, list):
        plays = []
    return PlaySpec(
        name=name,
        display_name=display_name,
        version=version,
        author=str(raw.get("author") or ""),
        desc=str(raw.get("desc") or ""),
        requires_worlditor=str(requires.get("worlditor") or "*"),
        requires_plays=[str(p) for p in plays if isinstance(p, str)],
        icon=str(raw.get("icon") or ""),
    )


def version_ok(installed: str, requirement: str) -> bool:
    """校验 installed 是否满足 requirement（支持 * / >= / > / == / < / <=，数字段比较）。"""
    requirement = requirement.strip()
    if not requirement or requirement == "*":
        return True
    op = "=="
    rest = requirement
    for candidate in (">=", "<=", "==", ">", "<"):
        if requirement.startswith(candidate):
            op = candidate
            rest = requirement[len(candidate) :].strip()
            break
    try:
        a = _parse_version(installed)
        b = _parse_version(rest)
    except (ValueError, AttributeError):
        return False
    if op == ">=":
        return a >= b
    if op == "<=":
        return a <= b
    if op == ">":
        return a > b
    if op == "<":
        return a < b
    return a == b


def _parse_version(version: str) -> tuple[int, ...]:
    """'0.3.0' / '0.3' / '0.3.0-beta' → (0, 3, 0)（数字前缀段，不足补 0）。"""
    parts: list[int] = []
    for token in version.strip().lstrip("vV").split("."):
        digits = ""
        for ch in token:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)
