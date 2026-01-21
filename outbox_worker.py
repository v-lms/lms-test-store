"""
Outbox worker для отправки событий из outbox в Kafka
"""
import asyncio
import json
import logging

from db import get_session
from repositories import get_pending_outbox_events, mark_outbox_event_as_sent

logger = logging.getLogger(__name__)


async def process_outbox_events():
    """Обработка pending событий из outbox и отправка в Kafka"""
    # Импортируем здесь, чтобы избежать циклического импорта
    from app import get_kafka_producer, settings

    kafka_producer = get_kafka_producer()

    if not kafka_producer:
        logger.warning("Kafka producer not initialized, skipping outbox processing")
        return

    session_factory = get_session()
    async with session_factory() as session:
        # Получаем pending события
        events = await get_pending_outbox_events(session, limit=100)

        if not events:
            return

        logger.info(f"Processing {len(events)} outbox events")

        # Отправляем каждое событие в Kafka
        for event in events:
            try:
                # Отправляем событие в Kafka
                # Согласно заданию, событие должно быть в формате:
                # {
                #   "event_type": "order.paid",
                #   "order_id": "order-uuid",
                #   "item_id": "item-uuid",
                #   "quantity": "10",
                #   "idempotency_key": "idempotency_key-uuid"
                # }
                # Payload уже содержит правильный формат, отправляем его напрямую
                message = event.payload

                order_id = event.payload.get("order_id", "")

                await kafka_producer.send_and_wait(
                    topic=settings.kafka_topic_order_events,
                    value=message,
                    key=order_id.encode('utf-8') if order_id else None,
                )

                logger.info(f"Sent outbox event {event.id} to Kafka: {event.event_type}")

                # Помечаем событие как отправленное
                async with session_factory() as session_mark:
                    await mark_outbox_event_as_sent(session_mark, event.id)
                    logger.info(f"Marked outbox event {event.id} as sent")

            except Exception as e:
                logger.error(f"Error processing outbox event {event.id}: {e}", exc_info=True)
                # Продолжаем обработку следующих событий
                continue


async def run_outbox_worker():
    """Основной цикл outbox worker"""
    logger.info("Starting outbox worker...")

    while True:
        try:
            await process_outbox_events()
        except Exception as e:
            logger.error(f"Error in outbox worker: {e}", exc_info=True)

        # Небольшая задержка перед следующей итерацией
        await asyncio.sleep(1)
