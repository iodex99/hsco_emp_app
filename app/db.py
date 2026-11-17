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
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.close()
        _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def init_db() -> None:
    from app import models  # noqa: F401  (register tables)

    Base.metadata.create_all(get_engine())
    from app.services.settings import ensure_defaults

    with session_scope() as db:
        ensure_defaults(db)


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
