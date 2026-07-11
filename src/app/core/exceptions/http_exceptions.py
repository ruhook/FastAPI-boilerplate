# ruff: noqa
from fastcrud.exceptions.http_exceptions import (
    CustomException,
    BadRequestException,
    NotFoundException,
    ForbiddenException,
    UnauthorizedException,
    UnprocessableEntityException,
    DuplicateValueException,
    RateLimitException,
)
from fastapi import HTTPException


class ConflictException(CustomException):
    def __init__(self, detail: str | None = None):
        super().__init__(status_code=409, detail=detail)


class TooManyRequestsException(HTTPException):
    def __init__(self, detail: str, retry_after: int):
        super().__init__(
            status_code=429,
            detail=detail,
            headers={"Retry-After": str(max(1, retry_after))},
        )
