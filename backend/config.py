"""Environment-backed settings for Volition."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


ROOT = Path(os.getenv("VOLITION_PROJECT_ROOT", str(Path(__file__).resolve().parents[1])))


class Settings(BaseSettings):
    """Runtime configuration with paper-safe defaults."""

    model_config = SettingsConfigDict(
        env_file=(ROOT / ".env", ROOT / "backend" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    volition_mode: str = "demo"
    execution_mode: str = "preview"
    allow_order_submission: bool = False
    scheduler_enabled: bool = True
    agent_interval_minutes: int = Field(default=15, ge=5, le=240)
    operator_api_key: str = ""

    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_paper_trade: bool = True
    hackathon_account_id: str = ""
    hackathon_starting_balance: float = 100_000.0
    hackathon_start_date: str = "2026-08-28"
    alpaca_cli_bin: str = "alpaca"
    alpaca_output: str = "json"

    featherless_api_key: str = ""
    featherless_base_url: str = "https://api.featherless.ai/v1"
    featherless_model: str = "Qwen/Qwen2.5-7B-Instruct"
    reasoning_api_key: str = ""
    reasoning_base_url: str = ""
    reasoning_model: str = ""
    reasoning_provider: str = ""
    reasoning_allow_unauthenticated: bool = False
    # The three opinions are sent together and a single-slot Ollama answers them one by one (~50 s each on 2 CPUs).
    reasoning_timeout_seconds: int = Field(default=240, ge=30, le=600)
    # off: do nothing. shadow: audit-log opinions that misstate the supplied evidence.
    # enforce: also downgrade such a model opinion to abstain (committee is advisory either way).
    factcheck_mode: Literal["off", "shadow", "enforce"] = "off"

    # Optional free intelligence providers. Missing keys never block trading.
    marketaux_api_token: str = ""
    fred_api_key: str = ""
    sec_user_agent: str = ""
    intelligence_cache_minutes: int = Field(default=15, ge=5, le=240)
    deep_scan_limit: int = Field(default=5, ge=1, le=8)
    symbol_cooldown_minutes: int = Field(default=60, ge=15, le=240)

    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )
    watchlist: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["SPY", "QQQ", "NVDA", "AAPL", "AMD"]
    )

    # Offline backtests (see the PR notes): the trend signal has no edge and bearish spreads fight the market's drift;
    # condors have a small premium edge on liquid index ETFs only. Each switch can be reverted from the environment.
    bearish_spreads_enabled: bool = False                # BEARISH_SPREADS_ENABLED=true restores bear put spreads
    condor_symbols: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["SPY", "QQQ", "IWM", "DIA"])   # empty = any symbol
    # Realized volatility after entry averaged 0.77-0.81 of implied (SPY/VIX, QQQ/VXN, 2005-2026). The stress test
    # simulates credit structures at this share of implied vol instead of assuming the premium away. 1.0 = old behaviour.
    credit_vol_factor: float = Field(default=0.85, ge=0.5, le=1.0)
    # The stress test deducts trading costs from a condor whose measured premium edge is only +6% of risk after costs
    # (2005-2026 SPY/QQQ simulation), so index-ETF condors may pass with expected P&L down to this share of max loss
    # below zero. 0 restores the strict "positive expected P&L" rule.
    condor_expected_pnl_floor_pct: float = Field(default=5.0, ge=0.0, le=15.0)

    max_risk_per_trade_pct: float = 1.25
    max_portfolio_open_risk_pct: float = 8.0
    max_symbol_open_risk_pct: float = 2.5
    max_directional_open_risk_pct: float = 4.0
    max_daily_loss_pct: float = 2.0
    max_positions: int = 4
    max_open_structures: int = 8
    min_open_interest: int = 500
    max_bid_ask_spread_pct: float = 8.0
    max_quote_age_seconds: int = Field(default=300, ge=30, le=1_800)
    min_dte: int = 7
    max_dte: int = 35

    @field_validator("cors_origins", "watchlist", "condor_symbols", mode="before")
    @classmethod
    def split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("volition_mode")
    @classmethod
    def valid_mode(cls, value: str) -> str:
        value = value.lower()
        if value not in {"demo", "paper"}:
            raise ValueError("VOLITION_MODE must be demo or paper")
        return value

    @field_validator("execution_mode")
    @classmethod
    def valid_execution_mode(cls, value: str) -> str:
        value = value.lower()
        if value not in {"preview", "paper"}:
            raise ValueError("EXECUTION_MODE must be preview or paper")
        return value

    @property
    def paper_credentials_configured(self) -> bool:
        return bool(self.alpaca_api_key and self.alpaca_secret_key)

    @property
    def can_submit_orders(self) -> bool:
        return (
            self.volition_mode == "paper"
            and self.execution_mode == "paper"
            and self.alpaca_paper_trade
            and self.allow_order_submission
            and self.paper_credentials_configured
        )

    def expected_pnl_floor(self, strategy: str, symbol: str, max_loss: float) -> float:
        """Lowest expected P&L the stress test accepts: zero, except for condors on the listed index ETFs."""

        if strategy != "iron_condor" or not self.condor_expected_pnl_floor_pct:
            return 0.0
        if symbol.upper() not in {item.upper() for item in self.condor_symbols}:
            return 0.0
        return -self.condor_expected_pnl_floor_pct / 100 * max_loss

    @property
    def operator_auth_required(self) -> bool:
        """Paper deployments fail closed when no operator credential exists."""

        return self.volition_mode == "paper" or bool(self.operator_api_key)

    @property
    def effective_reasoning_api_key(self) -> str:
        return self.reasoning_api_key or self.featherless_api_key

    @property
    def effective_reasoning_base_url(self) -> str:
        return self.reasoning_base_url or self.featherless_base_url

    @property
    def effective_reasoning_model(self) -> str:
        return self.reasoning_model or self.featherless_model

    @property
    def effective_reasoning_provider(self) -> str:
        return self.reasoning_provider or ("Featherless" if self.featherless_api_key else "")

    @property
    def reasoning_enabled(self) -> bool:
        return bool(
            self.effective_reasoning_base_url
            and self.effective_reasoning_model
            and (self.effective_reasoning_api_key or self.reasoning_allow_unauthenticated)
        )

    @property
    def reasoning_mode(self) -> str:
        if not self.reasoning_enabled:
            return "deterministic_fallback"
        return "local_model" if self.reasoning_allow_unauthenticated else "hosted_model"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
