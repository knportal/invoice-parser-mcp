"""
Invoice Parser MCP — Configuration
All values are read from environment variables with safe defaults.
"""

import os

# Claude Vision API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Stripe
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRO_PRICE_ID = os.getenv("STRIPE_PRO_PRICE_ID", "")

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Data directory (configurable for Railway / Docker)
DATA_DIR = os.getenv("INVOICEPARSER_DATA_DIR", os.path.expanduser("~/Projects/invoice-parser-mcp/data"))
os.makedirs(DATA_DIR, exist_ok=True)

# Derived DB paths
KEYS_DB = os.path.join(DATA_DIR, "keys.db")
USAGE_DB = os.path.join(DATA_DIR, "usage.db")

# Tier limits
FREE_MONTHLY_LIMIT = int(os.getenv("FREE_MONTHLY_LIMIT", "20"))

# Log file
LOG_FILE = os.path.expanduser(
    os.getenv("INVOICEPARSER_LOG_FILE", "./logs/server.log")
)

# Upgrade URL surfaced in error messages
UPGRADE_URL = os.getenv("UPGRADE_URL", "https://plenitudo.ai")

# Claude model to use for vision
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-4-5-20251101")
