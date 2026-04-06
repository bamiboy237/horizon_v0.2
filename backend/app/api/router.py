"""Central API router that aggregates all Horizon endpoint modules."""

from fastapi import APIRouter

from app.api import auth, chat, email, health, profile, research, search

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(profile.router)
api_router.include_router(chat.router)
api_router.include_router(search.router)
api_router.include_router(research.router)
api_router.include_router(email.router)
