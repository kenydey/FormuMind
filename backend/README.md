# FormuMind Backend

FastAPI + Celery gateway for the FormuMind formulation R&D platform.

```bash
pip install -e ".[dev]"      # core + test deps
pytest -q                    # run the test suite (fully offline)
uvicorn app.main:app --reload
```

Optional engines (LLM, scientific, heavy MD) are declared as extras in
`pyproject.toml` and are not required to run — every service falls back to a
deterministic offline implementation. See the repository root `README.md` for
the full architecture.
