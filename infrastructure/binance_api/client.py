"""
Binance Futures API Client

Wrapper around python-binance with enhanced error handling and rate limiting.
"""

from typing import Dict, Any, List, Optional
import time
from datetime import datetime
import structlog
from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceRequestException

logger = structlog.get_logger()


class BinanceFuturesClient:
    """
    Enhanced Binance Futures API client with retry logic and rate limiting.
    """

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        """
        Initialize Binance Futures client.

        Args:
            api_key: Binance API key
            api_secret: Binance API secret
            testnet: Use testnet (default: False)
        """
        if testnet:
            self.client = Client(
                api_key,
                api_secret,
                testnet=True,
                tld='com'
            )
        else:
            self.client = Client(api_key, api_secret)

        self.testnet = testnet
        self.logger = logger.bind(client="binance_futures", testnet=testnet)

        # Rate limiting
        self.rate_limit_weight = 0
        self.rate_limit_reset_time = time.time() + 60

        self.logger.info("Binance Futures client initialized")

    # ========== Account & Balance ==========

    def get_account_balance(self) -> Dict[str, Any]:
        """
        Get futures account balance.

        Returns:
            Account balance dict
        """
        try:
            account = self.client.futures_account()

            # Extract USDT balance
            usdt_balance = next(
                (asset for asset in account['assets'] if asset['asset'] == 'USDT'),
                None
            )

            if not usdt_balance:
                raise ValueError("USDT balance not found")

            return {
                "total_equity": float(account['totalWalletBalance']),
                "available_balance": float(usdt_balance['availableBalance']),
                "unrealized_pnl": float(account['totalUnrealizedProfit']),
                "margin_balance": float(account['totalMarginBalance']),
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }

        except BinanceAPIException as e:
            self.logger.error("Failed to get account balance", error=str(e))
            raise

    def get_positions(self) -> List[Dict[str, Any]]:
        """
        Get all open positions.

        Returns:
            List of open positions
        """
        try:
            positions = self.client.futures_position_information()

            # Filter only positions with non-zero amount
            open_positions = [
                {
                    "symbol": pos['symbol'],
                    "position_amount": float(pos['positionAmt']),
                    "entry_price": float(pos['entryPrice']),
                    "unrealized_pnl": float(pos['unRealizedProfit']),
                    "leverage": int(pos['leverage']),
                    "liquidation_price": float(pos['liquidationPrice'])
                }
                for pos in positions
                if float(pos['positionAmt']) != 0
            ]

            return open_positions

        except BinanceAPIException as e:
            self.logger.error("Failed to get positions", error=str(e))
            raise

    # ========== Market Data ==========

    def get_ticker_price(self, symbol: str) -> float:
        """
        Get current ticker price.

        Args:
            symbol: Trading symbol (e.g., "BTCUSDT")

        Returns:
            Current price
        """
        try:
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            return float(ticker['price'])

        except BinanceAPIException as e:
            self.logger.error("Failed to get ticker price", symbol=symbol, error=str(e))
            raise

    def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get candlestick data (OHLCV).

        Args:
            symbol: Trading symbol
            interval: Interval (1m, 5m, 15m, 1h, etc.)
            limit: Number of candles (max 1500)

        Returns:
            List of OHLCV dicts
        """
        try:
            klines = self.client.futures_klines(
                symbol=symbol,
                interval=interval,
                limit=limit
            )

            # Parse klines
            parsed_klines = []
            for k in klines:
                parsed_klines.append({
                    "timestamp": k[0],
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                    "close_time": k[6],
                    "quote_volume": float(k[7]),
                    "trades": int(k[8])
                })

            return parsed_klines

        except BinanceAPIException as e:
            self.logger.error("Failed to get klines", symbol=symbol, error=str(e))
            raise

    def get_order_book(self, symbol: str, limit: int = 10) -> Dict[str, Any]:
        """
        Get order book depth.

        Args:
            symbol: Trading symbol
            limit: Depth limit (5, 10, 20, 50, 100, 500, 1000)

        Returns:
            Order book dict with bids and asks
        """
        try:
            depth = self.client.futures_order_book(symbol=symbol, limit=limit)

            bids = [[float(price), float(qty)] for price, qty in depth['bids']]
            asks = [[float(price), float(qty)] for price, qty in depth['asks']]

            # Calculate depths
            bid_depth = sum(qty for _, qty in bids)
            ask_depth = sum(qty for _, qty in asks)

            # Calculate imbalance
            imbalance_ratio = (bid_depth - ask_depth) / (bid_depth + ask_depth) if (bid_depth + ask_depth) > 0 else 0

            return {
                "bid_depth": bid_depth,
                "ask_depth": ask_depth,
                "imbalance_ratio": imbalance_ratio
            }

        except BinanceAPIException as e:
            self.logger.error("Failed to get order book", symbol=symbol, error=str(e))
            raise

    def get_funding_rate(self, symbol: str) -> Dict[str, Any]:
        """
        Get current funding rate.

        Args:
            symbol: Trading symbol

        Returns:
            Funding rate info
        """
        try:
            funding = self.client.futures_funding_rate(symbol=symbol, limit=1)

            if not funding:
                return {"funding_rate": 0.0}

            latest = funding[0]

            return {
                "funding_rate": float(latest['fundingRate']),
                "funding_time": latest['fundingTime'],
                "symbol": latest['symbol']
            }

        except BinanceAPIException as e:
            self.logger.error("Failed to get funding rate", symbol=symbol, error=str(e))
            raise

    def get_24h_ticker(self, symbol: str) -> Dict[str, Any]:
        """
        Get 24-hour ticker statistics.

        Args:
            symbol: Trading symbol

        Returns:
            24h ticker stats
        """
        try:
            ticker = self.client.futures_ticker(symbol=symbol)

            return {
                "price_change": float(ticker['priceChange']),
                "price_change_percent": float(ticker['priceChangePercent']),
                "last_price": float(ticker['lastPrice']),
                "high_price": float(ticker['highPrice']),
                "low_price": float(ticker['lowPrice']),
                "volume": float(ticker['volume']),
                "quote_volume": float(ticker['quoteVolume'])
            }

        except BinanceAPIException as e:
            self.logger.error("Failed to get 24h ticker", symbol=symbol, error=str(e))
            raise

    # ========== Trading Orders ==========

    def create_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        reduce_only: bool = False,
        client_order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a futures order.

        Args:
            symbol: Trading symbol
            side: BUY or SELL
            order_type: MARKET, LIMIT, STOP_MARKET, etc.
            quantity: Order quantity
            price: Limit price (for LIMIT orders)
            stop_price: Stop price (for STOP orders)
            reduce_only: Reduce only flag
            client_order_id: Custom order ID

        Returns:
            Order result
        """
        try:
            params = {
                "symbol": symbol,
                "side": side,
                "type": order_type,
                "quantity": quantity
            }

            if price is not None:
                params["price"] = price
                params["timeInForce"] = "GTC"

            if stop_price is not None:
                params["stopPrice"] = stop_price

            if reduce_only:
                params["reduceOnly"] = True

            if client_order_id:
                params["newClientOrderId"] = client_order_id

            order = self.client.futures_create_order(**params)

            self.logger.info(
                "Order created",
                symbol=symbol,
                side=side,
                order_type=order_type,
                order_id=order['orderId']
            )

            return order

        except BinanceAPIException as e:
            self.logger.error(
                "Failed to create order",
                symbol=symbol,
                side=side,
                error=str(e)
            )
            raise

    def cancel_order(self, symbol: str, order_id: int) -> Dict[str, Any]:
        """
        Cancel an order.

        Args:
            symbol: Trading symbol
            order_id: Order ID to cancel

        Returns:
            Cancellation result
        """
        try:
            result = self.client.futures_cancel_order(
                symbol=symbol,
                orderId=order_id
            )

            self.logger.info("Order cancelled", symbol=symbol, order_id=order_id)
            return result

        except BinanceAPIException as e:
            self.logger.error("Failed to cancel order", symbol=symbol, error=str(e))
            raise

    def get_order(self, symbol: str, order_id: int) -> Dict[str, Any]:
        """
        Get order details.

        Args:
            symbol: Trading symbol
            order_id: Order ID

        Returns:
            Order details
        """
        try:
            order = self.client.futures_get_order(
                symbol=symbol,
                orderId=order_id
            )
            return order

        except BinanceAPIException as e:
            self.logger.error("Failed to get order", symbol=symbol, error=str(e))
            raise

    # ========== Leverage & Margin ==========

    def change_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        """
        Change leverage for symbol.

        Args:
            symbol: Trading symbol
            leverage: Leverage (1-125)

        Returns:
            Result
        """
        try:
            result = self.client.futures_change_leverage(
                symbol=symbol,
                leverage=leverage
            )

            self.logger.info("Leverage changed", symbol=symbol, leverage=leverage)
            return result

        except BinanceAPIException as e:
            self.logger.error("Failed to change leverage", symbol=symbol, error=str(e))
            raise

    # ========== Utility ==========

    def ping(self) -> bool:
        """
        Test connectivity to Binance API.

        Returns:
            True if successful
        """
        try:
            self.client.futures_ping()
            return True
        except Exception as e:
            self.logger.error("Ping failed", error=str(e))
            return False

    def get_server_time(self) -> int:
        """
        Get server timestamp.

        Returns:
            Server timestamp in milliseconds
        """
        try:
            result = self.client.futures_time()
            return result['serverTime']
        except Exception as e:
            self.logger.error("Failed to get server time", error=str(e))
            raise
