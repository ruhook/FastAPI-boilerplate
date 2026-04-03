from .admin.api import router
from .core.config import settings
from .core.setup import create_application

app = create_application(router=router, settings=settings, service_name="admin")
