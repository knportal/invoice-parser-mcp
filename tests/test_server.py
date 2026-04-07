"""
Unit tests for Invoice Parser MCP server tools.

These tests mock external dependencies (Anthropic Vision API, auth, x402) so
they can run without real documents, a database, or payment infrastructure.
"""

import csv
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Lightweight stubs so server.py can be imported without its full dep tree
# ---------------------------------------------------------------------------

def _make_stub_module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


def _setup_stubs():
    # config
    cfg = _make_stub_module("config")
    cfg.LOG_FILE = "/tmp/invoiceparser_test.log"
    cfg.LOG_LEVEL = "ERROR"
    cfg.ANTHROPIC_API_KEY = "test-key"
    cfg.ANTHROPIC_BASE_URL = ""
    cfg.CLAUDE_MODEL = "claude-3-5-sonnet-20241022"

    # auth
    auth = _make_stub_module("auth")
    auth.validate_and_charge = MagicMock(return_value=(True, None))

    # x402
    x402 = _make_stub_module("x402")
    x402.WALLET_ADDRESS = "0xTestWallet"
    x402.is_proof_used = MagicMock(return_value=False)
    x402.mark_proof_used = MagicMock()
    x402.verify_payment = MagicMock(return_value=(True, None))
    x402.payment_required_response = MagicMock(return_value={
        "error": "Payment required",
        "x402": {
            "network": "base",
            "token": "USDC",
            "recipient": "0xTestWallet",
            "amount_usdc": 0.05,
        },
    })

    # anthropic
    anthropic = _make_stub_module("anthropic")
    mock_client = MagicMock()
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text="{}")]
    mock_client.messages.create = MagicMock(return_value=mock_message)
    anthropic.Anthropic = MagicMock(return_value=mock_client)
    # Store client for reuse in tests
    anthropic._mock_client = mock_client

    # mcp
    _make_stub_module("mcp")
    _make_stub_module("mcp.server")
    fastmcp = _make_stub_module("mcp.server.fastmcp")

    class FakeMCP:
        def __init__(self, *a, **kw):
            pass
        def tool(self):
            def decorator(fn):
                return fn
            return decorator
        def streamable_http_app(self):
            return MagicMock()

    fastmcp.FastMCP = FakeMCP

    # pydantic
    pydantic = _make_stub_module("pydantic")
    pydantic.Field = MagicMock(return_value=None)

    # starlette
    starlette = _make_stub_module("starlette")
    starlette_apps = _make_stub_module("starlette.applications")
    starlette_apps.Starlette = MagicMock()
    starlette_req = _make_stub_module("starlette.requests")
    starlette_req.Request = MagicMock()
    starlette_resp = _make_stub_module("starlette.responses")
    starlette_resp.JSONResponse = MagicMock()
    starlette_routing = _make_stub_module("starlette.routing")
    starlette_routing.Route = MagicMock()


_setup_stubs()

os.environ.setdefault("PORT", "8000")

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "invoiceparser_server",
    str(Path(__file__).parent.parent / "server.py"),
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

parse_invoice = _mod.parse_invoice
parse_receipt = _mod.parse_receipt
extract_line_items = _mod.extract_line_items
extract_totals = _mod.extract_totals
validate_invoice = _mod.validate_invoice
export_to_csv = _mod.export_to_csv
_resolve_file = _mod._resolve_file
_auth_check = _mod._auth_check


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok(response: str) -> dict:
    return json.loads(response)


class TempFile:
    """Context manager that creates a temp file with given content."""

    def __init__(self, suffix=".pdf", content=b"%PDF-1.4 placeholder"):
        self.suffix = suffix
        self.content = content

    def __enter__(self):
        self._f = tempfile.NamedTemporaryFile(suffix=self.suffix, delete=False)
        self._f.write(self.content)
        self._f.flush()
        self._f.close()
        self.path = self._f.name
        return self.path

    def __exit__(self, *_):
        try:
            os.unlink(self.path)
        except OSError:
            pass


def _set_vision_response(json_dict: dict):
    """Configure the mock Anthropic client to return a specific JSON payload."""
    text = json.dumps(json_dict)
    sys.modules["anthropic"]._mock_client.messages.create.return_value.content[0].text = text


# ---------------------------------------------------------------------------
# Tests — _resolve_file
# ---------------------------------------------------------------------------

class TestResolveFile(unittest.TestCase):

    def test_missing_file_returns_error(self):
        path, err = _resolve_file("/nonexistent/invoice.pdf")
        self.assertIsNone(path)
        self.assertIn("not found", err.lower())

    def test_existing_file_returns_path(self):
        with TempFile() as f:
            path, err = _resolve_file(f)
        self.assertIsNone(err)
        self.assertIsNotNone(path)


# ---------------------------------------------------------------------------
# Tests — _auth_check
# ---------------------------------------------------------------------------

class TestAuthCheck(unittest.TestCase):

    def test_valid_api_key_returns_none(self):
        result = _auth_check("ip_free_test", None, "parse_invoice", 0.05)
        self.assertIsNone(result)

    def test_no_auth_returns_payment_required_json(self):
        result = _auth_check(None, None, "parse_invoice", 0.05)
        self.assertIsNotNone(result)
        data = json.loads(result)
        self.assertIn("x402", data)

    def test_invalid_api_key_returns_error_json(self):
        sys.modules["auth"].validate_and_charge = MagicMock(
            return_value=(False, "Invalid API key")
        )
        result = _auth_check("bad_key", None, "parse_invoice", 0.05)
        self.assertIsNotNone(result)
        data = json.loads(result)
        self.assertFalse(data.get("ok"))
        # Restore
        sys.modules["auth"].validate_and_charge = MagicMock(return_value=(True, None))

    def test_x402_replay_rejected(self):
        sys.modules["x402"].is_proof_used = MagicMock(return_value=True)
        result = _auth_check(None, "0xreplayedhash", "parse_invoice", 0.05)
        self.assertIsNotNone(result)
        data = json.loads(result)
        self.assertFalse(data.get("ok"))
        self.assertIn("already used", data.get("error", ""))
        # Restore
        sys.modules["x402"].is_proof_used = MagicMock(return_value=False)


# ---------------------------------------------------------------------------
# Tests — parse_invoice
# ---------------------------------------------------------------------------

class TestParseInvoice(unittest.TestCase):

    def setUp(self):
        _set_vision_response({
            "ok": True,
            "document_type": "invoice",
            "vendor": {"name": "Acme Corp", "address": "123 Main St"},
            "invoice_number": "INV-001",
            "invoice_date": "2025-01-15",
            "due_date": "2025-02-15",
            "currency": "USD",
            "line_items": [
                {"description": "Consulting", "quantity": 10, "unit_price": 150.0, "total": 1500.0}
            ],
            "subtotal": 1500.0,
            "tax_amount": 120.0,
            "total": 1620.0,
            "amount_due": 1620.0,
        })

    def test_missing_auth_returns_payment_info(self):
        with TempFile() as f:
            result = _ok(parse_invoice(file_path=f))
        # No auth provided — should return payment-required or error
        self.assertFalse(result.get("ok", True) and "error" not in result)

    def test_valid_api_key_returns_parsed_data(self):
        with TempFile() as f:
            result = _ok(parse_invoice(file_path=f, api_key="ip_free_test"))
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("document_type"), "invoice")
        self.assertIn("vendor", result)
        self.assertIn("line_items", result)

    def test_nonexistent_file_returns_error(self):
        result = _ok(parse_invoice(
            file_path="/no/such/invoice.pdf",
            api_key="ip_free_test",
        ))
        self.assertFalse(result.get("ok"))

    def test_response_contains_totals(self):
        with TempFile() as f:
            result = _ok(parse_invoice(file_path=f, api_key="ip_free_test"))
        self.assertIn("total", result)
        self.assertIn("subtotal", result)

    def test_x402_payment_proof_accepted(self):
        with TempFile() as f:
            result = _ok(parse_invoice(
                file_path=f,
                payment_proof="0xvalid_hash_abc",
            ))
        self.assertTrue(result.get("ok"))


# ---------------------------------------------------------------------------
# Tests — parse_receipt
# ---------------------------------------------------------------------------

class TestParseReceipt(unittest.TestCase):

    def setUp(self):
        _set_vision_response({
            "ok": True,
            "document_type": "receipt",
            "merchant": {"name": "Coffee Shop", "address": "456 Oak Ave"},
            "date": "2025-03-01",
            "items": [
                {"name": "Latte", "quantity": 2, "unit_price": 4.50, "total": 9.00}
            ],
            "subtotal": 9.00,
            "tax": 0.72,
            "total": 9.72,
            "payment_method": "credit_card",
        })

    def test_parse_receipt_returns_merchant(self):
        with TempFile(suffix=".png", content=b"\x89PNG\r\n") as f:
            result = _ok(parse_receipt(file_path=f, api_key="ip_free_test"))
        self.assertTrue(result.get("ok"))
        self.assertIn("merchant", result)

    def test_parse_receipt_returns_items(self):
        with TempFile(suffix=".jpg", content=b"\xff\xd8\xff") as f:
            result = _ok(parse_receipt(file_path=f, api_key="ip_free_test"))
        self.assertIn("items", result)

    def test_missing_auth_no_ok(self):
        with TempFile() as f:
            result = _ok(parse_receipt(file_path=f))
        self.assertFalse(result.get("ok", True) and "x402" not in result)


# ---------------------------------------------------------------------------
# Tests — extract_line_items
# ---------------------------------------------------------------------------

class TestExtractLineItems(unittest.TestCase):

    def setUp(self):
        _set_vision_response({
            "ok": True,
            "line_items": [
                {"description": "Widget A", "quantity": 5, "unit_price": 10.0, "total": 50.0},
                {"description": "Widget B", "quantity": 2, "unit_price": 25.0, "total": 50.0},
            ],
            "item_count": 2,
        })

    def test_returns_only_line_items(self):
        with TempFile() as f:
            result = _ok(extract_line_items(file_path=f, api_key="ip_free_test"))
        self.assertTrue(result.get("ok"))
        self.assertIn("line_items", result)
        self.assertNotIn("vendor", result)
        self.assertNotIn("invoice_number", result)

    def test_item_count_matches_array_length(self):
        with TempFile() as f:
            result = _ok(extract_line_items(file_path=f, api_key="ip_free_test"))
        items = result.get("line_items", [])
        self.assertEqual(result.get("item_count"), len(items))


# ---------------------------------------------------------------------------
# Tests — extract_totals
# ---------------------------------------------------------------------------

class TestExtractTotals(unittest.TestCase):

    def setUp(self):
        _set_vision_response({
            "ok": True,
            "currency": "USD",
            "subtotal": 100.0,
            "discount": 0.0,
            "tax_amount": 8.0,
            "tax_rate": 8.0,
            "shipping": 5.0,
            "total": 113.0,
            "amount_due": 113.0,
            "due_date": "2025-04-01",
        })

    def test_returns_totals_only(self):
        with TempFile() as f:
            result = _ok(extract_totals(file_path=f, api_key="ip_free_test"))
        self.assertTrue(result.get("ok"))
        self.assertIn("total", result)
        self.assertIn("subtotal", result)
        self.assertIn("tax_amount", result)
        # Should not contain line items
        self.assertNotIn("line_items", result)

    def test_currency_present(self):
        with TempFile() as f:
            result = _ok(extract_totals(file_path=f, api_key="ip_free_test"))
        self.assertEqual(result.get("currency"), "USD")


# ---------------------------------------------------------------------------
# Tests — validate_invoice
# ---------------------------------------------------------------------------

class TestValidateInvoice(unittest.TestCase):

    def test_valid_invoice_returns_valid_true(self):
        _set_vision_response({
            "ok": True,
            "valid": True,
            "issues": [],
            "summary": {
                "line_items_checked": 3,
                "subtotal": 300.0,
                "tax": 24.0,
                "total": 324.0,
                "currency": "USD",
            },
        })
        with TempFile() as f:
            result = _ok(validate_invoice(file_path=f, api_key="ip_free_test"))
        self.assertTrue(result.get("ok"))
        self.assertTrue(result.get("valid"))
        self.assertEqual(result.get("issues"), [])

    def test_invalid_invoice_returns_issues(self):
        _set_vision_response({
            "ok": True,
            "valid": False,
            "issues": [
                {
                    "field": "line_item_1_total",
                    "expected": 50.0,
                    "found": 45.0,
                    "description": "quantity (5) x unit_price (10) = 50, but total shows 45",
                }
            ],
            "summary": {
                "line_items_checked": 1,
                "subtotal": 45.0,
                "tax": 3.60,
                "total": 48.60,
                "currency": "USD",
            },
        })
        with TempFile() as f:
            result = _ok(validate_invoice(file_path=f, api_key="ip_free_test"))
        self.assertTrue(result.get("ok"))
        self.assertFalse(result.get("valid"))
        self.assertGreater(len(result.get("issues", [])), 0)

    def test_missing_auth_returns_error(self):
        with TempFile() as f:
            result = _ok(validate_invoice(file_path=f))
        self.assertFalse(result.get("ok", True) and "x402" not in result)


# ---------------------------------------------------------------------------
# Tests — export_to_csv
# ---------------------------------------------------------------------------

class TestExportToCSV(unittest.TestCase):

    def setUp(self):
        _set_vision_response({
            "document_type": "invoice",
            "vendor_or_merchant": "Acme Corp",
            "date": "2025-01-15",
            "invoice_or_receipt_number": "INV-001",
            "subtotal": 1500.0,
            "tax": 120.0,
            "total": 1620.0,
            "currency": "USD",
            "due_date": "2025-02-15",
            "payment_method": "",
        })

    def test_export_creates_csv_with_header(self):
        with TempFile() as f1, TempFile() as f2:
            with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as out:
                out_path = out.name

            try:
                result = _ok(export_to_csv(
                    file_paths=[f1, f2],
                    output_path=out_path,
                    api_key="ip_free_test",
                ))
                self.assertTrue(result.get("ok"))
                self.assertEqual(result.get("rows_written"), 2)

                with open(out_path, newline="", encoding="utf-8") as csvf:
                    reader = csv.DictReader(csvf)
                    rows = list(reader)
                self.assertEqual(len(rows), 2)
                self.assertIn("vendor_merchant", rows[0])
                self.assertIn("total", rows[0])
            finally:
                os.unlink(out_path)

    def test_too_many_files_returns_error(self):
        paths = ["/fake/file.pdf"] * 21
        result = _ok(export_to_csv(
            file_paths=paths,
            output_path="/tmp/out.csv",
            api_key="ip_free_test",
        ))
        self.assertFalse(result.get("ok"))
        self.assertIn("20", result.get("error", ""))

    def test_missing_auth_returns_error(self):
        result = _ok(export_to_csv(
            file_paths=["/fake/file.pdf"],
            output_path="/tmp/out.csv",
        ))
        self.assertFalse(result.get("ok", True) and "x402" not in result)

    def test_nonexistent_files_reported_as_errors(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as out:
            out_path = out.name

        try:
            result = _ok(export_to_csv(
                file_paths=["/no/such/file1.pdf", "/no/such/file2.pdf"],
                output_path=out_path,
                api_key="ip_free_test",
            ))
            # All files fail — should return error
            self.assertFalse(result.get("ok"))
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Tests — response shape invariants
# ---------------------------------------------------------------------------

class TestResponseShapeInvariants(unittest.TestCase):
    """All tools must return valid JSON. Error responses must include 'ok': false."""

    def _check_json(self, response: str):
        data = json.loads(response)  # must not raise
        self.assertIn("ok", data)
        return data

    def test_parse_invoice_bad_file_is_valid_json(self):
        data = self._check_json(
            parse_invoice(file_path="/missing.pdf", api_key="ip_free_test")
        )
        self.assertFalse(data["ok"])

    def test_parse_receipt_bad_file_is_valid_json(self):
        data = self._check_json(
            parse_receipt(file_path="/missing.pdf", api_key="ip_free_test")
        )
        self.assertFalse(data["ok"])

    def test_extract_line_items_bad_file_is_valid_json(self):
        data = self._check_json(
            extract_line_items(file_path="/missing.pdf", api_key="ip_free_test")
        )
        self.assertFalse(data["ok"])

    def test_validate_invoice_bad_file_is_valid_json(self):
        data = self._check_json(
            validate_invoice(file_path="/missing.pdf", api_key="ip_free_test")
        )
        self.assertFalse(data["ok"])


if __name__ == "__main__":
    unittest.main()
