from sqlalchemy import ForeignKey, Integer, String
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


class DealAlias(Base):
    """Another name for a registered deal: "COMM 2014-CCRE18" for "COMM 2014-CR18".

    A lookup that resolves to an issuing entity already registered under another name records
    the name it was given here, so every name of one trust reaches the one deal.
    """

    __tablename__ = "deal_alias"
    __table_args__ = {"schema": "silver"}

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("silver.deal.id"), index=True)
