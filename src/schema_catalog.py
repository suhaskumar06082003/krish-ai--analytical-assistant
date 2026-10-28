from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .business_context import write_business_context
from .config import Settings
from .database import AzureSqlDatabase
from .metadata import inspect_silver_schema

TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")

BUSINESS_KEYWORDS: dict[str, tuple[str, ...]] = {
    "sales": ("invoice", "order", "line", "amount", "quantity", "qty", "value"),
    "product": ("product", "sku", "item", "category", "flavour", "mrp"),
    "products": ("product", "sku", "item", "category", "flavour", "mrp"),
    "sku": ("product", "sku", "item", "product_code", "product_id"),
    "item": ("product", "sku", "item", "product_code", "product_id"),
    "category": ("product", "main_category", "sub_category", "category"),
    "flavour": ("product", "flavour"),
    "flavor": ("product", "flavour"),
    "mrp": ("product", "mrp", "mrp_per_unit"),
    "primary": ("primary", "invoice", "order", "distributor", "super"),
    "secondary": ("secondary", "invoice", "order", "retailer", "distributor"),
    "retailer": ("retailer", "outlet", "customer"),
    "distributor": ("distributor", "db", "channel"),
    "salesman": ("salesman", "zsm", "rsm", "asm", "sso", "so", "psr"),
    "hierarchy": ("salesman_mapping", "zsm", "rsm", "asm", "sso", "so", "psr"),
    "beat": ("beat", "route", "visit"),
    "visit": ("visit", "task", "beat", "retailer"),
    "hub": ("hub", "zone", "region"),
    "freezer": ("freezer", "asset", "movement", "request"),
    "asset": ("asset", "freezer", "movement", "request"),
    "active": ("active", "inactive", "status", "history"),
    "inactive": ("active", "inactive", "status", "history"),
}


def write_schema_catalog(path: Path, catalog: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(catalog, indent=2, ensure_ascii=True), encoding="utf-8")


def load_schema_catalog(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": "Silver", "table_count": 0, "tables": []}
    return json.loads(path.read_text(encoding="utf-8"))


def generate_metadata_artifacts(settings: Settings) -> dict[str, Any]:
    db = AzureSqlDatabase(settings)
    catalog = inspect_silver_schema(db)
    write_schema_catalog(settings.schema_catalog_path, catalog)
    write_business_context(settings.business_context_path, catalog)
    return catalog


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in TOKEN_RE.findall(text) if token.strip()}


def _table_text(table: dict[str, Any]) -> str:
    column_names = " ".join(str(column.get("name", "")) for column in table.get("columns", []))
    return " ".join(
        [
            str(table.get("table_name", "")),
            str(table.get("description", "")),
            column_names,
        ]
    )


def _expanded_question_tokens(question: str) -> set[str]:
    tokens = _tokens(question)
    expanded = set(tokens)
    for token in tokens:
        expanded.update(BUSINESS_KEYWORDS.get(token, ()))
    return expanded


def _score_table(table: dict[str, Any], tokens: set[str]) -> tuple[int, list[str]]:
    table_name = str(table.get("table_name", ""))
    table_lower = table_name.lower()
    description = str(table.get("description", "")).lower()
    columns = [str(column.get("name", "")).lower() for column in table.get("columns", [])]

    score = 0
    reasons: list[str] = []
    for token in tokens:
        if token in table_lower:
            score += 6
            reasons.append(f"`{token}` matched table name `{table_name}`")
        if token in description:
            score += 3
            reasons.append(f"`{token}` matched table description")
        matching_columns = [column for column in columns if token in column]
        if matching_columns:
            score += min(4, len(matching_columns)) * 2
            reasons.append(f"`{token}` matched columns: {', '.join(matching_columns[:5])}")

    return score, reasons


def search_catalog(catalog: dict[str, Any], keyword: str, *, max_results: int = 10) -> dict[str, Any]:
    tokens = _expanded_question_tokens(keyword)
    matches: list[dict[str, Any]] = []
    for table in catalog.get("tables", []):
        score, reasons = _score_table(table, tokens)
        if score > 0:
            matches.append(
                {
                    "table_name": table.get("table_name"),
                    "description": table.get("description", ""),
                    "score": score,
                    "matched_columns": [
                        column.get("name")
                        for column in table.get("columns", [])
                        if any(token in str(column.get("name", "")).lower() for token in tokens)
                    ][:20],
                    "reasons": reasons[:8],
                }
            )

    matches.sort(key=lambda item: (-int(item["score"]), str(item["table_name"])))
    return {"keyword": keyword, "matches": matches[:max_results]}


def _column_names(table: dict[str, Any]) -> set[str]:
    return {str(column.get("name", "")).lower() for column in table.get("columns", [])}


def _short_table_name(table: dict[str, Any]) -> str:
    qualified = str(table.get("table_name", ""))
    return qualified.split(".", 1)[1].lower() if "." in qualified else qualified.lower()


def _wants_product_master(tokens: set[str]) -> bool:
    product_tokens = {
        "product",
        "products",
        "sku",
        "item",
        "items",
        "category",
        "main_category",
        "sub_category",
        "flavour",
        "flavor",
        "mrp",
        "mrp_per_unit",
    }
    return bool(tokens.intersection(product_tokens))


def _wants_sales_fact(tokens: set[str]) -> bool:
    sales_tokens = {"sales", "invoice", "order", "amount", "quantity", "qty", "value", "revenue"}
    return bool(tokens.intersection(sales_tokens))


def _is_line_item_table(table: dict[str, Any]) -> bool:
    return _short_table_name(table).endswith("line_items")


def _join_suggestions(selected_tables: list[dict[str, Any]]) -> list[str]:
    suggestions: list[str] = []
    for left_index, left in enumerate(selected_tables):
        left_name = str(left.get("table_name"))
        left_columns = _column_names(left)
        for right in selected_tables[left_index + 1 :]:
            right_name = str(right.get("table_name"))
            right_columns = _column_names(right)
            common = sorted(
                column
                for column in left_columns.intersection(right_columns)
                if column.endswith(("id", "code", "key")) or "_id" in column or "_code" in column
            )
            if common:
                suggestions.append(
                    f"{left_name} may join to {right_name} on shared column(s): {', '.join(common[:6])}"
                )

    names = " ".join(str(table.get("table_name", "")).lower() for table in selected_tables)
    if "salesman_mapping" in names:
        suggestions.append(
            "When joining Salesman_Mapping, use the relevant salesman code and ensure transaction dates fall between From_Date and To_Date."
        )
    if "invoice" in names and "order" in names:
        suggestions.append("For order-to-invoice analysis, inspect document number, order id, invoice id, and date columns before joining.")
    if "product_master" in names and any("line_items" in str(table.get("table_name", "")).lower() for table in selected_tables):
        suggestions.append(
            "For product enrichment, join line item `Product_Code` to `Silver.Product_Master.Product_ID`. "
            "Use `MRP_Per_Unit` by default unless the user explicitly asks for `MRP`."
        )
    if "retailer" in names and "beat" in names:
        suggestions.append("For beat analysis, inspect retailer code/id and salesman code/id mappings before joining.")
    if "asset" in names or "freezer" in names:
        suggestions.append("For asset or freezer movement, use movement/request dates to avoid treating current location as historical truth.")
    return suggestions[:10]


def suggest_tables(catalog: dict[str, Any], question: str, *, max_tables: int = 6) -> dict[str, Any]:
    tokens = _expanded_question_tokens(question)
    scored: list[tuple[int, dict[str, Any], list[str]]] = []
    for table in catalog.get("tables", []):
        score, reasons = _score_table(table, tokens)
        if score > 0:
            scored.append((score, table, reasons))

    scored.sort(key=lambda item: (-item[0], str(item[1].get("table_name"))))
    selected = scored[:max_tables]

    if _wants_product_master(tokens) and not any(_short_table_name(table) == "product_master" for _, table, _ in selected):
        product_match = next((item for item in scored if _short_table_name(item[1]) == "product_master"), None)
        if product_match:
            if len(selected) >= max_tables:
                selected = selected[: max_tables - 1] + [product_match]
            else:
                selected.append(product_match)

    if _wants_sales_fact(tokens):
        line_index = next((index for index, (_, table, _) in enumerate(selected) if _is_line_item_table(table)), None)
        if line_index is not None and line_index > 0:
            selected = [selected[line_index], *selected[:line_index], *selected[line_index + 1 :]]

    selected_tables = [table for _, table, _ in selected]

    return {
        "question": question,
        "tables": [
            {
                "table_name": table.get("table_name"),
                "description": table.get("description", ""),
                "score": score,
                "date_columns": table.get("date_columns", []),
                "primary_keys": table.get("primary_keys", []),
            }
            for score, table, _ in selected
        ],
        "reasoning": [
            f"{table.get('table_name')}: " + "; ".join(reasons[:5])
            for _, table, reasons in selected
        ],
        "join_suggestions": _join_suggestions(selected_tables),
    }


def get_table_from_catalog(catalog: dict[str, Any], table_name: str) -> dict[str, Any] | None:
    requested = table_name.replace("[", "").replace("]", "").lower()
    if requested.startswith("silver."):
        requested = requested.split(".", 1)[1]
    for table in catalog.get("tables", []):
        qualified = str(table.get("table_name", ""))
        short = qualified.split(".", 1)[1] if "." in qualified else qualified
        if short.lower() == requested or qualified.lower() == requested:
            return table
    return None
