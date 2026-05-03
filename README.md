# AI Infrastructure Architecture Recommendation System (MVP)

Production-oriented MVP skeleton for an **infrastructure architecture recommendation system**.

It ingests **structured telemetry + topology**, detects **architecture smells** deterministically, retrieves **curated architecture patterns**, produces **recommendations**, applies a **critic** pass, and outputs a **prioritized plan**.

## What’s implemented (MVP skeleton)

- FastAPI service with a single endpoint: `POST /v1/recommendations`
- LangGraph pipeline with nodes:
  - telemetry → smells → retrieval → recommend → critic → planner
- Deterministic smell rules (no “LLM guesses”)
- Structured pattern catalog loaded from `app/patterns/` (filesystem now; interface designed to swap to Postgres later)
- Fully typed Pydantic models for inputs/outputs/state

## Quickstart

Create a virtualenv and install deps:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run the API:

```bash
uvicorn agent.app.main:app --reload
```

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

## Notes on design

- **Smell detection is deterministic** in `app/services/smell_rules.py` to keep the MVP explainable and repeatable.
- **Patterns are structured** in JSON and validated by Pydantic (`app/models/pattern.py`).
- Each node is independently callable for unit tests and future pipeline reuse.

