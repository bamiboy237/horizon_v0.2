"""Research session streaming and control endpoints."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/research", tags=["research"])
