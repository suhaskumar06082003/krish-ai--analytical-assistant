from __future__ import annotations

import re
from typing import Any

from ..database import quote_identifier
from ..schema_catalog import get_table_from_catalog, suggest_tables
from ..sql_validator import validate_sql

NUMERIC_TYPES = (
    "bigint",
    "int",
    "smallint",
    "tinyint",
    "decimal",
    "numeric",
    "float",
    "real",
    "money",
    "smallmoney",
)

AGGREGATION_WORDS = {
    "sum",
    "total",
    "sales",
    "amount",
    "value",
    "quantity",
    "qty",
    "revenue",
    "count",
}

METRIC_HINTS = (
    "net",
    "gross",
    "amount",
    "value",
    "total",
    "sales",
    "quantity",
    "qty",
    "volume",
    "discount",
)

DISPLAY_HINTS = (
    "date",
    "code",
    "id",
    "name",
    "status",
    "hub",
    "beat",
    "retailer",
    "distributor",
    "salesman",
    "zsm",
    "rsm",
    "asm",
    "sso",
    "so",
    "psr",
)

PLAIN_MRP_PHRASES = (
    "just mrp",
    "only mrp",
    "plain mrp",
    "pack mrp",
    "total mrp",
    "mrp column",
    "mrp field",
    "use mrp",
)

BOTH_MRP_PHRASES = (
    "both mrp",
    "mrp and mrp per unit",
    "mrp per unit and mrp",
    "mrp and mrp_per_unit",
    "mrp_per_unit and mrp",
)

PRODUCT_ENRICHMENT_TOKENS = {
    "product",
    "products",
    "sku",
    "item",
    "items",
    "category",
    "main",
    "main_category",
    "sub",
    "sub_category",
    "flavour",
    "flavor",
    "mrp",
    "mrp_per_unit",
}


def _question_tokens(question: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[A-Za-z0-9_]+", question)}


def _short_table_name(table: dict[str, Any]) -> str:
    qualified = str(table.get("table_name", ""))
    return qualified.split(".", 1)[1] if "." in qualified else qualified


def _qualified_table(table: dict[str, Any]) -> str:
    return f"{quote_identifier('Silver')}.{quote_identifier(_short_table_name(table))}"


def _qualified_column(alias: str, column: str) -> str:
    return f"{alias}.{quote_identifier(column)}"


def _is_product_master(table: dict[str, Any]) -> bool:
    return _short_table_name(table).lower() == "product_master"


def _is_line_item_table(table: dict[str, Any]) -> bool:
    table_name = _short_table_name(table).lower()
    column_names = {str(column.get("name", "")).lower() for column in table.get("columns", [])}
    return table_name.endswith("line_items") and "product_code" in column_names


def _wants_product_enrichment(tokens: set[str]) -> bool:
    return bool(tokens.intersection(PRODUCT_ENRICHMENT_TOKENS))


def _wants_plain_mrp(question: str) -> bool:
    raw_lower = question.lower()
    lower = raw_lower.replace("_", " ")
    if "mrp per unit" in lower or "mrp_per_unit" in raw_lower:
        return False
    return any(phrase in lower or phrase in raw_lower for phrase in PLAIN_MRP_PHRASES)


def _wants_both_mrp(question: str) -> bool:
    raw_lower = question.lower()
    lower = raw_lower.replace("_", " ")
    return any(phrase in lower or phrase in raw_lower for phrase in BOTH_MRP_PHRASES)


def _mentions_mrp(tokens: set[str]) -> bool:
    return "mrp" in tokens or "mrp_per_unit" in tokens


def _column_exists(table: dict[str, Any], column_name: str) -> bool:
    return any(str(column.get("name", "")).lower() == column_name.lower() for column in table.get("columns", []))


def _find_numeric_column(table: dict[str, Any], column_name: str) -> str | None:
    for column in table.get("columns", []):
        if str(column.get("name", "")).lower() == column_name.lower() and _is_numeric(column):
            return str(column["name"])
    return None


def _skip_product_mrp_column(table: dict[str, Any], column: str, question: str, tokens: set[str]) -> bool:
    if not _is_product_master(table) or not _mentions_mrp(tokens) or _wants_both_mrp(question):
        return False

    lower_column = column.lower()
    if lower_column == "mrp":
        return not _wants_plain_mrp(question)
    if lower_column == "mrp_per_unit":
        return _wants_plain_mrp(question)
    return False


def _is_numeric(column: dict[str, Any]) -> bool:
    data_type = str(column.get("data_type", "")).lower().split("(", 1)[0]
    return data_type in NUMERIC_TYPES


def _choose_date_column(table: dict[str, Any]) -> str | None:
    date_columns = [str(column) for column in table.get("date_columns", [])]
    preferred = ("invoice", "order", "created", "modified", "from", "to", "date")
    for hint in preferred:
        for column in date_columns:
            if hint in column.lower():
                return column
    return date_columns[0] if date_columns else None


def _choose_metric_column(table: dict[str, Any], question: str) -> str | None:
    numeric_columns = [column for column in table.get("columns", []) if _is_numeric(column)]
    tokens = _question_tokens(question)
    if _is_product_master(table) and _mentions_mrp(tokens):
        preferred_mrp = "MRP" if _wants_plain_mrp(question) and not _wants_both_mrp(question) else "MRP_Per_Unit"
        mrp_column = _find_numeric_column(table, preferred_mrp)
        if mrp_column:
            return mrp_column

    for hint in METRIC_HINTS:
        for column in numeric_columns:
            if hint in str(column.get("name", "")).lower():
                return str(column["name"])
    return str(numeric_columns[0]["name"]) if numeric_columns else None


def _choose_display_columns(table: dict[str, Any], question: str, *, max_columns: int = 12) -> list[str]:
    tokens = _question_tokens(question)
    columns = [str(column.get("name")) for column in table.get("columns", []) if column.get("name")]
    selected: list[str] = []

    for column in columns:
        lower = column.lower()
        if _skip_product_mrp_column(table, column, question, tokens):
            continue
        if any(token in lower for token in tokens) and column not in selected:
            selected.append(column)

    for hint in DISPLAY_HINTS:
        for column in columns:
            if _skip_product_mrp_column(table, column, question, tokens):
                continue
            if hint in column.lower() and column not in selected:
                selected.append(column)
            if len(selected) >= max_columns:
                return selected

    return selected[:max_columns] or columns[:max_columns]


def _date_filter(question: str, date_column: str | None, *, qualifier: str = "") -> tuple[str | None, list[str]]:
    if not date_column:
        return None, ["No date column was identified, so no date filter was applied."]

    lower = question.lower()
    col = _qualified_column(qualifier, date_column) if qualifier else quote_identifier(date_column)
    if "last month" in lower:
        return (
            f"{col} >= DATEADD(month, DATEDIFF(month, 0, GETDATE()) - 1, 0) "
            f"AND {col} < DATEADD(month, DATEDIFF(month, 0, GETDATE()), 0)",
            [f"Used `{date_column}` for last-month filtering."],
        )
    if "this month" in lower or "current month" in lower:
        return (
            f"{col} >= DATEADD(month, DATEDIFF(month, 0, GETDATE()), 0) "
            f"AND {col} < DATEADD(month, DATEDIFF(month, 0, GETDATE()) + 1, 0)",
            [f"Used `{date_column}` for current-month filtering."],
        )
    if "today" in lower:
        return (
            f"{col} >= CONVERT(date, GETDATE()) AND {col} < DATEADD(day, 1, CONVERT(date, GETDATE()))",
            [f"Used `{date_column}` for today filtering."],
        )
    if "yesterday" in lower:
        return (
            f"{col} >= DATEADD(day, -1, CONVERT(date, GETDATE())) AND {col} < CONVERT(date, GETDATE())",
            [f"Used `{date_column}` for yesterday filtering."],
        )
    days_match = re.search(r"last\s+(\d{1,3})\s+days", lower)
    if days_match:
        days = min(int(days_match.group(1)), 365)
        return (
            f"{col} >= DATEADD(day, -{days}, GETDATE())",
            [f"Used `{date_column}` for last-{days}-days filtering."],
        )
    if "last year" in lower:
        return (
            f"{col} >= DATEADD(year, DATEDIFF(year, 0, GETDATE()) - 1, 0) "
            f"AND {col} < DATEADD(year, DATEDIFF(year, 0, GETDATE()), 0)",
            [f"Used `{date_column}` for last-year filtering."],
        )
    return None, []


def _product_group_columns(product_table: dict[str, Any], question: str) -> list[str]:
    tokens = _question_tokens(question)
    selected: list[str] = []

    def add(column: str) -> None:
        if _column_exists(product_table, column) and column not in selected:
            selected.append(column)

    if tokens.intersection({"category", "main", "main_category"}):
        add("Main_Category")
    if tokens.intersection({"sub", "sub_category"}):
        add("Sub_Category")
    if tokens.intersection({"flavour", "flavor"}):
        add("Flavour")
    if "product" in tokens and ("name" in tokens or "wise" in tokens):
        add("Product_Name")
    if tokens.intersection({"sku", "item", "items"}):
        add("Product_ID")
    if _mentions_mrp(tokens):
        if _wants_both_mrp(question):
            add("MRP")
            add("MRP_Per_Unit")
        elif _wants_plain_mrp(question):
            add("MRP")
        else:
            add("MRP_Per_Unit")

    if not selected:
        add("Product_Name")
    return selected


def _product_enriched_aggregate_sql(
    question: str,
    fact_table: dict[str, Any],
    product_table: dict[str, Any],
    *,
    row_limit: int,
) -> tuple[str | None, list[str]]:
    date_column = _choose_date_column(fact_table)
    metric_column = _choose_metric_column(fact_table, question)
    if not metric_column:
        return None, []

    date_clause, assumptions = _date_filter(question, date_column, qualifier="li")
    group_columns = _product_group_columns(product_table, question)
    group_select = ", ".join(_qualified_column("pm", column) for column in group_columns)
    group_by = ", ".join(_qualified_column("pm", column) for column in group_columns)
    metric = _qualified_column("li", metric_column)
    metric_alias = quote_identifier("Total_" + metric_column)
    where_clause = f"\nWHERE {date_clause}" if date_clause else ""
    sql = (
        f"SELECT TOP ({row_limit}) {group_select}, "
        f"SUM(TRY_CONVERT(decimal(18, 2), {metric})) AS {metric_alias}\n"
        f"FROM {_qualified_table(fact_table)} AS li\n"
        f"INNER JOIN {_qualified_table(product_table)} AS pm ON li.[Product_Code] = pm.[Product_ID]"
        f"{where_clause}\n"
        f"GROUP BY {group_by}\n"
        f"ORDER BY {metric_alias} DESC"
    )
    assumptions.append(
        "Joined line item `Product_Code` to `Silver.Product_Master.Product_ID` for product attributes. "
        "Used `MRP_Per_Unit` unless plain `MRP` was explicitly requested."
    )
    return sql, assumptions


def generate_sql_from_question(question: str, catalog: dict[str, Any], *, row_limit: int) -> dict[str, Any]:
    suggestions = suggest_tables(catalog, question, max_tables=4)
    if not suggestions["tables"]:
        return {
            "question": question,
            "sql": None,
            "is_valid": False,
            "tables": [],
            "reasoning": ["No relevant Silver tables were found in SCHEMA_CATALOG.json."],
            "join_suggestions": [],
            "validation": {"is_valid": False, "errors": ["No relevant table found."]},
        }

    first_table_name = str(suggestions["tables"][0]["table_name"])
    table = get_table_from_catalog(catalog, first_table_name)
    if table is None:
        return {
            "question": question,
            "sql": None,
            "is_valid": False,
            "tables": suggestions["tables"],
            "reasoning": ["Suggested table was not found in the loaded catalog."],
            "join_suggestions": suggestions["join_suggestions"],
            "validation": {"is_valid": False, "errors": ["Suggested table missing from catalog."]},
        }

    date_column = _choose_date_column(table)
    metric_column = _choose_metric_column(table, question)
    date_clause, assumptions = _date_filter(question, date_column)
    tokens = _question_tokens(question)
    wants_aggregation = bool(tokens.intersection(AGGREGATION_WORDS))
    from_clause = f"FROM {_qualified_table(table)}"
    where_clause = f"\nWHERE {date_clause}" if date_clause else ""

    product_table = get_table_from_catalog(catalog, "Product_Master")
    if (
        wants_aggregation
        and product_table
        and _is_line_item_table(table)
        and _wants_product_enrichment(tokens)
    ):
        enriched_sql, enriched_assumptions = _product_enriched_aggregate_sql(
            question,
            table,
            product_table,
            row_limit=row_limit,
        )
        if enriched_sql:
            sql = enriched_sql
            assumptions = enriched_assumptions
        else:
            sql = ""
    else:
        sql = ""

    if sql:
        pass
    elif wants_aggregation and metric_column:
        metric = quote_identifier(metric_column)
        if date_column:
            date_expr = f"CONVERT(date, {quote_identifier(date_column)})"
            sql = (
                f"SELECT TOP ({row_limit}) {date_expr} AS {quote_identifier(date_column + '_Date')}, "
                f"SUM(TRY_CONVERT(decimal(18, 2), {metric})) AS {quote_identifier('Total_' + metric_column)}\n"
                f"{from_clause}"
                f"{where_clause}\n"
                f"GROUP BY {date_expr}\n"
                f"ORDER BY {date_expr} DESC"
            )
        else:
            sql = (
                f"SELECT TOP ({row_limit}) "
                f"SUM(TRY_CONVERT(decimal(18, 2), {metric})) AS {quote_identifier('Total_' + metric_column)}\n"
                f"{from_clause}"
                f"{where_clause}"
            )
    else:
        selected_columns = ", ".join(quote_identifier(column) for column in _choose_display_columns(table, question))
        order_clause = f"\nORDER BY {quote_identifier(date_column)} DESC" if date_column else ""
        sql = f"SELECT TOP ({row_limit}) {selected_columns}\n{from_clause}{where_clause}{order_clause}"

    if ("active" in tokens or "inactive" in tokens) and not any("active" in item.lower() or "status" in item.lower() for item in assumptions):
        assumptions.append("Active/inactive values were not guessed. Inspect status columns and sample rows before adding that filter.")

    validation = validate_sql(sql, row_limit=row_limit)
    return {
        "question": question,
        "sql": validation.sql,
        "is_valid": validation.is_valid,
        "tables": suggestions["tables"],
        "reasoning": suggestions["reasoning"],
        "join_suggestions": suggestions["join_suggestions"],
        "assumptions": assumptions,
        "validation": validation.to_dict(),
    }
