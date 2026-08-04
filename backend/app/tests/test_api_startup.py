"""
Application Startup and End-to-End API Security Test Suite (BAGIAN 2)
Guarantees FastAPI application imports cleanly without NameError or ModuleNotFoundError,
and verifies X-API-Key authentication enforcement across all state-changing endpoints.
"""
import os
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

# Direct import of app instance — fails instantly if import errors or NameErrors exist
from app.main import app
from app.core.config import settings


class TestAPIStartupAndAuth(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.test_api_key = "secret_admin_test_key_12345"

    def test_app_startup_and_health_check(self):
        """Verifies root and health endpoints return 200 OK without errors."""
        res_health = self.client.get("/health")
        self.assertEqual(res_health.status_code, 200)
        self.assertEqual(res_health.json(), {"status": "healthy"})

        res_root = self.client.get("/")
        self.assertEqual(res_root.status_code, 200)
        self.assertIn("status", res_root.json())

    def test_retrain_endpoint_security_auth(self):
        """Verifies POST /api/v1/retrain blocks requests without X-API-Key when ADMIN_API_KEY is set."""
        with patch.dict(os.environ, {"ADMIN_API_KEY": self.test_api_key}):
            # Request without header -> MUST be 401
            res_unauth = self.client.post("/api/v1/retrain")
            self.assertEqual(res_unauth.status_code, 401)
            self.assertIn("Unauthorized", res_unauth.json().get("detail", ""))

            # Request with wrong header -> MUST be 401
            res_wrong = self.client.post("/api/v1/retrain", headers={"X-API-Key": "wrong_key"})
            self.assertEqual(res_wrong.status_code, 401)

            # Request with correct header -> Auth passes (returns 503 because scheduler mock not in app.state)
            res_auth = self.client.post("/api/v1/retrain", headers={"X-API-Key": self.test_api_key})
            self.assertNotEqual(res_auth.status_code, 401)

    def test_wallet_endpoints_security_auth(self):
        """Verifies POST/DELETE wallet endpoints block requests without X-API-Key when ADMIN_API_KEY is set."""
        with patch.dict(os.environ, {"ADMIN_API_KEY": self.test_api_key}):
            endpoints_to_test = [
                ("POST", "/api/v1/dashboard/wallets", {"wallet_address": "11111111111111111111111111111111", "label": "test"}),
                ("POST", "/api/v1/dashboard/wallets/11111111111111111111111111111111/approve", {"action": "approve"}),
                ("DELETE", "/api/v1/dashboard/wallets/11111111111111111111111111111111", None),
                ("POST", "/api/v1/dashboard/insights/insight_1/approve", None),
                ("POST", "/api/v1/dashboard/insights/insight_1/reject", None),
                ("POST", "/api/v1/dashboard/insights/trigger", None),
            ]

            for method, path, json_payload in endpoints_to_test:
                if method == "POST":
                    res = self.client.post(path, json=json_payload)
                elif method == "DELETE":
                    res = self.client.delete(path)
                
                self.assertEqual(
                    res.status_code, 
                    401, 
                    f"Endpoint {method} {path} failed to reject unauthorized request! Response: {res.status_code} {res.text}"
                )

                # Test with valid X-API-Key header -> Auth passes (status code is NOT 401)
                headers = {"X-API-Key": self.test_api_key}
                if method == "POST":
                    res_valid = self.client.post(path, json=json_payload, headers=headers)
                elif method == "DELETE":
                    res_valid = self.client.delete(path, headers=headers)

                self.assertNotEqual(
                    res_valid.status_code,
                    401,
                    f"Endpoint {method} {path} rejected VALID X-API-Key header! Response: {res_valid.status_code} {res_valid.text}"
                )


if __name__ == "__main__":
    unittest.main()
