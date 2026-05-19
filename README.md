# ArchAgent

[![Tests](https://github.com/zain-elabedeen/arch-agent/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/zain-elabedeen/arch-agent/actions/workflows/ci.yml)

ArchAgent is a cloud infrastructure intelligence system. It turns Kubernetes runtime snapshots, topology, logs, curated architecture patterns, and optional architecture knowledge into infrastructure smells, architecture recommendations, risk critiques, execution plans, and human-readable reports.

ArchAgent is a reasoning layer on top of infrastructure data. It does not apply changes to a cluster.

## What It Does

ArchAgent consumes:

- Kubernetes workload state
- normalized runtime signals
- inferred service topology
- normalized pod log summaries
- curated architecture patterns
- optional architecture knowledge from books, markdown, PDFs, and text files

ArchAgent produces:

- deterministic infrastructure smells
- architecture recommendations mapped from detected smells
- constraint and risk critiques
- prioritized plan steps
- scoped analysis grouped by affected service/workload
- a topology graph for the dashboard
- an explanation report, optionally enriched by an LLM and RAG citations

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
Smells + Recommendations + Critiques + Plan + Optional Log Analysis + Optional RAG + Report
```

The ingestion orchestrator creates one run per polling cycle, then calls each configured connector worker with the same `run_id`. The Kubernetes worker owns workload, metric, and topology data. The logs worker owns log ingestion and merges normalized log signals into that same run.

The API reads the latest stored snapshot, builds `GraphState`, and runs the reasoning graph.

## API

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/healthz` | Liveness check |
| `GET` | `/v1/topology` | Return the latest persisted topology graph |
| `GET` | `/v1/topology?run_id=<uuid>` | Return topology for a specific snapshot run |
| `POST` | `/v1/recommendations` | Run the recommendation pipeline on the latest persisted snapshot |
| `POST` | `/v1/recommendations?run_id=<uuid>` | Run the recommendation pipeline on a specific snapshot run |

Run recommendations for the latest snapshot:

```bash
curl -s -X POST http://127.0.0.1:8000/v1/recommendations | jq .
```

Run recommendations for a specific snapshot:

```bash
curl -s -X POST "http://127.0.0.1:8000/v1/recommendations?run_id=<uuid>" | jq .
```

Fetch the latest topology graph:

```bash
curl -s http://127.0.0.1:8000/v1/topology | jq .
```

Recommendation response fields:

- `snapshot_run_id`
- `smells`
- `recommendations`
- `critiques`
- `plan`
- `scoped_analysis`
- `log_analysis`
- `knowledge_context`
- `explanation_source`
- `explanation_report`

## Quickstart

Create a virtualenv and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Start the API:

```bash
uvicorn agent.app.main:app --reload
```

Validate the API:

```bash
curl -s http://127.0.0.1:8000/healthz
```

Snapshot-backed recommendation and topology endpoints require Postgres data. Use Docker Compose and the ingestion worker for the normal local flow.

## Docker Compose

Create an env file:

```bash
cp .env.example .env
```

Start Postgres with pgvector and the API:

```bash
docker compose up --build
```

Start the ingestion orchestrator in a separate terminal:

```bash
docker compose --profile workers up --build ingestion-worker
```

Run individual connector workers for debugging:

```bash
docker compose --profile connection-workers up --build k8s-worker logs-worker
```

Services:

- API: `http://127.0.0.1:8000`
- Postgres: `localhost:5432`
- Ingestion orchestrator: `python -m agent.app.connectors.orchestrator`
- Kubernetes worker: `python -m agent.app.connectors.kubernetes.worker`
- Logs worker: `python -m agent.app.connectors.logs.worker`

The orchestrator and Kubernetes-backed workers read kube credentials from `${HOME}/.kube/config`, mounted into the container at `/kube/config`.

If kubeconfig references local cert/key files, those paths must also be mounted. The compose file currently mounts `${HOME}/.minikube` for local Minikube-style credentials.

For in-cluster deployment, remove the kubeconfig mount and rely on in-cluster service account auth.

## Configuration

Environment variables use the `ARCHAGENT_` prefix. See `agent/app/config.py` and `.env.example`.

| Variable | Purpose |
| --- | --- |
| `ARCHAGENT_ENVIRONMENT` | Runtime environment: `dev`, `test`, or `prod` |
| `ARCHAGENT_POSTGRES_DSN` | Postgres connection string |
| `ARCHAGENT_K8S_AUTO_MIGRATE` | Auto-create/update connector tables |
| `ARCHAGENT_INGESTION_CONNECTORS` | Comma-separated connectors for the orchestrator. Default: `kubernetes,logs` |
| `ARCHAGENT_K8S_POLL_INTERVAL_SEC` | Kubernetes worker polling interval |
| `ARCHAGENT_K8S_INCLUDE_NAMESPACES` | Optional namespace allow-list |
| `ARCHAGENT_K8S_EXCLUDE_NAMESPACES` | Namespace exclude list |
| `ARCHAGENT_LOGS_ENABLED` | Enable the logs connector worker |
| `ARCHAGENT_LOG_WINDOW_GRACE_SEC` | Extra seconds added to the log read window |
| `ARCHAGENT_LOG_TAIL_LINES` | Max log lines read per pod/container per poll |
| `ARCHAGENT_PATTERN_STORE` | Pattern store mode. Current implementation: `filesystem` |
| `ARCHAGENT_PATTERNS_PATH` | Path to JSON architecture patterns |
| `ARCHAGENT_LOG_LLM_ENABLED` | Enable experimental log-analysis LLM sidecar |
| `ARCHAGENT_LOG_SAMPLE_LIMIT` | Max normalized log samples sent to the log-analysis LLM |
| `ARCHAGENT_LOG_LLM_MODEL` | Optional model override for log classification |
| `ARCHAGENT_LOG_LLM_MAX_OUTPUT_TOKENS` | Max output tokens for log classification |
| `ARCHAGENT_LLM_REASONING_ENABLED` | Enable optional explanation-only LLM report rewrite |
| `ARCHAGENT_LLM_PROVIDER` | `agent_platform_gemini`, `openai`, `ollama`, or `agent_platform_claude` |
| `ARCHAGENT_LLM_MODEL` | Model used for explanation polish |
| `ARCHAGENT_LLM_TIMEOUT_SEC` | Max seconds to wait before deterministic fallback |
| `ARCHAGENT_LLM_MAX_OUTPUT_TOKENS` | Max output tokens for explanation LLM |
| `ARCHAGENT_OPENAI_API_KEY` | OpenAI key for OpenAI LLM or RAG embeddings |
| `ARCHAGENT_OLLAMA_BASE_URL` | Ollama OpenAI-compatible base URL |
| `GOOGLE_CLOUD_PROJECT` | GCP project ID for Agent Platform providers |
| `ARCHAGENT_GCP_LOCATION` | Agent Platform region, multi-region, or `global` endpoint |
| `ARCHAGENT_GCP_GENAI_API_VERSION` | Google Gen AI SDK API version |

## Architecture Knowledge RAG

RAG is optional and disabled by default. It enriches the explanation report with cited architecture knowledge, but it does not create smells, recommendations, critiques, or plan steps.

Supported source files:

- `.md`
- `.txt`
- `.pdf`

Add source files:

```bash
mkdir -p agent/app/knowledge_sources
cp /path/to/architecture-notes.md agent/app/knowledge_sources/
```

Index knowledge into Postgres/pgvector:

```bash
python -m agent.app.knowledge.ingest --path agent/app/knowledge_sources
```

Enable retrieval:

```bash
ARCHAGENT_RAG_ENABLED=true
ARCHAGENT_RAG_EMBEDDING_PROVIDER=openai
ARCHAGENT_OPENAI_API_KEY=...
```

RAG settings:

| Variable | Purpose |
| --- | --- |
| `ARCHAGENT_RAG_ENABLED` | Enable knowledge retrieval during recommendation runs |
| `ARCHAGENT_RAG_STORE` | Knowledge store. Current implementation: `postgres` |
| `ARCHAGENT_RAG_KNOWLEDGE_PATH` | Source directory mounted for ingestion |
| `ARCHAGENT_RAG_EMBEDDING_PROVIDER` | `openai` for production, `hash` for offline tests/dev |
| `ARCHAGENT_RAG_EMBEDDING_MODEL` | Embedding model, default `text-embedding-3-small` |
| `ARCHAGENT_RAG_EMBEDDING_DIMENSIONS` | Embedding vector size |
| `ARCHAGENT_RAG_TOP_K` | Number of chunks retrieved per recommendation run |
| `ARCHAGENT_RAG_CHUNK_TOKENS` | Approximate chunk size |
| `ARCHAGENT_RAG_CHUNK_OVERLAP_TOKENS` | Approximate overlap between chunks |

Retrieved chunks appear in the API response as `knowledge_context` and in the report under `Relevant Architecture Knowledge`.

## LLM Providers

LLMs are optional and used only for explanation/log sidecar enrichment. Deterministic agents remain responsible for smells, recommendations, critiques, and plans.

Gemini on Google Cloud Agent Platform:

```bash
ARCHAGENT_LLM_PROVIDER=agent_platform_gemini
ARCHAGENT_LLM_MODEL=gemini-2.5-flash
ARCHAGENT_LLM_TIMEOUT_SEC=20
ARCHAGENT_LLM_MAX_OUTPUT_TOKENS=2500
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
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
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
ARCHAGENT_GCP_LOCATION=global
```

Agent Platform providers use Google Application Default Credentials. For local development:

```bash
gcloud auth application-default login
gcloud auth application-default set-quota-project your-gcp-project-id
```

For service accounts, set `GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/service-account.json`. The project must have the Agent Platform API enabled, suitable AI Platform permissions, and access to the selected Gemini or Claude model in the configured location.

For Docker Compose local development, the API container mounts `${HOME}/.config/gcloud` into `/root/.config/gcloud`, so host ADC credentials are available inside the container after recreation.

## Reasoning Pipeline

The LangGraph pipeline is linear:

```text
Telemetry
  -> Smell Detection
  -> Pattern Retrieval
  -> Recommendation
  -> Critic
  -> Planner
  -> Log Analysis
  -> Knowledge Retrieval
  -> Reasoning
```

Node responsibilities:

- `telemetry`: normalize snapshot signals and topology into canonical state
- `smells`: detect deterministic architecture smells
- `retrieval`: map smell types to curated architecture patterns
- `recommend`: rank mapped patterns into recommendation records
- `critic`: apply `avoid_when` constraints and structured pattern rules
- `planner`: turn recommendations into ordered plan steps
- `log_analysis`: optionally classify sampled normalized logs for report context only
- `knowledge`: optionally retrieve architecture knowledge chunks for report context only
- `reasoning`: produce the final explanation report

The graph is defined in `agent/app/graph.py`. Node implementations live in `agent/app/nodes/`.

## Data Foundation

The ingestion orchestrator lives at `agent/app/connectors/orchestrator.py`. It creates a shared run, then calls configured connector workers with that `run_id`.

The Kubernetes connector lives under `agent/app/connectors/kubernetes/`:

- `client.py`: builds Kubernetes API clients
- `collector.py`: pulls pods, deployments, services, pod metrics, and HPAs
- `normalizer.py`: converts Kubernetes objects into the canonical snapshot shape
- `topology_builder.py`: infers service dependencies
- `topology_graph_builder.py`: builds the UI-ready topology graph
- `worker.py`: runs the collect, normalize, and persist loop

The logs connector lives under `agent/app/connectors/logs/`:

- `models.py`: source-neutral raw log batch model
- `kubernetes_source.py`: reads Kubernetes pod logs into raw batches
- `normalizer.py`: parses JSON/plain-text logs and aggregates log signals
- `worker.py`: runs the log collect, normalize, and merge loop

Shared connector persistence lives in `agent/app/connectors/repository.py`.

The optional log classifier is an agent node, not part of the connector. It reads normalized `logs.events` during the recommendation pipeline, stores sidecar context in `GraphState.log_analysis`, and cannot create smells or recommendations.

## Storage Model

Postgres is the persistence layer. Docker Compose uses `pgvector/pgvector:pg16` so architecture knowledge embeddings can be stored next to snapshots.

Core tables:

- `runs`: one row per ingestion snapshot, with full `snapshot` JSONB
- `runs.data_quality`: collector completeness and inference quality JSONB
- `service_metrics`: queryable per-service metrics, namespace, replica health, and restart counts
- `signals`: stable signal columns plus extensible `payload` JSONB
- `topology`: dependency edges per run
- `log_events`: queryable normalized log event summaries
- `architecture_knowledge_sources`: indexed RAG source metadata
- `architecture_knowledge_chunks`: indexed RAG chunks and embeddings

JSONB is used for evolving snapshot shape while relational tables keep common query paths simple.

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

Supported dependency prefixes include `db`, `database`, `queue`, `broker`, `stream`, `http`, `grpc`, and `api`.

## Smells And Patterns

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

## Tests

Use the repo virtualenv so LangGraph and the app dependencies are available:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q
```

## Repository Structure

```text
agent/app/
  api/                    FastAPI route handlers
  connectors/             Ingestion orchestration, persistence, Kubernetes and logs connectors
  knowledge/              RAG extraction, chunking, embeddings, repository, retrieval, ingestion CLI
  knowledge_sources/      Local source directory for architecture knowledge files
  models/                 Domain models
  nodes/                  LangGraph node implementations
  patterns/               Curated architecture pattern catalog
  services/               Smell rules, pattern loading, snapshot/topology loading
  graph.py                LangGraph orchestration
  main.py                 FastAPI app construction
  state.py                API and graph state models
```
