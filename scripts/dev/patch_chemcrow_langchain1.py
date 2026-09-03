#!/usr/bin/env python3
"""chemcrow 0.3.7 → langchain 1.x 就地移植补丁（幂等，可重复执行）。

背景：chemcrow 停在 0.3.x（PyPI 最新 0.3.24 仍 pin langchain<=0.0.275），
与 venv 的 langchain 1.3.14 / pydantic 2.13 架构性不兼容：
  - 无注解类属性覆盖 BaseTool 字段（pydantic 2.13 报 model-field-overridden）
  - langchain 0.x 删除的模块（langchain.llms / base_language / chains.LLMChain / callbacks）
平台只用 chemcrow.tools（chemtools._chemcrow_tool），agents/frontend 面裁剪掉。

用法：backend/.venv/bin/python scripts/dev/patch_chemcrow_langchain1.py
venv 重建后重跑本脚本即可恢复。
"""
import re
import sys
from pathlib import Path

VENV = Path(sys.prefix)
CC = VENV / "lib" / "python3.11" / "site-packages" / "chemcrow"
assert CC.is_dir(), f"未找到 chemcrow: {CC}"


def patch_file(rel: str, subs: list[tuple[str, str]]) -> None:
    p = CC / rel
    src = p.read_text()
    for old, new in subs:
        if old not in src:
            print(f"  [SKIP] {rel}: 已修复或不存在 — {old[:44]!r}")
            continue
        src = src.replace(old, new)
        print(f"  [OK]   {rel}: {old[:44]!r}")
    p.write_text(src)


print("== 1/4 pydantic v2 无注解覆盖 -> 补 : str ==")
for rel in ("tools/databases.py", "tools/safety.py", "tools/search.py", "tools/rdkit.py", "tools/rxn4chem.py"):
    p = CC / rel
    src = p.read_text()
    new = re.sub(r"^([ \t]+)(name|description) = ", r"\1\2: str = ", src, flags=re.M)
    if new != src:
        p.write_text(new)
        print(f"  [OK] {rel}: name/description 注解补齐")
    else:
        print(f"  [SKIP] {rel}: 已修复")

print("== 2/4 langchain 0.x import 路径 -> 1.x ==")
patch_file("tools/search.py", [
    ("from langchain import SerpAPIWrapper", "from langchain_community.utilities import SerpAPIWrapper"),
    ("from langchain.base_language import BaseLanguageModel", "from langchain_core.language_models import BaseLanguageModel"),
    ("from langchain.chains import LLMChain", "from langchain_classic.chains import LLMChain"),
])
patch_file("tools/safety.py", [
    ("from langchain import LLMChain, PromptTemplate", "from langchain_classic.chains import LLMChain\nfrom langchain_core.prompts import PromptTemplate"),
    ("from langchain.llms import OpenAI, BaseLLM", "from langchain_community.llms import OpenAI\nfrom langchain_core.language_models.llms import BaseLLM"),
])

print("== 3/4 顶层 __init__ 裁剪（去掉 streamlit/agents 面）==")
init = CC / "__init__.py"
src = init.read_text()
if "frontend" in src or "agents" in src:
    init.write_text(
        "# FormuMind 裁剪版：只暴露 tools 面（frontend/agents 依赖 streamlit 与\n"
        "# langchain 0.x 删除的 callbacks，与 langchain 1.x 不兼容且平台不需要）。\n"
        "from .tools.databases import *\n"
        "from .tools.rdkit import *\n"
        "from .tools.search import *\n"
        "from .version import __version__\n"
    )
    print("  [OK] __init__.py 已裁剪")
else:
    print("  [SKIP] __init__.py 已裁剪")

print("== 4/4 验证 ==")
import chemcrow.tools as cct  # noqa: E402
ok = 0
for name in ("Query2SMILES", "Query2CAS", "FuncGroups", "PatentCheck", "ExplosiveCheck"):
    cls = getattr(cct, name, None)
    assert cls is not None, f"工具类缺失: {name}"
    cls()
    ok += 1
print(f"  [OK] chemcrow.tools import + {ok} 个工具类实例化成功")
