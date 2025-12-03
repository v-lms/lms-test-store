"""
Простое приложение для тестирования интеграции с капаши
"""
import asyncio
import json
import logging
import os
import uuid
from decimal import Decimal
from typing import Optional

import httpx
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, HttpUrl, Field
from pydantic_settings import BaseSettings

app = FastAPI(
    title="LMS Test Store",
    description="Тестовое приложение для интеграции с капаши",
    version="1.0.0",
)


class Settings(BaseSettings):
    """Настройки приложения"""
    capashi_url: str = "http://capashi-c223baff-4c9b-4bb1-bc6b-474ae9b90a59-web.capashi-c223baff-4c9b-4bb1-bc6b-474ae9b90a59.svc:8000"
    capashi_api_key: str = "b5brEutpPGGf6mGNqpTbFTAZPL8ILEuJ2RQf3jM7P-4"
    callback_base_url: str = "http://order-service-707e52c1-1f84-4687-b3e6-9b0a54c49fb9-web.order-service-707e52c1-1f84-4687-b3e6-9b0a54c49fb9.svc:8000"
    kafka_brokers: str = "kafka.kafka.svc.cluster.local:9092"
    kafka_topic_order_events: str = "student_vladarefiev_order.events"

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
logger = logging.getLogger(__name__)

# Global Kafka producer
kafka_producer: Optional[AIOKafkaProducer] = None


# Schemas
class OrderCreate(BaseModel):
    """Создание ордера"""
    amount: Decimal = Field(..., gt=0, description="Сумма платежа")
    item_id: Optional[str] = Field(None, description="ID товара (опционально)")
    idempotency_key: Optional[str] = Field(None, description="Ключ идемпотентности")


class OrderResponse(BaseModel):
    """Ответ при создании ордера"""
    order_id: str
    payment_id: str
    amount: Decimal
    status: str
    created_at: str


class PaymentCreate(BaseModel):
    """Создание платежа"""
    order_id: str = Field(..., description="ID заказа")
    amount: Decimal = Field(..., gt=0, description="Сумма платежа")
    idempotency_key: Optional[str] = Field(None, description="Ключ идемпотентности")


class PaymentResponse(BaseModel):
    """Ответ при создании платежа"""
    id: str
    user_id: str
    order_id: str
    amount: Decimal
    status: str
    created_at: str


class PaymentCallback(BaseModel):
    """Callback от капаши"""
    payment_id: str
    order_id: str
    status: str
    amount: Decimal
    error_message: Optional[str] = None
    processed_at: str


class ShipmentCreate(BaseModel):
    """Создание shipment - отправка order.paid в Kafka"""
    order_id: str = Field(..., description="ID заказа")
    item_id: str = Field(..., description="ID товара")
    quantity: int = Field(..., gt=0, description="Количество товара")
    idempotency_key: str = Field(..., description="Ключ идемпотентности")
    event_type: str = Field(default="order.paid", description="Тип события")


class ShipmentResponse(BaseModel):
    """Ответ при создании shipment"""
    message: str
    order_id: str
    topic: str


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


@app.post("/orders", response_model=OrderResponse, status_code=201)
async def create_order(order_data: OrderCreate):
    """
    Создать ордер и платеж в капаши.

    Генерирует order_id и создает платеж в капаши.
    """
    # Генерируем order_id
    order_id = f"order-{uuid.uuid4().hex[:12]}"

    # Формируем callback URL
    callback_url = f"{settings.callback_base_url}/callback"

    # Создаем платеж в капаши
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.capashi_url}/api/payments",
                json={
                    "order_id": order_id,
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

    return OrderResponse(
        order_id=order_id,
        payment_id=str(payment_data["id"]),
        amount=Decimal(str(payment_data["amount"])),
        status=payment_data["status"],
        created_at=payment_data["created_at"],
    )


@app.post("/payments", response_model=PaymentResponse, status_code=201)
async def create_payment(payment_data: PaymentCreate):
    """
    Создать платеж в капаши.

    Прямой вызов API капаши для создания платежа.
    """
    # Формируем callback URL
    callback_url = f"{settings.callback_base_url}/callback"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.capashi_url}/api/payments",
                json={
                    "order_id": payment_data.order_id,
                    "amount": str(payment_data.amount),
                    "callback_url": callback_url,
                    "idempotency_key": payment_data.idempotency_key,
                },
                headers={
                    "X-API-Key": settings.capashi_api_key,
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
            response.raise_for_status()
            result = response.json()
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

    return PaymentResponse(
        id=str(result["id"]),
        user_id=str(result["user_id"]),
        order_id=result["order_id"],
        amount=Decimal(str(result["amount"])),
        status=result["status"],
        created_at=result["created_at"],
    )


@app.post("/callback")
async def payment_callback(callback: PaymentCallback):
    """
    Callback endpoint для получения уведомлений от капаши о статусе платежа.

    Этот endpoint вызывается капаши после обработки платежа.
    """
    # Здесь можно добавить логику обработки callback
    # Например, обновление статуса заказа в БД, отправка уведомлений и т.д.

    print(f"Received payment callback:")
    print(f"  Payment ID: {callback.payment_id}")
    print(f"  Order ID: {callback.order_id}")
    print(f"  Status: {callback.status}")
    print(f"  Amount: {callback.amount}")
    if callback.error_message:
        print(f"  Error: {callback.error_message}")
    print(f"  Processed at: {callback.processed_at}")

    # Возвращаем успешный ответ
    return {
        "status": "received",
        "message": "Callback processed successfully",
    }


@app.on_event("startup")
async def startup_event():
    """Инициализация Kafka producer при старте приложения"""
    global kafka_producer
    kafka_producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_brokers,
        value_serializer=lambda v: json.dumps(v).encode('utf-8') if v else None,
    )
    await kafka_producer.start()
    logger.info(f"Kafka producer started, connected to {settings.kafka_brokers}")


@app.on_event("shutdown")
async def shutdown_event():
    """Закрытие Kafka producer при остановке приложения"""
    global kafka_producer
    if kafka_producer:
        await kafka_producer.stop()
        logger.info("Kafka producer stopped")


@app.post("/shipments", response_model=ShipmentResponse, status_code=201)
async def create_shipment(shipment_data: ShipmentCreate):
    """
    Создать shipment - отправить событие order.paid в Kafka.

    Это событие будет обработано shipping service в lms-capashi.
    """
    global kafka_producer

    if not kafka_producer:
        raise HTTPException(
            status_code=500,
            detail="Kafka producer not initialized",
        )

    # Формируем сообщение для Kafka
    message = {
        "order_id": shipment_data.order_id,
        "item_id": shipment_data.item_id,
        "quantity": shipment_data.quantity,
        "idempotency_key": shipment_data.idempotency_key,
        "event_type": shipment_data.event_type,
    }

    try:
        # Отправляем сообщение в топик order.paid
        await kafka_producer.send(
            topic=settings.kafka_topic_order_events,
            value=message,
            key=shipment_data.order_id.encode('utf-8'),
        )
        await kafka_producer.flush()

        logger.info(f"Sent order.paid event to Kafka: order_id={shipment_data.order_id}")

        return ShipmentResponse(
            message="Shipment event sent to Kafka",
            order_id=shipment_data.order_id,
            topic="order.paid",
        )
    except Exception as e:
        logger.error(f"Error sending message to Kafka: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка при отправке сообщения в Kafka: {str(e)}",
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)

