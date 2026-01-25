# LMS Test Store

Простое тестовое приложение для интеграции с капаши.

## Описание

Это приложение предоставляет API для тестирования интеграции с капаши:
- Создание ордеров (с автоматическим созданием платежа в капаши)
- Создание платежей напрямую
- Получение callback от капаши о статусе платежа
- Создание shipment (отправка order.paid в Kafka)
- Consumer для получения событий shipping (order.shipped, order.cancelled)

## Установка

1. Создайте виртуальное окружение:
```bash
python3 -m venv venv
source venv/bin/activate  # на Windows: venv\Scripts\activate
```

2. Установите зависимости:
```bash
pip install -r requirements.txt
```

3. Создайте файл `.env` на основе `.env.example`:
```bash
cp .env.example .env
```

4. Отредактируйте `.env` и укажите:
   - `CAPASHI_URL` - URL капаши API (по умолчанию `http://localhost:8000`)
   - `CAPASHI_API_KEY` - API ключ для аутентификации в капаши
   - `CALLBACK_BASE_URL` - базовый URL для callback (должен быть доступен из капаши)
   - `KAFKA_BROKERS` - адрес Kafka брокера (по умолчанию `localhost:29092`)
   - `KAFKA_GROUP_ID` - ID группы consumer (по умолчанию `test-store-consumer-group`)

## Запуск

### Локальный запуск

**Запуск API и consumer вместе (рекомендуется):**
```bash
python runtime.py
```

Это запустит и API сервер, и consumer в одном процессе.

**Или отдельно:**

API сервер:
```bash
python app.py
```

Или через uvicorn:
```bash
uvicorn app:app --host 0.0.0.0 --port 8080 --reload
```

Consumer для событий shipping (в отдельном терминале):
```bash
python consumer.py
```

Consumer будет слушать топики `order.shipped` и `order.cancelled` и выводить полученные сообщения в консоль.

### Запуск через Docker

1. Соберите образ:
```bash
docker build -t lms-test-store .
```

2. Запустите контейнер:
```bash
docker run -d \
  --name lms-test-store \
  -p 8080:8080 \
  -e CAPASHI_URL=http://localhost:8000 \
  -e CAPASHI_API_KEY=your-api-key-here \
  -e CALLBACK_BASE_URL=http://localhost:8080 \
  lms-test-store
```

Или используйте файл `.env`:
```bash
docker run -d \
  --name lms-test-store \
  -p 8080:8080 \
  --env-file .env \
  lms-test-store
```

Приложение будет доступно по адресу `http://localhost:8080`

## API Endpoints

Все API endpoints имеют префикс `/api`.

### GET /api/

Корневой endpoint — информация о приложении.

### GET /api/health

Health check.

### GET /api/orders/{order_id}

Получить информацию об ордере по ID.

### POST /api/orders

Создает ордер и автоматически создает платеж в капаши.

**Тело запроса:**
```json
{
  "amount": "100.50",
  "idempotency_key": "unique-key-123"  // опционально
}
```

**Ответ:**
```json
{
  "order_id": "order-abc123",
  "payment_id": "uuid",
  "amount": "100.50",
  "status": "pending",
  "created_at": "2024-01-01T00:00:00Z"
}
```

### POST /payments

Создает платеж в капаши напрямую.

**Тело запроса:**
```json
{
  "order_id": "order-123",
  "amount": "100.50",
  "idempotency_key": "unique-key-123"  // опционально
}
```

**Ответ:**
```json
{
  "id": "uuid",
  "user_id": "uuid",
  "order_id": "order-123",
  "amount": "100.50",
  "status": "pending",
  "created_at": "2024-01-01T00:00:00Z"
}
```

### POST /api/callback

Endpoint для получения callback от капаши о статусе платежа.

**Тело запроса (от капаши):**
```json
{
  "payment_id": "uuid",
  "order_id": "order-123",
  "status": "succeeded",
  "amount": "100.50",
  "error_message": null,
  "processed_at": "2024-01-01T00:01:00Z"
}
```

**Ответ:**
```json
{
  "status": "received",
  "message": "Callback processed successfully"
}
```

### POST /shipments

Создает shipment - отправляет событие `order.paid` в Kafka для обработки shipping service.

**Тело запроса:**
```json
{
  "order_id": "order-123",
  "item_id": "550e8400-e29b-41d4-a716-446655440000",
  "quantity": 5,
  "idempotency_key": "unique-key-123",
  "event_type": "order.paid"
}
```

**Ответ:**
```json
{
  "message": "Shipment event sent to Kafka",
  "order_id": "order-123",
  "topic": "order.paid"
}
```

## Примеры использования

### Создание ордера

```bash
curl -X POST http://localhost:8080/api/orders \
  -H "Content-Type: application/json" \
  -d '{
    "amount": "100.50",
    "idempotency_key": "test-order-1"
  }'
```

### Создание платежа

```bash
curl -X POST http://localhost:8080/payments \
  -H "Content-Type: application/json" \
  -d '{
    "order_id": "order-123",
    "amount": "100.50"
  }'
```

### Создание shipment

```bash
curl -X POST http://localhost:8080/shipments \
  -H "Content-Type: application/json" \
  -d '{
    "order_id": "order-123",
    "item_id": "550e8400-e29b-41d4-a716-446655440000",
    "quantity": 5,
    "idempotency_key": "unique-key-123"
  }'
```

После отправки shipment, shipping service обработает событие и отправит ответ в топики `order.shipped` или `order.cancelled`, которые можно увидеть в consumer.

## Документация API

После запуска приложения документация доступна по адресам:
- Swagger UI: `http://localhost:8080/docs`
- ReDoc: `http://localhost:8080/redoc`

