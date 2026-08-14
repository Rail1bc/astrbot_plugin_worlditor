"""stdio 传输入口（v4.1，B7 本地接入）。

独立进程运行：本地 AstrBot（或任意 MCP client）以 stdio 配置连接——
一个 stdio 连接绑定**一个实体身份**（启动凭据），工具调用默认以该身份执行。

用法：
    python -m astrbot_plugin_worlditor.world.mcp.stdio \
        --db <world.db 路径> [--plays-dir <玩法包目录>] \
        [--token <agent 凭据>]

凭据也可经环境变量提供：``WORLDITOR_TOKEN``、``WORLDITOR_DB``。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from ..identity import IdentityService
from ..play import PlayLoader
from ..v4engine import V4WorldEngine
from ..v4store import V4WorldStore
from . import build_mcp_server

_PLUGIN_ROOT = Path(__file__).resolve().parents[2]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="worlditor MCP stdio server")
    parser.add_argument(
        "--db", default=os.environ.get("WORLDITOR_DB", ""), help="world.db 路径"
    )
    parser.add_argument(
        "--plays-dir", default="", help="玩法包目录（<数据目录>/plays）"
    )
    parser.add_argument(
        "--token", default=os.environ.get("WORLDITOR_TOKEN", ""), help="实体凭据"
    )
    return parser.parse_args(argv)


async def _amain(argv: list[str]) -> None:
    args = _parse_args(argv)
    if not args.db:
        raise SystemExit("缺少 --db（或环境变量 WORLDITOR_DB）")
    if not args.token:
        raise SystemExit("缺少 --token（或环境变量 WORLDITOR_TOKEN）")
    engine = V4WorldEngine(V4WorldStore(Path(args.db)))
    await engine.initialize()
    try:
        identity = IdentityService(engine, auth_mode="open")
        info = identity.resolve(args.token)
        if info is None:
            raise SystemExit("凭据无效或已吊销")
        loader = PlayLoader(
            engine,
            plays_dir=Path(args.plays_dir)
            if args.plays_dir
            else Path(args.db).parent / "plays",
            demo_dir=_PLUGIN_ROOT / "demo_play",
        )
        await loader.load_all(None)
        mcp = build_mcp_server(engine, fixed_identity=info)
        await mcp.run_stdio_async()
    finally:
        await engine.terminate()


def main() -> None:
    # Windows 下 stdout/stderr 默认按 locale 编码（GBK），MCP 协议帧要求
    # UTF-8——强制重配置，否则 MCP client 解码失败
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):  # pragma: no cover
            pass
    asyncio.run(_amain(sys.argv[1:]))


if __name__ == "__main__":
    main()
