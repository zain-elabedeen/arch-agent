# ArchAgent

[![Tests](https://github.com/zain-elabedeen/archi-agent/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/zain-elabedeen/archi-agent/actions/workflows/ci.yml)

ArchAgent is a cloud infrastructure intelligence system. It uses runtime data and service topology to detect infratsruture smells,  create architeture recommendations, risk critiques and execution plans

## What It Is

ArchAgent is a reasoning layer on top of infrastructure data.

It consumes:

- Workload state
- normalized runtime signals
- inferred or annotated service topology
- curated architecture patterns

It produces:

- architecture smells
- candidate architecture patterns
- ranked recommendations
- constraint and risk warnings
- a prioritized plan
- a human-readable report

ArchAgent sits above infrastructure signals  (Prometheus, Datadog, Grafana, or Kubernetes..)  and converts them into architecture reasoning.

## Current Scope

The current implementation focuses on Kubernetes data foundation plus the MVP architecture reasoning pipeline.

Implemented:

- FastAPI API layer
- LangGraph multi-agent pipeline
- Kubernetes pull-based connector
- snapshot normalization
- Postgres persistence with JSONB snapshot payloads
- snapshot-backed recommendation API
- deterministic smell detection
- smell-to-pattern retrieval
- pattern-based critic rules
- prioritized planner
- explanation report (LLM)
- Prometheus connector
- log ingestion
- incident timelines
- security intelligence
- action/execution layer
- long-term trend analysis

## Architecture

```text
Kubernetes API
    ->
Kubernetes Connector Worker
    ->
Normalizer + Topology Builder
    ->
Postgres Snapshots
    ->
Recommendation API
    ->
LangGraph Pipeline
    ->
Smells + Recommendations + Critiques + Plan + Report
```

Important design rule: the API does not call Kubernetes directly. The worker collects infrastructure data and writes snapshots. The API reads the latest snapshot, builds `GraphState`, and runs the reasoning graph.

## Reasoning Pipeline

The LangGraph pipeline is linear:

```text
Telemetry -> Smell Detection -> Pattern Retrieval -> Recommendation -> Critic -> Planner -> Reasoning
```

Node responsibilities:

- `telemetry`: normalize raw inputs into canonical signal and topology keys
- `smells`: detect deterministic architecture smells
- `retrieval`: map smell types to architecture patterns
- `recommend`: rank mapped patterns into recommendation records
- `critic`: apply `avoid_when` constraints and structured pattern rules
- `planner`: turn recommendations into ordered plan steps
- `reasoning`: produce the final explanation report

The graph is defined in `agent/app/graph.py`. Node implementations live in `agent/app/nodes/`.

## Data Foundation

The Kubernetes connector lives under `agent/app/connectors/kubernetes/`.

Main files:

- `client.py`: builds Kubernetes API clients
- `collector.py`: pulls pods, deployments, services, pod metrics, and HPAs
- `normalizer.py`: converts Kubernetes objects into the canonical snapshot shape
- `topology_builder.py`: infers service dependencies
- `repository.py`: stores and loads snapshots from Postgres
- `worker.py`: runs the collect -> normalize -> persist loop

The current snapshot model includes:

- per-service CPU and memory utilization
- raw CPU cores and memory bytes when metrics-server is available
- desired, available, and unavailable replicas
- restart totals
- HPA scaling pressure
- queue backlog from HPA external metrics when available
- topology edges
- data quality hints

By default, ingestion excludes Kubernetes/platform namespaces so local Minikube
control-plane components do not drive application architecture recommendations:

- `kube-system`
- `kube-public`
- `kube-node-lease`
- `kubernetes-dashboard`

Use `ARCHAGENT_K8S_INCLUDE_NAMESPACES` and `ARCHAGENT_K8S_EXCLUDE_NAMESPACES`
to scope the worker to the namespaces you want analyzed.

Canonical snapshot types are defined in `agent/app/state.py`:

- `ServiceSnapshot`
- `SnapshotSignals`
- `SnapshotDataQuality`
- `ClusterSnapshot`

## Storage Model

Postgres is the current persistence layer.

ArchAgent uses relational tables for stable query paths and JSONB for evolving snapshot shape:

- `runs`: one row per ingestion snapshot, with full `snapshot` JSONB
- `runs.data_quality`: collector completeness and inference quality JSONB
- `service_metrics`: queryable per-service metrics, namespace, replica health, and restart counts
- `signals`: stable signal columns plus extensible `payload` JSONB
- `topology`: dependency edges per run, including inference provenance when known

JSONB is preferred over a separate NoSQL datastore at this stage because snapshots still need run history, relational joins, and simple operational deployment.

## Topology Inference

Topology nodes are logical services derived from Kubernetes labels:

- `app.kubernetes.io/name`
- `app`
- `k8s-app`

Edges can be inferred from:

- Kubernetes service DNS values in environment variables
- short service DNS names such as `jobs.default.svc`
- URL-style values such as `postgres://postgres:5432/app`
- host/port values such as `redis:6379`
- manual dependency annotations

Manual annotation format:

```yaml
metadata:
  annotations:
    archagent.io/depends-on: "db:postgres,queue:worker"
```

Supported dependency prefixes include:

- `db`
- `database`
- `queue`
- `broker`
- `stream`
- `http`
- `grpc`
- `api`

## Smells

Smells are deterministic labels for architecture stress. They trigger pattern retrieval; they are not fixes by themselves.

Current smell examples:

- `read_scaling_bottleneck`
- `cpu_saturation`
- `memory_pressure`
- `queue_backlog`
- `restart_instability`
- `replica_unavailability`
- `autoscaling_pressure`
- `single_instance_risk`
- `coupling_risk`
- `high_error_rate`

Kubernetes-native smells make the system useful before Prometheus ingestion exists. Latency and error-rate based smells still require inline input today and will be better supported once a Prometheus connector is added.

## Patterns

Architecture patterns live in `agent/app/patterns/` as JSON files validated by `ArchitecturePattern` in `agent/app/models/pattern.py`.

Each pattern can define:

- `use_when`
- `avoid_when`
- `solutions`
- `tradeoffs`
- `impact`
- `effort`
- `confidence`

Smell-to-pattern routing is explicit in `agent/app/services/pattern_loader.py` through `SMELL_TO_PATTERN_MAP`.

## API Layout

FastAPI app construction lives in `agent/app/main.py`.

Route handlers are split into API modules:

- `agent/app/api/health.py`
- `agent/app/api/recommendations.py`

Endpoints:

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/healthz` | Liveness check |
| `POST` | `/v1/recommendations` | Run the recommendation pipeline |

### Recommendation Modes

Inline mode:

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

Snapshot mode:

```bash
curl -s -X POST http://127.0.0.1:8000/v1/recommendations | jq .
```

Snapshot mode uses the latest stored Kubernetes snapshot. To analyze a specific run:

```bash
curl -s -X POST "http://127.0.0.1:8000/v1/recommendations?run_id=<uuid>" | jq .
```

Response shape:

- `snapshot_run_id`
- `smells`
- `recommendations`
- `critiques`
- `plan`
- `explanation_report`

## Quickstart

Create a virtualenv and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run the API:

```bash
uvicorn agent.app.main:app --reload
```

Validate:

```bash
curl -s http://127.0.0.1:8000/healthz
```

## Docker Compose

Create an env file:

```bash
cp .env.example .env
```

Start API, worker, and Postgres:

```bash
docker compose up --build
```

Services:

- API: `http://127.0.0.1:8000`
- Postgres: `localhost:5432`
- Worker: `python -m agent.app.connectors.kubernetes.worker`

The worker reads kube credentials from `${HOME}/.kube/config`, mounted into the container at `/kube/config`.

If kubeconfig references local cert/key files, those paths must also be mounted. The compose file currently mounts `${HOME}/.minikube` for local Minikube-style credentials.

For in-cluster deployment, remove the kubeconfig mount and rely on in-cluster service account auth.

## Configuration

Environment variables use the `ARCHAGENT_` prefix. See `agent/app/config.py` and `.env.example`.

Common variables:

| Variable | Purpose |
| --- | --- |
| `ARCHAGENT_ENVIRONMENT` | Runtime environment: `dev`, `test`, or `prod` |
| `ARCHAGENT_PATTERN_STORE` | Pattern store mode. Current implementation: `filesystem` |
| `ARCHAGENT_PATTERNS_PATH` | Path to JSON architecture patterns |
| `ARCHAGENT_POSTGRES_DSN` | Postgres connection string |
| `ARCHAGENT_K8S_AUTO_MIGRATE` | Auto-create/update connector tables |
| `ARCHAGENT_K8S_POLL_INTERVAL_SEC` | Kubernetes worker polling interval |
| `ARCHAGENT_K8S_INCLUDE_NAMESPACES` | Optional comma-separated allow-list of namespaces |
| `ARCHAGENT_K8S_EXCLUDE_NAMESPACES` | Comma-separated namespace exclude list |
| `ARCHAGENT_LLM_REASONING_ENABLED` | Enable optional explanation-only LLM pass |
| `ARCHAGENT_LLM_PROVIDER` | `openai`, `ollama`, `agent_platform_gemini`, or `agent_platform_claude` |
| `ARCHAGENT_LLM_MODEL` | Model used for explanation polish |
| `ARCHAGENT_OPENAI_API_KEY` | OpenAI API key when using OpenAI |
| `ARCHAGENT_OLLAMA_BASE_URL` | Ollama OpenAI-compatible base URL |
| `ARCHAGENT_GCP_PROJECT_ID` | GCP project ID for Agent Platform providers |
| `ARCHAGENT_GCP_LOCATION` | Agent Platform region, multi-region, or `global` endpoint |
| `ARCHAGENT_GCP_GENAI_API_VERSION` | Google Gen AI SDK API version. Default: `v1` |

### LLM Providers

The LLM is only used by the reasoning node to rewrite the deterministic report into a clearer teaching-oriented explanation. Smell detection, pattern retrieval, criticism, and planning stay rule-based.

Local Ollama:

```bash
ARCHAGENT_LLM_PROVIDER=ollama
ARCHAGENT_LLM_MODEL=llama3.1
ARCHAGENT_OLLAMA_BASE_URL=http://localhost:11434/v1
```

Gemini on Gemini Enterprise Agent Platform:

```bash
ARCHAGENT_LLM_PROVIDER=agent_platform_gemini
ARCHAGENT_LLM_MODEL=gemini-2.5-flash
ARCHAGENT_GCP_PROJECT_ID=your-gcp-project-id
ARCHAGENT_GCP_LOCATION=global
ARCHAGENT_GCP_GENAI_API_VERSION=v1
```

Claude on Gemini Enterprise Agent Platform:

```bash
ARCHAGENT_LLM_PROVIDER=agent_platform_claude
ARCHAGENT_LLM_MODEL=claude-sonnet-4-5@20250929
ARCHAGENT_GCP_PROJECT_ID=your-gcp-project-id
ARCHAGENT_GCP_LOCATION=global
```

Agent Platform providers use Google Application Default Credentials. For local development, run:

```bash
gcloud auth application-default login
```

For service accounts, set `GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/service-account.json` in the process environment. The GCP project must have the Agent Platform API enabled, `roles/aiplatform.user` or equivalent permissions, and access to the selected Gemini or Claude model in the configured location.

The Google Gen AI SDK also recognizes `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, and `GOOGLE_GENAI_USE_VERTEXAI=True` when they are exported in the process environment. ArchAgent still prefers `ARCHAGENT_GCP_PROJECT_ID` and `ARCHAGENT_GCP_LOCATION` so the app configuration remains explicit. Legacy provider aliases `vertex_gemini`, `gcp_gemini`, `vertex_claude`, and `gcp_claude` are accepted for backward compatibility.

## Tests

Use the repo virtualenv so LangGraph and the app dependencies are available:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q
```

Current suite coverage includes:

- graph pipeline execution
- recommendation snapshot mode
- smell rules
- pattern loading and smell mapping
- critic behavior
- planner behavior
- reasoning report generation
- Kubernetes normalizer behavior
- topology inference behavior

## Repository Structure

```text
agent/app/
  api/                    FastAPI route handlers
  connectors/kubernetes/  Kubernetes collection, normalization, topology, persistence
  models/                 Domain models
  nodes/                  LangGraph node implementations
  patterns/               Architecture pattern catalog
  services/               Smell rules, pattern loading, snapshot loading
  graph.py                LangGraph orchestration
  main.py                 FastAPI app construction
  state.py                API and graph state models
```
