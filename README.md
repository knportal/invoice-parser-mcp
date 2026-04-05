# Invoice Parser MCP

Parse invoices, receipts, and financial documents into structured JSON — from your AI agent.

Built for the [Model Context Protocol](https://modelcontextprotocol.io). Powered by Claude Vision.

## What it does

Extracts structured data from PDF invoices, scanned receipts, and image files. No templates, no OCR configuration — Claude Vision reads the document and returns clean JSON.

## Tools

| Tool | Description | Price |
|------|-------------|-------|
| `parse_invoice` | Full invoice parsing (vendor, line items, totals, due date) | $0.05/call |
| `parse_receipt` | Retail receipt parsing (merchant, items, tax, payment method) | $0.05/call |
| `extract_line_items` | Just the itemized list, nothing else | $0.01/call |
| `extract_totals` | Just subtotal, tax, total, due date | $0.01/call |
| `validate_invoice` | Math validation — checks that line items add up | $0.01/call |
| `export_to_csv` | Batch parse multiple files → summary CSV (max 20) | $0.10/call |

## Supported formats

- PDF (invoices, scanned documents)
- PNG, JPG, WEBP (photos of receipts, screenshots)

## Authentication

**Free tier:** 20 parses/month with an API key (get one at plenitudo.ai)

**Pay-per-use (x402):** No account needed. Send USDC on Base to the wallet address, pass the tx hash as `payment_proof`.

```json
// x402 payment instructions (returned when no auth provided):
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

## Usage (Claude Desktop / MCP client)

```json
{
  "mcpServers": {
    "invoice-parser": {
      "url": "https://invoice-parser.plenitudo.ai/mcp"
    }
  }
}
```

## Deployment (Railway)

1. Fork this repo
2. Connect to Railway → New Project → Deploy from GitHub
3. Add environment variables:
   - `ANTHROPIC_API_KEY` — your Anthropic API key (required; Railway runs in the cloud and calls the Anthropic API directly)
   - `INVOICEPARSER_DATA_DIR` — `/data`
   - `STRIPE_WEBHOOK_SECRET` — from Stripe dashboard
   - `STRIPE_PRO_PRICE_ID` — from Stripe dashboard
4. Add a persistent volume at `/data`
5. Deploy

## Running locally (optional maxproxy routing)

If you run the server on the same machine as a maxproxy instance on port 3456, you can route Claude Vision calls through it instead of hitting the Anthropic API directly:

```
ANTHROPIC_API_KEY=maxproxy
ANTHROPIC_BASE_URL=http://localhost:3456
```

Leave `ANTHROPIC_BASE_URL` unset (or empty) in any cloud/Railway deployment — those environments cannot reach a local proxy.

## License

MIT
