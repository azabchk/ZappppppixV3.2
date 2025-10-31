"""FastAPI application entry point for ZappppppixV3.2.

This module wires together the database layer, authentication helpers, Pydantic
schemas and matching engine to expose a REST API.  All endpoints are written
in English and follow the specification described in the project brief.
"""

import uuid
from datetime import datetime
from typing import List, Dict, Optional

from fastapi import FastAPI, Depends, HTTPException, Header, Path, Query
from sqlalchemy.orm import Session

from .database import get_db, create_all_tables
from .settings import get_settings
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
from .schemas import (
    RegisterRequest,
    UserResponse,
    InstrumentRequest,
    InstrumentResponse,
    OrderbookLevel,
    OrderbookSnapshot,
    TransactionResponse,
    OrderRequest,
    OrderResponse,
    SuccessResponse,
    DepositWithdrawRequest,
    Side,
    OrderType as SchemaOrderType,
    OrderStatus as SchemaOrderStatus,
)
from .auth import require_auth, require_admin
from .engine import MatchingEngine, balance_update_lock


settings = get_settings()

app = FastAPI(
    title="ZappppppixV3.2 API",
    version="3.2.0",
    description="A simple trading engine supporting market and limit orders, balances and instruments.",
)


@app.on_event("startup")
async def on_startup() -> None:
    """Initialise the database and bootstrap admin and instruments."""
    # Create tables if they do not exist
    create_all_tables()
    # Use a short‑lived session to perform bootstrap operations
    db = next(get_db())
    try:
        # Bootstrap admin user
        admin = db.query(User).filter(User.role == "ADMIN").first()
        if not admin:
            admin = User(
                id=uuid.uuid4(),
                name="Administrator",
                role="ADMIN",
                api_key=settings.admin_token,
            )
            db.add(admin)
            db.commit()
        else:
            # Always synchronise the API key from configuration
            if admin.api_key != settings.admin_token:
                admin.api_key = settings.admin_token
                db.commit()
        # Ensure default instruments exist.  Assign type CURRENCY to each.
        for ticker in settings.instrument_list():
            exists = db.query(Instrument).filter(Instrument.ticker == ticker).first()
            if not exists:
                instr = Instrument(ticker=ticker, type="CURRENCY")
                db.add(instr)
        db.commit()
    finally:
        db.close()


@app.get("/health", tags=["health"])
def health_check() -> Dict[str, str]:
    """Simple health check endpoint returning a static status."""
    return {"status": "healthy"}


@app.post("/api/v1/public/register", response_model=UserResponse, tags=["public"], summary="Register a new user")
def register_user(body: RegisterRequest, db: Session = Depends(get_db)) -> UserResponse:
    """Register a new user and return their API token.

    The caller must supply a non‑empty name.  A unique API key is generated
    using a UUID.  The resulting user is assigned the `USER` role.
    """
    new_id = uuid.uuid4()
    api_key = f"key-{uuid.uuid4()}"
    user = User(id=new_id, name=body.name, role="USER", api_key=api_key)
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserResponse(id=str(user.id), name=user.name, role=user.role, api_key=user.api_key)


@app.get("/api/v1/public/instrument", response_model=List[InstrumentResponse], tags=["public"], summary="List instruments")
def list_instruments(db: Session = Depends(get_db)) -> List[InstrumentResponse]:
    """Return all instruments currently available for trading."""
    instruments = db.query(Instrument).all()
    return [InstrumentResponse(ticker=i.ticker, type=i.type) for i in instruments]


@app.get("/api/v1/public/orderbook/{ticker}", response_model=OrderbookSnapshot, tags=["public"], summary="Get order book")
def get_orderbook(
    ticker: str = Path(..., description="Instrument ticker"),
    limit: int = Query(10, ge=1, le=25, description="Maximum number of price levels to return"),
    db: Session = Depends(get_db),
) -> OrderbookSnapshot:
    """Return an aggregated snapshot of open limit orders for a ticker.

    Buy orders are aggregated and sorted by descending price, while sell orders
    are aggregated and sorted by ascending price.  Only orders in NEW or
    PARTIALLY_EXECUTED status are considered.
    """
    # Verify instrument exists
    ticker = ticker.upper()
    instrument = db.query(Instrument).filter(Instrument.ticker == ticker).first()
    if not instrument:
        raise HTTPException(status_code=404, detail="Instrument not found")
    # Aggregate bids
    bids_raw = (
        db.query(Order.price, Order.quantity, Order.filled_quantity)
        .filter(
            Order.ticker == ticker,
            Order.side == OrderSide.BUY,
            Order.order_type == OrderType.LIMIT,
            Order.status.in_([OrderStatus.NEW, OrderStatus.PARTIALLY_EXECUTED]),
        )
        .all()
    )
    bid_levels: Dict[int, int] = {}
    for price, qty, filled in bids_raw:
        remaining = qty - filled
        if remaining <= 0:
            continue
        bid_levels[price] = bid_levels.get(price, 0) + remaining
    # Aggregate asks
    asks_raw = (
        db.query(Order.price, Order.quantity, Order.filled_quantity)
        .filter(
            Order.ticker == ticker,
            Order.side == OrderSide.SELL,
            Order.order_type == OrderType.LIMIT,
            Order.status.in_([OrderStatus.NEW, OrderStatus.PARTIALLY_EXECUTED]),
        )
        .all()
    )
    ask_levels: Dict[int, int] = {}
    for price, qty, filled in asks_raw:
        remaining = qty - filled
        if remaining <= 0:
            continue
        ask_levels[price] = ask_levels.get(price, 0) + remaining
    # Sort and limit
    sorted_bids = sorted(bid_levels.items(), key=lambda x: -x[0])[:limit]
    sorted_asks = sorted(ask_levels.items(), key=lambda x: x[0])[:limit]
    return OrderbookSnapshot(
        bids=[OrderbookLevel(price=p, quantity=q) for p, q in sorted_bids],
        asks=[OrderbookLevel(price=p, quantity=q) for p, q in sorted_asks],
    )


@app.get("/api/v1/public/transactions/{ticker}", response_model=List[TransactionResponse], tags=["public"], summary="Get recent trades")
def get_transactions(
    ticker: str = Path(..., description="Instrument ticker"),
    limit: int = Query(10, ge=1, le=100, description="Maximum number of trades to return"),
    db: Session = Depends(get_db),
) -> List[TransactionResponse]:
    """Return recent executed trades for the given ticker."""
    # Verify instrument exists
    ticker = ticker.upper()
    instrument = db.query(Instrument).filter(Instrument.ticker == ticker).first()
    if not instrument:
        raise HTTPException(status_code=404, detail="Instrument not found")
    transactions = (
        db.query(Transaction)
        .filter(Transaction.ticker == ticker)
        .order_by(Transaction.executed_at.desc())
        .limit(limit)
        .all()
    )
    return [
        TransactionResponse(price=t.price, quantity=t.quantity, executed_at=t.executed_at.replace(tzinfo=None))
        for t in transactions
    ]


@app.get("/api/v1/balance", response_model=Dict[str, int], tags=["balance"], summary="Get balances")
def get_balances(user: User = Depends(require_auth), db: Session = Depends(get_db)) -> Dict[str, int]:
    """Return all balances for the authenticated user."""
    balances = db.query(Balance).filter(Balance.user_id == user.id).all()
    return {b.ticker: b.amount for b in balances}


@app.post("/api/v1/order", response_model=OrderResponse, tags=["order"], summary="Place a new order")
async def place_order(
    body: OrderRequest,
    user: User = Depends(require_auth),
    db: Session = Depends(get_db),
) -> OrderResponse:
    """Create a new order for the authenticated user.

    Validation ensures that limit orders provide a price and that the user can
    afford the order.  The matching engine executes trades immediately and
    updates balances atomically.
    """
    # Convert Pydantic enums to string values expected by the engine
    try:
        order = await MatchingEngine(db).create_order(
            user=user,
            ticker=body.ticker,
            side=body.side.value,
            order_type=body.type.value,
            quantity=body.quantity,
            price=body.price,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Compute average execution price
    txs = db.query(Transaction).filter(
        (Transaction.buy_order_id == order.id) | (Transaction.sell_order_id == order.id)
    ).all()
    total_qty = sum(tx.quantity for tx in txs)
    total_value = sum(tx.price * tx.quantity for tx in txs)
    avg_price: Optional[float] = None
    if total_qty > 0:
        avg_price = total_value / total_qty
    remaining = order.quantity - order.filled_quantity
    return OrderResponse(
        order_id=str(order.id),
        ticker=order.ticker,
        side=Side(order.side),
        type=SchemaOrderType(order.order_type),
        original_quantity=order.quantity,
        filled_quantity=order.filled_quantity,
        remaining_quantity=remaining,
        average_execution_price=avg_price,
        status=SchemaOrderStatus(order.status),
        created_at=order.created_at.replace(tzinfo=None),
    )


@app.get("/api/v1/order", response_model=List[OrderResponse], tags=["order"], summary="List user orders")
def list_orders(user: User = Depends(require_auth), db: Session = Depends(get_db)) -> List[OrderResponse]:
    """Return all orders belonging to the authenticated user."""
    orders = db.query(Order).filter(Order.user_id == user.id).order_by(Order.created_at.desc()).all()
    responses: List[OrderResponse] = []
    for order in orders:
        # Compute fills
        txs = db.query(Transaction).filter(
            (Transaction.buy_order_id == order.id) | (Transaction.sell_order_id == order.id)
        ).all()
        total_qty = sum(tx.quantity for tx in txs)
        total_value = sum(tx.price * tx.quantity for tx in txs)
        avg_price: Optional[float] = None
        if total_qty > 0:
            avg_price = total_value / total_qty
        remaining = order.quantity - order.filled_quantity
        responses.append(
            OrderResponse(
                order_id=str(order.id),
                ticker=order.ticker,
                side=Side(order.side),
                type=SchemaOrderType(order.order_type),
                original_quantity=order.quantity,
                filled_quantity=order.filled_quantity,
                remaining_quantity=remaining,
                average_execution_price=avg_price,
                status=SchemaOrderStatus(order.status),
                created_at=order.created_at.replace(tzinfo=None),
            )
        )
    return responses


@app.get("/api/v1/order/{order_id}", response_model=OrderResponse, tags=["order"], summary="Get order details")
def get_order_details(
    order_id: str = Path(..., description="Order identifier"),
    user: User = Depends(require_auth),
    db: Session = Depends(get_db),
) -> OrderResponse:
    """Return a single order belonging to the authenticated user."""
    # Validate UUID
    try:
        parsed_id = uuid.UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid order_id format")
    order = db.query(Order).filter(Order.id == parsed_id, Order.user_id == user.id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    txs = db.query(Transaction).filter(
        (Transaction.buy_order_id == order.id) | (Transaction.sell_order_id == order.id)
    ).all()
    total_qty = sum(tx.quantity for tx in txs)
    total_value = sum(tx.price * tx.quantity for tx in txs)
    avg_price: Optional[float] = None
    if total_qty > 0:
        avg_price = total_value / total_qty
    remaining = order.quantity - order.filled_quantity
    return OrderResponse(
        order_id=str(order.id),
        ticker=order.ticker,
        side=Side(order.side),
        type=SchemaOrderType(order.order_type),
        original_quantity=order.quantity,
        filled_quantity=order.filled_quantity,
        remaining_quantity=remaining,
        average_execution_price=avg_price,
        status=SchemaOrderStatus(order.status),
        created_at=order.created_at.replace(tzinfo=None),
    )


@app.delete("/api/v1/order/{order_id}", response_model=SuccessResponse, tags=["order"], summary="Cancel order")
async def cancel_order(
    order_id: str = Path(..., description="Order identifier"),
    user: User = Depends(require_auth),
    db: Session = Depends(get_db),
) -> SuccessResponse:
    """Cancel an open limit order belonging to the authenticated user."""
    try:
        parsed_id = uuid.UUID(order_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid order_id format")
    order = db.query(Order).filter(Order.id == parsed_id, Order.user_id == user.id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    # Only limit orders in NEW or PARTIALLY_EXECUTED status with remaining quantity can be cancelled
    remaining = order.quantity - order.filled_quantity
    if order.order_type != OrderType.LIMIT or order.status not in [OrderStatus.NEW, OrderStatus.PARTIALLY_EXECUTED] or remaining <= 0:
        raise HTTPException(status_code=409, detail="Order cannot be cancelled in its current state")
    # Cancel the order
    order.status = OrderStatus.CANCELLED
    db.commit()
    return SuccessResponse(success=True)


@app.post("/api/v1/admin/balance/deposit", response_model=SuccessResponse, tags=["admin"], summary="Deposit funds")
async def deposit_balance(
    body: DepositWithdrawRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> SuccessResponse:
    """Increase a user’s balance of a given ticker by a positive amount."""
    # Validate target user
    try:
        target_id = uuid.UUID(body.user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id format")
    target_user = db.query(User).filter(User.id == target_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    # Validate instrument
    instrument = db.query(Instrument).filter(Instrument.ticker == body.ticker.upper()).first()
    if not instrument:
        raise HTTPException(status_code=404, detail="Instrument not found")
    # Perform update inside the global lock
    async with balance_update_lock:
        balance = db.query(Balance).filter(Balance.user_id == target_id, Balance.ticker == body.ticker.upper()).first()
        if balance:
            balance.amount += body.amount
            balance.updated_at = datetime.utcnow()
        else:
            new_balance = Balance(user_id=target_id, ticker=body.ticker.upper(), amount=body.amount, updated_at=datetime.utcnow())
            db.add(new_balance)
        db.commit()
    return SuccessResponse(success=True)


@app.post("/api/v1/admin/balance/withdraw", response_model=SuccessResponse, tags=["admin"], summary="Withdraw funds")
async def withdraw_balance(
    body: DepositWithdrawRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> SuccessResponse:
    """Decrease a user’s balance of a given ticker by a positive amount if sufficient funds exist."""
    try:
        target_id = uuid.UUID(body.user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id format")
    target_user = db.query(User).filter(User.id == target_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    instrument = db.query(Instrument).filter(Instrument.ticker == body.ticker.upper()).first()
    if not instrument:
        raise HTTPException(status_code=404, detail="Instrument not found")
    async with balance_update_lock:
        balance = db.query(Balance).filter(Balance.user_id == target_id, Balance.ticker == body.ticker.upper()).first()
        if not balance or balance.amount < body.amount:
            raise HTTPException(status_code=400, detail="Insufficient funds")
        balance.amount -= body.amount
        balance.updated_at = datetime.utcnow()
        db.commit()
    return SuccessResponse(success=True)


@app.post("/api/v1/admin/instrument", response_model=SuccessResponse, tags=["admin"], summary="Add instrument")
def add_instrument(
    body: InstrumentRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> SuccessResponse:
    """Add a new trading instrument.  Ticker must be unique."""
    existing = db.query(Instrument).filter(Instrument.ticker == body.ticker).first()
    if existing:
        raise HTTPException(status_code=400, detail="Instrument already exists")
    instr = Instrument(ticker=body.ticker.upper(), type=body.type)
    db.add(instr)
    db.commit()
    return SuccessResponse(success=True)


@app.delete("/api/v1/admin/instrument/{ticker}", response_model=SuccessResponse, tags=["admin"], summary="Delete instrument")
async def delete_instrument(
    ticker: str = Path(..., description="Ticker to remove"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> SuccessResponse:
    """Remove an instrument and all associated orders, balances and trades."""
    instr = db.query(Instrument).filter(Instrument.ticker == ticker.upper()).first()
    if not instr:
        raise HTTPException(status_code=404, detail="Instrument not found")
    async with balance_update_lock:
        # Delete balances
        db.query(Balance).filter(Balance.ticker == ticker).delete(synchronize_session=False)
        # Delete orders
        db.query(Order).filter(Order.ticker == ticker).delete(synchronize_session=False)
        # Delete transactions
        db.query(Transaction).filter(Transaction.ticker == ticker).delete(synchronize_session=False)
        # Delete the instrument
        db.delete(instr)
        db.commit()
    return SuccessResponse(success=True)


@app.delete("/api/v1/admin/user/{user_id}", response_model=SuccessResponse, tags=["admin"], summary="Delete user")
async def delete_user(
    user_id: str = Path(..., description="User identifier to remove"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> SuccessResponse:
    """Delete a user and all their associated balances, orders and trades."""
    try:
        target_id = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id format")
    target_user = db.query(User).filter(User.id == target_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    async with balance_update_lock:
        # Collect order IDs belonging to the user
        order_ids = [o.id for o in db.query(Order.id).filter(Order.user_id == target_id).all()]
        if order_ids:
            # Delete transactions where the user was buyer or seller
            db.query(Transaction).filter(
                (Transaction.buy_order_id.in_(order_ids)) | (Transaction.sell_order_id.in_(order_ids))
            ).delete(synchronize_session=False)
        # Delete balances
        db.query(Balance).filter(Balance.user_id == target_id).delete(synchronize_session=False)
        # Delete orders
        db.query(Order).filter(Order.user_id == target_id).delete(synchronize_session=False)
        # Finally delete the user
        db.delete(target_user)
        db.commit()
    return SuccessResponse(success=True)