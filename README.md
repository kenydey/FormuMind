# FormuMind

**AI-assisted formulation R&D platform for metal surface treatment** — covering
anti-corrosion coatings (防腐蚀涂料), degreasers (脱脂剂), and surface treatment
agents (表面处理剂).

FormuMind takes an R&D requirement (substrate, salt-spray target, film weight,
cure temperature, cleaning efficiency, VOC limit…) and runs a closed loop:

```
requirement → patent/literature retrieval → RAG-grounded research
            → recommended formulations → DOE plan → cure/interface simulation
            → Bayesian closed-loop optimization → Top-N formula leaderboard
                          ↑                                  │
                          └──── DOE experimental results ◄───┘
                                (train data-driven models)
```

The goal is to raise formulation success rate and shorten the development cycle.
Measured DOE/lab results are fed back to **train data-driven prediction models**
that progressively supersede the empirical surrogate, so recommendations and
optimization improve as real data accumulates.

## Architecture

A lightweight "glue" monorepo: FormuMind owns the orchestration, domain
knowledge, and UI; heavy AI/physics engines are pulled in as **optional**
dependencies or Docker images and can auto-upgrade with the ecosystem.

| Layer | Tech | In this repo |
|-------|------|--------------|
| Frontend | Vite + React + TypeScript + Tailwind + Zustand | 3-pane dark industrial UI (`frontend/`) |
| Gateway | FastAPI | research / DOE / optimize / tasks routers (`backend/app/api`) |
| Async | Celery + Redis | optimization & ingestion tasks, in-process fallback (`backend/app/worker`) |
| Domain | Pure Python | schemas, knowledge base, **real DOE engine**, stoichiometry (`backend/app/domain`) |
| Services | Adapter + fallback | LLM, literature, RAG, predictor, optimizer, simulator (`backend/app/services`) |

### Adapter + fallback design

Every external engine sits behind an adapter with a **deterministic offline
fallback**, so the platform runs end-to-end today — no GPU, API key, or C++
toolchain required — and "lights up" the real engine when it is installed:

| Capability | Real engine (optional) | Built-in fallback |
|------------|------------------------|-------------------|
| Research / chat | Anthropic Claude (`anthropic`) | rule-based synthesis over the knowledge base |
| Patent/literature | `patent_client`, `paper-qa` | curated seed corpus per domain |
| RAG store | OpenNotebook pipeline | in-memory TF-IDF index |
| Property prediction | RDKit + DeepChem/ChemBERTa | transparent empirical surrogate |
| Optimization | Summit (Bayesian/TSEMO) | numpy UCB Bayesian optimizer |
| Stoichiometry | ChemFormula / RDKit | self-contained formula parser |
| Cure/MD simulation | HTPolyNet · LUNAR · LAMMPS (Docker) | analytic cure/interface approximation |

The genuinely lightweight, high-value parts — **the DOE engine** (full /
fractional factorial, Plackett-Burman, central composite, Latin hypercube) and
the **Bayesian optimizer** — are implemented for real in pure numpy.

## Quick start

### Local (no Docker, fully offline)

```bash
# Backend
cd backend
pip install -e ".[dev]"
pytest -q                          # 47 tests, all offline
uvicorn app.main:app --reload      # http://localhost:8000/docs

# Frontend (separate shell)
cd frontend
npm install
npm run dev                        # http://localhost:5173
```

### Docker

```bash
cp .env.example .env               # optional: add ANTHROPIC_API_KEY
docker compose up                  # redis + backend + worker + frontend
docker compose --profile heavy up  # also start LAMMPS / HTPolyNet engines
```

## API

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/research` | retrieve prior art + RAG + recommended formulations |
| POST | `/api/doe?design=…` | generate a DOE plan (`full_factorial`, `fractional_factorial`, `plackett_burman`, `ccd`, `lhs`) |
| GET | `/api/doe/{plan_id}/export?format=csv\|xlsx` | export a generated plan as a fill-in worksheet (blank `measured_*` columns) |
| POST | `/api/optimize` | start the async **multi-objective** closed-loop optimizer → returns `task_id` |
| GET | `/api/tasks/{id}` | poll task progress + result (Top-N leaderboard) |
| POST | `/api/experiments` | feed back measured DOE/lab results → persist + (re)train models |
| POST | `/api/experiments/import-csv` | upload a filled-in worksheet → bulk-ingest results + (re)train |
| POST | `/api/train` | force a retrain over all stored experiments |
| GET | `/api/models` | list trained models with `n_samples`, `R²`, `cv_R²`, `RMSE` |
| GET | `/api/ingredients` | full raw-material library incl. price (CNY/kg) & VOC contribution |
| GET | `/api/meta`, `/api/templates/{domain}` | metadata & baseline templates |
| GET | `/health` | service + active-engine status |

## DOE feedback & model training (回灌)

The empirical predictor is only a prior. As you run the DOE plans the platform
proposes and measure real performance, post the results back:

```bash
curl -X POST localhost:8000/api/experiments -H 'content-type: application/json' -d '{
  "records": [
    {"domain": "anticorrosion_coating",
     "factors": {"Zinc phosphate": 12, "Bisphenol-A epoxy (DGEBA)": 38, "Polyamide hardener": 14},
     "cure_temperature_c": 80,
     "measured": {"salt_spray_hours": 980}}
  ]
}'
```

Each record is featurized (`backend/app/domain/features.py`) into a role-based
composition + process vector. Once a metric has at least
`FORMUMIND_MIN_TRAIN_SAMPLES` (default 4) samples, a model is trained per
`(domain, metric)` — scikit-learn `RandomForestRegressor` when installed,
otherwise a self-contained numpy ridge regressor. `predictor.predict` then
**blends** the trained model with the empirical prior, weighting the model more
as samples accumulate (`w = n / (n + 8)`), so optimization and recommendations
shift toward measured reality. The dataset is the source of truth: models are
rebuilt from the persisted experiments on startup (no model binaries stored).

## Enabling the real engines

Install the optional extras on a capable machine and the adapters switch over
automatically — no code change:

```bash
pip install -e ".[llm]"      # Anthropic Claude
pip install -e ".[science]"  # scipy, scikit-learn, RDKit, ChemFormula
pip install -e ".[intel]"    # patent_client, paper-qa, chemcrow
pip install -e ".[heavy]"    # torch, deepchem, transformers, summit, ase
pip install -e ".[export]"   # openpyxl (XLSX DOE worksheet export; CSV needs nothing)
```

> The performance numbers produced offline are engineering-reasonable estimates
> for screening, not laboratory-validated specifications.
