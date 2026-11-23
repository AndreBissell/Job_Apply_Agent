"""SQLite's lock wait is 30 s (docs/second-worker-plan.md D4).

Two worker threads and the request threads can write at the same time, and SQLite allows
one writer at a time: a second writer waits for the first one's commit instead of failing
with "database is locked". app/db.py's connect listener sets ``PRAGMA busy_timeout`` on
every SQLite connection; other dialects are left alone.

Scratch SQLite files under pytest's tmp_path only. No server, no AI call, no network.
"""

from __future__ import annotations

import threading

from sqlalchemy import create_engine, text

from app import db as app_db

WAIT_S = 10  # upper bound for every Event / join below: a failure fails fast, never hangs


def _scratch_engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'lock.db'}")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, who TEXT)"))
    return eng


def test_every_new_sqlite_connection_waits_30_s_for_the_write_lock(tmp_path):
    eng = _scratch_engine(tmp_path)
    try:
        with eng.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 30_000
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1  # unchanged
            assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "delete"  # no WAL
    finally:
        eng.dispose()
    assert app_db.SQLITE_LOCK_WAIT_MS == 30_000


def test_the_listener_leaves_other_drivers_alone():
    class NotSqlite:
        def cursor(self):  # pragma: no cover - reaching here is the failure
            raise AssertionError("a non-SQLite connection was configured")

    app_db._enable_sqlite_foreign_keys(NotSqlite(), None)


def test_a_second_writer_waits_for_the_first_ones_commit(tmp_path):
    eng = _scratch_engine(tmp_path)
    about_to_write = threading.Event()
    result: dict[str, object] = {}

    def second_writer():
        try:
            with eng.begin() as conn:
                about_to_write.set()
                conn.execute(text("INSERT INTO t (who) VALUES ('second')"))
            result["ok"] = True
        except Exception as exc:  # noqa: BLE001 - reported by the assertion below
            result["error"] = exc

    try:
        with eng.connect() as first:
            first.begin()
            first.execute(text("INSERT INTO t (who) VALUES ('first')"))  # takes the write lock

            writer = threading.Thread(target=second_writer, daemon=True)
            writer.start()
            assert about_to_write.wait(WAIT_S)
            writer.join(timeout=0.5)
            assert writer.is_alive(), f"the second write did not wait for the lock: {result}"

            first.commit()  # releases the lock

        writer.join(timeout=WAIT_S)
        assert not writer.is_alive()
        assert result == {"ok": True}
        with eng.connect() as conn:
            rows = conn.execute(text("SELECT who FROM t ORDER BY id")).scalars().all()
        assert rows == ["first", "second"]
    finally:
        eng.dispose()
