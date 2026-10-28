from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from .database import AzureSqlDatabase, quote_silver_table

logger = logging.getLogger(__name__)

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_@$#]*$")
DATE_NAME_HINTS = ("date", "time", "month", "year")
AUDIT_DATE_NAME_HINTS = ("created", "modified", "updated")
DATE_TYPES = {"date", "datetime", "datetime2", "smalldatetime", "datetimeoffset", "time"}


def normalize_table_name(table_name: str) -> str:
    cleaned = (
        table_name.strip()
        .replace("[", "")
        .replace("]", "")
        .replace('"', "")
        .replace("'", "")
    )
    parts = [part.strip() for part in cleaned.split(".") if part.strip()]
    if len(parts) == 1:
        candidate = parts[0]
    elif len(parts) == 2 and parts[0].lower() == "silver":
        candidate = parts[1]
    else:
        raise ValueError("Only Silver schema table names are accepted.")

    if not IDENTIFIER_RE.match(candidate):
        raise ValueError("Invalid table name.")
    return candidate


def _format_data_type(column: dict[str, Any]) -> str:
    data_type = str(column["data_type"]).lower()
    char_len = column.get("character_maximum_length")
    precision = column.get("numeric_precision")
    scale = column.get("numeric_scale")
    datetime_precision = column.get("datetime_precision")

    if data_type in {"varchar", "nvarchar", "char", "nchar", "binary", "varbinary"}:
        if char_len == -1:
            return f"{data_type}(max)"
        if char_len is not None:
            return f"{data_type}({char_len})"
    if data_type in {"decimal", "numeric"} and precision is not None:
        return f"{data_type}({precision},{scale or 0})"
    if data_type in {"datetime2", "datetimeoffset", "time"} and datetime_precision is not None:
        return f"{data_type}({datetime_precision})"
    return data_type


def _description_for_table(table_name: str) -> str:
    lower = table_name.lower()
    if "distributor_target" in lower:
        return (
            "Monthly distributor sales targets: `Primary_Target` and `Secondary_Target` per `Month` and "
            "`Distributor_Code`, with `Distributor_Type` (`Direct`, `Super_Stockist`, or `Under_Super_Stockist`). "
            "Join `Distributor_Code` to `Distributor_Master`."
        )
    if "salesman_target" in lower:
        return (
            "Monthly salesman sales targets: `Primary_Target` and `Secondary_Target` per `Month` and "
            "`Salesman_Code`. Join `Salesman_Code` to `Salesman_Master`; for a hierarchy level target, roll up "
            "salesmen via `Salesman_Mapping`."
        )
    if "hub_target" in lower:
        return (
            "Monthly hub sales targets: `Primary_Target` and `Secondary_Target` per `Month` and `Hub_Name`. "
            "`Hub_Name` matches the `Hub` text column on the master tables."
        )
    if "plant_master" in lower:
        return "Factory plant master. `Plant_Code` identifies a manufacturing or stocking plant and is joined from Factory_Purchases and Inventory_Current_Stock."
    if "material_master" in lower:
        return (
            "Factory material master for raw materials, packaging, consumables, spares, and finished-good SKUs. "
            "Join `Material_Code` from Factory_Purchases and Inventory_Current_Stock. "
            "`Material_Code` also overlaps `Product_Master.Product_ID` for finished goods."
        )
    if "supplier_master" in lower:
        return "Factory supplier/vendor master. Join `Supplier_Code` from Factory_Purchases."
    if "consumption" in lower:
        return (
            "Factory material consumption facts by plant and month (`Year_Month`), with `Consumed_Quantity` "
            "and `Amount`. Join `Material_Code` and `Plant_Code` to their masters."
        )
    if "lead_time" in lower:
        return (
            "Factory procurement lead-time reference: `Lead_Time` (in days) per `Plant_Code`, `Vendor_Code`, "
            "and `Material_Code`. `Vendor_Code` equals `Supplier_Master.Supplier_Code`; join `Material_Code` "
            "and `Plant_Code` to their masters."
        )
    if "beat_master" in lower:
        return (
            "Beat dimension. Unique `Beat_Code` maps to `Beat_Name`; the master for beats referenced by "
            "Beat_Salesman_Mapping and Beat_Retailer_Mapping. Has no `Hub` column - resolve a beat's hub via "
            "Beat_Retailer_Mapping -> Retailer_Master.Hub."
        )
    if "storage_master" in lower:
        return (
            "Factory storage-location master. `Storage_Code` labels a storage location and is not unique here, "
            "so joins to Inventory_Current_Stock can fan out; aggregate or validate before trusting counts."
        )
    if "inventory" in lower or ("current" in lower and "stock" in lower):
        return (
            "Factory current inventory snapshot by material, plant, and storage location, split into unrestricted, "
            "quality-inspection, and blocked stock. Join `Material_Code`, `Plant_Code`, and `Storage_Code` to their masters."
        )
    if "factory_purchase" in lower or "purchase" in lower:
        return (
            "Factory purchase-order facts for procuring materials from suppliers, with ordered and delivered "
            "quantity/value by `Order_Date`. Join `Material_Code`, `Supplier_Code`, and `Plant_Code` to their masters."
        )
    if "beat_salesman_mapping" in lower:
        return (
            "Maps a beat to the salesman who owns it (`Beat_Code`, `Salesman_Code`). `Salesman_Master` has no "
            "`Hub` column; to find a salesman's hub, join `Salesman_Retailer_Mapping` -> `Retailer_Master.Hub` "
            "instead (this table alone does not carry hub)."
        )
    if "beat_retailer_mapping" in lower:
        return (
            "Maps a beat to the retailers it contains (`Beat_Code`, `Retailer_Code`). To find a beat's hub, join "
            "`Retailer_Code` -> `Retailer_Master.Hub` (a beat almost always resolves to one hub, but check for "
            "more than one distinct `Hub` value if it matters)."
        )
    if "salesman_retailer_mapping" in lower:
        return (
            "Maps a salesman to the retailers they serve (`Salesman_Code`, `Retailer_Code`); only the lowest "
            "hierarchy level present is mapped per retailer (see \"Salesman Performance And Retailer Roll-Up\"). "
            "`Salesman_Master` has no `Hub` column - join `Retailer_Code` -> `Retailer_Master.Hub` to find a "
            "salesman's hub (almost always one hub, but check for more than one distinct value if it matters)."
        )
    if "salesman_mapping" in lower:
        return "Historical sales hierarchy mapping. Join by salesman code and date between From_Date and To_Date."
    if "salesman_master" in lower:
        return (
            "Salesman dimension: `Salesman_Code`, `Name`, `MobilePhone`, `Email`, `Role`. Has NO `Hub` column - "
            "to find a salesman's hub, join `Salesman_Retailer_Mapping` -> `Retailer_Master.Hub`."
        )
    if "retailer" in lower:
        return "Retailer or outlet information used in secondary sales, beats, hubs, visits, and active status analysis."
    if "distributor" in lower:
        return "Distributor channel information used in primary sales, hubs, retailer servicing, and active status analysis."
    if "primary_invoice" in lower:
        return (
            "Primary sales invoice facts (company -> distributor). `Transaction_Type` is `Sales` or `Returns`; "
            "Sales rows are positive and Returns are negative, so summing amount/quantity gives net sales. Use "
            "`Date` for time analysis and ignore `Creation_Date`. `Distributor_Code` is the invoice distributor "
            "and `Billing_Distributor_Code` is the billing distributor. `Plant_Code` maps to "
            "`Plant_Master.Plant_Code`."
        )
    if "secondary_invoice" in lower and "line" in lower:
        return (
            "Secondary sales invoice line item facts (distributor -> retailer). `TOD` is the discount the "
            "distributor gives the retailer, which the distributor then claims back from the company; TOD "
            "percentage = TOD / (Amount_Excl_Tax + TOD) * 100. Use `Invoice_Date` for time analysis."
        )
    if "secondary_invoice" in lower:
        return (
            "Secondary sales invoice header facts (distributor -> retailer). `TOD` is the discount the "
            "distributor gives the retailer, which the distributor then claims back from the company; TOD "
            "percentage = TOD / (Amount_Excl_Tax + TOD) * 100. Use `Date` for time analysis."
        )
    if "invoice" in lower and "line" in lower:
        return "Invoice line item facts. Useful for quantity, amount, SKU, product, and sales value analysis."
    if "invoice" in lower:
        return "Invoice header facts. Useful for sales, billing dates, distributor or retailer relationships, and document status."
    if "order" in lower and "line" in lower:
        return "Order line item facts. Useful for ordered quantity, amount, SKU, and conversion to invoice analysis."
    if "order" in lower:
        return "Order header facts. Useful for demand, order dates, customer relationships, and order status."
    if "product" in lower or "sku" in lower:
        return (
            "Product/SKU master for product name, category, sub-category, flavour, units, volume, GM, "
            "and MRP analysis. Prefer MRP_Per_Unit unless the user explicitly asks for MRP."
        )
    if "asset" in lower or "freezer" in lower:
        return "Asset or freezer records. Treat ownership and location as historical when date columns exist."
    if "beat" in lower:
        return "Beat or route records connecting salesmen to retailers."
    if "hub" in lower:
        return "Hub mapping or operational grouping records."
    if lower == "tc_pc":
        return (
            "Sales visit/task facts, linked to retailers, distributors, and salesmen. Use `Task_Planned_Date` "
            "for time analysis - the date the visit/task was planned for. Do NOT use `CreatedDate`, which is "
            "only the record-creation timestamp."
        )
    if "visit" in lower or "task" in lower:
        return "Sales visit or field task records, often linked to beats, retailers, and salesmen."
    return ""


def _date_columns(columns: list[dict[str, Any]]) -> list[str]:
    dates: list[str] = []
    for column in columns:
        name = str(column["name"])
        lower_name = name.lower()
        data_type = str(column["data_type"]).split("(", 1)[0].lower()
        audit_date_name = any(hint in lower_name for hint in AUDIT_DATE_NAME_HINTS) and not lower_name.endswith("_by")
        if data_type in DATE_TYPES or any(hint in lower_name for hint in DATE_NAME_HINTS) or audit_date_name:
            dates.append(name)
    return dates


def _is_backup_table(table_name: str) -> bool:
    """Backup/snapshot tables (e.g. Asset_Master_bak_20260807) are not for analysis."""
    lower = table_name.lower()
    return "_bak_" in lower or lower.endswith("_bak")


def get_silver_table_names(db: AzureSqlDatabase) -> list[str]:
    result = db.execute(
        """
        SELECT TABLE_NAME
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = ? AND TABLE_TYPE = 'BASE TABLE'
        ORDER BY TABLE_NAME
        """,
        ("Silver",),
    )
    return [row["TABLE_NAME"] for row in result.rows if not _is_backup_table(row["TABLE_NAME"])]


def get_columns(db: AzureSqlDatabase, table_name: str) -> list[dict[str, Any]]:
    result = db.execute(
        """
        SELECT
            COLUMN_NAME,
            DATA_TYPE,
            IS_NULLABLE,
            ORDINAL_POSITION,
            CHARACTER_MAXIMUM_LENGTH,
            NUMERIC_PRECISION,
            NUMERIC_SCALE,
            DATETIME_PRECISION
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
        ORDER BY ORDINAL_POSITION
        """,
        ("Silver", table_name),
    )
    columns: list[dict[str, Any]] = []
    for row in result.rows:
        raw = {
            "data_type": row["DATA_TYPE"],
            "character_maximum_length": row.get("CHARACTER_MAXIMUM_LENGTH"),
            "numeric_precision": row.get("NUMERIC_PRECISION"),
            "numeric_scale": row.get("NUMERIC_SCALE"),
            "datetime_precision": row.get("DATETIME_PRECISION"),
        }
        columns.append(
            {
                "name": row["COLUMN_NAME"],
                "data_type": _format_data_type(raw),
                "nullable": row["IS_NULLABLE"] == "YES",
                "ordinal_position": row["ORDINAL_POSITION"],
            }
        )
    return columns


def get_primary_keys(db: AzureSqlDatabase, table_name: str) -> list[str]:
    result = db.execute(
        """
        SELECT kcu.COLUMN_NAME
        FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS AS tc
        INNER JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE AS kcu
            ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
            AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA
            AND tc.TABLE_NAME = kcu.TABLE_NAME
        WHERE tc.TABLE_SCHEMA = ?
            AND tc.TABLE_NAME = ?
            AND tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
        ORDER BY kcu.ORDINAL_POSITION
        """,
        ("Silver", table_name),
    )
    return [row["COLUMN_NAME"] for row in result.rows]


def get_row_count_estimates(db: AzureSqlDatabase) -> dict[str, int]:
    try:
        result = db.execute(
            """
            SELECT
                t.name AS TABLE_NAME,
                SUM(p.rows) AS ROW_COUNT
            FROM sys.tables AS t
            INNER JOIN sys.schemas AS s ON t.schema_id = s.schema_id
            INNER JOIN sys.partitions AS p ON t.object_id = p.object_id
            WHERE s.name = ?
                AND p.index_id IN (0, 1)
            GROUP BY t.name
            """,
            ("Silver",),
        )
        return {row["TABLE_NAME"]: int(row["ROW_COUNT"] or 0) for row in result.rows}
    except Exception as exc:
        logger.warning("Could not read row count estimates: %s", exc)
        return {}


def build_table_entry(
    table_name: str,
    columns: list[dict[str, Any]],
    primary_keys: list[str],
    row_count_estimate: int | None = None,
    indexes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "table_name": f"Silver.{table_name}",
        "description": _description_for_table(table_name),
        "columns": columns,
        "data_types": [
            {"column_name": column["name"], "data_type": column["data_type"]}
            for column in columns
        ],
        "primary_keys": primary_keys,
        "date_columns": _date_columns(columns),
        "row_count_estimate": row_count_estimate,
        "indexes": indexes or [],
    }


def inspect_silver_schema(db: AzureSqlDatabase) -> dict[str, Any]:
    tables = get_silver_table_names(db)
    row_counts = get_row_count_estimates(db)
    catalog_tables: list[dict[str, Any]] = []

    for table_name in tables:
        columns = get_columns(db, table_name)
        primary_keys = get_primary_keys(db, table_name)
        catalog_tables.append(
            build_table_entry(
                table_name=table_name,
                columns=columns,
                primary_keys=primary_keys,
                row_count_estimate=row_counts.get(table_name),
                indexes=get_indexes(db, table_name),
            )
        )

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "schema": "Silver",
        "table_count": len(catalog_tables),
        "tables": catalog_tables,
    }


def resolve_silver_table_name(db: AzureSqlDatabase, requested_table_name: str) -> str:
    normalized = normalize_table_name(requested_table_name)
    table_names = get_silver_table_names(db)
    for table_name in table_names:
        if table_name.lower() == normalized.lower():
            return table_name
    raise ValueError(f"Silver table not found: {requested_table_name}")


def get_indexes(db: AzureSqlDatabase, table_name: str) -> list[dict[str, Any]]:
    result = db.execute(
        """
        SELECT
            i.name AS index_name,
            i.type_desc AS index_type,
            i.is_unique AS is_unique,
            i.is_primary_key AS is_primary_key,
            ic.is_included_column AS is_included,
            ic.key_ordinal AS key_ordinal,
            c.name AS column_name
        FROM sys.indexes AS i
        INNER JOIN sys.index_columns AS ic
            ON i.object_id = ic.object_id AND i.index_id = ic.index_id
        INNER JOIN sys.columns AS c
            ON ic.object_id = c.object_id AND ic.column_id = c.column_id
        INNER JOIN sys.tables AS t ON i.object_id = t.object_id
        INNER JOIN sys.schemas AS s ON t.schema_id = s.schema_id
        WHERE s.name = ? AND t.name = ? AND i.type > 0 AND i.name IS NOT NULL
        ORDER BY i.name, ic.is_included_column, ic.key_ordinal
        """,
        ("Silver", table_name),
    )
    indexes: dict[str, dict[str, Any]] = {}
    for row in result.rows:
        name = row["index_name"]
        entry = indexes.setdefault(
            name,
            {
                "index_name": name,
                "index_type": row["index_type"],
                "is_unique": bool(row["is_unique"]),
                "is_primary_key": bool(row["is_primary_key"]),
                "key_columns": [],
                "included_columns": [],
            },
        )
        if row["is_included"]:
            entry["included_columns"].append(row["column_name"])
        else:
            entry["key_columns"].append(row["column_name"])
    return list(indexes.values())


def describe_table_indexes(db: AzureSqlDatabase, requested_table_name: str) -> dict[str, Any]:
    table_name = resolve_silver_table_name(db, requested_table_name)
    return {
        "table_name": f"Silver.{table_name}",
        "qualified_name": quote_silver_table(table_name),
        "indexes": get_indexes(db, table_name),
    }


def describe_silver_table(
    db: AzureSqlDatabase,
    requested_table_name: str,
    *,
    sample_limit: int,
) -> dict[str, Any]:
    table_name = resolve_silver_table_name(db, requested_table_name)
    row_counts = get_row_count_estimates(db)
    columns = get_columns(db, table_name)
    primary_keys = get_primary_keys(db, table_name)
    sample = db.sample_rows(table_name, sample_limit)
    entry = build_table_entry(
        table_name=table_name,
        columns=columns,
        primary_keys=primary_keys,
        row_count_estimate=row_counts.get(table_name),
        indexes=get_indexes(db, table_name),
    )
    entry["sample_rows"] = sample.rows
    entry["sample_row_count"] = sample.row_count
    entry["qualified_name"] = quote_silver_table(table_name)
    return entry
