"""
Polymarket API Client - wraps Data API, Gamma API, and CLOB API
"""
import time
import logging
import asyncio
from typing import Optional
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    """Represents a single trade activity from a target wallet"""
    tx_hash: str
    timestamp: int
    side: str          # BUY or SELL
    asset: str         # token_id (condition token asset)
    condition_id: str
    size: float        # number of tokens
    price: float       # price per token in USDC
    usdc_amount: float # total USDC value
    title: str         # market question
    slug: str
    event_slug: str
    outcome: str       # "Yes" or "No"
    outcome_index: int
    wallet: str        # source wallet
    market_id: Optional[str] = None

    @classmethod
    def from_activity(cls, data: dict, wallet: str) -> "Trade":
        price = float(data.get("price", 0))
        size = float(data.get("size", 0))
        usdc_size = float(data.get("usdcSize", 0)) or round(size * price, 4)
        return cls(
            tx_hash=data.get("transactionHash", ""),
            timestamp=int(data.get("timestamp", 0)),
            side=data.get("side", ""),
            asset=data.get("asset", ""),
            condition_id=data.get("conditionId", ""),
            size=size,
            price=price,
            usdc_amount=usdc_size,
            title=data.get("title", ""),
            slug=data.get("slug", ""),
            event_slug=data.get("eventSlug", ""),
            outcome=data.get("outcome", ""),
            outcome_index=int(data.get("outcomeIndex", 0)),
            wallet=wallet,
        )


@dataclass
class Position:
    """Represents a position held by a wallet"""
    asset: str
    condition_id: str
    size: float
    avg_price: float
    initial_value: float
    current_value: float
    cash_pnl: float
    percent_pnl: float
    cur_price: float
    title: str
    slug: str
    event_slug: str
    outcome: str
    outcome_index: int
    opposite_asset: str
    redeemable: bool

    @classmethod
    def from_api(cls, data: dict) -> "Position":
        return cls(
            asset=data.get("asset", ""),
            condition_id=data.get("conditionId", ""),
            size=float(data.get("size", 0)),
            avg_price=float(data.get("avgPrice", 0)),
            initial_value=float(data.get("initialValue", 0)),
            current_value=float(data.get("currentValue", 0)),
            cash_pnl=float(data.get("cashPnl", 0)),
            percent_pnl=float(data.get("percentPnl", 0)),
            cur_price=float(data.get("curPrice", 0)),
            title=data.get("title", ""),
            slug=data.get("slug", ""),
            event_slug=data.get("eventSlug", ""),
            outcome=data.get("outcome", ""),
            outcome_index=int(data.get("outcomeIndex", 0)),
            opposite_asset=data.get("oppositeAsset", ""),
            redeemable=data.get("redeemable", False),
        )


class PolymarketClient:
    """Unified client for all Polymarket APIs"""

    def __init__(
        self,
        data_api_url: str = "https://data-api.polymarket.com",
        gamma_url: str = "https://gamma-api.polymarket.com",
        clob_url: str = "https://clob.polymarket.com",
    ):
        self.data_api_url = data_api_url
        self.gamma_url = gamma_url
        self.clob_url = clob_url
        self._http = httpx.AsyncClient(timeout=30, follow_redirects=True)
        self._rate_limit_delay = 0.25  # 250ms between requests

    @staticmethod
    def normalize_address(addr: str) -> str:
        """
        Normalize an Ethereum address to proper 0x + 40 hex chars.
        Polymarket URLs sometimes strip leading zeros (e.g., 39 hex chars).
        """
        clean = addr.strip().lstrip("@")
        if clean.startswith("0x") or clean.startswith("0X"):
            hex_part = clean[2:]
            if len(hex_part) < 40:
                # Pad with leading zeros (common in Polymarket profile URLs)
                hex_part = hex_part.zfill(40)
                logger.info(f"Padded address: 0x{hex_part} (was {len(clean[2:])} hex chars)")
            return "0x" + hex_part.lower()
        return clean

    async def close(self):
        await self._http.aclose()

    async def _get(self, url: str, params: dict = None) -> dict | list:
        """Make a rate-limited GET request"""
        await asyncio.sleep(self._rate_limit_delay)
        try:
            resp = await self._http.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error {e.response.status_code} for {url}: {e.response.text[:200]}")
            raise
        except Exception as e:
            logger.error(f"Request failed for {url}: {e}")
            raise

    # ─── DATA API ─────────────────────────────────────────────────────

    async def get_user_activity(
        self,
        user_address: str,
        activity_type: str = "TRADE",
        limit: int = 50,
        start: Optional[int] = None,
        end: Optional[int] = None,
    ) -> list[dict]:
        """
        Fetch user activity (trades, splits, merges, redeems, etc.)
        from the Polymarket Data API.
        
        Params per docs: user, type, start, end, side, sortBy, sortDirection
        """
        params = {
            "user": self.normalize_address(user_address),
            "type": activity_type,
            "sortBy": "TIMESTAMP",
            "sortDirection": "DESC",
        }
        if start:
            params["start"] = str(start)
        if end:
            params["end"] = str(end)

        url = f"{self.data_api_url}/activity"
        data = await self._get(url, params)

        # API returns a list directly
        if isinstance(data, list):
            return data[:limit]
        # Or possibly wrapped
        return (data.get("history", data.get("data", [])))[:limit]

    async def get_user_positions(
        self,
        user_address: str,
        market: Optional[str] = None,
    ) -> list[dict]:
        """Fetch current positions for a user"""
        params = {"user": self.normalize_address(user_address)}
        if market:
            params["market"] = market
        url = f"{self.data_api_url}/positions"
        data = await self._get(url, params)
        return data if isinstance(data, list) else data.get("positions", [])

    async def resolve_username(self, username_or_address: str) -> Optional[str]:
        """
        Resolve a Polymarket profile identifier to the actual proxy wallet address.
        
        Accepts:
          - A proxy wallet address (0x... 40 hex chars) -> returns as-is after validation
          - A short address from Polymarket URL (39 hex chars) -> pads and validates
          - A Polymarket profile slug / username -> resolves via Gamma API search
        """
        clean = username_or_address.strip().lstrip("@")

        # Normalize 0x addresses (pad leading zeros if needed)
        if clean.startswith("0x") or clean.startswith("0X"):
            clean = self.normalize_address(clean)

        # Step 1: Try /public-profile?address=<clean>
        if clean.startswith("0x") and len(clean) == 42:
            try:
                url = f"{self.gamma_url}/public-profile"
                data = await self._get(url, {"address": clean})
                if data and isinstance(data, dict):
                    proxy = data.get("proxyWallet")
                    if proxy:
                        logger.info(f"Resolved via public-profile -> proxyWallet {proxy}")
                        return proxy
                    # proxyWallet is null, address itself might be the proxy
                    logger.info(f"Profile found, no separate proxyWallet. Using: {clean}")
                    return clean
            except Exception as e:
                logger.warning(f"public-profile lookup failed for '{clean[:20]}': {e}")

        # Step 2: Try /public-search?q=<clean>
        try:
            url = f"{self.gamma_url}/public-search"
            data = await self._get(url, {"q": clean})
            if data and isinstance(data, dict):
                profiles = data.get("profiles", [])
                if profiles:
                    profile = profiles[0]
                    proxy = profile.get("proxyWallet") or profile.get("address")
                    if proxy:
                        name = profile.get("name", profile.get("pseudonym", "?"))
                        logger.info(f"Resolved via search '{clean}' -> {name} -> {proxy}")
                        return proxy
        except Exception as e:
            logger.warning(f"public-search failed for '{clean}': {e}")

        # Step 3: If it looks like a valid address, return it
        if clean.startswith("0x") and len(clean) == 42:
            logger.warning(f"Could not resolve via API, using normalized address: {clean}")
            return clean

        logger.error(f"Could not resolve '{username_or_address}' to a valid wallet address")
        return None

    # ─── GAMMA API ────────────────────────────────────────────────────

    async def get_market(self, condition_id: str) -> Optional[dict]:
        """Get market details by condition ID"""
        try:
            url = f"{self.gamma_url}/markets"
            data = await self._get(url, {"condition_id": condition_id})
            if isinstance(data, list) and len(data) > 0:
                return data[0]
            return data
        except Exception:
            return None

    async def get_market_by_slug(self, slug: str) -> Optional[dict]:
        """Get market details by slug"""
        try:
            url = f"{self.gamma_url}/markets"
            data = await self._get(url, {"slug": slug})
            if isinstance(data, list) and len(data) > 0:
                return data[0]
            return data
        except Exception:
            return None

    async def get_event(self, event_slug: str) -> Optional[dict]:
        """Get event details by slug"""
        try:
            url = f"{self.gamma_url}/events"
            data = await self._get(url, {"slug": event_slug})
            if isinstance(data, list) and len(data) > 0:
                return data[0]
            return data
        except Exception:
            return None

    async def search_markets(self, query: str, limit: int = 10) -> list[dict]:
        """Search for markets"""
        url = f"{self.gamma_url}/markets"
        data = await self._get(url, {"_q": query, "_limit": limit, "active": True})
        return data if isinstance(data, list) else []

    # ─── CLOB API (READ-ONLY) ────────────────────────────────────────

    async def get_orderbook(self, token_id: str) -> dict:
        """Get order book for a token"""
        url = f"{self.clob_url}/book"
        return await self._get(url, {"token_id": token_id})

    async def get_midpoint(self, token_id: str) -> Optional[float]:
        """Get midpoint price for a token"""
        try:
            url = f"{self.clob_url}/midpoint"
            data = await self._get(url, {"token_id": token_id})
            return float(data.get("mid", 0))
        except Exception:
            return None

    async def get_best_price(self, token_id: str, side: str = "BUY") -> Optional[float]:
        """Get best available price for a token"""
        try:
            url = f"{self.clob_url}/price"
            data = await self._get(url, {"token_id": token_id, "side": side})
            return float(data.get("price", 0))
        except Exception:
            return None

    async def get_last_trade_price(self, token_id: str) -> Optional[float]:
        """Get last trade price for a token"""
        try:
            url = f"{self.clob_url}/last-trade-price"
            data = await self._get(url, {"token_id": token_id})
            return float(data.get("price", 0))
        except Exception:
            return None

    async def get_spread(self, token_id: str) -> dict:
        """Get spread for a token"""
        url = f"{self.clob_url}/spread"
        return await self._get(url, {"token_id": token_id})
