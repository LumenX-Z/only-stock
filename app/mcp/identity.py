"""MCP 请求身份上下文。

依据 docs/04-认证、会话与凭证安全.md 5.5 第 7 条：每次请求都以 Key 当前所属
用户和数据库中的当前角色建立 MCP 身份，不在 Key 中固化角色。认证中间件在进入
协议处理前写入本上下文，工具调用再按 ``user_id``/``api_key_id`` 重新读取数据库
校验账户、Key 与权限。
"""

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MCPIdentity:
    """一次已认证 MCP 请求的最小身份；角色等可变信息每次从数据库读取。"""

    user_id: str
    api_key_id: str


_IDENTITY: ContextVar[MCPIdentity | None] = ContextVar("mcp_identity", default=None)


def set_identity(identity: MCPIdentity) -> Token[MCPIdentity | None]:
    return _IDENTITY.set(identity)


def reset_identity(token: Token[MCPIdentity | None]) -> None:
    _IDENTITY.reset(token)


def current_identity() -> MCPIdentity | None:
    return _IDENTITY.get()
