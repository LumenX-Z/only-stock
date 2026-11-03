from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.error_handlers import register_error_handlers
from app.errors import AppError


class Payload(BaseModel):
    password: str
    required_field: str


def _create_error_test_app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/app-error")
    def app_error() -> None:
        raise AppError(
            "EXAMPLE_CONFLICT",
            "示例冲突",
            status_code=409,
            resource="warehouse",
            items=[{"code": "CK-00001"}],
        )

    @app.post("/validate")
    def validate(payload: Payload) -> dict[str, bool]:
        return {"ok": bool(payload)}

    @app.get("/crash")
    def crash() -> None:
        raise RuntimeError("secret internal detail")

    return app


def _client() -> TestClient:
    return TestClient(_create_error_test_app(), raise_server_exceptions=False)


def _error(response_json: dict[str, Any]) -> dict[str, Any]:
    return response_json["error"]


def test_app_error_uses_unified_structure() -> None:
    response = _client().get("/app-error")

    assert response.status_code == 409
    error = _error(response.json())
    assert error["code"] == "EXAMPLE_CONFLICT"
    assert error["message"] == "示例冲突"
    assert error["request_id"]
    assert error["details"] == {"resource": "warehouse", "items": [{"code": "CK-00001"}]}


def test_not_found_uses_unified_structure() -> None:
    response = _client().get("/does-not-exist")

    assert response.status_code == 404
    error = _error(response.json())
    assert error["code"] == "NOT_FOUND"
    assert error["details"] == {"resource": None, "items": []}


def test_method_not_allowed_uses_unified_structure() -> None:
    response = _client().post("/app-error")

    assert response.status_code == 405
    assert _error(response.json())["code"] == "METHOD_NOT_ALLOWED"


def test_validation_error_does_not_echo_input_values() -> None:
    response = _client().post("/validate", json={"password": "s3cr3t-value"})

    assert response.status_code == 422
    error = _error(response.json())
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"]["resource"] is None
    assert error["details"]["items"]
    assert error["details"]["items"][0]["field"] == "body.required_field"
    assert error["details"]["items"][0]["reason"]
    assert "s3cr3t-value" not in response.text


def test_unexpected_error_is_generic_and_hides_details() -> None:
    response = _client().get("/crash")

    assert response.status_code == 500
    error = _error(response.json())
    assert error["code"] == "INTERNAL_ERROR"
    assert error["message"] == "服务器内部错误"
    assert error["request_id"] == response.headers["X-Request-ID"]
    assert "secret internal detail" not in response.text
