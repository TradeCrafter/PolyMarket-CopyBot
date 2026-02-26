"""
Wallet Watcher - monitors target wallets for new trades
"""
import time
import logging
import asyncio
from typing import Optional, Callable, Awaitable
from dataclasses import dataclass

from .config import WalletConfig
from .api_client import PolymarketClient, Trade

logger = logging.getLogger(__name__)


@dataclass
class WatcherState:
    """Tracks the state for each watched wallet"""
    wallet: WalletConfig
    last_seen_timestamp: int = 0
    last_seen_tx_hashes: set = None
    trades_detected: int = 0
    errors: int = 0
    last_error: str = ""
    resolved_address: str = ""

    def __post_init__(self):
        if self.last_seen_tx_hashes is None:
            self.last_seen_tx_hashes = set()


class WalletWatcher:
    """Monitors target wallets and emits new trade events"""

    def __init__(
        self,
        client: PolymarketClient,
        wallets: list[WalletConfig],
        poll_interval: int = 5,
        on_new_trade: Optional[Callable[[Trade, WalletConfig], Awaitable[None]]] = None,
    ):
        self.client = client
        self.poll_interval = poll_interval
        self.on_new_trade = on_new_trade
        self._states: dict[str, WatcherState] = {}
        self._running = False

        for w in wallets:
            self._states[w.address] = WatcherState(wallet=w)

    async def _resolve_addresses(self):
        """Resolve all addresses to actual proxy wallet addresses via Gamma API"""
        for addr, state in list(self._states.items()):
            resolved = await self.client.resolve_username(addr)
            if resolved and resolved != addr:
                state.resolved_address = resolved
                logger.info(f"Resolved '{addr[:16]}...' -> {resolved}")
            elif resolved:
                state.resolved_address = resolved
                logger.info(f"Using address as-is: {resolved[:16]}...")
            else:
                state.resolved_address = addr
                logger.warning(f"Could not resolve '{addr[:16]}' - using as-is")

    async def _initialize_state(self, state: WatcherState):
        """
        Fetch recent trades to establish baseline (avoid replaying old trades on startup).
        """
        addr = state.resolved_address or state.wallet.address
        label = state.wallet.label or addr[:10]

        try:
            recent = await self.client.get_user_activity(
                user_address=addr,
                activity_type="TRADE",
                limit=20,
            )

            if recent:
                for trade_data in recent:
                    tx = trade_data.get("transactionHash", "")
                    ts = int(trade_data.get("timestamp", 0))
                    if tx:
                        state.last_seen_tx_hashes.add(tx)
                    state.last_seen_timestamp = max(state.last_seen_timestamp, ts)

                logger.info(
                    f"📋 [{label}] Initialized with {len(recent)} recent trades. "
                    f"Latest timestamp: {state.last_seen_timestamp}"
                )
            else:
                state.last_seen_timestamp = int(time.time())
                logger.info(f"📋 [{label}] No recent trades found. Starting from now.")

        except Exception as e:
            logger.error(f"Failed to initialize state for [{label}]: {e}")
            state.last_seen_timestamp = int(time.time())

    async def _poll_wallet(self, state: WatcherState) -> list[Trade]:
        """Poll a single wallet for new trades"""
        addr = state.resolved_address or state.wallet.address
        label = state.wallet.label or addr[:10]
        new_trades = []

        try:
            # Fetch activity since last seen timestamp
            activity = await self.client.get_user_activity(
                user_address=addr,
                activity_type="TRADE",
                limit=20,
                start=state.last_seen_timestamp,
            )

            if not activity:
                return []

            for trade_data in activity:
                tx_hash = trade_data.get("transactionHash", "")
                timestamp = int(trade_data.get("timestamp", 0))

                # Skip already-seen trades
                if tx_hash in state.last_seen_tx_hashes:
                    continue

                # Skip old trades (shouldn't happen but safety check)
                if timestamp <= state.last_seen_timestamp - 60:
                    continue

                trade = Trade.from_activity(trade_data, addr)

                if trade.tx_hash:
                    state.last_seen_tx_hashes.add(trade.tx_hash)

                    # Keep tx hash set bounded
                    if len(state.last_seen_tx_hashes) > 500:
                        state.last_seen_tx_hashes = set(
                            list(state.last_seen_tx_hashes)[-250:]
                        )

                state.last_seen_timestamp = max(state.last_seen_timestamp, timestamp)
                state.trades_detected += 1
                new_trades.append(trade)

                logger.info(
                    f"🔔 [{label}] New trade: {trade.side} {trade.size:.2f} tokens "
                    f"@ ${trade.price:.4f} on '{trade.title}' ({trade.outcome})"
                )

        except Exception as e:
            state.errors += 1
            state.last_error = str(e)
            logger.error(f"Error polling [{label}]: {e}")

        return new_trades

    async def start(self):
        """Start watching all wallets"""
        logger.info(f"🔍 Starting wallet watcher for {len(self._states)} wallet(s)")

        await self._resolve_addresses()

        # Initialize state for all wallets
        for state in self._states.values():
            await self._initialize_state(state)

        self._running = True

        while self._running:
            for state in self._states.values():
                if not self._running:
                    break

                new_trades = await self._poll_wallet(state)

                for trade in new_trades:
                    if self.on_new_trade:
                        try:
                            await self.on_new_trade(trade, state.wallet)
                        except Exception as e:
                            logger.error(f"Error in trade handler: {e}")

            await asyncio.sleep(self.poll_interval)

    def stop(self):
        """Stop watching"""
        self._running = False
        logger.info("Wallet watcher stopped")

    def get_status(self) -> list[dict]:
        """Get status of all watched wallets"""
        result = []
        for addr, state in self._states.items():
            label = state.wallet.label or addr[:10]
            result.append({
                "address": addr,
                "resolved": state.resolved_address,
                "label": label,
                "copy_ratio": state.wallet.copy_ratio,
                "trades_detected": state.trades_detected,
                "last_timestamp": state.last_seen_timestamp,
                "errors": state.errors,
                "last_error": state.last_error,
            })
        return result
