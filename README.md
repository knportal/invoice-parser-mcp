# Invoice Parser MCP

Parse invoices, receipts, and financial documents into structured JSON — from your AI agent.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)](CHANGELOG.md)
[![MCP](https://img.shields.io/badge/MCP-compatible-brightgreen.svg)](https://modelcontextprotocol.io)

**Listed on:** [Glama](https://glama.ai/mcp/servers/knportal/invoice-parser-mcp?ref=readme) · [Smithery](https://smithery.ai/server/knportal/invoice-parser-mcp?ref=readme) · [mcp.so](https://mcp.so/server/invoice-parser-mcp?ref=readme)

Built for the [Model Context Protocol](https://modelcontextprotocol.io). Powered by Claude Vision.

## What it does

Extracts structured data from PDF invoices, scanned receipts, and image files. No templates, no OCR configuration — Claude Vision reads the document and returns clean JSON.

---

## Tools

| Tool | Description | Price |
|------|-------------|-------|
| `parse_invoice` | Full invoice parsing (vendor, line items, totals, due date) | $0.05/call |
| `parse_receipt` | Retail receipt parsing (merchant, items, tax, payment method) | $0.05/call |
| `extract_line_items` | Just the itemized list, nothing else | $0.01/call |
| `extract_totals` | Just subtotal, tax, total, due date | $0.01/call |
| `validate_invoice` | Math validation — checks that line items add up | $0.01/call |
| `export_to_csv` | Batch parse multiple files → summary CSV (max 20) | $0.10/call |

---

## Tool Reference

### `parse_invoice`

Full invoice parsing. Extracts every structured field from a vendor invoice.

**Parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file_path` | string | Yes | Absolute path to the invoice PDF or image (PNG, JPG, WEBP). |
| `api_key` | string | No* | Your InvoiceParser API key. Get one at [plenitudo.ai](https://plenitudo.ai). |
| `payment_proof` | string | No* | x402 payment proof (Base USDC tx hash). Alternative to `api_key`. |

*Either `api_key` or `payment_proof` must be provided.

**Example input**
```json
{
  "file_path": "/Users/me/documents/invoice_acme_jan2025.pdf",
  "api_key": "ip_free_abc123"
}
```

**Example output**
```json
{
  "ok": true,
  "document_type": "invoice",
  "vendor": {
    "name": "Acme Corp",
    "address": "123 Industrial Way, Austin TX 78701",
    "email": "billing@acme.com",
    "phone": "512-555-0100",
    "tax_id": "12-3456789"
  },
  "bill_to": {
    "name": "Jane Smith",
    "address": "456 Oak Ave, Boston MA 02101",
    "email": "jane@example.com"
  },
  "invoice_number": "INV-2025-0142",
  "invoice_date": "2025-01-15",
  "due_date": "2025-02-15",
  "payment_terms": "Net 30",
  "currency": "USD",
  "line_items": [
    {
      "description": "Software consulting — January",
      "quantity": 40,
      "unit_price": 175.0,
      "total": 7000.0,
      "tax_rate": 0.0
    }
  ],
  "subtotal": 7000.0,
  "discount": 0.0,
  "tax_amount": 560.0,
  "shipping": 0.0,
  "total": 7560.0,
  "amount_due": 7560.0,
  "notes": "Wire transfer preferred. See banking details on page 2.",
  "po_number": "PO-98765"
}
```

---

### `parse_receipt`

Parse a retail or expense receipt. Designed for point-of-sale receipts, restaurant bills, and expense claim documents.

**Parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file_path` | string | Yes | Absolute path to the receipt PDF or image (PNG, JPG, WEBP). |
| `api_key` | string | No* | Your InvoiceParser API key. |
| `payment_proof` | string | No* | x402 payment proof (Base USDC tx hash). |

**Example input**
```json
{
  "file_path": "/Users/me/receipts/coffee_shop_march1.jpg",
  "api_key": "ip_free_abc123"
}
```

**Example output**
```json
{
  "ok": true,
  "document_type": "receipt",
  "merchant": {
    "name": "Blue Bottle Coffee",
    "address": "300 Webster St, Oakland CA 94609",
    "phone": "510-555-0200",
    "website": "bluebottlecoffee.com"
  },
  "date": "2025-03-01",
  "time": "09:14",
  "receipt_number": "5541",
  "cashier": "Maria",
  "items": [
    { "name": "Latte (large)", "quantity": 1, "unit_price": 6.50, "total": 6.50, "sku": "", "category": "beverage" },
    { "name": "Croissant", "quantity": 1, "unit_price": 4.00, "total": 4.00, "sku": "", "category": "pastry" }
  ],
  "subtotal": 10.50,
  "discounts": 0.0,
  "tax": 0.84,
  "tip": 2.00,
  "total": 13.34,
  "currency": "USD",
  "payment_method": "Visa",
  "card_last_four": "4242",
  "transaction_id": "TXN-88821",
  "loyalty_points": null,
  "notes": ""
}
```

---

### `extract_line_items`

Lightweight extraction that returns only the itemized list. Faster and cheaper than `parse_invoice` when you only need the line items.

**Parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file_path` | string | Yes | Absolute path to the invoice or receipt PDF or image. |
| `api_key` | string | No* | Your InvoiceParser API key. |
| `payment_proof` | string | No* | x402 payment proof (Base USDC tx hash). |

**Example output**
```json
{
  "ok": true,
  "line_items": [
    { "description": "Widget A (x10)", "quantity": 10, "unit_price": 12.00, "total": 120.00 },
    { "description": "Widget B (x5)",  "quantity": 5,  "unit_price": 24.00, "total": 120.00 }
  ],
  "item_count": 2
}
```

---

### `extract_totals`

Extract only the financial summary (subtotal, taxes, totals, due date) without parsing line items or vendor details.

**Parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file_path` | string | Yes | Absolute path to the invoice or receipt PDF or image. |
| `api_key` | string | No* | Your InvoiceParser API key. |
| `payment_proof` | string | No* | x402 payment proof (Base USDC tx hash). |

**Example output**
```json
{
  "ok": true,
  "currency": "USD",
  "subtotal": 240.00,
  "discount": 0.0,
  "tax_amount": 19.20,
  "tax_rate": 8.0,
  "shipping": 0.0,
  "tip": 0.0,
  "total": 259.20,
  "amount_due": 259.20,
  "invoice_date": "2025-01-15",
  "due_date": "2025-02-15"
}
```

---

### `validate_invoice`

Math validation tool. Verifies that line item totals equal `quantity × unit_price`, that the subtotal matches the sum of line items, that the tax calculation is consistent, and that the final total reconciles. Allows ±$0.02 rounding tolerance.

**Parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file_path` | string | Yes | Absolute path to the invoice PDF or image. |
| `api_key` | string | No* | Your InvoiceParser API key. |
| `payment_proof` | string | No* | x402 payment proof (Base USDC tx hash). |

**Example output — valid invoice**
```json
{
  "ok": true,
  "valid": true,
  "issues": [],
  "summary": {
    "line_items_checked": 3,
    "subtotal": 450.00,
    "tax": 36.00,
    "total": 486.00,
    "currency": "USD"
  }
}
```

**Example output — invalid invoice**
```json
{
  "ok": true,
  "valid": false,
  "issues": [
    {
      "field": "line_item_2_total",
      "expected": 120.00,
      "found": 100.00,
      "description": "quantity (10) × unit_price (12.00) = 120.00, but stated total is 100.00"
    }
  ],
  "summary": {
    "line_items_checked": 3,
    "subtotal": 340.00,
    "tax": 27.20,
    "total": 367.20,
    "currency": "USD"
  }
}
```

---

### `export_to_csv`

Batch parse up to 20 invoices or receipts and export a summary CSV. Each row contains: filename, document type, vendor/merchant, date, number, subtotal, tax, total, currency, due date, payment method.

**Parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `file_paths` | array of strings | Yes | List of absolute paths to invoice/receipt PDFs or images. Maximum 20. |
| `output_path` | string | Yes | Absolute path where the output CSV file will be saved. |
| `api_key` | string | No* | Your InvoiceParser API key. |
| `payment_proof` | string | No* | x402 payment proof (Base USDC tx hash). |

**Example input**
```json
{
  "file_paths": [
    "/Users/me/invoices/jan2025.pdf",
    "/Users/me/invoices/feb2025.pdf",
    "/Users/me/receipts/expense_march.jpg"
  ],
  "output_path": "/Users/me/exports/q1_summary.csv",
  "api_key": "ip_free_abc123"
}
```

**Example output (JSON response)**
```json
{
  "ok": true,
  "output_path": "/Users/me/exports/q1_summary.csv",
  "rows_written": 3,
  "errors": []
}
```

**CSV columns:** `filename`, `document_type`, `vendor_merchant`, `date`, `number`, `subtotal`, `tax`, `total`, `currency`, `due_date`, `payment_method`

---

## Supported formats

- PDF (invoices, scanned documents)
- PNG, JPG, WEBP (photos of receipts, screenshots)

---

## Authentication

**Free tier:** 20 parses/month with an API key (get one at plenitudo.ai)

**Pay-per-use (x402):** No account needed. Send USDC on Base to the wallet address, pass the tx hash as `payment_proof`.

```json
{
  "error": "Payment required",
  "x402": {
    "network": "base",
    "token": "USDC",
    "recipient": "0x9053FeDC90c1BCB4a8Cf708DdB426aB02430d6ad",
    "amount_usdc": 0.05
  }
}
```

---

## Usage (Claude Desktop / MCP client)

```json
{
  "mcpServers": {
    "invoice-parser": {
      "url": "https://invoice-parser.plenitudo.ai/mcp?ref=readme"
    }
  }
}
```

---

## Architecture

```
server.py          — MCP server (6 tools: parse_invoice, parse_receipt,
                     extract_line_items, extract_totals, validate_invoice,
                     export_to_csv)
auth.py            — API key validation + usage tracking (SQLite)
x402.py            — x402 micropayment verification (USDC on Base)
config.py          — Environment variable loading
worker.js          — Cloudflare Worker (remote proxy for MCP traffic)
data/keys.db       — API key store (created at runtime)
data/usage.db      — Monthly usage counters (created at runtime)
logs/              — Structured log files
tests/             — Unit tests (mock Vision API, no real documents needed)
```

**Request flow**

```
AI agent (Claude Desktop, Cursor, etc.)
    │
    │  MCP tool call (JSON-RPC over HTTP)
    ▼
Cloudflare Worker (worker.js)        ← optional remote proxy
    │
    │  Forwards to Railway deployment
    ▼
server.py (FastMCP, streamable HTTP)
    │
    ├── auth.py              validates API key / x402 proof
    ├── x402.py              verifies USDC transaction on Base
    └── Anthropic API        Claude Vision reads the document
            │
            ▼
        structured JSON → returned to agent
```

**Environment variables**

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key for Claude Vision. Set to `maxproxy` for local proxy routing. |
| `ANTHROPIC_BASE_URL` | No | Override Anthropic base URL. Set to `http://localhost:3456` when using maxproxy locally. Leave unset in cloud deployments. |
| `CLAUDE_MODEL` | No | Claude model ID. Defaults to `claude-3-5-sonnet-20241022`. |
| `INVOICEPARSER_DATA_DIR` | No | Directory for SQLite databases. Defaults to `./data`. |
| `PORT` | No | HTTP port. Defaults to `8000`. Railway sets this automatically. |

---

## Deployment (Railway)

1. Fork this repo
2. Connect to Railway → New Project → Deploy from GitHub
3. Add environment variables:
   - `ANTHROPIC_API_KEY` — your Anthropic API key
   - `INVOICEPARSER_DATA_DIR` — `/data`
   - `STRIPE_WEBHOOK_SECRET` — from Stripe dashboard
   - `STRIPE_PRO_PRICE_ID` — from Stripe dashboard
4. Add a persistent volume at `/data`
5. Deploy

---

## Running locally (optional maxproxy routing)

If you run the server on the same machine as a maxproxy instance on port 3456, you can route Claude Vision calls through it instead of hitting the Anthropic API directly:

```
ANTHROPIC_API_KEY=maxproxy
ANTHROPIC_BASE_URL=http://localhost:3456
```

Leave `ANTHROPIC_BASE_URL` unset (or empty) in any cloud/Railway deployment — those environments cannot reach a local proxy.

---

## Contributing & Security

- [CONTRIBUTING.md](CONTRIBUTING.md) — dev setup and PR guidelines
- [SECURITY.md](SECURITY.md) — responsible disclosure policy
- [CHANGELOG.md](CHANGELOG.md) — version history

## License

[MIT](LICENSE) — Copyright © 2025 Kenneth Nygren / Plenitudo AI
