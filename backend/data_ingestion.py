"""Dynamic user file upload, preprocessing, intelligent column mapping, and isolated session loading.

Supports CSV, Excel (.xlsx via openpyxl), and legacy Excel (.xls via xlrd when available).
Never silently fabricates missing business metrics (prices, sales history, supplier scores, etc.).
"""

from __future__ import annotations

import atexit
import io
import json
import logging
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, BinaryIO, Optional, Union

import numpy as np
import pandas as pd

from config import MAX_UPLOAD_FILE_SIZE_BYTES, MAX_UPLOAD_FILE_SIZE_MB, MAX_UPLOAD_ROWS
from database.db import get_connection, init_db
from utils.helpers import utc_now_iso

logger = logging.getLogger(__name__)
_ACTIVE_SESSION_DIRS: set[str] = set()


def _cleanup_all_registered_dirs() -> None:
    for d in list(_ACTIVE_SESSION_DIRS):
        cleanup_session_temp_dir(d)


atexit.register(_cleanup_all_registered_dirs)


SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls"}

# Canonical fields Stock Sentry understands, with clear labels & synonym matching rules
CANONICAL_FIELDS: list[dict[str, Any]] = [
    {
        "key": "product_id",
        "label": "Product ID / SKU",
        "group": "Core Product",
        "required_hint": "Recommended (auto-derived from Product Name if absent)",
        "type": "text",
        "synonyms": [
            "product_id", "sku", "sku_id", "item_code", "item_id", "product_code",
            "part_no", "part_number", "article_id", "article_code", "code", "barcode", "id",
        ],
    },
    {
        "key": "product_name",
        "label": "Product Name",
        "group": "Core Product",
        "required_hint": "Required (or Product ID)",
        "type": "text",
        "synonyms": [
            "product_name", "item_name", "product", "item", "name", "title",
            "description", "product_description", "sku_name", "goods", "material",
        ],
    },
    {
        "key": "category",
        "label": "Category",
        "group": "Core Product",
        "required_hint": "Optional",
        "type": "text",
        "synonyms": [
            "category", "product_category", "department", "dept", "group",
            "segment", "type", "item_category", "class", "brand",
        ],
    },
    {
        "key": "current_stock",
        "label": "Stock Quantity / Available Units",
        "group": "Inventory",
        "required_hint": "Required for inventory monitoring & reorder math",
        "type": "int",
        "synonyms": [
            "current_stock", "stock_quantity", "stock_qty", "available_units",
            "available_stock", "inventory", "inventory_qty", "qty_on_hand",
            "on_hand", "quantity_on_hand", "closing_stock", "stock", "qty",
            "balance_qty", "units_in_stock", "in_stock",
        ],
    },
    {
        "key": "unit",
        "label": "Unit / UOM",
        "group": "Inventory",
        "required_hint": "Optional",
        "type": "text",
        "synonyms": ["unit", "uom", "unit_of_measure", "pack_unit", "measure", "packaging"],
    },
    {
        "key": "cost_price",
        "label": "Unit Cost / Purchase Price",
        "group": "Pricing",
        "required_hint": "Required for cost valuation & procurement budgets",
        "type": "float",
        "synonyms": [
            "cost_price", "unit_cost", "purchase_price", "buying_price",
            "cost", "landing_cost", "wholesale_cost", "procurement_cost",
            "unit_purchase_price", "buy_price", "cp",
        ],
    },
    {
        "key": "selling_price",
        "label": "Selling Price / MRP",
        "group": "Pricing",
        "required_hint": "Optional (enables retail valuation & sales revenue)",
        "type": "float",
        "synonyms": [
            "selling_price", "sale_price", "retail_price", "mrp", "price",
            "unit_selling_price", "sp", "list_price", "customer_price",
        ],
    },
    {
        "key": "reorder_point",
        "label": "Reorder Point / Min Stock",
        "group": "Inventory Thresholds",
        "required_hint": "Enables low-stock alerts",
        "type": "int",
        "synonyms": [
            "reorder_point", "reorder_level", "min_stock", "minimum_stock",
            "min_qty", "par_level", "safety_level", "rop", "threshold",
        ],
    },
    {
        "key": "target_stock",
        "label": "Target Stock / Max Stock",
        "group": "Inventory Thresholds",
        "required_hint": "Enables overstock detection & exact order quantities",
        "type": "int",
        "synonyms": [
            "target_stock", "max_stock", "maximum_stock", "optimal_stock",
            "max_qty", "target_qty", "capacity", "stock_capacity",
        ],
    },
    {
        "key": "units_sold",
        "label": "Units Sold / Sales / Demand",
        "group": "Sales & Demand",
        "required_hint": "Enables sales analytics & demand forecasting",
        "type": "int",
        "synonyms": [
            "units_sold", "quantity_sold", "qty_sold", "sales", "sales_qty",
            "demand", "sold", "sold_qty", "order_qty", "daily_sales", "volume",
        ],
    },
    {
        "key": "transaction_date",
        "label": "Order / Transaction Date",
        "group": "Sales & Demand",
        "required_hint": "Required with Units Sold for time-series forecasting",
        "type": "date",
        "synonyms": [
            "date", "order_date", "transaction_date", "sale_date", "sales_date",
            "invoice_date", "bill_date", "timestamp", "created_at", "day", "period",
        ],
    },
    {
        "key": "supplier_name",
        "label": "Supplier / Vendor Name",
        "group": "Supplier",
        "required_hint": "Required for Supplier Analysis",
        "type": "text",
        "synonyms": [
            "supplier_name", "supplier", "vendor", "vendor_name", "distributor",
            "wholesaler", "manufacturer", "source", "party_name",
        ],
    },
    {
        "key": "supplier_id",
        "label": "Supplier / Vendor ID",
        "group": "Supplier",
        "required_hint": "Optional (auto-derived from Supplier Name if absent)",
        "type": "text",
        "synonyms": ["supplier_id", "vendor_id", "vendor_code", "supplier_code"],
    },
    {
        "key": "supplier_unit_price",
        "label": "Supplier Unit Price",
        "group": "Supplier",
        "required_hint": "Optional (falls back to Cost Price for supplier comparison if absent)",
        "type": "float",
        "synonyms": [
            "supplier_price", "supplier_unit_price", "vendor_price", "wholesale_price",
            "quote_price", "unit_price", "rate",
        ],
    },
    {
        "key": "delivery_days",
        "label": "Delivery / Lead Time (Days)",
        "group": "Supplier",
        "required_hint": "Optional (improves supplier ranking & reorder points)",
        "type": "int",
        "synonyms": [
            "delivery_days", "lead_time", "lead_time_days", "delivery_time",
            "transit_days", "shipping_days", "eta_days", "turnaround_days",
        ],
    },
    {
        "key": "supplier_available_qty",
        "label": "Supplier Available Quantity",
        "group": "Supplier",
        "required_hint": "Optional",
        "type": "int",
        "synonyms": [
            "available_quantity", "supplier_stock", "supplier_available_qty",
            "vendor_stock", "vendor_qty", "supply_capacity",
        ],
    },
    {
        "key": "reliability_score",
        "label": "Supplier Reliability Score (0-100)",
        "group": "Supplier",
        "required_hint": "Optional",
        "type": "float",
        "synonyms": [
            "reliability_score", "reliability", "vendor_rating", "supplier_rating",
            "on_time_rate", "otif", "score", "fulfillment_rate",
        ],
    },
    {
        "key": "min_order_qty",
        "label": "Minimum Order Quantity (MOQ)",
        "group": "Supplier",
        "required_hint": "Optional",
        "type": "int",
        "synonyms": ["min_order_qty", "moq", "minimum_order_quantity", "minimum_order_qty"],
    },
    {
        "key": "movement_type",
        "label": "Stock Movement Type",
        "group": "Stock Movements",
        "required_hint": "Optional (RESTOCK / SALE / ADJUSTMENT / DAMAGE / RETURN)",
        "type": "text",
        "synonyms": ["movement_type", "transaction_type", "event_type", "stock_action", "activity_type"],
    },
    {
        "key": "movement_quantity",
        "label": "Stock Movement Quantity",
        "group": "Stock Movements",
        "required_hint": "Optional (signed stock change)",
        "type": "int",
        "synonyms": ["movement_quantity", "movement_qty", "stock_change", "adjustment_qty", "delta", "change_qty"],
    },
    {
        "key": "remarks",
        "label": "Remarks / Notes",
        "group": "Stock Movements",
        "required_hint": "Optional",
        "type": "text",
        "synonyms": ["remarks", "notes", "comment", "comments", "reason"],
    },
]

CANONICAL_FIELD_MAP = {f["key"]: f for f in CANONICAL_FIELDS}


class DataIngestionError(ValueError):
    """Raised when an uploaded file cannot be parsed or fails critical validation."""


def normalize_column_name(col: Any) -> str:
    """Normalize a column header into lowercase snake_case for matching."""
    text = str(col).strip().lower()
    text = re.sub(r"[\s\-/\.]+", "_", text)
    text = re.sub(r"[^a-z0-9_]", "", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "unnamed_col"


def format_file_size(num_bytes: int) -> str:
    """Format byte size into human-readable B / KB / MB string."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.2f} MB"


def _read_bytes(source: Union[bytes, BinaryIO, str, Path], filename: Optional[str] = None) -> tuple[bytes, str]:
    if isinstance(source, (str, Path)):
        path = Path(source)
        if not path.exists():
            raise DataIngestionError(f"File not found: {path}")
        return path.read_bytes(), filename or path.name
    if isinstance(source, bytes):
        return source, filename or "uploaded_file.csv"
    # File-like object (e.g. Streamlit UploadedFile or BytesIO)
    pos = source.tell() if hasattr(source, "tell") else None
    raw_bytes = source.read()
    if pos is not None and hasattr(source, "seek"):
        source.seek(pos)
    inferred_name = filename or getattr(source, "name", "uploaded_file.csv")
    return raw_bytes, str(inferred_name)


def list_excel_sheets(source: Union[bytes, BinaryIO, str, Path], filename: Optional[str] = None) -> list[str]:
    """Return list of sheet names for an Excel (.xlsx / .xls) file."""
    raw_bytes, fname = _read_bytes(source, filename)
    ext = Path(fname).suffix.lower()
    if ext not in {".xlsx", ".xls"}:
        return []
    engine = "openpyxl" if ext == ".xlsx" else "xlrd"
    try:
        xls = pd.ExcelFile(io.BytesIO(raw_bytes), engine=engine)
        return list(xls.sheet_names)
    except ImportError as exc:
        raise DataIngestionError(
            f"Reading '{ext}' files requires the '{engine}' package. "
            f"Install it with `pip install {engine}` or save the file as CSV/XLSX."
        ) from exc
    except Exception as exc:
        logger.warning("Failed opening Excel file %s: %s", fname, exc)
        raise DataIngestionError(f"Could not open Excel file '{fname}'. Please verify it is a valid, unencrypted Excel workbook.") from exc


def read_uploaded_file(
    source: Union[bytes, BinaryIO, str, Path],
    filename: Optional[str] = None,
    sheet_name: Optional[Union[str, int]] = 0,
) -> dict[str, Any]:
    """Read CSV, XLSX, or XLS data into a DataFrame while preserving original content.

    Returns metadata dict with raw_df, file_name, file_size_bytes, file_size_label,
    file_type, sheet_names, selected_sheet, and warnings.
    """
    raw_bytes, raw_fname = _read_bytes(source, filename)
    # Strip any directory components to prevent path-traversal tricks in filenames
    fname = Path(str(raw_fname).replace("\\", "/")).name or "uploaded_file.csv"
    size_bytes = len(raw_bytes)
    ext = Path(fname).suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        raise DataIngestionError(
            f"Unsupported file format '{ext or 'unknown'}'. Please upload a CSV (.csv) or Excel (.xlsx / .xls) file."
        )

    if size_bytes == 0:
        raise DataIngestionError(f"The uploaded file '{fname}' is empty (0 bytes).")

    if size_bytes > MAX_UPLOAD_FILE_SIZE_BYTES:
        raise DataIngestionError(
            f"Uploaded file '{fname}' ({format_file_size(size_bytes)}) exceeds the maximum allowed size of "
            f"{MAX_UPLOAD_FILE_SIZE_MB:.0f} MB."
        )

    warnings: list[str] = []
    sheet_names: list[str] = []
    selected_sheet: Optional[str] = None

    if ext == ".csv":
        df = None
        last_err: Optional[Exception] = None
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                df = pd.read_csv(io.BytesIO(raw_bytes), encoding=encoding)
                if encoding not in ("utf-8-sig", "utf-8"):
                    warnings.append(f"File decoded using '{encoding}' fallback encoding.")
                break
            except UnicodeDecodeError as exc:
                last_err = exc
                continue
            except pd.errors.EmptyDataError as exc:
                raise DataIngestionError(f"The CSV file '{fname}' contains no data rows or headers.") from exc
            except Exception as exc:
                logger.warning("Failed parsing CSV file %s: %s", fname, exc)
                raise DataIngestionError(f"Failed to parse CSV file '{fname}'. Please check delimiter formatting and column headers.") from exc
        if df is None:
            logger.warning("Failed decoding CSV file %s: %s", fname, last_err)
            raise DataIngestionError(f"Could not decode CSV file '{fname}'. Please save the file using UTF-8 encoding.")
    else:
        engine = "openpyxl" if ext == ".xlsx" else "xlrd"
        try:
            xls = pd.ExcelFile(io.BytesIO(raw_bytes), engine=engine)
            sheet_names = list(xls.sheet_names)
            if not sheet_names:
                raise DataIngestionError(f"Excel workbook '{fname}' contains no sheets.")
            target_sheet = sheet_name if sheet_name in sheet_names else sheet_names[0]
            if isinstance(sheet_name, int) and 0 <= sheet_name < len(sheet_names):
                target_sheet = sheet_names[sheet_name]
            selected_sheet = str(target_sheet)
            df = pd.read_excel(xls, sheet_name=target_sheet)
        except ImportError as exc:
            raise DataIngestionError(
                f"Reading '{ext}' files requires '{engine}'. Run `pip install {engine}` or upload CSV/XLSX."
            ) from exc
        except DataIngestionError:
            raise
        except Exception as exc:
            logger.warning("Failed reading Excel file %s: %s", fname, exc)
            raise DataIngestionError(f"Failed to read Excel file '{fname}'. Please verify the workbook is not corrupted or password-protected.") from exc

    # Drop completely empty unnamed columns/rows that often appear in Excel exports
    df = df.dropna(how="all").reset_index(drop=True)
    unnamed_empty = [c for c in df.columns if str(c).startswith("Unnamed:") and df[c].isna().all()]
    if unnamed_empty:
        df = df.drop(columns=unnamed_empty)
        warnings.append(f"Ignored {len(unnamed_empty)} completely blank unnamed column(s).")

    # Handle duplicate column names cleanly
    seen_cols: dict[str, int] = {}
    unique_cols: list[str] = []
    dup_col_count = 0
    for c in df.columns:
        cname = str(c).strip()
        if cname in seen_cols:
            seen_cols[cname] += 1
            unique_cols.append(f"{cname}_{seen_cols[cname]}")
            dup_col_count += 1
        else:
            seen_cols[cname] = 1
            unique_cols.append(cname)
    if dup_col_count > 0:
        df.columns = unique_cols
        warnings.append(f"Renamed {dup_col_count} duplicate column header(s) to keep them distinct.")
    else:
        df.columns = unique_cols

    if df.empty or len(df.columns) == 0:
        raise DataIngestionError(
            f"The uploaded file '{fname}' has headers but 0 data rows (or no usable columns)."
        )

    if len(df) > MAX_UPLOAD_ROWS:
        raise DataIngestionError(
            f"Uploaded file '{fname}' contains {len(df):,} rows, exceeding the safe limit of {MAX_UPLOAD_ROWS:,} rows."
        )

    return {
        "file_name": fname,
        "file_size_bytes": size_bytes,
        "file_size_label": format_file_size(size_bytes),
        "file_type": ext.lstrip(".").upper(),
        "sheet_names": sheet_names,
        "selected_sheet": selected_sheet,
        "raw_df": df.copy(),
        "read_warnings": warnings,
    }


def analyze_dataframe_quality(df: pd.DataFrame) -> dict[str, Any]:
    """Generate a comprehensive Data Quality audit for an uploaded DataFrame."""
    total_rows = int(len(df))
    total_cols = int(len(df.columns))
    total_cells = max(1, total_rows * total_cols)

    # Treat whitespace-only strings as missing for accurate profiling
    profile_df = df.replace(r"^\s*$", np.nan, regex=True)
    missing_per_col = profile_df.isna().sum()
    total_missing = int(missing_per_col.sum())

    exact_dup_mask = df.duplicated(keep=False)
    duplicate_rows_count = int(df.duplicated(keep="first").sum())
    duplicate_preview_df = df[exact_dup_mask].head(50).copy()

    col_records = []
    for col in df.columns:
        series = profile_df[col]
        miss = int(series.isna().sum())
        non_null = total_rows - miss
        miss_pct = round((miss / total_rows) * 100.0, 2) if total_rows > 0 else 0.0
        uniq = int(series.nunique(dropna=True))
        samples = [str(v)[:40] for v in series.dropna().head(3).tolist()]
        col_records.append(
            {
                "Original Column": str(col),
                "Normalized Name": normalize_column_name(col),
                "Detected Type": str(df[col].dtype),
                "Non-Null": non_null,
                "Missing": miss,
                "Missing (%)": miss_pct,
                "Unique Values": uniq,
                "Sample Values": ", ".join(samples) if samples else "—",
            }
        )

    column_info_df = pd.DataFrame(col_records)
    missing_summary_df = (
        column_info_df[column_info_df["Missing"] > 0][["Original Column", "Missing", "Missing (%)"]]
        .sort_values("Missing", ascending=False)
        .reset_index(drop=True)
    )

    warnings: list[str] = []
    if duplicate_rows_count > 0:
        warnings.append(
            f"Detected {duplicate_rows_count} exact duplicate row(s). Exact duplicates are deduplicated during analysis while preserving your original preview."
        )
    if total_missing > 0:
        warnings.append(
            f"Detected {total_missing} missing cell(s) across {len(missing_summary_df)} column(s). Missing business metrics are NEVER fabricated."
        )

    completeness_pct = round(((total_cells - total_missing) / total_cells) * 100.0, 1)

    return {
        "total_rows": total_rows,
        "total_columns": total_cols,
        "total_missing_cells": total_missing,
        "completeness_pct": completeness_pct,
        "duplicate_rows_count": duplicate_rows_count,
        "duplicate_preview_df": duplicate_preview_df,
        "column_info_df": column_info_df,
        "missing_summary_df": missing_summary_df,
        "warnings": warnings,
    }


def suggest_column_mapping(columns: list[str]) -> dict[str, Optional[str]]:
    """Auto-detect likely mappings from user column names to Stock Sentry canonical fields."""
    norm_to_orig = {normalize_column_name(c): str(c) for c in columns}
    used_originals: set[str] = set()
    mapping: dict[str, Optional[str]] = {f["key"]: None for f in CANONICAL_FIELDS}

    # Pass 1: Exact synonym matches
    for field in CANONICAL_FIELDS:
        for syn in field["synonyms"]:
            if syn in norm_to_orig and norm_to_orig[syn] not in used_originals:
                mapping[field["key"]] = norm_to_orig[syn]
                used_originals.add(norm_to_orig[syn])
                break

    # Pass 2: Substring / partial token matches for remaining unmapped fields
    for field in CANONICAL_FIELDS:
        if mapping[field["key"]] is not None:
            continue
        for norm_col, orig_col in norm_to_orig.items():
            if orig_col in used_originals:
                continue
            for syn in field["synonyms"]:
                if len(syn) >= 4 and (syn in norm_col or norm_col in syn):
                    mapping[field["key"]] = orig_col
                    used_originals.add(orig_col)
                    break
            if mapping[field["key"]] is not None:
                break

    return mapping


def _clean_numeric_series(series: pd.Series) -> tuple[pd.Series, int]:
    """Parse numeric values safely (stripping currency symbols & commas). Never fabricates."""
    cleaned_text = (
        series.astype(str)
        .str.strip()
        .replace({"": np.nan, "nan": np.nan, "None": np.nan, "null": np.nan, "N/A": np.nan, "-": np.nan})
    )
    non_empty_before = int(cleaned_text.notna().sum())
    stripped = cleaned_text.str.replace(r"[₹$€£,\s]", "", regex=True)
    numeric = pd.to_numeric(stripped, errors="coerce")
    invalid_count = max(0, non_empty_before - int(numeric.notna().sum()))
    return numeric, invalid_count


def _clean_date_series(series: pd.Series) -> tuple[pd.Series, int]:
    """Parse dates safely without crashing on mixed/invalid strings."""
    cleaned_text = (
        series.astype(str)
        .str.strip()
        .replace({"": np.nan, "nan": np.nan, "None": np.nan, "NaT": np.nan, "null": np.nan})
    )
    non_empty_before = int(cleaned_text.notna().sum())
    parsed = pd.to_datetime(cleaned_text, errors="coerce")
    invalid_count = max(0, non_empty_before - int(parsed.notna().sum()))
    return parsed, invalid_count


def validate_and_preprocess_dataset(
    raw_df: pd.DataFrame,
    mapping: dict[str, Optional[str]],
) -> dict[str, Any]:
    """Validate user column mapping, clean data without fabricating values, and assess capabilities."""
    errors: list[str] = []
    warnings: list[str] = []

    active_mapping = {
        k: v for k, v in mapping.items()
        if k in CANONICAL_FIELD_MAP and v is not None and v in raw_df.columns
    }

    # Check duplicate assignments (same user column mapped to multiple fields except allowed cost/supplier price)
    assigned_cols: dict[str, list[str]] = {}
    for canonical, user_col in active_mapping.items():
        assigned_cols.setdefault(user_col, []).append(canonical)
    for user_col, fields in assigned_cols.items():
        if len(fields) > 1 and set(fields) != {"cost_price", "supplier_unit_price"}:
            warnings.append(
                f"Column '{user_col}' is mapped to multiple fields ({', '.join(fields)}). Verify this is intentional."
            )

    if "product_name" not in active_mapping and "product_id" not in active_mapping:
        errors.append(
            "You must map at least 'Product Name' or 'Product ID / SKU' so Stock Sentry can identify your items."
        )

    if errors:
        return {
            "valid": False,
            "errors": errors,
            "warnings": warnings,
            "cleaned_df": pd.DataFrame(),
            "capabilities": {},
        }

    # Work on a copy; never mutate caller's raw_df
    work = raw_df.copy()
    exact_dups = int(work.duplicated(keep="first").sum())
    if exact_dups > 0:
        work = work.drop_duplicates(keep="first").reset_index(drop=True)
        warnings.append(f"Removed {exact_dups} exact duplicate row(s) during preprocessing.")

    cleaned = pd.DataFrame(index=work.index)

    # Extract & clean each mapped canonical field
    for field in CANONICAL_FIELDS:
        key = field["key"]
        ftype = field["type"]
        user_col = active_mapping.get(key)
        if not user_col:
            continue

        if ftype == "text":
            s = work[user_col].astype(str).str.strip()
            s = s.replace({"": np.nan, "nan": np.nan, "None": np.nan, "null": np.nan})
            cleaned[key] = s
        elif ftype in {"int", "float"}:
            num_s, inv_cnt = _clean_numeric_series(work[user_col])
            if inv_cnt > 0:
                warnings.append(
                    f"Column '{user_col}' ({field['label']}) had {inv_cnt} non-numeric value(s) coerced to missing."
                )
            if key in {"current_stock", "reorder_point", "target_stock", "units_sold", "cost_price", "selling_price", "supplier_unit_price", "delivery_days", "supplier_available_qty", "min_order_qty"}:
                neg_cnt = int((num_s < 0).sum())
                if neg_cnt > 0:
                    warnings.append(
                        f"Column '{user_col}' ({field['label']}) had {neg_cnt} negative value(s) excluded/set to missing."
                    )
                    num_s = num_s.where(num_s >= 0, np.nan)
            if key in {"cost_price", "selling_price", "supplier_unit_price", "target_stock", "delivery_days", "min_order_qty"}:
                zero_cnt = int((num_s == 0).sum())
                if zero_cnt > 0:
                    warnings.append(
                        f"Column '{user_col}' ({field['label']}) had {zero_cnt} zero value(s) ignored for positive-only metric."
                    )
                    num_s = num_s.where(num_s > 0, np.nan)
            if key == "reliability_score":
                # If reliability looks like 0-1 ratio (e.g., 0.95), scale transparently to 0-100
                valid_rel = num_s.dropna()
                if not valid_rel.empty and valid_rel.max() <= 1.0 and valid_rel.min() >= 0.0:
                    num_s = (num_s * 100.0).round(2)
                    warnings.append(
                        f"Column '{user_col}' appeared to be a 0–1 proportion and was scaled to 0–100%."
                    )
                out_range = int(((num_s < 0) | (num_s > 100)).sum())
                if out_range > 0:
                    warnings.append(
                        f"Column '{user_col}' had {out_range} reliability score(s) outside 0–100 ignored."
                    )
                    num_s = num_s.where((num_s >= 0) & (num_s <= 100), np.nan)
            cleaned[key] = num_s
        elif ftype == "date":
            dt_s, inv_cnt = _clean_date_series(work[user_col])
            if inv_cnt > 0:
                warnings.append(
                    f"Column '{user_col}' ({field['label']}) had {inv_cnt} unparseable date value(s) ignored."
                )
            cleaned[key] = dt_s

    # Ensure product_name and product_id exist consistently
    if "product_name" not in cleaned.columns and "product_id" in cleaned.columns:
        cleaned["product_name"] = cleaned["product_id"]
    elif "product_name" in cleaned.columns and "product_id" in cleaned.columns:
        cleaned["product_name"] = cleaned["product_name"].fillna(cleaned["product_id"])

    if "product_id" not in cleaned.columns:
        # Derive deterministic SKU IDs from distinct product names
        name_to_id: dict[str, str] = {}
        derived_ids = []
        counter = 1
        for nm in cleaned["product_name"]:
            if pd.isna(nm):
                derived_ids.append(np.nan)
                continue
            key_nm = str(nm).strip()
            if key_nm not in name_to_id:
                name_to_id[key_nm] = f"SKU-{counter:04d}"
                counter += 1
            derived_ids.append(name_to_id[key_nm])
        cleaned["product_id"] = derived_ids
        warnings.append("No Product ID / SKU column mapped; generated stable SKU IDs (SKU-0001...) from Product Name.")
    else:
        # Fill missing IDs from product names if partial
        missing_pid = cleaned["product_id"].isna() & cleaned["product_name"].notna()
        if missing_pid.any():
            for idx in cleaned[missing_pid].index:
                cleaned.at[idx, "product_id"] = f"SKU-ROW-{idx + 1}"

    # Drop rows with neither product_id nor product_name
    before_valid = len(cleaned)
    cleaned = cleaned.dropna(subset=["product_id", "product_name"]).reset_index(drop=True)
    dropped_id = before_valid - len(cleaned)
    if dropped_id > 0:
        warnings.append(f"Skipped {dropped_id} row(s) missing both Product ID and Product Name.")

    if cleaned.empty:
        errors.append("No valid product rows remain after removing blank rows.")
        return {
            "valid": False,
            "errors": errors,
            "warnings": warnings,
            "cleaned_df": pd.DataFrame(),
            "capabilities": {},
        }

    # Check if current_stock is mapped at all
    has_stock = bool("current_stock" in cleaned.columns and cleaned["current_stock"].notna().any())
    has_cost = bool("cost_price" in cleaned.columns and cleaned["cost_price"].notna().any())
    has_selling = bool("selling_price" in cleaned.columns and cleaned["selling_price"].notna().any())
    has_rop = bool("reorder_point" in cleaned.columns and cleaned["reorder_point"].notna().any())
    has_target = bool("target_stock" in cleaned.columns and cleaned["target_stock"].notna().any())
    has_sales = bool("units_sold" in cleaned.columns and (cleaned["units_sold"].fillna(0) > 0).any())
    has_dates = bool("transaction_date" in cleaned.columns and cleaned["transaction_date"].notna().any())
    distinct_dates = int(cleaned["transaction_date"].dt.normalize().nunique()) if has_dates else 0
    has_supplier = bool("supplier_name" in cleaned.columns and cleaned["supplier_name"].notna().any())
    has_sup_price = bool(
        ("supplier_unit_price" in cleaned.columns and cleaned["supplier_unit_price"].notna().any())
        or (has_supplier and has_cost)
    )
    has_delivery = bool("delivery_days" in cleaned.columns and cleaned["delivery_days"].notna().any())
    has_sup_avail = bool("supplier_available_qty" in cleaned.columns and cleaned["supplier_available_qty"].notna().any())
    has_reliability = bool("reliability_score" in cleaned.columns and cleaned["reliability_score"].notna().any())
    has_movements = bool(
        "movement_type" in cleaned.columns
        and "movement_quantity" in cleaned.columns
        and cleaned["movement_type"].notna().any()
        and cleaned["movement_quantity"].notna().any()
    )

    if not has_stock:
        warnings.append(
            "No valid 'Stock Quantity' column is mapped. Inventory counts, valuation, and stockout days cannot be calculated accurately."
        )

    # Honest capability matrix describing exactly what works and why anything is limited
    capabilities = {
        "inventory_monitoring": {
            "available": has_stock,
            "reason": (
                "Active using uploaded product and stock quantity columns."
                if has_stock
                else "Unavailable: Map a valid 'Stock Quantity / Available Units' column to monitor inventory levels."
            ),
        },
        "inventory_valuation": {
            "available": has_stock and (has_cost or has_selling),
            "has_cost_price": has_stock and has_cost,
            "has_selling_price": has_stock and has_selling,
            "reason": (
                "Active using uploaded stock quantity and pricing columns."
                if (has_stock and (has_cost or has_selling))
                else "Unavailable: Map 'Stock Quantity' along with 'Unit Cost' and/or 'Selling Price' to calculate inventory value."
            ),
        },
        "low_stock_alerts": {
            "available": has_stock and has_rop,
            "out_of_stock_available": has_stock,
            "reason": (
                "Active using uploaded stock quantity and reorder point thresholds."
                if (has_stock and has_rop)
                else (
                    "Partial: Out-of-stock (0 units) detection is active, but 'Reorder Point' is not mapped so low-stock thresholds are unavailable."
                    if has_stock
                    else "Unavailable: Map 'Stock Quantity' and 'Reorder Point'."
                )
            ),
        },
        "overstock_detection": {
            "available": has_stock and has_target,
            "reason": (
                "Active by comparing current stock against uploaded 'Target / Max Stock'."
                if (has_stock and has_target)
                else "Unavailable: Map a 'Target Stock / Max Stock' column to detect overstocked items without guessing thresholds."
            ),
        },
        "sales_analytics": {
            "available": has_sales,
            "has_revenue": has_sales and has_selling,
            "has_dates": has_sales and has_dates,
            "reason": (
                "Active using uploaded 'Units Sold / Sales' records."
                if has_sales
                else "Unavailable: Map a 'Units Sold / Sales' column to view sales analytics."
            ),
        },
        "demand_forecasting": {
            "available": has_sales and distinct_dates >= 2,
            "distinct_dates": distinct_dates,
            "reason": (
                f"Active using {distinct_dates} distinct historical sales dates (statistical SMA/EMA baselines)."
                if (has_sales and distinct_dates >= 2)
                else (
                    "Unavailable: Demand forecasting requires 'Units Sold' AND an 'Order / Transaction Date' column with at least 2 distinct dates."
                )
            ),
        },
        "anomaly_detection": {
            "available": (has_sales and distinct_dates >= 5) or has_movements or has_stock,
            "sales_spike_check": has_sales and distinct_dates >= 5,
            "movement_check": has_movements,
            "stock_outlier_check": has_stock and (has_target or has_rop),
            "reason": (
                "Active on available sales history, stock movements, and inventory thresholds."
                if ((has_sales and distinct_dates >= 5) or has_movements)
                else "Limited: Uploaded data lacks multi-day sales history (>=5 dates) and stock movement logs. Only threshold/outlier checks can run."
            ),
        },
        "supplier_analysis": {
            "available": has_supplier,
            "criteria": {
                "price": has_sup_price,
                "delivery": has_delivery,
                "reliability": has_reliability,
                "availability": has_sup_avail,
            },
            "reason": (
                "Active using uploaded supplier records (scoring uses ONLY provided supplier metrics)."
                if has_supplier
                else "Unavailable: Map a 'Supplier / Vendor Name' column to enable supplier comparison and ranking."
            ),
        },
        "procurement": {
            "available": has_stock and (has_target or has_rop or (has_sales and distinct_dates >= 2)),
            "has_supplier": has_supplier,
            "has_pricing": has_sup_price or has_cost,
            "reason": (
                "Active using uploaded stock levels and reorder/target thresholds (or forecasted demand)."
                if (has_stock and (has_target or has_rop or (has_sales and distinct_dates >= 2)))
                else "Unavailable: Procurement requires 'Stock Quantity' plus either 'Target Stock', 'Reorder Point', or dated sales history."
            ),
        },
    }

    return {
        "valid": True,
        "errors": errors,
        "warnings": warnings,
        "cleaned_df": cleaned,
        "active_mapping": active_mapping,
        "capabilities": capabilities,
    }


def _last_non_null(grp: pd.DataFrame, col: str) -> Any:
    """Return the last non-null value in column `col` of DataFrame group `grp`, or None."""
    if col in grp.columns and grp[col].notna().any():
        return grp[col].dropna().iloc[-1]
    return None


def create_isolated_session_db(
    cleaned_df: pd.DataFrame,
    capabilities: dict[str, Any],
    source_filename: str,
    previous_temp_dir: Optional[Union[str, Path]] = None,
) -> dict[str, Any]:
    """Load preprocessed user data into a brand-new, session-isolated temporary SQLite DB.

    Cleans up any previous temporary directory for the session so old data never lingers.
    Never modifies original project CSVs or default demo DB.
    """
    if previous_temp_dir:
        cleanup_session_temp_dir(previous_temp_dir)

    temp_dir = Path(tempfile.mkdtemp(prefix="stock_sentry_user_"))
    try:
        os.chmod(temp_dir, 0o700)
    except OSError:
        pass
    _ACTIVE_SESSION_DIRS.add(str(temp_dir))
    db_path = temp_dir / f"session_{uuid.uuid4().hex[:10]}.db"
    init_db(db_path)

    now = utc_now_iso()
    df = cleaned_df.copy()

    # Sort chronologically if transaction_date exists so latest snapshot per SKU is last
    if "transaction_date" in df.columns and df["transaction_date"].notna().any():
        df = df.sort_values("transaction_date", ascending=True, na_position="first").reset_index(drop=True)

    # 1. Build unique product catalog (taking latest non-null values per product_id)
    product_rows = []
    for pid, grp in df.groupby("product_id", sort=True):
        pname = str(_last_non_null(grp, "product_name") or pid)
        cat = str(_last_non_null(grp, "category") or "Uncategorized")
        unit = str(_last_non_null(grp, "unit") or "unit")
        cur_stock_val = _last_non_null(grp, "current_stock")
        cur_stock = max(0, int(round(float(cur_stock_val)))) if cur_stock_val is not None else 0
        sell_p = _last_non_null(grp, "selling_price")
        cost_p = _last_non_null(grp, "cost_price")
        rop = _last_non_null(grp, "reorder_point")
        target = _last_non_null(grp, "target_stock")

        product_rows.append(
            (
                str(pid),
                pname,
                cat,
                cur_stock,
                unit,
                round(float(sell_p), 2) if sell_p is not None else None,
                round(float(cost_p), 2) if cost_p is not None else None,
                int(round(float(rop))) if rop is not None else None,
                int(round(float(target))) if target is not None else None,
                now,
            )
        )

    # 2. Build sales table from actual positive units_sold rows (vectorized extraction)
    sales_rows = []
    if "units_sold" in df.columns:
        sales_sub = df[df["units_sold"].notna() & (df["units_sold"] > 0)]
        if not sales_sub.empty:
            pids_s = sales_sub["product_id"].astype(str).tolist()
            qtys_s = sales_sub["units_sold"].round().astype(int).tolist()
            if "transaction_date" in sales_sub.columns:
                dts_s = [d.strftime("%Y-%m-%d") if pd.notna(d) else None for d in sales_sub["transaction_date"]]
            else:
                dts_s = [None] * len(pids_s)
            if "selling_price" in sales_sub.columns:
                sps_s = [round(float(v), 2) if pd.notna(v) else None for v in sales_sub["selling_price"]]
            else:
                sps_s = [None] * len(pids_s)
            sales_rows = [
                (f"US-{idx:05d}", dt_str, pid_v, qty_v, sp_v)
                for idx, (pid_v, qty_v, dt_str, sp_v) in enumerate(zip(pids_s, qtys_s, dts_s, sps_s), start=1)
            ]

    # 3. Build suppliers table from actual supplier rows (never fabricating missing metrics!)
    supplier_rows = []
    if "supplier_name" in df.columns:
        sup_name_to_id: dict[str, str] = {}
        sup_counter = 1
        sub = df[df["supplier_name"].notna()].copy()
        for (sname, pid), grp in sub.groupby(["supplier_name", "product_id"], sort=True):
            sname_str = str(sname).strip()
            sid_raw = _last_non_null(grp, "supplier_id")
            if sid_raw is not None:
                sid = str(sid_raw).strip()
            else:
                if sname_str not in sup_name_to_id:
                    sup_name_to_id[sname_str] = f"SUP-{sup_counter:03d}"
                    sup_counter += 1
                sid = sup_name_to_id[sname_str]

            uprice = _last_non_null(grp, "supplier_unit_price")
            if uprice is None:
                uprice = _last_non_null(grp, "cost_price")
            ddays = _last_non_null(grp, "delivery_days")
            aqty = _last_non_null(grp, "supplier_available_qty")
            rel = _last_non_null(grp, "reliability_score")
            moq = _last_non_null(grp, "min_order_qty")

            supplier_rows.append(
                (
                    sid,
                    sname_str,
                    str(pid),
                    round(float(uprice), 2) if uprice is not None else None,
                    int(round(float(ddays))) if ddays is not None else None,
                    int(round(float(aqty))) if aqty is not None else None,
                    round(float(rel), 2) if rel is not None else None,
                    int(round(float(moq))) if moq is not None else None,
                )
            )

    # 4. Build stock_movements table if movement columns were mapped (vectorized extraction)
    movement_rows = []
    if "movement_type" in df.columns and "movement_quantity" in df.columns:
        mov_sub = df[df["movement_type"].notna() & df["movement_quantity"].notna()]
        mov_sub = mov_sub[mov_sub["movement_quantity"].round().astype(int) != 0]
        if not mov_sub.empty:
            pids_m = mov_sub["product_id"].astype(str).tolist()
            types_m = mov_sub["movement_type"].astype(str).str.strip().str.upper().tolist()
            qtys_m = mov_sub["movement_quantity"].round().astype(int).tolist()
            if "transaction_date" in mov_sub.columns:
                dts_m = [d.strftime("%Y-%m-%d") if pd.notna(d) else None for d in mov_sub["transaction_date"]]
            else:
                dts_m = [None] * len(pids_m)
            if "remarks" in mov_sub.columns:
                rems_m = [str(r)[:250] if pd.notna(r) else "" for r in mov_sub["remarks"]]
            else:
                rems_m = [""] * len(pids_m)
            movement_rows = [
                (f"UM-{idx:05d}", dt_str, pid_v, mt_v, q_v, rem_v)
                for idx, (pid_v, mt_v, q_v, dt_str, rem_v) in enumerate(
                    zip(pids_m, types_m, qtys_m, dts_m, rems_m), start=1
                )
            ]

    with get_connection(db_path) as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO products (
                product_id, product_name, category, current_stock, unit,
                selling_price, cost_price, reorder_point, target_stock, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            product_rows,
        )
        if sales_rows:
            conn.executemany(
                """
                INSERT OR IGNORE INTO sales (
                    sale_id, date, product_id, quantity_sold, selling_price
                ) VALUES (?, ?, ?, ?, ?)
                """,
                sales_rows,
            )
        if supplier_rows:
            conn.executemany(
                """
                INSERT OR REPLACE INTO suppliers (
                    supplier_id, supplier_name, product_id, unit_price,
                    delivery_days, available_quantity, reliability_score, min_order_qty
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                supplier_rows,
            )
        if movement_rows:
            conn.executemany(
                """
                INSERT OR IGNORE INTO stock_movements (
                    movement_id, date, product_id, movement_type, quantity, remarks
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                movement_rows,
            )
        conn.execute(
            "INSERT OR REPLACE INTO app_metadata (key, value) VALUES (?, ?)",
            ("dataset_mode", "uploaded"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_metadata (key, value) VALUES (?, ?)",
            ("source_filename", source_filename),
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_metadata (key, value) VALUES (?, ?)",
            ("capabilities_json", json.dumps(capabilities)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO app_metadata (key, value) VALUES (?, ?)",
            ("loaded_at", now),
        )

    return {
        "temp_dir": str(temp_dir),
        "db_path": str(db_path),
        "products_loaded": len(product_rows),
        "sales_loaded": len(sales_rows),
        "suppliers_loaded": len(supplier_rows),
        "movements_loaded": len(movement_rows),
        "loaded_at": now,
    }


def cleanup_session_temp_dir(temp_dir: Optional[Union[str, Path]]) -> None:
    """Safely remove a session's temporary SQLite directory."""
    if not temp_dir:
        return
    _ACTIVE_SESSION_DIRS.discard(str(temp_dir))
    try:
        p = Path(temp_dir)
        if p.exists() and p.is_dir() and "stock_sentry_user_" in p.name:
            shutil.rmtree(p, ignore_errors=True)
    except OSError as exc:
        logger.warning("Failed to remove temporary session directory %s: %s", temp_dir, exc)
