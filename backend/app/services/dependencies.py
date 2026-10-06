"""Optional-dependency catalog + runtime install / upgrade management.

FormuMind runs on a small hard-dependency core; every advanced capability
(LLM providers, online retrieval, embeddings, optimizers, colorimetry, file
ingestion…) lives behind an optional *extra* and degrades gracefully when
absent. This module lets the Settings UI introspect what is installed and
install / upgrade the missing pieces on the running machine via ``pip``.

Security: install / upgrade only ever operate on packages declared in
``CATALOG`` — arbitrary user-supplied names are rejected (``validate_names``) —
so the endpoints cannot be abused to pull in unknown code. The exact pip spec
(including version pins / extras) is owned here, never taken from the request.
"""
from __future__ import annotations

import functools
import logging
from .errors import degrade_return, log_handled_exception
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from importlib import metadata, util
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Dependency:
    pip_name: str       # canonical distribution name (and default install spec)
    import_name: str    # module imported to verify presence
    extra: str          # the [extra] group it belongs to
    enables: str        # human-readable capability it unlocks
    spec: str = ""      # explicit pip install spec; falls back to pip_name
    also: tuple[str, ...] = ()  # companion specs installed in the same pip call (a runtime the package needs but does not declare)

    @property
    def install_spec(self) -> str:
        # The extra's own requirement (floor / pin / extras) when pyproject.toml is shipped: a bare name lets pip
        # settle a conflict with the backend's pins by backtracking to an ancient release of *this* package - measured
        # with the since-removed patent-client: ``pip install -c <pins> patent-client`` quietly installs 3.2.6 instead
        # of failing.
        return self.spec or _declared_requirements().get((self.extra, _canonical(self.pip_name))) or self.pip_name


_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _canonical(name: str) -> str:
    """PEP 503 normalisation (``ChemFormula`` == ``chemformula``, ``paper_qa`` == ``paper-qa``)."""
    return re.sub(r"[-_.]+", "-", name).lower()


@functools.lru_cache(maxsize=1)
def _declared_requirements() -> dict[tuple[str, str], str]:
    """{(extra, canonical name): requirement string} from pyproject.toml; empty when it is not shipped."""
    try:
        data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    declared: dict[tuple[str, str], str] = {}
    for extra, requirements in (data.get("project", {}).get("optional-dependencies") or {}).items():
        for raw in requirements:
            match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", raw)
            if match:
                declared[(extra, _canonical(match.group(1)))] = raw.strip()
    return declared


# Curated set: the practical, CPU-friendly extras that unlock "online mode".
# The truly heavy `heavy` extra (torch/deepchem/transformers/ase) is
# intentionally omitted from the one-click UI to avoid multi-GB surprise pulls;
# `bo` (botorch/gpytorch) already pulls a CPU torch for users who opt in.
CATALOG: tuple[Dependency, ...] = (
    # ── LLM providers ──────────────────────────────────────────────────────
    Dependency("anthropic", "anthropic", "llm", "Claude 大模型问答 / 综述"),
    Dependency("openai", "openai", "llm", "OpenAI 及兼容供应商（DeepSeek/Qwen/Grok/Kimi…）"),
    Dependency("google-generativeai", "google.generativeai", "llm", "Google Gemini 大模型"),
    # ── Online retrieval (the offline-mode pain point) ─────────────────────
    # Deliberately absent: patent-client (USPTO/EPO SDK), removed from the project. Every release requires httpx<0.28
    # and pypdf<5.0, so installing it would replace the backend's pinned httpx and pypdf (49 advisories on the pypdf it
    # forces). The patent search is EPO OPS over plain httpx (services/epo_ops.py): credentials, nothing to install.
    # Also absent: the `semanticscholar` SDK - search_semantic_scholar() calls the HTTP API directly (no SDK to hang),
    # so installing it changed nothing but a status probe.
    Dependency("ddgs", "ddgs", "intel", "DuckDuckGo 互联网检索"),
    Dependency("paper-qa", "paperqa", "intel", "paper-qa 语义 RAG 文献综合"),
    Dependency("molbloom", "molbloom", "intel", "molbloom 分子专利预筛（SureChEMBL 布隆过滤器）"),
    Dependency("pubchempy", "pubchempy", "intel", "PubChem 原料 SMILES / 分子量富集"),
    # ── Embedding RAG ──────────────────────────────────────────────────────
    Dependency(
        "sentence-transformers", "sentence_transformers", "embedding",
        "语义向量检索（离线 RAG 质量升级）",
    ),
    Dependency(
        "ragatouille", "ragatouille", "colbert",
        "ColBERT 检索后端（需要 torch；GPU 或 CPU 均可）",
    ),
    Dependency(
        "langgraph", "langgraph", "crag",
        "CRAG 联邦检索编排（LangGraph StateGraph）",
    ),
    # ── Science ────────────────────────────────────────────────────────────
    Dependency("rdkit", "rdkit", "science", "RDKit 分子描述符 / SMARTS 相容性校验"),
    Dependency("scipy", "scipy", "science", "科学计算（曲线拟合等）"),
    Dependency("scikit-learn", "sklearn", "science", "数据驱动代理模型训练"),
    Dependency("thermo", "thermo", "science", "物性估算（密度 → VOC g/L 接地）"),
    Dependency("ChemFormula", "chemformula", "science", "化学式解析与校验"),
    # ── Optimizers ─────────────────────────────────────────────────────────
    # Honesty (P1 #21): code uses TPESampler on a scalar objective — not NSGA-II.
    # True Pareto multi-objective lives on the BayBE path (qNEHVI / ParetoObjective).
    Dependency("optuna", "optuna", "optimize", "Optuna TPE 标量寻优（非 NSGA-II）"),
    Dependency(
        "botorch",
        "botorch",
        "bo",
        "BoTorch GP + LogEI 标量寻优（非 Pareto；多目标见 BayBE）",
    ),
    Dependency("gpytorch", "gpytorch", "bo", "BoTorch GP 内核依赖"),
    # ── Color ──────────────────────────────────────────────────────────────
    Dependency("colour-science", "colour", "color", "CIELAB / ΔE₀₀ 色差计算"),
    # ── File ingestion ─────────────────────────────────────────────────────
    Dependency(
        "markitdown", "markitdown", "file_ingest", "通用文档解析（PDF/DOCX/XLSX/PPTX…）",
        # Without the extras MarkItDown installs no format backend at all: the
        # description above would be false, .pptx/.xlsx would parse to nothing,
        # and .docx would fall through to python-docx and lose its tables.
        spec="markitdown[pdf,docx,pptx,xlsx]>=0.1",
    ),
    Dependency("pypdf", "pypdf", "file_ingest", "PDF 解析回退"),
    Dependency("python-docx", "docx", "file_ingest", "DOCX 解析回退"),
    Dependency("trafilatura", "trafilatura", "file_ingest", "网页正文抽取（去导航/广告 → Markdown）"),
    Dependency(
        "mineru-open-sdk", "mineru", "parse_pro",
        "MinerU 云端解析（难页升级：密集表格/公式/图表；需 Token）",
    ),
    # The engine (rapidocr_local) imports ``rapidocr`` 3.x - not the retired ``rapidocr-onnxruntime`` this entry used
    # to install, which made the one-click install add a package nothing imports while the availability report said
    # OCR was ready. 3.x ships no inference runtime: it imports fine and then fails to build an engine, hence ``also``.
    Dependency(
        "rapidocr", "rapidocr", "parse_pro",
        "本地 OCR 扫描件（ONNX Runtime，纯 CPU；中文 PP-OCRv6 模型随包分发，无需下载）",
        spec="rapidocr==3.9.2",
        also=("onnxruntime>=1.17",),
    ),
    # Pinned like the extra: 1.28.2 rewrites OCR backend selection and breaks the layout path (see pyproject.toml).
    Dependency(
        "pymupdf4llm", "pymupdf4llm", "parse_pro",
        "本地版面感知 PDF → Markdown（极快、无模型权重，CPU 首选；⚠️ AGPL-3.0）",
        spec="pymupdf4llm==1.28.0",
    ),
    Dependency(
        "docling", "docling", "parse_pro",
        "Docling 版面/表格感知 PDF → Markdown（IBM，公式→LaTeX，需 torch）",
    ),
    Dependency(
        "marker-pdf", "marker", "parse_pro",
        "marker 版面感知 PDF → Markdown（表格保真，重型）",
    ),
    # ── Export ─────────────────────────────────────────────────────────────
    Dependency("openpyxl", "openpyxl", "export", "DOE / 结果 Excel 导出"),
    Dependency("pydoe", "pydoe", "pydoe", "pyDOE 经典实验设计（LHS/CCD/混合物/Sobol）"),
    Dependency("baybe", "baybe", "baybe", "BayBE 约束贝叶斯主动学习 Campaign"),
    Dependency("pandas", "pandas", "baybe", "BayBE 测量数据 DataFrame 依赖"),
    # ── NotebookLM ─────────────────────────────────────────────────────────
    Dependency(
        "notebooklm-py", "notebooklm", "notebooklm", "NotebookLM 资料来源（非官方 SDK + 浏览器）",
        spec="notebooklm-py[browser]>=0.1",
    ),
)

# Packages that make "online mode" work end-to-end — the one-click target.
ONLINE_CORE_EXTRAS = ("llm", "intel")

_BY_PIP = {d.pip_name: d for d in CATALOG}

_REQUIREMENTS = Path(__file__).resolve().parents[2] / "requirements.txt"
_EXTRAS_IN_SPEC = re.compile(r"\[[^\]]*\]")
_COMMENT = re.compile(r"(^|\s)#.*$")


def pin_constraints(path: Path | None = None) -> str | None:
    """``requirements.txt`` as a pip *constraints* file, or None when it is not shipped next to the package.

    Without one, ``pip install <extra package>`` resolves a conflict with the backend's pins by *replacing* the pinned
    package: ``patent-client`` (httpx<0.28, pypdf<5) silently swapped httpx 0.28.1 and pypdf 6.x for 0.27.2 and 4.3.1.
    Under a constraint the pinned package cannot move: pip either picks a release of the *requested* package that fits
    (which is why ``install_spec`` carries the extra's floor) or refuses with ``ResolutionImpossible``, which is what
    an install from a UI button should do. Extras are stripped because pip rejects them in a constraints file.
    """
    try:
        text = (path or _REQUIREMENTS).read_text(encoding="utf-8")
    except OSError:
        return None
    lines = []
    for raw in text.splitlines():
        line = _COMMENT.sub("", raw).strip()
        if not line or line.startswith("-"):
            continue
        lines.append(_EXTRAS_IN_SPEC.sub("", line))
    return "\n".join(lines) + "\n" if lines else None


def _is_installed(import_name: str) -> bool:
    """True if the module can be located, without executing its top-level code."""
    try:
        return util.find_spec(import_name) is not None
    except Exception as exc:
        log_handled_exception(logger, exc, "optional feature check")
        return False


def _installed_version(dist_name: str) -> str | None:
    try:
        return metadata.version(dist_name)
    except Exception as exc:
        return degrade_return(logger, exc, "operation failed", None)


def status() -> list[dict]:
    """Current install state of every catalogued optional dependency."""
    out: list[dict] = []
    for dep in CATALOG:
        installed = _is_installed(dep.import_name)
        out.append(
            {
                "pip_name": dep.pip_name,
                "import_name": dep.import_name,
                "extra": dep.extra,
                "enables": dep.enables,
                "installed": installed,
                "version": _installed_version(dep.pip_name) if installed else None,
            }
        )
    return out


def validate_names(names: list[str]) -> None:
    """Reject any name not in the catalog allowlist (raises ValueError)."""
    unknown = [n for n in names if n not in _BY_PIP]
    if unknown:
        raise ValueError(f"Unknown dependency name(s): {', '.join(unknown)}")


def online_core_missing() -> list[str]:
    """pip names of not-yet-installed packages needed for online mode."""
    return [
        d.pip_name
        for d in CATALOG
        if d.extra in ONLINE_CORE_EXTRAS and not _is_installed(d.import_name)
    ]


def install(names: list[str], upgrade: bool = False, timeout: int = 1800) -> dict:
    """pip-install (or --upgrade) the given catalogued packages.

    Names are validated against the allowlist; the actual pip specs come from
    the catalog, never from the caller. The backend's own pins (``requirements.txt``) are passed as constraints, so
    an install that would need a different version of one of them fails instead of replacing it. Returns a
    JSON-serialisable result with a short summary plus the (truncated) pip log for display.
    """
    validate_names(names)
    if not names:
        return {"ok": False, "summary": "未选择任何依赖", "stdout": "", "stderr": ""}

    deps = [_BY_PIP[n] for n in names]
    specs = [d.install_spec for d in deps]
    for dep in deps:
        specs.extend(extra for extra in dep.also if extra not in specs)
    args = [sys.executable, "-m", "pip", "install"]
    if upgrade:
        args.append("--upgrade")

    pins = pin_constraints()
    pins_path: str | None = None
    try:
        if pins:
            fd, pins_path = tempfile.mkstemp(prefix="formumind-pins-", suffix=".txt")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(pins)
            args += ["-c", pins_path]
        args += specs

        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "summary": f"安装超时（>{timeout}s）：{', '.join(n for n in names)}",
                "stdout": "",
                "stderr": "pip install timed out",
            }
        except Exception as exc:  # pragma: no cover - environment-dependent
            return {"ok": False, "summary": f"安装失败：{exc}", "stdout": "", "stderr": str(exc)}
    finally:
        if pins_path:
            try:
                os.remove(pins_path)
            except OSError:
                pass

    ok = proc.returncode == 0
    verb = "更新" if upgrade else "安装"
    if ok:
        summary = f"已{verb} {', '.join(n for n in names)}（成功）。请重启后端使新依赖生效。"
    else:
        output = proc.stderr or proc.stdout or ""
        if pins_path and "ResolutionImpossible" in output:
            summary = (
                f"{verb}失败：与后端固定的依赖版本（requirements.txt）冲突，已拒绝——"
                "否则 pip 会把固定的包静默降级。详见日志。"
            )
        else:
            tail = output.strip().splitlines()[-1:] or [""]
            summary = f"{verb}失败：{tail[0][:200]}"
    return {
        "ok": ok,
        "returncode": proc.returncode,
        "summary": summary,
        "stdout": (proc.stdout or "")[-4000:],
        "stderr": (proc.stderr or "")[-4000:],
    }
