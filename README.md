# LMS Test Store

Простое тестовое приложение для интеграции с капаши.

## Описание

Это приложение предоставляет API для тестирования интеграции с капаши:
- Создание ордеров (с автоматическим созданием платежа в капаши)
- Создание платежей напрямую
- Получение callback от капаши о статусе платежа

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

## Запуск

### Локальный запуск

```bash
python app.py
```

Или через uvicorn:
```bash
uvicorn app:app --host 0.0.0.0 --port 8080 --reload
```

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

### POST /orders

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

### POST /callback

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

## Примеры использования

### Создание ордера

```bash
curl -X POST http://localhost:8080/orders \
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

## Документация API

После запуска приложения документация доступна по адресам:
- Swagger UI: `http://localhost:8080/docs`
- ReDoc: `http://localhost:8080/redoc`

