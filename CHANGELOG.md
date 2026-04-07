# Changelog

All notable changes to Invoice Parser MCP are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] — 2025-04-01

### Added

**Core tools**

- `parse_invoice` — Full invoice parsing powered by Claude Vision. Extracts vendor name, vendor address, bill-to details, invoice number, invoice date, due date, payment terms, currency, line items (description, quantity, unit price, total, tax rate), subtotal, discount, tax amount, shipping, total, amount due, notes, and PO number. Returns structured JSON. Supports PDF and image files (PNG, JPG, WEBP). Cost: $0.05/call.
- `parse_receipt` — Retail and expense receipt parsing. Extracts merchant name, date, time, receipt number, cashier, itemized line items (name, quantity, unit price, total, SKU, category), subtotal, discounts, tax, tip, total, currency, payment method, card last four, transaction ID, and loyalty points. Cost: $0.05/call.
- `extract_line_items` — Lightweight extraction returning only the itemized list. Faster and cheaper than full invoice parsing when only line items are needed. Returns an array with description, quantity, unit price, and total per item. Cost: $0.01/call.
- `extract_totals` — Extract only the financial summary (subtotal, tax, discount, shipping, tip, total, amount due, due date) without parsing the full document. Cost: $0.01/call.
- `validate_invoice` — Math validation tool. Checks that each line item total equals quantity × unit price, that the subtotal equals the sum of line items, that the tax calculation matches the stated tax rate, and that the final total reconciles. Returns `{valid: bool, issues: [], summary: {}}`. Allows ±$0.02 rounding tolerance. Cost: $0.01/call.
- `export_to_csv` — Batch parse up to 20 invoices or receipts and export a summary CSV. Each row contains filename, document type, vendor/merchant, date, number, subtotal, tax, total, currency, due date, and payment method. Cost: $0.10/call.

**Authentication & payments**

- API key authentication with per-key usage tracking (SQLite).
- Free tier: 20 parses/month at no cost. Keys issued at plenitudo.ai.
- x402 micropayments: pay-per-use with USDC on Base. No account required — send USDC to the wallet address and pass the transaction hash as `payment_proof`. Per-tool pricing applies.
- Replay protection: each x402 transaction hash can only be used once.

**Backend**

- Claude Vision backend (Anthropic API) for document understanding — no OCR templates or field configuration required.
- Supports PDF documents and image files (PNG, JPG, JPEG, WEBP, GIF).
- Configurable Claude model via `CLAUDE_MODEL` environment variable.
- Optional `maxproxy` routing for local development (`ANTHROPIC_BASE_URL=http://localhost:3456`).

**Infrastructure**

- One-click Railway deployment via included `railway.toml`.
- Cloudflare Worker proxy (`worker.js`) for remote agent routing.
- `/health` endpoint for Railway health checks.
- Structured logging to file and stderr with configurable log level.
- MCP server exposed via streamable HTTP transport using `fastmcp`.

---

## [Unreleased]

- Pro tier with higher monthly quota via Stripe
- Webhook delivery on parse completion
- Support for XLSX and DOCX invoice formats
