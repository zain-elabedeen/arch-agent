# AI Infrastructure Architecture Intelligence System

An agent system that turns **runtime signals + service topology** into **actionable architecture decisions**.

> Based on what is happening in production, how should the system evolve?

---

## 🧠 Core Idea

Modern distributed systems continuously evolve:

- Load increases  
- Bottlenecks emerge  
- Failures cascade  
- Architecture becomes outdated  

Today, this evolution is:
- manual  
- reactive  
- dependent on senior engineers  

This system introduces a new layer:

> A **runtime architecture intelligence engine** that understands system behavior and suggests how the architecture should change.

---

## 🎯 What This System Does

Given:

- runtime signals (latency, CPU, errors, backlog)
- system topology (services, dependencies)

It produces:

- architecture **smells** (signals of stress)
- **root-cause-aware insights** (where issues originate)
- **architecture recommendations**
- **tradeoff-aware critiques**
- **prioritized execution plan**
- **human-readable explanation**

---

## 🚫 What This System Is NOT

To keep scope precise:

- Not an observability platform (Datadog, Grafana, etc.)
- Not a monitoring or alerting system
- Not an auto-remediation engine (yet)
- Not an LLM guessing from logs

Instead:

> It is a **reasoning layer on top of structured system data**

---

## 🏗️ System Architecture (Pipeline)

```text
Telemetry → Smell Detection → Pattern Retrieval → Recommendation → Critic → Planner → Reasoning (explanation)
```

Design principles:

| Layer | Role |
|--------|------|
| Detection | Deterministic rules over normalized signals + topology |
| Knowledge | Structured patterns (`ArchitecturePattern`) and smell→pattern mapping |
| Explanation | Deterministic markdown report;  LLM pass for clarity |

---

## 🔁 Multi-Agent Pipeline (LangGraph)

Each node is a focused agent operating on shared state (`GraphState`):

### 1. Telemetry Agent

Normalizes input into canonical signals and topology.

### 2. Smell Agent

Detects **architecture smells** using deterministic rules.

> Smells = signals of stress (NOT root causes)

---

### 3. Retrieval Agent

Maps smells → candidate architecture patterns.

---

### 4. Recommendation Agent

Generates structured recommendations:

- pattern

- solution

- impact

- effort

- priority

---

### 5. Critic Agent

Applies constraints (`avoid_when`) to detect risks.

---

### 6. Planner Agent

Creates a prioritized execution plan.

---

### 7. Reasoning Agent

Produces explanation report.

- deterministic fallback

- optional LLM for readability only

Orchestration lives in `agent/app/graph.py`. Node implementations are under `agent/app/nodes/`.

---

## Core concepts

### Signals and topology (inputs)

- **Signals**: e.g. request/DB latency, CPU, queue backlog, error rate (see `TelemetrySignals` in `agent/app/state.py` and aliases in `agent/app/nodes/telemetry.py`).
- **Topology**: services, edges (who calls whom, edge types), optional critical stores/queues.

### Architecture smells

Smells are **labels for patterns of stress** inferred from signals/topology (e.g. `read_scaling_bottleneck`, `cpu_saturation`). They **trigger** retrieval; they are not fixes by themselves.

Examples:
- `read_scaling_bottleneck`
- `cpu_saturation`
- `queue_backlog`
- `coupling_risk`

### Architecture patterns

Patterns are the unit of reusable knowledge: use/avoid conditions, solutions, tradeoffs, impact/effort/confidence. Files live in `agent/app/patterns/` (JSON validated by `agent/app/models/pattern.py`).

Examples:
- read replicas  
- caching  
- load balancing  
- queue partitioning

### Smell → pattern mapping

`agent/app/services/pattern_loader.py` defines `SMELL_TO_PATTERN_MAP`: explicit, explainable links from smell types to pattern ids. This can later evolve into semantic or graph-based retrieval.

---

## State model

`GraphState` (TypedDict in `agent/app/state.py`) flows through the pipeline:

| Field | Set by |
|--------|--------|
| `signals` | normalized metrics |
| `topology` | system structure |
| `smells` | detected stress signals |
| `patterns` | candidate solutions |
| `recommendations` | ranked outputs |
| `critiques` | risk warnings |
| `final_plan` | execution steps |
| `explanation_report` | human-readable output |

---

## API

- `POST /v1/recommendations` — Run the full pipeline; returns JSON:

  - `smells` — type, severity, confidence, evidence  
  - `recommendations` — pattern, solution, impact, effort, priority, reason  
  - `critiques` — pattern_id, level, message, evidence  
  - `plan` — ordered steps with impact/effort  
  - `explanation_report` — markdown narrative  

---

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

```bash
uvicorn agent.app.main:app --reload
```

## Run with Docker Compose

1) Create env file:

```bash
cp .env.example .env
```

2) Build and start API + ingestion worker + Postgres:

```bash
docker compose up --build
```

3) Validate:

```bash
curl -s http://127.0.0.1:8000/healthz
```

Notes:
- API is exposed on `localhost:8000`
- Postgres is exposed on `localhost:5432`
- Worker reads kube credentials from `${HOME}/.kube/config` (mounted read-only at `/kube/config`).
- If kubeconfig references local cert/key files (for example Minikube paths like `${HOME}/.minikube/...`), those paths must also be mounted into the worker container.
- For in-cluster deployment, remove the kubeconfig mount and rely on in-cluster service account auth.

Example request:

```bash
curl -s http://127.0.0.1:8000/v1/recommendations \
  -H 'content-type: application/json' \
  -d '{
    "signals": {
      "request_latency_p95_ms": 850,
      "db_latency_p95_ms": 420,
      "error_rate": 0.03,
      "cpu_utilization": 0.92,
      "queue_backlog": 12000
    },
    "topology": {
      "services": ["api", "worker", "db"],
      "edges": [
        {"from": "api", "to": "db", "type": "db"},
        {"from": "api", "to": "worker", "type": "queue"}
      ],
      "critical_stores": ["db"],
      "critical_queues": ["jobs"]
    }
  }' | jq .
```

### Configuration

Copy `.env.example` to `.env`. Variables use the `ARCHAGENT_` prefix (see `agent/app/config.py`):

- `ARCHAGENT_PATTERN_STORE` — `filesystem` (default; Postgres path reserved)
- `ARCHAGENT_PATTERNS_PATH` — catalog directory
- `ARCHAGENT_LLM_REASONING_ENABLED`, `ARCHAGENT_LLM_PROVIDER`, `ARCHAGENT_LLM_MODEL` — optional explanation LLM
- `ARCHAGENT_OPENAI_API_KEY` or `ARCHAGENT_OLLAMA_BASE_URL` — provider credentials

---

## Tests

```bash
pytest
```

Nodes are written to be callable independently for unit tests; the graph is covered in `tests/test_graph_pipeline.py`.

