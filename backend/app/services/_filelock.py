"""An exclusive advisory lock on an open file — ``flock`` on POSIX, ``msvcrt.locking`` on Windows.

Two services (``artifact_versions``, ``smart_collections``) serialise cross-process read-modify-write
transactions on a sibling ``*.lock`` file. Both did ``import fcntl`` at module level, and ``fcntl`` does not exist
on Windows — so ``app.main`` could not even be imported there (``artifact_versions`` is reached through the
router list), and the Windows installer finished "successfully" in front of an application that crashed on start.
The comments in both modules already say the lock degrades to threading locks + atomic writes off POSIX; this is
that, made real.
"""
from __future__ import annotations

import sys
import time

DEFAULT_TIMEOUT_S = 30.0

if sys.platform == "win32":  # pragma: no cover - exercised on Windows (CI: installer-windows) and by a fake in tests
    import msvcrt

    def lock_exclusive(fh, *, timeout: float = DEFAULT_TIMEOUT_S) -> None:
        """Block until the first byte of *fh* is locked; ``OSError`` after *timeout* seconds.

        ``msvcrt.locking`` has no blocking mode that waits indefinitely (``LK_LOCK`` gives up after ten tries), so
        the non-blocking form is retried.
        """
        deadline = time.monotonic() + timeout
        while True:
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    def unlock(fh) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def lock_exclusive(fh, *, timeout: float = DEFAULT_TIMEOUT_S) -> None:
        """Block until *fh* is locked (``flock`` waits as long as it takes; *timeout* is the Windows contract)."""
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)

    def unlock(fh) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
