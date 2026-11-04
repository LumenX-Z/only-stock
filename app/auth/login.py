"""登录流程：限流、认证、审计与会话建立。

依据 docs/04-认证、会话与凭证安全.md 5.4：登录匿名但按 IP 与登录名限流；
登录成功写审计并建立会话；失败不区分未知账户与错误密码。
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from app.auth.account import authenticate_and_create_session
from app.auth.login_names import validate_login_name
from app.auth.rate_limit import LoginRateLimiter
from app.auth.sessions import SessionCredentials
from app.config import Settings
from app.errors import AppError
from app.modules.audit.service import record_audit
from app.modules.users.models import User


@dataclass(frozen=True, slots=True)
class LoginResult:
    credentials: SessionCredentials
    user: User


def login(
    session_factory: sessionmaker[Session],
    settings: Settings,
    limiter: LoginRateLimiter,
    *,
    ip: str,
    login_name: str,
    password: str,
    request_id: str | None,
    source: str = "API",
) -> LoginResult:
    validated_name = validate_login_name(login_name)
    retry_after = limiter.check(ip, validated_name)
    if retry_after is not None:
        raise AppError(
            "RATE_LIMITED",
            "登录尝试过于频繁，请稍后重试",
            status_code=429,
            resource="login",
            items=[{"retry_after_seconds": retry_after}],
        )

    try:
        credentials, user = authenticate_and_create_session(
            session_factory,
            settings,
            validated_name,
            password,
        )
    except AppError as error:
        if error.code == "INVALID_CREDENTIALS":
            limiter.record_failure(ip, validated_name)
        raise

    limiter.reset(ip, validated_name)
    with session_factory() as session:
        record_audit(
            session,
            actor_user_id=user.id,
            action="LOGIN_SUCCEEDED",
            entity_type="user",
            entity_id=user.id,
            after={"id": user.id, "login_name": user.login_name, "role": user.role},
            request_id=request_id,
            source=source,
        )
        session.commit()
    return LoginResult(credentials=credentials, user=user)
