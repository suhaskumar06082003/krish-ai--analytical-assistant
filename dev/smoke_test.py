"""End-to-end check: talks to the running MCP server over streamable HTTP like a real AI client would."""
from __future__ import annotations

import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/mcp"


def banner(title: str) -> None:
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


def table(rows: list[dict], limit: int = 12) -> None:
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


async def main() -> None:
    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            init = await session.initialize()

            async def call(name: str, **args):
                result = await session.call_tool(name, args)
                return json.loads(result.content[0].text)

            banner("1. initialize + list_tools")
            print("server:", init.serverInfo.name)
            print("instructions sent to the AI:", len(init.instructions or ""), "chars")
            print("tools:", ", ".join(sorted(t.name for t in (await session.list_tools()).tools)))

            banner("2. list_silver_tables")
            res = await call("list_silver_tables")
            print(res["table_count"], "tables:", ", ".join(res["tables"]))

            banner("3. suggest_tables_for_question('ZSM wise secondary sales last month')")
            res = await call("suggest_tables_for_question", question="ZSM wise secondary sales last month")
            for t in res["tables"]:
                print(f"  score {t['score']:>3}  {t['table_name']}")
            for hint in res["join_suggestions"]:
                print("  join hint:", hint)

            banner("4. describe_table('Product_Master', sample_rows=3)")
            res = await call("describe_table", table_name="Product_Master", sample_rows=3)
            t = res["table"]
            print("columns:", ", ".join(f"{c['name']} {c['data_type']}" for c in t["columns"]))
            print("primary key:", t["primary_keys"], "| row estimate:", t["row_count_estimate"])
            table(t["sample_rows"])

            banner("5. generate_sql('total sales by flavour last month') -> run_readonly_sql")
            gen = await call("generate_sql", question="total sales by flavour last month")
            print(gen["sql"])
            run = await call("run_readonly_sql", sql=gen["sql"])
            print(f"ok={run['ok']} rows={run.get('row_count')} time={run.get('execution_time_ms')}ms")
            table(run.get("data", []))

            banner("6. validate_sql: unsafe queries must be blocked")
            for sql in (
                "DELETE FROM Silver.Product_Master",
                "SELECT * FROM Bronze.Raw_Secrets",
                "SELECT 1 FROM Silver.Product_Master; DROP TABLE Silver.Product_Master",
                "SELECT * INTO Silver.Copy FROM Silver.Product_Master",
                "SELECT * FROM Product_Master",
                "SELECT name FROM sys.tables",
            ):
                res = await call("validate_sql", sql=sql)
                verdict = "ALLOWED" if res["is_valid"] else "BLOCKED"
                print(f"  {verdict:8} {sql[:60]:60}  {'; '.join(res['errors'])}")

            banner("7. ZSM-wise secondary invoice value, Sep 2026 (retailer roll-up from BUSINESS_CONTEXT)")
            sql = """
SELECT sm.ZSM_Code, z.Name AS ZSM_Name, COUNT(DISTINCT si.Invoice_Id) AS invoices,
       SUM(si.Amount_Incl_Tax) AS invoice_value
FROM Silver.Salesman_Retailer_Mapping srm
JOIN Silver.Salesman_Mapping sm
  ON srm.Salesman_Code IN (sm.PSR_Code, sm.SO_Code, sm.SSO_Code, sm.ASM_Code, sm.RSM_Code, sm.ZSM_Code)
 AND sm.To_date IS NULL
JOIN Silver.Secondary_Invoices si ON si.Retailer_Code = srm.Retailer_Code
JOIN Silver.Salesman_Master z ON z.Salesman_Code = sm.ZSM_Code
WHERE srm.To_date IS NULL AND si.Date >= '2026-09-01' AND si.Date < '2026-10-01'
GROUP BY sm.ZSM_Code, z.Name
ORDER BY invoice_value DESC"""
            run = await call("run_readonly_sql", sql=sql)
            print(f"ok={run['ok']} estimated_rows={run['output_assessment']['estimated_rows']}")
            table(run.get("data", []))
            print("NOTE: a retailer mapped to an SO matches 2 mapping rows (one per PSR under that SO),")
            print("      so the textbook query double-counts those retailers. Check vs. plain hub total:")
            run = await call("run_readonly_sql", sql="""
SELECT r.Hub, SUM(si.Amount_Incl_Tax) AS invoice_value
FROM Silver.Secondary_Invoices si JOIN Silver.Retailer_Master r ON r.Retailer_Code = si.Retailer_Code
WHERE si.Date >= '2026-09-01' AND si.Date < '2026-10-01'
GROUP BY r.Hub ORDER BY r.Hub""")
            table(run.get("data", []))

            banner("8. TOD % by hub (TOD / (Amount_Excl_Tax + TOD) * 100), 2026")
            run = await call("run_readonly_sql", sql="""
SELECT r.Hub,
       SUM(si.TOD) AS tod,
       CAST(SUM(si.TOD) * 100.0 / NULLIF(SUM(si.Amount_Excl_Tax + si.TOD), 0) AS decimal(6, 2)) AS tod_pct
FROM Silver.Secondary_Invoices si
JOIN Silver.Retailer_Master r ON r.Retailer_Code = si.Retailer_Code
WHERE si.Date >= '2026-01-01'
GROUP BY r.Hub""")
            table(run.get("data", []))

            banner("9. Primary sales vs hub target by month, 2026 H1 (net of returns)")
            run = await call("run_readonly_sql", sql="""
SELECT t.Month, t.Hub_Name, t.Primary_Target, ISNULL(a.net_sales, 0) AS net_sales,
       CAST(ISNULL(a.net_sales, 0) * 100.0 / t.Primary_Target AS decimal(6, 1)) AS achievement_pct
FROM Silver.Hub_Target t
LEFT JOIN (
    SELECT DATEFROMPARTS(YEAR(p.Date), MONTH(p.Date), 1) AS Month, d.Hub, SUM(p.Amount_Excl_Tax) AS net_sales
    FROM Silver.Primary_Invoices p
    JOIN Silver.Distributor_Master d ON d.Distributor_Code = p.Distributor_Code
    GROUP BY DATEFROMPARTS(YEAR(p.Date), MONTH(p.Date), 1), d.Hub
) a ON a.Month = t.Month AND a.Hub = t.Hub_Name
WHERE t.Month >= '2026-01-01' AND t.Month < '2026-07-01'
ORDER BY t.Month, t.Hub_Name""")
            table(run.get("data", []), limit=12)

            banner("10. estimate_sql_output on a raw dump of the biggest table")
            res = await call("estimate_sql_output", sql="SELECT * FROM Silver.Secondary_Invoice_Line_Items")
            oa = res["output_assessment"]
            print(f"can_execute={oa['can_execute']} rows={oa['estimated_rows']} cols={oa['estimated_columns']} cells={oa['estimated_cells']}")
            for w in oa["warnings"]:
                print("  warning:", w)

            banner("11. CTE + GROUP BY (no TOP) -> does run_readonly_sql accept it?")
            run = await call("run_readonly_sql", sql="""
WITH monthly AS (
    SELECT DATEFROMPARTS(YEAR(Date), MONTH(Date), 1) AS Month, SUM(Amount_Excl_Tax) AS sales
    FROM Silver.Secondary_Invoices GROUP BY DATEFROMPARTS(YEAR(Date), MONTH(Date), 1)
)
SELECT Month, sales FROM monthly ORDER BY Month""")
            print("ok =", run["ok"], "|", run.get("message") or f"{run.get('row_count')} rows")

            banner("12. get_business_context(topic='TOD')")
            res = await call("get_business_context", topic="TOD")
            print(res["content"][:700], "...")


if __name__ == "__main__":
    asyncio.run(main())
