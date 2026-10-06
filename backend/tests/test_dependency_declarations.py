"""What the installers install, what the code imports and what the pins allow must say the same thing.

Four places where they did not, found in round 4 - none of them visible to a test, because a test environment has
the packages it has:

* ``.[intel]`` (which the Dockerfile installs) pulled ``patent-client``, whose every release requires ``httpx<0.28``
  and ``pypdf<5``. pip resolved the conflict with ``requirements.txt`` by *replacing* the pinned packages, so the
  production image ran pypdf 4.3.1 - 49 published advisories, all of them crafted-PDF denial of service - and httpx
  0.27.2, and nothing complained. The Settings page's one-click "online mode" did the same to a running install.
  (The SDK has since been removed altogether: the patent search is EPO OPS over httpx.)
* the one-click installer offered ``rapidocr-onnxruntime``, a package the OCR engine stopped importing, so the
  button added dead weight while the availability report said OCR was ready;
* it offered ``pymupdf4llm`` unpinned, while the extra pins 1.28.0 because 1.28.2 breaks the layout path;
* it offered the ``semanticscholar`` SDK, which no code imports (Semantic Scholar is queried over HTTP).

Every test here reads the declarations; none needs a package index.
"""
from __future__ import annotations

import ast
import re
import subprocess
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from app.services import dependencies as deps

BACKEND = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    return tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))


def _extras() -> dict[str, list[Requirement]]:
    table = _pyproject()["project"]["optional-dependencies"]
    return {name: [Requirement(r) for r in reqs] for name, reqs in table.items()}


def _core() -> list[Requirement]:
    return [Requirement(r) for r in _pyproject()["project"]["dependencies"]]


def _pins() -> dict[str, str]:
    """canonical name -> version of every ``==`` line in requirements.txt."""
    pins: dict[str, str] = {}
    for raw in (BACKEND / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        req = Requirement(line)
        exact = [s.version for s in req.specifier if s.operator == "=="]
        if exact:
            pins[canonicalize_name(req.name)] = exact[0]
    return pins


def _dockerfile_extras() -> list[str]:
    match = re.search(r'-e "\.\[([^\]]+)\]"', (BACKEND / "Dockerfile").read_text(encoding="utf-8"))
    assert match, "the Dockerfile no longer has an editable install with extras"
    return [name.strip() for name in match.group(1).split(",")]


# Catalog entries that are deliberately not in any extra: the one-click installer is the only way to add them.
CATALOG_ONLY = {
    "docling": "needs torch weights; a dormant stub in the PDF tiers (see the parse_pro comment in pyproject.toml)",
    "marker-pdf": "needs torch weights, ~54 s/page on CPU; a dormant stub in the PDF tiers",
}


# ── the catalog says what pyproject says ─────────────────────────────────────


def test_every_catalog_entry_is_declared_by_the_extra_it_names():
    extras = _extras()
    problems = []
    for dep in deps.CATALOG:
        if dep.pip_name in CATALOG_ONLY:
            continue
        if dep.extra not in extras:
            problems.append(f"{dep.pip_name}: there is no extra {dep.extra!r}")
        elif canonicalize_name(dep.pip_name) not in {canonicalize_name(r.name) for r in extras[dep.extra]}:
            problems.append(f"{dep.pip_name}: extra {dep.extra!r} does not declare it")
    assert not problems, "\n".join(problems)


def test_the_catalog_only_allowlist_is_not_stale():
    declared = {canonicalize_name(r.name) for reqs in _extras().values() for r in reqs}
    catalog = {d.pip_name for d in deps.CATALOG}
    for name in CATALOG_ONLY:
        assert name in catalog, f"{name} left the catalog; drop it from CATALOG_ONLY"
        assert canonicalize_name(name) not in declared, f"{name} is now declared by an extra; drop it from CATALOG_ONLY"


def test_a_catalog_install_spec_carries_its_extras_version_rules():
    """pyproject pins pymupdf4llm / rapidocr exactly because newer releases break the OCR and layout paths, and floors
    everything else; an installer that asks for the bare name installs the broken release - and, under the pins,
    lets pip settle a conflict by backtracking to an ancient one (``pip install -c <pins> patent-client`` installs
    3.2.6 rather than failing)."""
    extras = _extras()
    problems = []
    for dep in deps.CATALOG:
        declared = next(
            (r for r in extras.get(dep.extra, []) if canonicalize_name(r.name) == canonicalize_name(dep.pip_name)), None
        )
        if declared is None:
            continue
        got = Requirement(dep.install_spec)
        if str(got.specifier) != str(declared.specifier) or got.extras != declared.extras:
            problems.append(f"{dep.pip_name}: the extra says {declared}, the installer asks for {dep.install_spec!r}")
    assert not problems, "\n".join(problems)


def test_an_install_spec_falls_back_to_the_bare_name_when_pyproject_is_not_shipped(monkeypatch, tmp_path):
    monkeypatch.setattr(deps, "_PYPROJECT", tmp_path / "absent.toml")
    deps._declared_requirements.cache_clear()
    try:
        entry = next(d for d in deps.CATALOG if d.pip_name == "ddgs")
        assert entry.install_spec == "ddgs"
        assert next(d for d in deps.CATALOG if d.pip_name == "rapidocr").install_spec == "rapidocr==3.9.2"  # explicit wins
    finally:
        deps._declared_requirements.cache_clear()


def test_requirement_names_are_normalised_the_way_pip_does():
    assert deps._canonical("ChemFormula") == "chemformula"
    assert deps._canonical("Paper_QA") == "paper-qa"
    assert deps._canonical("ruamel.yaml") == "ruamel-yaml"


def test_companion_specs_are_requirements():
    for dep in deps.CATALOG:
        for spec in dep.also:
            Requirement(spec)


def test_ocr_installs_the_package_the_engine_imports_and_the_runtime_it_needs():
    from app.services import rapidocr_local

    entry = next(d for d in deps.CATALOG if d.import_name == rapidocr_local.REQUIRED_MODULES[0])
    assert entry.pip_name == "rapidocr"
    assert "rapidocr_onnxruntime" not in {d.import_name for d in deps.CATALOG}
    assert {canonicalize_name(Requirement(s).name) for s in entry.also} == {canonicalize_name(rapidocr_local.REQUIRED_MODULES[1])}
    # and the extra that `pip install -e '.[parse_pro]'` uses names the runtime too, instead of borrowing it from pymupdf-layout
    parse_pro = {canonicalize_name(r.name) for r in _extras()["parse_pro"]}
    assert {"rapidocr", "onnxruntime"} <= parse_pro


# ── nothing installable replaces a pin ───────────────────────────────────────


def test_patent_client_is_not_offered_by_the_one_click_installer():
    """Its releases require httpx<0.28 and pypdf<5: one click would replace the backend's httpx and pypdf."""
    assert "patent-client" not in {d.pip_name for d in deps.CATALOG}
    assert "semanticscholar" not in {d.pip_name for d in deps.CATALOG}, "no code imports the SDK (HTTP API only)"


def test_patent_client_is_declared_by_no_extra_and_there_is_no_patents_extra():
    """The EPO / USPTO search is EPO OPS over httpx now; the SDK would put httpx 0.27.2 and pypdf 4.3.1 back."""
    extras = _extras()
    holders = [n for n, reqs in extras.items() if any(canonicalize_name(r.name) == "patent-client" for r in reqs)]
    assert holders == [], holders
    assert "patents" not in extras
    assert "patent-client" not in {canonicalize_name(r.name) for r in _core()}


def test_the_dockerfile_installs_only_declared_extras():
    extras = _extras()
    installed = _dockerfile_extras()
    assert set(installed) <= set(extras), sorted(set(installed) - set(extras))
    assert "intel" in installed and "file_ingest" in installed  # not vacuous: the two that used to collide


def test_no_declared_requirement_excludes_a_pinned_version():
    """``pip install -r requirements.txt`` then ``pip install -e .[extra]`` must find every pin acceptable. A floor
    above a pin makes pip *upgrade* it behind requirements.txt's back; a cap below one makes pip downgrade it."""
    pins = _pins()
    problems = []
    for where, reqs in [("core", _core())] + [(f"extra {name}", reqs) for name, reqs in _extras().items()]:
        for req in reqs:
            pin = pins.get(canonicalize_name(req.name))
            if pin and not req.specifier.contains(pin, prereleases=True):
                problems.append(f"{where}: {req} excludes the pinned {req.name}=={pin}")
    assert not problems, "\n".join(problems)


def test_requirements_dev_does_not_repin_a_runtime_pin():
    """requirements-dev.txt includes requirements.txt (``-r``); a second pin of the same package makes
    ``pip install -r requirements-dev.txt`` fail with ResolutionImpossible - pypdf==6.14.0 here against 6.14.2 there did,
    once requirements.txt began pinning pypdf too, and nothing noticed because CI installs ``-e '.[dev]'`` instead."""
    dev = set()
    for raw in (BACKEND / "requirements-dev.txt").read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            dev.add(canonicalize_name(Requirement(line).name))
    assert dev, "requirements-dev.txt lost its test stack"
    assert not (dev & set(_pins())), sorted(dev & set(_pins()))


def test_the_minimum_security_floor_of_pypdf_is_declared_everywhere_it_is_used():
    """pypdf < 6.19.0 has published advisories (crafted-PDF denial of service). Every declaration that can install it
    must say so, or an editable install picks an older release - which then also satisfies a lower floor elsewhere."""
    floors = {}
    for where, reqs in [("core", _core())] + [(f"extra {name}", reqs) for name, reqs in _extras().items()]:
        for req in reqs:
            if canonicalize_name(req.name) == "pypdf":
                floors[where] = [s.version for s in req.specifier if s.operator == ">="]
    assert floors, "pypdf is no longer declared anywhere"
    assert all(v == ["6.19.0"] for v in floors.values()), floors
    assert _pins()["pypdf"] == "6.19.0"


# ── pin_constraints / install ────────────────────────────────────────────────


def test_pin_constraints_drops_comments_options_and_extras(tmp_path):
    path = tmp_path / "requirements.txt"
    path.write_text(
        "# comment\n-r other.txt\n\nuvicorn[standard]==0.49.0\ntenacity>=8.2.0   # why\nuvloop==0.22.1; sys_platform != 'win32'\n",
        encoding="utf-8",
    )
    assert deps.pin_constraints(path).splitlines() == [
        "uvicorn==0.49.0",
        "tenacity>=8.2.0",
        "uvloop==0.22.1; sys_platform != 'win32'",
    ]


def test_pin_constraints_is_none_without_a_requirements_file(tmp_path):
    assert deps.pin_constraints(tmp_path / "missing.txt") is None
    empty = tmp_path / "empty.txt"
    empty.write_text("# nothing\n", encoding="utf-8")
    assert deps.pin_constraints(empty) is None


def test_the_real_requirements_make_a_valid_constraints_file():
    text = deps.pin_constraints()
    assert text is not None
    names = set()
    for line in text.splitlines():
        names.add(canonicalize_name(Requirement(line).name))  # raises on anything pip would reject
        assert "[" not in line  # pip: "Constraints cannot have extras"
    assert {"httpx", "pypdf", "fastapi"} <= names


class _Pip:
    """Stands in for ``subprocess.run`` and records what pip would have been asked, including the constraints file."""

    def __init__(self, returncode: int = 0, stderr: str = "", raises: Exception | None = None):
        self.returncode, self.stderr, self.raises = returncode, stderr, raises
        self.args: list[str] = []
        self.constraints_path: str | None = None
        self.constraints: str | None = None

    def __call__(self, args, **kwargs):
        self.args = list(args)
        if "-c" in args:
            self.constraints_path = args[args.index("-c") + 1]
            self.constraints = Path(self.constraints_path).read_text(encoding="utf-8")
        if self.raises:
            raise self.raises
        return subprocess.CompletedProcess(args, self.returncode, "out", self.stderr)


def _install(monkeypatch, pip: _Pip, names: list[str], **kwargs) -> dict:
    monkeypatch.setattr(deps.subprocess, "run", pip)
    return deps.install(names, **kwargs)


def test_install_holds_pip_to_the_backends_pins(monkeypatch):
    pip = _Pip()
    result = _install(monkeypatch, pip, ["ddgs"])
    assert result["ok"] is True
    assert pip.args[-1] == "ddgs>=6.0"  # the extra's floor, not the bare name
    assert re.search(r"^httpx==", pip.constraints, re.M) and re.search(r"^pypdf==", pip.constraints, re.M)
    assert not Path(pip.constraints_path).exists(), "the temporary constraints file was left behind"


def test_install_constrains_an_upgrade_too(monkeypatch):
    pip = _Pip()
    _install(monkeypatch, pip, ["pypdf"], upgrade=True)
    assert "--upgrade" in pip.args and "-c" in pip.args


def test_install_adds_the_companion_specs_in_the_same_call(monkeypatch):
    pip = _Pip()
    _install(monkeypatch, pip, ["rapidocr"])
    assert pip.args[-2:] == ["rapidocr==3.9.2", "onnxruntime>=1.17"]


def test_the_layout_parser_is_installed_pinned(monkeypatch):
    pip = _Pip()
    _install(monkeypatch, pip, ["pymupdf4llm"])
    assert pip.args[-1] == "pymupdf4llm==1.28.0"


def test_a_conflict_with_the_pins_is_refused_and_says_why(monkeypatch):
    pip = _Pip(returncode=1, stderr="ERROR: ResolutionImpossible: for help visit https://pip.pypa.io/")
    result = _install(monkeypatch, pip, ["ddgs"])
    assert result["ok"] is False
    assert "冲突" in result["summary"] and "requirements.txt" in result["summary"]
    assert not Path(pip.constraints_path).exists()


def test_the_constraints_file_is_removed_when_pip_times_out(monkeypatch):
    pip = _Pip(raises=subprocess.TimeoutExpired(cmd="pip", timeout=1))
    result = _install(monkeypatch, pip, ["ddgs"])
    assert result["ok"] is False and "超时" in result["summary"]
    assert not Path(pip.constraints_path).exists()


def test_without_a_requirements_file_install_is_unconstrained_as_before(monkeypatch, tmp_path):
    monkeypatch.setattr(deps, "_REQUIREMENTS", tmp_path / "absent.txt")
    pip = _Pip()
    assert _install(monkeypatch, pip, ["ddgs"])["ok"] is True
    assert "-c" not in pip.args


def test_an_unrelated_failure_reports_pips_last_line(monkeypatch):
    pip = _Pip(returncode=1, stderr="line one\nERROR: No matching distribution found for ddgs")
    result = _install(monkeypatch, pip, ["ddgs"])
    assert result["summary"].endswith("No matching distribution found for ddgs")


# ── every probe names something this repository can install ──────────────────
#
# ``optional_import("x")`` is how the app decides what to offer: an upload format, a status badge, a "install this"
# hint. A probe for a module nothing here installs is a promise nobody can keep - ``magic_pdf`` (the retired local
# MinerU path) kept counting towards "a PDF parser exists", and ``chemcrow`` was probed - and reported "installed but
# incompatible" - long after no code imported it.

# import name -> the distribution that provides it
IMPORT_TO_DISTRIBUTION = {
    "anthropic": "anthropic",
    "colour": "colour-science",
    "ddgs": "ddgs",
    "docling": "docling",
    "docx": "python-docx",
    "httpx": "httpx",
    "mammoth": "mammoth",
    "marker": "marker-pdf",
    "markitdown": "markitdown",
    "mineru": "mineru-open-sdk",
    "molbloom": "molbloom",
    "onnxruntime": "onnxruntime",
    "openai": "openai",
    "openpyxl": "openpyxl",
    "paperqa": "paper-qa",
    "pdfminer": "pdfminer.six",
    "pdfplumber": "pdfplumber",
    "pptx": "python-pptx",
    "psycopg2": "psycopg2-binary",
    "pubchempy": "pubchempy",
    "pymupdf4llm": "pymupdf4llm",
    "pypdf": "pypdf",
    "rapidocr": "rapidocr",
    "rdkit": "rdkit",
    "sentence_transformers": "sentence-transformers",
    "trafilatura": "trafilatura",
}

# Probed on purpose although nothing in this repository installs it - each with the reason.
PROBED_BUT_NOT_INSTALLABLE = {
    "duckduckgo_search": "the pre-rename name of ddgs: literature.search_web falls back to it for environments that "
    "still have it; the extra installs ddgs",
}

# Distributions that arrive with ``markitdown[pdf,docx,pptx,xlsx]`` rather than from a line of their own.
PROVIDED_BY_MARKITDOWN_EXTRAS = {"pdfminer.six", "pdfplumber", "mammoth"}

# Probes that were retired with their integrations: they must not come back.
RETIRED_PROBES = {
    "magic_pdf": "the local MinerU path, never installed in this deployment (the cloud SDK is `mineru`)",
    "chemcrow": "removed 2026-09; the status key is a fixed deprecated payload",
    "patent_client": "removed 2026-10; the patent search is EPO OPS over httpx",
    "semanticscholar": "Semantic Scholar is queried over HTTP",
    "rapidocr_onnxruntime": "the engine imports rapidocr 3.x",
}


def _probed_names() -> dict[str, list[str]]:
    """Every literal module name given to ``optional_import`` / the local ``_ok`` helpers, plus the module tuples the
    parsers keep (MarkItDown's converters, the OCR runtime)."""
    from app.services import parsing, rapidocr_local

    found: dict[str, list[str]] = {}
    for path in sorted((BACKEND / "app").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) in {"optional_import", "_ok"}:
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        found.setdefault(arg.value, []).append(f"{path.relative_to(BACKEND)}:{node.lineno}")
    for modules in parsing._MARKITDOWN_BACKENDS.values():
        for module in modules:
            found.setdefault(module, []).append("app/services/parsing.py:_MARKITDOWN_BACKENDS")
    for module in rapidocr_local.REQUIRED_MODULES:
        found.setdefault(module, []).append("app/services/rapidocr_local.py:REQUIRED_MODULES")
    return found


def _installable_distributions() -> set[str]:
    out = {canonicalize_name(r.name) for r in _core()}
    out |= {canonicalize_name(r.name) for reqs in _extras().values() for r in reqs}
    out |= {canonicalize_name(d.pip_name) for d in deps.CATALOG}
    out |= {canonicalize_name(Requirement(spec).name) for d in deps.CATALOG for spec in d.also}
    out |= set(_pins())
    return out


def test_every_probed_module_is_installable_from_this_repository():
    installable = _installable_distributions() | {canonicalize_name(n) for n in PROVIDED_BY_MARKITDOWN_EXTRAS}
    problems = []
    for module, where in sorted(_probed_names().items()):
        if module in PROBED_BUT_NOT_INSTALLABLE:
            continue
        dist = IMPORT_TO_DISTRIBUTION.get(module)
        if dist is None:
            problems.append(f"{module} ({where[0]}): add it to IMPORT_TO_DISTRIBUTION, or to PROBED_BUT_NOT_INSTALLABLE with a reason")
        elif canonicalize_name(dist) not in installable:
            problems.append(f"{module} ({where[0]}): nothing installs {dist!r} - an extra, a catalog entry or a pin must")
    assert not problems, "\n".join(problems)


def test_the_probe_allowlists_are_not_stale():
    probed = _probed_names()
    assert set(PROBED_BUT_NOT_INSTALLABLE) <= set(probed), "an allowlisted probe is gone: drop it from the allowlist"
    installable = _installable_distributions()
    for module in PROBED_BUT_NOT_INSTALLABLE:
        dist = IMPORT_TO_DISTRIBUTION.get(module)
        assert dist is None or canonicalize_name(dist) not in installable, f"{module} became installable: drop the exemption"
    assert not set(IMPORT_TO_DISTRIBUTION) - set(probed), sorted(set(IMPORT_TO_DISTRIBUTION) - set(probed))


def test_markitdown_brings_the_backends_the_probes_rely_on():
    markitdown = next(r for r in _extras()["file_ingest"] if canonicalize_name(r.name) == "markitdown")
    assert {"pdf", "docx", "pptx", "xlsx"} <= markitdown.extras


def test_retired_integrations_are_not_probed():
    probed = set(_probed_names())
    assert not (set(RETIRED_PROBES) & probed), {name: RETIRED_PROBES[name] for name in set(RETIRED_PROBES) & probed}


def test_the_scan_finds_the_probes_it_exists_for(tmp_path):
    """Not vacuous: it sees both spellings."""
    names = _probed_names()
    assert "pymupdf4llm" in names and "mineru" in names  # optional_import(...)
    assert "ddgs" in names  # the local _ok(...) helper in literature.get_source_availability
    assert "pdfminer" in names and "onnxruntime" in names  # the tuples the parsers keep
