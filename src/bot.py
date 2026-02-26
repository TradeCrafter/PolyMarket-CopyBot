"""
Copy Trading Bot - Main orchestrator
"""
import logging
import asyncio
import signal
import json
from datetime import datetime, timezone
from pathlib import Path

from .config import BotConfig, WalletConfig
from .api_client import PolymarketClient, Trade
from .executor import TradeExecutor, OrderResult
from .risk_manager import RiskManager
from .watcher import WalletWatcher
from .telegram_notifier import TelegramNotifier
from .sizing import SizingEngine, SizingInput, SizingStrategy

logger = logging.getLogger(__name__)

BANNER = """
╔══════════════════════════════════════════════════════════╗
║         🎯 POLYMARKET COPY TRADING BOT 🎯                ║
║                                                          ║
║  Monitor target wallets and replicate their trades       ║
║  with configurable risk management.                      ║
╚══════════════════════════════════════════════════════════╝
"""


class CopyTradingBot:
    """Main bot that coordinates watching, risk management, and execution"""

    def __init__(self, config: BotConfig):
        self.config = config
        self.client = PolymarketClient(
            data_api_url=config.data_api_url,
            gamma_url=config.gamma_url,
            clob_url=config.clob_url,
        )
        self.executor = TradeExecutor(config)
        self.risk = RiskManager(config)
        self.watcher: WalletWatcher = None
        self.telegram: Optional[TelegramNotifier] = None
        self.sizing: SizingEngine = None
        self._running = False
        self._start_time = None
        self._trade_log: list[dict] = []
        self._log_file = Path("trade_history.json")

    async def start(self):
        """Start the copy trading bot"""
        print(BANNER)
        self._setup_logging()

        logger.info("=" * 60)
        logger.info("Starting Polymarket Copy Trading Bot")
        logger.info(f"Mode: {'DRY RUN' if self.config.dry_run else '🔴 LIVE TRADING'}")
        logger.info(f"Target wallets: {len(self.config.target_wallets)}")
        logger.info(f"Poll interval: {self.config.poll_interval_seconds}s")
        logger.info(f"Max trade: ${self.config.max_trade_usdc}")
        logger.info(f"Max exposure: ${self.config.max_total_exposure}")
        logger.info(f"Max daily loss: ${self.config.max_daily_loss}")
        logger.info("=" * 60)

        # Initialize sizing engine
        if self.config.sizing_ensemble:
            self.sizing = SizingEngine.from_config({"ensemble": self.config.sizing_ensemble})
        else:
            self.sizing = SizingEngine.from_config({
                "strategy": self.config.sizing_strategy,
                "params": self.config.sizing_params,
            })
        logger.info(f"Sizing strategy: {self.config.sizing_strategy}")

        # Initialize Telegram
        if self.config.telegram_enabled:
            self.telegram = TelegramNotifier(
                bot_token=self.config.telegram_bot_token,
                chat_id=self.config.telegram_chat_id,
                notify_trades=self.config.telegram_notify_trades,
                notify_errors=self.config.telegram_notify_errors,
                notify_risk_events=self.config.telegram_notify_risk,
                notify_summary=True,
                summary_interval_hours=self.config.telegram_summary_hours,
                commands_enabled=self.config.telegram_commands,
            )
            self.telegram.set_bot_ref(self)
            await self.telegram.start_polling()
            await self.telegram.notify_startup(
                mode="DRY_RUN" if self.config.dry_run else "LIVE",
                num_wallets=len(self.config.target_wallets),
                max_exposure=self.config.max_total_exposure,
                sizing_strategy=self.config.sizing_strategy,
            )
            logger.info("Telegram notifications enabled ✅")
        else:
            logger.info("Telegram notifications disabled")

        # Print target wallets
        for i, w in enumerate(self.config.target_wallets, 1):
            label = w.label or w.address[:16]
            logger.info(f"  [{i}] {label} (ratio: {w.copy_ratio}x)")

        # Initialize trade executor
        if not self.config.dry_run:
            try:
                self.executor.initialize()
                addr = self.executor.get_address()
                logger.info(f"Trading wallet: {addr}")
            except Exception as e:
                logger.error(f"Failed to initialize executor: {e}")
                logger.info("Falling back to DRY RUN mode")
                self.config.dry_run = True

        # Initialize wallet watcher
        self.watcher = WalletWatcher(
            client=self.client,
            wallets=self.config.target_wallets,
            poll_interval=self.config.poll_interval_seconds,
            on_new_trade=self._handle_new_trade,
        )

        # Setup graceful shutdown
        self._running = True
        self._start_time = datetime.now(timezone.utc)
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))
            except NotImplementedError:
                pass  # Windows

        # Start watching
        try:
            await self.watcher.start()
        except asyncio.CancelledError:
            pass
        finally:
            await self.cleanup()

    async def stop(self):
        """Graceful shutdown"""
        logger.info("🛑 Shutting down...")
        self._running = False
        if self.watcher:
            self.watcher.stop()

    async def cleanup(self):
        """Cleanup resources"""
        if self.telegram:
            await self.telegram.notify_shutdown("Graceful shutdown")
            await self.telegram.close()
        await self.client.close()
        self._save_trade_log()
        logger.info("Bot stopped. Trade history saved.")

    async def _handle_new_trade(self, trade: Trade, wallet_config: WalletConfig):
        """
        Called when a new trade is detected from a target wallet.
        Validates, sizes, and executes the copy trade.
        """
        label = wallet_config.label or trade.wallet[:10]
        logger.info(
            f"\n{'─' * 50}\n"
            f"📥 Processing trade from [{label}]\n"
            f"   Market: {trade.title}\n"
            f"   Side: {trade.side} | Outcome: {trade.outcome}\n"
            f"   Size: {trade.size:.2f} tokens @ ${trade.price:.4f}\n"
            f"   USDC: ${trade.usdc_amount:.2f}\n"
            f"{'─' * 50}"
        )

        # Telegram: notify detection
        if self.telegram:
            await self.telegram.notify_new_trade_detected(
                source_label=label,
                side=trade.side,
                size=trade.size,
                price=trade.price,
                usdc_amount=trade.usdc_amount,
                market_title=trade.title,
                outcome=trade.outcome,
            )

        # === FILTERS ===

        if trade.usdc_amount < self.config.min_target_trade_usdc:
            logger.info(f"⏭️  Skipping: trade too small (${trade.usdc_amount:.2f} < ${self.config.min_target_trade_usdc})")
            return

        if self.config.skip_sports and self._is_sports_market(trade):
            logger.info(f"⏭️  Skipping: sports market filtered")
            return

        # === FETCH MARKET DATA ===

        current_price = await self.client.get_best_price(trade.asset, trade.side) or trade.price
        spread_data = {}
        try:
            spread_data = await self.client.get_spread(trade.asset)
        except Exception:
            pass
        spread = float(spread_data.get("spread", 0)) if spread_data else 0.0
        midpoint = float(spread_data.get("mid", 0)) if spread_data else current_price

        # === SIZING ===

        sizing_input = SizingInput(
            target_usdc=trade.usdc_amount,
            target_price=trade.price,
            target_side=trade.side,
            target_size_tokens=trade.size,
            my_balance=max(0, self.config.max_total_exposure - self.risk.total_exposure),
            my_total_exposure=self.risk.total_exposure,
            max_total_exposure=self.config.max_total_exposure,
            current_price=current_price,
            spread=spread,
            midpoint=midpoint,
            copy_ratio=wallet_config.copy_ratio,
        )

        sizing_result = self.sizing.calculate(sizing_input)
        copy_amount = sizing_result.usdc_amount

        logger.info(
            f"📐 Sizing [{sizing_result.strategy_name}]: ${copy_amount:.2f} "
            f"(confidence: {sizing_result.confidence:.2f})"
        )
        if sizing_result.adjustments:
            for adj in sizing_result.adjustments:
                logger.info(f"   ↳ {adj}")
        logger.info(f"   {sizing_result.reasoning}")

        # === RISK CHECK ===

        allowed, reason, adjusted_amount = self.risk.validate_trade(
            side=trade.side,
            usdc_amount=copy_amount,
            price=trade.price,
            token_id=trade.asset,
            condition_id=trade.condition_id,
            market_title=trade.title,
        )

        if not allowed:
            logger.warning(f"🚫 Trade rejected: {reason}")
            self._log_trade(trade, wallet_config, None, rejected=True, reject_reason=reason)
            if self.telegram:
                await self.telegram.notify_trade_rejected(
                    side=trade.side,
                    usdc_amount=copy_amount,
                    market_title=trade.title,
                    reason=reason,
                    source_label=label,
                )
            if self.risk.is_halted and self.telegram:
                await self.telegram.notify_halt(self.risk.halt_reason)
            return

        if adjusted_amount != copy_amount:
            logger.info(f"📐 Risk-adjusted: ${copy_amount:.2f} → ${adjusted_amount:.2f}")

        # === SLIPPAGE CHECK ===

        if current_price and trade.price > 0:
            slippage = abs(current_price - trade.price) / trade.price
            if slippage > 0.10:
                logger.warning(
                    f"⚠️  High slippage: target ${trade.price:.4f}, "
                    f"current ${current_price:.4f} ({slippage:.1%})"
                )
                if self.telegram:
                    await self.telegram.notify_slippage_warning(
                        market_title=trade.title,
                        target_price=trade.price,
                        current_price=current_price,
                        slippage_pct=slippage,
                    )

        # === EXECUTION ===

        result = self.executor.execute_market_order(
            token_id=trade.asset,
            side=trade.side,
            amount_usdc=adjusted_amount,
            market_title=trade.title,
        )

        if result.success:
            self.risk.record_trade(
                side=trade.side,
                usdc_amount=adjusted_amount,
                token_id=trade.asset,
                condition_id=trade.condition_id,
                market_title=trade.title,
            )
            status_emoji = "✅" if not result.is_dry_run else "🔵"
            logger.info(
                f"{status_emoji} Trade executed: {trade.side} ${adjusted_amount:.2f} on '{trade.title}'"
            )
            if self.telegram:
                await self.telegram.notify_trade_executed(
                    side=trade.side,
                    usdc_amount=adjusted_amount,
                    market_title=trade.title,
                    outcome=trade.outcome,
                    order_id=result.order_id,
                    status=result.status,
                    is_dry_run=result.is_dry_run,
                    source_label=label,
                    copy_ratio=wallet_config.copy_ratio,
                    source_usdc=trade.usdc_amount,
                    source_price=trade.price,
                    source_size=trade.size,
                )
        else:
            logger.error(f"❌ Trade failed: {result.error}")
            if self.telegram:
                await self.telegram.notify_trade_failed(
                    side=trade.side,
                    usdc_amount=adjusted_amount,
                    market_title=trade.title,
                    error=result.error,
                )

        self._log_trade(trade, wallet_config, result)

    def _is_sports_market(self, trade: Trade) -> bool:
        """Check if a trade is in a sports market"""
        sports_keywords = [
            "nba", "nfl", "mlb", "nhl", "soccer", "football", "basketball",
            "baseball", "hockey", "tennis", "golf", "mma", "ufc", "boxing",
            "f1", "formula", "premier league", "champions league", "world cup",
            "olympics", "score", "win game", "series", "playoff",
        ]
        title_lower = trade.title.lower()
        slug_lower = trade.slug.lower() + " " + trade.event_slug.lower()
        combined = title_lower + " " + slug_lower
        return any(kw in combined for kw in sports_keywords)

    def _log_trade(
        self,
        trade: Trade,
        wallet_config: WalletConfig,
        result: OrderResult = None,
        rejected: bool = False,
        reject_reason: str = "",
    ):
        """Log trade to history"""
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "source_wallet": trade.wallet,
            "source_label": wallet_config.label,
            "copy_ratio": wallet_config.copy_ratio,
            "market": trade.title,
            "condition_id": trade.condition_id,
            "asset": trade.asset,
            "outcome": trade.outcome,
            "outcome_index": trade.outcome_index,
            "side": trade.side,
            "source_size": trade.size,
            "source_price": trade.price,
            "source_usdc": trade.usdc_amount,
            "rejected": rejected,
            "reject_reason": reject_reason,
        }

        if result:
            entry.update({
                "executed": result.success,
                "execution_usdc": result.usdc_amount,
                "order_id": result.order_id,
                "status": result.status,
                "error": result.error,
                "dry_run": result.is_dry_run,
            })

        self._trade_log.append(entry)

        # Periodic save
        if len(self._trade_log) % 5 == 0:
            self._save_trade_log()

    def _save_trade_log(self):
        """Save trade log to JSON file"""
        try:
            with open(self._log_file, "w") as f:
                json.dump(self._trade_log, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save trade log: {e}")

    def _setup_logging(self):
        """Configure logging (with Windows emoji-safe console output)"""
        import sys
        import io

        log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        date_format = "%Y-%m-%d %H:%M:%S"

        # Force UTF-8 on console to avoid cp1252 emoji crashes on Windows
        if sys.platform == "win32":
            try:
                sys.stdout = io.TextIOWrapper(
                    sys.stdout.buffer, encoding="utf-8", errors="replace"
                )
                sys.stderr = io.TextIOWrapper(
                    sys.stderr.buffer, encoding="utf-8", errors="replace"
                )
            except Exception:
                pass  # Already wrapped or no buffer

        logging.basicConfig(
            level=getattr(logging, self.config.log_level.upper(), logging.INFO),
            format=log_format,
            datefmt=date_format,
            handlers=[
                logging.StreamHandler(
                    stream=open(sys.stdout.fileno(), "w", encoding="utf-8", errors="replace", closefd=False)
                    if sys.platform == "win32"
                    else sys.stdout
                ),
            ],
        )

        # Also log to file (always UTF-8)
        if self.config.log_file:
            fh = logging.FileHandler(self.config.log_file, encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(logging.Formatter(log_format, date_format))
            logging.getLogger().addHandler(fh)

    def get_status(self) -> dict:
        """Get comprehensive bot status"""
        # Calculate uptime
        uptime_str = "?"
        if hasattr(self, "_start_time") and self._start_time:
            delta = datetime.now(timezone.utc) - self._start_time
            hours, remainder = divmod(int(delta.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            if hours > 24:
                days = hours // 24
                hours = hours % 24
                uptime_str = f"{days}d {hours}h {minutes}m"
            else:
                uptime_str = f"{hours}h {minutes}m {seconds}s"

        return {
            "running": self._running,
            "mode": "DRY_RUN" if self.config.dry_run else "LIVE",
            "uptime": uptime_str,
            "risk": self.risk.get_status(),
            "wallets": self.watcher.get_status() if self.watcher else [],
            "total_trades_logged": len(self._trade_log),
        }

    async def get_my_positions(self) -> list[dict]:
        """Fetch real positions from Polymarket for our wallet."""
        try:
            addr = self.config.funder_address
            if not addr:
                addr = self.executor.get_address()
            if not addr:
                return []

            # Resolve funder address to proxy wallet (Polymarket API needs proxy)
            proxy = await self.client.resolve_username(addr)
            if proxy:
                addr = proxy

            raw = await self.client.get_user_positions(addr)
            positions = []
            for p in raw:
                size = float(p.get("size", 0))
                if size <= 0:
                    continue
                positions.append({
                    "title": p.get("title", "?"),
                    "outcome": p.get("outcome", "?"),
                    "size": size,
                    "avg_price": float(p.get("avgPrice", 0)),
                    "cur_price": float(p.get("curPrice", 0)),
                    "initial_value": float(p.get("initialValue", 0)),
                    "current_value": float(p.get("currentValue", 0)),
                    "pnl": float(p.get("cashPnl", 0)),
                    "pnl_pct": float(p.get("percentPnl", 0)),
                    "redeemable": p.get("redeemable", False),
                    "condition_id": p.get("conditionId", ""),
                })
            # Sort by initial_value descending
            positions.sort(key=lambda x: x["initial_value"], reverse=True)
            return positions
        except Exception as e:
            logger.error(f"Failed to fetch positions: {e}")
            return []
