"""
Invoice Parser MCP Server
Parse invoices, receipts, and financial documents using Claude Vision.

Extracts structured JSON data from PDF or image files (PNG, JPG, WEBP).
Supports batch processing, line-item extraction, math validation, and CSV export.

Authentication: api_key (free/pro tier) OR payment_proof (x402 USDC on Base).
"""

import base64
import csv
import json
import logging
import mimetypes
import os
import sys
import time as _time
from pathlib import Path
from typing import Annotated

import anthropic
from pydantic import Field

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
from config import LOG_FILE, LOG_LEVEL, ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL, CLAUDE_MODEL

os.makedirs(os.path.dirname(LOG_FILE) if os.path.dirname(LOG_FILE) else ".", exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stderr),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
from auth import validate_and_charge  # noqa: E402

# ---------------------------------------------------------------------------
# x402 micropayments (per-tool pricing)
# ---------------------------------------------------------------------------
from x402 import (  # noqa: E402
    WALLET_ADDRESS,
    is_proof_used,
    mark_proof_used,
    payment_required_response,
    verify_payment,
)

# In-memory stats
import sqlite3 as _sqlite3
from datetime import datetime as _dt, timedelta as _td, timezone as _tz

_stats: dict = {
    "total_calls": 0,
    "errors": 0,
    "start_time": _time.time(),
    "tools_breakdown": {},
    "vision_calls": 0,
}


def _track_tool(tool_name: str) -> None:
    """Increment per-tool call counter."""
    _stats["tools_breakdown"][tool_name] = _stats["tools_breakdown"].get(tool_name, 0) + 1


# ---------------------------------------------------------------------------
# SQLite analytics logger
# ---------------------------------------------------------------------------
_ANALYTICS_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analytics.db")


def _init_analytics_db() -> None:
    """Create the analytics table if it doesn't exist."""
    conn = _sqlite3.connect(_ANALYTICS_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tool_calls (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tool_name   TEXT    NOT NULL,
            timestamp   TEXT    NOT NULL,
            payment_received INTEGER NOT NULL DEFAULT 0,
            amount_usdc REAL    NOT NULL DEFAULT 0.0,
            success     INTEGER NOT NULL DEFAULT 1,
            latency_ms  INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def _log_call(
    tool_name: str,
    payment_received: bool,
    amount_usdc: float,
    success: bool,
    latency_ms: int,
) -> None:
    """Insert one analytics row. Never raises — failures are logged silently."""
    try:
        conn = _sqlite3.connect(_ANALYTICS_DB)
        conn.execute(
            """
            INSERT INTO tool_calls
                (tool_name, timestamp, payment_received, amount_usdc, success, latency_ms)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                tool_name,
                _dt.now(_tz.utc).isoformat(),
                int(payment_received),
                amount_usdc,
                int(success),
                latency_ms,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("analytics log failed: %s", exc)


# Initialise DB at import time (no-op if table already exists)
try:
    _init_analytics_db()
except Exception as _exc:
    logger.warning("Could not initialise analytics DB: %s", _exc)

# Pricing per tool (USDC)
PRICE_PARSE = 0.05      # parse_invoice, parse_receipt
PRICE_EXTRACT = 0.01    # extract_line_items, extract_totals, validate_invoice
PRICE_EXPORT = 0.10     # export_to_csv (batch, multiple docs)

# ---------------------------------------------------------------------------
# Claude Vision client
# ---------------------------------------------------------------------------
_anthropic_client = None

def _get_client() -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        if not ANTHROPIC_API_KEY:
            raise RuntimeError(
                "ANTHROPIC_API_KEY environment variable is not set. "
                "On Railway: set it to a real Anthropic API key. "
                "Locally via maxproxy: set ANTHROPIC_API_KEY=maxproxy and "
                "ANTHROPIC_BASE_URL=http://localhost:3456"
            )
        kwargs: dict = {"api_key": ANTHROPIC_API_KEY}
        if ANTHROPIC_BASE_URL:
            kwargs["base_url"] = ANTHROPIC_BASE_URL
        _anthropic_client = anthropic.Anthropic(**kwargs)
    return _anthropic_client


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------
_PORT = int(os.environ.get("PORT", 8000))

mcp = FastMCP(
    "InvoiceParser",
    instructions=(
        "Parse invoices, receipts, and financial documents into structured JSON. "
        "Supports PDF and image files (PNG, JPG, WEBP). "
        "Use parse_invoice for vendor invoices, parse_receipt for retail receipts. "
        "Use extract_line_items or extract_totals for partial extraction. "
        "Use validate_invoice to check math. Use export_to_csv for batch processing."
    ),
    host="0.0.0.0",
    port=_PORT,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _err(msg: str) -> str:
    return json.dumps({"ok": False, "error": msg})


def _resolve_file(file_path: str) -> tuple[Path | None, str | None]:
    """Expand and validate file path. Returns (path, None) or (None, error)."""
    try:
        p = Path(file_path).expanduser().resolve()
    except Exception as e:
        return None, f"Invalid path: {e}"
    if not p.exists():
        return None, f"File not found: {p}"
    if not p.is_file():
        return None, f"Not a file: {p}"
    return p, None


def _file_to_vision_content(path: Path) -> dict:
    """Convert a file to an Anthropic vision content block."""
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        # Use base64 PDF document block
        data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": data,
            },
        }
    else:
        # Image file
        mime_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }
        media_type = mime_map.get(suffix, "image/jpeg")
        data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": data,
            },
        }


def _vision_call(file_path: Path, prompt: str) -> str:
    """Send a file + prompt to Claude Vision, return the text response."""
    _stats["vision_calls"] += 1
    client = _get_client()
    content_block = _file_to_vision_content(file_path)
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": [
                    content_block,
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    return response.content[0].text


def _auth_check(
    api_key: str | None,
    payment_proof: str | None,
    tool_name: str,
    price: float,
) -> str | None:
    """
    Unified auth check for all tools.
    Returns None if authorized, or a JSON error string if not.
    """
    # 1. API key path
    if api_key:
        ok, err = validate_and_charge(api_key)
        if not ok:
            return _err(err)
        return None

    # 2. x402 payment proof path
    if payment_proof:
        tx = payment_proof.strip()
        if is_proof_used(tx):
            return _err("Payment proof already used. Each transaction can only be used once.")
        ok, err = verify_payment(tx, price, WALLET_ADDRESS)
        if not ok:
            return _err(f"Payment verification failed: {err}")
        mark_proof_used(tx, tool_name)
        return None

    # 3. Neither — return payment instructions
    resp = payment_required_response(tool_name)
    resp["x402"]["amount_usdc"] = price
    resp["x402"]["amount_raw"] = int(price * 1_000_000)
    return json.dumps(resp)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def parse_invoice(
    file_path: Annotated[str, Field(description="Absolute path to the invoice PDF or image (PNG, JPG, WEBP).")],
    api_key: Annotated[str | None, Field(description="Your InvoiceParser API key.")] = None,
    payment_proof: Annotated[str | None, Field(description="x402 payment proof (Base tx hash). Alternative to api_key.")] = None,
) -> str:
    """
    Parse a vendor invoice into structured JSON.

    Extracts: vendor name, vendor address, invoice number, invoice date, due date,
    line items (description, quantity, unit price, total), subtotal, tax amount,
    tax rate, total amount, currency, payment terms, and notes.

    Supports PDF and image files. Returns a JSON object.
    Cost: $0.05 USDC per call (x402) or counts against your API key quota.
    """
    _stats["total_calls"] += 1
    _track_tool("parse_invoice")
    _t0 = _time.monotonic()
    _paid = bool(payment_proof)
    auth_err = _auth_check(api_key, payment_proof, "parse_invoice", PRICE_PARSE)
    if auth_err:
        _log_call("parse_invoice", False, 0.0, False, int((_time.monotonic() - _t0) * 1000))
        return auth_err

    path, err = _resolve_file(file_path)
    if err:
        _log_call("parse_invoice", _paid, PRICE_PARSE if _paid else 0.0, False, int((_time.monotonic() - _t0) * 1000))
        return _err(err)

    prompt = """Extract all invoice data from this document and return ONLY valid JSON with this exact structure:
{
  "ok": true,
  "document_type": "invoice",
  "vendor": {
    "name": "",
    "address": "",
    "email": "",
    "phone": "",
    "tax_id": ""
  },
  "bill_to": {
    "name": "",
    "address": "",
    "email": ""
  },
  "invoice_number": "",
  "invoice_date": "",
  "due_date": "",
  "payment_terms": "",
  "currency": "",
  "line_items": [
    {
      "description": "",
      "quantity": 0,
      "unit_price": 0.0,
      "total": 0.0,
      "tax_rate": 0.0
    }
  ],
  "subtotal": 0.0,
  "discount": 0.0,
  "tax_amount": 0.0,
  "shipping": 0.0,
  "total": 0.0,
  "amount_due": 0.0,
  "notes": "",
  "po_number": ""
}

Use null for any fields not found in the document. Return ONLY the JSON, no explanation."""

    try:
        result_text = _vision_call(path, prompt)
        # Extract JSON from response (Claude sometimes adds markdown)
        text = result_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
        parsed = json.loads(text)
        _log_call("parse_invoice", _paid, PRICE_PARSE if _paid else 0.0, True, int((_time.monotonic() - _t0) * 1000))
        return json.dumps(parsed, indent=2)
    except json.JSONDecodeError as e:
        logger.error("parse_invoice: JSON parse error: %s", e)
        _log_call("parse_invoice", _paid, PRICE_PARSE if _paid else 0.0, False, int((_time.monotonic() - _t0) * 1000))
        return json.dumps({"ok": True, "raw": result_text, "parse_error": str(e)})
    except Exception as e:
        logger.error("parse_invoice error: %s", e)
        _log_call("parse_invoice", _paid, PRICE_PARSE if _paid else 0.0, False, int((_time.monotonic() - _t0) * 1000))
        return _err(f"Vision API error: {e}")


@mcp.tool()
def parse_receipt(
    file_path: Annotated[str, Field(description="Absolute path to the receipt PDF or image (PNG, JPG, WEBP).")],
    api_key: Annotated[str | None, Field(description="Your InvoiceParser API key.")] = None,
    payment_proof: Annotated[str | None, Field(description="x402 payment proof (Base tx hash). Alternative to api_key.")] = None,
) -> str:
    """
    Parse a retail receipt or expense receipt into structured JSON.

    Extracts: merchant name, date, time, items purchased (name, quantity, price),
    subtotal, tax, total, payment method, and transaction ID.

    Supports PDF and image files. Returns a JSON object.
    Cost: $0.05 USDC per call (x402) or counts against your API key quota.
    """
    _stats["total_calls"] += 1
    _track_tool("parse_receipt")
    auth_err = _auth_check(api_key, payment_proof, "parse_receipt", PRICE_PARSE)
    if auth_err:
        return auth_err

    path, err = _resolve_file(file_path)
    if err:
        return _err(err)

    prompt = """Extract all receipt data from this document and return ONLY valid JSON with this exact structure:
{
  "ok": true,
  "document_type": "receipt",
  "merchant": {
    "name": "",
    "address": "",
    "phone": "",
    "website": ""
  },
  "date": "",
  "time": "",
  "receipt_number": "",
  "cashier": "",
  "items": [
    {
      "name": "",
      "quantity": 1,
      "unit_price": 0.0,
      "total": 0.0,
      "sku": "",
      "category": ""
    }
  ],
  "subtotal": 0.0,
  "discounts": 0.0,
  "tax": 0.0,
  "tip": 0.0,
  "total": 0.0,
  "currency": "",
  "payment_method": "",
  "card_last_four": "",
  "transaction_id": "",
  "loyalty_points": null,
  "notes": ""
}

Use null for any fields not found. Return ONLY the JSON, no explanation."""

    try:
        result_text = _vision_call(path, prompt)
        text = result_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
        parsed = json.loads(text)
        return json.dumps(parsed, indent=2)
    except json.JSONDecodeError as e:
        logger.error("parse_receipt: JSON parse error: %s", e)
        return json.dumps({"ok": True, "raw": result_text, "parse_error": str(e)})
    except Exception as e:
        logger.error("parse_receipt error: %s", e)
        return _err(f"Vision API error: {e}")


@mcp.tool()
def extract_line_items(
    file_path: Annotated[str, Field(description="Absolute path to the invoice or receipt PDF or image.")],
    api_key: Annotated[str | None, Field(description="Your InvoiceParser API key.")] = None,
    payment_proof: Annotated[str | None, Field(description="x402 payment proof (Base tx hash). Alternative to api_key.")] = None,
) -> str:
    """
    Extract only the line items from an invoice or receipt.

    Faster and cheaper than full parse when you only need the itemized list.
    Returns an array of line items with description, quantity, unit price, and total.

    Cost: $0.01 USDC per call (x402) or counts against your API key quota.
    """
    _stats["total_calls"] += 1
    _track_tool("extract_line_items")
    auth_err = _auth_check(api_key, payment_proof, "extract_line_items", PRICE_EXTRACT)
    if auth_err:
        return auth_err

    path, err = _resolve_file(file_path)
    if err:
        return _err(err)

    prompt = """Extract ONLY the line items from this document. Return ONLY valid JSON:
{
  "ok": true,
  "line_items": [
    {
      "description": "",
      "quantity": 0,
      "unit_price": 0.0,
      "total": 0.0
    }
  ],
  "item_count": 0
}

Return ONLY the JSON, no explanation."""

    try:
        result_text = _vision_call(path, prompt)
        text = result_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
        parsed = json.loads(text)
        return json.dumps(parsed, indent=2)
    except json.JSONDecodeError as e:
        return json.dumps({"ok": True, "raw": result_text, "parse_error": str(e)})
    except Exception as e:
        logger.error("extract_line_items error: %s", e)
        return _err(f"Vision API error: {e}")


@mcp.tool()
def extract_totals(
    file_path: Annotated[str, Field(description="Absolute path to the invoice or receipt PDF or image.")],
    api_key: Annotated[str | None, Field(description="Your InvoiceParser API key.")] = None,
    payment_proof: Annotated[str | None, Field(description="x402 payment proof (Base tx hash). Alternative to api_key.")] = None,
) -> str:
    """
    Extract only the financial totals from an invoice or receipt.

    Returns subtotal, tax, discount, shipping, and total — without line items.
    Useful when you need amounts quickly without parsing the full document.

    Cost: $0.01 USDC per call (x402) or counts against your API key quota.
    """
    _stats["total_calls"] += 1
    _track_tool("extract_totals")
    auth_err = _auth_check(api_key, payment_proof, "extract_totals", PRICE_EXTRACT)
    if auth_err:
        return auth_err

    path, err = _resolve_file(file_path)
    if err:
        return _err(err)

    prompt = """Extract ONLY the financial totals from this document. Return ONLY valid JSON:
{
  "ok": true,
  "currency": "",
  "subtotal": 0.0,
  "discount": 0.0,
  "tax_amount": 0.0,
  "tax_rate": 0.0,
  "shipping": 0.0,
  "tip": 0.0,
  "total": 0.0,
  "amount_due": 0.0,
  "invoice_date": "",
  "due_date": ""
}

Return ONLY the JSON, no explanation."""

    try:
        result_text = _vision_call(path, prompt)
        text = result_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
        parsed = json.loads(text)
        return json.dumps(parsed, indent=2)
    except json.JSONDecodeError as e:
        return json.dumps({"ok": True, "raw": result_text, "parse_error": str(e)})
    except Exception as e:
        logger.error("extract_totals error: %s", e)
        return _err(f"Vision API error: {e}")


@mcp.tool()
def validate_invoice(
    file_path: Annotated[str, Field(description="Absolute path to the invoice PDF or image.")],
    api_key: Annotated[str | None, Field(description="Your InvoiceParser API key.")] = None,
    payment_proof: Annotated[str | None, Field(description="x402 payment proof (Base tx hash). Alternative to api_key.")] = None,
) -> str:
    """
    Validate the math on an invoice — check that line items add up correctly.

    Detects: line item totals that don't match (quantity × unit_price),
    subtotal that doesn't match sum of line items, tax calculation errors,
    and final total discrepancies.

    Returns {valid: bool, issues: [], summary: {}}.

    Cost: $0.01 USDC per call (x402) or counts against your API key quota.
    """
    _stats["total_calls"] += 1
    _track_tool("validate_invoice")
    auth_err = _auth_check(api_key, payment_proof, "validate_invoice", PRICE_EXTRACT)
    if auth_err:
        return auth_err

    path, err = _resolve_file(file_path)
    if err:
        return _err(err)

    prompt = """Carefully validate the mathematics of this invoice. Extract all numbers and check:
1. Does each line item total = quantity × unit_price? (allow ±0.02 rounding)
2. Does subtotal = sum of all line item totals? (allow ±0.02 rounding)
3. Is the tax calculation correct given the tax rate shown?
4. Does total = subtotal + tax - discount + shipping? (allow ±0.02 rounding)

Return ONLY valid JSON:
{
  "ok": true,
  "valid": true,
  "issues": [
    {
      "field": "",
      "expected": 0.0,
      "found": 0.0,
      "description": ""
    }
  ],
  "summary": {
    "line_items_checked": 0,
    "subtotal": 0.0,
    "tax": 0.0,
    "total": 0.0,
    "currency": ""
  }
}

Set valid=false if any issues are found. Return ONLY the JSON, no explanation."""

    try:
        result_text = _vision_call(path, prompt)
        text = result_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
        parsed = json.loads(text)
        return json.dumps(parsed, indent=2)
    except json.JSONDecodeError as e:
        return json.dumps({"ok": True, "raw": result_text, "parse_error": str(e)})
    except Exception as e:
        logger.error("validate_invoice error: %s", e)
        return _err(f"Vision API error: {e}")


@mcp.tool()
def export_to_csv(
    file_paths: Annotated[list[str], Field(description="List of absolute paths to invoice/receipt PDFs or images to process.")],
    output_path: Annotated[str, Field(description="Absolute path where the output CSV file will be saved.")],
    api_key: Annotated[str | None, Field(description="Your InvoiceParser API key.")] = None,
    payment_proof: Annotated[str | None, Field(description="x402 payment proof (Base tx hash). Alternative to api_key.")] = None,
) -> str:
    """
    Parse multiple invoices or receipts and export a summary CSV.

    Each row in the CSV contains: filename, document_type, vendor/merchant,
    date, invoice_number, subtotal, tax, total, currency, due_date.

    Useful for batch expense processing and bookkeeping workflows.
    Maximum 20 files per call.

    Cost: $0.10 USDC per call (x402) or counts against your API key quota.
    """
    _stats["total_calls"] += 1
    _track_tool("export_to_csv")
    auth_err = _auth_check(api_key, payment_proof, "export_to_csv", PRICE_EXPORT)
    if auth_err:
        return auth_err

    if len(file_paths) > 20:
        return _err("Maximum 20 files per export_to_csv call.")

    output = Path(output_path).expanduser().resolve()
    os.makedirs(output.parent, exist_ok=True)

    rows = []
    errors = []

    for fp in file_paths:
        path, err = _resolve_file(fp)
        if err:
            errors.append({"file": fp, "error": err})
            continue

        prompt = """Extract key data from this invoice or receipt. Return ONLY valid JSON:
{
  "document_type": "invoice or receipt",
  "vendor_or_merchant": "",
  "date": "",
  "invoice_or_receipt_number": "",
  "subtotal": 0.0,
  "tax": 0.0,
  "total": 0.0,
  "currency": "",
  "due_date": "",
  "payment_method": ""
}
Return ONLY the JSON."""

        try:
            result_text = _vision_call(path, prompt)
            text = result_text.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
            data = json.loads(text)
            rows.append({
                "filename": path.name,
                "document_type": data.get("document_type", ""),
                "vendor_merchant": data.get("vendor_or_merchant", ""),
                "date": data.get("date", ""),
                "number": data.get("invoice_or_receipt_number", ""),
                "subtotal": data.get("subtotal", ""),
                "tax": data.get("tax", ""),
                "total": data.get("total", ""),
                "currency": data.get("currency", ""),
                "due_date": data.get("due_date", ""),
                "payment_method": data.get("payment_method", ""),
            })
        except Exception as e:
            errors.append({"file": fp, "error": str(e)})

    if not rows and errors:
        return _err(f"All files failed to parse: {errors}")

    fieldnames = ["filename", "document_type", "vendor_merchant", "date", "number",
                  "subtotal", "tax", "total", "currency", "due_date", "payment_method"]

    with open(output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return json.dumps({
        "ok": True,
        "output_path": str(output),
        "rows_written": len(rows),
        "errors": errors,
    })


# ---------------------------------------------------------------------------
# Health endpoint + startup
# ---------------------------------------------------------------------------
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


async def health(request: Request):
    return JSONResponse({"status": "ok", "service": "invoice-parser-mcp"})


async def analytics_endpoint(request: Request):
    uptime = _time.time() - (_stats["start_time"] or _time.time())
    return JSONResponse({
        "server": "invoice-parser-mcp",
        "total_calls": _stats["total_calls"],
        "errors": _stats["errors"],
        "uptime_seconds": int(uptime),
        "version": "1.0.0",
    })


async def stats_endpoint(request: Request):
    """Full /stats endpoint for analytics dashboard."""
    import sqlite3 as _sqlite3
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    from x402 import _PROOF_DB

    now = _dt.now(_tz.utc)
    today_str = now.strftime("%Y-%m-%d")
    week_ago = (now - _td(days=7)).isoformat()

    revenue_total = 0.0
    revenue_this_week = 0.0
    unique_callers = 0
    calls_today = 0
    calls_this_week = 0

    if os.path.exists(_PROOF_DB):
        try:
            conn = _sqlite3.connect(_PROOF_DB)
            conn.row_factory = _sqlite3.Row

            # Total revenue — per-tool pricing
            rows = conn.execute("SELECT tool FROM used_proofs").fetchall()
            price_map = {
                "parse_invoice": 0.05, "parse_receipt": 0.05,
                "extract_line_items": 0.01, "extract_totals": 0.01,
                "validate_invoice": 0.01, "export_to_csv": 0.10,
            }
            for r in rows:
                revenue_total += price_map.get(r["tool"], 0.01)

            # Revenue this week
            rows_week = conn.execute(
                "SELECT tool FROM used_proofs WHERE used_at >= ?", (week_ago,)
            ).fetchall()
            for r in rows_week:
                revenue_this_week += price_map.get(r["tool"], 0.01)

            # Unique callers
            row = conn.execute(
                "SELECT COUNT(DISTINCT SUBSTR(tx_hash, 1, 42)) AS cnt FROM used_proofs"
            ).fetchone()
            unique_callers = row["cnt"] if row else 0

            # Calls today
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM used_proofs WHERE used_at LIKE ?",
                (today_str + "%",),
            ).fetchone()
            calls_today = row["cnt"] if row else 0

            # Calls this week
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM used_proofs WHERE used_at >= ?",
                (week_ago,),
            ).fetchone()
            calls_this_week = row["cnt"] if row else 0

            conn.close()
        except Exception:
            pass

    # API cost estimate: vision_calls * $0.01 avg
    api_cost = _stats.get("vision_calls", 0) * 0.01

    return JSONResponse({
        "server": "invoice-parser-mcp",
        "total_calls": _stats["total_calls"],
        "calls_today": calls_today,
        "calls_this_week": calls_this_week,
        "unique_callers": unique_callers,
        "revenue_total": round(revenue_total, 6),
        "revenue_this_week": round(revenue_this_week, 6),
        "api_cost_estimate": round(api_cost, 4),
        "tools_breakdown": _stats.get("tools_breakdown", {}),
        "uptime_since": _dt.fromtimestamp(
            _stats["start_time"], tz=_tz.utc
        ).isoformat() if _stats["start_time"] else None,
        "version": "1.0.0",
    })


async def payments(request: Request):
    try:
        import sqlite3 as _sqlite3
        import os as _os
        from x402 import _PROOF_DB
        if not _os.path.exists(_PROOF_DB):
            return JSONResponse({"payments": [], "server": "invoice-parser"})
        conn = _sqlite3.connect(_PROOF_DB)
        conn.row_factory = _sqlite3.Row
        rows = conn.execute(
            "SELECT tx_hash, used_at AS timestamp, tool AS tool_name, 0.001 AS amount "
            "FROM used_proofs ORDER BY used_at DESC LIMIT 100"
        ).fetchall()
        conn.close()
        result = [dict(row) for row in rows]
        return JSONResponse({"payments": result, "server": "invoice-parser"})
    except Exception as exc:
        return JSONResponse({"payments": [], "server": "invoice-parser", "error": str(exc)})


def build_app():
    mcp_app = mcp.streamable_http_app()
    app = Starlette(routes=[
        Route("/health", health),
        Route("/analytics", analytics_endpoint),
        Route("/stats", stats_endpoint),
        Route("/payments", payments),
        Mount("/", app=mcp_app),
    ])
    return app


if __name__ == "__main__":
    import uvicorn
    app = build_app()
    uvicorn.run(app, host="0.0.0.0", port=_PORT)
