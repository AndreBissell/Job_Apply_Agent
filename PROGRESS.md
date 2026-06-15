# Progress Log — Job Application Assistant

Running record of what's been built. Newest entries on top. Keep entries slim —
one block per milestone.

---

## 2026-06-15 — Database layer (Task 1) ✅

Built the full persistence layer per `docs/database-schema.md`. Nothing else yet
(no scraper, matching, generation, or UI).

**Delivered**
- Repo scaffold: `app/`, `alembic/`, `scripts/`, `docs/`, `requirements.txt`,
  `.env.example`, `.gitignore`.
- `app/db.py` — engine + `SessionLocal` + `Base` from `DATABASE_URL`
  (default `sqlite:///app.db`); SQLite `PRAGMA foreign_keys=ON` listener; loads
  `.env` via python-dotenv.
- `app/models.py` — all 11 tables as SQLAlchemy 2.0 typed models, portable
  across SQLite and Postgres.
- `alembic/` wired to the app's engine + metadata; initial migration
  `cf7f9fc0ffc2_initial_schema` creates all 11 tables.
- `scripts/smoke_test.py` — seeds a full graph, runs the "experiences that
  demonstrate skill X" evidence query, and verifies cascade delete.

**Verified**
- `alembic upgrade head` on a fresh `app.db` → 11 tables. Downgrade/upgrade
  round-trips. `alembic check` reports no drift (models = migration = DB).
- `python scripts/smoke_test.py` → PASS (evidence query + cascade + shared job
  survives).

**Decisions worth remembering**
- Boolean defaults use `text("false")` / `text("true")` (not `0/1` or
  `false()`), so the migration runs unchanged on both SQLite 3.23+ and Postgres.
- PKs use `BigInteger().with_variant(Integer, "sqlite")` to autoincrement on
  both dialects.
- Relationships use `passive_deletes=True`, so deletes rely on DB-level
  `ON DELETE CASCADE` (this is what the smoke test actually exercises).

**Environment notes**
- pip is behind a TLS-intercepting cert here; installs need
  `--trusted-host pypi.org --trusted-host files.pythonhosted.org`.
- `docs/job-application-assistant-plan.md` is referenced by CLAUDE.md but not yet
  present in the repo.

**Next up:** the Seek scraper (Feature 2) — not started.
