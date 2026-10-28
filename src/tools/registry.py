from __future__ import annotations

import logging
import time
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from mcp.server.fastmcp import FastMCP

from ..business_context import filter_business_context, read_business_context, write_business_context
from ..config import Settings, clamp_limit
from ..database import AzureSqlDatabase
from ..metadata import describe_silver_table, describe_table_indexes
from ..output_guard import assess_output_size
from ..schema_catalog import (
    generate_metadata_artifacts,
    load_schema_catalog,
    search_catalog,
    suggest_tables,
)
from ..sql_validator import validate_sql as validate_sql_text
from .sql_generation import generate_sql_from_question

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def _logged_tool(name: str) -> Callable[[F], F]:
    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            logger.info("tool_call name=%s", name)
            try:
                result = func(*args, **kwargs)
                elapsed_ms = (time.perf_counter() - start) * 1000
                logger.info("tool_call_complete name=%s duration_ms=%.2f", name, elapsed_ms)
                return result
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - start) * 1000
                logger.exception("tool_call_error name=%s duration_ms=%.2f error=%s", name, elapsed_ms, exc)
                raise

        return wrapper  # type: ignore[return-value]

    return decorator


def _load_catalog(settings: Settings, *, refresh: bool = False) -> dict[str, Any]:
    if refresh or not settings.schema_catalog_path.exists():
        return generate_metadata_artifacts(settings)
    return load_schema_catalog(settings.schema_catalog_path)


def _error(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


def register_tools(mcp: FastMCP, settings: Settings) -> None:
    @mcp.tool()
    @_logged_tool("get_business_context")
    def get_business_context(topic: str = "") -> dict[str, Any]:
        """Return business concepts from BUSINESS_CONTEXT.md, optionally filtered by a topic."""
        if not settings.business_context_path.exists():
            catalog = load_schema_catalog(settings.schema_catalog_path)
            write_business_context(settings.business_context_path, catalog)
        content = read_business_context(settings.business_context_path)
        return {"ok": True, "content": filter_business_context(content, topic)}

    @mcp.tool()
    @_logged_tool("refresh_metadata")
    def refresh_metadata() -> dict[str, Any]:
        """Regenerate BUSINESS_CONTEXT.md and SCHEMA_CATALOG.json from live Silver schema metadata."""
        catalog = generate_metadata_artifacts(settings)
        return {
            "ok": True,
            "table_count": catalog.get("table_count", 0),
            "business_context_path": str(settings.business_context_path),
            "schema_catalog_path": str(settings.schema_catalog_path),
        }

    @mcp.tool()
    @_logged_tool("list_silver_tables")
    def list_silver_tables(refresh: bool = False) -> dict[str, Any]:
        """Return all discovered Silver schema tables without loading all column metadata into the response."""
        try:
            catalog = _load_catalog(settings, refresh=refresh)
        except Exception as exc:
            return _error(str(exc))
        tables = [table.get("table_name") for table in catalog.get("tables", [])]
        return {
            "ok": True,
            "table_count": len(tables),
            "tables": tables,
            "generated_at_utc": catalog.get("generated_at_utc"),
        }

    @mcp.tool()
    @_logged_tool("describe_table")
    def describe_table(table_name: str, sample_rows: int | None = None) -> dict[str, Any]:
        """Return columns, data types, nullability, primary keys, row estimate, and sample rows for one Silver table."""
        requested_sample = settings.sample_row_limit if sample_rows is None else sample_rows
        safe_sample = max(1, min(requested_sample, settings.sample_row_limit))
        try:
            db = AzureSqlDatabase(settings)
            return {"ok": True, "table": describe_silver_table(db, table_name, sample_limit=safe_sample)}
        except Exception as exc:
            return _error(str(exc))

    @mcp.tool()
    @_logged_tool("get_table_indexes")
    def get_table_indexes(table_name: str) -> dict[str, Any]:
        """Return all indexes for one Silver table: index name, type, key columns, and included columns.

        Use this before building live artifacts, dashboards, or repeated/interactive queries so that
        filters (WHERE), joins, GROUP BY, and ORDER BY target indexed columns. Large fact tables use
        clustered columnstore indexes (efficient for aggregations and scans); master and mapping tables
        use rowstore indexes on their key/code columns.
        """
        try:
            db = AzureSqlDatabase(settings)
            return {"ok": True, **describe_table_indexes(db, table_name)}
        except Exception as exc:
            return _error(str(exc))

    @mcp.tool()
    @_logged_tool("search_schema")
    def search_schema(keyword: str, max_results: int = 10, refresh: bool = False) -> dict[str, Any]:
        """Search Silver table names, column names, and descriptions by keyword."""
        try:
            catalog = _load_catalog(settings, refresh=refresh)
        except Exception as exc:
            return _error(str(exc))
        return {"ok": True, **search_catalog(catalog, keyword, max_results=max(1, min(max_results, 25)))}

    @mcp.tool()
    @_logged_tool("suggest_tables_for_question")
    def suggest_tables_for_question(question: str, max_tables: int = 6, refresh: bool = False) -> dict[str, Any]:
        """Suggest relevant Silver tables, reasoning, and likely join hints for a natural language question."""
        try:
            catalog = _load_catalog(settings, refresh=refresh)
        except Exception as exc:
            return _error(str(exc))
        return {"ok": True, **suggest_tables(catalog, question, max_tables=max(1, min(max_tables, 10)))}

    @mcp.tool()
    @_logged_tool("generate_sql")
    def generate_sql(question: str, row_limit: int | None = None, refresh: bool = False) -> dict[str, Any]:
        """Generate a conservative safe SQL Server SELECT query from a natural language question."""
        safe_limit = clamp_limit(row_limit, settings)
        try:
            catalog = _load_catalog(settings, refresh=refresh)
        except Exception as exc:
            return _error(str(exc))
        return {"ok": True, **generate_sql_from_question(question, catalog, row_limit=safe_limit)}

    @mcp.tool()
    @_logged_tool("validate_sql")
    def validate_sql(sql: str, row_limit: int | None = None) -> dict[str, Any]:
        """Validate SQL safety. This checks permissions and SQL shape, not output size."""
        safe_limit = clamp_limit(row_limit, settings)
        result = validate_sql_text(sql, row_limit=safe_limit)
        if not result.is_valid:
            logger.warning("validation_failure errors=%s", result.errors)
        return {"ok": result.is_valid, **result.to_dict()}

    @mcp.tool()
    @_logged_tool("estimate_sql_output")
    def estimate_sql_output(sql: str, row_limit: int | None = None, refresh: bool = False) -> dict[str, Any]:
        """Estimate result rows, columns, and token pressure before executing SQL."""
        safe_limit = clamp_limit(row_limit, settings)
        validation = validate_sql_text(sql, row_limit=safe_limit)
        if not validation.is_valid or validation.sql is None:
            logger.warning("validation_failure errors=%s", validation.errors)
            return {"ok": False, "validation": validation.to_dict()}

        try:
            catalog = _load_catalog(settings, refresh=refresh)
            assessment = assess_output_size(
                AzureSqlDatabase(settings),
                validation.sql,
                settings,
                catalog,
                max_response_rows=safe_limit,
            )
        except Exception as exc:
            return _error(str(exc))

        return {
            "ok": assessment.can_execute,
            "validation": validation.to_dict(),
            "output_assessment": assessment.to_dict(),
        }

    @mcp.tool()
    @_logged_tool("run_readonly_sql")
    def run_readonly_sql(sql: str, row_limit: int | None = None) -> dict[str, Any]:
        """Execute validated read-only SQL after estimating output size."""
        safe_limit = clamp_limit(row_limit, settings)
        validation = validate_sql_text(sql, row_limit=safe_limit)
        if not validation.is_valid or validation.sql is None:
            logger.warning("validation_failure errors=%s", validation.errors)
            return {"ok": False, "validation": validation.to_dict()}

        db = AzureSqlDatabase(settings)
        try:
            catalog = _load_catalog(settings)
            assessment = assess_output_size(
                db,
                validation.sql,
                settings,
                catalog,
                max_response_rows=safe_limit,
            )
        except Exception as exc:
            return _error(str(exc))

        if not assessment.can_execute:
            logger.warning("output_size_blocked errors=%s", assessment.errors)
            return {
                "ok": False,
                "validation": validation.to_dict(),
                "output_assessment": assessment.to_dict(),
                "message": (
                    "The SQL is safe for Azure SQL, but output rows could not be estimated before execution. "
                    "Use aggregation, filters, fewer columns, or TOP N ranking before running it."
                ),
            }

        result = db.execute(validation.sql)
        logger.info(
            "readonly_query_complete duration_ms=%.2f returned_rows=%s",
            result.execution_time_ms,
            result.row_count,
        )
        return {
            "ok": True,
            "sql": validation.sql,
            "validation": validation.to_dict(),
            "output_assessment": assessment.to_dict(),
            "columns": result.columns,
            "data": result.rows,
            "row_count": result.row_count,
            "execution_time_ms": result.execution_time_ms,
        }
