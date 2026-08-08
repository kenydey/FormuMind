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

**Why a baseline.** `pip install --dry-run --report -e ".[extra]"` does not
apply requirements.txt as a constraint, so pyproject's `>=` lower bounds
resolve to whatever is newest on PyPI. Comparing that directly against the pins
reported four "upgrades" (alembic, fastapi, pydantic-settings, redis) in *every*
extra, for a reason that has nothing to do with extras — and a job that fails
identically forever is a job everyone learns to ignore.

So in report mode the comparison is against a **baseline resolution of the same
project with no extras**. What that subtracts is exactly the noise; what
survives is attributable to the extra, in either direction. A downgrade like
httpx 0.28.1 -> 0.27.2 still fails, and an extra that drags a base package
*forward* now fails too, which comparing against the pins could never see.

Usage:
    python scripts/check_pins.py backend/requirements.txt
    python scripts/check_pins.py backend/requirements.txt \
        --report=extra.json --baseline=base.json --allow=httpx:0.27.2

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


def _flag(argv: list[str], name: str) -> str | None:
    prefix = f"--{name}="
    match = next((a for a in argv if a.startswith(prefix)), None)
    return match[len(prefix):] if match is not None else None


def _load_report(path_str: str) -> dict[str, str] | None:
    path = Path(path_str)
    if not path.exists():
        print(f"no such pip report: {path}")
        return None
    return read_report(path)


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    report_arg = _flag(argv[1:], "report")
    baseline_arg = _flag(argv[1:], "baseline")
    allow_arg = _flag(argv[1:], "allow")
    allowed = read_allowed(allow_arg) if allow_arg else {}
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
    baseline: dict[str, str] | None = None
    if report_arg:
        resolved = _load_report(report_arg)
        if resolved is None:
            return 2
        print(f"checking {len(pins)} pins against {len(resolved)} resolved packages")
    if baseline_arg:
        if resolved is None:
            print("--baseline requires --report")
            return 2
        baseline = _load_report(baseline_arg)
        if baseline is None:
            return 2
        # ⚠️ The baseline must be resolved with `--ignore-installed`. Without it
        # `pip install --dry-run` omits already-satisfied packages (measured: 1
        # package versus 51), every missing package then looks extra-introduced,
        # and the comparison silently reverts to the noisy one it replaced.
        # There is no reliable way to detect that from the report alone — a
        # small baseline is indistinguishable from a small project — so the flag
        # is asserted on the workflow instead (`test_ci_deps_workflow.py`).
        print(f"baseline (no extras) resolved {len(baseline)} packages")

    drift: list[tuple[str, str, str]] = []
    accepted: list[tuple[str, str, str]] = []
    ambient: list[tuple[str, str, str]] = []
    introduced: list[tuple[str, str, str]] = []
    missing: list[str] = []
    for name, want in sorted(pins.items()):
        downgrade_only = False
        if resolved is not None:
            # A pin absent from the report simply is not part of this
            # resolution — that is not drift.
            if name not in resolved:
                continue
            got = resolved[name]
            if baseline is not None:
                # The question a baseline lets us ask: did *this extra* move the
                # package? A difference the extra-free resolution already shows
                # is pyproject's bounds being looser than requirements.txt —
                # worth printing, but not this job's failure.
                base = baseline.get(name)
                if base is not None:
                    if base == got:
                        if got != want:
                            ambient.append((name, want, got))
                        continue
                    want = base       # compare against what the extra changed
                else:
                    # No baseline entry: the extra brought this package in, so
                    # there is nothing to subtract and the pin is the only
                    # ruler. Newer than the pin is the same PyPI-moved-on noise
                    # as `ambient` — pypdf arrives via markitdown here. Older
                    # than the pin is a genuine conflict, because the codebase
                    # runs on the pinned version.
                    downgrade_only = True
        else:
            try:
                got = version(name)
            except PackageNotFoundError:
                missing.append(name)
                continue
        if got != want:
            if allowed.get(name) == got:
                accepted.append((name, want, got))
            elif downgrade_only and not _is_older(got, want):
                introduced.append((name, want, got))
            else:
                drift.append((name, want, got))

    if ambient:
        print(
            f"\n{len(ambient)} package(s) differ from {req} without any extra "
            "(pyproject's bounds are looser than the pins — not this job's concern):"
        )
        for name, want, got in ambient:
            print(f"  {name}: pinned {want}, baseline resolves {got}")

    if introduced:
        print(
            f"\n{len(introduced)} package(s) the extra introduces, resolving "
            f"newer than {req} (no base version to conflict with):"
        )
        for name, want, got in introduced:
            print(f"  {name}: pinned {want}, extra resolves {got}")

    if accepted:
        print(f"\n{len(accepted)} known/accepted difference(s):")
        for name, want, got in accepted:
            print(f"  {name}: expected {want}, got {got}  [accepted]")

    if missing:
        print(f"not installed ({len(missing)}): {', '.join(missing)}")
    if drift:
        noun = "resolved" if resolved is not None else "installed"
        source = "the extra-free baseline" if baseline is not None else str(req)
        print(f"\n{len(drift)} {noun} version(s) differ from {source}:\n")
        for name, want, got in drift:
            # Direction matters for diagnosis: a downgrade is almost always a
            # conflicting extra resolving in its own favour, while an upgrade is
            # usually an unpinned transitive dependency or a stale image.
            direction = "DOWNGRADED" if _is_older(got, want) else "upgraded"
            print(f"  {name}: expected {want}, {noun} {got}  [{direction}]")
        print(
            "\nThis extra changed a base dependency. A DOWNGRADE means it resolved "
            "a conflict in its own favour; an upgrade means it requires a newer "
            "floor than the base does. Drop the extra, or record the difference "
            "with --allow deliberately. Either way it must not pass silently."
        )
        return 1

    print(f"all applicable pins match {req}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
