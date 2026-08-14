"""玩法包发现加载器（DESIGN_V4.md「发现加载流程」）。

扫描 demo_play/（内核自带）与 `<数据目录>/plays/` 下 worlditor_play_* 目录；
校验 play.yaml 与 requires（v4.0 只校验 worlditor 版本；plays 依赖声明保留，
解析 v4.2）；importlib 加载 main.py 并调用 setup(api, context)。

每个玩法包一个独立 WorlditorPlayAPI（kv namespace 隔离）；单个玩法包
加载失败记日志跳过，不阻断内核与其他玩法包（异常隔离）。
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..v4engine import V4WorldEngine
from .api import WorlditorPlayAPI
from .spec import PlaySpec, load_play_spec, version_ok

logger = logging.getLogger("astrbot")


@dataclass
class PlayInfo:
    """一个已加载的玩法包。"""

    spec: PlaySpec
    api: WorlditorPlayAPI
    module: Any
    path: Path

    @property
    def play_id(self) -> str:
        return self.spec.play_id


class PlayLoader:
    """玩法包发现与加载（跟随内核整体重载，C2；无单包热重载）。"""

    def __init__(
        self,
        engine: V4WorldEngine,
        *,
        plays_dir: Path,
        demo_dir: Path | None = None,
        worlditor_version: str = "0.3.0",
    ) -> None:
        self.engine = engine
        self.plays_dir = Path(plays_dir)
        self.demo_dir = Path(demo_dir) if demo_dir else None
        self.worlditor_version = worlditor_version
        self.plays: dict[str, PlayInfo] = {}

    # ---------- 发现 ----------

    def discover(self) -> list[Path]:
        """候选玩法包目录：demo_play/ + plays/ 下 worlditor_play_*。"""
        dirs: list[Path] = []
        if self.demo_dir is not None and self.demo_dir.is_dir():
            dirs.append(self.demo_dir)
        if self.plays_dir.is_dir():
            for p in sorted(self.plays_dir.iterdir()):
                if p.is_dir() and p.name.startswith("worlditor_play_"):
                    dirs.append(p)
        return dirs

    # ---------- 加载 ----------

    async def load_all(self, context: Any | None = None) -> list[PlayInfo]:
        """加载全部候选玩法包；返回成功加载的列表。"""
        loaded: list[PlayInfo] = []
        for path in self.discover():
            info = await self.load_one(path, context)
            if info is not None:
                loaded.append(info)
        await self.engine.flush_item_defs()
        return loaded

    async def load_one(self, path: Path, context: Any | None) -> PlayInfo | None:
        """加载单个玩法包目录；失败记日志跳过（不抛）。"""
        spec = load_play_spec(path)
        if spec is None:
            logger.warning("[worlditor] 跳过玩法包 %s：play.yaml 缺失或非法", path.name)
            return None
        if not version_ok(self.worlditor_version, spec.requires_worlditor):
            logger.warning(
                "[worlditor] 跳过玩法包 %s：需要 worlditor %s，当前 %s",
                spec.name,
                spec.requires_worlditor,
                self.worlditor_version,
            )
            return None
        main_py = path / "main.py"
        if not main_py.is_file():
            logger.warning("[worlditor] 跳过玩法包 %s：缺少 main.py", spec.name)
            return None
        try:
            module = self._load_module(spec.play_id, main_py)
            api = WorlditorPlayAPI(self.engine, spec.play_id)
            self.engine.attach_play_api(spec.play_id, api)
            setup = getattr(module, "setup", None)
            if not callable(setup):
                raise RuntimeError("main.py 缺少 setup(api, context)")
            setup(api, context)
            info = PlayInfo(spec=spec, api=api, module=module, path=path)
            self.plays[spec.play_id] = info
            logger.info(
                "[worlditor] 玩法包已加载：%s (%s)", spec.display_name, spec.version
            )
            return info
        except Exception:  # noqa: BLE001
            logger.exception("[worlditor] 玩法包加载失败：%s", path.name)
            # 回滚半注册（kind/interaction/event/ui 按 play_id 清理）
            self.engine.clear_play_registrations(spec.play_id)
            self.engine.detach_play_api(spec.play_id)
            return None

    def _load_module(self, play_id: str, main_py: Path) -> Any:
        """importlib 加载玩法包 main.py（模块名 = play id）。"""
        spec = importlib.util.spec_from_file_location(play_id, main_py)
        if spec is None or spec.loader is None:
            raise RuntimeError("无法创建模块加载器")
        module = importlib.util.module_from_spec(spec)
        sys.modules[play_id] = module
        spec.loader.exec_module(module)
        return module

    # ---------- 卸载 ----------

    async def unload_all(self) -> None:
        """卸载全部玩法包（teardown(api) 可选；随内核重载，C2）。"""
        for info in self.plays.values():
            teardown = getattr(info.module, "teardown", None)
            if callable(teardown):
                try:
                    teardown(info.api)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "[worlditor] 玩法包 teardown 异常：%s", info.play_id
                    )
            self.engine.clear_play_registrations(info.play_id)
            self.engine.detach_play_api(info.play_id)
        self.plays.clear()
