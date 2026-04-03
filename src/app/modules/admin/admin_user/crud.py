from fastcrud import FastCRUD

from .model import AdminUser
from .schema import AdminUserCreateInternal, AdminUserDBRead, AdminUserDelete, AdminUserUpdate, AdminUserUpdateInternal

CRUDAdminUser = FastCRUD[
    AdminUser,
    AdminUserCreateInternal,
    AdminUserUpdate,
    AdminUserUpdateInternal,
    AdminUserDelete,
    AdminUserDBRead,
]
crud_admin_users = CRUDAdminUser(AdminUser)
