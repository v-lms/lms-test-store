"""
Простое приложение для тестирования интеграции с капаши
"""
import json
import logging
import os
import uuid
from decimal import Decimal
from typing import Optional
from uuid import UUID

import httpx
from aiokafka import AIOKafkaProducer
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings
from sqlalchemy.ext.asyncio import AsyncSession

import db
from db import OrderStatusEnum, get_session
from repositories import (
    create_order as create_order_in_db,
    create_outbox_event,
    find_order_by_payment_id,
    get_order_by_id,
    get_order_status,
    update_order_status,
)

app = FastAPI(
    title="LMS Test Store",
    description="Тестовое приложение для интеграции с капаши",
    version="1.0.0",
)

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Настройки приложения"""
    capashi_url: str = "https://capashi.dev-1.python-labs.ru"
    capashi_api_key: str = "b5brEutpPGGf6mGNqpTbFTAZPL8ILEuJ2RQf3jM7P-4"
    callback_base_url: str = "http://order-service-707e52c1-1f84-4687-b3e6-9b0a54c49fb9-web.order-service-707e52c1-1f84-4687-b3e6-9b0a54c49fb9.svc:8000"
    kafka_brokers: str = "kafka.kafka.svc.cluster.local:9092"
    kafka_topic_order_events: str = "student_vladarefiev_order.events"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/teststore"

    class Config:
        env_file = ".env"
        case_sensitive = False

    @model_validator(mode="before")
    @classmethod
    def set_database_url_from_postgres_connection_string(cls, values):
        """Read POSTGRES_CONNECTION_STRING and set it as database_url"""
        if isinstance(values, dict):
            # Check if POSTGRES_CONNECTION_STRING is in environment
            postgres_conn = os.getenv("POSTGRES_CONNECTION_STRING")
            if postgres_conn:
                # Convert postgres:// or postgresql:// to postgresql+asyncpg://
                if postgres_conn.startswith("postgres://"):
                    values["database_url"] = postgres_conn.replace("postgres://", "postgresql+asyncpg://")
                elif postgres_conn.startswith("postgresql://"):
                    values["database_url"] = postgres_conn.replace("postgresql://", "postgresql+asyncpg://")
                elif postgres_conn.startswith("postgresql+asyncpg://"):
                    values["database_url"] = postgres_conn
                else:
                    values["database_url"] = postgres_conn
            # Also check if it's in the values dict (from .env file)
            elif "POSTGRES_CONNECTION_STRING" in values:
                conn = values["POSTGRES_CONNECTION_STRING"]
                if conn.startswith("postgres://"):
                    values["database_url"] = conn.replace("postgres://", "postgresql+asyncpg://")
                elif conn.startswith("postgresql://"):
                    values["database_url"] = conn.replace("postgresql://", "postgresql+asyncpg://")
                elif conn.startswith("postgresql+asyncpg://"):
                    values["database_url"] = conn
                else:
                    values["database_url"] = conn

            # Read CAPASHI_URL from environment if available
            capashi_url = os.getenv("CAPASHI_URL")
            if capashi_url:
                values["capashi_url"] = capashi_url
            elif "CAPASHI_URL" in values:
                values["capashi_url"] = values["CAPASHI_URL"]
        return values


settings = Settings()

# Global Kafka producer
kafka_producer: Optional[AIOKafkaProducer] = None


# Schemas
class OrderCreate(BaseModel):
    """Создание ордера"""
    amount: Decimal = Field(..., gt=0, description="Сумма платежа")
    item_id: Optional[str] = Field(None, description="ID товара (опционально)")
    user_id: str = Field(default="user-1", description="ID пользователя")
    idempotency_key: Optional[str] = Field(None, description="Ключ идемпотентности")


class OrderResponse(BaseModel):
    """Ответ при создании ордера"""
    order_id: str
    payment_id: str
    amount: Decimal
    status: str


class OrderDetailResponse(BaseModel):
    """Детальная информация об ордере"""
    id: str
    user_id: str
    payment_id: str
    items: list[dict]
    amount: Decimal
    status: str
    created_at: Optional[str] = None


class PaymentCallback(BaseModel):
    """Callback от капаши"""
    payment_id: str
    order_id: str
    status: str
    amount: Decimal
    error_message: Optional[str] = None
    processed_at: str


@app.get("/")
async def root():
    """Корневой endpoint"""
    return {
        "message": "LMS Test Store API",
        "version": "1.0.0",
        "capashi_url": settings.capashi_url,
    }


@app.get("/health")
async def health():
    """Health check"""
    return {"status": "ok"}


@app.get("/orders/{order_id}", response_model=OrderDetailResponse)
async def get_order(order_id: str):
    """
    Получить информацию об ордере по ID.

    Возвращает полную информацию об ордере, включая текущий статус.
    """
    try:
        order_id_uuid = UUID(order_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid order_id format: {order_id}",
        )

    session_factory = get_session()
    async with session_factory() as session:
        order_data = await get_order_by_id(session, order_id_uuid)

        if not order_data:
            raise HTTPException(
                status_code=404,
                detail=f"Order not found: {order_id}",
            )

        return OrderDetailResponse(**order_data)


@app.post("/orders", response_model=OrderResponse, status_code=201)
async def create_order(order_data: OrderCreate):
    """
    Создать ордер и платеж в капаши.

    1. Создаем ордер в БД со статусом NEW
    2. Создаем платеж в капаши
    3. Сохраняем payment_id в ордере
    """
    # Генерируем order_id
    order_id_uuid = uuid.uuid4()
    order_id_str = str(order_id_uuid)

    # Формируем callback URL
    callback_url = f"{settings.callback_base_url}/callback"

    # Создаем платеж в капаши
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.capashi_url}/api/payments",
                json={
                    "order_id": order_id_str,
                    "amount": str(order_data.amount),
                    "callback_url": callback_url,
                    "idempotency_key": order_data.idempotency_key,
                },
                headers={
                    "X-API-Key": settings.capashi_api_key,
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
            response.raise_for_status()
            payment_data = response.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Ошибка при создании платежа в капаши: {e.response.text}",
        )
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка подключения к капаши: {str(e)}",
        )

    payment_id = str(payment_data["id"])

    # Сохраняем ордер в БД
    session_factory = get_session()
    async with session_factory() as session:
        items = []
        if order_data.item_id:
            items = [{"id": order_data.item_id, "name": f"Item {order_data.item_id}", "price": float(order_data.amount)}]
        else:
            items = [{"id": "item-1", "name": "Default Item", "price": float(order_data.amount)}]

        await create_order_in_db(
            session=session,
            order_id=order_id_uuid,
            user_id=order_data.user_id,
            payment_id=payment_id,
            items=items,
            amount=order_data.amount,
        )

    logger.info(f"Created order {order_id_str} with payment {payment_id}")

    return OrderResponse(
        order_id=order_id_str,
        payment_id=payment_id,
        amount=Decimal(str(payment_data["amount"])),
        status=OrderStatusEnum.NEW,
    )


@app.post("/callback")
async def payment_callback(callback: PaymentCallback):
    """
    Callback endpoint для получения уведомлений от капаши о статусе платежа.

    1. Находим ордер по payment_id
    2. Обновляем статус ордера (PAID или CANCELLED)
    3. Если платеж прошел - создаем событие в outbox для отправки в shipping
    """
    logger.info(f"Received payment callback: payment_id={callback.payment_id}, status={callback.status}")

    session_factory = get_session()
    async with session_factory() as session:
        # Находим ордер по payment_id
        order_id = await find_order_by_payment_id(session, callback.payment_id)

        if not order_id:
            logger.warning(f"Order not found for payment_id={callback.payment_id}")
            return {
                "status": "error",
                "message": f"Order not found for payment_id={callback.payment_id}",
            }

        # Определяем статус ордера
        if callback.status.upper() == "SUCCESS" or callback.status.upper() == "PAID":
            new_status = OrderStatusEnum.PAID
        elif callback.status.upper() == "FAILED" or callback.status.upper() == "CANCELLED":
            new_status = OrderStatusEnum.CANCELLED
        else:
            logger.warning(f"Unknown payment status: {callback.status}")
            return {
                "status": "received",
                "message": f"Unknown payment status: {callback.status}",
            }

        # Обновляем статус ордера
        await update_order_status(session, order_id, new_status)
        logger.info(f"Updated order {order_id} status to {new_status}")

        # Если платеж прошел - создаем событие в outbox для отправки в shipping
        if new_status == OrderStatusEnum.PAID:
            # Получаем текущий статус для проверки
            current_status = await get_order_status(session, order_id)

            # Создаем событие в outbox
            payload = {
                "order_id": str(order_id),
                "event_type": "order.paid",
            }
            await create_outbox_event(
                session=session,
                event_type="order.paid",
                payload=payload,
            )
            logger.info(f"Created outbox event for order {order_id}")

    return {
        "status": "received",
        "message": "Callback processed successfully",
    }


@app.on_event("startup")
async def startup_event():
    """Инициализация БД и Kafka producer при старте приложения"""
    global kafka_producer

    # Инициализируем БД
    await db.init_db(settings.database_url)
    logger.info(f"Database initialized: {settings.database_url}")

    # Инициализируем Kafka producer
    kafka_producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_brokers,
        value_serializer=lambda v: json.dumps(v).encode('utf-8') if v else None,
    )
    await kafka_producer.start()
    logger.info(f"Kafka producer started, connected to {settings.kafka_brokers}")


@app.on_event("shutdown")
async def shutdown_event():
    """Закрытие подключений при остановке приложения"""
    global kafka_producer

    # Закрываем Kafka producer
    if kafka_producer:
        await kafka_producer.stop()
        logger.info("Kafka producer stopped")

    # Закрываем подключение к БД
    await db.close_db()
    logger.info("Database connection closed")


# Глобальная переменная для передачи kafka_producer в другие модули
def get_kafka_producer() -> Optional[AIOKafkaProducer]:
    """Получить глобальный kafka producer"""
    return kafka_producer
