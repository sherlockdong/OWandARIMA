"""Tests for athlete readiness check-in endpoints."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AthleteCheckin, User


class TestCreateAthleteCheckin:
    """Tests for POST /api/v1/users/{user_id}/athlete-checkins."""

    def test_create_athlete_checkin_success(
        self,
        client: TestClient,
        db: Session,
        api_v1_prefix: str,
        api_key_header: dict[str, str],
        user: User,
    ) -> None:
        payload = {
            "checkin_type": "pre_event",
            "event_type": "game",
            "event_name": "League final",
            "stress_rating": 7,
            "energy_rating": 8,
            "focus_rating": 9,
            "confidence_rating": 8,
            "soreness_rating": 3,
        }

        response = client.post(
            f"{api_v1_prefix}/users/{user.id}/athlete-checkins",
            json=payload,
            headers=api_key_header,
        )

        assert response.status_code == 201

        data = response.json()
        assert data["user_id"] == str(user.id)
        assert data["checkin_type"] == "pre_event"
        assert data["event_type"] == "game"
        assert data["event_name"] == "League final"
        assert data["stress_rating"] == 7
        assert data["energy_rating"] == 8
        assert data["focus_rating"] == 9
        assert data["confidence_rating"] == 8
        assert data["soreness_rating"] == 3
        assert "id" in data
        assert "created_at" in data

        checkin = db.scalar(
            select(AthleteCheckin).where(
                AthleteCheckin.id == data["id"],
            )
        )

        assert checkin is not None
        assert checkin.user_id == user.id
        assert checkin.event_name == "League final"

    def test_create_athlete_checkin_unauthorized(
        self,
        client: TestClient,
        api_v1_prefix: str,
        user: User,
    ) -> None:
        response = client.post(
            f"{api_v1_prefix}/users/{user.id}/athlete-checkins",
            json={
                "checkin_type": "daily",
                "event_type": "daily",
                "stress_rating": 5,
                "energy_rating": 5,
                "focus_rating": 5,
                "confidence_rating": 5,
                "soreness_rating": 5,
            },
        )

        assert response.status_code == 401

    def test_create_athlete_checkin_user_not_found(
        self,
        client: TestClient,
        api_v1_prefix: str,
        api_key_header: dict[str, str],
    ) -> None:
        response = client.post(
            f"{api_v1_prefix}/users/{uuid4()}/athlete-checkins",
            json={
                "checkin_type": "daily",
                "event_type": "daily",
                "stress_rating": 5,
                "energy_rating": 5,
                "focus_rating": 5,
                "confidence_rating": 5,
                "soreness_rating": 5,
            },
            headers=api_key_header,
        )

        assert response.status_code == 404


class TestListAthleteCheckins:
    """Tests for GET /api/v1/users/{user_id}/athlete-checkins."""

    def test_list_athlete_checkins_newest_first(
        self,
        client: TestClient,
        db: Session,
        api_v1_prefix: str,
        api_key_header: dict[str, str],
        user: User,
    ) -> None:
        base_time = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)

        older = self._create_checkin(
            db,
            user,
            event_name="Older check-in",
            created_at=base_time,
        )
        newer = self._create_checkin(
            db,
            user,
            event_name="Newer check-in",
            created_at=base_time + timedelta(hours=1),
        )

        response = client.get(
            f"{api_v1_prefix}/users/{user.id}/athlete-checkins",
            headers=api_key_header,
        )

        assert response.status_code == 200

        data = response.json()
        assert [item["id"] for item in data] == [
            str(newer.id),
            str(older.id),
        ]

    def test_list_athlete_checkins_applies_limit_and_offset(
        self,
        client: TestClient,
        db: Session,
        api_v1_prefix: str,
        api_key_header: dict[str, str],
        user: User,
    ) -> None:
        base_time = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)

        oldest = self._create_checkin(
            db,
            user,
            event_name="Oldest",
            created_at=base_time,
        )
        middle = self._create_checkin(
            db,
            user,
            event_name="Middle",
            created_at=base_time + timedelta(hours=1),
        )
        self._create_checkin(
            db,
            user,
            event_name="Newest",
            created_at=base_time + timedelta(hours=2),
        )

        response = client.get(
            (f"{api_v1_prefix}/users/{user.id}/athlete-checkins?limit=2&offset=1"),
            headers=api_key_header,
        )

        assert response.status_code == 200

        data = response.json()
        assert [item["id"] for item in data] == [
            str(middle.id),
            str(oldest.id),
        ]

    def test_list_athlete_checkins_does_not_return_other_users_records(
        self,
        client: TestClient,
        db: Session,
        api_v1_prefix: str,
        api_key_header: dict[str, str],
        user: User,
    ) -> None:
        other_user = User(id=uuid4())
        db.add(other_user)
        db.flush()

        expected = self._create_checkin(
            db,
            user,
            event_name="Expected",
            created_at=datetime(2026, 7, 1, tzinfo=UTC),
        )
        self._create_checkin(
            db,
            other_user,
            event_name="Other user",
            created_at=datetime(2026, 7, 2, tzinfo=UTC),
        )

        response = client.get(
            f"{api_v1_prefix}/users/{user.id}/athlete-checkins",
            headers=api_key_header,
        )

        assert response.status_code == 200
        assert [item["id"] for item in response.json()] == [str(expected.id)]

    @staticmethod
    def _create_checkin(
        db: Session,
        user: User,
        *,
        event_name: str,
        created_at: datetime,
    ) -> AthleteCheckin:
        checkin = AthleteCheckin(
            id=uuid4(),
            user_id=user.id,
            checkin_type="pre_event",
            event_type="game",
            event_name=event_name,
            stress_rating=5,
            energy_rating=6,
            focus_rating=7,
            confidence_rating=8,
            soreness_rating=2,
            created_at=created_at,
        )
        db.add(checkin)
        db.commit()
        db.refresh(checkin)
        return checkin
