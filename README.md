# Fitness Coach Agent

An adaptive AI fitness coach built on LangGraph, MongoDB, and Streamlit. Part of a larger fitness application ecosystem.

> **Philosophy:** "Life happens. The coach adapts."  
> The coach never punishes slip-ups. It observes, adjusts, and plans around the user's reality.

---

## What This Is

This repository contains the **coaching intelligence layer** of a fitness app — the agent that converses with users, interprets their daily state, and generates personalized workout plans across three planning horizons.

It is **not** a standalone fitness tracker or social platform. It is the reasoning engine that:
- Interprets free-text daily check-ins
- Handles disruptions (sickness, injury, fatigue, travel, time constraints)
- Generates structured workout plans with warmup, main work, and cooldown phases
- Tracks progress against monthly goals with volume and metric targets
- Retrieves coaching knowledge from a RAG-backed vector store

---

## Architecture Overview

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Streamlit UI  │────▶│  LangGraph Agent │────▶│   MongoDB       │
│  (app/)         │     │  (agent/)        │     │  (db/)          │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                               │
                               ▼
                        ┌─────────────────┐
                        │   RAG Pipeline  │
                        │  (rag/)         │
                        │  - Embeddings   │
                        │  - Vector Store │
                        │  - Ingestion    │
                        └─────────────────┘
```

---

## The Three Planning Horizons

| Horizon | Scope | Generated | Stored In |
|---------|-------|-----------|-----------|
| **3-Day Plan** | Tomorrow + 2 days ahead | Daily via agent tool call | `plans` collection |
| **Week Plan** | 2 blocks (Mon-Wed, Thu-Sat) + focus | Every Sunday | `week_plans` collection |
| **Month Plan** | Goal + volume targets + week themes | 1st of month | `month_plans` collection |

**Interaction flow:**
```
Month Goal (Volume/Intensity targets)
    ↓
Week Plan (Block structure + focus areas)
    ↓
3-Day Plan (Specific exercises + sets/reps)
    ↓
Daily Check-in (User reports state)
    ↓
Adjust 3-Day Plan (if disruption detected)
    ↓
Optionally adjust Week Plan (if 2+ missed days)
    ↓
Optionally adjust Month Goal (if consistently struggling)
```

---

## Project Structure

```
fitness_coach_agent/
├── agent/                  # LangGraph agent core
│   ├── graph.py           # State graph definition, nodes, edges
│   ├── prompts.py         # System prompt, monthly review prompt, theme path prompt
│   ├── state.py           # CoachState TypedDict
│   ├── context_trim.py    # View-time message trimming (V6.2)
│   └── monthly_review.py  # Pipeline: close out month, generate week themes
│
├── app/                   # Streamlit frontend
│   └── streamlit_app.py   # Chat interface, workout session, progress dashboard
│
├── tools/                 # 15 agent tools
│   ├── workout_library.py      # search_workout_library
│   ├── knowledge_base.py       # search_fitness_knowledge_base
│   ├── checkins.py             # record_checkin
│   ├── checkin_history.py      # get_recent_checkins, fetch_checkin
│   ├── plans.py                # update_three_day_plan
│   ├── plan_history.py         # get_current_plan, get_past_plans
│   ├── backlog.py              # sync_backlog, get_backlog, mark_backlog_reinserted
│   ├── metrics.py              # log_metric
│   ├── progress.py             # get_progress_summary, calculate_progress
│   ├── today_status.py         # get_today_workout_status
│   ├── week_plans.py           # get_current_week_plan, update_week_plan
│   └── month_plans.py          # get_current_month_plan, stage_month_goal, confirm_month_goal
│
├── rag/                   # RAG pipeline
│   ├── embeddings.py      # HuggingFace embedding model
│   ├── vectorstore.py     # AstraDB vector store client
│   ├── ingest.py          # Document ingestion pipeline
│   └── preprocessor.py    # Semantic chunking, table structuring
│
├── db/                    # Database layer
│   └── mongo_client.py    # MongoDB connection + collection getters
│
├── data/                  # Static data
│   ├── workouts.json      # Exercise library (validated against in tools)
│   └── documents/         # Preprocessed fitness books for RAG
│
├── docs/                  # Development documentation
│   ├── V6.1 token audit findings.md
│   ├── V6.1 validation results - prompt and tool compaction.md
│   ├── V6.2 validation results - context trimming.md
│   └── ...
│
├── tests/                 # Validation scripts
│   ├── check_caching.py
│   └── prompt_variants.py
│
├── config/                # (empty — configuration via environment variables)
├── requirements.txt       # Python dependencies
└── README.md              # This file
```

---

## Key Features

### Adaptive Disruption Handling

The agent parses free-text check-ins and automatically adjusts plans:

| User Says | Detected | Action |
|-----------|----------|--------|
| "I have a fever" | Sickness | Skip workout. Suggest rest + hydration. |
| "My shoulder hurts" | Injury | Replace shoulder exercises for 3 days. |
| "I slept 4 hours" | Fatigue | Reduce intensity (lower reps/rounds). |
| "Deadline today" | Stress/No time | 10-min mobility instead of full workout. |
| "I'm traveling" | No equipment | Bodyweight-only routine. |
| "Had a Coke" | Poor nutrition | Gentle hydration note, no guilt. |

### 3-Phase Workout Structure

Every generated plan includes:
- **Phase 1 — Warm-up:** 2-4 exercises (mobility, activation, dynamic warm-up)
- **Phase 2 — Main Work:** 4-12 exercises (strength, skill, conditioning)
- **Phase 3 — Cool-down:** 1-4 exercises (static stretch, breathing, recovery)

Total: 7-20 exercises per session, 30-90 minutes depending on volume.

### Safety Rules

- **Injury rule:** Any mentioned pain/soreness automatically excludes that body part from all exercise searches in the same turn
- **Exercise validation:** All presented exercises must come from `search_workout_library` results in the current turn — no hallucination
- **Name fidelity:** Exercise names and descriptions are used exactly as returned by the tool, never renamed or modified

### Nutrition Tracking (Simplified)

No calorie counting. Three simple metrics:
- **Sugary drinks:** Yes/No — high impact on weight loss goals
- **Protein intake:** Yes/No — ensures muscle recovery
- **Water intake:** Glass count — essential for performance

---

## Development History

| Version | Focus | Key Addition |
|---------|-------|-------------|
| **V1** | Hello world | Single LangGraph node → LLM → text output |
| **V2** | Tool calling | ReAct loop with `search_workout_library` |
| **V3** | Real RAG | HuggingFace embeddings + AstraDB vector store |
| **V4** | Persistence | MongoDB check-ins + multi-tool orchestration |
| **V5** | Memory | Historical check-in retrieval for contextual coaching |
| **V6** | Planning | 3-Day plan generation with structured output |
| **V6.1** | Token optimization | Prompt compaction, tool output trimming |
| **V6.2** | Context pruning | View-time message trimming without state mutation |
| **V7** | Multi-horizon | Week/Month plan routing with conditional graph edges |
| **V8** | Dynamic themes | LLM-generated week themes based on prior month adherence |

---

## Environment Variables

```bash
GEMINI_API_KEY=          # Google Gemini API key
MONGO_URI=               # MongoDB connection string
MONGO_DB_NAME=           # Database name
ASTRA_DB_APPLICATION_TOKEN=  # DataStax Astra DB token
ASTRA_DB_API_ENDPOINT=   # Astra DB API endpoint
LANGFUSE_PUBLIC_KEY=     # Langfuse tracing (optional)
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=
```

---

## Running Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Run Streamlit app
streamlit run app/streamlit_app.py
```

The app will be available at `http://localhost:8501`.


---

## Acknowledgments

Built with LangGraph, LangChain, Google Gemini, MongoDB, DataStax AstraDB, and Streamlit.
