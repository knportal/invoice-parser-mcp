# Contributing to Invoice Parser MCP

Thanks for your interest in contributing! This project is maintained by [Plenitudo AI](https://plenitudo.ai).

## Dev Environment Setup

### Prerequisites

- Python 3.10+
- `pip` and `venv`
- [wrangler](https://developers.cloudflare.com/workers/wrangler/) (for Cloudflare Worker changes)
- An Anthropic API key (or a local `maxproxy` instance on port 3456)

### Install

```bash
git clone https://github.com/knportal/invoice-parser-mcp.git
cd invoice-parser-mcp

python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Fill in .env values for local dev (at minimum: ANTHROPIC_API_KEY)
```

### Run the server locally

```bash
source venv/bin/activate
python server.py
# MCP server starts on http://localhost:8000
```

### Local development with maxproxy

If you have a `maxproxy` instance running locally, you can route Claude Vision calls through it to avoid Anthropic API costs during development:

```bash
ANTHROPIC_API_KEY=maxproxy
ANTHROPIC_BASE_URL=http://localhost:3456
python server.py
```

Leave `ANTHROPIC_BASE_URL` unset in any cloud or Railway deployment.

## Making Changes

### Code style

- Follow [PEP 8](https://peps.python.org/pep-0008/) for Python code
- Use descriptive variable names; avoid single-letter names outside loops
- All tool functions must return JSON strings (`json.dumps({...})`)
- All tool responses must include an `ok` boolean field
- Log meaningful events at appropriate levels (INFO for normal ops, ERROR for failures)

### Testing

Before submitting a PR, manually verify that:

1. `parse_invoice` returns correctly structured JSON for a sample PDF invoice
2. `parse_receipt` returns correctly structured JSON for a sample receipt image
3. `extract_line_items` returns only the line items array, not full invoice data
4. `extract_totals` returns only financial summary fields
5. `validate_invoice` correctly identifies a math error when line items don't add up
6. `export_to_csv` produces a valid CSV file for a batch of 2+ documents
7. Invalid API keys return `{"ok": false, "error": "..."}`
8. Usage limits are enforced for free-tier keys
9. x402 payment proof replay is rejected with a clear error

### Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add support for XLSX invoice format
fix: handle PDFs with no text layer gracefully
docs: update README with export_to_csv examples
chore: bump anthropic SDK to latest
```

## Pull Request Process

1. Fork the repo and create a branch: `git checkout -b feat/your-feature`
2. Make your changes with focused commits
3. Push to your fork and open a PR against `main`
4. Describe what you changed and why in the PR description
5. One of the maintainers will review within a few days

## Questions?

Open an issue or email [hello@plenitudo.ai](mailto:hello@plenitudo.ai).
