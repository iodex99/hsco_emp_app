"""SQLAlchemy ORM schema. Documented in docs/ARCHITECTURE.md §Database."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint, Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def now() -> datetime:
    return datetime.now()


# ----------------------------------------------------------------------------- security

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(128), default="")
    email: Mapped[str] = mapped_column(String(256), default="")
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16), default="viewer")   # admin | hr | viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    last_login: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    mfa_methods: Mapped[list["MfaMethod"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class MfaMethod(Base):
    __tablename__ = "mfa_methods"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))           # totp | webauthn | email
    label: Mapped[str] = mapped_column(String(64), default="")
    secret: Mapped[str] = mapped_column(Text, default="")  # totp secret (encrypted)
    credential_id: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    public_key: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    sign_count: Mapped[int] = mapped_column(Integer, default=0)
    last_totp_step: Mapped[int] = mapped_column(Integer, default=0)  # replay protection
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    last_used: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    user: Mapped[User] = relationship(back_populates="mfa_methods")


class RecoveryCode(Base):
    __tablename__ = "recovery_codes"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(128))
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class EmailOtp(Base):
    __tablename__ = "email_otps"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class UserSession(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    mfa_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(256), default="")
    pending: Mapped[dict] = mapped_column(JSON, default=dict)   # webauthn challenges, enrolment secrets


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity: Mapped[str] = mapped_column(String(64), default="")
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[str] = mapped_column(String(64), default="")


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


# ----------------------------------------------------------------------------- master data

class HolidayCalendar(Base):
    __tablename__ = "holiday_calendars"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    holidays: Mapped[list["Holiday"]] = relationship(back_populates="calendar", cascade="all, delete-orphan",
                                                      order_by="Holiday.day")


class Holiday(Base):
    __tablename__ = "holidays"
    __table_args__ = (UniqueConstraint("calendar_id", "day"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    calendar_id: Mapped[int] = mapped_column(ForeignKey("holiday_calendars.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date)
    name: Mapped[str] = mapped_column(String(128), default="")
    calendar: Mapped[HolidayCalendar] = relationship(back_populates="holidays")


class Employee(Base):
    __tablename__ = "employees"
    emp_no: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    group: Mapped[str] = mapped_column(String(32), default="")
    location: Mapped[str] = mapped_column(String(64), default="")
    department: Mapped[str] = mapped_column(String(64), default="")
    designation: Mapped[str] = mapped_column(String(64), default="")
    email: Mapped[str] = mapped_column(String(256), default="")
    doj: Mapped[date | None] = mapped_column(Date, nullable=True)
    dol: Mapped[date | None] = mapped_column(Date, nullable=True)
    weekly_off: Mapped[str] = mapped_column(String(16), default="MUSTER")
    off_weekdays: Mapped[str] = mapped_column(String(32), default="5,6")      # CUSTOM pattern
    working_saturdays: Mapped[str] = mapped_column(String(16), default="")   # CUSTOM pattern e.g. "1,3"
    hours_per_day: Mapped[float | None] = mapped_column(Float, nullable=True)
    exempt: Mapped[bool] = mapped_column(Boolean, default=False)
    exempt_reason: Mapped[str] = mapped_column(String(128), default="")
    holiday_calendar_id: Mapped[int | None] = mapped_column(ForeignKey("holiday_calendars.id", ondelete="SET NULL"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32), default="manual")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
    holiday_calendar: Mapped[HolidayCalendar | None] = relationship()


class Geofence(Base):
    __tablename__ = "geofences"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(32), default="Client")   # Office | Client | Home | Other
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    radius_m: Mapped[int] = mapped_column(Integer, default=200)


class GeoCache(Base):
    __tablename__ = "geo_cache"
    key: Mapped[str] = mapped_column(String(32), primary_key=True)   # "19.0760,72.8777" (4 dp)
    name: Mapped[str] = mapped_column(String(256))
    source: Mapped[str] = mapped_column(String(16))


# ----------------------------------------------------------------------------- periods & imported data

class Period(Base):
    __tablename__ = "periods"
    __table_args__ = (UniqueConstraint("year", "month"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="DRAFT")   # DRAFT | FINALISED
    computed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finalised_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finalised_by: Mapped[str] = mapped_column(String(64), default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    @property
    def label(self) -> str:
        return date(self.year, self.month, 1).strftime("%B %Y")

    @property
    def key(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


class ImportBatch(Base):
    __tablename__ = "imports"
    id: Mapped[int] = mapped_column(primary_key=True)
    period_id: Mapped[int | None] = mapped_column(ForeignKey("periods.id", ondelete="CASCADE"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))
    filename: Mapped[str] = mapped_column(String(256))
    sha256: Mapped[str] = mapped_column(String(64))
    sheet: Mapped[str] = mapped_column(String(64), default="")
    rows: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    uploaded_by: Mapped[str] = mapped_column(String(64), default="")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class MusterRow(Base):
    __tablename__ = "muster_rows"
    __table_args__ = (UniqueConstraint("period_id", "emp_no", "day"), Index("ix_muster_period_emp", "period_id", "emp_no"))
    id: Mapped[int] = mapped_column(primary_key=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("periods.id", ondelete="CASCADE"))
    emp_no: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(128), default="")
    day: Mapped[date] = mapped_column(Date)
    s1: Mapped[str] = mapped_column(String(8), default="")
    s2: Mapped[str] = mapped_column(String(8), default="")
    in_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    out_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    work_min: Mapped[int] = mapped_column(Integer, default=0)
    shift: Mapped[str] = mapped_column(String(64), default="")


class SwipeRow(Base):
    __tablename__ = "swipes"
    __table_args__ = (Index("ix_swipe_period_emp", "period_id", "emp_no"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("periods.id", ondelete="CASCADE"))
    emp_no: Mapped[str] = mapped_column(String(32))
    ts: Mapped[datetime] = mapped_column(DateTime)
    door: Mapped[str] = mapped_column(String(128), default="")
    location_type: Mapped[str] = mapped_column(String(64), default="")
    swipe_type: Mapped[str] = mapped_column(String(32), default="")
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    place: Mapped[str] = mapped_column(String(256), default="")


class SummaryRow(Base):
    """greytHR Attendance Summary rows (kept for reconciliation / audit)."""
    __tablename__ = "summary_rows"
    __table_args__ = (UniqueConstraint("period_id", "emp_no"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("periods.id", ondelete="CASCADE"))
    emp_no: Mapped[str] = mapped_column(String(32))
    data: Mapped[dict] = mapped_column(JSON)


class ManualMisRow(Base):
    """The firm's manual MIS figures (uploaded during parallel run) — used for reconciliation."""
    __tablename__ = "manual_mis_rows"
    __table_args__ = (UniqueConstraint("period_id", "emp_no"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("periods.id", ondelete="CASCADE"))
    emp_no: Mapped[str] = mapped_column(String(32))
    data: Mapped[dict] = mapped_column(JSON)


# ----------------------------------------------------------------------------- manual entries

class DayOverrideRow(Base):
    __tablename__ = "day_overrides"
    __table_args__ = (UniqueConstraint("period_id", "emp_no", "day"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("periods.id", ondelete="CASCADE"))
    emp_no: Mapped[str] = mapped_column(String(32))
    day: Mapped[date] = mapped_column(Date)
    day_type: Mapped[str] = mapped_column(String(16), default="")
    s1: Mapped[str] = mapped_column(String(8), default="")
    s2: Mapped[str] = mapped_column(String(8), default="")
    work_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class MonthOverrideRow(Base):
    __tablename__ = "month_overrides"
    __table_args__ = (UniqueConstraint("period_id", "emp_no", "field"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("periods.id", ondelete="CASCADE"))
    emp_no: Mapped[str] = mapped_column(String(32))
    field: Mapped[str] = mapped_column(String(32))
    value: Mapped[str] = mapped_column(String(256))
    reason: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class LeaveOpening(Base):
    """Opening leave / comp-off balance as at the START of a given month (go-live or correction)."""
    __tablename__ = "leave_openings"
    __table_args__ = (UniqueConstraint("emp_no", "year", "month"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    emp_no: Mapped[str] = mapped_column(String(32), index=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    leave_balance: Mapped[float | None] = mapped_column(Float, nullable=True)
    compoff_balance: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


# ----------------------------------------------------------------------------- results

class MonthResult(Base):
    __tablename__ = "month_results"
    __table_args__ = (UniqueConstraint("period_id", "emp_no"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("periods.id", ondelete="CASCADE"), index=True)
    emp_no: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    group: Mapped[str] = mapped_column(String(32), default="")
    location: Mapped[str] = mapped_column(String(64), default="")
    deduction_days: Mapped[float] = mapped_column(Float, default=0)
    balance_hours: Mapped[float] = mapped_column(Float, default=0)
    flag_count: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[dict] = mapped_column(JSON)      # all EmployeeMonth scalar fields + ledger
    flags: Mapped[list] = mapped_column(JSON, default=list)
    days: Mapped[list] = mapped_column(JSON, default=list)


class FlagReview(Base):
    """HR has looked at a flag for an employee-month (review queue progress). Survives recomputation."""
    __tablename__ = "flag_reviews"
    __table_args__ = (UniqueConstraint("period_id", "emp_no", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("periods.id", ondelete="CASCADE"), index=True)
    emp_no: Mapped[str] = mapped_column(String(32))
    code: Mapped[str] = mapped_column(String(48))
    note: Mapped[str] = mapped_column(Text, default="")
    reviewed_by: Mapped[str] = mapped_column(String(64), default="")
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, default=now)
