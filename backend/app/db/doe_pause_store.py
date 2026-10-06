"""The closed loop's pause flag, kept in the database (``models.DOECyclePauseRow``).

It used to be a Redis key with a 24 h TTL. That lost the user's pause in three ways: without Redis (development,
eager mode) pausing answered 503; with Redis unreachable the flag read as "not paused"; and a pause set on
Friday lapsed on Saturday without a word. Here it is one row per campaign:

* ``set_paused`` pauses for ``ttl_hours`` (``None`` / ``0`` = until somebody resumes);
* a pause that runs out is noticed by whoever reads it next, recorded once (``lapsed_at``) and logged - the loop
  is then running again, and ``read_state`` still says when and why, so the UI can show it;
* ``clear_pause`` is an explicit resume and forgets everything.

All times are naive UTC (``app.clock.utcnow``), like every other column.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ..clock import utcnow
from .models import Campaign, DOECyclePauseRow
from .session_utils import commit_session

logger = logging.getLogger(__name__)

# A pause cannot be longer than this (also the API's bound on ``ttlHours``): a typo like 9e9 would make
# ``timedelta`` overflow, and a year is already "until somebody resumes" for every practical purpose.
MAX_TTL_HOURS = 24 * 366

# How long after a pause ran out the status keeps saying so.
LAPSE_NOTICE = timedelta(days=7)


class CampaignNotFoundError(LookupError):
    """The campaign the pause was meant for does not exist."""


@dataclass(frozen=True)
class PauseState:
    paused: bool
    paused_at: datetime | None = None
    paused_until: datetime | None = None  # while paused: None = until somebody resumes
    lapsed_at: datetime | None = None  # the pause ran out by itself at this moment (loop running again)


RUNNING = PauseState(paused=False)


def utc_iso(value: datetime | None) -> str | None:
    """A naive-UTC database value as an unambiguous ISO string (``…Z``): a bare ISO string is read as *local* time
    by a browser."""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _factory() -> sessionmaker[Session]:
    from .database import default_session_factory

    return default_session_factory()


def campaign_owner(campaign_id: int, *, session_factory: sessionmaker[Session] | None = None) -> str | None:
    """``Campaign.owner_id`` of an existing campaign (the API's ownership check); raises
    :class:`CampaignNotFoundError` for one that does not exist."""
    with (session_factory or _factory())() as session:
        campaign = session.get(Campaign, campaign_id)
        if campaign is None:
            raise CampaignNotFoundError(campaign_id)
        return campaign.owner_id


def _state_of(row: DOECyclePauseRow | None, now: datetime) -> PauseState:
    if row is None:
        return RUNNING
    if row.lapsed_at is not None:
        return PauseState(paused=False, paused_at=row.paused_at, paused_until=row.paused_until, lapsed_at=row.lapsed_at)
    if row.paused_until is None or row.paused_until > now:
        return PauseState(paused=True, paused_at=row.paused_at, paused_until=row.paused_until)
    return PauseState(paused=False, paused_at=row.paused_at, paused_until=row.paused_until, lapsed_at=row.paused_until)


def read_state(
    campaign_id: int,
    *,
    session_factory: sessionmaker[Session] | None = None,
    now: datetime | None = None,
) -> PauseState:
    """The campaign's pause state; a pause that has run out is recorded (once) and logged on the way."""
    factory = session_factory or _factory()
    now = now or utcnow()
    with factory() as session:
        row = session.get(DOECyclePauseRow, campaign_id)
        state = _state_of(row, now)
    if row is not None and row.lapsed_at is None and not state.paused:
        _record_lapse(factory, campaign_id, now)
        with factory() as session:
            state = _state_of(session.get(DOECyclePauseRow, campaign_id), now)
    return state


def _record_lapse(factory: sessionmaker[Session], campaign_id: int, now: datetime) -> None:
    """Mark an expired pause as lapsed. The conditional UPDATE lets exactly one reader (across processes) win, so
    the log line appears once however many workers and pollers notice at the same moment."""
    with commit_session(factory) as session:
        won = session.execute(
            update(DOECyclePauseRow)
            .where(
                DOECyclePauseRow.campaign_id == campaign_id,
                DOECyclePauseRow.lapsed_at.is_(None),
                DOECyclePauseRow.paused_until.is_not(None),
                DOECyclePauseRow.paused_until <= now,
            )
            .values(lapsed_at=DOECyclePauseRow.paused_until)
        ).rowcount
        row = session.get(DOECyclePauseRow, campaign_id) if won else None
        paused_at = row.paused_at if row is not None else None
        paused_until = row.paused_until if row is not None else None
    if won:
        logger.warning(
            "DOE cycle pause for campaign %s ran out at %s (set %s): the closed loop is running again. "
            "Pause it again, or set FORMUMIND_DOE_CYCLE_PAUSE_TTL_HOURS=0 for pauses that last until resumed.",
            campaign_id, utc_iso(paused_until), utc_iso(paused_at),
        )


def _write_pause(session: Session, campaign_id: int, paused_at: datetime, paused_until: datetime | None) -> None:
    updated = session.execute(
        update(DOECyclePauseRow)
        .where(DOECyclePauseRow.campaign_id == campaign_id)
        .values(paused_at=paused_at, paused_until=paused_until, lapsed_at=None)
    ).rowcount
    if not updated:
        session.add(
            DOECyclePauseRow(campaign_id=campaign_id, paused_at=paused_at, paused_until=paused_until, lapsed_at=None)
        )


def set_paused(
    campaign_id: int,
    *,
    ttl_hours: float | None,
    session_factory: sessionmaker[Session] | None = None,
    now: datetime | None = None,
) -> PauseState:
    """Pause the campaign's loop for ``ttl_hours`` from now (``None`` / ``0`` = until resumed). Pausing again
    restarts the clock. Raises :class:`CampaignNotFoundError` for a campaign that does not exist."""
    factory = session_factory or _factory()
    now = now or utcnow()
    if ttl_hours is not None and not 0 <= ttl_hours <= MAX_TTL_HOURS:
        raise ValueError(f"ttl_hours must be between 0 and {MAX_TTL_HOURS}, got {ttl_hours!r}")
    until = now + timedelta(hours=ttl_hours) if ttl_hours else None

    with factory() as session:
        if session.get(Campaign, campaign_id) is None:
            raise CampaignNotFoundError(campaign_id)
    try:
        with commit_session(factory) as session:
            _write_pause(session, campaign_id, now, until)
    except IntegrityError:
        # Two pauses for a campaign without a row yet: the other one inserted it between our UPDATE and INSERT.
        # Its row exists now, so the UPDATE path takes it.
        with commit_session(factory) as session:
            _write_pause(session, campaign_id, now, until)
    return PauseState(paused=True, paused_at=now, paused_until=until)


def clear_pause(campaign_id: int, *, session_factory: sessionmaker[Session] | None = None) -> bool:
    """Resume: forget the pause and any lapse note. True when there was a row to forget."""
    factory = session_factory or _factory()
    with factory() as session:
        if session.get(Campaign, campaign_id) is None:
            raise CampaignNotFoundError(campaign_id)
    with commit_session(factory) as session:
        return bool(session.execute(delete(DOECyclePauseRow).where(DOECyclePauseRow.campaign_id == campaign_id)).rowcount)
