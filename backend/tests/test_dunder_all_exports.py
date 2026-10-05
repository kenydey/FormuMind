"""Every name a module lists in ``__all__`` must exist in it (round-4).

``app/evals/__init__.py`` declared ``__all__ = ["evaluate_rigor"]`` without ever importing it, so
``from app.evals import *`` raised AttributeError. Nothing star-imports it today — which is exactly why it went
unnoticed. AST-only (no imports), so it also covers modules whose optional dependencies are absent.
"""
from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"


def _defined_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        for sub in ast.walk(node) if isinstance(node, (ast.If, ast.Try, ast.With)) else [node]:
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(sub.name)
            elif isinstance(sub, ast.Import):
                names.update((a.asname or a.name).split(".")[0] for a in sub.names)
            elif isinstance(sub, ast.ImportFrom):
                names.update(a.asname or a.name for a in sub.names)
            elif isinstance(sub, (ast.Assign, ast.AnnAssign)):
                targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
                for target in targets:
                    for leaf in ast.walk(target):
                        if isinstance(leaf, ast.Name):
                            names.add(leaf.id)
    return names


def _declared_all(tree: ast.Module) -> list[str] | None:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            if isinstance(node.value, (ast.List, ast.Tuple)):
                return [e.value for e in node.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return None


def test_every_name_in_dunder_all_is_defined_in_its_module():
    problems = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        declared = _declared_all(tree)
        if not declared:
            continue
        missing = sorted(set(declared) - _defined_names(tree))
        # a package may re-export a submodule that it does not import explicitly
        if path.name == "__init__.py":
            missing = [m for m in missing if not (path.parent / f"{m}.py").exists() and not (path.parent / m).is_dir()]
        if missing:
            problems.append(f"{path.relative_to(APP.parent)}: {missing}")
    assert not problems, "\n".join(problems)
