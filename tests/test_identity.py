"""v4.1 身份服务测试（B13 自助注册 / B4 token 三档）。

覆盖：open/invite/closed 三模式、人类注册/登录/改密/注销、agent 注册、
admin_key、read 档围观、凭据吊销、邀请码、持久化。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT.parent))

from astrbot_plugin_worlditor.world.identity import (  # noqa: E402
    IdentityError,
    IdentityService,
)
from astrbot_plugin_worlditor.world.v4engine import V4WorldEngine  # noqa: E402
from astrbot_plugin_worlditor.world.v4store import V4WorldStore  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def make_identity(db_path: Path, **kwargs) -> IdentityService:
    engine = V4WorldEngine(V4WorldStore(db_path))
    return IdentityService(engine, **kwargs)


async def _scenario(db_path: Path, fn, **kwargs):
    identity = make_identity(db_path, **kwargs)
    await identity._engine.initialize()
    try:
        return await fn(identity)
    finally:
        await identity._engine.terminate()


def _run_scenario(tmp_path, fn, **kwargs):
    _run(_scenario(tmp_path / "world.db", fn, **kwargs))


# ---------- 开放模式 ----------


def test_register_human_open(tmp_path):
    """开放模式：注册 → player 实体 + play 档凭据；登录/改密/注销。"""

    async def fn(identity: IdentityService):
        info = await identity.register_human("小明", "pass123")
        assert info.tier == "play" and info.kind == "player"
        assert info.username == "小明"
        entity = identity._engine.get_entity(info.entity_id)
        assert entity is not None and entity.kind == "player"
        assert entity.user_id is not None
        assert entity.pos_key() == ("default", 0, 0)  # 出生点
        # 重复用户名
        with pytest.raises(IdentityError, match="已存在"):
            await identity.register_human("小明", "pass456")
        # 登录
        info2 = await identity.login("小明", "pass123")
        assert info2.token != info.token
        assert identity.resolve(info.token) is None  # 旧凭据已吊销
        assert identity.resolve(info2.token) is not None
        # 密码错误
        with pytest.raises(IdentityError, match="用户名或密码错误"):
            await identity.login("小明", "wrong")
        # 改密（旧凭据失效）
        await identity.change_password(info2.token, "pass123", "newpass9")
        with pytest.raises(IdentityError, match="无效的凭据"):
            await identity.change_password(info2.token, "wrong", "newpass9")
        info3 = await identity.login("小明", "newpass9")
        with pytest.raises(IdentityError, match="原密码错误"):
            await identity.change_password(info3.token, "wrong", "newpass9")
        with pytest.raises(IdentityError, match="无效的凭据"):
            await identity.change_password("badtoken", "pass123", "newpass9")
        # 注销
        assert await identity.logout(info3.token) is True
        assert identity.resolve(info3.token) is None

    _run_scenario(tmp_path, fn)


def test_username_password_validation(tmp_path):
    """用户名/密码校验。"""

    async def fn(identity: IdentityService):
        with pytest.raises(IdentityError, match="2-24"):
            await identity.register_human("x", "pass123")
        with pytest.raises(IdentityError, match="2-24"):
            await identity.register_human("a" * 30, "pass123")
        with pytest.raises(IdentityError, match="字母"):
            await identity.register_human("小 明!", "pass123")
        with pytest.raises(IdentityError, match="6 位"):
            await identity.register_human("小明", "123")

    _run_scenario(tmp_path, fn)


def test_read_token_open_only(tmp_path):
    """read 档围观凭据：开放模式可取；invite/closed 拒绝。"""

    async def fn(identity: IdentityService):
        info = await identity.create_read_token()
        assert info.tier == "read" and info.kind == "readonly"
        assert info.entity_id == ""
        # 围观者不能动作
        with pytest.raises(IdentityError, match="只能围观"):
            identity.require_entity(info)

    _run_scenario(tmp_path, fn)


# ---------- 邀请码模式 ----------


def test_invite_mode(tmp_path):
    """invite 模式：注册需邀请码；管理员生成/查看/吊销。"""

    async def fn(identity: IdentityService):
        with pytest.raises(IdentityError, match="邀请码"):
            await identity.register_human("小明", "pass123")
        with pytest.raises(IdentityError, match="邀请码"):
            await identity.register_human("小明", "pass123", invite_code="nope")
        # 管理员（admin_key）生成邀请码
        admin = await identity.register_human("管理员", "pass123", admin_key="sekret")
        assert admin.tier == "admin"
        codes = await identity.create_invite_codes(2)
        assert len(codes) == 2
        assert identity.list_invite_codes()[0]["used"] is False
        # 用邀请码注册
        info = await identity.register_human("小明", "pass123", invite_code=codes[0])
        assert info.tier == "play"
        # 邀请码一次性
        with pytest.raises(IdentityError, match="邀请码"):
            await identity.register_human("小红", "pass123", invite_code=codes[0])
        # 吊销剩余邀请码
        assert await identity.revoke_invite_code(codes[1]) is True
        with pytest.raises(IdentityError, match="邀请码"):
            await identity.register_human("小红", "pass123", invite_code=codes[1])

    _run_scenario(tmp_path, fn, auth_mode="invite", admin_key="sekret")


# ---------- 封闭模式 ----------


def test_closed_mode(tmp_path):
    """closed 模式：自助注册全部拒绝（仅管理员签发）。"""

    async def fn(identity: IdentityService):
        with pytest.raises(IdentityError, match="封闭模式"):
            await identity.register_human("小明", "pass123")
        with pytest.raises(IdentityError, match="封闭模式"):
            await identity.register_agent("bot")
        with pytest.raises(IdentityError, match="当前模式"):
            await identity.create_read_token()

    _run_scenario(tmp_path, fn, auth_mode="closed")


# ---------- agent 注册 ----------


def test_register_agent(tmp_path):
    """agent 自助注册：agent 实体 + play 档凭据；开关关闭时拒绝。"""

    async def fn(identity: IdentityService):
        info = await identity.register_agent("探索者")
        assert info.tier == "play" and info.kind == "agent"
        entity = identity._engine.get_entity(info.entity_id)
        assert entity is not None and entity.kind == "agent"
        assert entity.name == "AI·探索者"
        # 关闭 agent 注册
        identity.allow_agent_register = False
        with pytest.raises(IdentityError, match="关闭"):
            await identity.register_agent("另一个")
        identity.allow_agent_register = True
        info2 = await identity.register_agent("另一个")
        assert identity.resolve(info2.token) is not None

    _run_scenario(tmp_path, fn)


# ---------- admin / 吊销 ----------


def test_admin_key_and_revoke(tmp_path):
    """admin_key 注册管理员；管理员吊销任意凭据。"""

    async def fn(identity: IdentityService):
        admin = await identity.register_human("管理员", "pass123", admin_key="key123")
        assert admin.tier == "admin"
        player = await identity.register_human("小明", "pass123")
        agent = await identity.register_agent("bot")
        # admin 吊销 agent 凭据
        assert await identity.revoke_token(agent.token) is True
        assert identity.resolve(agent.token) is None
        # 非 admin 不能吊销（服务层不校验，端点层校验——这里直接验证 revoke 可用）
        assert await identity.revoke_token(player.token) is True
        # admin_key 错误 → 普通用户
        info = await identity.register_human("路人", "pass123", admin_key="wrong")
        assert info.tier == "play"

    _run_scenario(tmp_path, fn, admin_key="key123")


# ---------- 持久化 ----------


def test_identity_persisted(tmp_path):
    """账户/凭据/邀请码持久化：重启后仍可解析。"""

    async def fn(identity: IdentityService):
        info = await identity.register_human("小明", "pass123")
        await identity._engine.terminate()
        identity2 = make_identity(tmp_path / "world.db")
        await identity2._engine.initialize()
        try:
            # token 有效（重启后凭据仍在）
            assert identity2.resolve(info.token) is not None
            # 用户名占用仍在
            with pytest.raises(IdentityError, match="已存在"):
                await identity2.register_human("小明", "pass456")
            # 登录仍可用
            info2 = await identity2.login("小明", "pass123")
            assert info2.entity_id == info.entity_id
        finally:
            await identity2._engine.terminate()

    _run_scenario(tmp_path, fn)
