"""应用 CLI 入口。

P1 只提供可执行入口；初始化、迁移、备份、恢复和回滚命令按
docs/13-Docker部署与运维.md 10.4–10.7 在后续阶段实现。
"""

import argparse
from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app.cli",
        description="only-stock 管理命令（备份与恢复命令将在 P9 提供）",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
