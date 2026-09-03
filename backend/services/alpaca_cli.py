"""Safe wrapper around Alpaca's structured-output CLI.

The adapter never enables live trading and never includes credentials in output.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import date, timedelta
from typing import Any

from config import Settings


class AlpacaCliError(RuntimeError):
    pass


class AlpacaCli:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def available(self) -> bool:
        return shutil.which(self.settings.alpaca_cli_bin) is not None

    def _environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env["ALPACA_API_KEY"] = self.settings.alpaca_api_key
        env["ALPACA_SECRET_KEY"] = self.settings.alpaca_secret_key
        env["ALPACA_LIVE_TRADE"] = "false"
        env["ALPACA_OUTPUT"] = "json"
        env["ALPACA_QUIET"] = "1"
        return env

    def run(self, *args: str, timeout: int = 35) -> Any:
        if not self.available:
            raise AlpacaCliError(
                "Alpaca CLI was not found. Install github.com/alpacahq/cli or use VOLITION_MODE=demo."
            )
        if not self.settings.paper_credentials_configured:
            raise AlpacaCliError("Paper-account credentials are not configured.")

        command = [self.settings.alpaca_cli_bin, *args]
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=self._environment(),
            check=False,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "unknown CLI error").strip()
            raise AlpacaCliError(f"Alpaca CLI failed ({' '.join(args[:3])}): {detail[:600]}")
        raw = (proc.stdout or "").strip()
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AlpacaCliError("Alpaca CLI did not return valid JSON.") from exc

    def account(self) -> Any:
        return self.run("account", "get")

    def positions(self) -> Any:
        return self.run("position", "list")

    def option_chain(
        self,
        underlying: str,
        spot: float | None = None,
        expiration: str | None = None,
    ) -> Any:
        args = [
            "data",
            "option",
            "chain",
            "--underlying-symbol",
            underlying,
            "--feed",
            "indicative",
            "--limit",
            "1000",
        ]
        if expiration:
            args.extend(["--expiration-date", expiration])
        else:
            args.extend(
                [
                    "--expiration-date-gte",
                    (date.today() + timedelta(days=7)).isoformat(),
                    "--expiration-date-lte",
                    (date.today() + timedelta(days=60)).isoformat(),
                ]
            )
        if spot and spot > 0:
            args.extend(
                [
                    "--strike-price-gte",
                    f"{spot * 0.84:.2f}",
                    "--strike-price-lte",
                    f"{spot * 1.16:.2f}",
                ]
            )
        return self.run(*args, timeout=60)

    def stock_snapshot(self, symbol: str) -> Any:
        return self.run("data", "stock", "snapshot", "--symbol", symbol)

    def news(self, symbol: str) -> Any:
        return self.run("data", "news", "--symbols", symbol)

    def clock(self) -> Any:
        return self.run("clock")

    @staticmethod
    def evidence(command: str) -> str:
        return f"Alpaca CLI · {command} · structured JSON"
