"""
Простые репозитории для работы с БД
"""
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from db import (
    OrderStatusEnum,
    OutboxStatusEnum,
    order_statuses_tbl,
    orders_tbl,
    outbox_tbl,
)


async def create_order(
    session: AsyncSession,
    order_id: UUID,
    user_id: str,
    payment_id: str,
    items: list[dict],
    amount: Decimal,
) -> UUID:
    """Создать ордер в БД"""
    # Создаем ордер
    stmt_order = (
        insert(orders_tbl)
        .values(
            id=order_id,
            user_id=user_id,
            payment_id=payment_id,
            items=items,
            amount=amount,
        )
        .returning(orders_tbl.c.id)
    )
    result = await session.execute(stmt_order)
    order_id = result.scalar_one()

    # Создаем статус NEW
    stmt_status = insert(order_statuses_tbl).values(
        order_id=order_id,
        status=OrderStatusEnum.NEW,
    )
    await session.execute(stmt_status)

    await session.commit()
    return order_id


async def update_order_status(
    session: AsyncSession,
    order_id: UUID,
    status: OrderStatusEnum,
) -> None:
    """Обновить статус ордера"""
    # Добавляем новый статус в историю
    stmt = insert(order_statuses_tbl).values(
        order_id=order_id,
        status=status,
    )
    await session.execute(stmt)
    await session.commit()


async def get_order_status(
    session: AsyncSession,
    order_id: UUID,
) -> Optional[str]:
    """Получить текущий статус ордера"""
    # Получаем последний статус
    latest_status_subq = (
        select(
            order_statuses_tbl.c.order_id,
            order_statuses_tbl.c.status,
            func.row_number()
            .over(
                partition_by=order_statuses_tbl.c.order_id,
                order_by=order_statuses_tbl.c.created_at.desc(),
            )
            .label("rn"),
        )
        .subquery()
    )

    stmt = (
        select(latest_status_subq.c.status)
        .where(
            and_(
                latest_status_subq.c.order_id == order_id,
                latest_status_subq.c.rn == 1,
            )
        )
    )
    result = await session.execute(stmt)
    status = result.scalar_one_or_none()
    return status


async def find_order_by_payment_id(
    session: AsyncSession,
    payment_id: str,
) -> Optional[UUID]:
    """Найти ордер по payment_id"""
    stmt = select(orders_tbl.c.id).where(orders_tbl.c.payment_id == payment_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_order_by_id(
    session: AsyncSession,
    order_id: UUID,
) -> Optional[dict]:
    """Получить полную информацию об ордере по ID"""
    # Получаем последний статус
    latest_status_subq = (
        select(
            order_statuses_tbl.c.order_id,
            order_statuses_tbl.c.status,
            func.row_number()
            .over(
                partition_by=order_statuses_tbl.c.order_id,
                order_by=order_statuses_tbl.c.created_at.desc(),
            )
            .label("rn"),
        )
        .subquery()
    )

    # Получаем ордер с текущим статусом
    stmt = (
        select(
            orders_tbl.c.id,
            orders_tbl.c.user_id,
            orders_tbl.c.payment_id,
            orders_tbl.c.items,
            orders_tbl.c.amount,
            orders_tbl.c.created_at,
            latest_status_subq.c.status.label("current_status"),
        )
        .select_from(orders_tbl)
        .outerjoin(
            latest_status_subq,
            and_(
                orders_tbl.c.id == latest_status_subq.c.order_id,
                latest_status_subq.c.rn == 1,
            ),
        )
        .where(orders_tbl.c.id == order_id)
    )
    
    result = await session.execute(stmt)
    row = result.fetchone()
    
    if row is None:
        return None
    
    return {
        "id": str(row.id),
        "user_id": row.user_id,
        "payment_id": row.payment_id,
        "items": row.items,
        "amount": row.amount,
        "status": row.current_status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def create_outbox_event(
    session: AsyncSession,
    event_type: str,
    payload: dict,
) -> None:
    """Создать событие в outbox"""
    stmt = insert(outbox_tbl).values(
        event_type=event_type,
        payload=payload,
        status=OutboxStatusEnum.PENDING,
    )
    await session.execute(stmt)
    await session.commit()


async def get_pending_outbox_events(session: AsyncSession, limit: int = 100):
    """Получить pending события из outbox"""
    stmt = (
        select(outbox_tbl)
        .where(outbox_tbl.c.status == OutboxStatusEnum.PENDING)
        .order_by(outbox_tbl.c.created_at)
        .limit(limit)
    )
    result = await session.execute(stmt)
    return result.fetchall()


async def mark_outbox_event_as_sent(session: AsyncSession, event_id: UUID) -> None:
    """Пометить событие как отправленное"""
    stmt = (
        update(outbox_tbl)
        .where(outbox_tbl.c.id == event_id)
        .values(status=OutboxStatusEnum.SENT)
    )
    await session.execute(stmt)
    await session.commit()
