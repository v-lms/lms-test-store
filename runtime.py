"""
Runtime для запуска API сервера и consumer
"""
import asyncio
import logging
import signal
import sys

import uvicorn

from app import app
from consumer import process_shipment_events

logger = logging.getLogger(__name__)


def signal_handler(sig, frame):
    """Обработчик сигналов для graceful shutdown"""
    logger.info(f"Received signal {sig}, initiating shutdown...")


async def run_uvicorn():
    """Запуск uvicorn сервера"""
    config = uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )
    server = uvicorn.Server(config)

    try:
        await server.serve()
    except asyncio.CancelledError:
        logger.info("Uvicorn server cancelled")
        raise
    except Exception as e:
        logger.error(f"Uvicorn server error: {e}", exc_info=True)
        raise


async def run_shipment_consumer():
    """Запуск консумера shipping событий"""
    try:
        await process_shipment_events()
    except asyncio.CancelledError:
        logger.info("Shipment consumer cancelled")
        raise
    except Exception as e:
        logger.error(f"Shipment consumer error: {e}", exc_info=True)
        raise


async def main():
    """Главная функция для запуска всех сервисов"""
    # Настраиваем обработчики сигналов
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info("Starting all services...")

    # Создаем задачи для всех сервисов
    tasks = [
        asyncio.create_task(run_uvicorn(), name="uvicorn"),
        asyncio.create_task(run_shipment_consumer(), name="shipment_consumer"),
    ]

    try:
        # Запускаем все сервисы параллельно
        await asyncio.gather(*tasks, return_exceptions=False)
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
        # Отменяем все задачи
        for task in tasks:
            if not task.done():
                task.cancel()
        # Ждем завершения всех задач
        await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
        logger.error(f"Error in main: {e}", exc_info=True)
        # Отменяем все задачи
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        sys.exit(1)
    finally:
        logger.info("All services stopped")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    asyncio.run(main())

