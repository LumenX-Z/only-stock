"""可读单号生成。

依据 docs/05-数据模型与实体约束.md 6.5：单号示例为 ``RK20261024-000001``
（入库）与 ``CK20261024-000001``（出库），数据库内唯一，不要求断号连续。
单号生成、单据写入与计数器更新必须在同一 ``BEGIN IMMEDIATE`` 事务内完成。
"""

from datetime import date

from sqlalchemy.orm import Session

from app.errors import AppError
from app.modules.documents.models import DocType, DocumentCounter

DOC_NO_PREFIXES: dict[str, str] = {
    DocType.INBOUND.value: "RK",
    DocType.OUTBOUND.value: "CK",
}
DOC_NO_SEQUENCE_LIMIT = 999_999


def next_doc_no(session: Session, doc_type: str, biz_date: date) -> str:
    """在调用方的写事务内生成下一个单号并推进计数器。"""
    try:
        prefix = DOC_NO_PREFIXES[doc_type]
    except KeyError as exc:  # pragma: no cover - 调用方已校验
        raise ValueError(f"未知单据类型：{doc_type}") from exc

    key = biz_date.isoformat()
    counter = session.get(DocumentCounter, (key, doc_type))
    if counter is None:
        counter = DocumentCounter(biz_date=key, doc_kind=doc_type, next_value=1)
        session.add(counter)
        session.flush()

    sequence = int(counter.next_value)
    if sequence > DOC_NO_SEQUENCE_LIMIT:
        raise AppError(
            "DOCUMENT_NUMBER_EXHAUSTED",
            "当日单号已用尽，需扩展单号方案后才能继续创建",
            status_code=409,
            resource="document",
            items=[{"biz_date": key, "doc_type": doc_type}],
        )
    counter.next_value = sequence + 1
    session.flush()
    return f"{prefix}{biz_date:%Y%m%d}-{sequence:06d}"
