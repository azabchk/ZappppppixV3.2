from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from database import User as UserDB, Instrument as InstrumentDB, Balance as BalanceDB, Order as OrderDB, Transaction as TransactionDB
from schemas import *
import uuid
import time
import random
from datetime import datetime
from typing import List, Dict, Optional, Union
import threading
import asyncio

balance_update_lock = asyncio.Lock()

class TradingEngine:
    """ZapppppixV3.2."""
    
    def __init__(self, db: Session):
        self.db = db
    
    async def create_order(self, user: UserDB, order_data: Union[LimitOrderBody, MarketOrderBody]) -> str:
        """Create an order."""
        # Verify the instrument exists
        instrument = self.db.query(InstrumentDB).filter(InstrumentDB.ticker == order_data.ticker).first()
        if not instrument:
            raise ValueError("Instrument not found")
        # Check balance before creating the order
        if order_data.direction == "BUY":
            # Buying uses RUB balance
            total_cost = order_data.qty * (getattr(order_data, 'price', 0) or 1)
            rub_balance = self.db.query(BalanceDB).filter(BalanceDB.user_id == user.id, BalanceDB.ticker == "RUB").first()
            if not rub_balance or rub_balance.amount < total_cost:
                raise ValueError("Insufficient funds to buy")
        elif order_data.direction == "SELL":
            # Selling requires the asset
            asset_balance = self.db.query(BalanceDB).filter(BalanceDB.user_id == user.id, BalanceDB.ticker == order_data.ticker).first()
            if not asset_balance or asset_balance.amount < order_data.qty:
                raise ValueError("Insufficient asset to sell")
        order_id = str(uuid.uuid4())
        order_type = "LIMIT" if isinstance(order_data, LimitOrderBody) else "MARKET"
        
        # Create the order
        order = OrderDB(
            id=order_id,
            user_id=user.id,
            ticker=order_data.ticker,
            direction=order_data.direction,
            qty=order_data.qty,
            price=getattr(order_data, 'price', None),
            order_type=order_type,
            status="NEW",
            filled=0
        )
        
        self.db.add(order)
        
        # For market orders, try to execute immediately
        if order_type == "MARKET":
            await self._execute_market_order(order)
        else:
            await self._try_execute_limit_order(order)
        
        self.db.commit()
        return order_id
    
    async def _execute_market_order(self, order: OrderDB):
        """Execute a market order."""
        # Find best opposite-side orders
        opposite_direction = "SELL" if order.direction == "BUY" else "BUY"
        opposite_orders = self.db.query(OrderDB).filter(
            OrderDB.ticker == order.ticker,
            OrderDB.direction == opposite_direction,
            OrderDB.status.in_(["NEW", "PARTIALLY_EXECUTED"]),
            OrderDB.order_type == "LIMIT"
        ).order_by(
            OrderDB.price.asc() if order.direction == "BUY" else OrderDB.price.desc()
        ).all()
        
        remaining_qty = order.qty
        
        for opposite_order in opposite_orders:
            if remaining_qty <= 0:
                break
            
            # Determine execution quantity
            available_qty = opposite_order.qty - opposite_order.filled
            execute_qty = min(remaining_qty, available_qty)
            
            if execute_qty > 0:
                # Create transaction
                transaction = TransactionDB(
                    ticker=order.ticker,
                    amount=execute_qty,
                    price=opposite_order.price,
                    buyer_id=order.user_id if order.direction == "BUY" else opposite_order.user_id,
                    seller_id=order.user_id if order.direction == "SELL" else opposite_order.user_id
                )
                self.db.add(transaction)
                
                # Update orders
                order.filled += execute_qty
                opposite_order.filled += execute_qty
                remaining_qty -= execute_qty
                
                # Update statuses
                if opposite_order.filled >= opposite_order.qty:
                    opposite_order.status = "EXECUTED"
                else:
                    opposite_order.status = "PARTIALLY_EXECUTED"
                
                # Update balances
                await self._update_balances_after_trade(order, opposite_order, execute_qty, opposite_order.price)
        
        # Update market order status
        if order.filled >= order.qty:
            order.status = "EXECUTED"
        elif order.filled > 0:
            order.status = "PARTIALLY_EXECUTED"
        else:
            # Market order not filled — cancel it
            order.status = "CANCELLED"
    
    async def _try_execute_limit_order(self, order: OrderDB):
        """Attempt to execute a limit order."""
        # Similar to market orders but with price checks
        opposite_direction = "SELL" if order.direction == "BUY" else "BUY"
        if order.direction == "BUY":
            # Buy: find sell orders priced <= our price
            opposite_orders = self.db.query(OrderDB).filter(
                OrderDB.ticker == order.ticker,
                OrderDB.direction == opposite_direction,
                OrderDB.status.in_(["NEW", "PARTIALLY_EXECUTED"]),
                OrderDB.price <= order.price
            ).order_by(OrderDB.price.asc()).all()
        else:
            # Sell: find buy orders priced >= our price
            opposite_orders = self.db.query(OrderDB).filter(
                OrderDB.ticker == order.ticker,
                OrderDB.direction == opposite_direction,
                OrderDB.status.in_(["NEW", "PARTIALLY_EXECUTED"]),
                OrderDB.price >= order.price
            ).order_by(OrderDB.price.desc()).all()
        
        remaining_qty = order.qty
        
        for opposite_order in opposite_orders:
            if remaining_qty <= 0:
                break
            
            available_qty = opposite_order.qty - opposite_order.filled
            execute_qty = min(remaining_qty, available_qty)
            
            if execute_qty > 0:
                # Execution price is the resting order's price
                execution_price = opposite_order.price
                
                transaction = TransactionDB(
                    ticker=order.ticker,
                    amount=execute_qty,
                    price=execution_price,
                    buyer_id=order.user_id if order.direction == "BUY" else opposite_order.user_id,
                    seller_id=order.user_id if order.direction == "SELL" else opposite_order.user_id
                )
                self.db.add(transaction)
                order.filled += execute_qty
                opposite_order.filled += execute_qty
                remaining_qty -= execute_qty
                
                if opposite_order.filled >= opposite_order.qty:
                    opposite_order.status = "EXECUTED"
                else:
                    opposite_order.status = "PARTIALLY_EXECUTED"
                
                await self._update_balances_after_trade(order, opposite_order, execute_qty, execution_price)
        
        # Update our order status
        if order.filled >= order.qty:
            order.status = "EXECUTED"
        elif order.filled > 0:
            order.status = "PARTIALLY_EXECUTED"
        # Otherwise it remains NEW
    
    async def _update_balances_after_trade(self, order1: OrderDB, order2: OrderDB, qty: int, price: int):
        """Update balances after a trade."""
        buyer_id = order1.user_id if order1.direction == "BUY" else order2.user_id
        seller_id = order1.user_id if order1.direction == "SELL" else order2.user_id
        ticker = order1.ticker
        total_cost = qty * price
        
        # Map to accumulate balance changes
        balance_changes = {}
        
        # Helper to record a balance change
        def update_balance(user_id: str, ticker: str, amount_change: int):
            key = (user_id, ticker)
            if key not in balance_changes:
                balance_changes[key] = 0
            balance_changes[key] += amount_change
        
        # Record all changes
        update_balance(buyer_id, ticker, qty)        # Buyer receives the asset
        update_balance(buyer_id, "RUB", -total_cost) # Buyer pays RUB
        update_balance(seller_id, ticker, -qty)      # Seller gives up the asset
        update_balance(seller_id, "RUB", total_cost) # Seller receives RUB
        
        # Sort changes to avoid deadlocks
        sorted_changes = sorted(balance_changes.items(), key=lambda x: (str(x[0][0]), x[0][1]))
        
        # Critical section: apply balance updates
        async with balance_update_lock:
            # Apply changes to the database with retry logic
            for (user_id, ticker), amount_change in sorted_changes:
                if amount_change == 0:
                    continue
                
                await self._upsert_balance_with_retry(user_id, ticker, amount_change)
    
    async def _upsert_balance_with_retry(self, user_id: str, ticker: str, amount_change: int, max_retries: int = 3):
        """Safe balance update with atomic upsert and retry/backoff for deadlocks."""
        for attempt in range(max_retries):
            try:
                # Use PostgreSQL ON CONFLICT for atomic upsert
                upsert_sql = text("""
                    INSERT INTO balances (user_id, ticker, amount, updated_at)
                    VALUES (:user_id, :ticker, :amount, :updated_at)
                    ON CONFLICT (user_id, ticker)
                    DO UPDATE SET 
                        amount = balances.amount + :amount,
                        updated_at = :updated_at
                """)
                
                self.db.execute(upsert_sql, {
                    'user_id': user_id,
                    'ticker': ticker,
                    'amount': amount_change,
                    'updated_at': datetime.utcnow()
                })
                break  # Success — exit loop
                
            except OperationalError as e:
                if "deadlock detected" in str(e).lower() and attempt < max_retries - 1:
                    # Deadlock detected — wait then retry (exponential backoff)
                    wait_time = random.uniform(0.01, 0.1) * (2 ** attempt)
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    # Last attempt or a different error — re-raise
                    raise
    
    def cancel_order(self, order_id: str, user: UserDB) -> bool:
        """Cancel an order."""
        order = self.db.query(OrderDB).filter(
            OrderDB.id == order_id,
            OrderDB.user_id == user.id,
            OrderDB.status.in_(["NEW", "PARTIALLY_EXECUTED"])
        ).first()
        
        if not order:
            return False
        
        order.status = "CANCELLED"
        self.db.commit()
        return True
    
    def get_orderbook(self, ticker: str, limit: int = 10) -> L2OrderBook:
        """Get the order book."""
        # Buy orders (bids) — sort by descending price
        bids = self.db.query(OrderDB).filter(
            OrderDB.ticker == ticker,
            OrderDB.direction == "BUY",
            OrderDB.status.in_(["NEW", "PARTIALLY_EXECUTED"]),
            OrderDB.order_type == "LIMIT"
        ).order_by(OrderDB.price.desc()).all()
        # Sell orders (asks) — sort by ascending price
        asks = self.db.query(OrderDB).filter(
            OrderDB.ticker == ticker,
            OrderDB.direction == "SELL",
            OrderDB.status.in_(["NEW", "PARTIALLY_EXECUTED"]),
            OrderDB.order_type == "LIMIT"
        ).order_by(OrderDB.price.asc()).all()
        # Aggregate by price for unfilled remainder only
        bid_levels = {}
        for bid in bids:
            qty = bid.qty - bid.filled
            if qty <= 0:
                continue
            price = bid.price
            if price in bid_levels:
                bid_levels[price] += qty
            else:
                bid_levels[price] = qty
        ask_levels = {}
        for ask in asks:
            qty = ask.qty - ask.filled
            if qty <= 0:
                continue
            price = ask.price
            if price in ask_levels:
                ask_levels[price] += qty
            else:
                ask_levels[price] = qty
        # Sort order book levels
        bid_levels_sorted = sorted(bid_levels.items(), key=lambda x: -x[0])
        ask_levels_sorted = sorted(ask_levels.items(), key=lambda x: x[0])
        return L2OrderBook(
            bid_levels=[Level(price=price, qty=qty) for price, qty in bid_levels_sorted[:limit]],
            ask_levels=[Level(price=price, qty=qty) for price, qty in ask_levels_sorted[:limit]]
        )
