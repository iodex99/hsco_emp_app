from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_config


class Base(DeclarativeBase):
    pass


_engine = None
_Session = None


def get_engine():
    global _engine, _Session
    if _engine is None:
        url = get_config().db_url
        _engine = create_engine(url, future=True, connect_args={"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {})
        if url.startswith("sqlite"):
            @event.listens_for(_engine, "connect")
            def _pragmas(dbapi_conn, _):  # noqa: ANN001
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA synchronous=FULL")      # a confirmed save survives a power cut
                cur.execute("PRAGMA busy_timeout=30000")
                cur.close()
        _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def init_db() -> None:
    from app import models  # noqa: F401  (register tables)

    Base.metadata.create_all(get_engine())
    add_missing_columns(get_engine())
    from app.services.settings import ensure_defaults

    with session_scope() as db:
        ensure_defaults(db)
    migrate_data()


def migrate_data() -> None:
    """One-time data migrations, each recorded in the settings table so it runs once. Draft months are recalculated
    afterwards by the rules-version step at start-up; finalised months never change."""
    from sqlalchemy import update
    from app.models import Employee, Setting
    with session_scope() as db:
        st = db.get(Setting, "migrations")
        done = set((st.value or {}).get("done", [])) if st else set()
        if "categories_v1" not in done:
            # v1.7: weekly-off "MUSTER" was simply the default — employees now follow their category (whose pattern
            # defaults to greytHR's muster, so figures stay the same until a category pattern is set)
            db.execute(update(Employee).where(Employee.weekly_off.in_(("MUSTER", "", None))).values(weekly_off="CATEGORY"))
            done.add("categories_v1")
        if "holidays_by_category_v1" not in done:
            _holidays_by_category(db)
            done.add("holidays_by_category_v1")
        if st is None:
            db.add(Setting(key="migrations", value={"done": sorted(done)}))
        else:
            st.value = {"done": sorted(done)}


def _holidays_by_category(db) -> None:
    """v1.9 (owner): holidays are targeted only by category. A targeted holiday for categories becomes a holiday in
    each of those categories' lists; targeting by location / group / named people no longer exists (those rows are
    listed in the audit log). Employees' own holiday calendars are cleared — holidays follow the category."""
    from sqlalchemy import select, update
    from app.models import Employee, TargetedHoliday
    from app.services.audit import audit
    from app.services.holidays import split
    rows = list(db.scalars(select(TargetedHoliday)))
    plan, dropped = [], []
    for h in rows:
        cats = split(h.categories or "")
        for cn in cats:
            plan.append({"date": h.day, "name": h.name or "Holiday", "optional": False, "target": ("category", cn)})
        if not cats or split(h.locations) or split(h.groups) or split(h.emp_nos):
            dropped.append(f"{h.day:%d-%b-%Y} {h.name}: locations={h.locations or '-'} groups={h.groups or '-'} "
                           f"employees={h.emp_nos or '-'}" + (" (kept for its categories)" if cats else ""))
    lines = []
    if plan:
        from app.services.imports import _apply_holiday_lists
        lines = _apply_holiday_lists(db, plan, {e.emp_no: e for e in db.scalars(select(Employee))})
    for h in rows:
        db.delete(h)
    own = [no for no, in db.execute(select(Employee.emp_no).where(Employee.holiday_calendar_id.is_not(None)))]
    if own:
        db.execute(update(Employee).where(Employee.holiday_calendar_id.is_not(None)).values(holiday_calendar_id=None))
    if rows or own:
        audit(db, None, "migration.holidays_by_category", "holiday", "",
              {"moved_to_category_lists": lines, "no_longer_targeted": dropped, "own_calendar_cleared": own})
    db.flush()


def add_missing_columns(engine) -> list[str]:
    """Additive schema upgrade for existing databases: create_all() makes missing tables but never new columns of an
    existing table, so add them (ALTER TABLE … ADD COLUMN, with the model's scalar default). Never drops or changes
    anything. Returns the columns added (logged)."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    added = []
    with engine.begin() as con:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = col.type.compile(engine.dialect)
                default = ""
                d = col.default.arg if col.default is not None and getattr(col.default, "is_scalar", False) else None
                if isinstance(d, bool):
                    default = f" DEFAULT {int(d)}"
                elif isinstance(d, (int, float)):
                    default = f" DEFAULT {d}"
                elif isinstance(d, str):
                    default = " DEFAULT '" + d.replace("'", "''") + "'"
                con.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}{default}'))
                added.append(f"{table.name}.{col.name}")
    if added:
        import logging
        logging.getLogger("hsco.db").info("schema upgraded: added %s", ", ".join(added))
    return added


def SessionLocal():
    get_engine()
    return _Session()


@contextmanager
def session_scope():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def reset_engine() -> None:  # tests
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _Session = None
