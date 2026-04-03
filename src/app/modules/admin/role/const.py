PERMISSION_CATALOG: list[dict[str, list[str] | str]] = [
    {"group": "岗位管理", "items": ["查看岗位", "编辑岗位", "发布岗位", "复制岗位"]},
    {"group": "招聘进展", "items": ["查看招聘进展", "流转阶段", "批量操作", "上传附件"]},
    {"group": "总人才库", "items": ["查看人才库", "编辑候选人", "导出候选人"]},
    {"group": "邮件模块", "items": ["查看邮件模板", "编辑邮件模板", "管理发信账号"]},
    {"group": "系统设置", "items": ["管理账号", "管理角色", "管理字典与表单"]},
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
