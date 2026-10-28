from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import Settings
from src.database import AzureSqlDatabase, quote_silver_table
from src.output_guard import assess_output_size
from src.schema_catalog import generate_metadata_artifacts
from src.server import mcp
from src.sql_validator import validate_sql


async def _tool_names() -> list[str]:
    tools = await mcp.list_tools()
    return sorted(tool.name for tool in tools)


def _first_table(catalog: dict) -> str:
    tables = catalog.get("tables", [])
    if not tables:
        raise RuntimeError("No Silver tables were discovered.")
    qualified = tables[0]["table_name"]
    return qualified.split(".", 1)[1] if "." in qualified else qualified


def main() -> None:
    settings = Settings.from_env()

    tool_names = asyncio.run(_tool_names())
    print("tools:", ", ".join(tool_names))

    catalog = generate_metadata_artifacts(settings)
    print("metadata_table_count:", catalog.get("table_count", 0))
    print("business_context_generated:", settings.business_context_path.exists())
    print("schema_catalog_generated:", settings.schema_catalog_path.exists())

    unsafe = validate_sql("DELETE FROM Silver.SomeTable", row_limit=settings.default_row_limit)
    print("unsafe_sql_blocked:", not unsafe.is_valid)

    table_name = _first_table(catalog)
    safe = validate_sql(f"SELECT COUNT_BIG(1) AS Row_Count FROM {quote_silver_table(table_name)}", row_limit=1)
    if not safe.is_valid or safe.sql is None:
        raise RuntimeError(f"Safe SQL failed validation: {safe.errors}")

    db = AzureSqlDatabase(settings)
    assessment = assess_output_size(
        db,
        safe.sql,
        settings,
        catalog,
        max_response_rows=1,
    )
    if not assessment.can_execute:
        raise RuntimeError(f"Safe SQL failed output assessment: {assessment.errors}")
    print("output_estimation_ok:", True)
    result = db.execute(safe.sql, fetch_limit=1)
    print("safe_sql_executed:", True)
    print("safe_sql_returned_rows:", result.row_count)


if __name__ == "__main__":
    main()
