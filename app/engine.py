"""Order matching engine for ZappppppixV3.2.

This module encapsulates the logic for placing orders, matching them against
existing limit orders and updating account balances.  A single global
`balance_update_lock` is used to serialise all balance modifications, ensuring
that concurrent requests cannot corrupt user balances.  Matching occurs
immediately when an order is placed; market orders never rest on the book.
"""

import asyncio
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from .models import (
    User,
    Instrument,
    Balance,
    Order,
    Transaction,
    OrderSide,
    OrderType,
    OrderStatus,
)


# Global lock to serialise balance updates and matching.  This is shared
# between the engine and administrative endpoints.
balance_update_lock = asyncio.Lock()


class MatchingEngine:
    """A simple matching engine for market and limit orders."""

    def __init__(self, db: Session):
        self.db = db

    async def create_order(self, user: User, ticker: str, side: str, order_type: str, quantity: int, price: Optional[int]) -> Order:
        """Create a new order and attempt to match it immediately.

        A new `Order` record is inserted into the database.  The matching
        process runs synchronously with the request: limit orders are matched
        against existing opposite limit orders based on price/time priority,
        whereas market orders consume the best available liquidity.  Any
        remaining quantity from a limit order stays on the book; market orders
        never remain on the book.

        Balance checks are performed before order insertion.  If the user
        cannot afford the order, a `ValueError` is raised.  Insufficient
        liquidity for a market order does not raise but will result in the
        order finishing without full execution.
        """
        # Normalise ticker to uppercase
        ticker = ticker.upper()
        # Ensure instrument exists
        instrument = self.db.query(Instrument).filter(Instrument.ticker == ticker).first()
        if not instrument:
            raise ValueError("Instrument not found")

        # Pre‑validate balances.  For buy orders the user must have enough
        # quote currency (RUB) to cover quantity * price.  For market orders we
        # conservatively assume a price of 1 if no price is provided.  This
        # conservative check prevents obviously unaffordable orders.
        if side == OrderSide.BUY:
            effective_price = price if (order_type == OrderType.LIMIT and price is not None) else 1
            total_cost = quantity * effective_price
            rub_balance = self.db.query(Balance).filter(Balance.user_id == user.id, Balance.ticker == "RUB").first()
            if not rub_balance or rub_balance.amount < total_cost:
                raise ValueError("Insufficient balance for purchase")
        elif side == OrderSide.SELL:
            asset_balance = self.db.query(Balance).filter(Balance.user_id == user.id, Balance.ticker == ticker).first()
            if not asset_balance or asset_balance.amount < quantity:
                raise ValueError("Insufficient balance for sale")

        # Create the order
        order = Order(
            user_id=user.id,
            ticker=ticker,
            side=side,
            order_type=order_type,
            price=price,
            quantity=quantity,
            filled_quantity=0,
            status=OrderStatus.NEW,
            created_at=datetime.utcnow(),
        )
        self.db.add(order)
        # Flush to assign an ID to the order before matching
        self.db.flush()

        # Perform matching inside the balance lock to ensure atomic updates
        async with balance_update_lock:
            if order_type == OrderType.MARKET:
                await self._match_market_order(order)
            else:
                await self._match_limit_order(order)
        # Commit all database changes
        self.db.commit()
        return order

    async def _match_market_order(self, order: Order) -> None:
        """Match a market order against the best opposite limit orders."""
        remaining = order.quantity - order.filled_quantity
        if remaining <= 0:
            return
        # Determine the opposite side and sort order
        if order.side == OrderSide.BUY:
            opposite_side = OrderSide.SELL
            price_order = Order.price.asc()
        else:
            opposite_side = OrderSide.BUY
            price_order = Order.price.desc()
        # Fetch candidate opposite orders: limit orders only, open status
        opposite_orders = (
            self.db.query(Order)
            .filter(
                Order.ticker == order.ticker,
                Order.side == opposite_side,
                Order.order_type == OrderType.LIMIT,
                Order.status.in_([OrderStatus.NEW, OrderStatus.PARTIALLY_EXECUTED]),
            )
            .order_by(price_order, Order.created_at)
            .all()
        )
        for opp in opposite_orders:
            if remaining <= 0:
                break
            available = opp.quantity - opp.filled_quantity
            if available <= 0:
                continue
            trade_qty = min(remaining, available)
            execution_price = opp.price
            await self._execute_trade(order, opp, trade_qty, execution_price)
            remaining -= trade_qty
        # Finalise status for market order: once matching is done we mark as
        # executed regardless of how much was filled.  Unfilled remainder does
        # not stay on the book.
        if order.filled_quantity > 0:
            order.status = OrderStatus.EXECUTED
        else:
            order.status = OrderStatus.CANCELLED

    async def _match_limit_order(self, order: Order) -> None:
        """Attempt to match a limit order against opposite limit orders."""
        remaining = order.quantity - order.filled_quantity
        if remaining <= 0:
            return
        # Determine matching conditions based on side
        if order.side == OrderSide.BUY:
            # Match sells priced at or below our limit price
            opposite_side = OrderSide.SELL
            opposite_orders = (
                self.db.query(Order)
                .filter(
                    Order.ticker == order.ticker,
                    Order.side == opposite_side,
                    Order.order_type == OrderType.LIMIT,
                    Order.status.in_([OrderStatus.NEW, OrderStatus.PARTIALLY_EXECUTED]),
                    Order.price <= order.price,
                )
                .order_by(Order.price.asc(), Order.created_at)
                .all()
            )
        else:
            # Sell: match buys priced at or above our limit price
            opposite_side = OrderSide.BUY
            opposite_orders = (
                self.db.query(Order)
                .filter(
                    Order.ticker == order.ticker,
                    Order.side == opposite_side,
                    Order.order_type == OrderType.LIMIT,
                    Order.status.in_([OrderStatus.NEW, OrderStatus.PARTIALLY_EXECUTED]),
                    Order.price >= order.price,
                )
                .order_by(Order.price.desc(), Order.created_at)
                .all()
            )
        for opp in opposite_orders:
            if remaining <= 0:
                break
            available = opp.quantity - opp.filled_quantity
            if available <= 0:
                continue
            trade_qty = min(remaining, available)
            execution_price = opp.price
            await self._execute_trade(order, opp, trade_qty, execution_price)
            remaining -= trade_qty
        # Update order status
        if order.filled_quantity >= order.quantity:
            order.status = OrderStatus.EXECUTED
        elif order.filled_quantity > 0:
            order.status = OrderStatus.PARTIALLY_EXECUTED
        else:
            order.status = OrderStatus.NEW

    async def _execute_trade(self, taker: Order, maker: Order, quantity: int, price: int) -> None:
        """Execute a trade between two orders and update balances and order state."""
        # Determine buyer and seller based on order sides
        if taker.side == OrderSide.BUY:
            buyer_order = taker
            seller_order = maker
        else:
            buyer_order = maker
            seller_order = taker
        # Update filled quantities
        taker.filled_quantity += quantity
        maker.filled_quantity += quantity
        # Update order statuses for the maker
        if maker.filled_quantity >= maker.quantity:
            maker.status = OrderStatus.EXECUTED
        else:
            maker.status = OrderStatus.PARTIALLY_EXECUTED
        # Record the transaction
        transaction = Transaction(
            ticker=taker.ticker,
            price=price,
            quantity=quantity,
            executed_at=datetime.utcnow(),
            buy_order_id=buyer_order.id,
            sell_order_id=seller_order.id,
        )
        self.db.add(transaction)
        # Adjust balances
        await self._apply_balance_changes(buyer_order.user_id, seller_order.user_id, taker.ticker, quantity, price)

    async def _apply_balance_changes(self, buyer_id: uuid.UUID, seller_id: uuid.UUID, ticker: str, quantity: int, price: int) -> None:
        """Apply balance changes for a single trade.

        The buyer gains `quantity` units of `ticker` and loses `quantity * price`
        of RUB.  The seller loses `quantity` units of `ticker` and gains
        `quantity * price` of RUB.  Balance records are created as needed.
        """
        total_cost = quantity * price
        # Buyer gains the asset and loses RUB
        self._update_balance(buyer_id, ticker, quantity)
        self._update_balance(buyer_id, "RUB", -total_cost)
        # Seller loses the asset and gains RUB
        self._update_balance(seller_id, ticker, -quantity)
        self._update_balance(seller_id, "RUB", total_cost)

    def _update_balance(self, user_id: uuid.UUID, ticker: str, delta: int) -> None:
        """Increment or create a balance entry for a user and ticker."""
        balance = (
            self.db.query(Balance)
            .filter(Balance.user_id == user_id, Balance.ticker == ticker)
            .first()
        )
        if balance:
            balance.amount += delta
            balance.updated_at = datetime.utcnow()
        else:
            new_balance = Balance(
                user_id=user_id,
                ticker=ticker,
                amount=delta,
                updated_at=datetime.utcnow(),
            )
            self.db.add(new_balance)