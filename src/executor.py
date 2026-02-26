"""
Trade Executor - handles order creation and submission via py-clob-client
"""
import logging
from typing import Optional
from dataclasses import dataclass, field
from datetime import datetime

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    MarketOrderArgs,
    OrderArgs,
    OrderType,
    ApiCreds,
)
from py_clob_client.order_builder.constants import BUY, SELL

from .config import BotConfig

logger = logging.getLogger(__name__)


@dataclass
class OrderResult:
    success: bool
    order_id: str = ""
    status: str = ""
    error: str = ""
    side: str = ""
    token_id: str = ""
    size: float = 0.0
    price: float = 0.0
    usdc_amount: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)
    market_title: str = ""
    is_dry_run: bool = False


class TradeExecutor:
    """Executes trades on Polymarket via the CLOB API"""

    def __init__(self, config: BotConfig):
        self.config = config
        self._client: Optional[ClobClient] = None
        self._initialized = False

    def initialize(self):
        """Initialize the CLOB client with credentials"""
        if not self.config.private_key:
            logger.warning("No private key configured - running in monitor-only mode")
            return

        try:
            sig_type = self.config.signature_type
            funder = self.config.funder_address or None
            chain = self.config.chain_id

            logger.info(
                f"Initializing CLOB client:\n"
                f"  signature_type: {sig_type}\n"
                f"  funder: {funder}\n"
                f"  chain_id: {chain}\n"
                f"  key: {self.config.private_key[:6]}...{self.config.private_key[-4:]}"
            )

            self._client = ClobClient(
                host=self.config.clob_url,
                key=self.config.private_key,
                chain_id=chain,
                signature_type=sig_type,
                funder=funder,
            )

            # Create or derive API credentials
            creds = self._client.create_or_derive_api_creds()
            self._client.set_api_creds(creds)

            logger.info("CLOB client initialized successfully")
            self._initialized = True

        except Exception as e:
            logger.error(f"Failed to initialize CLOB client: {e}")
            raise

    @property
    def is_ready(self) -> bool:
        return self._initialized and self._client is not None

    def get_address(self) -> str:
        """Get our trading address"""
        if self._client:
            return self._client.get_address()
        return ""

    def set_allowances(self) -> bool:
        """
        Approve Polymarket contracts to spend USDC.
        This needs to be called ONCE before trading.
        Approves both normal Exchange and NegRisk Exchange.
        
        Returns:
            True if successful, False otherwise
        """
        if not self.is_ready:
            logger.error("Cannot set allowances - executor not initialized")
            return False
        
        try:
            logger.info("🔓 Setting allowances for Polymarket contracts...")
            logger.info("   This may take 30-60 seconds (blockchain confirmation)")
            result = self._client.set_allowances()
            logger.info(f"✅ Allowances set successfully: {result}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to set allowances: {e}")
            return False

    def _check_neg_risk(self, token_id: str) -> bool:
        """Check if a market is neg-risk (multi-outcome) via CLOB API"""
        try:
            resp = self._client.get_neg_risk(token_id)
            if isinstance(resp, bool):
                return resp
            if isinstance(resp, dict):
                return resp.get("neg_risk", False)
            return bool(resp)
        except Exception as e:
            logger.warning(f"neg-risk check failed for {token_id[:16]}...: {e}")
            # Default to True for safety (weather markets are neg-risk)
            return True

    def execute_market_order(
        self,
        token_id: str,
        side: str,
        amount_usdc: float,
        market_title: str = "",
    ) -> OrderResult:
        """
        Execute a market order (Fill-or-Kill).
        
        Args:
            token_id: The condition token asset ID
            side: "BUY" or "SELL"
            amount_usdc: Amount in USDC to trade
            market_title: For logging purposes
        """
        if self.config.dry_run:
            logger.info(
                f"[DRY RUN] Market {side} ${amount_usdc:.2f} USDC on '{market_title}' "
                f"(token: {token_id[:16]}...)"
            )
            return OrderResult(
                success=True,
                side=side,
                token_id=token_id,
                usdc_amount=amount_usdc,
                market_title=market_title,
                is_dry_run=True,
                status="DRY_RUN",
            )

        if not self.is_ready:
            return OrderResult(
                success=False,
                error="Trade executor not initialized",
                side=side,
                token_id=token_id,
            )

        try:
            order_side = BUY if side.upper() == "BUY" else SELL

            # Check if market is neg-risk (multi-outcome like weather)
            neg_risk = self._check_neg_risk(token_id)
            if neg_risk:
                logger.info(f"  Neg-risk market detected, using NegRisk signing")

            # Create a Fill-or-Kill market order
            try:
                order_args = MarketOrderArgs(
                    token_id=token_id,
                    amount=amount_usdc,
                    side=order_side,
                    order_type=OrderType.FOK,
                    neg_risk=neg_risk,
                )
            except TypeError:
                # Older version without neg_risk in args
                order_args = MarketOrderArgs(
                    token_id=token_id,
                    amount=amount_usdc,
                    side=order_side,
                    order_type=OrderType.FOK,
                )

            signed_order = self._client.create_market_order(order_args)

            # Pass neg_risk to post_order so CLOB uses correct exchange contract
            try:
                response = self._client.post_order(
                    signed_order, OrderType.FOK, neg_risk=neg_risk
                )
            except TypeError:
                # Older py-clob-client without neg_risk param
                response = self._client.post_order(signed_order, OrderType.FOK)

            order_id = response.get("orderID", response.get("id", ""))
            status = response.get("status", "UNKNOWN")

            logger.info(
                f"Order placed: {side} ${amount_usdc:.2f} on '{market_title}' "
                f"| ID: {order_id} | Status: {status}"
            )

            return OrderResult(
                success=status in ("MATCHED", "LIVE", "DELAYED"),
                order_id=order_id,
                status=status,
                side=side,
                token_id=token_id,
                usdc_amount=amount_usdc,
                market_title=market_title,
            )

        except Exception as e:
            logger.error(f"Order execution failed: {e}")
            return OrderResult(
                success=False,
                error=str(e),
                side=side,
                token_id=token_id,
                usdc_amount=amount_usdc,
                market_title=market_title,
            )

    def execute_limit_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        market_title: str = "",
    ) -> OrderResult:
        """
        Execute a GTC limit order.
        
        Args:
            token_id: The condition token asset ID
            side: "BUY" or "SELL"
            price: Limit price (0-1)
            size: Number of tokens
            market_title: For logging purposes
        """
        if self.config.dry_run:
            usdc_val = round(size * price, 4)
            logger.info(
                f"[DRY RUN] Limit {side} {size:.2f} tokens @ ${price:.4f} "
                f"(~${usdc_val:.2f}) on '{market_title}'"
            )
            return OrderResult(
                success=True,
                side=side,
                token_id=token_id,
                size=size,
                price=price,
                usdc_amount=usdc_val,
                market_title=market_title,
                is_dry_run=True,
                status="DRY_RUN",
            )

        if not self.is_ready:
            return OrderResult(
                success=False,
                error="Trade executor not initialized",
                side=side,
                token_id=token_id,
            )

        try:
            order_side = BUY if side.upper() == "BUY" else SELL

            # Check neg-risk
            neg_risk = self._check_neg_risk(token_id)
            if neg_risk:
                logger.info(f"  Neg-risk market detected")

            try:
                order_args = OrderArgs(
                    token_id=token_id,
                    price=price,
                    size=size,
                    side=order_side,
                    neg_risk=neg_risk,
                )
            except TypeError:
                order_args = OrderArgs(
                    token_id=token_id,
                    price=price,
                    size=size,
                    side=order_side,
                )

            signed_order = self._client.create_order(order_args)
            try:
                response = self._client.post_order(
                    signed_order, OrderType.GTC, neg_risk=neg_risk
                )
            except TypeError:
                response = self._client.post_order(signed_order, OrderType.GTC)

            order_id = response.get("orderID", response.get("id", ""))
            status = response.get("status", "UNKNOWN")

            logger.info(
                f"Limit order placed: {side} {size:.2f} @ ${price:.4f} on '{market_title}' "
                f"| ID: {order_id} | Status: {status}"
            )

            return OrderResult(
                success=status in ("LIVE", "MATCHED", "DELAYED"),
                order_id=order_id,
                status=status,
                side=side,
                token_id=token_id,
                size=size,
                price=price,
                usdc_amount=round(size * price, 4),
                market_title=market_title,
            )

        except Exception as e:
            logger.error(f"Limit order failed: {e}")
            return OrderResult(
                success=False,
                error=str(e),
                side=side,
                token_id=token_id,
                size=size,
                price=price,
                market_title=market_title,
            )

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order"""
        if self.config.dry_run:
            logger.info(f"[DRY RUN] Cancel order {order_id}")
            return True

        if not self.is_ready:
            return False

        try:
            resp = self._client.cancel(order_id)
            logger.info(f"Cancelled order {order_id}: {resp}")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    def cancel_all_orders(self) -> bool:
        """Cancel all open orders"""
        if self.config.dry_run:
            logger.info("[DRY RUN] Cancel all orders")
            return True

        if not self.is_ready:
            return False

        try:
            resp = self._client.cancel_all()
            logger.info(f"Cancelled all orders: {resp}")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel all orders: {e}")
            return False
