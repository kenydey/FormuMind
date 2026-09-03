"""Shared Playwright locators for the screenshot / smoke scripts.

These scripts used to reach into the DOM with `.fixed.inset-0.z-50 button`,
which coupled them to Tailwind classes *and* to the modal having exactly one
button. Both assumptions broke: the shared `Modal` title bar now carries up to
four controls (save / minimize / maximize / close), so `.first` resolved to
minimize, and maximize and the minimized-bar restore button share the glyph
`□`, so a text filter could not separate them either.

`Modal` now emits stable hooks instead:

    modal-<name>            the dialog
    modal-<name>-overlay    the backdrop
    modal-<name>-close      ... -minimize / -maximize / -save / -restore

and the action tiles emit `open-<name>` from the same id that drives
`openModal`, so the tile and the dialog it opens share one vocabulary.

Only these out-of-process drivers use data-testid. The vitest suite queries by
role and text, which stays the better default there because it tests what a
user can actually perceive.
"""
from __future__ import annotations

import asyncio


def modal(page, name: str):
    """The dialog body for `modal-<name>` (not the backdrop)."""
    return page.locator(f'[data-testid="modal-{name}"]')


def in_modal(page, name: str, selector: str):
    """A descendant of the named modal — replaces the old class-chain scoping."""
    return modal(page, name).locator(selector)


def in_any_modal(page, selector: str):
    """A descendant of whichever dialog is open.

    Some flows cross several dialogs (a nested one opens at z-[60] over its
    parent) and the caller genuinely does not care which is on top. This keeps
    that looseness while still not depending on Tailwind classes. The window
    controls carry `modal-<name>-close` etc. and so match the prefix too, but
    a button contains no buttons, so scoping through them finds nothing extra.
    """
    return page.locator('[data-testid^="modal-"]').locator(selector)


async def open_modal(page, name: str, *, timeout: float = 10_000):
    """Click the `open-<name>` tile and wait for the dialog to actually appear.

    Waiting on the dialog rather than sleeping is what makes these scripts
    stable: several of these modals lazy-load their body.
    """
    await page.locator(f'[data-testid="open-{name}"]').first.click()
    await modal(page, name).wait_for(state="visible", timeout=timeout)


async def close_modal(page, name: str):
    """Close the named modal, falling back to Escape if the button is gone."""
    close = page.locator(f'[data-testid="modal-{name}-close"]')
    if await close.count():
        await close.first.click()
    else:
        await page.keyboard.press("Escape")
    await modal(page, name).wait_for(state="detached", timeout=5_000)
    await asyncio.sleep(0.2)


async def click_if_present(locator, *, settle: float = 0.0) -> bool:
    """Click the first match when there is one; report whether anything happened.

    Several steps are best-effort (a button only rendered once a backend
    responds). Returning the outcome lets a caller log a skip instead of
    silently continuing as though it had clicked.
    """
    if not await locator.count():
        return False
    await locator.first.click()
    if settle:
        await asyncio.sleep(settle)
    return True
