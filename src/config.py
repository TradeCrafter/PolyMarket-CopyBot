"""
Configuration for Polymarket Copy Trading Bot
"""
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class WalletConfig:
    """Target wallet to copy trades from"""
    address: str
    label: str = ""
    copy_ratio: float = 1.0  # 0.1 = 10% of target's trade size


@dataclass
class BotConfig:
    # === YOUR WALLET ===
    private_key: str = ""
    funder_address: str = ""  # Your Polymarket proxy wallet address
    signature_type: int = 2   # 0=EOA, 1=Magic/email, 2=Safe proxy

    # === TARGET WALLETS ===
    target_wallets: list[WalletConfig] = field(default_factory=list)

    # === RISK MANAGEMENT ===
    min_trade_usdc: float = 5.0       # Minimum trade size in USDC
    max_trade_usdc: float = 500.0     # Maximum single trade size in USDC
    max_total_exposure: float = 5000.0 # Maximum total portfolio exposure
    max_per_market: float = 1000.0    # Maximum per market
    max_daily_loss: float = 200.0     # Stop trading if daily loss exceeds this

    # === BOT SETTINGS ===
    poll_interval_seconds: int = 5    # How often to check for new trades
    dry_run: bool = True              # If True, log trades but don't execute
    auto_redeem: bool = True          # Auto-redeem resolved positions
    redeem_interval_minutes: int = 60

    # === FILTERS ===
    skip_sports: bool = False
    min_target_trade_usdc: float = 10.0  # Ignore target trades smaller than this
    max_price: float = 0.95              # Don't buy above this price
    min_price: float = 0.05              # Don't buy below this price

    # === API ENDPOINTS ===
    clob_url: str = "https://clob.polymarket.com"
    gamma_url: str = "https://gamma-api.polymarket.com"
    data_api_url: str = "https://data-api.polymarket.com"
    chain_id: int = 137  # Polygon

    # === TELEGRAM ===
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_enabled: bool = False
    telegram_notify_trades: bool = True
    telegram_notify_errors: bool = True
    telegram_notify_risk: bool = True
    telegram_summary_hours: int = 6
    telegram_commands: bool = True

    # === SIZING ===
    # "fixed_ratio" | "fixed_amount" | "proportional" | "kelly" |
    # "tiered" | "confidence" | "volatility_scaled" | "ensemble"
    sizing_strategy: str = "fixed_ratio"
    sizing_params: dict = field(default_factory=dict)
    # For ensemble mode: list of {"strategy": str, "weight": float, "params": dict}
    sizing_ensemble: list[dict] = field(default_factory=list)

    # === LOGGING ===
    log_level: str = "INFO"
    log_file: str = "copy_trader.log"

    @classmethod
    def from_env(cls) -> "BotConfig":
        """Load configuration from environment variables"""
        config = cls()
        config.private_key = os.getenv("POLYMARKET_PRIVATE_KEY", "")
        config.funder_address = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
        config.signature_type = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "2"))

        config.dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
        config.poll_interval_seconds = int(os.getenv("POLL_INTERVAL", "5"))
        config.min_trade_usdc = float(os.getenv("MIN_TRADE_USDC", "5"))
        config.max_trade_usdc = float(os.getenv("MAX_TRADE_USDC", "500"))
        config.max_total_exposure = float(os.getenv("MAX_TOTAL_EXPOSURE", "5000"))
        config.max_per_market = float(os.getenv("MAX_PER_MARKET", "1000"))
        config.max_daily_loss = float(os.getenv("MAX_DAILY_LOSS", "200"))
        config.skip_sports = os.getenv("SKIP_SPORTS", "false").lower() == "true"
        config.log_level = os.getenv("LOG_LEVEL", "INFO")

        # Telegram
        config.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        config.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        config.telegram_enabled = bool(config.telegram_bot_token and config.telegram_chat_id)
        config.telegram_summary_hours = int(os.getenv("TELEGRAM_SUMMARY_HOURS", "6"))

        # Sizing
        config.sizing_strategy = os.getenv("SIZING_STRATEGY", "fixed_ratio")

        # Parse target wallets from env: TARGET_WALLETS="addr1:label1:ratio1,addr2:label2:ratio2"
        wallets_str = os.getenv("TARGET_WALLETS", "")
        if wallets_str:
            for entry in wallets_str.split(","):
                parts = entry.strip().split(":")
                addr = parts[0]
                label = parts[1] if len(parts) > 1 else ""
                ratio = float(parts[2]) if len(parts) > 2 else 1.0
                config.target_wallets.append(WalletConfig(addr, label, ratio))

        return config

    @classmethod
    def from_file(cls, path: str) -> "BotConfig":
        """Load configuration from a JSON file"""
        import json
        with open(path) as f:
            data = json.load(f)

        config = cls()
        for key, value in data.items():
            if key == "target_wallets":
                config.target_wallets = [
                    WalletConfig(**w) for w in value
                ]
            elif hasattr(config, key):
                setattr(config, key, value)
        return config
