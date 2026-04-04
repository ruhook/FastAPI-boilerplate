import re


def validate_admin_password(value: str) -> str:
    checks = (
        (len(value) >= 8, "Password must be at least 8 characters long."),
        (re.search(r"\s", value) is None, "Password must not contain whitespace."),
    )
    for passed, message in checks:
        if not passed:
            raise ValueError(message)
    return value


def validate_password_strength(value: str) -> str:
    checks = (
        (len(value) >= 8, "Password must be at least 8 characters long."),
        (re.search(r"[A-Z]", value) is not None, "Password must contain at least one uppercase letter."),
        (re.search(r"[a-z]", value) is not None, "Password must contain at least one lowercase letter."),
        (re.search(r"\d", value) is not None, "Password must contain at least one digit."),
        (re.search(r"[^A-Za-z0-9]", value) is not None, "Password must contain at least one special character."),
        (re.search(r"\s", value) is None, "Password must not contain whitespace."),
    )
    for passed, message in checks:
        if not passed:
            raise ValueError(message)
    return value
