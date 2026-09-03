from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from config import Settings
from main import app, require_operator


class OperatorAuthorizationTests(unittest.TestCase):
    def test_demo_without_configured_key_preserves_local_controls(self) -> None:
        demo = Settings(_env_file=None, volition_mode="demo", operator_api_key="")
        with patch("main.settings", demo):
            self.assertIsNone(require_operator())

    def test_paper_mode_fails_closed_when_key_is_not_configured(self) -> None:
        paper = Settings(_env_file=None, volition_mode="paper", operator_api_key="")
        with patch("main.settings", paper):
            with self.assertRaises(HTTPException) as raised:
                require_operator()
        self.assertEqual(raised.exception.status_code, 503)

    def test_paper_mode_rejects_missing_and_incorrect_keys(self) -> None:
        paper = Settings(_env_file=None, volition_mode="paper", operator_api_key="correct-horse")
        with patch("main.settings", paper):
            for supplied in (None, "wrong-battery"):
                with self.subTest(supplied=supplied):
                    with self.assertRaises(HTTPException) as raised:
                        require_operator(supplied)
                    self.assertEqual(raised.exception.status_code, 401)

    def test_paper_mode_accepts_exact_operator_key(self) -> None:
        paper = Settings(_env_file=None, volition_mode="paper", operator_api_key="correct-horse")
        with patch("main.settings", paper):
            self.assertIsNone(require_operator("correct-horse"))

    def test_public_http_route_cannot_reach_cycle_without_operator_key(self) -> None:
        paper = Settings(_env_file=None, volition_mode="paper", operator_api_key="correct-horse")
        with patch("main.settings", paper):
            response = TestClient(app).post("/api/cycle", json={})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Valid operator access is required for this action.")


if __name__ == "__main__":
    unittest.main()
