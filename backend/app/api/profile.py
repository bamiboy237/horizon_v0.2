"""Profile read and update endpoints for authenticated Horizon users."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.exceptions import MissingProfileError
from app.core.security import get_current_user
from app.models.schemas import AuthenticatedUser, ProfileResponse, ProfileUpdateRequest
from app.services.profile import get_profile_by_user_id, update_profile_by_user_id

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("", response_model=ProfileResponse)
async def read_profile(
	auth_user: AuthenticatedUser = Depends(get_current_user),
) -> ProfileResponse:
	profile = await get_profile_by_user_id(auth_user.user_id)

	if profile is None:
		raise MissingProfileError()

	return ProfileResponse.model_validate(profile)


@router.put("", response_model=ProfileResponse)
@router.patch("", response_model=ProfileResponse)
async def update_profile(
	payload: ProfileUpdateRequest,
	auth_user: AuthenticatedUser = Depends(get_current_user),
) -> ProfileResponse:
	patch = payload.model_dump(exclude_unset=True)
	profile = await update_profile_by_user_id(auth_user.user_id, patch)

	if profile is None:
		raise MissingProfileError()

	return ProfileResponse.model_validate(profile)
