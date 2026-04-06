"""Chat streaming endpoints for the PydanticAI conversation workflow."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/chat", tags=["chat"])
