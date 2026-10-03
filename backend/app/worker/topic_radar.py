"""Topic radar: the Celery Beat schedule behind ``FORMUMIND_TOPIC_RADAR_*``.

``formumind.topic_sweep`` (search → topic filter → background KB fill) existed for a long time
with no way to run it: the only trigger was a commented-out beat schedule, and the docstring's
claim that an API could start it was not true. It now has both entry points:

* ``POST /api/search/topic-sweep`` — one sweep on demand;
* this module — a schedule built from settings, so changing a topic or its time is a settings
  change instead of an edit to ``celery_app.py``. It needs a beat process (compose
  ``--profile radar``, or ``celery -A app.worker.celery_app.celery_app worker -B``).

A malformed entry is skipped with a warning: the radar must never keep a worker from booting.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CRON = "0 1 * * 1"  # Mondays 01:00
_TASK_NAME = "formumind.topic_sweep"


def _crontab(expr: str):
    """Celery ``crontab`` for a 5-field ``minute hour day-of-month month day-of-week`` string."""
    from celery.schedules import crontab

    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"cron needs 5 fields (minute hour day month weekday), got {expr!r}")
    minute, hour, day_of_month, month_of_year, day_of_week = fields
    return crontab(
        minute=minute,
        hour=hour,
        day_of_month=day_of_month,
        month_of_year=month_of_year,
        day_of_week=day_of_week,
    )


def _slug(text: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z]+", "-", text).strip("-").lower()
    return slug[:24] or "topic"


def parse_topics(raw: str) -> list[dict[str, Any]]:
    """The valid topics in ``topic_radar_topics`` (JSON array), each with defaults filled in."""
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        loaded = json.loads(raw)
    except ValueError as exc:
        logger.warning("topic radar: topic_radar_topics is not valid JSON (%s); no sweeps scheduled", exc)
        return []
    if not isinstance(loaded, list):
        logger.warning("topic radar: topic_radar_topics must be a JSON array; no sweeps scheduled")
        return []

    topics: list[dict[str, Any]] = []
    for index, item in enumerate(loaded):
        if not isinstance(item, dict):
            logger.warning("topic radar: entry %d is not an object; skipped", index)
            continue
        query = str(item.get("query") or "").strip()
        if not query:
            logger.warning("topic radar: entry %d has no query; skipped", index)
            continue
        cron = str(item.get("cron") or DEFAULT_CRON).strip()
        try:
            _crontab(cron)
        except Exception as exc:  # noqa: BLE001 - any bad field is a bad entry, not a crash
            logger.warning("topic radar: entry %d has an invalid cron (%s); skipped", index, exc)
            continue
        try:
            total_limit = max(1, min(1000, int(item.get("total_limit", 100))))
            per_source_cap = max(1, min(200, int(item.get("per_source_cap", 30))))
        except (TypeError, ValueError):
            logger.warning("topic radar: entry %d has a non-numeric limit; skipped", index)
            continue
        source_types = item.get("source_types") or []
        if not isinstance(source_types, list) or not all(isinstance(t, str) for t in source_types):
            logger.warning("topic radar: entry %d has invalid source_types; skipped", index)
            continue
        topics.append(
            {
                "query": query,
                "project_id": (str(item["project_id"]).strip() or None) if item.get("project_id") else None,
                "cron": cron,
                "total_limit": total_limit,
                "per_source_cap": per_source_cap,
                "source_types": list(source_types),
            }
        )
    return topics


def build_beat_schedule(settings) -> dict[str, dict[str, Any]]:
    """Celery ``beat_schedule`` for the configured topics; empty when the radar is off."""
    if not settings.topic_radar_enabled:
        return {}
    schedule: dict[str, dict[str, Any]] = {}
    for index, topic in enumerate(parse_topics(settings.topic_radar_topics)):
        schedule[f"topic-radar-{index}-{_slug(topic['query'])}"] = {
            "task": _TASK_NAME,
            "schedule": _crontab(topic["cron"]),
            "args": (
                {
                    "query": topic["query"],
                    "project_id": topic["project_id"],
                    "total_limit": topic["total_limit"],
                    "per_source_cap": topic["per_source_cap"],
                    "source_types": topic["source_types"],
                },
            ),
        }
    if not schedule:
        logger.warning("topic radar is enabled but topic_radar_topics defines no valid sweep")
    return schedule
