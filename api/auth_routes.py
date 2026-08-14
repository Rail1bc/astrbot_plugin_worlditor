"""v4.1 身份注册端点（B13 自助注册 / B4 token 三档）。

REST（公网可达）：register / register-agent / login / logout /
change-password / revoke / read-token / invite-codes。
"""

from __future__ import annotations

from astrbot.api.web import json_response, request

from ..world.identity import IdentityError
from .v4common import auth_guard, error_response, token_from_request


class V4AuthAPI:
    """身份注册与凭据管理（挂载到插件 Star）。"""

    async def world_v4_register(self):
        """人类自助注册（用户名+密码 → player 实体 + play 档凭据）。

        Body:
            username / password / invite_code（invite 模式）/ admin_key（可选）。
        """
        try:
            data = await request.json() or {}
            info = await self.identity.register_human(
                str(data.get("username") or ""),
                str(data.get("password") or ""),
                invite_code=data.get("invite_code"),
                admin_key=data.get("admin_key"),
            )
        except IdentityError as e:
            return error_response(e)
        return json_response({"ok": True, "token": info.to_dict()})

    async def world_v4_login(self):
        """人类登录（旧凭据吊销，刷新新凭据）。"""
        try:
            data = await request.json() or {}
            info = await self.identity.login(
                str(data.get("username") or ""), str(data.get("password") or "")
            )
        except IdentityError as e:
            return error_response(e)
        return json_response({"ok": True, "token": info.to_dict()})

    async def world_v4_register_agent(self):
        """agent 自助注册（→ agent 实体 + play 档凭据）。"""
        try:
            data = await request.json() or {}
            info = await self.identity.register_agent(
                str(data.get("name") or ""), invite_code=data.get("invite_code")
            )
        except IdentityError as e:
            return error_response(e)
        return json_response({"ok": True, "token": info.to_dict()})

    async def world_v4_read_token(self):
        """开放模式：获取 read 档围观凭据（免注册）。"""
        try:
            info = await self.identity.create_read_token()
        except IdentityError as e:
            return error_response(e)
        return json_response({"ok": True, "token": info.to_dict()})

    async def world_v4_logout(self):
        """注销当前凭据。"""
        token = token_from_request()
        ok = await self.identity.logout(token) if token else False
        return json_response({"ok": ok})

    async def world_v4_change_password(self):
        """玩家自助改密（改后旧凭据全部失效，需重新登录）。"""
        try:
            data = await request.json() or {}
            await self.identity.change_password(
                token_from_request(),
                str(data.get("old_password") or ""),
                str(data.get("new_password") or ""),
            )
        except IdentityError as e:
            return error_response(e)
        return json_response({"ok": True})

    async def world_v4_revoke(self):
        """管理员吊销一份凭据（agent 凭据 / 玩家会话）。"""
        try:
            auth_guard(self.identity, tiers=("admin",))
            data = await request.json() or {}
            token = str(data.get("token") or "")
            if not token:
                raise IdentityError("缺少 token 参数")
            ok = await self.identity.revoke_token(token)
        except IdentityError as e:
            return error_response(e)
        return json_response({"ok": ok})

    async def world_v4_create_invite_codes(self):
        """管理员批量生成邀请码（invite 模式）。"""
        try:
            auth_guard(self.identity, tiers=("admin",))
            data = await request.json() or {}
            count = data.get("count", 1)
            if not isinstance(count, int) or isinstance(count, bool) or count < 1:
                raise IdentityError("count 必须是正整数")
            codes = await self.identity.create_invite_codes(min(count, 100))
        except IdentityError as e:
            return error_response(e)
        return json_response({"ok": True, "codes": codes})

    async def world_v4_list_invite_codes(self):
        """管理员查看邀请码列表。"""
        try:
            auth_guard(self.identity, tiers=("admin",))
        except IdentityError as e:
            return error_response(e)
        return json_response({"ok": True, "codes": self.identity.list_invite_codes()})
