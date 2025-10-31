"""Database models for ZappppppixV3.2.

The tables defined in this module represent users, instruments, account balances,
orders and executed trades.  SQLAlchemy’s ORM is used to map Python classes
onto relational tables.  Relationships are defined to simplify navigation
between entities.
"""

import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, DateTime, Enum, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from .database import Base


class UserRole(str):
    """Enumeration of user roles."""
    USER = "USER"
    ADMIN = "ADMIN"


class OrderSide(str):
    """Enumeration of order sides."""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str):
    """Enumeration of order types."""
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class OrderStatus(str):
    """Enumeration of order statuses."""
    NEW = "NEW"
    EXECUTED = "EXECUTED"
    PARTIALLY_EXECUTED = "PARTIALLY_EXECUTED"
    CANCELLED = "CANCELLED"


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    api_key = Column(String, unique=True, nullable=False)
    role = Column(String, nullable=False, default=UserRole.USER)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    balances = relationship("Balance", back_populates="user", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="user", cascade="all, delete-orphan")
    # Transactions are linked via buy_order_id and sell_order_id in Transaction


class Instrument(Base):
    __tablename__ = "instruments"

    ticker = Column(String, primary_key=True)
    type = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    orders = relationship("Order", back_populates="instrument", cascade="all, delete-orphan")
    transactions = relationship("Transaction", back_populates="instrument", cascade="all, delete-orphan")


class Balance(Base):
    __tablename__ = "balances"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    ticker = Column(String, ForeignKey("instruments.ticker"), primary_key=True)
    amount = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="balances")
    instrument = relationship("Instrument")


class Order(Base):
    __tablename__ = "orders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    ticker = Column(String, ForeignKey("instruments.ticker"), nullable=False)
    side = Column(String, nullable=False)  # BUY or SELL
    order_type = Column(String, nullable=False)  # LIMIT or MARKET
    price = Column(Integer, nullable=True)  # Price per unit for limit orders
    quantity = Column(Integer, nullable=False)  # Total quantity requested
    filled_quantity = Column(Integer, nullable=False, default=0)  # Quantity already filled
    status = Column(String, nullable=False, default=OrderStatus.NEW)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="orders")
    instrument = relationship("Instrument", back_populates="orders")
    # Transactions are linked via buy_order_id and sell_order_id


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, ForeignKey("instruments.ticker"), nullable=False)
    price = Column(Integer, nullable=False)
    quantity = Column(Integer, nullable=False)
    executed_at = Column(DateTime, default=datetime.utcnow)
    buy_order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False)
    sell_order_id = Column(UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False)

    # Relationships
    instrument = relationship("Instrument", back_populates="transactions")
    buy_order = relationship("Order", foreign_keys=[buy_order_id])
    sell_order = relationship("Order", foreign_keys=[sell_order_id])