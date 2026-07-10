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


class ConflictException(CustomException):
    def __init__(self, detail: str | None = None):
        super().__init__(status_code=409, detail=detail)
