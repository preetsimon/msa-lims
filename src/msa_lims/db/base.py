"""Declarative base, shared column types, and constraint naming.

The naming convention matters more than it looks. Without it, Alembic
autogenerate emits migrations that drop and recreate constraints under
server-assigned names, and a review of the diff cannot tell an intentional
change from churn. With it, every constraint has a name derived from its
columns, so a migration diff means what it says.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from sqlalchemy import BigInteger, MetaData, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# Primary keys are BIGINT identity rather than UUID. This is an internal system
# with a single writer, and two things follow from that: sequential keys keep
# time-ordered inserts local in the index, which is what the append-only tables
# need at a year of data; and an auditor quoting "result 41207" over the phone
# is doing something a UUID would make painful.
IdPk = Annotated[int, mapped_column(BigInteger, primary_key=True, autoincrement=True)]


# Measurement columns are declared as `mapped_column(Numeric)` at each use site
# rather than through an alias here. NUMERIC without precision or scale is
# arbitrary precision in Postgres and maps to Decimal in Python; constraining it
# would silently round results, and a rounded result is a different analytical
# statement from the one the instrument reported.

Sha256 = Annotated[str, mapped_column(String(64))]


class TimestampMixin:
    """When the row was written, as distinct from when the work happened.

    ``analysed_at`` on a result is instrument wall-clock time; ``received_at`` on
    a sample is when it physically arrived at the door. ``created_at`` here is
    system time, always with a zone, and is what the audit trail orders by.
    Conflating the two is how a back-entered result appears to have been
    reported before the sample arrived.
    """

    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False, index=True
    )
