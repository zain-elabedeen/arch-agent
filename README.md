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

- infrastructure smells
- architecture recommendations
- constraint and risk warnings
- a prioritized plan
- a human-readable report

ArchAgent sits above infrastructure signals  (Prometheus, Datadog, Grafana, or Kubernetes..)  and converts them into architecture reasoning.

## Current Scope

The current implementation focuses on data foundation and the architecture reasoning pipeline (MVP).

- FastAPI API layer
- LangGraph multi-agent pipeline
- Kubernetes connector
- Prometheus connector
- log ingestion
- metrics snapshot normalization
- snapshot payload persistence (Postgresql)
- recommendation API
- smell detection engine
- smell-to-pattern retrieval
- pattern-based critic rules
- prioritized planner
- explanation report (LLM)

Next:

- incidents data
- security intelligence
- long-term trend analysis

## Architecture

```text
Kubernetes API
    ->
Ingestion Orchestrator Worker
    ->
Kubernetes Connector Worker
    ->
Metrics/Topology Normalizer
    ->
Postgres Snapshots

Kubernetes Pod Logs
    ->
Ingestion Orchestrator Worker
    ->
Logs Connector Worker
    ->
Log Normalizer + Aggregator
    ->
Postgres Snapshots

Postgres Snapshots
    ->
Recommendation API
    ->
LangGraph Pipeline
    ->
Smells + Recommendations + Critiques + Plan + Optional Log Analysis + Report
```

The ingestion orchestrator creates one run per polling cycle, then calls each
configured connector worker with the same `run_id`. The Kubernetes worker owns
workload, metric, and topology data. The logs worker owns log ingestion and
merges normalized log signals into that same run.

The API reads the latest snapshot, builds `GraphState`, and runs the reasoning graph.

## API Layout

Endpoints:

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/healthz` | Liveness check |
| `POST` | `/v1/recommendations` | Run the recommendation pipeline |

### Recommendation Modes

Snapshot mode:

```bash
curl -s -X POST http://127.0.0.1:8000/v1/recommendations | jq .
```

Snapshot mode uses the latest stored Kubernetes snapshot. To analyze a specific run:

```bash
curl -s -X POST "http://127.0.0.1:8000/v1/recommendations?run_id=<uuid>" | jq .
```

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

Start API and Postgres:

```bash
docker compose up --build
```

Start the ingestion orchestrator in a separate terminal:

```bash
docker compose --profile workers up --build ingestion-worker
```

The individual connector workers are still available for debugging:

```bash
docker compose --profile connection-workers up --build k8s-worker logs-worker
```

Services:

- API: `http://127.0.0.1:8000`
- Postgres: `localhost:5432`
- Ingestion orchestrator: `python -m agent.app.connectors.orchestrator`
- Standalone Kubernetes worker: `python -m agent.app.connectors.kubernetes.worker`
- Standalone logs worker: `python -m agent.app.connectors.logs.worker`

The orchestrator and Kubernetes-backed workers read kube credentials from `${HOME}/.kube/config`, mounted into the container at `/kube/config`.

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
| `ARCHAGENT_INGESTION_CONNECTORS` | Comma-separated connectors for the orchestrator. Default: `kubernetes,logs` |
| `ARCHAGENT_K8S_INCLUDE_NAMESPACES` | Optional comma-separated allow-list of namespaces |
| `ARCHAGENT_K8S_EXCLUDE_NAMESPACES` | Comma-separated namespace exclude list |
| `ARCHAGENT_LOGS_ENABLED` | Enable the logs connector worker |
| `ARCHAGENT_LOG_WINDOW_GRACE_SEC` | Extra seconds added to the log read window |
| `ARCHAGENT_LOG_TAIL_LINES` | Max log lines read per pod/container per poll |
| `ARCHAGENT_LOG_LLM_ENABLED` | Enable the experimental log-analysis agent in the recommendation pipeline |
| `ARCHAGENT_LOG_SAMPLE_LIMIT` | Max normalized log samples sent to the optional log-analysis agent |
| `ARCHAGENT_LOG_LLM_MODEL` | Optional model override for log classification. Defaults to `ARCHAGENT_LLM_MODEL` |
| `ARCHAGENT_LOG_LLM_MAX_OUTPUT_TOKENS` | Max output tokens for log classification. Default: `512` |
| `ARCHAGENT_LLM_REASONING_ENABLED` | Enable optional explanation-only LLM pass |
| `ARCHAGENT_LLM_PROVIDER` | `agent_platform_gemini`, `openai`, `ollama`, or `agent_platform_claude` |
| `ARCHAGENT_LLM_MODEL` | Model used for explanation polish |
| `ARCHAGENT_LLM_TIMEOUT_SEC` | Max seconds to wait for explanation LLM before deterministic fallback. Default: `20` |
| `ARCHAGENT_LLM_MAX_OUTPUT_TOKENS` | Max output tokens for explanation LLM. Default: `2500` |
| `ARCHAGENT_OPENAI_API_KEY` | OpenAI API key when using OpenAI |
| `ARCHAGENT_OLLAMA_BASE_URL` | Ollama OpenAI-compatible base URL |
| `ARCHAGENT_GCP_PROJECT_ID` | GCP project ID for Agent Platform providers |
| `ARCHAGENT_GCP_LOCATION` | Agent Platform region, multi-region, or `global` endpoint |
| `ARCHAGENT_GCP_GENAI_API_VERSION` | Google Gen AI SDK API version. Default: `v1` |

### LLM Providers

The LLM is used by the explanation-only reasoning pass. Gemini on GCP is the default provider.

Gemini on Google Cloud Agent Platform:

```bash
ARCHAGENT_LLM_PROVIDER=agent_platform_gemini
ARCHAGENT_LLM_MODEL=gemini-2.5-flash
ARCHAGENT_LLM_TIMEOUT_SEC=20
ARCHAGENT_LLM_MAX_OUTPUT_TOKENS=2500
ARCHAGENT_GCP_PROJECT_ID=your-gcp-project-id
ARCHAGENT_GCP_LOCATION=global
ARCHAGENT_GCP_GENAI_API_VERSION=v1
```

Local Ollama:

```bash
ARCHAGENT_LLM_PROVIDER=ollama
ARCHAGENT_LLM_MODEL=llama3.1
ARCHAGENT_OLLAMA_BASE_URL=http://localhost:11434/v1
```

Claude on Google Cloud Agent Platform:

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

For Docker Compose local development, the API container mounts `${HOME}/.config/gcloud` into `/root/.config/gcloud`, so `gcloud auth application-default login` on the host is enough for Application Default Credentials. Recreate the API container after authenticating.

If you use end-user ADC credentials, set a quota project to avoid quota/billing ambiguity:

```bash
gcloud auth application-default set-quota-project your-gcp-project-id
```

For quota-sensitive local testing, prefer one LLM call per request: enable `ARCHAGENT_LOG_LLM_ENABLED=true` and set `ARCHAGENT_LLM_REASONING_ENABLED=false`, or keep log analysis disabled while testing the report LLM. Keep `ARCHAGENT_LLM_TIMEOUT_SEC` lower than the HTTP client timeout so the API can return the deterministic report instead of hanging on a slow model response.

## Tests

Use the repo virtualenv so LangGraph and the app dependencies are available:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q
```

## Repository Structure

```text
agent/app/
  api/                    FastAPI route handlers
  connectors/orchestrator.py Creates runs and calls connector workers
  connectors/repository.py Shared connector snapshot persistence
  connectors/kubernetes/  Kubernetes collection, normalization, topology
  connectors/logs/        Source-neutral log ingestion, Kubernetes log source, normalization
  models/                 Domain models
  nodes/                  LangGraph node implementations
  patterns/               Architecture pattern catalog
  services/               Smell rules, pattern loading, snapshot loading
  graph.py                LangGraph orchestration
  main.py                 FastAPI app construction
  state.py                API and graph state models
```

## Reasoning Pipeline

The LangGraph pipeline is linear:

```text
Telemetry -> Smell Detection -> Pattern Retrieval -> Recommendation -> Critic -> Planner -> Log Analysis -> Reasoning
```

Node responsibilities:

- `telemetry`: normalize raw inputs into canonical signal and topology keys
- `smells`: detect deterministic architecture smells
- `retrieval`: map smell types to architecture patterns
- `recommend`: rank mapped patterns into recommendation records
- `critic`: apply `avoid_when` constraints and structured pattern rules
- `planner`: turn recommendations into ordered plan steps
- `log_analysis`: optionally classify sampled normalized log events with Gemini for report context only
- `reasoning`: produce the final explanation report

The graph is defined in `agent/app/graph.py`. Node implementations live in `agent/app/nodes/`.

## Data Foundation

The ingestion orchestrator lives at `agent/app/connectors/orchestrator.py`.
It creates a shared run, then calls configured connector workers with that
`run_id`. This is the mechanism that will later read integration settings and
decide which connections should contribute to each run.

The Kubernetes connector lives under `agent/app/connectors/kubernetes/`.

Main files:

- `client.py`: builds Kubernetes API clients
- `collector.py`: pulls pods, deployments, services, pod metrics, and HPAs
- `normalizer.py`: converts Kubernetes objects into the canonical snapshot shape
- `topology_builder.py`: infers service dependencies
- `worker.py`: runs the collect -> normalize -> persist loop

The logs connector lives under `agent/app/connectors/logs/`.

Main files:

- `models.py`: source-neutral raw log batch model
- `kubernetes_source.py`: reads Kubernetes pod logs into raw batches
- `normalizer.py`: parses JSON/plain-text logs and aggregates log signals
- `worker.py`: runs the log collect -> normalize -> merge snapshot loop

Shared connector persistence lives in `agent/app/connectors/repository.py`.

The optional Gemini log classifier is an agent node, not part of the connector:
`agent/app/nodes/log_analysis.py`. It reads normalized `logs.events` from the
snapshot during the recommendation pipeline, stores sidecar context in
`GraphState.log_analysis`, and cannot create smells or recommendations.

The current snapshot model includes:

- per-service CPU and memory utilization
- raw CPU cores and memory bytes when metrics-server is available
- desired, available, and unavailable replicas
- restart totals
- HPA scaling pressure
- queue backlog from HPA external metrics when available
- topology edges
- normalized log summaries when the logs worker is enabled
- data quality hints

By default, ingestion excludes Kubernetes/platform namespaces so
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
- `log_events`: queryable normalized log event summaries for the latest log-enabled snapshots

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
- `error_burst`
- `timeout_pressure`
- `dependency_instability`
- `probe_instability`
- `crash_loop_signal`

Kubernetes-native smells make the system useful before Prometheus ingestion exists. Log-backed smells use normalized request, error, timeout, dependency, probe, and crash signals from the logs connector.

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
