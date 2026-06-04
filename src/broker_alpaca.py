"""
src/broker_alpaca.py — Alpaca Paper Trading Client Wrapper

PAPER TRADING ONLY. This module never creates a live trading connection.

paper=True is hardcoded in the TradingClient constructor on this line:
    self._client = TradingClient(api_key=key, secret_key=secret, paper=True)
There is no parameter, flag, or config option that can override this.

What this module does:
  - Wraps alpaca-py's TradingClient with a clean, typed interface
  - Enforces paper mode at instantiation via config.require_paper_mode()
  - Returns plain Python dicts for every operation (no raw alpaca objects leak out)
  - Provides clear error messages when the API rejects an order

What this module never does:
  - Never uses a live endpoint
  - Never submits a short sell (selling shares you don't own)
  - Never submits an options order
  - Never uses margin

Operations:
  get_account()          — portfolio value, buying power, status
  get_positions()        — open positions as {ticker: info_dict}
  get_open_orders()      — pending/unfilled orders
  submit_buy_order()     — notional market buy (dollar amount, not shares)
  submit_sell_order()    — market sell by share quantity
  cancel_all_orders()    — cancel every pending order
  close_position()       — close one position by ticker
  close_all_positions()  — close every open position
"""

from typing import Dict, List

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest

from src.utils import setup_logger

logger = setup_logger(__name__)


class AlpacaPaperBroker:
    """
    Thin wrapper around alpaca-py's TradingClient.

    Always uses paper=True — this is hardcoded and cannot be changed.

    Usage:
        broker = AlpacaPaperBroker.from_config(cfg)   # recommended
        account = broker.get_account()
        print(account["buying_power"])
    """

    def __init__(self, api_key: str, secret_key: str) -> None:
        """
        Create the paper trading client.

        paper=True is hardcoded. It cannot be overridden by any argument.

        Args:
            api_key:    Alpaca paper trading API key
            secret_key: Alpaca paper trading secret key

        Raises:
            ValueError: If either key is missing or empty
        """
        if not api_key:
            raise ValueError(
                "ALPACA_API_KEY is missing.\n"
                "  Fix: Open .env and set ALPACA_API_KEY=your_paper_key"
            )
        if not secret_key:
            raise ValueError(
                "ALPACA_SECRET_KEY is missing.\n"
                "  Fix: Open .env and set ALPACA_SECRET_KEY=your_paper_secret"
            )

        # paper=True is HARDCODED — never remove or parameterise this argument
        self._client = TradingClient(
            api_key=api_key,
            secret_key=secret_key,
            paper=True,  # PAPER TRADING ONLY — NEVER live
        )
        logger.info("AlpacaPaperBroker initialized (paper=True)")

    @classmethod
    def from_config(cls, config) -> "AlpacaPaperBroker":
        """
        Create a broker from an AppConfig object.

        Calls config.require_paper_mode() before connecting —
        if ALPACA_PAPER is not true or keys are missing, raises
        RuntimeError before any API call is attempted.

        Args:
            config: AppConfig object from src.config

        Returns:
            AlpacaPaperBroker instance ready to use

        Raises:
            RuntimeError: If paper mode is not correctly configured
        """
        config.require_paper_mode()  # Hard gate — raises if not properly configured
        return cls(config.alpaca_api_key, config.alpaca_secret_key)

    # ================================================================
    # Account
    # ================================================================

    def get_account(self) -> dict:
        """
        Fetch current account state from Alpaca.

        Returns:
            {
                "portfolio_value": float,  total paper portfolio value in $
                "buying_power":    float,  available cash for new positions
                "cash":            float,  uninvested cash
                "status":          str,    e.g. "AccountStatus.ACTIVE"
            }
        """
        account = self._client.get_account()
        return {
            "portfolio_value": float(account.portfolio_value or 0),
            "buying_power": float(account.buying_power or 0),
            "cash": float(account.cash or 0),
            "status": str(account.status),
        }

    # ================================================================
    # Positions
    # ================================================================

    def get_positions(self) -> Dict[str, dict]:
        """
        Fetch all open positions from Alpaca.

        Returns:
            Dict mapping uppercase ticker → position info dict:
            {
                "NVDA": {
                    "ticker":          "NVDA",
                    "qty":             150,        whole shares
                    "avg_entry_price": 120.50,
                    "market_value":    18075.00,
                    "unrealized_pl":   75.00,      profit/loss in $
                    "unrealized_plpc": 0.00417,    profit/loss as fraction
                    "current_price":   120.75,
                },
                ...
            }
            Returns {} when no positions are open.
        """
        raw = self._client.get_all_positions()
        result: Dict[str, dict] = {}
        for pos in raw:
            ticker = str(pos.symbol).upper()
            result[ticker] = {
                "ticker": ticker,
                "qty": int(float(pos.qty or 0)),
                "avg_entry_price": float(pos.avg_entry_price or 0),
                "market_value": float(pos.market_value or 0),
                "unrealized_pl": float(pos.unrealized_pl or 0),
                "unrealized_plpc": float(pos.unrealized_plpc or 0),
                "current_price": float(pos.current_price or 0),
            }
        return result

    # ================================================================
    # Open orders
    # ================================================================

    def get_open_orders(self) -> List[dict]:
        """
        Fetch all pending/unfilled orders from Alpaca.

        Returns:
            List of order dicts:
            [
                {
                    "id":         "uuid-string",
                    "ticker":     "NVDA",
                    "side":       "buy",       or "sell"
                    "qty":        None or 150,
                    "notional":   18000.0 or None,
                    "status":     "new",
                    "created_at": "2026-06-02T09:32:00Z",
                },
                ...
            ]
            Returns [] if no open orders or on network error.
        """
        try:
            raw = self._client.get_orders(
                GetOrdersRequest(status=QueryOrderStatus.OPEN)
            )
        except Exception as e:
            logger.warning(f"Could not fetch open orders: {e}")
            return []

        result = []
        for o in raw:
            result.append({
                "id": str(o.id),
                "ticker": str(o.symbol).upper(),
                "side": str(o.side).lower(),
                "qty": int(float(o.qty)) if o.qty else None,
                "notional": float(o.notional) if o.notional else None,
                "status": str(o.status),
                "created_at": str(o.created_at),
            })
        return result

    # ================================================================
    # Order submission
    # ================================================================

    def submit_buy_order(self, ticker: str, notional: float) -> dict:
        """
        Submit a notional market BUY order for paper trading.

        Uses dollar amount (notional) instead of share count. This avoids
        the risk of ordering the wrong number of shares due to price
        movement between signal time and execution time.

        Args:
            ticker:   Uppercase stock symbol
            notional: Dollar amount to spend (e.g. 18000.00)

        Returns:
            {
                "id":           "alpaca-order-uuid",
                "ticker":       "NVDA",
                "side":         "buy",
                "notional":     18000.00,
                "qty":          None,
                "status":       "accepted",
                "submitted_at": "2026-06-02T09:32:00Z",
            }

        Raises:
            ValueError:  If notional <= 0 or ticker is empty
            RuntimeError: If Alpaca rejects the order
        """
        ticker = str(ticker or "").upper()
        if not ticker:
            raise ValueError("ticker must not be empty")
        if notional <= 0:
            raise ValueError(f"notional must be > 0, got {notional}")

        logger.info(f"Submitting paper BUY: {ticker}  ${notional:,.2f}")

        order_data = MarketOrderRequest(
            symbol=ticker,
            notional=round(notional, 2),
            side=OrderSide.BUY,         # Always BUY — hardcoded
            time_in_force=TimeInForce.DAY,
        )

        try:
            order = self._client.submit_order(order_data)
        except Exception as e:
            raise RuntimeError(f"Alpaca rejected BUY order for {ticker}: {e}") from e

        result = {
            "id": str(order.id),
            "ticker": ticker,
            "side": "buy",
            "notional": notional,
            "qty": None,
            "status": str(order.status),
            "submitted_at": str(order.submitted_at),
        }
        logger.info(
            f"BUY order accepted: {ticker}  id={result['id']}  status={result['status']}"
        )
        return result

    def submit_sell_order(self, ticker: str, qty: int) -> dict:
        """
        Submit a market SELL order for paper trading.

        Uses share quantity (not notional) so we sell exactly the
        right number of shares from our position.

        Args:
            ticker: Uppercase stock symbol
            qty:    Number of shares to sell (must be > 0)

        Returns:
            {
                "id":           "alpaca-order-uuid",
                "ticker":       "NVDA",
                "side":         "sell",
                "qty":          150,
                "notional":     None,
                "status":       "accepted",
                "submitted_at": "2026-06-02T09:32:00Z",
            }

        Raises:
            ValueError:  If qty <= 0 or ticker is empty
            RuntimeError: If Alpaca rejects the order
        """
        ticker = str(ticker or "").upper()
        if not ticker:
            raise ValueError("ticker must not be empty")
        if qty <= 0:
            raise ValueError(f"qty must be > 0, got {qty}")

        logger.info(f"Submitting paper SELL: {ticker}  qty={qty}")

        order_data = MarketOrderRequest(
            symbol=ticker,
            qty=qty,
            side=OrderSide.SELL,       # Always SELL — hardcoded
            time_in_force=TimeInForce.DAY,
        )

        try:
            order = self._client.submit_order(order_data)
        except Exception as e:
            raise RuntimeError(f"Alpaca rejected SELL order for {ticker}: {e}") from e

        result = {
            "id": str(order.id),
            "ticker": ticker,
            "side": "sell",
            "qty": qty,
            "notional": None,
            "status": str(order.status),
            "submitted_at": str(order.submitted_at),
        }
        logger.info(
            f"SELL order accepted: {ticker}  qty={qty}  id={result['id']}  status={result['status']}"
        )
        return result

    # ================================================================
    # Bulk operations (for emergency-close-all and cleanup)
    # ================================================================

    def cancel_all_orders(self) -> int:
        """
        Cancel all pending open orders.

        Returns:
            Number of orders cancelled (0 if no open orders).
        """
        try:
            response = self._client.cancel_orders()
            count = len(response) if response else 0
            logger.info(f"Cancelled {count} open order(s)")
            return count
        except Exception as e:
            logger.warning(f"Error cancelling orders: {e}")
            return 0

    def close_position(self, ticker: str) -> dict:
        """
        Close a single open position at market price.

        Fetches the actual position qty from Alpaca and submits
        a SELL order for that quantity.

        Args:
            ticker: Uppercase stock symbol

        Returns:
            Order dict from submit_sell_order()

        Raises:
            RuntimeError: If no position exists or Alpaca rejects the close
        """
        ticker = str(ticker or "").upper()
        positions = self.get_positions()

        if ticker not in positions:
            raise RuntimeError(
                f"Cannot close {ticker}: no open position found in Alpaca"
            )

        qty = positions[ticker]["qty"]
        if qty <= 0:
            raise RuntimeError(
                f"Cannot close {ticker}: position qty is {qty}"
            )

        return self.submit_sell_order(ticker, qty)

    def get_clock(self) -> dict:
        """
        Fetch the Alpaca market clock.

        Returns:
            {
                "is_open":    bool     — True if market is currently open
                "next_open":  datetime — next scheduled market open
                "next_close": datetime — next scheduled market close
            }

        Raises:
            RuntimeError: If Alpaca rejects the request.
        """
        try:
            clock = self._client.get_clock()
            return {
                "is_open": bool(clock.is_open),
                "next_open": clock.next_open,
                "next_close": clock.next_close,
            }
        except Exception as e:
            raise RuntimeError(f"Could not fetch market clock from Alpaca: {e}") from e

    def close_all_positions(self) -> List[dict]:
        """
        Close every open position at market price.

        Returns:
            List of order/error dicts for each close attempt.
            Returns [] if no positions are open.
        """
        positions = self.get_positions()
        if not positions:
            logger.info("close_all_positions: no open positions found")
            return []

        results = []
        for ticker in list(positions.keys()):
            try:
                order = self.close_position(ticker)
                results.append(order)
            except RuntimeError as e:
                logger.error(f"Failed to close {ticker}: {e}")
                results.append({
                    "ticker": ticker,
                    "side": "sell",
                    "status": "failed",
                    "error": str(e),
                })
        return results
