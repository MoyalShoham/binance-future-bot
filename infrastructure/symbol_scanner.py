"""
Hot Symbol Scanner

Multi-source symbol discovery:
1. Binance Futures 24h tickers (momentum/volume)
2. CoinGecko trending coins
3. CryptoPanic news-based trending

Merges all sources, filters for Binance Futures availability, and ranks by score.
"""

import os
from typing import List, Dict, Any, Set
import requests
import structlog

logger = structlog.get_logger()

# Symbols to never trade (stablecoins, low-liquidity, delisted-risk)
BLACKLIST = {
    "USDCUSDT", "BUSDUSDT", "TUSDUSDT", "FDUSDUSDT", "USDPUSDT",
    "EURUSDT", "GBPUSDT",
}

# Only trade USDT-margined perpetuals
REQUIRED_SUFFIX = "USDT"

# Common coin symbol -> Binance Futures symbol mapping overrides
# (for coins whose CoinGecko symbol doesn't match Binance directly)
COINGECKO_SYMBOL_MAP = {
    "shib": "1000SHIBUSDT",
    "floki": "1000FLOKIUSDT",
    "pepe": "1000PEPEUSDT",
    "lunc": "LUNCUSDT",
    "bonk": "1000BONKUSDT",
    "rats": "1000RATSUSDT",
    "sats": "1000SATSUSDT",
    "cat": "1000CATUSDT",
}


class SymbolScanner:
    """
    Multi-source hot symbol scanner for Binance Futures.

    Sources:
    1. Binance 24h tickers: high-volume movers (momentum)
    2. CoinGecko trending: coins trending on CoinGecko (social/search interest)
    3. CryptoPanic: coins mentioned in trending crypto news

    All discovered symbols are filtered against Binance Futures tradeable list
    and merged with configured symbols.
    """

    def __init__(self, binance_client, config: Dict[str, Any]):
        self.binance_client = binance_client
        self.config = config

        scanner_config = config.get("symbol_scanner", {})
        self.enabled = scanner_config.get("enabled", True)
        self.min_volume_usdt = scanner_config.get("min_volume_usdt", 100_000_000)
        self.top_n = scanner_config.get("top_n", 10)
        self.min_price_change_pct = scanner_config.get("min_price_change_pct", 1.5)
        self.max_price_change_pct = scanner_config.get("max_price_change_pct", 30.0)
        self.always_include = set(scanner_config.get("always_include", []))

        # External sources config
        self.use_coingecko = scanner_config.get("use_coingecko", True)
        self.use_cryptopanic = scanner_config.get("use_cryptopanic", True)
        self.cryptopanic_api_key = os.environ.get(
            "CRYPTOPANIC_API_KEY",
            scanner_config.get("cryptopanic_api_key", "")
        )

        # Cache of tradeable symbols (refreshed each scan)
        self._tradeable_symbols: Set[str] = set()

        logger.info(
            "SymbolScanner initialized",
            enabled=self.enabled,
            min_volume=self.min_volume_usdt,
            top_n=self.top_n,
            use_coingecko=self.use_coingecko,
            use_cryptopanic=self.use_cryptopanic and bool(self.cryptopanic_api_key),
        )

    def _load_tradeable_symbols(self):
        """Fetch exchange info and return set of symbols with status=TRADING."""
        try:
            info = self.binance_client.client.futures_exchange_info()
            self._tradeable_symbols = {
                s["symbol"]
                for s in info.get("symbols", [])
                if s.get("status") == "TRADING"
                and s.get("contractType") == "PERPETUAL"
            }
            logger.debug("Tradeable symbols loaded", count=len(self._tradeable_symbols))
        except Exception as e:
            logger.warning("Failed to load exchange info for scanner", error=str(e))

    # ========== Source 1: Binance 24h Tickers ==========

    def _scan_binance_movers(self) -> Dict[str, float]:
        """
        Scan Binance Futures 24h tickers for high-momentum symbols.

        Returns:
            Dict of {symbol: score} where score is based on price change and volume.
        """
        scores = {}
        try:
            tickers = self.binance_client.client.futures_ticker()

            for t in tickers:
                symbol = t["symbol"]

                if not symbol.endswith(REQUIRED_SUFFIX):
                    continue
                if symbol in BLACKLIST:
                    continue
                if self._tradeable_symbols and symbol not in self._tradeable_symbols:
                    continue

                quote_volume = float(t.get("quoteVolume", 0))
                price_change_pct = abs(float(t.get("priceChangePercent", 0)))
                last_price = float(t.get("lastPrice", 0))

                if quote_volume < self.min_volume_usdt:
                    continue
                if price_change_pct < self.min_price_change_pct:
                    continue
                if price_change_pct > self.max_price_change_pct:
                    continue
                if last_price < 0.0001:
                    continue

                # Score: weighted combination of momentum and volume
                scores[symbol] = price_change_pct

        except Exception as e:
            logger.warning("Binance mover scan failed", error=str(e))

        return scores

    # ========== Source 2: CoinGecko Trending ==========

    def _scan_coingecko_trending(self) -> Dict[str, float]:
        """
        Fetch trending coins from CoinGecko and map to Binance Futures symbols.

        Returns:
            Dict of {symbol: score} for coins that exist on Binance Futures.
        """
        scores = {}
        if not self.use_coingecko:
            return scores

        try:
            resp = requests.get(
                "https://api.coingecko.com/api/v3/search/trending",
                timeout=10,
                headers={"accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()

            coins = data.get("coins", [])
            for i, item in enumerate(coins):
                coin = item.get("item", {})
                coin_symbol = coin.get("symbol", "").lower()
                coin_name = coin.get("name", "")
                market_cap_rank = coin.get("market_cap_rank", 9999) or 9999

                # Map to Binance Futures symbol
                binance_symbol = self._coingecko_to_binance(coin_symbol)

                if not binance_symbol:
                    continue
                if binance_symbol in BLACKLIST:
                    continue
                if self._tradeable_symbols and binance_symbol not in self._tradeable_symbols:
                    continue

                # Score: higher for top-ranked trending coins, bonus for low market cap rank
                trend_score = max(0, 10 - i)  # Position in trending list (10 to 1)
                cap_bonus = max(0, 3 - (market_cap_rank / 100))  # Bonus for top 300 coins
                scores[binance_symbol] = trend_score + cap_bonus

            if scores:
                logger.info(
                    "CoinGecko trending symbols",
                    found=list(scores.keys()),
                )

        except Exception as e:
            logger.warning("CoinGecko trending fetch failed", error=str(e))

        return scores

    def _coingecko_to_binance(self, coin_symbol: str) -> str:
        """Map a CoinGecko coin symbol to Binance Futures symbol."""
        lower = coin_symbol.lower()

        # Check manual overrides first
        if lower in COINGECKO_SYMBOL_MAP:
            return COINGECKO_SYMBOL_MAP[lower]

        # Default: uppercase + USDT
        candidate = coin_symbol.upper() + "USDT"
        return candidate

    # ========== Source 3: CryptoPanic News ==========

    def _scan_cryptopanic_news(self) -> Dict[str, float]:
        """
        Fetch trending crypto news from CryptoPanic and extract mentioned symbols.

        Returns:
            Dict of {symbol: score} based on news mention frequency and sentiment.
        """
        scores = {}
        if not self.use_cryptopanic or not self.cryptopanic_api_key:
            return scores

        try:
            resp = requests.get(
                "https://cryptopanic.com/api/free/v1/posts/",
                params={
                    "auth_token": self.cryptopanic_api_key,
                    "kind": "news",
                    "filter": "hot",
                    "public": "true",
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            posts = data.get("results", [])
            symbol_mentions: Dict[str, int] = {}

            for post in posts:
                # Extract currencies mentioned in the post
                currencies = post.get("currencies", [])
                for currency in currencies:
                    code = currency.get("code", "").upper()
                    if not code:
                        continue

                    binance_symbol = self._news_symbol_to_binance(code)
                    if not binance_symbol:
                        continue
                    if binance_symbol in BLACKLIST:
                        continue
                    if self._tradeable_symbols and binance_symbol not in self._tradeable_symbols:
                        continue

                    symbol_mentions[binance_symbol] = symbol_mentions.get(binance_symbol, 0) + 1

            # Convert mention count to score (logarithmic scaling)
            for symbol, count in symbol_mentions.items():
                scores[symbol] = min(count * 2, 10)  # Cap at 10

            if scores:
                logger.info(
                    "CryptoPanic trending symbols",
                    found=list(scores.keys()),
                )

        except Exception as e:
            logger.warning("CryptoPanic news fetch failed", error=str(e))

        return scores

    def _news_symbol_to_binance(self, code: str) -> str:
        """Map a news currency code to Binance Futures symbol."""
        lower = code.lower()
        if lower in COINGECKO_SYMBOL_MAP:
            return COINGECKO_SYMBOL_MAP[lower]
        return code.upper() + "USDT"

    # ========== Main Scan ==========

    def scan(self) -> List[str]:
        """
        Scan all sources for hot symbols, merge and rank.

        Returns:
            List of symbol strings sorted by combined score (highest first).
        """
        if not self.enabled:
            return list(self.always_include) if self.always_include else []

        try:
            # Refresh tradeable symbols list
            self._load_tradeable_symbols()

            # Gather scores from all sources
            binance_scores = self._scan_binance_movers()
            coingecko_scores = self._scan_coingecko_trending()
            cryptopanic_scores = self._scan_cryptopanic_news()

            # Merge scores: symbols from multiple sources get boosted
            merged: Dict[str, Dict[str, Any]] = {}

            for symbol, score in binance_scores.items():
                merged.setdefault(symbol, {"score": 0, "sources": []})
                merged[symbol]["score"] += score
                merged[symbol]["sources"].append("binance")

            for symbol, score in coingecko_scores.items():
                merged.setdefault(symbol, {"score": 0, "sources": []})
                merged[symbol]["score"] += score
                merged[symbol]["sources"].append("coingecko")

            for symbol, score in cryptopanic_scores.items():
                merged.setdefault(symbol, {"score": 0, "sources": []})
                merged[symbol]["score"] += score
                merged[symbol]["sources"].append("cryptopanic")

            # Multi-source bonus: symbols found in 2+ sources get 50% boost
            for symbol, data in merged.items():
                if len(data["sources"]) >= 2:
                    data["score"] *= 1.5
                if len(data["sources"]) >= 3:
                    data["score"] *= 1.3  # Extra boost for all 3 sources

            # Sort by score descending
            ranked = sorted(merged.items(), key=lambda x: x[1]["score"], reverse=True)

            # Take top N
            hot_symbols = [symbol for symbol, _ in ranked[:self.top_n]]

            # Always include pinned symbols
            for sym in self.always_include:
                if sym not in hot_symbols:
                    if not self._tradeable_symbols or sym in self._tradeable_symbols:
                        hot_symbols.append(sym)

            logger.info(
                "Symbol scan complete",
                total_candidates=len(merged),
                selected=len(hot_symbols),
                symbols=hot_symbols,
                top_ranked=[
                    f"{sym} ({data['score']:.1f} from {','.join(data['sources'])})"
                    for sym, data in ranked[:5]
                ],
                sources_summary={
                    "binance": len(binance_scores),
                    "coingecko": len(coingecko_scores),
                    "cryptopanic": len(cryptopanic_scores),
                },
            )

            return hot_symbols

        except Exception as e:
            logger.error("Symbol scan failed, using fallback", error=str(e))
            fallback = self.config.get("trading", {}).get("symbols", [])
            return list(self.always_include) + [s for s in fallback if s not in self.always_include]
