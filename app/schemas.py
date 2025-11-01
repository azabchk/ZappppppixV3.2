# app/schemas.py
"""Pydantic schema definitions used by the API (English-only, Pydantic v2)."""

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


# ===== Enums =====
class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class OrderStatus(str, Enum):
    NEW = "NEW"
    EXECUTED = "EXECUTED"
    PARTIALLY_EXECUTED = "PARTIALLY_EXECUTED"
    CANCELLED = "CANCELLED"


# ===== Public: register =====
class RegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, description="User name")


class UserResponse(BaseModel):
    id: str
    name: str
    role: str
    api_key: str


# ===== Instruments =====
class InstrumentRequest(BaseModel):
    ticker: str = Field(
        ...,
        min_length=1,
        max_length=16,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Instrument ticker",
    )
    type: str = Field(..., min_length=1, description="Instrument type (e.g. CURRENCY, STOCK)")


class InstrumentResponse(BaseModel):
    ticker: str
    type: str


# ===== Order book (L2 snapshot) =====
class OrderbookLevel(BaseModel):
    price: int
    quantity: int


class OrderbookSnapshot(BaseModel):
    bids: List[OrderbookLevel]
    asks: List[OrderbookLevel]


# ===== Executed transactions =====
class TransactionResponse(BaseModel):
    price: int
    quantity: int
    executed_at: datetime = Field(..., description="ISO8601 UTC timestamp of the trade")


# ===== Orders =====
class OrderRequest(BaseModel):
    ticker: str = Field(..., description="Instrument ticker")
    side: Side = Field(..., description="BUY or SELL")
    type: OrderType = Field(..., description="LIMIT or MARKET")
    quantity: int = Field(..., gt=0, description="Positive quantity")
    price: Optional[int] = Field(None, gt=0, description="Required for LIMIT; ignored for MARKET")

    @model_validator(mode="after")
    def _enforce_price_for_limit(self):
        # LIMIT orders must have a positive price; MARKET orders must not keep a price
        if self.type == OrderType.LIMIT:
            if self.price is None or self.price <= 0:
                raise ValueError("Price must be a positive number for LIMIT orders")
        else:
            self.price = None
        return self


class OrderResponse(BaseModel):
    order_id: str
    ticker: str
    side: Side
    type: OrderType
    original_quantity: int
    filled_quantity: int
    remaining_quantity: int
    average_execution_price: Optional[float] = None
    status: OrderStatus
    created_at: datetime


# ===== Generic success and admin bodies =====
class SuccessResponse(BaseModel):
    success: bool = True


class DepositWithdrawRequest(BaseModel):
    user_id: str = Field(..., description="Target user identifier")
    ticker: str = Field(..., description="Asset ticker")
    amount: int = Field(..., gt=0, description="Positive amount to deposit or withdraw")