"""Call one MCP tool on the running server, like an AI client would.

  docker compose exec -T mcp python - <tool_name> '<json args>' < ask.py

Examples:
  ... python - list_silver_tables '{}' < ask.py
  ... python - run_readonly_sql '{"sql": "SELECT TOP 5 * FROM Silver.Product_Master"}' < ask.py
"""
from __future__ import annotations

import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = "http://127.0.0.1:8000/mcp"


def print_rows(rows: list[dict], limit: int = 50) -> None:
    if not rows:
        print("(no rows)")
        return
    cols = list(rows[0].keys())
    widths = {c: max(len(c), *(len(str(r.get(c))) for r in rows[:limit])) for c in cols}
    print(" | ".join(c.ljust(widths[c]) for c in cols))
    print("-+-".join("-" * widths[c] for c in cols))
    for row in rows[:limit]:
        print(" | ".join(str(row.get(c)).ljust(widths[c]) for c in cols))
    if len(rows) > limit:
        print(f"... {len(rows) - limit} more rows")


async def main(tool: str, args: dict) -> None:
    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, args)
            payload = json.loads(result.content[0].text)

    if tool == "run_readonly_sql" and payload.get("ok"):
        oa = payload["output_assessment"]
        print(f"ok=True rows={payload['row_count']} time={payload['execution_time_ms']}ms "
              f"(estimated rows={oa['estimated_rows']}, cols={oa['estimated_columns']})")
        for warning in oa["warnings"]:
            print("warning:", warning)
        print_rows(payload["data"])
    else:
        print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}))
