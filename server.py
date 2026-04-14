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


@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    from starlette.responses import JSONResponse
    return JSONResponse({"status": "ok", "service": "invoice-parser-mcp"})


@mcp.custom_route("/analytics", methods=["GET"])
async def analytics_endpoint(request):
    from starlette.responses import JSONResponse
    uptime = _time.time() - (_stats["start_time"] or _time.time())
    return JSONResponse({
        "server": "invoice-parser-mcp",
        "total_calls": _stats["total_calls"],
        "errors": _stats["errors"],
        "uptime_seconds": int(uptime),
        "version": "1.0.0",
    })


@mcp.custom_route("/stats", methods=["GET"])
async def stats_endpoint(request):
    from starlette.responses import JSONResponse
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
            price_map = {
                "parse_invoice": 0.05, "parse_receipt": 0.05,
                "extract_line_items": 0.01, "extract_totals": 0.01,
                "validate_invoice": 0.01, "export_to_csv": 0.10,
            }
            for r in conn.execute("SELECT tool FROM used_proofs").fetchall():
                revenue_total += price_map.get(r["tool"], 0.01)
            for r in conn.execute(
                "SELECT tool FROM used_proofs WHERE used_at >= ?", (week_ago,)
            ).fetchall():
                revenue_this_week += price_map.get(r["tool"], 0.01)
            row = conn.execute(
                "SELECT COUNT(DISTINCT SUBSTR(tx_hash, 1, 42)) AS cnt FROM used_proofs"
            ).fetchone()
            unique_callers = row["cnt"] if row else 0
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM used_proofs WHERE used_at LIKE ?",
                (today_str + "%",),
            ).fetchone()
            calls_today = row["cnt"] if row else 0
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM used_proofs WHERE used_at >= ?", (week_ago,)
            ).fetchone()
            calls_this_week = row["cnt"] if row else 0
            conn.close()
        except Exception:
            pass

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


@mcp.custom_route("/payments", methods=["GET"])
async def payments(request):
    from starlette.responses import JSONResponse
    try:
        import sqlite3 as _sqlite3
        from x402 import _PROOF_DB
        if not os.path.exists(_PROOF_DB):
            return JSONResponse({"payments": [], "server": "invoice-parser"})
        conn = _sqlite3.connect(_PROOF_DB)
        conn.row_factory = _sqlite3.Row
        rows = conn.execute(
            "SELECT tx_hash, used_at AS timestamp, tool AS tool_name, 0.001 AS amount "
            "FROM used_proofs ORDER BY used_at DESC LIMIT 100"
        ).fetchall()
        conn.close()
        return JSONResponse({"payments": [dict(r) for r in rows], "server": "invoice-parser"})
    except Exception as exc:
        return JSONResponse({"payments": [], "server": "invoice-parser", "error": str(exc)})


_LANDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Invoice Parser MCP — Parse Any Invoice from Any AI Agent</title>
  <meta name="description" content="Invoice Parser MCP lets AI agents extract structured data from any invoice PDF or image — vendor, amount, line items, dates — with a single tool call. Pay per use." />
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg: #0a0a0f; --surface: #12121a; --border: #1e1e2e;
      --accent: #7c6fcd; --accent-light: #a89be0; --text: #e8e8f0;
      --muted: #8888aa; --green: #4ade80;
      --mono: 'Fira Code', 'Cascadia Code', 'Consolas', monospace;
    }
    body { background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; line-height: 1.6; }
    a { color: var(--accent-light); text-decoration: none; }
    a:hover { text-decoration: underline; }
    nav { display: flex; justify-content: space-between; align-items: center; padding: 1.25rem 2rem; border-bottom: 1px solid var(--border); }
    .logo { font-weight: 700; font-size: 1.05rem; letter-spacing: -0.02em; }
    .logo span { color: var(--accent-light); }
    .nav-links { display: flex; gap: 2rem; font-size: 0.9rem; }
    .nav-links a { color: var(--muted); }
    .nav-links a:hover { color: var(--text); }
    .nav-cta { background: var(--accent); color: #fff; padding: 0.45rem 1.1rem; border-radius: 6px; font-size: 0.9rem; font-weight: 600; }
    .nav-cta:hover { background: var(--accent-light); text-decoration: none; }
    .hero { max-width: 760px; margin: 5rem auto 4rem; padding: 0 2rem; text-align: center; }
    .hero-badge { display: inline-block; background: rgba(124,111,205,0.15); border: 1px solid rgba(124,111,205,0.3); color: var(--accent-light); font-size: 0.75rem; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; padding: 0.3rem 0.9rem; border-radius: 100px; margin-bottom: 1.5rem; }
    h1 { font-size: clamp(2rem, 5vw, 3.2rem); font-weight: 800; letter-spacing: -0.03em; line-height: 1.15; margin-bottom: 1.25rem; }
    h1 em { font-style: normal; color: var(--accent-light); }
    .hero-sub { font-size: 1.15rem; color: var(--muted); max-width: 560px; margin: 0 auto 2.5rem; }
    .hero-actions { display: flex; gap: 1rem; justify-content: center; flex-wrap: wrap; }
    .btn-primary { background: var(--accent); color: #fff; padding: 0.75rem 1.75rem; border-radius: 8px; font-weight: 700; font-size: 1rem; }
    .btn-primary:hover { background: var(--accent-light); text-decoration: none; }
    .btn-secondary { border: 1px solid var(--border); color: var(--muted); padding: 0.75rem 1.75rem; border-radius: 8px; font-size: 1rem; }
    .btn-secondary:hover { border-color: var(--accent); color: var(--text); text-decoration: none; }
    .code-preview { max-width: 700px; margin: 3rem auto 5rem; padding: 0 2rem; }
    .code-block { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
    .code-header { display: flex; align-items: center; gap: 0.5rem; padding: 0.75rem 1.25rem; border-bottom: 1px solid var(--border); font-size: 0.8rem; color: var(--muted); }
    .dot { width: 10px; height: 10px; border-radius: 50%; }
    .dot-r { background: #ff5f57; } .dot-y { background: #febc2e; } .dot-g { background: #28c840; }
    .code-body { padding: 1.5rem; font-family: var(--mono); font-size: 0.82rem; line-height: 1.7; overflow-x: auto; }
    .kw { color: #c792ea; } .str { color: #c3e88d; } .key { color: #82aaff; } .val { color: #f78c6c; } .cmt { color: #546e7a; }
    section { max-width: 900px; margin: 0 auto; padding: 4rem 2rem; }
    h2 { font-size: 1.9rem; font-weight: 700; letter-spacing: -0.02em; margin-bottom: 0.5rem; }
    .section-sub { color: var(--muted); margin-bottom: 3rem; }
    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 1.25rem; }
    .card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 1.5rem; }
    .card-icon { font-size: 1.6rem; margin-bottom: 0.75rem; }
    .card h3 { font-size: 1rem; font-weight: 700; margin-bottom: 0.5rem; }
    .card p { font-size: 0.9rem; color: var(--muted); }
    .pricing-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 1.5rem; }
    .plan { background: var(--surface); border: 1px solid var(--border); border-radius: 14px; padding: 2rem; }
    .plan.featured { border-color: var(--accent); }
    .plan-badge { display: inline-block; background: var(--accent); color: #fff; font-size: 0.7rem; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; padding: 0.2rem 0.6rem; border-radius: 4px; margin-bottom: 1rem; }
    .plan-name { font-size: 1.1rem; font-weight: 700; margin-bottom: 0.25rem; }
    .plan-price { font-size: 2.5rem; font-weight: 800; letter-spacing: -0.04em; }
    .plan-price span { font-size: 1rem; font-weight: 400; color: var(--muted); }
    .plan-desc { color: var(--muted); font-size: 0.9rem; margin: 0.75rem 0 1.5rem; }
    .plan-features { list-style: none; }
    .plan-features li { font-size: 0.9rem; color: var(--muted); padding: 0.4rem 0; border-top: 1px solid var(--border); display: flex; align-items: center; gap: 0.6rem; }
    .plan-features li::before { content: '✓'; color: var(--green); font-weight: 700; }
    .plan-cta { display: block; text-align: center; margin-top: 1.75rem; padding: 0.7rem; border-radius: 8px; font-weight: 700; font-size: 0.95rem; }
    .plan-cta-outline { border: 1px solid var(--border); color: var(--muted); }
    .plan-cta-outline:hover { border-color: var(--accent); color: var(--text); text-decoration: none; }
    .plan-cta-filled { background: var(--accent); color: #fff; }
    .plan-cta-filled:hover { background: var(--accent-light); text-decoration: none; }
    hr { border: none; border-top: 1px solid var(--border); }
    footer { text-align: center; padding: 2.5rem 2rem; font-size: 0.85rem; color: var(--muted); }
    footer a { color: var(--muted); }
    footer a:hover { color: var(--text); }
  </style>
</head>
<body>
  <nav>
    <div class="logo">Invoice Parser <span>MCP</span></div>
    <div class="nav-links">
      <a href="#use-cases">Use Cases</a>
      <a href="#pricing">Pricing</a>
      <a href="https://github.com/knportal/invoice-parser-mcp" target="_blank">Docs</a>
    </div>
    <a class="nav-cta" href="https://github.com/knportal/invoice-parser-mcp" target="_blank">Get Started</a>
  </nav>

  <div class="hero">
    <div class="hero-badge">MCP Server &middot; Pay Per Use &middot; Plenitudo.ai</div>
    <h1>Parse any invoice from <em>any AI agent</em></h1>
    <p class="hero-sub">PDF or image URL &rarr; structured JSON in one tool call. Vendor, amount, line items, dates &mdash; extracted by Claude Vision. No signup, no subscription.</p>
    <div class="hero-actions">
      <a class="btn-primary" href="https://github.com/knportal/invoice-parser-mcp" target="_blank">View on GitHub</a>
      <a class="btn-secondary" href="https://smithery.ai" target="_blank">View on Smithery</a>
    </div>
  </div>

  <div class="code-preview">
    <div class="code-block">
      <div class="code-header">
        <div class="dot dot-r"></div><div class="dot dot-y"></div><div class="dot dot-g"></div>
        <span style="margin-left:0.5rem">Example tool call</span>
      </div>
      <div class="code-body"><pre><span class="cmt">// Agent parses an invoice in one call</span>
{
  <span class="key">"tool"</span>: <span class="str">"parse_invoice"</span>,
  <span class="key">"parameters"</span>: {
    <span class="key">"source"</span>: <span class="str">"https://example.com/invoice_2024_001.pdf"</span>
  }
}

<span class="cmt">// Response &mdash; structured JSON, ready to use</span>
{
  <span class="key">"vendor"</span>: <span class="str">"Acme Corp"</span>,
  <span class="key">"invoice_number"</span>: <span class="str">"INV-2024-001"</span>,
  <span class="key">"date"</span>: <span class="str">"2024-03-15"</span>,
  <span class="key">"total"</span>: <span class="val">1250.00</span>,
  <span class="key">"currency"</span>: <span class="str">"USD"</span>,
  <span class="key">"line_items"</span>: [
    { <span class="key">"description"</span>: <span class="str">"Consulting Services"</span>, <span class="key">"amount"</span>: <span class="val">1250.00</span> }
  ]
}</pre></div>
    </div>
  </div>

  <section id="use-cases">
    <h2>Built for agent workflows</h2>
    <p class="section-sub">Wherever an agent needs to read and act on invoice data, Invoice Parser handles the extraction.</p>
    <div class="cards">
      <div class="card"><div class="card-icon">💼</div><h3>Accounts Payable</h3><p>Agents receive supplier invoices, extract all fields automatically, and push to your ERP or accounting system without human review.</p></div>
      <div class="card"><div class="card-icon">📊</div><h3>Expense Reconciliation</h3><p>Upload a folder of receipts and invoices &mdash; the agent extracts every amount, vendor, and date and matches them against your records.</p></div>
      <div class="card"><div class="card-icon">🔍</div><h3>Audit &amp; Compliance</h3><p>Structured invoice data makes it trivial to flag duplicates, verify tax amounts, and ensure vendor details match approved lists.</p></div>
      <div class="card"><div class="card-icon">🏗️</div><h3>Construction &amp; Contractors</h3><p>Parse subcontractor invoices, extract line items by job code, and feed directly into project cost tracking &mdash; all from a single tool call.</p></div>
    </div>
  </section>

  <hr />

  <section id="pricing">
    <h2>Pay per use</h2>
    <p class="section-sub">No subscription. No API key. Just call the tool &mdash; payment handled via x402 micropayments.</p>
    <div class="pricing-grid">
      <div class="plan">
        <div class="plan-name">Per Call</div>
        <div class="plan-price">$0.02 <span>/ invoice</span></div>
        <div class="plan-desc">Charged automatically via x402 micropayments. No account required.</div>
        <ul class="plan-features">
          <li>PDF and image URLs supported</li>
          <li>Claude Vision extraction</li>
          <li>Vendor, amount, line items, dates</li>
          <li>USDC on Base (automatic)</li>
          <li>Works with any MCP client</li>
        </ul>
        <a class="plan-cta plan-cta-outline" href="https://github.com/knportal/invoice-parser-mcp" target="_blank">Read the Docs</a>
      </div>
      <div class="plan featured">
        <div class="plan-badge">MCP Endpoint</div>
        <div class="plan-name">Ready to Use</div>
        <div class="plan-price" style="font-size:1.1rem;padding-top:0.5rem;word-break:break-all;color:var(--accent-light);">invoice-parser.plenitudo.ai/mcp</div>
        <div class="plan-desc" style="margin-top:1rem;">Add to any MCP-compatible agent and start parsing invoices immediately.</div>
        <ul class="plan-features">
          <li>No signup required</li>
          <li>Live on Railway + Cloudflare</li>
          <li>Listed on Smithery &amp; Glama</li>
          <li>Open source on GitHub</li>
        </ul>
        <a class="plan-cta plan-cta-filled" href="https://smithery.ai" target="_blank">View on Smithery</a>
      </div>
    </div>
  </section>

  <hr />

  <section style="text-align:center;padding:5rem 2rem;">
    <h2>Connect your agent</h2>
    <p class="section-sub" style="margin-bottom:2rem;">Add this endpoint to any MCP-compatible AI client to start parsing invoices.</p>
    <div style="border:1px solid var(--border);border-radius:10px;padding:1.5rem;max-width:520px;margin:0 auto;text-align:left;">
      <p style="font-size:0.8rem;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:var(--muted);margin-bottom:0.75rem;">MCP Endpoint</p>
      <code style="font-family:var(--mono);font-size:0.85rem;color:var(--accent-light);word-break:break-all;">https://invoice-parser.plenitudo.ai/mcp</code>
    </div>
  </section>

  <footer>
    <p>Built by <a href="https://plenitudo.ai" target="_blank">Plenitudo.ai</a> &nbsp;&middot;&nbsp; <a href="https://smithery.ai" target="_blank">Smithery</a> &nbsp;&middot;&nbsp; <a href="https://github.com/knportal/invoice-parser-mcp" target="_blank">GitHub</a></p>
    <p style="margin-top:0.5rem;font-size:0.8rem;">&copy; 2026 Plenitudo &middot; Invoice Parser MCP is powered by Claude Vision (Anthropic).</p>
  </footer>
</body>
</html>"""


@mcp.custom_route("/", methods=["GET"])
async def landing_page(request):
    from starlette.responses import HTMLResponse
    return HTMLResponse(_LANDING_HTML)


if __name__ == "__main__":
    import uvicorn

    logger.info(f"Invoice Parser MCP server starting up (streamable-http on :{_PORT})")
    app = mcp.streamable_http_app()
    uvicorn.run(app, host="0.0.0.0", port=_PORT)
