"""Assert that installing an optional extra did not downgrade a pinned base dep.

`pip install -e ".[intel]"` does not fail when an extra disagrees with
`requirements.txt` — it silently resolves the conflict by downgrading. That is
how `patent-client` (httpx<0.28, pypdf<5.0) quietly replaces the pinned
httpx==0.28.1 and pypdf==6.14.2 that the whole codebase runs on. Nothing errors,
nothing logs, and the break surfaces much later as odd behaviour in unrelated
code.

pip's own `pip check` does not catch this: after the downgrade the environment
is internally *consistent*, just not the one requirements.txt describes. The
only reliable signal is comparing what is installed against the pins.

Usage:
    python scripts/check_pins.py backend/requirements.txt

Exits non-zero and prints every drift, so one run reports all of them rather
than one per fix cycle.
"""
from __future__ import annotations

import re
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

# `name==version`, ignoring comments, extras markers and environment markers.
_PIN = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([^\s;#]+)")


def _parts(v: str) -> tuple:
    """Version → comparable tuple, tolerating suffixes like `1.2.3rc1`."""
    out = []
    for chunk in re.split(r"[.\-+]", v):
        m = re.match(r"^(\d+)", chunk)
        out.append(int(m.group(1)) if m else 0)
    return tuple(out)


def _is_older(got: str, want: str) -> bool:
    try:
        return _parts(got) < _parts(want)
    except Exception:
        return False


def read_pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _PIN.match(line)
        if m:
            pins[m.group(1).lower().replace("_", "-")] = m.group(2)
    return pins


def read_report(path: Path) -> dict[str, str]:
    """`pip install --dry-run --report` JSON → {name: version} it would install.

    Checking the *resolution* rather than an installation is what keeps the CI
    job to seconds: pip decides the downgrade during resolution, so nothing has
    to be downloaded or built to see it.
    """
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for item in data.get("install", []):
        meta = item.get("metadata") or {}
        name = (meta.get("name") or "").lower().replace("_", "-")
        if name:
            out[name] = meta.get("version", "")
    return out


def read_allowed(spec: str) -> dict[str, str]:
    """`--allow=name:version,...` → the drift already known and accepted.

    Pinned to an exact version on purpose. An accepted downgrade stays accepted
    only while it is the one that was reviewed; if an upgrade elsewhere makes it
    *worse*, the check fails again instead of the exception silently widening.
    """
    out: dict[str, str] = {}
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        name, _, ver = item.partition(":")
        out[name.lower().replace("_", "-")] = ver
    return out


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    report_arg = next((a for a in argv[1:] if a.startswith("--report=")), None)
    allow_arg = next((a for a in argv[1:] if a.startswith("--allow=")), None)
    allowed = read_allowed(allow_arg.split("=", 1)[1]) if allow_arg else {}
    if len(args) != 1:
        print(__doc__)
        return 2
    req = Path(args[0])
    if not req.exists():
        print(f"no such requirements file: {req}")
        return 2

    pins = read_pins(req)
    if not pins:
        print(f"no == pins found in {req} — nothing to verify")
        return 2

    resolved: dict[str, str] | None = None
    if report_arg:
        report = Path(report_arg.split("=", 1)[1])
        if not report.exists():
            print(f"no such pip report: {report}")
            return 2
        resolved = read_report(report)
        print(f"checking {len(pins)} pins against {len(resolved)} resolved packages")

    drift: list[tuple[str, str, str]] = []
    accepted: list[tuple[str, str, str]] = []
    missing: list[str] = []
    for name, want in sorted(pins.items()):
        if resolved is not None:
            # A pin absent from the report simply is not part of this
            # resolution — that is not drift.
            if name not in resolved:
                continue
            got = resolved[name]
        else:
            try:
                got = version(name)
            except PackageNotFoundError:
                missing.append(name)
                continue
        if got != want:
            if allowed.get(name) == got:
                accepted.append((name, want, got))
            else:
                drift.append((name, want, got))

    if accepted:
        print(f"\n{len(accepted)} known/accepted difference(s):")
        for name, want, got in accepted:
            print(f"  {name}: pinned {want}, got {got}  [accepted]")

    if missing:
        print(f"not installed ({len(missing)}): {', '.join(missing)}")
    if drift:
        noun = "resolved" if resolved is not None else "installed"
        print(f"\n{len(drift)} {noun} version(s) differ from {req}:\n")
        for name, want, got in drift:
            # Direction matters for diagnosis: a downgrade is almost always a
            # conflicting extra resolving in its own favour, while an upgrade is
            # usually an unpinned transitive dependency or a stale image.
            direction = "DOWNGRADED" if _is_older(got, want) else "upgraded"
            print(f"  {name}: pinned {want}, {noun} {got}  [{direction}]")
        print(
            "\nA DOWNGRADE means something — usually an optional extra — resolved a "
            "conflict against the pinned base. Drop the extra, or relax the pin "
            "deliberately. Either way it must not pass silently."
        )
        return 1

    print(f"all applicable pins match {req}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
