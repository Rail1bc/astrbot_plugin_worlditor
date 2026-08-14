"""v4.1 身份注册与凭据（B13 自助注册 / B4 token 三档）。

统一注册机制服务两类接入者：人类（WebUI）与 agent（MCP/远程），
**不依赖管理员人工签发**：

- ``auth_mode`` 三种世界注册模式：open（开放）/ invite（邀请码）/ closed（封闭）。
- token 三档：``read``（围观，开放模式可公开获取免注册）/ ``play``（移动/交互/
  说话）/ ``admin``（编辑/管理）。
- 人类 = 账户（用户名+密码）→ 绑定 ``player`` 实体；凭据可自助改密/注销。
- agent = 注册接口 → 创建 ``agent`` 实体 + 凭据；管理员可吊销。
- ``admin_key`` 配置：注册时提供它的账户自动成为管理员（管理员只做配置、
  邀请码与吊销）。

密码哈希用 PBKDF2-HMAC-SHA256（标准库，无外部依赖）；token 为 secrets 随机
hex。身份服务是纯逻辑层（不依赖 AstrBot），可独立单测。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

AUTH_MODES = ("open", "invite", "closed")
TOKEN_TIERS = ("read", "play", "admin")
_PBKDF2_ITERATIONS = 200_000


class IdentityError(Exception):
    """身份业务错误（用户名已存在、密码错误、模式限制等），消息可直接展示。"""


@dataclass
class Account:
    """人类账户（唯一标识 = uuid4 hex）。"""

    id: str
    username: str
    password_hash: str
    role: str  # "user" | "admin"
    created_ts: float


@dataclass
class TokenInfo:
    """一份凭据：token → 实体（kind=player/agent）或围观（kind=readonly）。"""

    token: str
    entity_id: str
    tier: str
    kind: str  # "player" | "agent" | "readonly"
    account_id: str | None = None
    username: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "entity_id": self.entity_id,
            "tier": self.tier,
            "kind": self.kind,
            "username": self.username,
        }


def _hash_password(password: str) -> str:
    """PBKDF2 哈希：'pbkdf2$iter$salt_hex$hash_hex'。"""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return f"pbkdf2${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        algo, iter_s, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iter_s),
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def _new_token() -> str:
    return secrets.token_hex(24)


class IdentityService:
    """身份服务：注册 / 登录 / 凭据管理与校验（操作走引擎锁，防并发竞态）。"""

    def __init__(
        self,
        engine: Any,
        *,
        auth_mode: str = "open",
        admin_key: str = "",
        allow_agent_register: bool = True,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._engine = engine  # V4WorldEngine（创建 player/agent 实体）
        if auth_mode not in AUTH_MODES:
            raise ValueError(f"auth_mode 必须是 {'/'.join(AUTH_MODES)} 之一")
        self.auth_mode = auth_mode
        self.admin_key = admin_key or ""
        self.allow_agent_register = allow_agent_register
        self._clock = clock or time.time

    # ---------- 注册 / 登录 ----------

    async def register_human(
        self,
        username: str,
        password: str,
        *,
        invite_code: str | None = None,
        admin_key: str | None = None,
    ) -> TokenInfo:
        """人类自助注册：创建账户 + 绑定 player 实体 + 发放 play 档凭据。

        Raises:
            IdentityError: 模式限制 / 用户名非法或已存在 / 密码过弱 / 邀请码无效。
        """
        username = self._check_username(username)
        self._check_password(password)
        is_admin = bool(admin_key and self.admin_key and admin_key == self.admin_key)
        if not is_admin:
            # 管理员注册通道（admin_key）豁免模式限制——否则 invite 模式下
            # 第一个管理员无法注册（鸡生蛋）
            self._check_invite(self.auth_mode, invite_code)
        async with self._engine._lock:
            if self._engine.store.get_account_by_username(username) is not None:
                raise IdentityError("用户名已存在")
            role = "admin" if is_admin else "user"
            account = Account(
                id=uuid.uuid4().hex,
                username=username,
                password_hash=_hash_password(password),
                role=role,
                created_ts=self._now(),
            )
            spawn = self._spawn_pos()
            entity = await self._engine.place_entity(
                "player",
                spawn[0],
                spawn[1],
                spawn[2],
                name=username,
                user_id=account.id,
            )
            await self._engine.store.save_account(account)
            token = await self._issue_token(
                entity_id=entity.id,
                tier="admin" if role == "admin" else "play",
                kind="player",
                account_id=account.id,
                username=username,
            )
            if invite_code:
                await self._engine.store.set_invite_code_used(invite_code)
            return token

    async def register_agent(
        self, name: str, *, invite_code: str | None = None
    ) -> TokenInfo:
        """agent 自助注册：创建 agent 实体 + 发放 play 档凭据（B13）。

        Raises:
            IdentityError: 模式限制 / agent 注册关闭 / 邀请码无效 / 名称非法。
        """
        name = self._check_username(name)
        if not self.allow_agent_register:
            raise IdentityError("agent 自助注册已关闭，请联系管理员")
        self._check_invite(self.auth_mode, invite_code)
        async with self._engine._lock:
            spawn = self._spawn_pos()
            entity = await self._engine.place_entity(
                "agent", spawn[0], spawn[1], spawn[2], name=f"AI·{name}"
            )
            token = await self._issue_token(
                entity_id=entity.id, tier="play", kind="agent", username=name
            )
            if invite_code:
                await self._engine.store.set_invite_code_used(invite_code)
            return token

    async def login(self, username: str, password: str) -> TokenInfo:
        """人类登录：校验密码，刷新凭据（旧 token 吊销）。"""
        async with self._engine._lock:
            account = self._engine.store.get_account_by_username(username.strip())
            if account is None or not _verify_password(password, account.password_hash):
                raise IdentityError("用户名或密码错误")
            await self._engine.store.revoke_tokens_of_account(account.id)
            entity = self._find_entity_of_account(account.id)
            if entity is None:
                raise IdentityError("账户未绑定世界角色，请联系管理员")
            return await self._issue_token(
                entity_id=entity.id,
                tier="admin" if account.role == "admin" else "play",
                kind="player",
                account_id=account.id,
                username=account.username,
            )

    async def create_read_token(self) -> TokenInfo:
        """read 档围观凭据（开放模式可公开获取，免注册；B13）。"""
        if self.auth_mode != "open":
            raise IdentityError("当前模式不提供公开围观凭据")
        return await self._issue_token(entity_id="", tier="read", kind="readonly")

    # ---------- 凭据管理 ----------

    async def change_password(
        self, token: str, old_password: str, new_password: str
    ) -> None:
        """玩家自助改密（登录后旧凭据全部吊销，需重新登录）。"""
        info = self.resolve(token)
        if (
            info is None
            or info.tier not in ("play", "admin")
            or info.account_id is None
        ):
            raise IdentityError("无效的凭据")
        async with self._engine._lock:
            account = self._engine.store.get_account(info.account_id)
            if account is None or not _verify_password(
                old_password, account.password_hash
            ):
                raise IdentityError("原密码错误")
            self._check_password(new_password)
            account.password_hash = _hash_password(new_password)
            await self._engine.store.save_account(account)
            await self._engine.store.revoke_tokens_of_account(account.id)

    async def logout(self, token: str) -> bool:
        """注销当前凭据。"""
        return await self.revoke_token(token)

    async def revoke_token(self, token: str) -> bool:
        """吊销一份凭据（管理员吊销 agent 凭据 / 玩家注销）。"""
        async with self._engine._lock:
            return await self._engine.store.set_token_revoked(token, True)

    # ---------- 邀请码（invite 模式，管理员） ----------

    async def create_invite_codes(self, count: int = 1) -> list[str]:
        """批量生成邀请码（管理员）。"""
        codes: list[str] = []
        async with self._engine._lock:
            for _ in range(max(1, count)):
                code = secrets.token_hex(6)
                await self._engine.store.save_invite_code(code, self._now())
                codes.append(code)
        return codes

    def list_invite_codes(self) -> list[dict]:
        return self._engine.store.list_invite_codes()

    async def revoke_invite_code(self, code: str) -> bool:
        """吊销一个未使用的邀请码。"""
        async with self._engine._lock:
            return await self._engine.store.set_invite_code_used(code)

    # ---------- 凭据校验 ----------

    def resolve(self, token: str) -> TokenInfo | None:
        """解析 token → 身份；无效/已吊销/空返回 None。"""
        if not token:
            return None
        return self._engine.store.get_token(token)

    def require_entity(self, info: TokenInfo | None) -> str:
        """要求 play/admin 档（有实体）；围观者无实体不可动作。

        Raises:
            IdentityError: 未认证或围观档。
        """
        if info is None:
            raise IdentityError("无效的凭据")
        if info.tier not in ("play", "admin") or not info.entity_id:
            raise IdentityError("当前凭据只能围观，不能执行该操作")
        return info.entity_id

    def require_admin(self, info: TokenInfo | None) -> None:
        """要求 admin 档（编辑/管理）。"""
        if info is None or info.tier != "admin":
            raise IdentityError("需要管理员凭据")

    # ---------- 内部 ----------

    def _now(self) -> float:
        return self._clock()

    def _check_username(self, username: str) -> str:
        username = (username or "").strip()
        if not (2 <= len(username) <= 24):
            raise IdentityError("用户名长度须为 2-24 个字符")
        if not all(ch.isalnum() or ch in "_-" for ch in username):
            raise IdentityError("用户名只能包含字母、数字、下划线、连字符")
        return username

    def _check_password(self, password: str) -> None:
        if not password or len(password) < 6:
            raise IdentityError("密码至少 6 位")

    def _check_invite(self, mode: str, invite_code: str | None) -> None:
        if mode == "closed":
            raise IdentityError("当前世界为封闭模式，仅管理员可签发凭据")
        if mode == "invite":
            code = (invite_code or "").strip()
            entry = self._engine.store.get_invite_code(code)
            if entry is None or entry["used"]:
                raise IdentityError("邀请码无效或已使用")

    def _find_entity_of_account(self, account_id: str):
        for e in self._engine.list_entities():
            if e.user_id == account_id:
                return e
        return None

    def _spawn_pos(self) -> tuple[str, int, int]:
        """默认地图出生点（注册时的初始位置）。"""
        m = next(iter(self._engine.store.maps.values()), None)
        if m is None:
            raise IdentityError("世界尚未初始化")
        return (m.id, m.spawn_row, m.spawn_col)

    async def _issue_token(
        self,
        *,
        entity_id: str,
        tier: str,
        kind: str,
        account_id: str | None = None,
        username: str | None = None,
    ) -> TokenInfo:
        token = _new_token()
        info = TokenInfo(
            token=token,
            entity_id=entity_id,
            tier=tier,
            kind=kind,
            account_id=account_id,
            username=username,
        )
        await self._engine.store.save_token(info, self._now())
        return info
