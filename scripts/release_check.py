"""发布检查脚本。

依据 docs/18-实施顺序与完成定义.md 15.2：

- 应用版本、`pyproject.toml`、Compose 镜像标签一致；
- 迁移链只有一个 head；
- OpenAPI 可生成且包含健康检查与业务路径；
- Dockerfile/Compose/入口脚本满足固定 Python、uv 锁文件、非 root、单 worker；
- 仓库不追踪 `.env`、数据库与备份；
- 已构建镜像可运行 CLI，镜像内无 `.env`、数据库和备份。

输出 JSON 报告；任一检查失败返回非零退出码。
"""

import argparse
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="only-stock 发布检查")
    parser.add_argument("--report", type=Path, default=Path("release-check.json"))
    parser.add_argument("--skip-image", action="store_true", help="不检查已构建镜像")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))

    checks = [
        _version_check(),
        _image_tag_check(),
        _migration_check(),
        _openapi_check(),
        _dockerfile_check(),
        _compose_check(),
        _entrypoint_check(),
        _repository_secrets_check(),
    ]
    if not args.skip_image:
        checks.extend(_image_checks())
    report = {
        "checks": [check.__dict__ for check in checks],
        "passed": all(check.passed for check in checks),
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for check in checks:
        status = "PASS" if check.passed else "FAIL"
        print(f"[{status}] {check.name}: {check.detail}")
    print(json.dumps({"passed": report["passed"]}, ensure_ascii=False))
    return 0 if report["passed"] else 1


def _version_check() -> Check:
    from app.version import APP_VERSION

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = str(pyproject["project"]["version"])
    passed = APP_VERSION == declared
    return Check("version", passed, f"app={APP_VERSION} pyproject={declared}")


def _image_tag_check() -> Check:
    from app.version import APP_VERSION

    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    expected = f"image: only-stock:{APP_VERSION}"
    docs = (ROOT / "docs/13-Docker部署与运维.md").read_text(encoding="utf-8")
    passed = expected in compose and f"only-stock:{APP_VERSION}" in docs
    return Check("image_tag", passed, expected)


def _migration_check() -> Check:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    heads = script.get_heads()
    passed = len(heads) == 1
    return Check("migration_single_head", passed, f"heads={heads}")


def _openapi_check() -> Check:
    from app.config import Settings
    from app.main import create_app
    from app.version import APP_VERSION

    settings = Settings.from_env(
        {
            "APP_ENV": "test",
            "DATA_DIR": str(Path("/tmp/only-stock-release-check/data")),
            "BACKUP_DIR": str(Path("/tmp/only-stock-release-check/backups")),
            "BOOTSTRAP_ADMIN_USERNAME": "",
            "BOOTSTRAP_ADMIN_PASSWORD": "",
        }
    )
    schema = create_app(settings).openapi()
    paths = schema["paths"]
    required = {"/health/live", "/health/ready", "/api/v1/documents", "/api/v1/backups"}
    passed = required.issubset(paths) and schema["info"]["version"] == APP_VERSION
    return Check(
        "openapi",
        passed,
        f"version={schema['info']['version']} paths={len(paths)} operations={_operations(paths)}",
    )


def _operations(paths: dict[str, object]) -> int:
    count = 0
    for operations in paths.values():
        if isinstance(operations, dict):
            count += sum(1 for method in operations if method.lower() in _HTTP_METHODS)
    return count


_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})


def _dockerfile_check() -> Check:
    content = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    conditions = {
        "python313": "python:3.13" in content,
        "python_digest": bool(re.search(r"python:3\.13[^\s]*@sha256:[0-9a-f]{64}", content)),
        "uv_digest": bool(re.search(r"ghcr\.io/astral-sh/uv:[^\s]*@sha256:[0-9a-f]{64}", content)),
        "locked_sync": "uv sync --locked --no-dev" in content,
        "non_root": "USER app" in content,
        "no_pip_install": "pip install" not in content,
        "alembic": "alembic" in content,
    }
    failed = [name for name, ok in conditions.items() if not ok]
    return Check("dockerfile", not failed, "ok" if not failed else f"failed={failed}")


def _compose_check() -> Check:
    content = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    conditions = {
        "single_service": content.count("  app:") == 1 and "  db:" not in content,
        "port_8000": '"8000:8000"' in content,
        "data_volume": "inventory_data:/data" in content,
        "backup_volume": "inventory_backups:/backups" in content,
        "healthcheck": "/health/ready" in content,
        "no_workers_override": "--workers" not in content,
    }
    failed = [name for name, ok in conditions.items() if not ok]
    return Check("compose", not failed, "ok" if not failed else f"failed={failed}")


def _entrypoint_check() -> Check:
    content = (ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")
    passed = "python -m app.cli migrate" in content and 'exec "$@"' in content
    return Check("entrypoint", passed, "migrate+exec" if passed else "missing migrate/exec")


def _repository_secrets_check() -> Check:
    tracked = _git(["ls-files"]).splitlines()
    forbidden = [
        path
        for path in tracked
        if path.endswith(".env")
        or path.startswith(("data/", "backups/", "logs/"))
        or path.endswith((".sqlite3", ".sqlite3-wal", ".sqlite3-shm"))
    ]
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    bootstrap_leak = any(
        line.strip().startswith("BOOTSTRAP_ADMIN_PASSWORD=")
        and line.strip() != "BOOTSTRAP_ADMIN_PASSWORD="
        for line in env_example.splitlines()
    )
    private_key = "BEGIN PRIVATE KEY" in _git(["grep", "-l", "BEGIN PRIVATE KEY", "--", "."])
    passed = not forbidden and not bootstrap_leak and not private_key
    detail = "ok"
    if forbidden:
        detail = f"tracked forbidden files={forbidden}"
    elif bootstrap_leak:
        detail = ".env.example 含非空初始密码"
    elif private_key:
        detail = "仓库含私钥材料"
    return Check("repository_secrets", passed, detail)


def _image_checks() -> list[Check]:
    from app.version import APP_VERSION

    image = f"only-stock:{APP_VERSION}"
    if not _image_exists(image):
        return [Check("image", False, f"{image} 未构建，先执行 docker build")]
    config = json.loads(_docker(["image", "inspect", image]))[0]["Config"]
    user = str(config.get("User") or "")
    command = " ".join(config.get("Cmd") or [])
    entrypoint = " ".join(config.get("Entrypoint") or [])
    checks = [
        Check("image_non_root", user not in ("", "root", "0"), f"user={user or 'root'}"),
        Check(
            "image_single_worker",
            "--workers 1" in command and "uvicorn" in command,
            f"cmd={command}",
        ),
        Check("image_entrypoint", "docker-entrypoint.sh" in entrypoint, f"entrypoint={entrypoint}"),
    ]
    filesystem = _docker(
        [
            "run",
            "--rm",
            "--entrypoint",
            "/bin/sh",
            image,
            "-c",
            "test ! -e /app/.env && test ! -e /data/inventory.sqlite3 && "
            "test -d /app/app && test -d /app/alembic && id -u",
        ],
        check=False,
    )
    checks.append(
        Check(
            "image_filesystem",
            _docker_ok(filesystem),
            "无 .env/数据库，含 app 与 alembic" if _docker_ok(filesystem) else filesystem[-200:],
        )
    )
    cli = _docker(
        ["run", "--rm", "--entrypoint", "python", image, "-m", "app.cli", "backup", "list"],
        check=False,
    )
    checks.append(
        Check(
            "image_cli",
            _docker_ok(cli),
            "backup list 可执行" if _docker_ok(cli) else cli[-200:],
        )
    )
    return checks


def _image_exists(image: str) -> bool:
    return _docker(["image", "inspect", image], check=False) != ""


def _docker(args: list[str], *, check: bool = True) -> str:
    result = subprocess.run(
        ["docker", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=_docker_environment(),
    )
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "docker 命令失败")
    if result.returncode != 0:
        return ""
    return result.stdout


def _docker_ok(output: str) -> bool:
    return bool(output.strip())


def _docker_environment() -> dict[str, str]:
    import os

    env = dict(os.environ)
    env.setdefault("DOCKER_CONFIG", "/tmp/docker-config")
    env.setdefault("BUILDX_CONFIG", "/tmp/buildx")
    return env


def _git(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else ""


if __name__ == "__main__":
    raise SystemExit(main())
