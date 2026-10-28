from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from typing import Any

from .config import Settings
from .database import AzureSqlDatabase
from .schema_catalog import get_table_from_catalog
from .sql_validator import get_outer_top_value, has_outer_output_limit, strip_sql_for_analysis

logger = logging.getLogger(__name__)

AGGREGATE_RE = re.compile(r"\b(COUNT_BIG|COUNT|SUM|AVG|MIN|MAX)\s*\(", re.IGNORECASE)
GROUP_BY_RE = re.compile(r"\bGROUP\s+BY\b", re.IGNORECASE)
ORDER_BY_RE = re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)
WHERE_RE = re.compile(r"\bWHERE\b", re.IGNORECASE)
SELECT_STAR_RE = re.compile(r"\bSELECT\b[\s\S]{0,200}?(?:\*|[A-Za-z_][A-Za-z0-9_]*\.\*)", re.IGNORECASE)
DATE_FILTER_RE = re.compile(
    r"\bWHERE\b[\s\S]*\b(date|time|month|year|created|modified|updated|from_date|to_date)\b",
    re.IGNORECASE,
)
TABLE_REF_RE = re.compile(
    r"\b(?:FROM|JOIN|APPLY)\s+(?:\[Silver\]|Silver)\s*\.\s*(?:\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_@$#]*))",
    re.IGNORECASE,
)

TRANSACTION_TABLE_HINTS = (
    "invoice",
    "order",
    "line",
    "visit",
    "task",
    "sale",
    "sales",
    "request",
    "freezer",
    "asset",
)


@dataclass(frozen=True)
class OutputAssessment:
    can_execute: bool
    estimated_rows: int | None
    estimated_columns: int | None
    estimated_cells: int | None
    max_response_rows: int
    max_response_columns: int
    max_response_cells: int
    query_shape: dict[str, Any]
    warnings: list[str]
    errors: list[str]
    suggestions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_aggregate(sql: str) -> bool:
    return AGGREGATE_RE.search(strip_sql_for_analysis(sql)) is not None


def _has_group_by(sql: str) -> bool:
    return GROUP_BY_RE.search(strip_sql_for_analysis(sql)) is not None


def _has_where(sql: str) -> bool:
    return WHERE_RE.search(strip_sql_for_analysis(sql)) is not None


def _has_date_filter(sql: str) -> bool:
    return DATE_FILTER_RE.search(strip_sql_for_analysis(sql)) is not None


def _is_select_star(sql: str) -> bool:
    return SELECT_STAR_RE.search(strip_sql_for_analysis(sql)) is not None


def _referenced_tables(sql: str) -> list[str]:
    tables: list[str] = []
    for match in TABLE_REF_RE.finditer(strip_sql_for_analysis(sql)):
        table_name = match.group(1) or match.group(2)
        if table_name and table_name not in tables:
            tables.append(table_name)
    return tables


def _is_transaction_table(table_name: str) -> bool:
    lower = table_name.lower()
    return any(hint in lower for hint in TRANSACTION_TABLE_HINTS)


def _catalog_row_estimate(catalog: dict[str, Any], table_name: str) -> int | None:
    table = get_table_from_catalog(catalog, table_name)
    if not table:
        return None
    value = table.get("row_count_estimate")
    return int(value) if isinstance(value, int) else None


def _catalog_column_estimate(catalog: dict[str, Any], referenced_tables: list[str]) -> int | None:
    if not referenced_tables:
        return None
    total = 0
    found = False
    for table_name in referenced_tables:
        table = get_table_from_catalog(catalog, table_name)
        if table:
            found = True
            total += len(table.get("columns", []))
    return total if found else None


def _strip_trailing_semicolon(sql: str) -> str:
    return sql.strip().rstrip(";").strip()


def _starts_with_cte(sql: str) -> bool:
    return strip_sql_for_analysis(sql).lstrip().upper().startswith("WITH")


def _outer_order_by_index(sql: str) -> int | None:
    sanitized = strip_sql_for_analysis(sql)
    depth = 0
    order_by_index: int | None = None
    i = 0
    while i < len(sanitized):
        char = sanitized[i]
        if char == "(":
            depth += 1
            i += 1
            continue
        if char == ")":
            depth = max(0, depth - 1)
            i += 1
            continue
        if depth == 0:
            match = ORDER_BY_RE.match(sanitized, i)
            if match:
                order_by_index = i
                i = match.end()
                continue
        i += 1
    return order_by_index


def _strip_outer_order_by_when_needed(sql: str) -> str:
    cleaned = _strip_trailing_semicolon(sql)
    if has_outer_output_limit(cleaned):
        return cleaned
    order_index = _outer_order_by_index(cleaned)
    return cleaned[:order_index].strip() if order_index is not None else cleaned


def _describe_columns(db: AzureSqlDatabase, sql: str) -> list[dict[str, Any]]:
    try:
        result = db.execute(
            """
            SELECT name, system_type_name
            FROM sys.dm_exec_describe_first_result_set(?, NULL, 0)
            WHERE error_number IS NULL AND is_hidden = 0
            ORDER BY column_ordinal
            """,
            (_strip_trailing_semicolon(sql),),
        )
        return [
            {"name": row.get("name"), "data_type": row.get("system_type_name")}
            for row in result.rows
            if row.get("name")
        ]
    except Exception as exc:
        logger.warning("result_column_description_failed error=%s", exc)
        return []


def _exact_output_count(db: AzureSqlDatabase, sql: str) -> int | None:
    if _starts_with_cte(sql):
        return None
    countable_sql = _strip_outer_order_by_when_needed(sql)
    try:
        result = db.execute(f"SELECT COUNT_BIG(1) AS estimated_rows FROM ({countable_sql}) AS mcp_output_count")
        if result.rows:
            return int(result.rows[0]["estimated_rows"])
    except Exception as exc:
        logger.warning("exact_output_count_failed error=%s", exc)
    return None


def _suggestions(query_shape: dict[str, Any]) -> list[str]:
    suggestions = [
        "Return summaries instead of raw rows when possible.",
        "Add a date filter for invoice, order, visit, and other transaction tables.",
        "Select only the columns needed by the answer.",
    ]
    if not query_shape["is_aggregate"]:
        suggestions.append("Use SUM, COUNT, AVG, MIN, MAX, GROUP BY, or a TOP N ranking query.")
    if not query_shape["has_output_limit"]:
        suggestions.append("Add TOP N or OFFSET/FETCH when a ranked raw result is genuinely needed.")
    return suggestions


def assess_output_size(
    db: AzureSqlDatabase,
    sql: str,
    settings: Settings,
    catalog: dict[str, Any],
    *,
    max_response_rows: int,
) -> OutputAssessment:
    referenced_tables = _referenced_tables(sql)
    aggregate = _is_aggregate(sql)
    group_by = _has_group_by(sql)
    select_star = _is_select_star(sql)
    has_date_filter = _has_date_filter(sql)
    has_output_limit = has_outer_output_limit(sql)
    top_value = get_outer_top_value(sql)
    transaction_tables = [table for table in referenced_tables if _is_transaction_table(table)]
    catalog_rows = [
        value
        for table in referenced_tables
        if (value := _catalog_row_estimate(catalog, table)) is not None
    ]

    columns = _describe_columns(db, sql)
    estimated_columns = len(columns) if columns else None
    if estimated_columns is None and select_star:
        estimated_columns = _catalog_column_estimate(catalog, referenced_tables)

    estimated_rows: int | None = None
    warnings: list[str] = []
    errors: list[str] = []

    if top_value is not None:
        estimated_rows = top_value
    elif aggregate and not group_by:
        estimated_rows = 1
    elif settings.enable_exact_output_count:
        estimated_rows = _exact_output_count(db, sql)

    if estimated_rows is None and catalog_rows and not aggregate and not group_by:
        estimated_rows = max(catalog_rows)

    estimated_cells = (
        estimated_rows * estimated_columns
        if estimated_rows is not None and estimated_columns is not None
        else None
    )

    if transaction_tables and not has_date_filter:
        warnings.append(
            "Transaction-table query has no obvious date filter. Large database scans are allowed, "
            "but raw AI responses should stay narrow."
        )
    if select_star:
        warnings.append(
            "SELECT * is allowed, but it can return unnecessary columns. Confirm the estimated row count before execution."
        )
    if estimated_rows is None:
        errors.append(
            "Could not estimate output rows before execution. Narrow the query with aggregation, date filters, or TOP N."
        )
    if estimated_columns is None:
        warnings.append("Could not estimate output columns before execution.")

    if estimated_rows is not None and estimated_rows > max_response_rows:
        warnings.append(
            f"Estimated output rows ({estimated_rows}) exceed the configured default row limit ({max_response_rows}), "
            "but response row limits are disabled."
        )
    if estimated_columns is not None and estimated_columns > settings.max_response_columns:
        warnings.append(
            f"Estimated output columns ({estimated_columns}) exceed the configured column guideline "
            f"({settings.max_response_columns}), but response column limits are disabled."
        )
    if estimated_cells is not None and estimated_cells > settings.max_response_cells:
        warnings.append(
            f"Estimated output cells ({estimated_cells}) exceed the configured cell guideline "
            f"({settings.max_response_cells}), but response cell limits are disabled."
        )

    query_shape = {
        "is_aggregate": aggregate,
        "has_group_by": group_by,
        "is_select_star": select_star,
        "has_where": _has_where(sql),
        "has_date_filter": has_date_filter,
        "has_output_limit": has_output_limit,
        "top_value": top_value,
        "referenced_tables": [f"Silver.{table}" for table in referenced_tables],
        "transaction_tables": [f"Silver.{table}" for table in transaction_tables],
    }

    return OutputAssessment(
        can_execute=not errors,
        estimated_rows=estimated_rows,
        estimated_columns=estimated_columns,
        estimated_cells=estimated_cells,
        max_response_rows=max_response_rows,
        max_response_columns=settings.max_response_columns,
        max_response_cells=settings.max_response_cells,
        query_shape=query_shape,
        warnings=warnings,
        errors=errors,
        suggestions=_suggestions(query_shape),
    )
