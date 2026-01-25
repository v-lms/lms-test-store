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
from fastapi import APIRouter, FastAPI, HTTPException
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

api_router = APIRouter(prefix="/api")

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Настройки приложения"""
    capashi_url: str = "https://capashi.dev-1.python-labs.ru"
    capashi_api_key: str = "b5brEutpPGGf6mGNqpTbFTAZPL8ILEuJ2RQf3jM7P-4"
    callback_base_url: str = "https://vladarefiev-order-service.dev-1.python-labs.ru"
    kafka_brokers: str = "kafka.kafka.svc.cluster.local:9092"
    kafka_topic_order_events: str = "student_system_order.events"
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
    user_id: str = Field(..., description="ID пользователя")
    quantity: int = Field(..., gt=0, description="Количество товара")
    item_id: str = Field(..., description="ID товара")
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


@api_router.get("/")
async def root():
    """Корневой endpoint"""
    return {
        "message": "LMS Test Store API",
        "version": "1.0.0",
        "capashi_url": settings.capashi_url,
    }


@api_router.get("/health")
async def health():
    """Health check"""
    return {"status": "ok"}


@api_router.get("/orders/{order_id}", response_model=OrderDetailResponse)
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


@api_router.post("/orders", response_model=OrderResponse, status_code=201)
async def create_order(order_data: OrderCreate):
    """
    Создать ордер и платеж в капаши.

    1. Получаем информацию о товаре из каталога капаши
    2. Вычисляем сумму: price * quantity
    3. Создаем ордер в БД со статусом NEW
    4. Создаем платеж в капаши
    5. Сохраняем payment_id в ордере
    """
    # Генерируем order_id
    order_id_uuid = uuid.uuid4()
    order_id_str = str(order_id_uuid)

    # Получаем информацию о товаре из каталога капаши
    try:
        async with httpx.AsyncClient() as client:
            catalog_response = await client.get(
                f"{settings.capashi_url}/api/catalog/items/{order_data.item_id}",
                headers={
                    "X-API-Key": settings.capashi_api_key,
                },
                timeout=30.0,
            )
            catalog_response.raise_for_status()
            item_data = catalog_response.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail=f"Item not found: {order_data.item_id}",
            )
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Ошибка при получении товара из каталога: {e.response.text}",
        )
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка подключения к капаши: {str(e)}",
        )

    available_qty = item_data.get("available_qty", 0)
    if available_qty <= 0:
        raise HTTPException(
            status_code=400,
            detail="Товар не в наличии",
        )
    if available_qty < order_data.quantity:
        raise HTTPException(
            status_code=400,
            detail=f"Товар не в наличии в нужном количестве. В наличии: {available_qty}, запрошено: {order_data.quantity}",
        )

    # Вычисляем сумму заказа
    item_price = Decimal(str(item_data["price"]))
    total_amount = item_price * Decimal(order_data.quantity)

    # Формируем callback URL
    callback_url = f"{settings.callback_base_url}/api/callback"

    # Создаем платеж в капаши
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.capashi_url}/api/payments",
                json={
                    "order_id": order_id_str,
                    "amount": str(total_amount),
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
        items = [{
            "id": order_data.item_id,
            "name": item_data["name"],
            "price": float(item_price),
            "quantity": order_data.quantity
        }]

        await create_order_in_db(
            session=session,
            order_id=order_id_uuid,
            user_id=order_data.user_id,
            payment_id=payment_id,
            items=items,
            amount=total_amount,
        )

    logger.info(f"Created order {order_id_str} with payment {payment_id}, amount={total_amount}")

    return OrderResponse(
        order_id=order_id_str,
        payment_id=payment_id,
        amount=total_amount,
        status=OrderStatusEnum.NEW,
    )


@api_router.post("/callback")
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
        # Капаши отправляет статусы из PaymentStatus enum: "pending", "processing", "succeeded", "failed"
        status_lower = callback.status.lower()
        if status_lower == "succeeded":
            new_status = OrderStatusEnum.PAID
        elif status_lower == "failed":
            new_status = OrderStatusEnum.CANCELLED
        elif status_lower in ("pending", "processing"):
            # Платеж еще обрабатывается, не меняем статус ордера
            logger.info(f"Payment {callback.payment_id} is still {status_lower}, not updating order status")
            return {
                "status": "received",
                "message": f"Payment is still {status_lower}",
            }
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
            # Получаем информацию об ордере для отправки в shipping
            order_data = await get_order_by_id(session, order_id)

            if order_data:
                # Создаем отдельное событие для каждого item в заказе
                # Формат события согласно заданию:
                # {
                #   "event_type": "order.paid",
                #   "order_id": "order-uuid",
                #   "item_id": "item-uuid",
                #   "quantity": "10",
                #   "idempotency_key": "idempotency_key-uuid"
                # }
                items = order_data.get("items", [])
                if not items:
                    logger.warning(f"No items found in order {order_id}")
                else:
                    for item in items:
                        item_id = item.get("id")
                        quantity = item.get("quantity")

                        if not item_id or quantity is None:
                            logger.warning(f"Invalid item data in order {order_id}: {item}")
                            continue

                        # Генерируем idempotency_key на основе order_id, item_id и quantity
                        # Это обеспечит идемпотентность обработки
                        idempotency_key = f"{order_id}-{item_id}-{quantity}"

                        # Создаем событие в формате согласно заданию
                        # В задании указано quantity как строка, но в модели OrderPaidEvent это int
                        # Pydantic автоматически преобразует, но лучше отправить как int
                        event_payload = {
                            "event_type": "order.paid",
                            "order_id": str(order_id),
                            "item_id": str(item_id),
                            "quantity": int(quantity),  # Преобразуем в int для соответствия модели
                            "idempotency_key": idempotency_key,
                        }

                        await create_outbox_event(
                            session=session,
                            event_type="order.paid",
                            payload=event_payload,
                        )
                        logger.info(
                            f"Created outbox event for order {order_id}, item {item_id}, quantity {quantity}"
                        )

    return {
        "status": "received",
        "message": "Callback processed successfully",
    }


app.include_router(api_router)


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
