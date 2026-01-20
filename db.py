"""
База данных и схемы
"""
import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    DECIMAL,
    JSON,
    UUID,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

metadata = MetaData()

# Таблица ордеров
orders_tbl = Table(
    "orders",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("user_id", Text, nullable=False),
    Column("payment_id", Text, nullable=True),
    Column("items", JSON, nullable=False),
    Column("amount", DECIMAL(10, 2), nullable=False),
    Column("created_at", DateTime, server_default=func.now()),
)

# Таблица статусов ордеров
order_statuses_tbl = Table(
    "order_statuses",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("order_id", UUID(as_uuid=True), ForeignKey("orders.id"), nullable=False),
    Column("status", String(50), nullable=False),
    Column("created_at", DateTime, server_default=func.now()),
)

# Таблица outbox для событий
outbox_tbl = Table(
    "outbox",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("event_type", String(100), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("status", String(20), nullable=False, default="PENDING"),
    Column("created_at", DateTime, server_default=func.now()),
)


class OrderStatusEnum(StrEnum):
    NEW = "NEW"
    PAID = "PAID"
    SHIPPED = "SHIPPED"
    CANCELLED = "CANCELLED"


class OutboxStatusEnum(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"


# Глобальные переменные для подключения
engine = None
session_factory = None


async def init_db(database_url: str):
    """Инициализация базы данных"""
    global engine, session_factory

    engine = create_async_engine(database_url, echo=True)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    # Создаем таблицы
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)


async def close_db():
    """Закрытие подключения к БД"""
    global engine
    if engine:
        await engine.dispose()


def get_session() -> async_sessionmaker[AsyncSession]:
    """Получить session factory"""
    if session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return session_factory
