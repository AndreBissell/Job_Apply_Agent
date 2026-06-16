"""Seek scraping layer.

Two components plus orchestration:

  * ``search``  - Component 1: search/list scraper (structured fields per card).
  * ``detail``  - Component 2: job-detail scraper (full ``raw_description``).
  * ``run``     - orchestration: iterate active saved searches, dedup, cap, upsert.

The scraper captures data faithfully only. It does NOT interpret listings:
``job_skills`` / ``qualification_requirements`` / ``experience_requirements`` are
populated by a later LLM pass, not here.
"""

from app.scraper.run import run_daily_scrape

__all__ = ["run_daily_scrape"]
