"""
Risk Manager - enforces position limits, daily loss limits, and trade validation
"""
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass, field

from .config import BotConfig

logger = logging.getLogger(__name__)


@dataclass
class DailyStats:
    date: str = ""
    trades_executed: int = 0
    total_bought_usdc: float = 0.0
    total_sold_usdc: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0


class RiskManager:
    """Enforces risk limits and tracks exposure"""

    def __init__(self, config: BotConfig):
        self.config = config
        self._positions: dict[str, float] = {}       # token_id -> USDC exposure
        self._market_exposure: dict[str, float] = {}  # condition_id -> USDC exposure
        self._daily_stats = DailyStats(date=datetime.utcnow().strftime("%Y-%m-%d"))
        self._halted = False
        self._halt_reason = ""
        self._trade_history: list[dict] = []

    @property
    def total_exposure(self) -> float:
        return sum(self._positions.values())

    @property
    def is_halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    def halt(self, reason: str):
        """Emergency halt all trading"""
        self._halted = True
        self._halt_reason = reason
        logger.warning(f"🛑 TRADING HALTED: {reason}")

    def resume(self):
        """Resume trading"""
        self._halted = False
        self._halt_reason = ""
        logger.info("✅ Trading resumed")

    def _check_daily_reset(self):
        """Reset daily stats if new day"""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if self._daily_stats.date != today:
            logger.info(f"New day: resetting daily stats (prev: {self._daily_stats})")
            self._daily_stats = DailyStats(date=today)
            # Auto-resume if halted due to daily loss
            if self._halted and "daily loss" in self._halt_reason.lower():
                self.resume()

    def validate_trade(
        self,
        side: str,
        usdc_amount: float,
        price: float,
        token_id: str,
        condition_id: str,
        market_title: str = "",
    ) -> tuple[bool, str, float]:
        """
        Validate a trade against risk limits.
        
        Returns:
            (allowed, reason, adjusted_amount)
        """
        self._check_daily_reset()

        # Check if halted
        if self._halted:
            return False, f"Trading halted: {self._halt_reason}", 0.0

        # Check price limits
        if price > self.config.max_price:
            return False, f"Price {price:.4f} exceeds max {self.config.max_price}", 0.0

        if price < self.config.min_price:
            return False, f"Price {price:.4f} below min {self.config.min_price}", 0.0

        adjusted_amount = usdc_amount

        if side.upper() == "BUY":
            # Check minimum trade size
            if adjusted_amount < self.config.min_trade_usdc:
                return False, f"Trade ${adjusted_amount:.2f} below minimum ${self.config.min_trade_usdc}", 0.0

            # Cap at max trade size
            if adjusted_amount > self.config.max_trade_usdc:
                adjusted_amount = self.config.max_trade_usdc
                logger.info(f"Capped trade from ${usdc_amount:.2f} to ${adjusted_amount:.2f}")

            # Check total exposure
            new_total = self.total_exposure + adjusted_amount
            if new_total > self.config.max_total_exposure:
                remaining = self.config.max_total_exposure - self.total_exposure
                if remaining < self.config.min_trade_usdc:
                    return False, f"Total exposure ${new_total:.2f} would exceed limit ${self.config.max_total_exposure}", 0.0
                adjusted_amount = min(adjusted_amount, remaining)
                logger.info(f"Reduced trade to ${adjusted_amount:.2f} due to total exposure limit")

            # Check per-market exposure
            current_market = self._market_exposure.get(condition_id, 0)
            new_market = current_market + adjusted_amount
            if new_market > self.config.max_per_market:
                remaining = self.config.max_per_market - current_market
                if remaining < self.config.min_trade_usdc:
                    return False, f"Market exposure ${new_market:.2f} would exceed limit ${self.config.max_per_market}", 0.0
                adjusted_amount = min(adjusted_amount, remaining)
                logger.info(f"Reduced trade to ${adjusted_amount:.2f} due to per-market limit")

            # Check daily loss limit
            if self._daily_stats.realized_pnl < -self.config.max_daily_loss:
                self.halt(f"Daily loss ${abs(self._daily_stats.realized_pnl):.2f} exceeded limit ${self.config.max_daily_loss}")
                return False, self._halt_reason, 0.0

        return True, "OK", round(adjusted_amount, 2)

    def record_trade(
        self,
        side: str,
        usdc_amount: float,
        token_id: str,
        condition_id: str,
        market_title: str = "",
    ):
        """Record a trade execution for tracking"""
        self._check_daily_reset()

        if side.upper() == "BUY":
            self._positions[token_id] = self._positions.get(token_id, 0) + usdc_amount
            self._market_exposure[condition_id] = self._market_exposure.get(condition_id, 0) + usdc_amount
            self._daily_stats.total_bought_usdc += usdc_amount
        else:
            sold = min(usdc_amount, self._positions.get(token_id, 0))
            self._positions[token_id] = max(0, self._positions.get(token_id, 0) - sold)
            self._market_exposure[condition_id] = max(0, self._market_exposure.get(condition_id, 0) - sold)
            self._daily_stats.total_sold_usdc += usdc_amount

        self._daily_stats.trades_executed += 1

        self._trade_history.append({
            "timestamp": datetime.utcnow().isoformat(),
            "side": side,
            "usdc_amount": usdc_amount,
            "token_id": token_id,
            "condition_id": condition_id,
            "market_title": market_title,
        })

        logger.info(
            f"📊 Position update: Total exposure ${self.total_exposure:.2f} | "
            f"Today: {self._daily_stats.trades_executed} trades, "
            f"bought ${self._daily_stats.total_bought_usdc:.2f}, "
            f"sold ${self._daily_stats.total_sold_usdc:.2f}"
        )

    def get_status(self) -> dict:
        """Get current risk status"""
        return {
            "halted": self._halted,
            "halt_reason": self._halt_reason,
            "total_exposure": round(self.total_exposure, 2),
            "max_total_exposure": self.config.max_total_exposure,
            "max_trade_usdc": self.config.max_trade_usdc,
            "max_per_market": self.config.max_per_market,
            "max_daily_loss": self.config.max_daily_loss,
            "exposure_pct": round(self.total_exposure / self.config.max_total_exposure * 100, 1),
            "positions_count": len([v for v in self._positions.values() if v > 0]),
            "markets_count": len([v for v in self._market_exposure.values() if v > 0]),
            "daily_stats": {
                "date": self._daily_stats.date,
                "trades": self._daily_stats.trades_executed,
                "bought_usdc": round(self._daily_stats.total_bought_usdc, 2),
                "sold_usdc": round(self._daily_stats.total_sold_usdc, 2),
            },
        }
