# FormuMind Quick Start (5 minutes)

Run a complete formulation R&D loop in 5 minutes, with real UI screenshots.
For the full reference see [USER_GUIDE.md](./USER_GUIDE.md) (中文: [快速入门.md](./快速入门.md)).

---

## Prerequisite: start the platform

```bash
# One-click (recommended)
./scripts/install.sh
cp .env.example .env    # intranet: FORMUMIND_API_AUTH_ENABLED=false

# Or manual:
# Backend (terminal 1)
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --reload-exclude .venv  # http://localhost:8000/docs

# Frontend (terminal 2)
cd frontend
npm install
npm run dev                        # http://localhost:5173
```

Open **http://localhost:5173**. No LLM API key is required for full offline use.
If platform bearer auth is enabled, enter the **API access token** in Settings first
(matching `FORMUMIND_API_TOKEN`), or set `FORMUMIND_API_AUTH_ENABLED=false`.

### Or with Docker

```bash
cp .env.example .env
docker compose up -d --build       # redis + backend + worker + frontend
docker compose exec backend alembic upgrade head   # bring an existing DB up to date
curl -s localhost:8000/health
```

**Check `/health` before anything else** — it is the one place that tells you
whether the platform can actually do work:

```json
{"status":"ok",
 "database":  {"ok":true,"scheme":"sqlite"},
 "task_broker":{"required":true,"reachable":true},
 "datalab":   {"required":false,"reachable":false}}
```

`task_broker.reachable:false` means Redis is unreachable and **every async
feature is refused** — research, recommend, inverse design, optimization. See
[Troubleshooting](#troubleshooting) below.

> Pick one compose invocation and keep it. `docker compose up` uses the bridge
> network; `-f docker-compose.yml -f docker-compose.host.yml` uses the host
> network. Switching mid-life recreates only some services and strands the rest
> on the old network — the symptom is `task_broker.reachable:false` with redis
> apparently "Up". `docker compose down && docker compose up -d` realigns
> everything.

---

## Step 0 · Overview

FormuMind uses a NotebookLM-style three-pane layout that separates
**inputs → research → outputs**: **Sources** on the left, **Research** Q&A in
the center, and the **Actions** toolbar on the right. The header holds
**⚙ Settings** (LLM provider) and **🕐 History**.

![Overview](./images/01-overview.png)

- **Left (Sources)**: research-topic prompt box, source-type checkboxes
  (patents / literature / internet / local files / **📓 NotebookLM**) each with
  a **status dot** (green = available online, yellow = offline fallback, red =
  library not installed), file upload, a **Search** button, a **🔬 Deep
  Research** button (below Search) that triggers multi-agent DeepResearchEngine
  research, a **🧪 ChemCrow** chemistry-enhancement badge that appears when
  Literature or Internet are selected, an error banner shown when search fails,
  and the loaded-sources list.
- **Center (Research)**: chat that answers questions grounded in the loaded
  sources, with citations.
- **Right (Actions)**: ten buttons, each opening a focused modal:

  | | Button | What it does |
  |---|---|---|
  | 🧪 | Requirements | product domain, substrate, objectives |
  | ⭐ | Recommend | AI-retrieved Top-N formulations |
  | 🎯 | **Inverse Design** | target properties → Pareto front of formulations |
  | 🔁 | **Material Substitution** | replacements for a discontinued or costly ingredient |
  | 🔬 | DOE Design | generate a run table, export a worksheet |
  | 📋 | Workbench | record actual parameters and measured values |
  | 📄 | **QC Report** | upload a test report → extracted measurements bound to an experiment |
  | 📈 | Optimization | Bayesian multi-objective loop |
  | ⚙️ | Process Optimization | cure / dispersion / film-thickness parameters |
  | 🔄 | Self-Driving Loop | data → retrain → optimize → next DOE, one click |

  The three in bold are covered in steps 5b, 5c and 6b below.

---

## Step 1 · Load sources

Type a research topic (e.g. "low-VOC waterborne anti-corrosion coating"), tick
the source types to search (patents / literature / internet / **📓 NotebookLM**),
and click **Search**. You can also upload local files (PDF/DOCX/XLSX/PPTX/HTML/
images), parsed via markitdown.

![Search & sources](./images/02-search.png)

Results from every selected source are merged, de-duplicated, ranked by
relevance, and listed in the left column (each removable). Offline, patent
search returns the curated seed corpus; literature/internet search need the
optional `intel` libraries; NotebookLM needs the `notebooklm` extra and a
one-time browser login (see §12 of the full guide).

**v0.9 source panel additions:**

- **Status dots** beside each source-type checkbox give real-time availability
  at a glance: green = available online, yellow = offline fallback active
  (patents always have a seed corpus even offline), red = required library not
  installed.
- When **Literature** or **Internet** is selected, a **🧪 ChemCrow** badge
  appears in the panel indicating whether ChemCrow chemistry-enhanced retrieval
  is active (`[intel]` extra required).
- The **🔬 Deep Research** button (below Search) calls the async
  `POST /api/research/deep` endpoint. It launches **DeepResearchEngine** — a
  multi-agent pipeline comprising a `web_agent` and a `kb_agent` (with HyDE
  query expansion and LLM re-ranking), followed by a `report_agent` that
  cross-validates evidence and enforces cited conclusions. Use it when you need
  a thorough synthesised report rather than a quick keyword search.
- An **error banner** is displayed in the Sources column if a search request
  fails, with the reason returned by the backend.

---

## Step 2 · Ask the sources (grounded Q&A)

In the center column, ask a question about the loaded sources — e.g. "What is
the main corrosion-protection mechanism in these patents?". The answer is
**grounded in the evidence** (semantic embedding or TF-IDF re-rank → LLM) and
shows citation chips linking back to the sources used.

![Research Q&A](./images/03-research.png)

---

## Step 3 · Choose your LLM (Settings)

Click **⚙ Settings** in the header. The dialog has three tabs:

| Tab | Purpose |
|-----|---------|
| **LLM** | Nine providers — model and **LLM API key** |
| **API keys** | Tavily, SerpAPI, EPO, … search/data-source secrets |
| **Dependencies** | Install optional pip packages (`llm`, `intel`, `science`, …) from the UI |

If an **API access token** banner appears at the top, that is the **platform bearer**
(`FORMUMIND_API_TOKEN`), not your LLM key. For intranet dev, set
`FORMUMIND_API_AUTH_ENABLED=false` in `.env` and restart the backend.

Pick provider and model, paste an API key, optionally set base URL, then **Save &
test connection**. With no key, everything still runs via the offline rule engine.

![Settings · multi-LLM](./images/04-settings.png)

---

## Step 4 · Describe the project in one sentence (✨ NL Intent)

Open **🧪 Requirements** and use the **✨ NL Intent** box at the top: type a
plain-language brief like *"Develop a waterborne epoxy anti-corrosion coating
for automotive underbody, salt spray ≥ 1000 h, cures at 120 °C"*. Click
**Parse & Fill** — domain, substrate, salt-spray hours, VOC limit and cure
temperature are extracted and auto-filled (LLM when configured, otherwise the
deterministic regex fallback).

![NL Intent · auto-fill requirements](./images/08-nl-intent.png)

---

## Step 5 · Recommend formulations + IP analysis

Open **⭐ Recommend** (right column) and click **research patents & recommend
formulations**. The Top-N leaderboard appears — each card shows the ingredient
table and predicted metrics, including the auto-computed `cost_cny_per_kg`,
`voc_gpl`, `sustainability_idx`, **PVC / CPVC** (pigment volume concentration
vs. critical), **Tg (°C)** and **viscosity_relative** (Fox & Mooney models),
plus **lab_L/a/b + ΔE₀₀** when the `color` extra is present.

Click **🔍 IP 合规分析** on any formula card to retrieve relevant patents,
score the formula's novelty (0–1) and surface infringement-risk highlights
and white-space hints. Expand a card and a **3D molecular-viewer panel** lists
the SMILES-bearing components to be rendered via 3Dmol.js.

![Recommend · leaderboard with molecular viewer](./images/05-recommend.png)

---

## Step 5b · Inverse design — start from the target, not from a template

**⭐ Recommend** answers "what should I try?". **🎯 Inverse Design** answers the
harder question: *"salt spray ≥ 1000 h, VOC ≤ 250, cost ≤ ¥40/kg — what
formulations satisfy all of that at once, and what do I give up between them?"*

Open **🎯 Inverse Design**, state the targets as **hard constraints** (must be
satisfied) and **soft objectives** (optimized, traded off), and run the search.

The important part is what makes this different from rescaling a template:
**the ingredients themselves are variables.** The search picks materials per
role from the catalogue, not just percentages of a fixed recipe, so the results
on the Pareto front are **structurally different formulations** — different
hardeners, different anticorrosive pigments, different carriers — rather than
one recipe with the dials nudged.

- **Hard constraints** are enforced by constraint-domination: a feasible
  candidate always beats an infeasible one, so what comes back satisfies the
  constraints or the run reports that nothing could.
- **Soft objectives** produce the front. Each returned candidate is
  non-dominated: nothing else is better on every objective at once.
- `rejected_infeasible` tells you how many candidates were discarded, which is
  the honest signal that your constraints may be too tight.

Runs asynchronously (`POST /api/design/inverse`), with progress over SSE.

---

## Step 5c · Material substitution & supply risk

Open **🔁 Material Substitution**, pick a formulation and the ingredient at
risk. You get a ranked list of replacements, each with **the predicted change
to every metric** — not just "these are chemically similar".

Ranking fuses three signals:

1. **Structural similarity** — interchangeable-group match first, then chemical
   family, then Hansen solubility distance `Ra = √(4Δd² + Δp² + Δh²)`, plus
   Tanimoto fingerprint similarity when RDKit is installed.
2. **Predicted deviation** — the genome is rebuilt with the replacement and
   re-predicted, giving a per-metric Δ.
3. **Literature evidence** — `substitutes` edges from the knowledge graph, each
   carrying the source and sentence it came from.

> **Read `delta_confidence` before trusting the Δ.** Without RDKit the predictor
> distinguishes same-role materials only through role loading, amine/epoxy
> equivalent ratio and table lookups for price and VOC. Swapping three epoxy
> hardeners moves cost (¥13.2 / 17.8 / 20.2 per kg) and leaves `salt_spray_hours`
> **identical at 867 h** — that is the model having no resolution, not evidence
> that the swap is performance-neutral. The report says so with
> `delta_confidence: cost_only`; install the `science` extra to raise it.

**Supply risk**: mark a material `discontinued` (`POST /api/materials/availability`),
then `GET /api/materials/supply-risk` lists every affected formulation with
substitution suggestions attached.

---

## Step 6 · Generate a DOE and feed results back

Open **🔬 DOE Design**, choose a design (e.g. **central composite CCD** or
**🧠 AI active selection**) and click **Generate DOE**. You get a run table —
one row per experiment, natural factor values plus a blank "measured" column.
The 🧠 active-learning rows are highlighted in violet: they are the points
expected-improvement says will teach the surrogate the most.

![DOE design](./images/06-doe.png)

Two feedback paths:

1. **Manual**: type lab-measured values into the "measured" column, then click
   **③ feed back results and train model**.
2. **Batch**: click **Export CSV**, hand it to the lab, then **Import CSV** once
   it's filled in.

Once a metric reaches ≥ 4 samples, a data-driven model is trained automatically;
the model-quality dashboard shows an R² half-gauge + RMSE, and subsequent
recommendations/optimization switch to the "empirical + measured" blend.

---

## Step 6b · Feed back from a QC report instead of by hand

Typing numbers out of a PDF is where measured data usually stops arriving. Open
**📄 QC Report**, pick the experiment the report belongs to, and upload the file.

The extractor pulls each measurement as a **row of its own** — metric, value,
**unit, method and specification limit** — instead of the bare `{metric: value}`
dictionary that experiment records used to carry. A salt-spray figure now
records that it came from ASTM B117 with a ≥1000 h spec, so a later reading is
comparable rather than merely numeric.

The whole ingest is one transaction, deduplicated by content hash, and the
original file is attached **before** the values are written — so a failure
part-way through cannot leave you with numbers whose source is gone.

Read them back with `GET /api/qc/experiments/{id}/measurements`.

> Backward compatible: `ExperimentRecord.measured` still exists and still reads
> as the same flat dictionary. It is now derived from the measurement rows, so
> nothing that consumed it needs to change.

---

## Step 7 · Run the optimization loop

Open **📈 Optimization** and click **run optimization loop** to start Bayesian
multi-objective optimization (24 iterations by default). The **convergence
chart** plots the best-so-far objective score per iteration; hover for exact
values. The leaderboard updates to the optimized Top-5 formulations, balancing
salt-spray, cost and sustainability simultaneously.

![Optimization · convergence](./images/07-optimize.png)

---

## Step 8 · Process optimization & self-driving loop

- **⚙️ Process Optimization** — co-optimizes manufacturing parameters
  (cure temperature/time, dispersion RPM, film thickness, bath temperature, pH,
  …) with Arrhenius/empirical outcome models. Same Bayesian engine as
  formulation optimization, but over the *process* design space.
- **🔄 Self-Driving Loop** — one click runs **measured records → retrain →
  Bayesian optimize → next active-learning DOE batch** end-to-end. The modal
  shows model R²/RMSE cards (with a ↓ trend arrow), the convergence chart, and
  the next DOE batch (AI rows highlighted in violet). Export the next batch as
  CSV and the loop continues.

![Self-Driving Loop · convergence + next DOE](./images/09-loop.png)

> Every successful research / recommend / optimize / feedback / loop run is
> saved as a session snapshot — open **🕐 History** in the header to review and
> restore the last 20 sessions (stored in browser localStorage).

---

## Step 9 · Formulation revision history

Every formula card can be saved as a **version**, and versions form a lineage —
a parent/child chain you can walk. Given any two, the diff is structured
(ingredients added, removed, adjusted, plus renames) rather than a text blob,
and a one-line summary is generated when nobody wrote a note:

> 移除 1 项（聚酰胺固化剂）；新增 1 项（异佛尔酮二胺）；调整 2 项（环氧树脂、二甲苯）

Endpoints: `POST /api/formulations/versions` to save,
`GET /api/formulations/versions?name=` to find a lineage,
`GET /api/formulations/versions/{from}/diff/{to}` to compare.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| A feature returns **503** naming Redis | Broker unreachable; the submission was recorded, not lost | `docker compose ps`; start redis + worker. Recorded jobs are re-enqueued automatically |
| `/health` shows `task_broker.reachable:false` while redis looks "Up" | Services on different networks — usually from mixing compose files | `docker compose down && docker compose up -d` |
| `/health` shows `task_broker.required:false` but you run a worker | A UI-saved "run tasks synchronously" toggle used to override the deployment | Fixed: the deployment now wins and logs the ignored value. Turn the toggle off in Settings to clear it |
| Image build fails at `apt-get` | The apt layer is optional and off by default | `git pull`. Only add `--build-arg INSTALL_BUILD_TOOLCHAIN=true` if you need a compiler in the image |
| Image build fails at `npm ci` | Incomplete lockfile | `cd frontend && npm install --package-lock-only`, commit the result |
| `alembic: No config file 'alembic.ini' found` | Older image | `git pull` and rebuild; `alembic.ini` now ships in the image |

An unexpected error message that is **not** plain `path -> status` is working as
intended — the API returns a JSON `detail` the UI shows verbatim. A bare
`path -> 502` means the response never came from the API at all; check the
reverse proxy and whether the backend is reachable.

---

## Next steps

- Custom objectives, `constraint_values`, manual / AI formula edits, multi-LLM and
  search API setup? See the **[full User Guide](./USER_GUIDE.md)**.
- `pytest -q` runs **1040+** offline backend tests; `cd frontend && npm test`
  runs **106** frontend tests. `pip install -e ".[dev]"` for dev tooling.
- Stronger engines auto-detect on install — `".[optimize]"`, `".[bo]"`, `".[intel]"`,
  `".[science]"`, `".[embedding]"`, `".[colbert,crag]"`, `".[color]"`, `".[notebooklm]"`.
  Installing `".[science]"` (RDKit) is what raises substitution's
  `delta_confidence` above `cost_only`.
- Interactive API docs: start the backend and visit
  **http://localhost:8000/docs**.
- Check referential integrity any time: `GET /api/kb/integrity`.

> The offline performance numbers are engineering-reasonable screening
> estimates, not lab-validated specs. Feed real DOE data back and predictions
> get progressively more accurate.
