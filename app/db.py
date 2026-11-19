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
    """One-time data migrations, each recorded in the settings table so it runs once. Results never change by them
    alone (draft months are recalculated by the rules-version step at start-up)."""
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
        if st is None:
            db.add(Setting(key="migrations", value={"done": sorted(done)}))
        else:
            st.value = {"done": sorted(done)}


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
