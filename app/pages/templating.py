"""页面模板渲染、时间格式化、Flash 与一次性密钥存储。

页面与 ``/api/v1`` 分离，但共用服务层和权限组件；页面模板只负责展示，
不复制业务规则（docs/09 8.1、docs/12 第 9 章）。
"""

import json
import secrets
import threading
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.templating import Jinja2Templates

from app.auth.dependencies import AuthContext
from app.modules.inventory.quantity import format_quantity_milli

_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


class _MessageStore:
    """进程内 Flash/一次性数据；单 worker 部署，条目带短 TTL。"""

    def __init__(self, ttl_seconds: float = 300.0) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._messages: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._secrets: dict[str, tuple[float, str, str]] = {}

    def add_message(
        self,
        owner: str,
        message: str,
        *,
        level: str = "success",
        detail: str | None = None,
        link_url: str | None = None,
        link_label: str | None = None,
    ) -> None:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            entry = self._messages.get(owner)
            items = entry[1] if entry else []
            items.append(
                {
                    "message": message,
                    "level": level,
                    "detail": detail,
                    "link_url": link_url,
                    "link_label": link_label,
                }
            )
            self._messages[owner] = (now, items)

    def take_messages(self, owner: str) -> list[dict[str, Any]]:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            entry = self._messages.pop(owner, None)
            return entry[1] if entry else []

    def put_secret(self, owner: str, value: str) -> str:
        token = secrets.token_urlsafe(24)
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            self._secrets[token] = (now, owner, value)
        return token

    def pop_secret(self, token: str, owner: str) -> str | None:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            entry = self._secrets.pop(token, None)
            if entry is None:
                return None
            _, entry_owner, value = entry
            return value if entry_owner == owner else None

    def _prune(self, now: float) -> None:
        cutoff = now - self._ttl
        for key in [key for key, value in self._messages.items() if value[0] < cutoff]:
            self._messages.pop(key, None)
        for key in [key for key, value in self._secrets.items() if value[0] < cutoff]:
            self._secrets.pop(key, None)


store = _MessageStore()


def flash(
    auth: AuthContext,
    message: str,
    *,
    level: str = "success",
    detail: str | None = None,
    link_url: str | None = None,
    link_label: str | None = None,
) -> None:
    store.add_message(
        auth.session.id_hash,
        message,
        level=level,
        detail=detail,
        link_url=link_url,
        link_label=link_label,
    )


def take_flashes(auth: AuthContext | None) -> list[dict[str, Any]]:
    return store.take_messages(auth.session.id_hash) if auth is not None else []


def remember_secret(auth: AuthContext, value: str) -> str:
    return store.put_secret(auth.session.id_hash, value)


def take_secret(auth: AuthContext, token: str | None) -> str | None:
    if not token:
        return None
    return store.pop_secret(token, auth.session.id_hash)


def _local(value: datetime | date | None, timezone: Any, fmt: str) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(timezone).strftime(fmt)
    return value.strftime(fmt)


def _local_time(value: datetime | None, timezone: Any) -> str:
    return _local(value, timezone, "%Y-%m-%d %H:%M")


def _local_date(value: datetime | date | None, timezone: Any) -> str:
    return _local(value, timezone, "%Y-%m-%d")


def _pretty_json(value: Any) -> str:
    if value is None:
        return "—"
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


templates.env.filters["local_time"] = _local_time
templates.env.filters["local_date"] = _local_date
templates.env.filters["pretty_json"] = _pretty_json
templates.env.globals["local_time"] = _local_time
templates.env.globals["local_date"] = _local_date
templates.env.filters["quantity"] = format_quantity_milli


def is_active(request: Request, prefix: str) -> bool:
    path = request.url.path
    if prefix == "/":
        return path == "/"
    return path == prefix or path.startswith(prefix + "/")


def page_url(request: Request, page: int) -> str:
    pairs = [(key, value) for key, value in request.query_params.multi_items() if key != "page"]
    pairs.append(("page", str(max(1, page))))
    return str(request.url.replace(query=urlencode(pairs)))


def _category_path(category: Any, categories: dict[str, Any]) -> str:
    from app.pages.common import category_path

    return category_path(category, categories)


templates.env.globals["is_active"] = is_active
templates.env.globals["page_url"] = page_url
templates.env.globals["category_path"] = _category_path


def render(
    request: Request,
    template: str,
    *,
    auth: AuthContext | None = None,
    status_code: int = 200,
    **context: Any,
) -> HTMLResponse:
    settings = request.app.state.settings
    payload: dict[str, Any] = {
        "request": request,
        "auth": auth,
        "csrf_token": auth.csrf_token if auth else "",
        "flashes": take_flashes(auth),
        "tz": settings.timezone,
    }
    payload.update(context)
    return templates.TemplateResponse(request, template, payload, status_code=status_code)


def render_partial(request: Request, template: str, **context: Any) -> HTMLResponse:
    payload: dict[str, Any] = {"request": request}
    payload.update(context)
    return templates.TemplateResponse(request, template, payload)


def redirect(url: str, status_code: int = 303) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=status_code)


def render_error_page(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    details: dict[str, Any] | None = None,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "request": request,
            "status_code": status_code,
            "code": code,
            "message": message,
            "request_id": request_id,
            "details": details or {"resource": None, "items": []},
            "tz": request.app.state.settings.timezone,
        },
        status_code=status_code,
    )


def login_redirect(request: Request) -> RedirectResponse:
    next_url = request.url.path
    if request.url.query:
        next_url = f"{next_url}?{request.url.query}"
    return RedirectResponse(url=f"/login?next={next_url}", status_code=303)


def callable_page_url(request: Request) -> Callable[[int], str]:
    return lambda page: page_url(request, page)
