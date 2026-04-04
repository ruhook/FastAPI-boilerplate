PERMISSION_CATALOG: list[dict[str, list[str] | str]] = [
    {"group": "页面权限", "items": ["工作台", "岗位管理", "总人才库", "邮件与模板"]},
    {"group": "设置页面", "items": ["账户管理", "权限与角色", "常量字典", "报名表单策略"]},
]

ALL_ADMIN_PERMISSIONS: list[str] = [
    permission
    for group in PERMISSION_CATALOG
    for permission in group["items"]  # type: ignore[index]
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
