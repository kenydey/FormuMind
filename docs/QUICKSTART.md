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
- **Right (Actions)**: six buttons — 🧪 Requirements, ⭐ Recommend,
  🔬 DOE Design, 📈 Optimization, ⚙️ Process Optimization, 🔄 Self-Driving Loop
  — each opening a focused modal.

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

## Next steps

- Custom objectives, `constraint_values`, manual / AI formula edits, multi-LLM and
  search API setup? See the **[full User Guide](./USER_GUIDE.md)**.
- `pytest -q` runs **430+** offline tests; `pip install -e ".[dev]"` for dev tooling.
- Stronger engines auto-detect on install — `".[optimize]"`, `".[bo]"`, `".[intel]"`,
  `".[science]"`, `".[embedding]"`, `".[colbert,crag]"`, `".[color]"`, `".[notebooklm]"`.
- Interactive API docs: start the backend and visit
  **http://localhost:8000/docs**.

> The offline performance numbers are engineering-reasonable screening
> estimates, not lab-validated specs. Feed real DOE data back and predictions
> get progressively more accurate.
