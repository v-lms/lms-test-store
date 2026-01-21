"""
Kafka consumer для получения сообщений от shipping service
"""
import asyncio
import json
import logging
from uuid import UUID

from aiokafka import AIOKafkaConsumer
from pydantic_settings import BaseSettings

from db import OrderStatusEnum, get_session
from repositories import update_order_status

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Настройки consumer"""
    kafka_brokers: str = "kafka.kafka.svc.cluster.local:9092"
    kafka_group_id: str = "test-store-consumer-group"
    kafka_topic_shipping_events: str = "student_system_shipment.events"

    class Config:
        env_file = ".env"
        case_sensitive = False


async def process_shipment_events():
    """Основной цикл обработки событий shipping из Kafka"""
    settings = Settings()

    logger.info("Starting shipment events consumer...")

    # Небольшая задержка для того, чтобы Kafka успел запуститься
    await asyncio.sleep(5)

    # Создаем Kafka consumer
    consumer = AIOKafkaConsumer(
        settings.kafka_topic_shipping_events,
        bootstrap_servers=settings.kafka_brokers,
        group_id=settings.kafka_group_id,
        value_deserializer=lambda m: json.loads(m.decode('utf-8')) if m else None,
        auto_offset_reset="earliest",
        enable_auto_commit=True,
    )

    try:
        # Запускаем consumer
        await consumer.start()
        logger.info(f"Connected to Kafka at {settings.kafka_brokers}")
        logger.info(f"Listening for shipping events on topic: {settings.kafka_topic_shipping_events}")

        # Обрабатываем сообщения
        async for message in consumer:
            try:
                if message.value is None:
                    continue

                event_data = message.value
                event_type = event_data.get("event_type", "")
                order_id_str = event_data.get("order_id", "")

                logger.info(f"Received event: {event_type} for order {order_id_str}")

                if not order_id_str:
                    logger.warning(f"No order_id in event: {event_data}")
                    continue

                # Определяем новый статус в зависимости от типа события
                if event_type == "order.shipped":
                    new_status = OrderStatusEnum.SHIPPED
                elif event_type == "order.cancelled":
                    new_status = OrderStatusEnum.CANCELLED
                else:
                    logger.warning(f"Unknown event type: {event_type}")
                    continue

                # Обновляем статус ордера
                try:
                    order_id = UUID(order_id_str)
                except ValueError:
                    logger.error(f"Invalid order_id format: {order_id_str}")
                    continue

                session_factory = get_session()
                async with session_factory() as session:
                    await update_order_status(session, order_id, new_status)
                    logger.info(f"Updated order {order_id_str} status to {new_status}")

            except Exception as e:
                logger.error(f"Error processing message: {e}", exc_info=True)
                # Продолжаем обработку следующих сообщений
                continue

    except Exception as e:
        logger.error(f"Error in shipment events consumer: {e}", exc_info=True)
    finally:
        # Закрываем соединение
        await consumer.stop()
        logger.info("Shipment consumer stopped")
