"""单据请求指纹。

依据 docs/08-MCP服务与工具.md 7.9 第 6 条与 docs/16-验收标准.md ACC-DATA-030：
- 仅纳入 ``doc_type``、``warehouse_id``、``department_id``、``submit``
  以及每行 ``material_id``、``project_id``、``manufacturer_id``、``quantity_milli``、``remark``；
- 排除 ``request_id`` 与协议原始字段名；
- 对象键按字典序排列，省略值与默认值归一为同一值；
- 数量统一使用千分比整数，明细顺序参与比较；
- 结果为 UTF-8 规范化 JSON 的 SHA-256 十六进制字符串。
"""

import hashlib
import json

from app.modules.documents.inputs import PreparedDocument


def compute_request_fingerprint(document: PreparedDocument, *, submit: bool = False) -> str:
    """计算并返回 64 位十六进制 SHA-256 指纹。"""
    payload = {
        "doc_type": document.doc_type,
        "warehouse_id": document.warehouse_id,
        "department_id": document.department_id,
        "submit": bool(submit),
        "lines": [
            {
                "material_id": line.material_id,
                "project_id": line.project_id,
                "manufacturer_id": line.manufacturer_id,
                "quantity_milli": line.quantity_milli,
                "remark": line.remark,
            }
            for line in document.lines
        ],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
