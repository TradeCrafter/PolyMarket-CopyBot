"""
Sizing Strategies - multiple approaches for calculating copy trade size.

Each strategy takes the target's trade info and returns the USDC amount to trade.
Strategies can be combined or swapped via config.
"""
import math
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class SizingStrategy(str, Enum):
    FIXED_RATIO = "fixed_ratio"
    FIXED_AMOUNT = "fixed_amount"
    PROPORTIONAL = "proportional"
    KELLY = "kelly"
    TIERED = "tiered"
    CONFIDENCE = "confidence"
    VOLATILITY_SCALED = "volatility_scaled"


@dataclass
class SizingInput:
    """Input data for sizing calculation"""
    # Target trade info
    target_usdc: float          # How much the target traded in USDC
    target_price: float         # Price the target paid
    target_side: str            # BUY or SELL
    target_size_tokens: float   # Number of tokens

    # Your portfolio state
    my_balance: float           # Available USDC balance
    my_total_exposure: float    # Current total exposure
    max_total_exposure: float   # Max allowed exposure

    # Market info
    current_price: float = 0.0     # Current best price
    spread: float = 0.0            # Current bid-ask spread
    midpoint: float = 0.0          # Order book midpoint
    market_volume_24h: float = 0.0 # 24h volume (if available)

    # Target wallet info
    target_balance: float = 0.0       # Estimated target wallet balance
    target_win_rate: float = 0.0      # Historical win rate (0-1)
    target_avg_return: float = 0.0    # Historical avg return
    copy_ratio: float = 1.0           # Base copy ratio from config


@dataclass
class SizingResult:
    """Output of sizing calculation"""
    usdc_amount: float
    strategy_name: str
    reasoning: str
    confidence: float = 1.0   # 0-1 confidence in the sizing
    adjustments: list[str] = field(default_factory=list)


# ─── BASE CLASS ──────────────────────────────────────────────────────

class BaseSizer(ABC):
    """Base class for all sizing strategies"""

    @abstractmethod
    def calculate(self, inp: SizingInput) -> SizingResult:
        pass

    def _clamp(self, amount: float, min_val: float, max_val: float) -> float:
        return max(min_val, min(amount, max_val))


# ─── STRATEGY: FIXED RATIO ──────────────────────────────────────────

class FixedRatioSizer(BaseSizer):
    """
    Simplest approach: multiply target's trade size by a fixed ratio.
    
    Example: target trades $1000, ratio=0.5 → you trade $500
    """

    def calculate(self, inp: SizingInput) -> SizingResult:
        amount = inp.target_usdc * inp.copy_ratio
        return SizingResult(
            usdc_amount=round(amount, 2),
            strategy_name="fixed_ratio",
            reasoning=f"${inp.target_usdc:.2f} × {inp.copy_ratio}x = ${amount:.2f}",
        )


# ─── STRATEGY: FIXED AMOUNT ─────────────────────────────────────────

class FixedAmountSizer(BaseSizer):
    """
    Always trade the same fixed amount regardless of target's size.
    Good for limiting risk when copying whales.
    
    Params:
        fixed_amount: USDC amount per trade
    """

    def __init__(self, fixed_amount: float = 50.0):
        self.fixed_amount = fixed_amount

    def calculate(self, inp: SizingInput) -> SizingResult:
        return SizingResult(
            usdc_amount=self.fixed_amount,
            strategy_name="fixed_amount",
            reasoning=f"Fixed ${self.fixed_amount:.2f} per trade",
        )


# ─── STRATEGY: PROPORTIONAL (BALANCE-BASED) ─────────────────────────

class ProportionalSizer(BaseSizer):
    """
    Size proportionally based on your balance vs target's estimated balance.
    If target has $100k and trades $5k (5%), and you have $10k, you'd trade $500 (5%).
    
    Falls back to fixed_ratio if target_balance is unknown.
    
    Params:
        min_pct: Minimum % of balance per trade
        max_pct: Maximum % of balance per trade
    """

    def __init__(self, min_pct: float = 0.01, max_pct: float = 0.15):
        self.min_pct = min_pct
        self.max_pct = max_pct

    def calculate(self, inp: SizingInput) -> SizingResult:
        adjustments = []

        if inp.target_balance > 0:
            # Calculate what % of their portfolio the target traded
            target_pct = inp.target_usdc / inp.target_balance
            target_pct = self._clamp(target_pct, self.min_pct, self.max_pct)

            amount = inp.my_balance * target_pct
            adjustments.append(
                f"Target traded {inp.target_usdc / inp.target_balance:.1%} of portfolio → "
                f"clamped to {target_pct:.1%} of ${inp.my_balance:,.0f}"
            )
        else:
            # Fallback: use copy_ratio
            amount = inp.target_usdc * inp.copy_ratio
            adjustments.append(
                f"Target balance unknown, using ratio {inp.copy_ratio}x"
            )

        return SizingResult(
            usdc_amount=round(amount, 2),
            strategy_name="proportional",
            reasoning=f"Proportional sizing: ${amount:.2f}",
            adjustments=adjustments,
        )


# ─── STRATEGY: KELLY CRITERION ──────────────────────────────────────

class KellySizer(BaseSizer):
    """
    Kelly Criterion sizing based on target's historical win rate and edge.
    
    Kelly fraction = (win_rate × avg_win / avg_loss - (1 - win_rate)) / (avg_win / avg_loss)
    
    Uses fractional Kelly (default 0.25x) for safety.
    
    Params:
        kelly_fraction: Fraction of full Kelly to use (0.25 = quarter Kelly)
        default_edge: Assumed edge if no historical data
        max_pct_balance: Maximum % of balance per trade
    """

    def __init__(
        self,
        kelly_fraction: float = 0.25,
        default_edge: float = 0.05,
        max_pct_balance: float = 0.10,
    ):
        self.kelly_fraction = kelly_fraction
        self.default_edge = default_edge
        self.max_pct_balance = max_pct_balance

    def calculate(self, inp: SizingInput) -> SizingResult:
        adjustments = []

        if inp.target_win_rate > 0:
            # Binary outcome: win pays 1/price - 1, lose pays -1
            price = inp.target_price if inp.target_price > 0 else inp.current_price
            if price <= 0 or price >= 1:
                return SizingResult(
                    usdc_amount=0,
                    strategy_name="kelly",
                    reasoning="Invalid price for Kelly calculation",
                )

            # Odds = payout ratio (e.g., price 0.6 → odds = 1/0.6 - 1 = 0.667)
            odds = (1 / price) - 1
            win_rate = inp.target_win_rate

            # Kelly formula for binary bets
            kelly_pct = (win_rate * odds - (1 - win_rate)) / odds

            if kelly_pct <= 0:
                adjustments.append(f"Negative Kelly ({kelly_pct:.3f}) → no edge detected")
                return SizingResult(
                    usdc_amount=0,
                    strategy_name="kelly",
                    reasoning="No edge detected (Kelly ≤ 0)",
                    adjustments=adjustments,
                )

            # Apply fractional Kelly
            adj_kelly = kelly_pct * self.kelly_fraction
            adj_kelly = min(adj_kelly, self.max_pct_balance)

            amount = inp.my_balance * adj_kelly
            adjustments.append(
                f"Win rate: {win_rate:.1%} | Odds: {odds:.2f} | "
                f"Full Kelly: {kelly_pct:.3f} | {self.kelly_fraction}x Kelly: {adj_kelly:.3f}"
            )
        else:
            # No historical data — use default edge with conservative sizing
            adj_kelly = self.default_edge * self.kelly_fraction
            amount = inp.my_balance * adj_kelly
            adjustments.append(
                f"No win rate data, using default edge {self.default_edge:.1%} "
                f"× {self.kelly_fraction}x = {adj_kelly:.3f}"
            )

        return SizingResult(
            usdc_amount=round(amount, 2),
            strategy_name="kelly",
            reasoning=f"Kelly sizing: ${amount:.2f}",
            confidence=min(1.0, inp.target_win_rate * 2) if inp.target_win_rate > 0 else 0.3,
            adjustments=adjustments,
        )


# ─── STRATEGY: TIERED ───────────────────────────────────────────────

class TieredSizer(BaseSizer):
    """
    Different sizing based on the target's trade size brackets.
    Larger trades from the target → proportionally smaller copy (whales protection).
    Smaller conviction trades → proportionally larger copy.
    
    Params:
        tiers: list of (max_usdc_threshold, copy_multiplier)
    """

    def __init__(self, tiers: list[tuple[float, float]] = None):
        self.tiers = tiers or [
            (50, 2.0),       # Target trades ≤$50 → 2x their amount
            (200, 1.0),      # Target trades ≤$200 → 1x
            (1000, 0.5),     # Target trades ≤$1000 → 0.5x
            (5000, 0.2),     # Target trades ≤$5000 → 0.2x
            (float("inf"), 0.1),  # Target trades >$5000 → 0.1x
        ]

    def calculate(self, inp: SizingInput) -> SizingResult:
        multiplier = self.tiers[-1][1]  # Default to last tier

        for threshold, mult in self.tiers:
            if inp.target_usdc <= threshold:
                multiplier = mult
                break

        # Apply base copy_ratio on top
        effective_mult = multiplier * inp.copy_ratio
        amount = inp.target_usdc * effective_mult

        return SizingResult(
            usdc_amount=round(amount, 2),
            strategy_name="tiered",
            reasoning=(
                f"Tier: ${inp.target_usdc:.0f} → {multiplier}x base × "
                f"{inp.copy_ratio}x ratio = {effective_mult}x → ${amount:.2f}"
            ),
        )


# ─── STRATEGY: CONFIDENCE-BASED ─────────────────────────────────────

class ConfidenceSizer(BaseSizer):
    """
    Adjusts sizing based on multiple confidence signals:
    - Spread (tighter = more confident)
    - Price (mid-range = more confident than extremes)
    - Trade size relative to target's average
    - Time since market open
    
    Params:
        base_amount: Base USDC amount before adjustments
        max_multiplier: Maximum confidence multiplier
    """

    def __init__(self, base_amount: float = 100.0, max_multiplier: float = 2.0):
        self.base_amount = base_amount
        self.max_multiplier = max_multiplier

    def calculate(self, inp: SizingInput) -> SizingResult:
        adjustments = []
        confidence = 1.0

        # 1. Spread score: tighter spread = higher confidence
        if inp.spread > 0:
            if inp.spread < 0.02:
                spread_score = 1.2
                adjustments.append(f"Tight spread ({inp.spread:.3f}) → +20%")
            elif inp.spread < 0.05:
                spread_score = 1.0
                adjustments.append(f"Normal spread ({inp.spread:.3f}) → 0%")
            elif inp.spread < 0.10:
                spread_score = 0.7
                adjustments.append(f"Wide spread ({inp.spread:.3f}) → -30%")
            else:
                spread_score = 0.4
                adjustments.append(f"Very wide spread ({inp.spread:.3f}) → -60%")
            confidence *= spread_score

        # 2. Price score: mid-range prices are more interesting
        price = inp.target_price or inp.current_price
        if 0.20 <= price <= 0.80:
            price_score = 1.1
            adjustments.append(f"Mid-range price ({price:.2f}) → +10%")
        elif 0.10 <= price <= 0.90:
            price_score = 0.9
            adjustments.append(f"Edge price ({price:.2f}) → -10%")
        else:
            price_score = 0.5
            adjustments.append(f"Extreme price ({price:.2f}) → -50%")
        confidence *= price_score

        # 3. Size score: larger trades from target = more conviction signal
        if inp.target_usdc > 500:
            size_score = 1.3
            adjustments.append(f"Large trade (${inp.target_usdc:,.0f}) → +30%")
        elif inp.target_usdc > 100:
            size_score = 1.1
            adjustments.append(f"Medium trade (${inp.target_usdc:,.0f}) → +10%")
        else:
            size_score = 0.8
            adjustments.append(f"Small trade (${inp.target_usdc:,.0f}) → -20%")
        confidence *= size_score

        # Clamp confidence
        confidence = self._clamp(confidence, 0.1, self.max_multiplier)

        # Apply
        amount = self.base_amount * confidence * inp.copy_ratio

        return SizingResult(
            usdc_amount=round(amount, 2),
            strategy_name="confidence",
            reasoning=f"Confidence: {confidence:.2f}x → ${amount:.2f}",
            confidence=min(1.0, confidence),
            adjustments=adjustments,
        )


# ─── STRATEGY: VOLATILITY-SCALED ────────────────────────────────────

class VolatilityScaledSizer(BaseSizer):
    """
    Scales position size inversely with implied volatility.
    Higher price uncertainty (price far from 0 or 1) → smaller size.
    Binary markets near resolution (price near 0 or 1) → larger size.
    
    Params:
        base_amount: Base USDC amount
        vol_sensitivity: How much volatility affects sizing (higher = more sensitive)
    """

    def __init__(self, base_amount: float = 100.0, vol_sensitivity: float = 1.0):
        self.base_amount = base_amount
        self.vol_sensitivity = vol_sensitivity

    def calculate(self, inp: SizingInput) -> SizingResult:
        adjustments = []
        price = inp.target_price or inp.current_price

        if price <= 0 or price >= 1:
            return SizingResult(
                usdc_amount=self.base_amount * inp.copy_ratio,
                strategy_name="volatility_scaled",
                reasoning="Invalid price, using base amount",
            )

        # Implied volatility proxy for binary options:
        # max at p=0.5, min at p→0 or p→1
        # Vol ≈ sqrt(p * (1-p))
        implied_vol = math.sqrt(price * (1 - price))
        max_vol = 0.5  # At p=0.5

        # Scale inversely: low vol → bigger position
        vol_ratio = implied_vol / max_vol  # 0 to 1
        scale = 1 + (1 - vol_ratio) * self.vol_sensitivity

        adjustments.append(
            f"Price: {price:.2f} | Implied vol: {implied_vol:.3f} "
            f"({vol_ratio:.1%} of max) | Scale: {scale:.2f}x"
        )

        amount = self.base_amount * scale * inp.copy_ratio

        return SizingResult(
            usdc_amount=round(amount, 2),
            strategy_name="volatility_scaled",
            reasoning=f"Vol-scaled: {scale:.2f}x → ${amount:.2f}",
            confidence=1 - vol_ratio,
            adjustments=adjustments,
        )


# ─── SIZING ENGINE ───────────────────────────────────────────────────

class SizingEngine:
    """
    Manages sizing strategy selection and execution.
    Can combine multiple strategies via ensemble averaging.
    """

    STRATEGIES = {
        SizingStrategy.FIXED_RATIO: FixedRatioSizer,
        SizingStrategy.FIXED_AMOUNT: FixedAmountSizer,
        SizingStrategy.PROPORTIONAL: ProportionalSizer,
        SizingStrategy.KELLY: KellySizer,
        SizingStrategy.TIERED: TieredSizer,
        SizingStrategy.CONFIDENCE: ConfidenceSizer,
        SizingStrategy.VOLATILITY_SCALED: VolatilityScaledSizer,
    }

    def __init__(
        self,
        strategy: SizingStrategy = SizingStrategy.FIXED_RATIO,
        strategy_params: dict = None,
        ensemble: list[tuple[SizingStrategy, float, dict]] = None,
    ):
        """
        Args:
            strategy: Primary sizing strategy
            strategy_params: Parameters for the primary strategy
            ensemble: Optional list of (strategy, weight, params) for ensemble mode.
                     Weights are normalized automatically.
        """
        self.ensemble = ensemble
        self.strategy_params = strategy_params or {}

        if ensemble:
            self._sizers: list[tuple[BaseSizer, float]] = []
            total_weight = sum(w for _, w, _ in ensemble)
            for strat, weight, params in ensemble:
                cls = self.STRATEGIES[strat]
                sizer = cls(**params) if params else cls()
                self._sizers.append((sizer, weight / total_weight))
            self._primary = None
            logger.info(
                f"Sizing engine: ENSEMBLE with {len(ensemble)} strategies"
            )
        else:
            cls = self.STRATEGIES[strategy]
            self._primary = cls(**self.strategy_params) if self.strategy_params else cls()
            self._sizers = []
            logger.info(f"Sizing engine: {strategy.value}")

    def calculate(self, inp: SizingInput) -> SizingResult:
        """Calculate sizing using configured strategy/ensemble"""
        if self._primary:
            return self._primary.calculate(inp)

        # Ensemble: weighted average
        total_amount = 0.0
        all_adjustments = []
        all_reasoning = []
        weighted_confidence = 0.0

        for sizer, weight in self._sizers:
            result = sizer.calculate(inp)
            total_amount += result.usdc_amount * weight
            weighted_confidence += result.confidence * weight
            all_reasoning.append(f"  {result.strategy_name}: ${result.usdc_amount:.2f} (w={weight:.2f})")
            all_adjustments.extend(result.adjustments)

        return SizingResult(
            usdc_amount=round(total_amount, 2),
            strategy_name="ensemble",
            reasoning="Ensemble:\n" + "\n".join(all_reasoning),
            confidence=weighted_confidence,
            adjustments=all_adjustments,
        )

    @classmethod
    def from_config(cls, config: dict) -> "SizingEngine":
        """
        Create SizingEngine from a config dictionary.
        
        Single strategy:
            {"strategy": "kelly", "params": {"kelly_fraction": 0.25}}
        
        Ensemble:
            {"ensemble": [
                {"strategy": "kelly", "weight": 0.5, "params": {...}},
                {"strategy": "confidence", "weight": 0.3, "params": {...}},
                {"strategy": "fixed_ratio", "weight": 0.2, "params": {}}
            ]}
        """
        if "ensemble" in config:
            ensemble = []
            for entry in config["ensemble"]:
                strat = SizingStrategy(entry["strategy"])
                weight = entry.get("weight", 1.0)
                params = entry.get("params", {})
                ensemble.append((strat, weight, params))
            return cls(ensemble=ensemble)
        else:
            strategy = SizingStrategy(config.get("strategy", "fixed_ratio"))
            params = config.get("params", {})
            return cls(strategy=strategy, strategy_params=params)
