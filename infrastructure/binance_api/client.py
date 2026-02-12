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

    def get_order_book(self, symbol: str, limit: int = 100) -> Dict[str, Any]:
        """
        Get order book depth.

        Args:
            symbol: Trading symbol
            limit: Depth limit (5, 10, 20, 50, 100, 500, 1000). Default 100 for meaningful imbalance.

        Returns:
            Order book dict with bids, asks, imbalance, and spread
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

            # Calculate bid-ask spread
            best_bid = bids[0][0] if bids else 0
            best_ask = asks[0][0] if asks else 0
            mid_price = (best_bid + best_ask) / 2 if (best_bid + best_ask) > 0 else 1
            spread_bps = ((best_ask - best_bid) / mid_price) * 10000 if mid_price > 0 else 0

            return {
                "bid_depth": bid_depth,
                "ask_depth": ask_depth,
                "imbalance_ratio": imbalance_ratio,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread_bps": round(spread_bps, 2),
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
        quantity: float = 0,
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        reduce_only: bool = False,
        close_position: bool = False,
        client_order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a futures order.

        Args:
            symbol: Trading symbol
            side: BUY or SELL
            order_type: MARKET, LIMIT, STOP_MARKET, etc.
            quantity: Order quantity (ignored when close_position=True)
            price: Limit price (for LIMIT orders)
            stop_price: Stop price (for STOP orders)
            reduce_only: Reduce only flag
            close_position: If True, use closePosition=true (closes entire position, no quantity needed)
            client_order_id: Custom order ID

        Returns:
            Order result
        """
        try:
            params = {
                "symbol": symbol,
                "side": side,
                "type": order_type,
            }

            if close_position:
                params["closePosition"] = "true"
            else:
                params["quantity"] = quantity

            if price is not None:
                params["price"] = price
                params["timeInForce"] = "GTC"

            if stop_price is not None:
                params["stopPrice"] = stop_price

            if reduce_only and not close_position:
                params["reduceOnly"] = True

            if client_order_id:
                params["newClientOrderId"] = client_order_id

            order = self.client.futures_create_order(**params)

            self.logger.info(
                "Order created",
                symbol=symbol,
                side=side,
                order_type=order_type,
                close_position=close_position,
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

    def create_algo_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        trigger_price: float,
        quantity: float = 0,
        close_position: bool = False,
        client_algo_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create an algo/conditional order via /fapi/v1/algoOrder.

        Required since Dec 2025 for STOP_MARKET, TAKE_PROFIT_MARKET,
        STOP, TAKE_PROFIT, and TRAILING_STOP_MARKET order types.

        Args:
            symbol: Trading symbol
            side: BUY or SELL
            order_type: STOP_MARKET, TAKE_PROFIT_MARKET, etc.
            trigger_price: Price that triggers the order (replaces stopPrice)
            quantity: Order quantity (ignored when close_position=True)
            close_position: If True, closes entire position
            client_algo_id: Custom algo order ID

        Returns:
            Algo order result with algoId
        """
        try:
            params = {
                "algoType": "CONDITIONAL",
                "symbol": symbol,
                "side": side,
                "type": order_type,
                "triggerPrice": str(trigger_price),
            }

            if close_position:
                params["closePosition"] = "true"
            elif quantity > 0:
                params["quantity"] = str(quantity)

            if client_algo_id:
                params["clientAlgoId"] = client_algo_id

            result = self.client._request_futures_api(
                "post", "algoOrder", signed=True, data=params
            )

            self.logger.info(
                "Algo order created",
                symbol=symbol,
                side=side,
                order_type=order_type,
                trigger_price=trigger_price,
                close_position=close_position,
                algo_id=result.get("algoId")
            )

            return result

        except BinanceAPIException as e:
            self.logger.error(
                "Failed to create algo order",
                symbol=symbol,
                side=side,
                order_type=order_type,
                error=str(e)
            )
            raise

    def get_algo_order_status(self, symbol: str, algo_id: int) -> Dict[str, Any]:
        """
        Get algo order status.

        Args:
            symbol: Trading symbol
            algo_id: Algo order ID

        Returns:
            Dict with algoStatus, triggerPrice, etc.
        """
        try:
            result = self.client._request_futures_api(
                "get", "algoOrder", signed=True,
                data={"symbol": symbol, "algoId": algo_id}
            )
            return result

        except BinanceAPIException as e:
            self.logger.error("Failed to get algo order status", symbol=symbol, algo_id=algo_id, error=str(e))
            raise

    def cancel_algo_order(self, symbol: str, algo_id: int) -> Dict[str, Any]:
        """
        Cancel an algo order.

        Args:
            symbol: Trading symbol
            algo_id: Algo order ID

        Returns:
            Cancellation result
        """
        try:
            result = self.client._request_futures_api(
                "delete", "algoOrder", signed=True,
                data={"symbol": symbol, "algoId": algo_id}
            )
            self.logger.info("Algo order cancelled", symbol=symbol, algo_id=algo_id)
            return result

        except BinanceAPIException as e:
            self.logger.error("Failed to cancel algo order", symbol=symbol, algo_id=algo_id, error=str(e))
            raise

    def cancel_all_algo_orders(self, symbol: str) -> Dict[str, Any]:
        """
        Cancel all open algo orders for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Cancellation result
        """
        try:
            result = self.client._request_futures_api(
                "delete", "algoOpenOrders", signed=True,
                data={"symbol": symbol}
            )
            self.logger.info("All algo orders cancelled", symbol=symbol)
            return result

        except BinanceAPIException as e:
            self.logger.error("Failed to cancel all algo orders", symbol=symbol, error=str(e))
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

    def get_order_status(self, symbol: str, order_id: int) -> Dict[str, Any]:
        """
        Get order status with key fields extracted.

        Args:
            symbol: Trading symbol
            order_id: Order ID

        Returns:
            Dict with status, avgPrice, executedQty, type
        """
        order = self.get_order(symbol, order_id)
        return {
            "status": order.get("status"),
            "avg_price": float(order.get("avgPrice", 0)),
            "executed_qty": float(order.get("executedQty", 0)),
            "type": order.get("type"),
            "side": order.get("side"),
            "order_id": order.get("orderId"),
        }

    def cancel_all_open_orders(self, symbol: str) -> Dict[str, Any]:
        """
        Cancel all open orders for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Cancellation result
        """
        try:
            result = self.client.futures_cancel_all_open_orders(symbol=symbol)
            self.logger.info("All open orders cancelled", symbol=symbol)
            return result

        except BinanceAPIException as e:
            self.logger.error("Failed to cancel all orders", symbol=symbol, error=str(e))
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

    # ========== Exchange Info ==========

    def get_symbol_info(self, symbols: List[str] = None) -> Dict[str, Dict[str, Any]]:
        """
        Get exchange info for symbols (precision, step size, min notional).

        Args:
            symbols: List of symbols to query (None = all)

        Returns:
            Dict mapping symbol -> {min_qty, step_size, min_notional, price_precision}
        """
        try:
            exchange_info = self.client.futures_exchange_info()
            result = {}

            for sym_info in exchange_info.get('symbols', []):
                symbol = sym_info['symbol']
                if symbols and symbol not in symbols:
                    continue

                filters = {f['filterType']: f for f in sym_info.get('filters', [])}

                lot_size = filters.get('LOT_SIZE', {})
                min_notional = filters.get('MIN_NOTIONAL', {})
                price_filter = filters.get('PRICE_FILTER', {})

                step_size = float(lot_size.get('stepSize', 0.001))
                min_qty = float(lot_size.get('minQty', 0.001))

                result[symbol] = {
                    "min_qty": min_qty,
                    "step_size": step_size,
                    "min_notional": float(min_notional.get('notional', 5.0)),
                    "price_precision": int(sym_info.get('pricePrecision', 2)),
                    "quantity_precision": int(sym_info.get('quantityPrecision', 3)),
                }

            self.logger.info("Exchange info loaded", symbols_count=len(result))
            return result

        except BinanceAPIException as e:
            self.logger.error("Failed to get exchange info", error=str(e))
            raise

    # ========== Account Trades ==========

    def get_recent_trades(self, symbol: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get recent account trades (fills) for a symbol.

        Uses futures_account_trades to get actual fill prices, realized PnL,
        and commissions — much more accurate than algo order triggerPrice.

        Args:
            symbol: Trading symbol
            limit: Number of recent trades to return

        Returns:
            List of trade dicts with price, qty, realizedPnl, commission, etc.
        """
        try:
            trades = self.client.futures_account_trades(symbol=symbol, limit=limit)

            parsed = []
            for t in trades:
                parsed.append({
                    "id": t.get("id"),
                    "order_id": t.get("orderId"),
                    "symbol": t.get("symbol"),
                    "side": t.get("side"),
                    "price": float(t.get("price", 0)),
                    "qty": float(t.get("qty", 0)),
                    "realized_pnl": float(t.get("realizedPnl", 0)),
                    "commission": float(t.get("commission", 0)),
                    "commission_asset": t.get("commissionAsset", "USDT"),
                    "time": t.get("time"),
                    "buyer": t.get("buyer", False),
                    "maker": t.get("maker", False),
                })

            return parsed

        except BinanceAPIException as e:
            self.logger.error("Failed to get recent trades", symbol=symbol, error=str(e))
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
