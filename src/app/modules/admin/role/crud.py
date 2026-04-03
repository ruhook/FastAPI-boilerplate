from fastcrud import FastCRUD

from .model import Role
from .schema import RoleCreateInternal, RoleDelete, RoleRead, RoleUpdate, RoleUpdateInternal

CRUDRole = FastCRUD[Role, RoleCreateInternal, RoleUpdate, RoleUpdateInternal, RoleDelete, RoleRead]
crud_roles = CRUDRole(Role)
