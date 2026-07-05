from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from app.database import DbSession
from app.models.athlete_checkin import AthleteCheckin
from app.schemas.athlete_checkin import AthleteCheckinCreate, AthleteCheckinRead
from app.services import ApiKeyDep, user_service

router = APIRouter()


@router.post(
    "/users/{user_id}/athlete-checkins",
    response_model=AthleteCheckinRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an athlete readiness check-in",
)
def create_athlete_checkin(
    user_id: UUID,
    payload: AthleteCheckinCreate,
    db: DbSession,
    _api_key: ApiKeyDep,
) -> AthleteCheckin:
    user_service.get(db, user_id, raise_404=True)

    checkin = AthleteCheckin(
        id=uuid4(),
        user_id=user_id,
        checkin_type=payload.checkin_type.value,
        event_type=payload.event_type,
        event_name=payload.event_name,
        stress_rating=payload.stress_rating,
        energy_rating=payload.energy_rating,
        focus_rating=payload.focus_rating,
        confidence_rating=payload.confidence_rating,
        soreness_rating=payload.soreness_rating,
    )

    db.add(checkin)
    db.commit()
    db.refresh(checkin)

    return checkin


@router.get(
    "/users/{user_id}/athlete-checkins",
    response_model=list[AthleteCheckinRead],
    summary="List athlete readiness check-ins",
)
def list_athlete_checkins(
    user_id: UUID,
    db: DbSession,
    _api_key: ApiKeyDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AthleteCheckin]:
    user_service.get(db, user_id, raise_404=True)

    statement = (
        select(AthleteCheckin)
        .where(AthleteCheckin.user_id == user_id)
        .order_by(AthleteCheckin.created_at.desc())
        .offset(offset)
        .limit(limit)
    )

    return list(db.scalars(statement).all())
