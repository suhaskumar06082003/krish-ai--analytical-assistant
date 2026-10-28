from __future__ import annotations

import datetime as dt
import decimal
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueryExecution:
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    execution_time_ms: float


def quote_identifier(identifier: str) -> str:
    return "[" + identifier.replace("]", "]]") + "]"


def quote_silver_table(table_name: str) -> str:
    return f"{quote_identifier('Silver')}.{quote_identifier(table_name)}"


def _json_safe(value: Any) -> Any:
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


class AzureSqlDatabase:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _connection_string(self) -> str:
        self.settings.require_database_config()
        server = self.settings.sql_server
        if not server.lower().startswith("tcp:"):
            server = f"tcp:{server}"
        if "," not in server:
            server = f"{server},{self.settings.sql_port}"
        parts = {
            "DRIVER": "{" + self.settings.sql_driver + "}",
            "SERVER": server,
            "DATABASE": self.settings.sql_database,
            "UID": self.settings.sql_username,
            "PWD": self.settings.sql_password,
            "Encrypt": self.settings.sql_encrypt,
            "TrustServerCertificate": self.settings.sql_trust_server_certificate,
            "Connection Timeout": str(self.settings.sql_connection_timeout),
            "ApplicationIntent": "ReadOnly",
        }
        return ";".join(f"{key}={value}" for key, value in parts.items())

    def connect(self):
        try:
            import pyodbc
        except ImportError as exc:
            raise RuntimeError("pyodbc is required. Install dependencies from requirements.txt.") from exc

        connection_string = self._connection_string()
        attempts = max(1, self.settings.sql_connection_retries)
        retry_delay_seconds = max(0, self.settings.sql_connection_retry_delay_seconds)

        for attempt in range(1, attempts + 1):
            try:
                return pyodbc.connect(connection_string, autocommit=True)
            except pyodbc.Error as exc:
                if attempt >= attempts:
                    raise
                delay_seconds = retry_delay_seconds * attempt
                logger.warning(
                    "azure_sql_connect_failed attempt=%s/%s retry_in_seconds=%s error=%s",
                    attempt,
                    attempts,
                    delay_seconds,
                    exc,
                )
                if delay_seconds:
                    time.sleep(delay_seconds)

        raise RuntimeError("Azure SQL connection failed without returning an error.")

    def execute(
        self,
        sql: str,
        params: Sequence[Any] | None = None,
        *,
        timeout: int | None = None,
        fetch_limit: int | None = None,
    ) -> QueryExecution:
        start = time.perf_counter()
        connection = self.connect()
        query_timeout = timeout or self.settings.sql_query_timeout
        try:
            connection.timeout = query_timeout
        except Exception:
            logger.debug("pyodbc connection timeout attribute is not available")
        cursor = connection.cursor()
        try:
            try:
                cursor.timeout = query_timeout
            except Exception:
                logger.debug("pyodbc cursor timeout attribute is not available")
            logger.debug("Executing SQL query")
            cursor.execute(sql, tuple(params or ()))
            if cursor.description is None:
                raise RuntimeError("SQL did not return a result set.")

            columns = [column[0] for column in cursor.description]
            records = cursor.fetchmany(fetch_limit) if fetch_limit else cursor.fetchall()
            rows = [
                {column: _json_safe(value) for column, value in zip(columns, record)}
                for record in records
            ]
            elapsed_ms = (time.perf_counter() - start) * 1000
            return QueryExecution(
                columns=columns,
                rows=rows,
                row_count=len(rows),
                execution_time_ms=round(elapsed_ms, 2),
            )
        finally:
            try:
                cursor.close()
            finally:
                connection.close()

    def scalar(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        result = self.execute(sql, params, fetch_limit=1)
        if not result.rows:
            return None
        return next(iter(result.rows[0].values()))

    def sample_rows(self, table_name: str, limit: int) -> QueryExecution:
        safe_limit = max(1, min(limit, self.settings.max_row_limit))
        sql = f"SELECT TOP ({safe_limit}) * FROM {quote_silver_table(table_name)}"
        return self.execute(sql, fetch_limit=safe_limit)

    def executemany_scalar(self, sql: str, param_sets: Iterable[Sequence[Any]]) -> list[Any]:
        values: list[Any] = []
        for params in param_sets:
            values.append(self.scalar(sql, params))
        return values
