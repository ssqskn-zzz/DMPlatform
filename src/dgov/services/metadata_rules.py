"""Deterministic metadata enrichment rules.

These rules are intentionally transparent and versionable: every derived value
(layer, domain, sensitivity) can be explained to a data steward and later moved
into a configurable standard without changing the ingestion pipeline.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

LAYER_PREFIXES = (
    ("ods_", "ODS"),
    ("dwd_", "DWD"),
    ("dws_", "DWS"),
    ("ads_", "ADS"),
    ("dim_", "DIM"),
    ("stg_", "STG"),
    ("tmp_", "TMP"),
)
GRAIN_SUFFIXES = (
    ("_di", "日增量"),
    ("_df", "日全量"),
    ("_hi", "小时增量"),
    ("_hf", "小时全量"),
    ("_mi", "月增量"),
    ("_mf", "月全量"),
)
DOMAIN_KEYWORDS = (
    ("财务", ("财务", "资金", "fund", "finance", "fin_", "ledger", "voucher", "account")),
    ("人力", ("人力", "员工", "hr", "employee", "salary", "payroll", "组织")),
    ("审计", ("审计", "audit", "compliance", "合规")),
    ("投资", ("投资", "基金", "invest", "portfolio", "project", "项目")),
    ("风控", ("风控", "risk", "credit", "信用", "担保", "抵押")),
)


def layer_for(table_name: str) -> str:
    lowered = table_name.lower()
    for prefix, layer in LAYER_PREFIXES:
        if lowered.startswith(prefix):
            return layer
    return "OTHER"


def grain_for(table_name: str) -> str | None:
    lowered = table_name.lower()
    for suffix, grain in GRAIN_SUFFIXES:
        if lowered.endswith(suffix):
            return grain
    return None


def domain_for(*texts: str | None) -> str:
    haystack = " ".join(text for text in texts if text).lower()
    for domain, keywords in DOMAIN_KEYWORDS:
        if any(keyword in haystack for keyword in keywords):
            return domain
    return "通用"


def type_family(data_type: str | None) -> str:
    value = (data_type or "").lower()
    if any(token in value for token in ("char", "string", "text", "varchar")):
        return "string"
    if any(token in value for token in ("timestamp", "datetime", "date", "time")):
        return "datetime"
    if any(token in value for token in ("decimal", "numeric", "double", "float")):
        return "decimal"
    if any(token in value for token in ("bigint", "int", "smallint", "tinyint")):
        return "integer"
    if any(token in value for token in ("bool",)):
        return "boolean"
    if any(token in value for token in ("array", "map", "struct", "json")):
        return "complex"
    return "other"


SENSITIVE_KEYWORDS: tuple[tuple[str, str, str], ...] = (
    ("L4", "凭证信息", ("password", "passwd", "pwd", "secret", "token", "私钥")),
    ("L4", "个人信息", ("idcard", "id_card", "identity_card", "身份证", "护照", "passport")),
    ("L4", "金融信息", ("bank_card", "card_no", "银行卡", "salary", "wage", "薪资", "工资")),
    ("L3", "联系信息", ("mobile", "phone", "telephone", "手机", "电话", "email", "邮箱", "address", "地址")),
    ("L3", "个人信息", ("birthday", "birth", "生日", "性别", "gender", "姓名")),
    ("L3", "金融信息", ("amount", "balance", "credit", "金额", "余额", "授信", "净资产", "估值")),
    ("L2", "业务标识", ("customer_id", "cust_id", "客户号", "证件号", "统一社会信用代码")),
)


def classify_column(name: str, comment: str | None) -> tuple[str, str]:
    """Return ``(sensitive_level, data_category)`` for a column."""

    lowered = name.lower()
    text = f"{lowered} {comment or ''}"
    for level, category, keywords in SENSITIVE_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return level, category
    if lowered == "name" or lowered.endswith("_name"):
        return "L2", "业务名称"
    return "L1", "其他"


def stable_hash(values: dict[str, Any]) -> str:
    serialized = json.dumps(values, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:40]


def parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.replace(tzinfo=None)
    except ValueError:
        pass
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[: len(pattern) + 2], pattern)
        except ValueError:
            continue
    return None


def dataset_comment_coverage(has_comment: int, total: int) -> float:
    return round(has_comment / total * 100, 2) if total else 100.0
