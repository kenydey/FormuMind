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

import logging
from .errors import degrade_return, log_handled_exception, optional_import, reraise_if_fatal
import subprocess
import sys
from dataclasses import dataclass
from importlib import metadata, util

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Dependency:
    pip_name: str       # canonical distribution name (and default install spec)
    import_name: str    # module imported to verify presence
    extra: str          # the [extra] group it belongs to
    enables: str        # human-readable capability it unlocks
    spec: str = ""      # explicit pip install spec; falls back to pip_name

    @property
    def install_spec(self) -> str:
        return self.spec or self.pip_name


# Curated set: the practical, CPU-friendly extras that unlock "online mode".
# The truly heavy `heavy` extra (torch/deepchem/transformers/summit/ase) is
# intentionally omitted from the one-click UI to avoid multi-GB surprise pulls;
# `bo` (botorch/gpytorch) already pulls a CPU torch for users who opt in.
CATALOG: tuple[Dependency, ...] = (
    # ── LLM providers ──────────────────────────────────────────────────────
    Dependency("anthropic", "anthropic", "llm", "Claude 大模型问答 / 综述"),
    Dependency("openai", "openai", "llm", "OpenAI 及兼容供应商（DeepSeek/Qwen/Grok/Kimi…）"),
    Dependency("google-genai", "google.genai", "llm", "Google Gemini 大模型"),
    # ── Online retrieval (the offline-mode pain point) ─────────────────────
    Dependency("patent-client", "patent_client", "intel", "USPTO/EPO 真实专利检索"),
    Dependency("arxiv", "arxiv", "intel", "arXiv 学术文献检索"),
    Dependency("semanticscholar", "semanticscholar", "intel", "Semantic Scholar 文献检索"),
    Dependency("ddgs", "ddgs", "intel", "DuckDuckGo 互联网检索"),
    Dependency("paper-qa", "paperqa", "intel", "paper-qa 语义 RAG 文献综合"),
    Dependency(
        "chemcrow", "chemcrow", "intel", "ChemCrow 化学增强检索 / 工具链",
        spec="chemcrow>=0.3.7",  # versions <0.3.7 pin openai==0.27.8, conflicts with openai>=1.30
    ),
    Dependency("molbloom", "molbloom", "intel", "molbloom 分子专利预筛（SureChEMBL 布隆过滤器，无需 ChemCrow）"),
    Dependency("pubchempy", "pubchempy", "intel", "PubChem 原料 SMILES / 分子量富集"),
    # ── Embedding RAG ──────────────────────────────────────────────────────
    Dependency(
        "sentence-transformers", "sentence_transformers", "embedding",
        "语义向量检索（离线 RAG 质量升级）",
    ),
    # ── Science ────────────────────────────────────────────────────────────
    Dependency("rdkit", "rdkit", "science", "RDKit 分子描述符 / SMARTS 相容性校验"),
    Dependency("scipy", "scipy", "science", "科学计算（曲线拟合等）"),
    Dependency("scikit-learn", "sklearn", "science", "数据驱动代理模型训练"),
    Dependency("thermo", "thermo", "science", "物性估算（密度 → VOC g/L 接地）"),
    Dependency("ChemFormula", "chemformula", "science", "化学式解析与校验"),
    # ── Optimizers ─────────────────────────────────────────────────────────
    Dependency("optuna", "optuna", "optimize", "Optuna NSGA-II 多目标寻优"),
    Dependency("botorch", "botorch", "bo", "BoTorch 高斯过程贝叶斯寻优（含 CPU torch）"),
    Dependency("gpytorch", "gpytorch", "bo", "BoTorch GP 内核依赖"),
    # ── Color ──────────────────────────────────────────────────────────────
    Dependency("colour-science", "colour", "color", "CIELAB / ΔE₀₀ 色差计算"),
    # ── File ingestion ─────────────────────────────────────────────────────
    Dependency("markitdown", "markitdown", "file_ingest", "通用文档解析（PDF/DOCX/XLSX/PPTX…）"),
    Dependency("pypdf", "pypdf", "file_ingest", "PDF 解析回退"),
    Dependency("python-docx", "docx", "file_ingest", "DOCX 解析回退"),
    Dependency("trafilatura", "trafilatura", "file_ingest", "网页正文抽取（去导航/广告 → Markdown）"),
    Dependency(
        "docling", "docling", "parse_pro",
        "Docling 版面/表格感知 PDF → Markdown（IBM，公式→LaTeX，CPU 友好）",
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
    the catalog, never from the caller. Returns a JSON-serialisable result with
    a short summary plus the (truncated) pip log for display.
    """
    validate_names(names)
    if not names:
        return {"ok": False, "summary": "未选择任何依赖", "stdout": "", "stderr": ""}

    deps = [_BY_PIP[n] for n in names]
    specs = [d.install_spec for d in deps]
    args = [sys.executable, "-m", "pip", "install"]
    if upgrade:
        args.append("--upgrade")
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

    ok = proc.returncode == 0
    verb = "更新" if upgrade else "安装"
    if ok:
        summary = f"已{verb} {', '.join(n for n in names)}（成功）。请重启后端使新依赖生效。"
    else:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or [""]
        summary = f"{verb}失败：{tail[0][:200]}"
    return {
        "ok": ok,
        "returncode": proc.returncode,
        "summary": summary,
        "stdout": (proc.stdout or "")[-4000:],
        "stderr": (proc.stderr or "")[-4000:],
    }
