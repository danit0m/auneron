from fastapi import APIRouter
from fastapi import Response
from fastapi import status

from app.core.config import settings
from app.database.database import (
    check_database_connection,
)


router = APIRouter(
    tags=["Health"],
)


@router.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "api",
        "version": settings.app_version,
    }


@router.get("/ready")
def ready(
    response: Response,
):
    database_online = (
        check_database_connection()
    )

    if not database_online:
        response.status_code = (
            status.HTTP_503_SERVICE_UNAVAILABLE
        )

        return {
            "status": "not_ready",
            "database": "offline",
        }

    return {
        "status": "ready",
        "database": "online",
    }
