from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip() != "":
            return value
    return default


@dataclass(frozen=True)
class Settings:
    sql_server: str
    sql_database: str
    sql_username: str
    sql_password: str
    sql_driver: str
    sql_port: int
    sql_encrypt: str
    sql_trust_server_certificate: str
    sql_connection_timeout: int
    sql_connection_retries: int
    sql_connection_retry_delay_seconds: int
    sql_query_timeout: int
    fastmcp_host: str
    fastmcp_port: int
    fastmcp_json_response: bool
    fastmcp_stateless_http: bool
    mcp_transport: str
    default_row_limit: int
    max_row_limit: int
    max_response_columns: int
    max_response_cells: int
    large_table_row_threshold: int
    enable_exact_output_count: bool
    sample_row_limit: int
    artifact_dir: Path
    metadata_on_startup: bool
    fail_on_startup_metadata: bool
    mcp_bearer_token: str
    mcp_public_url: str
    mcp_auth_issuer_url: str
    mcp_auth_tenant_id: str
    mcp_auth_client_id: str
    mcp_auth_audience: str
    mcp_required_scope: str
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        artifact_dir = Path(os.getenv("MCP_ARTIFACT_DIR", ".")).resolve()
        return cls(
            sql_server=_first_env("SQL_SERVER", "server_name").strip(),
            sql_database=_first_env("SQL_DATABASE", "database").strip(),
            sql_username=_first_env("SQL_USERNAME", "server_admin", "server_user").strip(),
            sql_password=_first_env("SQL_PASSWORD", "sql_password"),
            sql_driver=os.getenv("SQL_DRIVER", "ODBC Driver 18 for SQL Server").strip(),
            sql_port=_as_int("SQL_PORT", 1433),
            sql_encrypt=os.getenv("SQL_ENCRYPT", "yes").strip(),
            sql_trust_server_certificate=os.getenv("SQL_TRUST_SERVER_CERTIFICATE", "no").strip(),
            sql_connection_timeout=_as_int("SQL_CONNECTION_TIMEOUT", 120),
            sql_connection_retries=_as_int("SQL_CONNECTION_RETRIES", 3),
            sql_connection_retry_delay_seconds=_as_int("SQL_CONNECTION_RETRY_DELAY_SECONDS", 5),
            sql_query_timeout=_as_int("SQL_QUERY_TIMEOUT", 120),
            fastmcp_host=os.getenv("FASTMCP_HOST", "0.0.0.0").strip(),
            fastmcp_port=_as_int("FASTMCP_PORT", _as_int("PORT", 8000)),
            fastmcp_json_response=_as_bool(os.getenv("FASTMCP_JSON_RESPONSE"), True),
            fastmcp_stateless_http=_as_bool(os.getenv("FASTMCP_STATELESS_HTTP"), True),
            mcp_transport=os.getenv("MCP_TRANSPORT", "streamable-http").strip(),
            default_row_limit=_as_int("MCP_DEFAULT_ROW_LIMIT", 100),
            max_row_limit=_as_int("MCP_MAX_ROW_LIMIT", 1000),
            max_response_columns=_as_int("MCP_MAX_RESPONSE_COLUMNS", 50),
            max_response_cells=_as_int("MCP_MAX_RESPONSE_CELLS", 20000),
            large_table_row_threshold=_as_int("MCP_LARGE_TABLE_ROW_THRESHOLD", 50000),
            enable_exact_output_count=_as_bool(os.getenv("MCP_ENABLE_EXACT_OUTPUT_COUNT"), True),
            sample_row_limit=_as_int("MCP_SAMPLE_ROW_LIMIT", 5),
            artifact_dir=artifact_dir,
            metadata_on_startup=_as_bool(os.getenv("MCP_METADATA_ON_STARTUP"), True),
            fail_on_startup_metadata=_as_bool(os.getenv("MCP_FAIL_ON_STARTUP_METADATA"), False),
            mcp_bearer_token=os.getenv("MCP_BEARER_TOKEN", ""),
            mcp_public_url=os.getenv("MCP_PUBLIC_URL", "").strip(),
            mcp_auth_issuer_url=os.getenv("MCP_AUTH_ISSUER_URL", "").strip(),
            mcp_auth_tenant_id=_first_env("MCP_AUTH_TENANT_ID", "MCP_ENTRA_TENANT_ID").strip(),
            mcp_auth_client_id=_first_env("MCP_AUTH_CLIENT_ID", "MCP_ENTRA_CLIENT_ID").strip(),
            mcp_auth_audience=_first_env("MCP_AUTH_AUDIENCE", "MCP_ENTRA_AUDIENCE").strip(),
            mcp_required_scope=os.getenv("MCP_REQUIRED_SCOPE", "sql.read").strip(),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        )

    @property
    def business_context_path(self) -> Path:
        return self.artifact_dir / "BUSINESS_CONTEXT.md"

    @property
    def schema_catalog_path(self) -> Path:
        return self.artifact_dir / "SCHEMA_CATALOG.json"

    @property
    def public_base_url(self) -> str:
        if not self.mcp_public_url:
            return ""
        url = self.mcp_public_url.rstrip("/")
        if url.endswith("/mcp"):
            url = url[: -len("/mcp")]
        return url.rstrip("/")

    def require_database_config(self) -> None:
        missing = [
            name
            for name, value in {
                "SQL_SERVER": self.sql_server,
                "SQL_DATABASE": self.sql_database,
                "SQL_USERNAME": self.sql_username,
                "SQL_PASSWORD": self.sql_password,
                "SQL_DRIVER": self.sql_driver,
            }.items()
            if not value
        ]
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")


def clamp_limit(value: int | None, settings: Settings) -> int:
    requested = settings.default_row_limit if value is None else value
    if requested < 1:
        return settings.default_row_limit
    return min(requested, settings.max_row_limit)
