from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from cmbs.access.roles import Role
from cmbs.db.models import APP_SCHEMA, Base

ROLE_VALUES = ", ".join(f"'{role.value}'" for role in Role)


class User(Base):
    __tablename__ = "user"
    __table_args__ = {"schema": APP_SCHEMA}

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserRole(Base):
    __tablename__ = "user_role"
    __table_args__ = (
        CheckConstraint(f"role IN ({ROLE_VALUES})", name="known_role"),
        {"schema": APP_SCHEMA},
    )

    user_name: Mapped[str] = mapped_column(
        ForeignKey(f"{APP_SCHEMA}.user.name", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16), primary_key=True)
