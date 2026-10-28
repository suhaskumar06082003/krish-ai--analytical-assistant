from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE_BUSINESS_CONTEXT = """# Business Context

This database belongs to an ice cream company. The MCP server exposes read-only access to the Silver schema and should be used to discover only the business context needed for a specific question.

## Salesman Hierarchy

The sales hierarchy is:

```text
ZSM -> RSM -> ASM -> SSO -> SO -> PSR
```

Hierarchy changes over time. Historical hierarchy should be read from `Silver.Salesman_Mapping`, using `From_Date` and `To_Date` when answering time-based questions.

Important columns:

- `ZSM_Code`
- `RSM_Code`
- `ASM_Code`
- `SSO_Code`
- `SO_Code`
- `PSR_Code`

Hierarchy may be incomplete. Some levels can be null, so joins and filters should not assume every row has every hierarchy level.

## Salesman Performance And Retailer Roll-Up

For ANY salesman performance question - ZSM, RSM, ASM, SSO, SO, or PSR sales, invoice value, quantity, or targets - do NOT expect a salesman hierarchy code on the invoice or order tables. Roll up through retailers instead:

1. Map salesman to retailer with `Silver.Salesman_Retailer_Mapping`. For the current state filter `To_date IS NULL`; for a historical point, filter so the date falls between `From_date` and `To_date`. Prefer this dated table with `To_date IS NULL` over the `_Current` view.
2. `Salesman_Retailer_Mapping` maps each retailer to only ONE salesman - the LOWEST level present in that retailer's chain. Preference order, lowest first: PSR, then SO, then SSO, then ASM, then RSM, then ZSM. The mapped salesman is often an SO or PSR but NOT always - never assume it is a PSR.
3. Because the mapped salesman can be at any level, resolve the full hierarchy by matching the mapped `Salesman_Code` against EVERY level column in `Silver.Salesman_Mapping` filtered to current rows with `To_date IS NULL` (use this dated table, not the `_Current` view), then read whichever level you need:

```text
Salesman_Retailer_Mapping.Salesman_Code   (To_date IS NULL)
   = ANY of Salesman_Mapping.(PSR_Code, SO_Code, SSO_Code, ASM_Code, RSM_Code, ZSM_Code)   (To_date IS NULL)
   -> then read ZSM_Code / RSM_Code / ASM_Code / SSO_Code / SO_Code for the requested level
```

4. Aggregate the retailer-level facts - secondary sales (`Secondary_Invoices` / `Secondary_Invoice_Line_Items`) joined on `Retailer_Code` - and GROUP BY the requested hierarchy level.

Example - invoice value by ZSM:

```sql
SELECT sm.ZSM_Code, SUM(si.Amount_Incl_Tax) AS invoice_value
FROM Silver.Salesman_Retailer_Mapping srm
JOIN Silver.Salesman_Mapping sm
  ON srm.Salesman_Code IN (sm.PSR_Code, sm.SO_Code, sm.SSO_Code, sm.ASM_Code, sm.RSM_Code, sm.ZSM_Code)
 AND sm.To_date IS NULL
JOIN Silver.Secondary_Invoices si
  ON si.Retailer_Code = srm.Retailer_Code
WHERE srm.To_date IS NULL
GROUP BY sm.ZSM_Code;
```

This retailer roll-up is the required path because salesmen operate in secondary sales (distributor -> retailer) and the invoice tables carry retailer/distributor codes, not hierarchy codes. (`Secondary_Orders` does have a `Salesman_Code`, but that is only the order-taking salesman - use the retailer roll-up for consistent ZSM/RSM/ASM/SSO/SO/PSR attribution.)

## Hubs

The company operates through hubs such as North Karnataka and South Karnataka. Future hub structure can change.

There is no separate hub master table; a hub is identified by its `Hub` text value (for example North Karnataka, South Karnataka), matched the same way across tables.

Only `Distributor_Master` and `Retailer_Master` carry a direct `Hub` column. `Silver.Salesman_Master` does NOT have a `Hub` column. To find a salesman's hub, join through their retailers: `Silver.Salesman_Retailer_Mapping` (Salesman_Code -> Retailer_Code) then `Silver.Retailer_Master.Hub`. The same applies to a beat: `Silver.Beat_Retailer_Mapping` (Beat_Code -> Retailer_Code) then `Silver.Retailer_Master.Hub`. A salesman or beat almost always resolves to one hub this way, but in rare cases their retailers span more than one hub - if that matters, check for multiple distinct `Hub` values rather than assuming exactly one. `Distributor_Master.Hub` needs no join since the distributor already carries `Hub` directly.

ZSMs typically own hubs. RSM and ASM roles operate between the company and distributors. SSO, SO, and PSR roles operate between distributors and retailers.

## Beat Concept

A beat is assigned to a salesman. A beat contains multiple retailers. Salesmen visit retailers through beats.

Beat-related analysis should usually look for beat, salesman, retailer, visit, route, or mapping tables and should respect date ranges when historical mapping columns exist.

## Primary Sales Flow

Primary sales describe company-to-channel sales.

```text
Company -> Distributor
Company -> Super Stockist -> Under Super Stockist
```

Orders can generate invoices.

IMPORTANT scope: "primary sales" means ONLY the direct company-to-distributor flow - the tables `Silver.Primary_Orders`, `Silver.Primary_Invoices`, `Silver.Primary_Order_Line_Items`, and `Silver.Primary_Invoice_Line_Items`. Do NOT include the `Silver.Primary_Under_SS_*` tables. Those cover the separate super stockist -> under super stockist flow and should be used only when the user explicitly asks about super stockists or under super stockists.

### Primary Invoices: Sales, Returns, And Columns

`Silver.Primary_Invoices` and `Silver.Primary_Invoice_Line_Items` have a `Transaction_Type` column with values `Sales` and `Returns`:

- `Sales` rows have positive `Quantity`, `Amount_Excl_Tax`, and `Amount_Incl_Tax`.
- `Returns` rows are negative.
- Summing amount or quantity across both types gives net sales (returns already subtract). Filter on `Transaction_Type` to report gross sales or returns separately.

Both tables have two distributor columns:

- `Distributor_Code`: the distributor on the invoice.
- `Billing_Distributor_Code`: the billing distributor.

Both tables also have a `Plant_Code` column that maps to `Silver.Plant_Master.Plant_Code` (the manufacturing/stocking plant on the invoice).

Dates: always use `Date` for time analysis (the invoice/transaction date). Ignore `Creation_Date` (a record-creation timestamp) - do not filter, group, or report on `Creation_Date`.

## Secondary Sales Flow

Secondary sales describe channel-to-retailer sales.

```text
Distributor -> Retailer
Under Super Stockist -> Retailer
```

Orders can generate invoices. Secondary sales questions usually involve retailers, distributors, under super stockists, orders, invoices, invoice line items, beats, and salesmen.

### TOD (Turnover Discount)

`Silver.Secondary_Invoices` and `Silver.Secondary_Invoice_Line_Items` have a `TOD` column: the discount the distributor gives to retailers, which the distributor then claims back from the company.

```text
TOD percentage = TOD / (Amount_Excl_Tax + TOD) * 100
```

Use this formula whenever a TOD percentage or rate is requested; do not compute it against `Amount_Excl_Tax` alone.

## Targets (Monthly Sales Targets)

Three tables hold monthly sales targets. Each has a `Month` (the target month, a date at month granularity), a `Primary_Target`, and a `Secondary_Target`:

- `Silver.Distributor_Target`: target per distributor. Columns `Month`, `Distributor_Code`, `Distributor_Type`, `Primary_Target`, `Secondary_Target`. `Distributor_Type` is one of `Direct`, `Super_Stockist`, or `Under_Super_Stockist`. Join `Distributor_Code` -> `Distributor_Master.Distributor_Code`.
- `Silver.Salesman_Target`: target per salesman. Columns `Month`, `Salesman_Code`, `Primary_Target`, `Secondary_Target`. Join `Salesman_Code` -> `Salesman_Master.Salesman_Code`. For a hierarchy-level target (ZSM/RSM/ASM/SSO/SO/PSR), roll salesmen up via `Salesman_Mapping` (see "Salesman Performance And Retailer Roll-Up").
- `Silver.Hub_Target`: target per hub. Columns `Month`, `Hub_Name`, `Primary_Target`, `Secondary_Target`. `Hub_Name` matches the `Hub` text value on `Distributor_Master.Hub` and `Retailer_Master.Hub`. `Salesman_Master` has no `Hub` column - resolve a salesman's hub via `Salesman_Retailer_Mapping` -> `Retailer_Master.Hub`.

`Primary_Target` is compared against primary sales (company -> distributor); `Secondary_Target` against secondary sales (distributor -> retailer). When measuring target vs achievement, aggregate the matching sales facts for the same `Month` and entity, then compare to the target column. Filter or group by `Month` (month granularity) on both sides.

## Sales Seasonality

Ice cream sales are at peak in the summer months: March, April, and May.

Monsoon first hits the western coastal region, then moves to other regions.

## Query Output Awareness

Before executing SQL, know the estimated output row count. This is especially important for orders, invoices, order line items, and invoice line items.

## Indexes And Query Performance

Silver tables are indexed. Use these indexes when filtering, joining, grouping, ordering, and especially when building live artifacts, dashboards, or repeated interactive queries.

- Large fact and transaction tables (all `*_Invoices`, `*_Invoice_Line_Items`, `*_Orders`, `*_Order_Line_Items`, `Secondary_*`, and `TC_PC`) use clustered columnstore indexes. These are efficient for aggregations, `GROUP BY`, `SUM`/`COUNT`/`AVG`, and date-range scans, so prefer aggregated queries over row-by-row access on these tables.
- Master and mapping tables use rowstore indexes on their key/code columns, for example `Distributor_Code`, `Retailer_Code`, `Salesman_Code`, `Product_ID`, `Beat_Code`, `Material_Code`, and `Hub`. Filter and join on these indexed columns.

Call the `get_table_indexes` tool with a table name to list that table's indexes (name, type, key columns, included columns). Do this before building any live artifact or dashboard so that filters, joins, `GROUP BY`, and `ORDER BY` target indexed columns.

## Products And MRP

Product and SKU attributes are in `Silver.Product_Master`.

Join product transaction line items to product master with:

```text
Line item Product_Code -> Product_Master Product_ID
```

For product price/MRP questions, use `MRP_Per_Unit` by default. Use `MRP` only when the user explicitly asks for pack MRP, total MRP, or just the `MRP` column.

Product category, sub-category, flavour, units, ML, liters, GM, product name, and product ID should come from `Silver.Product_Master` when product enrichment is needed.

## Assets

Freezers are called assets. Assets move frequently and ownership or location is historical and time-dependent.

Examples:

```text
Retailer A -> Distributor
Distributor -> Retailer B
Retailer A -> Retailer C
```

Asset questions should look for asset, freezer, request, movement, owner, location, retailer, distributor, and date columns.

## Active And Inactive Entities

Retailers and distributors can become active or inactive. Historical changes should be respected whenever date ranges, history tables, status dates, or active flags exist.

For active or inactive questions, inspect the relevant master/history table columns first. Do not assume the exact active status values without checking sample rows.

## Table Relationships And Join Keys

Most tables connect through shared *code* columns. The main exception is products: line-item `Product_Code` maps to `Product_Master.Product_ID` (the column names differ).

Core identifier columns and where they resolve:

- `Product_Code` (on any line-item table) = `Product_Master.Product_ID`
- `Distributor_Code` = `Distributor_Master.Distributor_Code`
- `Retailer_Code` = `Retailer_Master.Retailer_Code`
- `Salesman_Code` = `Salesman_Master.Salesman_Code`
- `Super_Stockist_Code`, `Under_SS_Code`, and `Under_Super_Stockist_Code` all reference `Distributor_Master.Distributor_Code`. Super stockists and under super stockists are stored inside `Distributor_Master`, distinguished by `Distributor_Type`.
- Salesman hierarchy codes `ZSM_Code`, `RSM_Code`, `ASM_Code`, `SSO_Code`, `SO_Code`, and `PSR_Code` each reference `Salesman_Master.Salesman_Code`.
- `Hub` is a plain text column on `Distributor_Master` and `Retailer_Master` only (NOT on `Salesman_Master`). There is no hub master table; group or filter on the `Hub` value directly (match the same text across tables). For a salesman's or beat's hub, join to `Retailer_Master.Hub` via `Salesman_Retailer_Mapping` or `Beat_Retailer_Mapping` respectively.
- `Asset_Code` = `Asset_Master.Asset_Code`
- `Beat_Code` = `Beat_Master.Beat_Code` (beat dimension, `Beat_Code` -> `Beat_Name`); `Beat_Code` also connects `Beat_Salesman_Mapping` and `Beat_Retailer_Mapping`.
- Factory keys (`Material_Code`, `Supplier_Code`, `Plant_Code`, `Storage_Code`) are covered in the Factory section below.

Product enrichment (all sales channels):

```text
<any line item>.Product_Code -> Product_Master.Product_ID
```

Primary sales document chain (company -> distributor):

```text
Primary_Orders.Order_Number      -> Primary_Order_Line_Items.Order_Number
Primary_Orders.Order_Number      -> Primary_Invoices.Order_Number
Primary_Invoices.Invoice_Number  -> Primary_Invoice_Line_Items.Invoice_Number
Primary_Invoices.Invoice_Id      -> Primary_Invoice_Line_Items.Invoice_Id
(all rows carry Distributor_Code -> Distributor_Master.Distributor_Code)
```

Under-super-stockist primary chain (super stockist -> under super stockist):

```text
Primary_Under_SS_Orders.Order_Number     -> Primary_Under_SS_Order_Line_Items.Order_Number
Primary_Under_SS_Orders.Order_Number     -> Primary_Under_SS_Invoices.Order_Number
Primary_Under_SS_Invoices.Invoice_Number -> Primary_Under_SS_Invoice_Line_Items.Invoice_Number
Primary_Under_SS_Invoices.Invoice_Id     -> Primary_Under_SS_Invoice_Line_Items.Invoice_Id
(Super_Stockist_Code and Under_SS_Code -> Distributor_Master.Distributor_Code)
```

Secondary sales document chain (distributor / under super stockist -> retailer):

```text
Secondary_Orders.Order_Number      -> Secondary_Order_Line_Items.Order_Number
Secondary_Orders.Order_Number      -> Secondary_Invoices.Order_Number
Secondary_Invoices.Invoice_Number  -> Secondary_Invoice_Line_Items.Invoice_Number
Secondary_Invoices.Invoice_Id      -> Secondary_Invoice_Line_Items.Invoice_Id
(rows carry Distributor_Code -> Distributor_Master and Retailer_Code -> Retailer_Master;
 Secondary_Orders also has Salesman_Code -> Salesman_Master.Salesman_Code)
```

Master / dimension connections (which tables point back to each master):

```text
Distributor_Master.Distributor_Code <- Distributor_Active_Status, Distributor_Retailer_Mapping,
                                        Asset_Mapping, Distributor_Target, and all primary/secondary sales facts
Retailer_Master.Retailer_Code       <- Retailer_Active_Status, Distributor_Retailer_Mapping,
                                        Beat_Retailer_Mapping, Salesman_Retailer_Mapping,
                                        Asset_Mapping, secondary sales facts, TC_PC
Salesman_Master.Salesman_Code       <- Salesman_Active_Status, Salesman_Retailer_Mapping,
                                        Beat_Salesman_Mapping, Salesman_Mapping (as ZSM/RSM/ASM/SSO/SO/PSR),
                                        Secondary_Orders, Salesman_Target, TC_PC
Hub (text value, no master table)   shared column on Distributor_Master.Hub and Retailer_Master.Hub only.
                                        Salesman_Master has no Hub column - resolve via
                                        Salesman_Retailer_Mapping -> Retailer_Master.Hub (also
                                        Beat_Retailer_Mapping -> Retailer_Master.Hub for a beat's hub)
Product_Master.Product_ID           <- every *_Line_Items.Product_Code
Asset_Master.Asset_Code             <- Asset_Mapping.Asset_Code
Beat_Master.Beat_Code               <- Beat_Salesman_Mapping, Beat_Retailer_Mapping
Plant_Master.Plant_Code             <- Primary_Invoices, Primary_Invoice_Line_Items, Factory_Purchases,
                                       Inventory_Current_Stock, Factory_Consumption, Material_Lead_Time
```

Salesman-to-retailer coverage (two paths):

```text
Salesman_Retailer_Mapping (Salesman_Code, Retailer_Code)  -- salesman serves retailer directly (historical)
Beat_Salesman_Mapping (Beat_Code, Salesman_Code)          -- salesman owns beats
Beat_Retailer_Mapping (Beat_Code, Retailer_Code)          -- beat contains retailers
=> salesman -> beat -> retailer, or salesman -> retailer directly
```

Visits and tasks:

```text
TC_PC (Retailer_Code, Distributor_Code, Salesman_Code) -- field visit/task facts joined to those three masters
```

Use `Task_Planned_Date` for time analysis on `TC_PC` - the date the visit/task was planned for. Do NOT use `CreatedDate`, which is only the record-creation timestamp.

Tables with `From_date`/`To_date` are historical: filter so the transaction date falls within the range. The `*_Current` variants (for example `Salesman_Mapping_Current`, `Salesman_Retailer_Mapping_Current`, `Salesman_Active_Status_Current`) hold only the latest state.

## Factory, Procurement, And Inventory

The Silver schema also covers the factory supply-chain side: plants, materials, suppliers, storage locations, procurement purchases, and current inventory. These factory tables are distinct from the downstream sales tables (distributors, retailers, orders, invoices).

Dimension (master) tables:

- `Silver.Plant_Master`: one row per manufacturing/stocking plant. Key column `Plant_Code`.
- `Silver.Material_Master`: raw materials, packaging, consumables, spares, and finished-good SKUs. Key column `Material_Code`, plus `Material_Name`, `Material_Category`, `Material_Group_Code`, and `UOM`.
- `Silver.Supplier_Master`: vendors/suppliers. Key column `Supplier_Code`.
- `Silver.Storage_Master`: storage-location labels via `Storage_Code`. `Storage_Code` is not unique in this table, so joining it can multiply rows; aggregate or validate before trusting counts.

Fact tables:

- `Silver.Factory_Purchases`: purchase-order lines for procuring materials from suppliers, with `Purchase_Order`, `Order_Date`, `Order_Quantity`, `Order_Value`, `Delivered_Quantity`, and `Delivered_Value`.
- `Silver.Inventory_Current_Stock`: current stock snapshot by material, plant, and storage location, split into `Unrestricted_Stock`, `Stock_in_Quality_Inspection`, and `Blocked_Stock`.
- `Silver.Factory_Consumption`: monthly material consumption tracked by plant, with `Plant_Code`, `Material_Code`, `Year_Month`, `Consumed_Quantity`, and `Amount`.
- `Silver.Material_Lead_Time`: procurement lead time (`Lead_Time`, in days) per `Plant_Code`, `Vendor_Code`, and `Material_Code`, one row per that combination. `Vendor_Code` is the supplier code (`Supplier_Master.Supplier_Code`).

How these factory tables connect:

```text
Factory_Purchases.Material_Code       -> Material_Master.Material_Code
Factory_Purchases.Supplier_Code       -> Supplier_Master.Supplier_Code
Factory_Purchases.Plant_Code          -> Plant_Master.Plant_Code

Inventory_Current_Stock.Material_Code -> Material_Master.Material_Code
Inventory_Current_Stock.Plant_Code    -> Plant_Master.Plant_Code
Inventory_Current_Stock.Storage_Code  -> Storage_Master.Storage_Code

Factory_Consumption.Material_Code     -> Material_Master.Material_Code
Factory_Consumption.Plant_Code        -> Plant_Master.Plant_Code

Material_Lead_Time.Material_Code       -> Material_Master.Material_Code
Material_Lead_Time.Plant_Code          -> Plant_Master.Plant_Code
Material_Lead_Time.Vendor_Code         -> Supplier_Master.Supplier_Code   (Vendor_Code = supplier)
```

`Material_Master.Material_Code` overlaps `Silver.Product_Master.Product_ID` for finished-good SKUs, so factory materials can be linked to the sales product catalog through `Material_Code = Product_ID`.

Factory purchase and inventory questions should use `Order_Date` or `Year_Month` for time filters and remember that `Inventory_Current_Stock` is a current snapshot, not history.

## Glossary

- ZSM: Zonal Sales Manager.
- RSM: Regional Sales Manager.
- ASM: Area Sales Manager.
- SSO: Senior Sales Officer.
- SO: Sales Officer.
- PSR: Pre-Sales Representative.
- Beat: A route or territory grouping retailers assigned to a salesman.
- Beat Master: Beat dimension mapping `Beat_Code` to `Beat_Name` in `Silver.Beat_Master`.
- Retailer: Outlet/customer receiving secondary sales.
- Distributor: Channel partner receiving primary sales and selling to retailers.
- Super Stockist: Channel partner between company and under super stockists.
- Under Super Stockist: Channel partner under a super stockist, often selling onward to retailers.
- Primary Sales: Company-to-distributor or company-to-super-stockist flow.
- Secondary Sales: Distributor-to-retailer or under-super-stockist-to-retailer flow.
- Transaction Type: `Sales` or `Returns` flag on `Silver.Primary_Invoices` and `Silver.Primary_Invoice_Line_Items`; Sales rows are positive and Returns rows are negative.
- Billing Distributor: The distributor billed for an invoice, in `Billing_Distributor_Code`, distinct from the invoice `Distributor_Code`.
- Target: Monthly sales target (`Primary_Target` and `Secondary_Target`, keyed by `Month`) held in `Silver.Distributor_Target`, `Silver.Salesman_Target`, and `Silver.Hub_Target`.
- Distributor_Type: Distributor category on `Silver.Distributor_Target` - `Direct`, `Super_Stockist`, or `Under_Super_Stockist`.
- TOD (Turnover Discount): Discount the distributor gives to retailers, then claims back from the company, in `Silver.Secondary_Invoices` and `Silver.Secondary_Invoice_Line_Items`. TOD percentage = `TOD / (Amount_Excl_Tax + TOD) * 100`.
- Task_Planned_Date: The date a `Silver.TC_PC` visit/task was planned for; use this for time analysis instead of `CreatedDate`.
- Product Master: SKU/product dimension table at `Silver.Product_Master`.
- MRP_Per_Unit: Preferred default MRP field for per-piece or per-unit MRP.
- MRP: Pack or total MRP field. Use only when explicitly requested.
- Asset: Freezer or company asset whose ownership/location can change over time.
- Hub: Operational grouping (for example North Karnataka, South Karnataka) stored as a `Hub` text column on `Distributor_Master` and `Retailer_Master` only. There is no hub master table. `Salesman_Master` has no `Hub` column - find a salesman's hub via `Salesman_Retailer_Mapping` -> `Retailer_Master.Hub`; find a beat's hub via `Beat_Retailer_Mapping` -> `Retailer_Master.Hub`.
- Plant: Manufacturing or stocking location, keyed by `Plant_Code` in `Silver.Plant_Master`.
- Material: Raw material, packaging item, consumable, spare, or finished-good SKU, keyed by `Material_Code` in `Silver.Material_Master`.
- UOM: Unit of Measure (for example kg or liters) associated with a material in `Silver.Material_Master`.
- Supplier: Vendor supplying materials to the factory, keyed by `Supplier_Code` in `Silver.Supplier_Master`.
- Storage Location: Storage-area label in `Silver.Storage_Master`, referenced by `Storage_Code`.
- Factory Purchase: Procurement purchase-order record in `Silver.Factory_Purchases`.
- Current Stock: Current inventory snapshot in `Silver.Inventory_Current_Stock`, split into unrestricted, quality-inspection, and blocked stock.
- Factory Consumption: Historical record of materials consumed by plants, tracked by month, in `Silver.Factory_Consumption`.
- Material Lead Time: Procurement lead time in days per plant, vendor, and material, in `Silver.Material_Lead_Time`.
- Vendor: Same as supplier; `Vendor_Code` in `Silver.Material_Lead_Time` maps to `Supplier_Master.Supplier_Code`.
"""


SERVER_INSTRUCTION_PREAMBLE = """You are a read-only analytics assistant for the Hangyo ice cream company's `Silver` schema in Azure SQL.

The full business context, table purposes, and join relationships are included below - read them before writing any SQL and do not guess table or column relationships. For details not covered here, use `search_schema` and `describe_table` on the specific Silver tables you need, then always `validate_sql` and `estimate_sql_output` before `run_readonly_sql`. When building live artifacts or dashboards, first call `get_table_indexes` for each table you use and make filters, joins, GROUP BY, and ORDER BY target indexed columns. For any salesman (ZSM/RSM/ASM/SSO/SO/PSR) performance or invoice-value question, always roll up via `Salesman_Retailer_Mapping` (retailer -> lowest-level salesman) then `Salesman_Mapping` (see "Salesman Performance And Retailer Roll-Up"); never expect a hierarchy code on the invoice/order tables. For TOD in Secondary_Invoices/Secondary_Invoice_Line_Items, TOD percentage = TOD / (Amount_Excl_Tax + TOD) * 100. For Silver.TC_PC, use `Task_Planned_Date` for time analysis, not `CreatedDate`. Only `Silver.*` tables are accessible; never request other schemas. Do not call `refresh_metadata` unless the user explicitly asks to refresh metadata."""


def render_server_instructions() -> str:
    """Full instructions advertised at MCP initialize: operational preamble + business context."""
    return SERVER_INSTRUCTION_PREAMBLE.strip() + "\n\n" + BASE_BUSINESS_CONTEXT.strip() + "\n"


def _table_names(catalog: dict[str, Any] | None) -> list[str]:
    if not catalog:
        return []
    tables = catalog.get("tables", [])
    return [str(table.get("table_name", "")) for table in tables if table.get("table_name")]


def _table_purpose(table: dict[str, Any]) -> str:
    description = str(table.get("description", "")).strip()
    if description:
        return description
    table_name = str(table.get("table_name", "")).lower()
    columns = " ".join(str(column.get("name", "")).lower() for column in table.get("columns", []))
    text = f"{table_name} {columns}"
    if "retailer" in text:
        return "Retailer-related table used for outlet, beat, hub, status, or secondary sales analysis."
    if "distributor" in text:
        return "Distributor-related table used for channel, hub, primary sales, or active status analysis."
    if "invoice" in text:
        return "Invoice-related table used for sales, billing, quantity, amount, and document-date analysis."
    if "order" in text:
        return "Order-related table used for demand, order status, order dates, and order-to-invoice analysis."
    if "product" in text or "sku" in text or "mrp" in text:
        return (
            "Product/SKU master used for product name, category, sub-category, flavour, units, volume, "
            "GM, and MRP analysis. Prefer `MRP_Per_Unit` unless the user explicitly asks for `MRP`."
        )
    if "salesman" in text or any(level in text for level in ("zsm", "rsm", "asm", "sso", "psr")):
        return "Salesman or hierarchy table used for role ownership, reporting hierarchy, or historical sales mapping."
    if "beat" in text:
        return "Beat or route table used to connect salesmen, visits, and retailers."
    if "asset" in text or "freezer" in text:
        return "Asset/freezer table used for ownership, location, movement, and request history analysis."
    if "hub" in text:
        return "Hub mapping table used for regional or operational grouping analysis."
    return "Silver business table. Inspect with `describe_table` before writing joins or filters."


def _index_summary(table: dict[str, Any]) -> str:
    indexes = table.get("indexes") or []
    if not indexes:
        return ""
    has_columnstore = any(
        "COLUMNSTORE" in str(index.get("index_type", "")).upper() for index in indexes
    )
    key_columns: list[str] = []
    for index in indexes:
        if "COLUMNSTORE" in str(index.get("index_type", "")).upper():
            continue
        for column in index.get("key_columns", []):
            if column not in key_columns:
                key_columns.append(column)
    parts: list[str] = []
    if has_columnstore:
        parts.append("clustered columnstore")
    if key_columns:
        parts.append("indexed on " + ", ".join(f"`{column}`" for column in key_columns[:8]))
    return "; ".join(parts)


def _table_purpose_lines(catalog: dict[str, Any] | None) -> list[str]:
    if not catalog:
        return []
    rows: list[str] = []
    for table in catalog.get("tables", []):
        table_name = str(table.get("table_name", ""))
        if not table_name:
            continue
        date_columns = table.get("date_columns", [])
        primary_keys = table.get("primary_keys", [])
        row_count = table.get("row_count_estimate")
        details: list[str] = [_table_purpose(table)]
        if date_columns:
            details.append("date columns: " + ", ".join(f"`{column}`" for column in date_columns[:8]))
        if primary_keys:
            details.append("primary key: " + ", ".join(f"`{column}`" for column in primary_keys))
        if isinstance(row_count, int):
            details.append(f"row estimate: {row_count}")
        index_summary = _index_summary(table)
        if index_summary:
            details.append("indexes: " + index_summary)
        rows.append(f"- `{table_name}`: " + "; ".join(details))
    return rows


def render_business_context(catalog: dict[str, Any] | None = None) -> str:
    table_names = _table_names(catalog)
    generated_at = datetime.now(timezone.utc).isoformat()
    discovered = [
        "",
        "## Discovered Silver Tables",
        "",
        f"Generated at UTC: `{generated_at}`",
        "",
    ]
    if table_names:
        discovered.extend(f"- `{name}`" for name in table_names)
    else:
        discovered.append("No Silver tables were discovered yet. Run metadata generation after database connectivity is available.")

    purpose_lines = _table_purpose_lines(catalog)
    purposes = [
        "",
        "## Silver Table Purpose Guide",
        "",
        "This section is generated once from metadata so clients do not need to infer table purpose on every request.",
        "",
    ]
    if purpose_lines:
        purposes.extend(purpose_lines)
    else:
        purposes.append("Table purpose guide is unavailable until metadata generation can inspect the Silver schema.")

    return BASE_BUSINESS_CONTEXT.rstrip() + "\n" + "\n".join(discovered + purposes) + "\n"


def write_business_context(path: Path, catalog: dict[str, Any] | None = None) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = render_business_context(catalog)
    path.write_text(content, encoding="utf-8")
    return content


def read_business_context(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return render_business_context()


def filter_business_context(content: str, topic: str | None) -> str:
    if not topic or not topic.strip():
        return content

    keywords = {word.lower() for word in topic.replace("_", " ").split() if len(word) > 2}
    if not keywords:
        return content

    sections = content.split("\n## ")
    selected: list[str] = [sections[0].rstrip()]
    for section in sections[1:]:
        section_text = "## " + section
        lower = section_text.lower()
        if any(keyword in lower for keyword in keywords):
            selected.append(section_text.rstrip())

    if len(selected) == 1:
        selected.append(
            "No exact section matched the topic. Use `search_schema` or `suggest_tables_for_question` for schema-specific discovery."
        )
    return "\n\n".join(selected).strip() + "\n"
