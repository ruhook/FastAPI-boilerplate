from collections.abc import Iterable


BUSINESS_ADMIN_PERMISSIONS = ["工作台", "岗位管理", "合同管理", "工时记录", "总人才库", "邮件与模板"]
SPECIAL_ADMIN_PERMISSIONS = ["测试题判题"]
SETTINGS_ADMIN_PERMISSIONS = ["账户管理", "权限与角色", "常量字典", "报名表单策略", "公司管理"]
CONFIGURABLE_ADMIN_PERMISSIONS = [*SPECIAL_ADMIN_PERMISSIONS]

PERMISSION_CATALOG: list[dict[str, list[str] | str]] = [
    {"group": "特殊权限", "items": SPECIAL_ADMIN_PERMISSIONS},
]

ALL_ADMIN_PERMISSIONS: list[str] = [
    *BUSINESS_ADMIN_PERMISSIONS,
    *CONFIGURABLE_ADMIN_PERMISSIONS,
    *SETTINGS_ADMIN_PERMISSIONS,
]


def deduplicate_permissions(permissions: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for permission in permissions:
        if permission in seen:
            continue
        seen.add(permission)
        normalized.append(permission)
    return normalized


def validate_permissions(permissions: list[str]) -> list[str]:
    unknown = sorted(set(permissions) - set(ALL_ADMIN_PERMISSIONS))
    if unknown:
        raise ValueError(f"Unknown admin permissions: {', '.join(unknown)}")
    return deduplicate_permissions(permissions)


def normalize_effective_role_permissions(permissions: Iterable[str] | None) -> list[str]:
    return deduplicate_permissions(
        [
            permission
            for permission in (permissions or [])
            if permission in CONFIGURABLE_ADMIN_PERMISSIONS
        ]
    )


def is_assessment_reviewer_only_permissions(
    permissions: Iterable[str] | None,
    *,
    is_superuser: bool = False,
) -> bool:
    if is_superuser:
        return False
    current_permissions = set(permissions or [])
    return (
        "测试题判题" in current_permissions
        and current_permissions.isdisjoint(BUSINESS_ADMIN_PERMISSIONS)
        and current_permissions.isdisjoint(SETTINGS_ADMIN_PERMISSIONS)
    )
