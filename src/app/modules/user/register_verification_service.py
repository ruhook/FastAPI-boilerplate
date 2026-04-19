import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formataddr

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import settings
from ...core.exceptions.http_exceptions import (
    BadRequestException,
    DuplicateValueException,
    RateLimitException,
    UnprocessableEntityException,
)
from ..user.crud import crud_users

logger = logging.getLogger(__name__)


def is_register_verification_enabled() -> bool:
    return bool(settings.CANDIDATE_REGISTER_VERIFICATION_ENABLED)


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _verification_cache_key(email: str) -> str:
    normalized = _normalize_email(email)
    email_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"{settings.CANDIDATE_REGISTER_VERIFICATION_REDIS_PREFIX}{email_hash}"


def _hash_code(email: str, code: str) -> str:
    normalized = _normalize_email(email)
    secret = settings.CANDIDATE_REGISTER_VERIFICATION_AUTH_SECRET.get_secret_value()
    raw = f"{normalized}:{code}:{secret}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _generate_verification_code() -> str:
    digits = max(4, int(settings.CANDIDATE_REGISTER_VERIFICATION_CODE_LENGTH))
    upper = 10**digits
    return str(secrets.randbelow(upper)).zfill(digits)


def _build_message(email: str, code: str) -> EmailMessage:
    sender_name = settings.CANDIDATE_REGISTER_VERIFICATION_SENDER_NAME.strip() or "Primnota Recruitment"
    sender_email = settings.CANDIDATE_REGISTER_VERIFICATION_SENDER_EMAIL.strip()
    if not sender_email:
        raise BadRequestException("Candidate registration verification sender email is not configured.")

    subject = settings.CANDIDATE_REGISTER_VERIFICATION_SUBJECT.strip() or "Your verification code"
    ttl_minutes = max(1, settings.CANDIDATE_REGISTER_VERIFICATION_CODE_TTL_SECONDS // 60)

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((sender_name, sender_email))
    message["To"] = email
    message.set_content(
        "\n".join(
            [
                "Hello,",
                "",
                "Use the verification code below to finish creating your account.",
                "",
                f"Verification code: {code}",
                f"Expires in: {ttl_minutes} minutes",
                "",
                "If you did not request this email, you can safely ignore it.",
                "",
                f"{sender_name}",
            ]
        )
    )
    message.add_alternative(
        f"""
        <html>
          <body style="margin: 0; padding: 28px 16px; font-family: Arial, sans-serif; color: #163247; background: linear-gradient(180deg, #eef8fd 0%, #f8fbfd 100%);">
            <div style="max-width: 560px; margin: 0 auto;">
              <div style="margin-bottom: 16px; color: #6d8494; font-size: 12px; letter-spacing: 0.22em; text-transform: uppercase;">
                Account Verification
              </div>
              <div style="background: #ffffff; border: 1px solid #d6e8f3; border-radius: 24px; overflow: hidden; box-shadow: 0 18px 48px rgba(19, 128, 175, 0.08);">
                <div style="padding: 24px 28px; background: linear-gradient(135deg, rgba(19,128,175,0.12) 0%, rgba(255,255,255,1) 70%); border-bottom: 1px solid #e3eef5;">
                  <div style="display: inline-block; padding: 8px 14px; border-radius: 999px; background: rgba(19,128,175,0.1); color: #1380af; font-size: 12px; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase;">
                    Secure Sign Up
                  </div>
                  <h1 style="margin: 16px 0 10px; font-size: 30px; line-height: 1.2; color: #17324a;">
                    Your verification code
                  </h1>
                  <p style="margin: 0; line-height: 1.75; font-size: 15px; color: #486476;">
                    Use the code below to complete your registration and continue to the candidate portal.
                  </p>
                </div>
                <div style="padding: 28px;">
                  <div style="margin: 0 0 20px; padding: 22px 20px; border-radius: 18px; background: linear-gradient(180deg, rgba(19,128,175,0.08), rgba(19,128,175,0.02)); border: 1px solid rgba(19,128,175,0.18); text-align: center;">
                    <div style="margin-bottom: 8px; color: #6b7f8e; font-size: 12px; letter-spacing: 0.16em; text-transform: uppercase;">
                      Verification Code
                    </div>
                    <div style="font-size: 34px; line-height: 1; font-weight: 700; letter-spacing: 0.24em; color: #1380af;">
                      {code}
                    </div>
                  </div>
                  <div style="margin: 0 0 18px; padding: 16px 18px; border-radius: 16px; background: #f7fbfd; border: 1px solid #e2eef5; color: #486476; font-size: 14px; line-height: 1.75;">
                    This code will expire in <strong style="color: #17324a;">{ttl_minutes} minutes</strong>. For your security, please do not share it with anyone.
                  </div>
                  <p style="margin: 0 0 16px; line-height: 1.75; color: #486476; font-size: 14px;">
                    If you did not request this email, you can safely ignore it.
                  </p>
                  <div style="padding-top: 16px; border-top: 1px solid #e8f0f5; color: #6b7f8e; font-size: 13px; line-height: 1.75;">
                    Best regards,<br />
                    <strong style="color: #17324a;">{sender_name}</strong>
                  </div>
                </div>
              </div>
            </div>
          </body>
        </html>
        """,
        subtype="html",
    )
    return message


def _send_mail_sync(email: str, code: str) -> None:
    sender_email = settings.CANDIDATE_REGISTER_VERIFICATION_SENDER_EMAIL.strip()
    username = settings.CANDIDATE_REGISTER_VERIFICATION_SMTP_USERNAME.strip() or sender_email
    host = settings.CANDIDATE_REGISTER_VERIFICATION_SMTP_HOST.strip()
    port = int(settings.CANDIDATE_REGISTER_VERIFICATION_SMTP_PORT)
    security_mode = settings.CANDIDATE_REGISTER_VERIFICATION_SMTP_SECURITY_MODE.strip().lower() or "ssl"
    auth_secret = settings.CANDIDATE_REGISTER_VERIFICATION_AUTH_SECRET.get_secret_value()

    if not sender_email or not username or not host or not auth_secret:
        raise BadRequestException("Candidate registration verification mail settings are incomplete.")

    message = _build_message(email, code)
    context = ssl.create_default_context()

    if security_mode == "ssl":
        with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as server:
            server.login(username, auth_secret)
            server.send_message(message)
        return

    with smtplib.SMTP(host, port, timeout=30) as server:
        if security_mode == "starttls":
            server.starttls(context=context)
        server.login(username, auth_secret)
        server.send_message(message)


async def send_register_verification_code(
    *,
    email: str,
    redis: Redis,
    db: AsyncSession,
) -> int:
    normalized_email = _normalize_email(email)
    if await crud_users.exists(db=db, email=normalized_email):
        raise DuplicateValueException("Email is already registered")

    now = int(time.time())
    cache_key = _verification_cache_key(normalized_email)
    existing_payload_raw = await redis.get(cache_key)
    if existing_payload_raw:
        try:
            existing_payload = json.loads(existing_payload_raw)
        except json.JSONDecodeError:
            existing_payload = {}
        sent_at = int(existing_payload.get("sent_at", 0) or 0)
        cooldown = int(settings.CANDIDATE_REGISTER_VERIFICATION_RESEND_COOLDOWN_SECONDS)
        retry_after = cooldown - (now - sent_at)
        if retry_after > 0:
            raise RateLimitException(f"Please wait {retry_after} seconds before requesting another verification code.")

    verification_code = _generate_verification_code()
    payload = {
        "email": normalized_email,
        "code_hash": _hash_code(normalized_email, verification_code),
        "sent_at": now,
        "attempt_count": 0,
    }

    ttl_seconds = int(settings.CANDIDATE_REGISTER_VERIFICATION_CODE_TTL_SECONDS)
    await redis.set(cache_key, json.dumps(payload), ex=ttl_seconds)

    try:
        await asyncio.to_thread(_send_mail_sync, normalized_email, verification_code)
    except Exception as exc:
        await redis.delete(cache_key)
        logger.exception("Failed to send candidate registration verification email", extra={"email": normalized_email})
        raise BadRequestException(f"Failed to send verification email: {exc}") from exc

    return int(settings.CANDIDATE_REGISTER_VERIFICATION_RESEND_COOLDOWN_SECONDS)


async def verify_register_verification_code(
    *,
    email: str,
    code: str,
    redis: Redis,
) -> None:
    normalized_email = _normalize_email(email)
    normalized_code = code.strip()
    if not normalized_code:
        raise UnprocessableEntityException("Verification code is required.")

    cache_key = _verification_cache_key(normalized_email)
    cached_payload_raw = await redis.get(cache_key)
    if not cached_payload_raw:
        raise UnprocessableEntityException("Verification code has expired or has not been requested.")

    try:
        payload = json.loads(cached_payload_raw)
    except json.JSONDecodeError as exc:
        await redis.delete(cache_key)
        raise UnprocessableEntityException("Verification code is invalid. Please request a new one.") from exc

    expected_hash = str(payload.get("code_hash") or "")
    actual_hash = _hash_code(normalized_email, normalized_code)
    if hmac.compare_digest(expected_hash, actual_hash):
        await redis.delete(cache_key)
        return

    attempt_count = int(payload.get("attempt_count", 0) or 0) + 1
    max_attempts = max(1, int(settings.CANDIDATE_REGISTER_VERIFICATION_MAX_ATTEMPTS))
    remaining_attempts = max_attempts - attempt_count
    if remaining_attempts <= 0:
        await redis.delete(cache_key)
        raise UnprocessableEntityException("Verification code is incorrect and has expired. Please request a new one.")

    payload["attempt_count"] = attempt_count
    ttl = await redis.ttl(cache_key)
    if ttl and ttl > 0:
        await redis.set(cache_key, json.dumps(payload), ex=ttl)
    else:
        await redis.set(cache_key, json.dumps(payload), ex=int(settings.CANDIDATE_REGISTER_VERIFICATION_CODE_TTL_SECONDS))
    raise UnprocessableEntityException(f"Verification code is incorrect. {remaining_attempts} attempt(s) remaining.")
