"""
Kafka consumer для получения сообщений от shipping service
"""
import asyncio
import json
import logging

from aiokafka import AIOKafkaConsumer
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Настройки consumer"""
    kafka_brokers: str = "kafka.kafka.svc.cluster.local:9092"
    kafka_group_id: str = "test-store-consumer-group"
    kafka_topic_shipping_events: str = "student_vladarefiev_shipment.events"

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
        logger.info("Listening for order.shipped and order.cancelled events...")

        # Обрабатываем сообщения
        async for message in consumer:
            try:
                if message.value is None:
                    continue

                # Просто принтим сообщение
                print(f"\n{'='*60}")
                print(f"Received {message.topic} event:")
                print(f"{'='*60}")
                print(json.dumps(message.value, indent=2, ensure_ascii=False))
                print(f"{'='*60}\n")

                logger.info(f"Received {message.topic} event for order {message.value.get('order_id', 'unknown')}")

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

