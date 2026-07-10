from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db.database import Base
from ...core.db.models import DataBackedSoftDeleteEntityMixin


class User(DataBackedSoftDeleteEntityMixin, Base):
    __tablename__ = "user"

    name: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(20), nullable=False, index=True, unique=True)
    email: Mapped[str] = mapped_column(String(50), nullable=False, index=True, unique=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    profile_image_url: Mapped[str] = mapped_column(String(255), nullable=False)
