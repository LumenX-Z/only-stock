"""请求指纹与草稿输入规范化单元测试。

依据 docs/08-MCP服务与工具.md 7.9 第 6 条、docs/16-验收标准.md ACC-DATA-030。
"""

import re
from uuid import uuid4

import pytest

from app.errors import AppError
from app.modules.documents.fingerprint import compute_request_fingerprint
from app.modules.documents.inputs import (
    DocumentInput,
    DocumentLineInput,
    prepare_document,
)

WAREHOUSE_ID = str(uuid4())
DEPARTMENT_ID = str(uuid4())
MATERIAL_ID = str(uuid4())
PROJECT_ID = str(uuid4())
MANUFACTURER_ID = str(uuid4())


def _document(
    *,
    quantity: str = "1",
    remark: str | None = None,
    project_id: str | None = None,
    manufacturer_id: str | None = MANUFACTURER_ID,
    lines: tuple[DocumentLineInput, ...] | None = None,
) -> DocumentInput:
    return DocumentInput(
        doc_type="INBOUND",
        warehouse_id=WAREHOUSE_ID,
        department_id=DEPARTMENT_ID,
        lines=lines
        or (
            DocumentLineInput(
                material_id=MATERIAL_ID,
                quantity=quantity,
                manufacturer_id=manufacturer_id,
                project_id=project_id,
                remark=remark,
            ),
        ),
    )


def _fingerprint(document: DocumentInput, *, submit: bool = False) -> str:
    return compute_request_fingerprint(prepare_document(document), submit=submit)


def test_fingerprint_is_sha256_hex() -> None:
    fingerprint = _fingerprint(_document())
    assert re.fullmatch(r"[0-9a-f]{64}", fingerprint)


def test_line_remark_omitted_null_and_empty_share_fingerprint() -> None:
    fingerprints = {
        _fingerprint(_document(remark=None)),
        _fingerprint(_document(remark="")),
        _fingerprint(_document(remark="   ")),
    }
    assert len(fingerprints) == 1


def test_line_remark_participates_in_fingerprint() -> None:
    assert _fingerprint(_document(remark="批次 A")) != _fingerprint(_document(remark="批次 B"))


def test_quantity_1_and_1_0_share_fingerprint_but_change_when_different() -> None:
    assert _fingerprint(_document(quantity="1")) == _fingerprint(_document(quantity="1.0"))
    assert _fingerprint(_document(quantity="1")) != _fingerprint(_document(quantity="1.001"))


def test_omitted_and_null_optional_fields_share_fingerprint() -> None:
    with_omitted = DocumentInput(
        doc_type="INBOUND",
        warehouse_id=WAREHOUSE_ID,
        department_id=DEPARTMENT_ID,
        lines=(DocumentLineInput(material_id=MATERIAL_ID, quantity="1.5"),),
    )
    with_null = DocumentInput(
        doc_type="INBOUND",
        warehouse_id=WAREHOUSE_ID,
        department_id=DEPARTMENT_ID,
        lines=(
            DocumentLineInput(
                material_id=MATERIAL_ID,
                quantity="1.500",
                manufacturer_id=None,
                project_id=None,
                remark=None,
            ),
        ),
    )
    assert _fingerprint(with_omitted) == _fingerprint(with_null)


def test_line_order_changes_fingerprint() -> None:
    first = DocumentLineInput(
        material_id=MATERIAL_ID, quantity="1", manufacturer_id=MANUFACTURER_ID
    )
    second = DocumentLineInput(
        material_id=str(uuid4()), quantity="2", manufacturer_id=MANUFACTURER_ID
    )
    forward = _document(lines=(first, second))
    backward = _document(lines=(second, first))
    assert _fingerprint(forward) != _fingerprint(backward)


def test_submit_flag_participates_in_fingerprint() -> None:
    document = _document()
    assert _fingerprint(document, submit=False) != _fingerprint(document, submit=True)


def test_project_id_participates_in_fingerprint() -> None:
    assert _fingerprint(_document(project_id=None)) != _fingerprint(
        _document(project_id=PROJECT_ID)
    )


@pytest.mark.parametrize("quantity", ["0", "-1", "1.2345", "1e3", "abc", ""])
def test_invalid_quantity_is_rejected(quantity: str) -> None:
    with pytest.raises(AppError) as error:
        prepare_document(_document(quantity=quantity))
    assert error.value.status_code == 422
    assert error.value.code == "VALIDATION_ERROR"


def test_outbound_manufacturer_is_rejected() -> None:
    document = DocumentInput(
        doc_type="OUTBOUND",
        warehouse_id=WAREHOUSE_ID,
        department_id=DEPARTMENT_ID,
        lines=(
            DocumentLineInput(
                material_id=MATERIAL_ID,
                quantity="1",
                manufacturer_id=MANUFACTURER_ID,
            ),
        ),
    )
    with pytest.raises(AppError) as error:
        prepare_document(document)
    assert error.value.status_code == 422
    assert error.value.items[0]["line_no"] == 1


def test_empty_lines_are_rejected() -> None:
    document = DocumentInput(
        doc_type="INBOUND",
        warehouse_id=WAREHOUSE_ID,
        department_id=DEPARTMENT_ID,
        lines=(),
    )
    with pytest.raises(AppError) as error:
        prepare_document(document)
    assert error.value.status_code == 422
