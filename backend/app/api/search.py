"""Opportunity search and detail endpoints backed by Postgres queries."""

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["search"])
