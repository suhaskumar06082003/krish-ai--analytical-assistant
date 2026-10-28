from __future__ import annotations

import re
from dataclasses import asdict, dataclass


BLOCKED_KEYWORDS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "TRUNCATE",
    "ALTER",
    "CREATE",
    "MERGE",
    "EXEC",
    "EXECUTE",
    "GRANT",
    "REVOKE",
    "DENY",
    "DECLARE",
    "SET",
    "USE",
    "BACKUP",
    "RESTORE",
    "INTO",
    "OPENROWSET",
    "OPENQUERY",
    "OPENDATASOURCE",
    "BULK",
    "WAITFOR",
)

BLOCKED_SCHEMAS = ("Bronze", "Gold", "sys", "INFORMATION_SCHEMA")
IDENTIFIER_PATTERN = r'(?:\[[^\]]+\]|"[^"]+"|[A-Za-z_][A-Za-z0-9_@$#]*)'
TABLE_REF_PATTERN = re.compile(
    rf"\b(?:FROM|JOIN|APPLY)\s+({IDENTIFIER_PATTERN}(?:\s*\.\s*{IDENTIFIER_PATTERN}){{0,2}}|\()",
    re.IGNORECASE,
)
CTE_PATTERN = re.compile(
    rf"(?:\bWITH\b|,)\s*({IDENTIFIER_PATTERN})(?:\s*\([^)]*\))?\s+AS\s*\(",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ValidationResult:
    is_valid: bool
    sql: str | None
    errors: list[str]
    warnings: list[str]
    applied_row_limit: bool
    row_limit: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _strip_comments_and_literals(sql: str) -> str:
    chars: list[str] = []
    i = 0
    length = len(sql)
    while i < length:
        current = sql[i]
        nxt = sql[i + 1] if i + 1 < length else ""

        if current == "-" and nxt == "-":
            chars.extend("  ")
            i += 2
            while i < length and sql[i] not in "\r\n":
                chars.append(" ")
                i += 1
            continue

        if current == "/" and nxt == "*":
            chars.extend("  ")
            i += 2
            while i < length:
                if sql[i] == "*" and i + 1 < length and sql[i + 1] == "/":
                    chars.extend("  ")
                    i += 2
                    break
                chars.append("\n" if sql[i] in "\r\n" else " ")
                i += 1
            continue

        if current == "'":
            chars.append(" ")
            i += 1
            while i < length:
                if sql[i] == "'" and i + 1 < length and sql[i + 1] == "'":
                    chars.extend("  ")
                    i += 2
                    continue
                if sql[i] == "'":
                    chars.append(" ")
                    i += 1
                    break
                chars.append("\n" if sql[i] in "\r\n" else " ")
                i += 1
            continue

        chars.append(current)
        i += 1

    return "".join(chars)


def _keyword_present(sanitized_sql: str, keyword: str) -> bool:
    return re.search(rf"(?<![\[\w]){re.escape(keyword)}(?![\]\w])", sanitized_sql, re.IGNORECASE) is not None


def _normalize_identifier(identifier: str) -> str:
    value = identifier.strip()
    if value.startswith("[") and value.endswith("]"):
        return value[1:-1].replace("]]", "]")
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1].replace('""', '"')
    return value


def _normalize_table_ref(ref: str) -> list[str]:
    cleaned = ref.strip()
    if cleaned == "(":
        return ["("]
    return [_normalize_identifier(part) for part in re.split(r"\s*\.\s*", cleaned)]


def _extract_cte_names(sanitized_sql: str) -> set[str]:
    return {_normalize_identifier(match.group(1)).lower() for match in CTE_PATTERN.finditer(sanitized_sql)}


def _extract_table_refs(sanitized_sql: str) -> list[list[str]]:
    return [_normalize_table_ref(match.group(1)) for match in TABLE_REF_PATTERN.finditer(sanitized_sql)]


def _has_blocked_schema(sanitized_sql: str) -> str | None:
    unbracketed = sanitized_sql.replace("[", "").replace("]", "")
    for schema in BLOCKED_SCHEMAS:
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(schema)}\s*\.", unbracketed, re.IGNORECASE):
            return schema
    return None


def _first_keyword(sanitized_sql: str) -> str | None:
    match = re.search(r"\b([A-Za-z]+)\b", sanitized_sql.lstrip(" \t\r\n;"))
    return match.group(1).upper() if match else None


def _find_outer_select_index(sanitized_sql: str) -> int | None:
    depth = 0
    i = 0
    length = len(sanitized_sql)
    while i < length:
        char = sanitized_sql[i]
        if char == "(":
            depth += 1
            i += 1
            continue
        if char == ")":
            depth = max(0, depth - 1)
            i += 1
            continue
        if depth == 0 and sanitized_sql[i : i + 6].upper() == "SELECT":
            before = sanitized_sql[i - 1] if i > 0 else " "
            after = sanitized_sql[i + 6] if i + 6 < length else " "
            if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
                return i
        i += 1
    return None


def _already_limited(sanitized_sql: str, select_index: int) -> bool:
    tail = sanitized_sql[select_index + len("SELECT") :]
    tail = tail.lstrip()
    modifier_match = re.match(r"(DISTINCT|ALL)\b", tail, re.IGNORECASE)
    if modifier_match:
        tail = tail[modifier_match.end() :].lstrip()
    if re.match(r"TOP\b", tail, re.IGNORECASE):
        return True
    return re.search(r"\bFETCH\s+NEXT\b|\bOFFSET\s+\d+\s+ROWS\b", sanitized_sql, re.IGNORECASE) is not None


def _outer_top_value(sanitized_sql: str) -> int | None:
    select_index = _find_outer_select_index(sanitized_sql)
    if select_index is None:
        return None
    tail = sanitized_sql[select_index + len("SELECT") :].lstrip()
    modifier_match = re.match(r"(DISTINCT|ALL)\b", tail, re.IGNORECASE)
    if modifier_match:
        tail = tail[modifier_match.end() :].lstrip()
    top_match = re.match(r"TOP\s*(?:\(\s*)?(\d+)", tail, re.IGNORECASE)
    return int(top_match.group(1)) if top_match else None


def add_row_limit(sql: str, row_limit: int) -> tuple[str, bool]:
    sanitized = _strip_comments_and_literals(sql)
    select_index = _find_outer_select_index(sanitized)
    if select_index is None or _already_limited(sanitized, select_index):
        return sql, False

    insert_at = select_index + len("SELECT")
    tail = sanitized[insert_at:]
    modifier_match = re.match(r"\s+(DISTINCT|ALL)\b", tail, re.IGNORECASE)
    if modifier_match:
        insert_at += modifier_match.end()

    return sql[:insert_at] + f" TOP ({row_limit})" + sql[insert_at:], True


def validate_sql(sql: str, *, row_limit: int = 100) -> ValidationResult:
    if not sql or not sql.strip():
        return ValidationResult(False, None, ["SQL is empty."], [], False, row_limit)

    raw_sql = sql.strip()
    sanitized = _strip_comments_and_literals(raw_sql)
    statement_parts = [part for part in sanitized.split(";") if part.strip()]
    if len(statement_parts) > 1:
        return ValidationResult(False, None, ["Multiple SQL statements are not allowed."], [], False, row_limit)

    first_keyword = _first_keyword(sanitized)
    if first_keyword not in {"SELECT", "WITH"}:
        return ValidationResult(False, None, ["Only SELECT and WITH queries are allowed."], [], False, row_limit)

    for keyword in BLOCKED_KEYWORDS:
        if _keyword_present(sanitized, keyword):
            return ValidationResult(False, None, [f"Blocked SQL keyword detected: {keyword}."], [], False, row_limit)

    blocked_schema = _has_blocked_schema(sanitized)
    if blocked_schema:
        return ValidationResult(False, None, [f"Access to {blocked_schema} schema is not allowed."], [], False, row_limit)

    cte_names = _extract_cte_names(sanitized)
    for ref_parts in _extract_table_refs(sanitized):
        if ref_parts == ["("]:
            continue
        if len(ref_parts) == 1:
            if ref_parts[0].lower() in cte_names:
                continue
            return ValidationResult(
                False,
                None,
                [f"Unqualified table reference `{ref_parts[0]}` is not allowed. Use Silver.<table>."],
                [],
                False,
                row_limit,
            )
        if len(ref_parts) == 2:
            schema = ref_parts[0]
            if schema.lower() != "silver":
                return ValidationResult(
                    False,
                    None,
                    [f"Only Silver schema tables are allowed. Found `{schema}`."],
                    [],
                    False,
                    row_limit,
                )
            continue
        return ValidationResult(
            False,
            None,
            ["Three-part or cross-database table references are not allowed."],
            [],
            False,
            row_limit,
        )

    return ValidationResult(True, raw_sql, [], [], False, row_limit)


def get_outer_top_value(sql: str) -> int | None:
    return _outer_top_value(_strip_comments_and_literals(sql))


def has_outer_output_limit(sql: str) -> bool:
    sanitized = _strip_comments_and_literals(sql)
    select_index = _find_outer_select_index(sanitized)
    if select_index is None:
        return False
    return _already_limited(sanitized, select_index)


def strip_sql_for_analysis(sql: str) -> str:
    return _strip_comments_and_literals(sql)
