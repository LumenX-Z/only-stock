"""10 并发、5 分钟只读压测脚本。

依据 docs/14-安全、可靠性与性能要求.md 11.3 与 docs/16 ACC-PERF-002：
在隔离临时数据库上生成 100,000 条有效明细，使用 10 个并发线程持续查询
库存、单据列表和库存流水，统计 P95 与错误率，并输出 JSON 报告。

只读脚本：不读写开发数据库、生产数据卷或真实备份目录。
"""

import argparse
import json
import math
import os
import platform
import sqlite3
import statistics
import sys
import threading
import time
from array import array
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="only-stock 10 万明细并发只读压测")
    parser.add_argument("--minutes", type=float, default=5.0, help="压测持续分钟数")
    parser.add_argument("--concurrency", type=int, default=10, help="并发查询线程数")
    parser.add_argument("--documents", type=int, default=1_000)
    parser.add_argument("--lines-per-document", type=int, default=100)
    parser.add_argument("--data-dir", type=Path, default=None, help="隔离数据目录")
    parser.add_argument("--report", type=Path, default=Path("perf-report.json"))
    parser.add_argument("--p95-limit-ms", type=float, default=2_000.0)
    parser.add_argument("--error-rate-limit", type=float, default=0.001)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.minutes <= 0 or args.concurrency <= 0:
        print("--minutes 与 --concurrency 必须大于 0", file=sys.stderr)
        return 2

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import tempfile

    from app.config import Settings
    from app.db.database import create_engine_from_settings, create_session_factory
    from app.db.migrations import upgrade_to_head
    from tests.performance.support import build_dataset

    root = args.data_dir or Path(tempfile.mkdtemp(prefix="only-stock-perf-"))
    settings = Settings.from_env(
        {
            "APP_ENV": "test",
            "DATA_DIR": str(root / "data"),
            "BACKUP_DIR": str(root / "backups"),
            "BOOTSTRAP_ADMIN_USERNAME": "",
            "BOOTSTRAP_ADMIN_PASSWORD": "",
        }
    )
    started_at = time.time()
    print(f"准备隔离数据库：{settings.database_path}", flush=True)
    upgrade_to_head(settings)
    stats = build_dataset(
        settings.database_path,
        documents=args.documents,
        lines_per_document=args.lines_per_document,
    )
    print(f"数据准备完成：{stats.lines} 条有效明细，{stats.database_bytes} 字节", flush=True)

    engine = create_engine_from_settings(settings)
    session_factory = create_session_factory(engine)
    latencies = array("d")
    errors: list[str] = []
    counters = {"iterations": 0, "errors": 0}
    lock = threading.Lock()
    stop_at = time.monotonic() + args.minutes * 60

    _warm_up(session_factory)

    def worker() -> None:
        from app.modules.documents.service import DocumentFilters, list_documents
        from app.modules.inventory.service import list_inventory, list_transactions

        local_latencies = array("d")
        local_errors = 0
        while time.monotonic() < stop_at:
            try:
                query_started = time.perf_counter()
                with session_factory() as session:
                    list_inventory(session, page=1, page_size=20)
                    list_documents(
                        session,
                        filters=DocumentFilters(status="POSTED"),
                        page=1,
                        page_size=20,
                    )
                    list_transactions(session, page=1, page_size=20)
                local_latencies.append((time.perf_counter() - query_started) * 1000)
            except Exception as exc:  # 压测需统计错误率，不中断其他线程
                local_errors += 1
                with lock:
                    if len(errors) < 20:
                        errors.append(f"{type(exc).__name__}: {exc}")
        with lock:
            latencies.extend(local_latencies)
            counters["iterations"] += len(local_latencies)
            counters["errors"] += local_errors

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.concurrency)]
    print(
        f"开始压测：{args.concurrency} 并发，{args.minutes} 分钟，只读查询",
        flush=True,
    )
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    engine.dispose()
    duration = time.time() - started_at
    iterations = counters["iterations"]
    error_count = counters["errors"]
    error_rate = error_count / (iterations + error_count) if iterations + error_count else 0.0
    p95 = _percentile(latencies, 0.95)
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": _environment(),
        "dataset": {
            "documents": stats.documents,
            "lines": stats.lines,
            "materials": stats.materials,
            "database_bytes": stats.database_bytes,
        },
        "load": {
            "concurrency": args.concurrency,
            "duration_seconds": round(duration, 3),
            "iterations": iterations,
            "errors": error_count,
            "error_rate": round(error_rate, 6),
        },
        "latency_ms": {
            "p50": round(_percentile(latencies, 0.50), 3),
            "p95": round(p95, 3),
            "p99": round(_percentile(latencies, 0.99), 3),
            "max": round(max(latencies, default=0.0), 3),
            "mean": round(statistics.fmean(latencies), 3) if latencies else 0.0,
        },
        "thresholds": {
            "p95_limit_ms": args.p95_limit_ms,
            "error_rate_limit": args.error_rate_limit,
        },
        "passed": p95 <= args.p95_limit_ms and error_rate < args.error_rate_limit,
        "sample_errors": errors,
        "data_dir": str(settings.data_dir),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["passed"] else 1


def _warm_up(session_factory: object) -> None:
    from app.modules.documents.service import DocumentFilters, list_documents
    from app.modules.inventory.service import list_inventory, list_transactions

    for _ in range(5):
        with session_factory() as session:  # type: ignore[operator]
            list_inventory(session, page=1, page_size=20)
            list_documents(session, filters=DocumentFilters(status="POSTED"), page=1, page_size=20)
            list_transactions(session, page=1, page_size=20)


def _percentile(values: array, percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _environment() -> dict[str, object]:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "cpu_affinity": _cpu_affinity(),
        "sqlite": sqlite3.sqlite_version,
        "memory_bytes": _memory_bytes(),
    }


def _cpu_affinity() -> int | None:
    if not hasattr(os, "sched_getaffinity"):
        return None
    try:
        return len(os.sched_getaffinity(0))
    except OSError:
        return None


def _memory_bytes() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except OSError:
        return None
    return None


if __name__ == "__main__":
    raise SystemExit(main())
