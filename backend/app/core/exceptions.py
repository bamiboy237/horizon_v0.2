"""Domain exceptions and HTTP error mapping for the Horizon backend."""

from fastapi import HTTPException


class HorizonError(Exception):
    """Base application exception for domain-specific failures."""


class HorizonHTTPError(HTTPException, HorizonError):
    """Domain error that maps directly to an HTTP response."""

    def __init__(self, status_code: int, detail: str) -> None:
        HTTPException.__init__(self, status_code=status_code, detail=detail)
        HorizonError.__init__(self, detail)


class AuthConfigurationError(HorizonHTTPError):
    def __init__(self, detail: str = "Auth is not configured on the backend.") -> None:
        super().__init__(500, detail)


class MissingAuthTokenError(HorizonHTTPError):
    def __init__(self, detail: str = "Missing bearer token.") -> None:
        super().__init__(401, detail)


class InvalidAuthTokenError(HorizonHTTPError):
    def __init__(self, detail: str = "Invalid Clerk token.") -> None:
        super().__init__(401, detail)


class MissingProfileError(HorizonHTTPError):
    def __init__(self, detail: str = "Authenticated user profile was not found.") -> None:
        super().__init__(404, detail)


class IncompleteOnboardingError(HorizonHTTPError):
    def __init__(self, detail: str = "Complete onboarding before accessing this resource.") -> None:
        super().__init__(403, detail)


class IncompleteProfileError(HorizonHTTPError):
    def __init__(
        self,
        detail: str = "Complete the required profile fields before marking onboarding complete.",
    ) -> None:
        super().__init__(422, detail)


class InvalidWebhookSignatureError(HorizonHTTPError):
    def __init__(self, detail: str = "Invalid Clerk webhook signature.") -> None:
        super().__init__(401, detail)


class InvalidWebhookPayloadError(HorizonHTTPError):
    def __init__(self, detail: str = "Invalid Clerk webhook payload.") -> None:
        super().__init__(400, detail)
