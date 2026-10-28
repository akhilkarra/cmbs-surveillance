from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from cmbs.db.models import Base


class Deal(Base):
    """A CMBS deal. `cik` is the issuing entity's CIK when it has one; many private deals do not."""

    __tablename__ = "deal"
    __table_args__ = {"schema": "silver"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    cik: Mapped[int | None] = mapped_column(Integer, index=True)
    edgar_name: Mapped[str | None] = mapped_column(String(256))
