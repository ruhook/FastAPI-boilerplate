import hashlib
import hmac
import logging
from dataclasses import dataclass
from enum import StrEnum

from redis.asyncio import Redis
from redis.exceptions import RedisError

from .config import EnvironmentOption, settings
from .exceptions.http_exceptions import AuthRateLimitUnavailableException, TooManyRequestsException

logger = logging.getLogger(__name__)

RATE_LIMIT_LUA = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
local ttl = redis.call('TTL', KEYS[1])
return {count, ttl}
"""


class AuthRateLimitAction(StrEnum):
    LOGIN = "login"
    VERIFICATION_SEND = "verification-send"
    VERIFICATION_CHECK = "verification-check"


@dataclass(frozen=True, slots=True)
class RateLimitRule:
    dimension: str
    value: str
    limit: int
    window_seconds: int


def _normalize(value: str) -> str:
    return value.strip().lower() or "unknown"


def _digest_dimension(*, action: AuthRateLimitAction, dimension: str, value: str) -> str:
    secret = settings.SECRET_KEY.get_secret_value().encode()
    message = f"auth-rate-limit:{action.value}:{dimension}:{value}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def _build_key(*, action: AuthRateLimitAction, dimension: str, value: str) -> str:
    digest = _digest_dimension(action=action, dimension=dimension, value=value)
    return f"{settings.AUTH_RATE_LIMIT_PREFIX}{action.value}:{dimension}:{digest}"


def _rules_for_action(
    *,
    action: AuthRateLimitAction,
    client_ip: str,
    identifier: str,
) -> list[RateLimitRule]:
    normalized_ip = _normalize(client_ip)
    normalized_identifier = _normalize(identifier)
    if action == AuthRateLimitAction.LOGIN:
        window = settings.AUTH_LOGIN_WINDOW_SECONDS
        return [
            RateLimitRule("ip", normalized_ip, settings.AUTH_LOGIN_IP_LIMIT, window),
            RateLimitRule(
                "identifier",
                normalized_identifier,
                settings.AUTH_LOGIN_IDENTIFIER_LIMIT,
                window,
            ),
            RateLimitRule(
                "pair",
                f"{normalized_ip}\0{normalized_identifier}",
                settings.AUTH_LOGIN_PAIR_LIMIT,
                window,
            ),
        ]
    if action == AuthRateLimitAction.VERIFICATION_SEND:
        window = settings.AUTH_VERIFICATION_SEND_WINDOW_SECONDS
        return [
            RateLimitRule("ip", normalized_ip, settings.AUTH_VERIFICATION_SEND_IP_LIMIT, window),
            RateLimitRule(
                "identifier",
                normalized_identifier,
                settings.AUTH_VERIFICATION_SEND_IDENTIFIER_LIMIT,
                window,
            ),
        ]

    window = settings.AUTH_VERIFICATION_CHECK_WINDOW_SECONDS
    return [
        RateLimitRule("ip", normalized_ip, settings.AUTH_VERIFICATION_CHECK_IP_LIMIT, window),
        RateLimitRule(
            "identifier",
            normalized_identifier,
            settings.AUTH_VERIFICATION_CHECK_IDENTIFIER_LIMIT,
            window,
        ),
    ]


async def enforce_auth_rate_limit(
    redis_client: Redis,
    *,
    action: AuthRateLimitAction,
    client_ip: str,
    identifier: str,
) -> None:
    exceeded_retry_after: list[int] = []
    try:
        for rule in _rules_for_action(
            action=action,
            client_ip=client_ip,
            identifier=identifier,
        ):
            key = _build_key(action=action, dimension=rule.dimension, value=rule.value)
            raw_result = await redis_client.eval(
                RATE_LIMIT_LUA,
                1,
                key,
                rule.window_seconds,
            )
            count, ttl = int(raw_result[0]), int(raw_result[1])
            if count > rule.limit:
                exceeded_retry_after.append(max(1, ttl))
    except RedisError:
        if settings.ENVIRONMENT == EnvironmentOption.LOCAL:
            logger.warning(
                "Authentication rate limit unavailable in local environment; allowing request",
                extra={"action": action.value},
            )
            return
        raise AuthRateLimitUnavailableException() from None

    if exceeded_retry_after:
        raise TooManyRequestsException(
            "Too many requests. Please try again later.",
            retry_after=max(exceeded_retry_after),
        )
