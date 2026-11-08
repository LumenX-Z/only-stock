"""XLSX 解析、名称消歧与错误分级单元测试。

依据 docs/07-XLSX模板、快速录入与导出.md 7.8.1–7.8.4 与
docs/16-验收标准.md ACC-XLSX-002–ACC-XLSX-006、ACC-XLSX-013。
"""

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.errors import AppError
from app.modules.masterdata.service import create_entity, set_entity_active
from app.modules.xlsx.archive import SheetScan
from app.modules.xlsx.parser import parse_document_entry
from app.modules.xlsx.templates import ExportRow, build_document_export
from tests.conftest import MasterDataFixture
from tests.xlsx_support import edit_workbook, filled_workbook


def _parse(
    tmp_path: Path,
    doc_type: str,
    data: bytes,
    session: Session,
):
    path = tmp_path / f"{uuid4().hex}.xlsx"
    path.write_bytes(data)
    return parse_document_entry(path, doc_type=doc_type, session=session)


def _inbound_rows(master: MasterDataFixture) -> list[list[Any]]:
    return [
        [
            master.project.name,
            master.category.name,
            master.category_child.name,
            master.material.name,
            "1.250",
            master.manufacturer.name,
            "采购批次 A",
        ],
        [
            master.project_second.name,
            master.category.name,
            None,
            master.material_second.name,
            "2",
            master.manufacturer.name,
            None,
        ],
    ]


def test_xlsx_parse_inbound_fills_header_and_lines(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    data = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=_inbound_rows(master_data),
    )
    with session_factory() as session:
        parsed = _parse(tmp_path, "INBOUND", data, session)

    assert parsed.status == "SUCCESS"
    assert parsed.total_rows == 2
    assert len(parsed.lines) == 2
    assert parsed.errors == ()
    assert parsed.warehouse_id == master_data.warehouse.id
    assert parsed.department_id == master_data.department.id
    first, second = parsed.lines
    assert first.material_id == master_data.material.id
    assert first.project_id == master_data.project.id
    assert first.remark == "采购批次 A"
    assert first.parent_category_name == master_data.category.name
    assert second.material_id == master_data.material_second.id
    assert second.project_id == master_data.project_second.id
    assert second.parent_category_name is None
    assert second.quantity == "2.000"


def test_xlsx_parse_project_and_remark_are_per_line(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    rows = [
        [
            master_data.project.name,
            master_data.category.name,
            master_data.category_child.name,
            master_data.material.name,
            "1",
            master_data.manufacturer.name,
            "第一行",
        ],
        [
            None,
            master_data.category.name,
            master_data.category_child.name,
            master_data.material.name,
            "2",
            master_data.manufacturer.name,
            "第二行",
        ],
    ]
    data = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=rows,
    )
    with session_factory() as session:
        parsed = _parse(tmp_path, "INBOUND", data, session)

    assert parsed.status == "SUCCESS"
    assert parsed.lines[0].project_id == master_data.project.id
    assert parsed.lines[1].project_id is None
    assert parsed.lines[0].remark == "第一行"
    assert parsed.lines[1].remark == "第二行"


def test_xlsx_parse_duplicate_material_project_is_row_error(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    base = [
        master_data.project.name,
        master_data.category.name,
        master_data.category_child.name,
        master_data.material.name,
        "1",
        master_data.manufacturer.name,
        "第一次",
    ]
    duplicate = [*base]
    duplicate[-1] = "备注不同仍是重复"
    rows = [
        base,
        duplicate,
        [
            master_data.project_second.name,
            master_data.category.name,
            master_data.category_child.name,
            master_data.material.name,
            "2",
            master_data.manufacturer.name,
            None,
        ],
    ]
    data = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=rows,
    )
    with session_factory() as session:
        parsed = _parse(tmp_path, "INBOUND", data, session)

    assert parsed.status == "PARTIAL"
    assert parsed.total_rows == 3
    assert len(parsed.lines) == 2
    assert len(parsed.errors) == 1
    assert parsed.errors[0].source_row_no == 5
    assert parsed.errors[0].error_code == "ROW_DUPLICATE"
    assert parsed.notice == "部分明细未填入，请核对后再保存或提交"


def test_xlsx_parse_cross_level_same_names_are_disambiguated(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    first_level = create_entity(
        session_factory,
        "category",
        name="一级甲",
        actor_user_id=master_data.actor.id,
        request_id=None,
    )
    second_level = create_entity(
        session_factory,
        "category",
        name="一级乙",
        actor_user_id=master_data.actor.id,
        request_id=None,
    )
    first_child = create_entity(
        session_factory,
        "category",
        name="同名二级",
        actor_user_id=master_data.actor.id,
        request_id=None,
        parent_id=first_level.id,
    )
    second_child = create_entity(
        session_factory,
        "category",
        name="同名二级",
        actor_user_id=master_data.actor.id,
        request_id=None,
        parent_id=second_level.id,
    )
    first_material = create_entity(
        session_factory,
        "material",
        name="同名物资",
        actor_user_id=master_data.actor.id,
        request_id=None,
        category_id=first_child.id,
        unit="个",
        quantity_type="DECIMAL",
    )
    second_material = create_entity(
        session_factory,
        "material",
        name="同名物资",
        actor_user_id=master_data.actor.id,
        request_id=None,
        category_id=second_child.id,
        unit="个",
        quantity_type="DECIMAL",
    )

    rows = [
        [
            None,
            "一级甲",
            "同名二级",
            "同名物资",
            "1",
            master_data.manufacturer.name,
            None,
        ],
        [
            None,
            "一级乙",
            "同名二级",
            "同名物资",
            "1",
            master_data.manufacturer.name,
            None,
        ],
    ]
    data = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=rows,
    )
    with session_factory() as session:
        parsed = _parse(tmp_path, "INBOUND", data, session)

    assert parsed.status == "SUCCESS"
    assert parsed.lines[0].material_id == first_material.id
    assert parsed.lines[1].material_id == second_material.id
    assert parsed.lines[0].parent_category_name == "一级甲"
    assert parsed.lines[1].parent_category_name == "一级乙"


def test_xlsx_parse_trims_names_but_does_not_fold_case(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    good = filled_workbook(
        "INBOUND",
        warehouse_name=f"  {master_data.warehouse.name}  ",
        department_name=master_data.department.name,
        rows=[
            [
                f"  {master_data.project.name}  ",
                master_data.category.name,
                master_data.category_child.name,
                f"  {master_data.material.name}  ",
                "1",
                master_data.manufacturer.name,
                None,
            ]
        ],
    )
    with session_factory() as session:
        parsed = _parse(tmp_path, "INBOUND", good, session)
    assert parsed.status == "SUCCESS"
    assert parsed.lines[0].material_id == master_data.material.id

    create_entity(
        session_factory,
        "project",
        name="CaseProject",
        actor_user_id=master_data.actor.id,
        request_id=None,
    )
    bad = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=[
            [
                "caseproject",
                master_data.category.name,
                master_data.category_child.name,
                master_data.material.name,
                "1",
                master_data.manufacturer.name,
                None,
            ]
        ],
    )
    with session_factory() as session:
        with pytest.raises(AppError) as raised:
            _parse(tmp_path, "INBOUND", bad, session)
    assert raised.value.code == "XLSX_NO_VALID_ROWS"


def test_xlsx_parse_unknown_names_are_row_errors(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    rows = [
        [
            "不存在的项目",
            master_data.category.name,
            None,
            master_data.material_second.name,
            "1",
            master_data.manufacturer.name,
            None,
        ],
        [None, "不存在的分类", None, "不存在的物资", "1", master_data.manufacturer.name, None],
        [
            None,
            master_data.category.name,
            None,
            master_data.material_second.name,
            "1",
            "不存在的厂家",
            None,
        ],
        [
            None,
            master_data.category.name,
            None,
            master_data.material_second.name,
            "2",
            master_data.manufacturer.name,
            None,
        ],
    ]
    data = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=rows,
    )
    with session_factory() as session:
        parsed = _parse(tmp_path, "INBOUND", data, session)

    assert parsed.status == "PARTIAL"
    assert len(parsed.lines) == 1
    codes = {error.error_code for error in parsed.errors}
    assert "PROJECT_NOT_FOUND" in codes
    assert "CATEGORY_NOT_FOUND" in codes
    assert "MANUFACTURER_NOT_FOUND" in codes
    assert parsed.notice == "部分明细未填入，请核对后再保存或提交"


def test_xlsx_parse_inactive_project_is_row_error(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    set_entity_active(
        session_factory,
        "project",
        master_data.project.id,
        active=False,
        actor_user_id=master_data.actor.id,
        request_id=None,
    )
    data = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=[
            [
                master_data.project.name,
                master_data.category.name,
                master_data.category_child.name,
                master_data.material.name,
                "1",
                master_data.manufacturer.name,
                None,
            ],
            [
                master_data.project_second.name,
                master_data.category.name,
                None,
                master_data.material_second.name,
                "1",
                master_data.manufacturer.name,
                None,
            ],
        ],
    )
    with session_factory() as session:
        parsed = _parse(tmp_path, "INBOUND", data, session)

    assert parsed.status == "PARTIAL"
    assert len(parsed.lines) == 1
    assert parsed.errors[0].source_row_no == 4
    assert parsed.errors[0].error_code == "PROJECT_INACTIVE"


def test_xlsx_parse_inactive_inbound_material_is_row_error(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    set_entity_active(
        session_factory,
        "material",
        master_data.material.id,
        active=False,
        actor_user_id=master_data.actor.id,
        request_id=None,
    )
    data = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=[
            [
                None,
                master_data.category.name,
                master_data.category_child.name,
                master_data.material.name,
                "1",
                master_data.manufacturer.name,
                None,
            ],
            [
                None,
                master_data.category.name,
                None,
                master_data.material_second.name,
                "1",
                master_data.manufacturer.name,
                None,
            ],
        ],
    )
    with session_factory() as session:
        parsed = _parse(tmp_path, "INBOUND", data, session)

    assert parsed.status == "PARTIAL"
    assert len(parsed.lines) == 1
    assert parsed.errors[0].error_code == "MATERIAL_INACTIVE"


def test_xlsx_parse_outbound_inactive_material_is_not_row_error(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    set_entity_active(
        session_factory,
        "material",
        master_data.material.id,
        active=False,
        actor_user_id=master_data.actor.id,
        request_id=None,
    )
    data = filled_workbook(
        "OUTBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=[
            [
                master_data.project.name,
                master_data.category.name,
                master_data.category_child.name,
                master_data.material.name,
                "1",
                "出库备注",
            ]
        ],
    )
    with session_factory() as session:
        parsed = _parse(tmp_path, "OUTBOUND", data, session)

    assert parsed.status == "SUCCESS"
    assert parsed.lines[0].manufacturer_id is None
    assert parsed.lines[0].remark == "出库备注"


def test_xlsx_parse_integer_material_rejects_fraction(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    data = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=[
            [
                None,
                master_data.category.name,
                master_data.category_child.name,
                master_data.material_integer.name,
                "1.5",
                master_data.manufacturer.name,
                None,
            ]
        ],
    )
    with session_factory() as session:
        with pytest.raises(AppError) as raised:
            _parse(tmp_path, "INBOUND", data, session)
    assert raised.value.code == "XLSX_NO_VALID_ROWS"


def test_xlsx_parse_uses_raw_xml_lexical_for_numeric_cells(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    rows = [
        [
            master_data.project.name,
            master_data.category.name,
            master_data.category_child.name,
            master_data.material.name,
            0.1,
            master_data.manufacturer.name,
            None,
        ],
        [
            master_data.project.name,
            master_data.category.name,
            None,
            master_data.material_second.name,
            1.234,
            master_data.manufacturer.name,
            None,
        ],
        [
            master_data.project.name,
            master_data.category.name,
            master_data.category_child.name,
            master_data.material.name,
            1.2345,
            master_data.manufacturer.name,
            None,
        ],
        [
            master_data.project_second.name,
            master_data.category.name,
            None,
            master_data.material_second.name,
            "1.250",
            master_data.manufacturer.name,
            None,
        ],
    ]
    data = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=rows,
    )
    with session_factory() as session:
        parsed = _parse(tmp_path, "INBOUND", data, session)

    assert parsed.status == "PARTIAL"
    assert len(parsed.lines) == 3
    assert parsed.lines[0].quantity == "0.100"
    assert parsed.lines[1].quantity == "1.234"
    assert parsed.lines[2].quantity == "1.250"
    assert len(parsed.errors) == 1
    assert parsed.errors[0].source_row_no == 6
    assert parsed.errors[0].error_code in {"QUANTITY_PRECISION", "QUANTITY_INVALID"}


def test_xlsx_parse_quantity_extremes_keep_raw_precision(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    rows = [
        [
            None,
            master_data.category.name,
            master_data.category_child.name,
            master_data.material.name,
            "999999999999999.999",
            master_data.manufacturer.name,
            None,
        ],
        [
            master_data.project.name,
            master_data.category.name,
            master_data.category_child.name,
            master_data.material.name,
            0.001,
            master_data.manufacturer.name,
            None,
        ],
    ]
    data = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=rows,
    )
    with session_factory() as session:
        parsed = _parse(tmp_path, "INBOUND", data, session)

    assert parsed.status == "SUCCESS"
    assert parsed.lines[0].quantity == "999999999999999.999"
    assert parsed.lines[1].quantity == "0.001"


def test_xlsx_parse_formula_cells_are_row_errors(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    rows = [
        [
            None,
            master_data.category.name,
            master_data.category_child.name,
            master_data.material.name,
            "=1+1",
            master_data.manufacturer.name,
            None,
        ],
        [
            None,
            master_data.category.name,
            master_data.category_child.name,
            master_data.material.name,
            "1",
            master_data.manufacturer.name,
            "=CONCAT(A1)",
        ],
        [
            "=A1",
            master_data.category.name,
            master_data.category_child.name,
            master_data.material.name,
            "1",
            master_data.manufacturer.name,
            None,
        ],
        [
            None,
            master_data.category.name,
            master_data.category_child.name,
            master_data.material.name,
            "1",
            master_data.manufacturer.name,
            "正常",
        ],
    ]
    data = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=rows,
    )
    with session_factory() as session:
        parsed = _parse(tmp_path, "INBOUND", data, session)

    assert parsed.status == "PARTIAL"
    assert len(parsed.lines) == 1
    assert {error.error_code for error in parsed.errors} == {"FORMULA_NOT_ALLOWED"}
    assert [error.source_row_no for error in parsed.errors] == [4, 5, 6]


def test_xlsx_parse_remark_too_long_is_row_error(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    data = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=[
            [
                None,
                master_data.category.name,
                master_data.category_child.name,
                master_data.material.name,
                "1",
                master_data.manufacturer.name,
                "超" * 501,
            ],
            [
                None,
                master_data.category.name,
                master_data.category_child.name,
                master_data.material.name,
                "1",
                master_data.manufacturer.name,
                "正常",
            ],
        ],
    )
    with session_factory() as session:
        parsed = _parse(tmp_path, "INBOUND", data, session)

    assert parsed.status == "PARTIAL"
    assert len(parsed.lines) == 1
    assert parsed.errors[0].error_code == "REMARK_TOO_LONG"


def test_xlsx_parse_raw_value_missing_is_row_error(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.modules.xlsx.parser.scan_data_sheet",
        lambda *args, **kwargs: SheetScan(),
    )
    data = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=[
            [
                None,
                master_data.category.name,
                master_data.category_child.name,
                master_data.material.name,
                1.5,
                master_data.manufacturer.name,
                None,
            ]
        ],
    )
    with session_factory() as session:
        with pytest.raises(AppError) as raised:
            _parse(tmp_path, "INBOUND", data, session)
    assert raised.value.code == "XLSX_NO_VALID_ROWS"


def test_xlsx_parse_blank_rows_are_not_counted(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    data = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=[
            [
                None,
                master_data.category.name,
                master_data.category_child.name,
                master_data.material.name,
                "1",
                master_data.manufacturer.name,
                None,
            ],
            [None, None, None, None, None, None, None],
            [
                None,
                master_data.category.name,
                None,
                master_data.material_second.name,
                "1",
                master_data.manufacturer.name,
                None,
            ],
        ],
    )
    with session_factory() as session:
        parsed = _parse(tmp_path, "INBOUND", data, session)

    assert parsed.total_rows == 2
    assert len(parsed.lines) == 2


def test_xlsx_parse_hidden_rows_are_still_validated(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    data = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=[
            [
                None,
                master_data.category.name,
                master_data.category_child.name,
                "不存在的物资",
                "1",
                master_data.manufacturer.name,
                None,
            ],
            [
                None,
                master_data.category.name,
                master_data.category_child.name,
                master_data.material.name,
                "1",
                master_data.manufacturer.name,
                None,
            ],
        ],
    )

    def hide(workbook) -> None:
        workbook["录入数据"].row_dimensions[4].hidden = True

    with session_factory() as session:
        parsed = _parse(tmp_path, "INBOUND", edit_workbook(data, hide), session)

    assert parsed.status == "PARTIAL"
    assert parsed.errors[0].source_row_no == 4
    assert parsed.errors[0].error_code == "MATERIAL_NOT_FOUND"


def test_xlsx_parse_all_failed_rows_is_file_level(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    data = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=[
            [None, "不存在", None, "不存在", "1", master_data.manufacturer.name, None],
        ],
    )
    with session_factory() as session:
        with pytest.raises(AppError) as raised:
            _parse(tmp_path, "INBOUND", data, session)
    assert raised.value.code == "XLSX_NO_VALID_ROWS"
    assert raised.value.status_code == 422


def test_xlsx_parse_empty_file_is_file_level(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    data = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=[],
    )
    with session_factory() as session:
        with pytest.raises(AppError) as raised:
            _parse(tmp_path, "INBOUND", data, session)
    assert raised.value.code == "XLSX_NO_VALID_ROWS"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (lambda wb: setattr(wb["录入数据"]["A2"], "value", "错误标签"), "XLSX_HEADER_INVALID"),
        (lambda wb: setattr(wb["录入数据"]["E2"], "value", "错误标签"), "XLSX_HEADER_INVALID"),
        (lambda wb: setattr(wb["录入数据"]["B2"], "value", "=1+1"), "XLSX_HEADER_INVALID"),
        (lambda wb: setattr(wb["录入数据"]["B2"], "value", None), "WAREHOUSE_REQUIRED"),
        (lambda wb: setattr(wb["录入数据"]["B2"], "value", "不存在仓库"), "WAREHOUSE_NOT_FOUND"),
        (lambda wb: setattr(wb["录入数据"]["F2"], "value", "不存在部门"), "DEPARTMENT_NOT_FOUND"),
        (lambda wb: setattr(wb["录入数据"]["D3"], "value", "错误列"), "XLSX_COLUMNS_INVALID"),
        (lambda wb: setattr(wb["录入数据"]["H3"], "value", "多余列"), "XLSX_COLUMNS_INVALID"),
    ],
)
def test_xlsx_parse_header_and_column_errors_are_file_level(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
    mutation,
    expected_code: str,
) -> None:
    data = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=_inbound_rows(master_data),
    )
    with session_factory() as session:
        with pytest.raises(AppError) as raised:
            _parse(tmp_path, "INBOUND", edit_workbook(data, mutation), session)
    assert raised.value.code == expected_code
    assert raised.value.status_code == 422


def test_xlsx_parse_inactive_warehouse_is_file_level(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    set_entity_active(
        session_factory,
        "warehouse",
        master_data.warehouse.id,
        active=False,
        actor_user_id=master_data.actor.id,
        request_id=None,
    )
    data = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=_inbound_rows(master_data),
    )
    with session_factory() as session:
        with pytest.raises(AppError) as raised:
            _parse(tmp_path, "INBOUND", data, session)
    assert raised.value.code == "WAREHOUSE_INACTIVE"


def test_xlsx_parse_template_versions_and_sheets(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    wrong_version = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        version="INBOUND_V2",
        rows=_inbound_rows(master_data),
    )
    with session_factory() as session:
        with pytest.raises(AppError) as raised:
            _parse(tmp_path, "INBOUND", wrong_version, session)
    assert raised.value.code == "XLSX_TEMPLATE_UNSUPPORTED"

    outbound = filled_workbook(
        "OUTBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=[],
    )
    with session_factory() as session:
        with pytest.raises(AppError) as raised:
            _parse(tmp_path, "INBOUND", outbound, session)
    assert raised.value.code == "XLSX_TEMPLATE_MISMATCH"

    def add_extra(workbook) -> None:
        workbook.create_sheet("其他")

    extra = edit_workbook(
        filled_workbook(
            "INBOUND",
            warehouse_name=master_data.warehouse.name,
            department_name=master_data.department.name,
            rows=_inbound_rows(master_data),
        ),
        add_extra,
    )
    with session_factory() as session:
        with pytest.raises(AppError) as raised:
            _parse(tmp_path, "INBOUND", extra, session)
    assert raised.value.code == "XLSX_WORKSHEET_INVALID"

    def remove_help(workbook) -> None:
        workbook.remove(workbook["填写说明"])

    missing = edit_workbook(
        filled_workbook(
            "INBOUND",
            warehouse_name=master_data.warehouse.name,
            department_name=master_data.department.name,
            rows=_inbound_rows(master_data),
        ),
        remove_help,
    )
    with session_factory() as session:
        with pytest.raises(AppError) as raised:
            _parse(tmp_path, "INBOUND", missing, session)
    assert raised.value.code == "XLSX_WORKSHEET_INVALID"


def test_xlsx_parse_outbound_manufacturer_column_is_file_level(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    data = filled_workbook(
        "OUTBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=[],
    )

    def add_manufacturer(workbook) -> None:
        workbook["录入数据"]["E3"] = "厂家名称"

    with session_factory() as session:
        with pytest.raises(AppError) as raised:
            _parse(tmp_path, "OUTBOUND", edit_workbook(data, add_manufacturer), session)
    assert raised.value.code == "XLSX_MANUFACTURER_COLUMN_FORBIDDEN"


def test_xlsx_parse_does_not_change_master_data_or_documents(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    data = filled_workbook(
        "INBOUND",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=_inbound_rows(master_data),
    )
    before = _counts(session_factory)
    with session_factory() as session:
        _parse(tmp_path, "INBOUND", data, session)
        _parse(tmp_path, "INBOUND", data, session)
    assert _counts(session_factory) == before


def test_xlsx_parse_export_ignores_responsible_row_and_unit_value(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    data = build_document_export(
        doc_type="INBOUND",
        doc_no="RK20261105-000001",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=[
            ExportRow(
                project_name=master_data.project.name,
                level1_name=master_data.category.name,
                level2_name=master_data.category_child.name,
                material_name=master_data.material.name,
                quantity="1.250",
                unit=master_data.material.unit,
                manufacturer_name=master_data.manufacturer.name,
                remark="导出后再次解析",
            )
        ],
        info_rows=[("正式单号", "RK20261105-000001")],
    )

    def tamper_unit(workbook) -> None:
        workbook["录入数据"]["E4"] = "篡改单位"

    data = edit_workbook(data, tamper_unit)
    with session_factory() as session:
        parsed = _parse(tmp_path, "INBOUND", data, session)

    assert parsed.status == "SUCCESS"
    assert parsed.total_rows == 1
    assert len(parsed.lines) == 1
    assert parsed.lines[0].remark == "导出后再次解析"
    assert parsed.lines[0].unit == master_data.material.unit


def test_xlsx_parse_outbound_export_ignores_unit_column(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
) -> None:
    data = build_document_export(
        doc_type="OUTBOUND",
        doc_no="CK20261105-000001",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=[
            ExportRow(
                project_name=None,
                level1_name=master_data.category.name,
                level2_name=master_data.category_child.name,
                material_name=master_data.material.name,
                quantity="1.000",
                unit=master_data.material.unit,
                manufacturer_name=None,
                remark=None,
            )
        ],
        info_rows=[],
    )
    with session_factory() as session:
        parsed = _parse(tmp_path, "OUTBOUND", data, session)

    assert parsed.status == "SUCCESS"
    assert len(parsed.lines) == 1
    assert parsed.lines[0].material_id == master_data.material.id
    assert parsed.lines[0].unit == master_data.material.unit


@pytest.mark.parametrize("doc_type", ["INBOUND", "OUTBOUND"])
def test_xlsx_parse_accepts_legacy_export_positions(
    tmp_path: Path,
    session_factory: sessionmaker[Session],
    master_data: MasterDataFixture,
    doc_type: str,
) -> None:
    inbound = doc_type == "INBOUND"
    data = build_document_export(
        doc_type=doc_type,
        doc_no="RK20261105-000001" if inbound else "CK20261105-000001",
        warehouse_name=master_data.warehouse.name,
        department_name=master_data.department.name,
        rows=[
            ExportRow(
                project_name=None,
                level1_name=master_data.category.name,
                level2_name=master_data.category_child.name,
                material_name=master_data.material.name,
                quantity="1.000",
                unit=master_data.material.unit,
                manufacturer_name=master_data.manufacturer.name if inbound else None,
                remark=None,
            )
        ],
        info_rows=[],
    )
    new_label, new_value = ("G2", "H2") if inbound else ("F2", "G2")

    def move_to_legacy_positions(workbook) -> None:
        sheet = workbook["录入数据"]
        label = sheet[new_label].value
        value = sheet[new_value].value
        sheet[new_label] = None
        sheet[new_value] = None
        sheet["E2"] = label
        sheet["F2"] = value
        sheet["F6"] = sheet["G6"].value
        sheet["G6"] = None

    data = edit_workbook(data, move_to_legacy_positions)
    with session_factory() as session:
        parsed = _parse(tmp_path, doc_type, data, session)

    assert parsed.status == "SUCCESS"
    assert parsed.department_id == master_data.department.id
    assert len(parsed.lines) == 1
    assert parsed.lines[0].unit == master_data.material.unit


def _counts(session_factory: sessionmaker[Session]) -> dict[str, int]:
    from sqlalchemy import func, select

    from app.modules.audit.models import AuditLog
    from app.modules.categories.models import Category
    from app.modules.documents.models import StockDocument, StockDocumentLine
    from app.modules.materials.models import Material

    with session_factory() as session:
        return {
            "documents": int(session.scalar(select(func.count()).select_from(StockDocument)) or 0),
            "lines": int(session.scalar(select(func.count()).select_from(StockDocumentLine)) or 0),
            "audit": int(session.scalar(select(func.count()).select_from(AuditLog)) or 0),
            "categories": int(session.scalar(select(func.count()).select_from(Category)) or 0),
            "materials": int(session.scalar(select(func.count()).select_from(Material)) or 0),
        }
