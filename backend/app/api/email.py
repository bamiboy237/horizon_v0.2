"""Email preference and delivery endpoints for Horizon users."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/email", tags=["email"])
