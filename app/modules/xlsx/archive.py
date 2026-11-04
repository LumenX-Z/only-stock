"""XLSX 压缩包安全校验与工作表原始数值读取。

依据 docs/14-安全、可靠性与性能要求.md 11.1 与 docs/07-XLSX模板、快速录入与导出.md
7.8.1、7.8.3：
- 上传按不可信文件处理，校验 ZIP 结构、解压尺寸、压缩比、外部关系与宏部件；
- 拒绝异常嵌套、路径穿越、符号链接和加密条目；
- 普通数值单元格必须从工作表 XML 的 ``<v>`` 读取原始十进制词法，禁止浮点结果。
"""

import zipfile
from dataclasses import dataclass, field
from xml.etree.ElementTree import Element, ParseError, iterparse

from openpyxl.utils.cell import column_index_from_string, coordinate_from_string

from app.modules.xlsx.constants import (
    FIRST_DATA_ROW,
    MAX_COMPRESSION_RATIO,
    MAX_DATA_ROWS,
    MAX_RAW_NUMERIC_CELLS,
    MAX_RELATIONSHIP_BYTES,
    MAX_SHEET_SCAN_ROW,
    MAX_UNCOMPRESSED_BYTES,
    MAX_ZIP_ENTRIES,
    MIN_RATIO_CHECK_BYTES,
)
from app.modules.xlsx.errors import file_error

_FORBIDDEN_PREFIXES = (
    "xl/externallinks/",
    "xl/embeddings/",
    "xl/macrosheets/",
    "xl/activex/",
    "xl/ctrlprops/",
)
_FORBIDDEN_SUFFIXES = ("vbaproject.bin",)
_EXTERNAL_MARKERS = (b'targetmode="external"', b"targetmode='external'")
_CONTENT_TYPE_MARKERS = (b"externallink", b"macroenabled", b"vbaproject")
_XML_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


class RowLimitExceeded(Exception):
    """扫描到第 2,001 个非空数据行时提前终止。"""

    def __init__(self, count: int) -> None:
        super().__init__(count)
        self.count = count


class SparseRows(Exception):
    """非空数据行位置超出允许扫描范围，视为工作表结构异常。"""


class ArchiveTooComplex(Exception):
    """原始数值单元格数量异常。"""


@dataclass(slots=True)
class SheetScan:
    """一次工作表 XML 扫描的结果。"""

    raw_numbers: dict[tuple[int, int], str] = field(default_factory=dict)
    nonempty_rows: int = 0
    max_content_row: int = 0


def validate_archive(archive: zipfile.ZipFile) -> None:
    """校验压缩包结构；任何异常均按文件级错误拒绝。"""
    infos = archive.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise file_error(
            "XLSX_ARCHIVE_UNSAFE",
            "XLSX 压缩包条目过多，已拒绝",
            items=[{"field": "file", "reason": "压缩包条目超过安全上限"}],
        )

    total_uncompressed = 0
    total_compressed = 0
    names: list[str] = []
    for info in infos:
        name = info.filename
        if not _safe_entry_name(name):
            raise file_error(
                "XLSX_ARCHIVE_UNSAFE",
                "XLSX 压缩包包含不安全的路径，已拒绝",
            )
        if info.flag_bits & 0x1:
            raise file_error("XLSX_ARCHIVE_UNSAFE", "XLSX 压缩包包含加密条目，已拒绝")
        if _is_symlink(info):
            raise file_error("XLSX_ARCHIVE_UNSAFE", "XLSX 压缩包包含符号链接，已拒绝")
        lowered = name.lower()
        if lowered.startswith(_FORBIDDEN_PREFIXES) or lowered.endswith(_FORBIDDEN_SUFFIXES):
            raise file_error(
                "XLSX_ARCHIVE_UNSAFE",
                "XLSX 包含宏、外部链接或嵌入对象，已拒绝",
            )
        total_uncompressed += int(info.file_size)
        total_compressed += int(info.compress_size)
        names.append(name)

    if len(names) != len(set(names)):
        raise file_error("XLSX_ARCHIVE_UNSAFE", "XLSX 压缩包包含重复条目，已拒绝")

    if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
        raise file_error(
            "XLSX_ARCHIVE_UNSAFE",
            "XLSX 解压后尺寸超过安全上限，已拒绝",
            items=[{"field": "file", "reason": "解压后尺寸异常"}],
        )
    if (
        total_uncompressed > MIN_RATIO_CHECK_BYTES
        and total_uncompressed / max(total_compressed, 1) > MAX_COMPRESSION_RATIO
    ):
        raise file_error(
            "XLSX_ARCHIVE_UNSAFE",
            "XLSX 压缩比异常，已拒绝",
            items=[{"field": "file", "reason": "压缩比超过安全上限"}],
        )

    try:
        corrupted = archive.testzip()
    except (RuntimeError, zipfile.BadZipFile, OSError) as exc:
        raise file_error("XLSX_INVALID_FILE", "XLSX 文件损坏，无法读取") from exc
    if corrupted is not None:
        raise file_error("XLSX_INVALID_FILE", "XLSX 文件损坏，无法读取")

    if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
        raise file_error("XLSX_INVALID_FILE", "不是有效的 XLSX 工作簿")

    _validate_relationships(archive, names)


def scan_data_sheet(
    archive: zipfile.ZipFile,
    sheet_path: str,
    *,
    expected_columns: int,
) -> SheetScan:
    """扫描“录入数据” XML，统计非空行并保存数值单元格原始词法。"""
    scan = SheetScan()
    last_counted_row = 0
    current_row = 0
    try:
        with archive.open(sheet_path) as source:
            for _, element in iterparse(source, events=("end",)):
                tag = _local_name(element.tag)
                if tag == "row":
                    row_number = _integer(element.get("r"))
                    current_row = row_number if row_number is not None else current_row + 1
                    element.clear()
                    continue
                if tag != "c":
                    continue

                row_number, column_number = _cell_position(element, current_row)
                if row_number <= 0 or column_number <= 0:
                    element.clear()
                    continue

                content, raw_value = _cell_content(element)
                if content and row_number >= FIRST_DATA_ROW:
                    if row_number != last_counted_row:
                        last_counted_row = row_number
                        scan.nonempty_rows += 1
                        if scan.nonempty_rows > MAX_DATA_ROWS:
                            raise RowLimitExceeded(scan.nonempty_rows)
                    scan.max_content_row = max(scan.max_content_row, row_number)

                if (
                    raw_value is not None
                    and row_number >= FIRST_DATA_ROW
                    and 1 <= column_number <= expected_columns
                ):
                    if len(scan.raw_numbers) >= MAX_RAW_NUMERIC_CELLS:
                        raise ArchiveTooComplex
                    scan.raw_numbers[(row_number, column_number)] = raw_value
                element.clear()
    except ParseError as exc:
        raise file_error("XLSX_INVALID_FILE", "XLSX 工作表 XML 损坏，无法读取") from exc
    except (KeyError, zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise file_error("XLSX_INVALID_FILE", "XLSX 工作表无法读取") from exc

    if scan.nonempty_rows and scan.max_content_row > MAX_SHEET_SCAN_ROW:
        raise SparseRows
    return scan


def _validate_relationships(archive: zipfile.ZipFile, names: list[str]) -> None:
    for name in names:
        lowered = name.lower()
        if lowered.endswith(".rels"):
            _read_metadata(archive, name, "XLSX 关系文件尺寸异常")
        if lowered == "[content_types].xml":
            content_types = _read_metadata(archive, name, "XLSX 内容类型文件尺寸异常")
            lowered_types = content_types.lower()
            if any(marker in lowered_types for marker in _CONTENT_TYPE_MARKERS):
                raise file_error(
                    "XLSX_ARCHIVE_UNSAFE",
                    "XLSX 包含宏或外部链接声明，已拒绝",
                )
        if lowered == "xl/workbook.xml":
            workbook_xml = _read_metadata(archive, name, "XLSX 工作簿文件尺寸异常")
            if b"externalreferences" in workbook_xml.lower():
                raise file_error("XLSX_ARCHIVE_UNSAFE", "XLSX 包含外部链接，已拒绝")

    for name in names:
        if not name.lower().endswith(".rels"):
            continue
        data = _read_metadata(archive, name, "XLSX 关系文件尺寸异常").lower()
        if any(marker in data for marker in _EXTERNAL_MARKERS):
            raise file_error("XLSX_ARCHIVE_UNSAFE", "XLSX 包含外部链接，已拒绝")


def _read_metadata(archive: zipfile.ZipFile, name: str, reason: str) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > MAX_RELATIONSHIP_BYTES:
        raise file_error(
            "XLSX_ARCHIVE_UNSAFE",
            "XLSX 元数据文件尺寸异常，已拒绝",
            items=[{"field": "file", "reason": reason}],
        )
    try:
        return archive.read(name)
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise file_error("XLSX_INVALID_FILE", "XLSX 文件损坏，无法读取") from exc


def _safe_entry_name(name: str) -> bool:
    if not name or "\x00" in name or "\\" in name or name.startswith("/"):
        return False
    stripped = name[:-1] if name.endswith("/") else name
    return all(part not in {"", ".", ".."} for part in stripped.split("/"))


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == 0o120000


def _cell_position(element: Element, current_row: int) -> tuple[int, int]:
    reference = element.get("r")
    if reference:
        try:
            column_letters, row_number = coordinate_from_string(reference)
            return row_number, column_index_from_string(column_letters)
        except (ValueError, TypeError):
            return 0, 0
    return current_row, 0


def _cell_content(element: Element) -> tuple[bool, str | None]:
    """返回（是否含内容, 数值原始词法）。公式单元格不贡献原始数值。"""
    cell_type = element.get("t")
    formula = element.find(f"{_XML_NS}f")
    value = element.find(f"{_XML_NS}v")
    raw_value = value.text if value is not None else None
    has_formula = formula is not None
    has_value = raw_value is not None and raw_value != ""

    if cell_type == "inlineStr":
        inline = element.find(f"{_XML_NS}is")
        if inline is None:
            return has_formula, None
        text = "".join(part.text or "" for part in inline.iter() if _local_name(part.tag) == "t")
        return has_formula or text != "", None

    if has_formula:
        return True, None
    if not has_value:
        return False, None
    if cell_type in (None, "n"):
        return True, str(raw_value)
    return True, None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _integer(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
