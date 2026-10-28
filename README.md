# Hangyo Silver Azure SQL MCP Server

Remote MCP server for read-only discovery and querying of the `Silver` schema in Azure SQL Serverless.

The server is designed for MCP-compatible clients such as Claude Desktop, ChatGPT, Cursor, Windsurf, VS Code, and future clients. It exposes tools that let the AI discover business context, inspect only relevant schema, generate safe SQL drafts, validate SQL, and execute read-only queries.

## Architecture

- Runtime: Python, official MCP Python SDK `FastMCP`
- Transport: Streamable HTTP at `/mcp`
- Optional legacy transport: SSE when `MCP_TRANSPORT=sse`
- Health check: `/health`
- Database: Azure SQL through `pyodbc`
- Deployment target: Azure Container Apps

The server does not send the full database schema or all business documentation with every request. It writes two startup artifacts and loads targeted context through tools:

- `BUSINESS_CONTEXT.md`
- `SCHEMA_CATALOG.json`

These files are generated at runtime and are ignored by git. `BUSINESS_CONTEXT.md` includes a generated Silver table purpose guide so clients do not need to rediscover table purpose on every request.

## Tools

- `get_business_context`: returns business context, optionally filtered by topic.
- `refresh_metadata`: regenerates `BUSINESS_CONTEXT.md` and `SCHEMA_CATALOG.json`.
- `list_silver_tables`: returns discovered Silver tables.
- `describe_table`: returns columns, data types, nullability, primary keys, row estimate, and sample rows for one table.
- `search_schema`: searches table names, column names, and descriptions.
- `suggest_tables_for_question`: suggests relevant tables and join hints for a natural language question.
- `generate_sql`: creates a conservative SQL Server `SELECT` query from a natural language question.
- `validate_sql`: validates SQL safety.
- `estimate_sql_output`: estimates result rows, columns, and response size before execution.
- `run_readonly_sql`: executes validated SQL only when the estimated AI response is within configured limits.

## Large Table Policy

Large scans inside Azure SQL are allowed. The server may execute aggregation, filtering, grouping, ranking, and summary queries over large tables.

Allowed patterns include:

- `SUM`, `COUNT`, `AVG`, `MIN`, `MAX`
- `GROUP BY`
- `TOP N` ranking
- Date-filtered summaries
- Retailer-wise, distributor-wise, SKU-wise, hub-wise, and salesman-wise summaries

The server blocks or warns before returning large raw outputs to the AI client:

- Raw `SELECT *` against large transaction tables
- Invoice/order row dumps with thousands or lakhs of rows
- Transaction queries without date filters
- Queries returning too many rows, columns, or cells

Before `run_readonly_sql` returns data, it estimates output rows and columns. If the result may exceed AI token limits, the server explains the issue, suggests aggregation or filtering, and asks the client to narrow the query.

## Local Setup

1. Create a virtual environment.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Install dependencies.

```powershell
pip install -r requirements.txt
```

3. Copy `.env.example` values into `.env` and fill your Azure SQL values.

Required variables:

```env
SQL_SERVER=
SQL_DATABASE=
SQL_USERNAME=
SQL_PASSWORD=
SQL_DRIVER=ODBC Driver 18 for SQL Server
```

For local compatibility with older `.env` files, the server also accepts these aliases: `server_name` for `SQL_SERVER`, `database` for `SQL_DATABASE`, `server_admin` or `server_user` for `SQL_USERNAME`, and `sql_password` for `SQL_PASSWORD`. Prefer the `SQL_*` names for production deployments.

4. Run the server.

```powershell
python -m src.server
```

5. Connect an MCP inspector or client to:

```text
http://localhost:8000/mcp
```

## Docker Build

```powershell
docker build -t hangyo-silver-mcp .
```

## Docker Run

```powershell
docker run --env-file .env -p 8000:8000 hangyo-silver-mcp
```

Then open:

```text
http://localhost:8000/health
http://localhost:8000/mcp
```

## Azure Container Apps Deployment

Build and push the image to Azure Container Registry:

```powershell
az login
az group create --name rg-hangyo-mcp --location centralindia
az acr create --resource-group rg-hangyo-mcp --name hangyomcpacr --sku Basic
az acr login --name hangyomcpacr
docker tag hangyo-silver-mcp hangyomcpacr.azurecr.io/hangyo-silver-mcp:latest
docker push hangyomcpacr.azurecr.io/hangyo-silver-mcp:latest
```

Create a Container Apps environment and app:

```powershell
az containerapp env create `
  --name hangyo-mcp-env `
  --resource-group rg-hangyo-mcp `
  --location centralindia

az containerapp create `
  --name hangyo-silver-mcp `
  --resource-group rg-hangyo-mcp `
  --environment hangyo-mcp-env `
  --image hangyomcpacr.azurecr.io/hangyo-silver-mcp:latest `
  --target-port 8000 `
  --ingress external `
  --registry-server hangyomcpacr.azurecr.io `
  --secrets sql-password="<SQL_PASSWORD>" mcp-bearer-token="<LONG_RANDOM_TOKEN>" `
  --env-vars `
    SQL_SERVER="<server>.database.windows.net" `
    SQL_DATABASE="<database>" `
    SQL_USERNAME="<readonly_user>" `
    SQL_PASSWORD=secretref:sql-password `
    SQL_DRIVER="ODBC Driver 18 for SQL Server" `
    SQL_CONNECTION_TIMEOUT=120 `
    SQL_CONNECTION_RETRIES=3 `
    SQL_CONNECTION_RETRY_DELAY_SECONDS=5 `
    FASTMCP_HOST=0.0.0.0 `
    FASTMCP_PORT=8000 `
    MCP_TRANSPORT=streamable-http `
    MCP_BEARER_TOKEN=secretref:mcp-bearer-token `
    MCP_PUBLIC_URL="https://<your-public-host>/mcp"
```

If you do not know the generated Container Apps FQDN before creation, get it and then update `MCP_PUBLIC_URL`:

```powershell
$fqdn = az containerapp show `
  --name hangyo-silver-mcp `
  --resource-group rg-hangyo-mcp `
  --query properties.configuration.ingress.fqdn `
  --output tsv

az containerapp update `
  --name hangyo-silver-mcp `
  --resource-group rg-hangyo-mcp `
  --set-env-vars MCP_PUBLIC_URL="https://$fqdn/mcp"
```

## Environment Variables

| Variable | Required | Description |
| --- | --- | --- |
| `SQL_SERVER` | Yes | Azure SQL server host. |
| `SQL_DATABASE` | Yes | Azure SQL database name. |
| `SQL_USERNAME` | Yes | Dedicated read-only SQL user. |
| `SQL_PASSWORD` | Yes | SQL password. Use secrets in Azure. |
| `SQL_DRIVER` | Yes | ODBC driver name, usually `ODBC Driver 18 for SQL Server`. |
| `SQL_PORT` | No | Defaults to `1433`. |
| `SQL_CONNECTION_TIMEOUT` | No | Azure SQL login timeout in seconds. Defaults to `120` to allow Azure SQL serverless cold starts. |
| `SQL_CONNECTION_RETRIES` | No | Number of Azure SQL login attempts before failing. Defaults to `3`. |
| `SQL_CONNECTION_RETRY_DELAY_SECONDS` | No | Base wait between login retries. Defaults to `5`; each retry waits `base * attempt`. |
| `SQL_QUERY_TIMEOUT` | No | Query execution timeout in seconds after login succeeds. Defaults to `120`. |
| `MCP_DEFAULT_ROW_LIMIT` | No | Default maximum rows allowed in an AI response. Defaults to `100`. |
| `MCP_MAX_ROW_LIMIT` | No | Maximum caller-requested response rows. Defaults to `1000`. |
| `MCP_MAX_RESPONSE_COLUMNS` | No | Maximum columns allowed in an AI response. Defaults to `50`. |
| `MCP_MAX_RESPONSE_CELLS` | No | Maximum row-by-column cells allowed in an AI response. Defaults to `20000`. |
| `MCP_LARGE_TABLE_ROW_THRESHOLD` | No | Row estimate where raw transaction queries are treated as large. Defaults to `50000`. |
| `MCP_ENABLE_EXACT_OUTPUT_COUNT` | No | Run an internal `COUNT_BIG` probe to estimate result rows before execution. Defaults to `true`. |
| `MCP_SAMPLE_ROW_LIMIT` | No | Maximum sample rows returned by `describe_table`. Defaults to `5`. |
| `MCP_METADATA_ON_STARTUP` | No | Generate metadata files on startup. Defaults to `true`. |
| `MCP_FAIL_ON_STARTUP_METADATA` | No | Fail container startup if metadata generation fails. Defaults to `false`. |
| `MCP_BEARER_TOKEN` | Recommended | Enables bearer-token protection for remote HTTP clients. |
| `MCP_PUBLIC_URL` | Required with auth | Public MCP URL, for example `https://host/mcp`. |
| `MCP_AUTH_TENANT_ID` | Required for Entra auth | Microsoft Entra tenant ID. When set with `MCP_AUTH_CLIENT_ID`, the server validates Entra JWT bearer tokens. |
| `MCP_AUTH_CLIENT_ID` | Required for Entra auth | Microsoft Entra app registration client ID. |
| `MCP_AUTH_AUDIENCE` | No | Expected access-token audience. Defaults to `api://<MCP_AUTH_CLIENT_ID>`. |
| `MCP_AUTH_ISSUER_URL` | No | OAuth issuer URL. Defaults to `https://login.microsoftonline.com/<MCP_AUTH_TENANT_ID>/v2.0` for Entra auth. |
| `MCP_REQUIRED_SCOPE` | No | Required bearer-token scope. For Entra, use the full API scope, for example `api://<client-id>/user_impersonation`. |
| `FASTMCP_HOST` | No | Defaults to `0.0.0.0`. |
| `FASTMCP_PORT` | No | Defaults to `8000`. |
| `MCP_TRANSPORT` | No | `streamable-http`, `sse`, or `stdio`. |

## Security Model

- Credentials are read only from environment variables.
- Credentials and connection strings are never logged.
- Database access should use the dedicated read-only SQL user restricted to `Silver`.
- Query execution is blocked unless validation passes.
- Validation allows only `SELECT` and `WITH`.
- Validation blocks write/admin verbs including `INSERT`, `UPDATE`, `DELETE`, `DROP`, `TRUNCATE`, `ALTER`, `CREATE`, `MERGE`, `EXEC`, `GRANT`, `REVOKE`, and `DENY`.
- Runtime SQL is restricted to `Silver.*` table references.
- `Bronze.*`, `Gold.*`, `sys.*`, and `INFORMATION_SCHEMA.*` are blocked for client-provided SQL.
- Internal metadata queries may use `INFORMATION_SCHEMA` and `sys` to build the catalog.
- Large scans inside Azure SQL are allowed, but large raw responses to AI clients are blocked.
- `run_readonly_sql` estimates result shape before execution and checks actual returned rows again before sending data.
- Oversized outputs are rejected with suggestions to aggregate, filter, select fewer columns, or use `TOP N` ranking.
- For public ingress, set Microsoft Entra MCP auth environment variables, `MCP_BEARER_TOKEN`, or put the app behind a private network, API gateway, or identity-aware proxy.
- Do not use Azure Container Apps built-in Authentication for ChatGPT or Claude MCP connectors. Keep Container App authentication disabled and let the MCP server advertise OAuth protected-resource metadata and validate Entra bearer tokens.

## MCP Client Examples

Remote streamable HTTP URL:

```text
https://<your-container-app>/mcp
```

Authorization header when `MCP_BEARER_TOKEN` is enabled:

```text
Authorization: Bearer <LONG_RANDOM_TOKEN>
```

Microsoft Entra OAuth for ChatGPT, Claude, or another remote MCP client:

```env
MCP_PUBLIC_URL=https://<your-container-app>/mcp
MCP_AUTH_TENANT_ID=<tenant-id>
MCP_AUTH_CLIENT_ID=<client-id>
MCP_AUTH_AUDIENCE=api://<client-id>
MCP_REQUIRED_SCOPE=api://<client-id>/user_impersonation
```

The server advertises protected resource metadata at:

```text
https://<your-container-app>/.well-known/oauth-protected-resource/mcp
```

Generic remote MCP JSON shape used by many clients:

```json
{
  "mcpServers": {
    "hangyo-silver-sql": {
      "type": "http",
      "url": "https://<your-container-app>/mcp",
      "headers": {
        "Authorization": "Bearer <LONG_RANDOM_TOKEN>"
      }
    }
  }
}
```

Local stdio shape for clients that run the server process directly:

```json
{
  "mcpServers": {
    "hangyo-silver-sql": {
      "command": "python",
      "args": ["-m", "src.server"],
      "cwd": "C:\\Users\\ChandrashekarG\\OneDrive - HANGYO ICECREAMS PRIVATE LIMITED\\Desktop\\remote MCP",
      "env": {
        "MCP_TRANSPORT": "stdio"
      }
    }
  }
}
```

Client-specific configuration names vary. Use the same URL and header for Claude Desktop, ChatGPT connectors, Cursor, Windsurf, VS Code, or any MCP client that supports remote HTTP MCP servers.

## Recommended AI Workflow

```text
User question
  -> suggest_tables_for_question
  -> describe_table
  -> generate_sql
  -> validate_sql
  -> estimate_sql_output
  -> run_readonly_sql
```

Use `get_business_context` only when business terminology is needed. Use `search_schema` or `describe_table` for focused schema discovery.

## Validation Checklist

After installing dependencies locally, you can run the repeatable runtime validation script:

```powershell
.\.venv\Scripts\python.exe .\scripts\validate_runtime.py
```

Before production use, verify:

1. Database connection works.
2. `list_silver_tables` discovers Silver tables.
3. `refresh_metadata` generates metadata.
4. `BUSINESS_CONTEXT.md` is generated.
5. `SCHEMA_CATALOG.json` is generated.
6. A safe `SELECT` executes through `run_readonly_sql`.
7. Unsafe SQL is blocked by `validate_sql`.
8. Docker image builds.
9. MCP tools are discoverable from a client or MCP inspector.
10. Container App runs and `/health` returns `ok`.
