"""
Infer a minimal service graph from pods, Services, env references, and annotations.

The legacy topology contract remains service-name oriented. Edges now carry
optional confidence and evidence so they can be projected into the UI graph
without changing downstream smell rules.
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Any, Dict, Iterable, List, Set
from urllib.parse import urlparse

from kubernetes.client import V1ConfigMap, V1Pod, V1Secret, V1Service

from agent.app.connectors.kubernetes.kube_labels import app_name_for_labels, app_name_for_pod

_SVC_DNS = re.compile(
    r"([a-z0-9](?:[a-z0-9-]*[a-z0-9])?)(?:\.(?:[a-z0-9-]+))?\.svc(?:\.cluster\.local)?",
    re.IGNORECASE,
)
_HOST_WITH_PORT = re.compile(r"^([a-z0-9](?:[a-z0-9-]*[a-z0-9])?)(?::(\d+))?(?:/.*)?$", re.IGNORECASE)
_DEPENDENCY_ANNOTATION = "archagent.io/depends-on"
_SOURCE_CONFIDENCE = {
    "annotation": 0.9,
    "service": 0.8,
    "env_dns": 0.75,
    "env_url": 0.7,
    "env_host_port": 0.65,
    "external_hostname": 0.45,
}


_DB_HINTS = ("postgres", "mysql", "mongo", "elastic", "cockroach", "clickhouse", "mariadb")
_CACHE_HINTS = ("redis", "memcached")
_QUEUE_HINTS = ("kafka", "rabbit", "nats", "sqs", "queue", "worker")


def _service_selectors(svc: V1Service) -> Dict[str, str]:
    spec = svc.spec
    if not spec or not spec.selector:
        return {}
    return dict(spec.selector)


def _selector_matches(labels: Dict[str, str], selector: Dict[str, str]) -> bool:
    return all(labels.get(k) == v for k, v in selector.items())


def _target_app_for_hostname(
    hostname: str,
    app_names: Set[str],
    services: List[V1Service],
    pod_labels_by_ns: Dict[tuple[str, str], Dict[str, str]],
) -> str | None:
    h = hostname.lower().strip()
    if not h:
        return None
    if h in app_names:
        return h

    # Only bare service names are resolved through Kubernetes Services. External
    # hostnames such as api.stripe.com should remain external nodes.
    if "." in h:
        return None

    for svc in services:
        if not svc.metadata or not svc.metadata.name:
            continue
        if svc.metadata.name.lower() != h:
            continue
        sel = _service_selectors(svc)
        if not sel:
            continue
        ns = svc.metadata.namespace or ""
        for (pns, _), labels in pod_labels_by_ns.items():
            if pns != ns:
                continue
            if _selector_matches(labels, sel):
                cand = app_name_for_labels(labels)
                if cand:
                    return cand
    return None


def _type_from_text(value: str, default: str = "http") -> str:
    t = value.lower()
    parsed = urlparse(t)
    if parsed.scheme in {"http", "https"}:
        return "http"
    if parsed.scheme == "grpc":
        return "grpc"
    if parsed.scheme in {"postgres", "postgresql", "mysql", "mongodb", "mongo", "clickhouse"}:
        return "db"
    if parsed.scheme in {"redis", "memcached"}:
        return "cache"
    if any(x in t for x in _CACHE_HINTS):
        return "cache"
    if any(x in t for x in _DB_HINTS):
        return "db"
    if any(x in t for x in _QUEUE_HINTS):
        return "queue"
    return default


def _edge_type_for_target(target_app: str) -> str:
    return _type_from_text(target_app, default="http")


def _edge_type_for_dependency(raw: str, target_app: str) -> str:
    value = raw.strip().lower()
    if ":" in value:
        prefix = value.split(":", 1)[0].strip()
        if prefix in {"db", "database", "datastore", "store"}:
            return "db"
        if prefix in {"cache", "redis", "memcached"}:
            return "cache"
        if prefix in {"queue", "broker", "stream"}:
            return "queue"
        if prefix in {"http", "grpc", "api"}:
            return "http" if prefix == "api" else prefix
    return _edge_type_for_target(target_app)


def _object_key(obj: Any) -> tuple[str, str] | None:
    metadata = getattr(obj, "metadata", None)
    name = getattr(metadata, "name", None)
    if not name:
        return None
    return (str(getattr(metadata, "namespace", "") or ""), str(name))


def _config_map_values(config_maps: List[V1ConfigMap] | None) -> Dict[tuple[str, str], Dict[str, str]]:
    out: Dict[tuple[str, str], Dict[str, str]] = {}
    for cm in config_maps or []:
        key = _object_key(cm)
        if key is None:
            continue
        values: Dict[str, str] = {}
        for k, v in (cm.data or {}).items():
            if v is not None:
                values[str(k)] = str(v)
        for k, v in (cm.binary_data or {}).items():
            if v is not None:
                values[str(k)] = str(v)
        out[key] = values
    return out


def _decode_secret_value(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value)
    try:
        return base64.b64decode(raw, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return raw


def _secret_values(secrets: List[V1Secret] | None) -> Dict[tuple[str, str], Dict[str, str]]:
    out: Dict[tuple[str, str], Dict[str, str]] = {}
    for secret in secrets or []:
        key = _object_key(secret)
        if key is None:
            continue
        values: Dict[str, str] = {}
        for k, v in (secret.data or {}).items():
            decoded = _decode_secret_value(v)
            if decoded is not None:
                values[str(k)] = decoded
        for k, v in (secret.string_data or {}).items():
            if v is not None:
                values[str(k)] = str(v)
        out[key] = values
    return out


def _lookup_key_ref(
    ref: Any,
    values_by_ref: Dict[tuple[str, str], Dict[str, str]],
    namespace: str,
) -> tuple[str, str] | None:
    name = getattr(ref, "name", None)
    key = getattr(ref, "key", None)
    if not name or not key:
        return None
    value = values_by_ref.get((namespace, str(name)), {}).get(str(key))
    if value is None:
        return None
    return str(key), value


def _env_values(
    pod: V1Pod,
    config_maps: List[V1ConfigMap] | None = None,
    secrets: List[V1Secret] | None = None,
) -> Iterable[tuple[str, str, str]]:
    if not pod.spec:
        return
    namespace = pod.metadata.namespace if pod.metadata else ""
    namespace = namespace or ""
    config_map_values = _config_map_values(config_maps)
    secret_values = _secret_values(secrets)
    for c in pod.spec.containers or []:
        for e in c.env or []:
            if e.value:
                env_name = e.name or "ENV"
                yield (env_name, e.value, f"{env_name}={e.value[:200]}")
                continue
            value_from = getattr(e, "value_from", None)
            if not value_from:
                continue
            cm_ref = getattr(value_from, "config_map_key_ref", None)
            if cm_ref:
                resolved = _lookup_key_ref(cm_ref, config_map_values, namespace)
                if resolved:
                    key, value = resolved
                    env_name = e.name or key
                    yield (env_name, value, f"{env_name} from configmap/{namespace}/{cm_ref.name}/{key}")
            secret_ref = getattr(value_from, "secret_key_ref", None)
            if secret_ref:
                resolved = _lookup_key_ref(secret_ref, secret_values, namespace)
                if resolved:
                    key, value = resolved
                    env_name = e.name or key
                    yield (env_name, value, f"{env_name} from secret/{namespace}/{secret_ref.name}/{key}")
        for source in c.env_from or []:
            prefix = getattr(source, "prefix", None) or ""
            cm_ref = getattr(source, "config_map_ref", None)
            if cm_ref and cm_ref.name:
                for key, value in config_map_values.get((namespace, str(cm_ref.name)), {}).items():
                    env_name = f"{prefix}{key}"
                    yield (env_name, value, f"{env_name} from configmap/{namespace}/{cm_ref.name}/{key}")
            secret_ref = getattr(source, "secret_ref", None)
            if secret_ref and secret_ref.name:
                for key, value in secret_values.get((namespace, str(secret_ref.name)), {}).items():
                    env_name = f"{prefix}{key}"
                    yield (env_name, value, f"{env_name} from secret/{namespace}/{secret_ref.name}/{key}")


def _candidate_hosts(value: str) -> Iterable[tuple[str, str, str | None, int | None]]:
    raw = value.strip()
    if not raw:
        return

    for m in _SVC_DNS.finditer(raw):
        yield m.group(1).lower(), "env_dns", None, None

    parsed = urlparse(raw)
    if parsed.hostname:
        yield parsed.hostname.lower(), "env_url", parsed.scheme or None, parsed.port

    for token in re.split(r"[,;\s]+", raw):
        token = token.strip()
        if not token or "://" in token:
            continue
        m = _HOST_WITH_PORT.match(token)
        if m:
            yield m.group(1).lower(), "env_host_port", None, int(m.group(2)) if m.group(2) else None


def _annotation_dependencies(pod: V1Pod) -> Iterable[str]:
    if not pod.metadata:
        return
    ann = pod.metadata.annotations or {}
    raw = ann.get(_DEPENDENCY_ANNOTATION)
    if not raw:
        return
    for item in raw.split(","):
        dep = item.strip()
        if dep:
            yield dep


def _dependency_target_name(raw: str) -> str:
    value = raw.strip()
    if ":" in value:
        value = value.split(":", 1)[1].strip()
    return value.split(".", 1)[0].lower()


def _is_meaningful_external(host: str) -> bool:
    h = host.strip().lower()
    if not h or h in {"localhost", "127", "127.0.0.1", "0.0.0.0"}:
        return False
    if h.endswith(".local") or h.endswith(".localhost"):
        return False
    if re.fullmatch(r"\d+(?:\.\d+){0,3}", h):
        return False
    if h in {"true", "false", "none", "null"}:
        return False
    return bool(re.search(r"[a-z]", h))


def _is_external_dependency_candidate(host: str, source: str, port: int | None) -> bool:
    if not _is_meaningful_external(host):
        return False
    if source == "annotation":
        return "." in host
    if source == "env_url":
        return "." in host
    if source == "env_host_port":
        return port is not None and "." in host
    return False


def _merge_sources(existing: Dict[str, Any], source: str, confidence: float, evidence: str) -> None:
    sources = set(str(existing.get("inferred_from") or "").split(",")) if existing.get("inferred_from") else set()
    sources.add(source)
    sources.discard("")
    evidence_items = list(existing.get("evidence") or [])
    if evidence and evidence not in evidence_items:
        evidence_items.append(evidence)
    source_count = len(sources)
    existing["inferred_from"] = ",".join(sorted(sources))
    existing["evidence"] = evidence_items
    existing["confidence"] = min(0.95, max(float(existing.get("confidence") or 0.0), confidence) + 0.05 * max(0, source_count - 1))


def _add_edge(
    edge_map: Dict[tuple[str, str, str], Dict[str, Any]],
    src: str,
    tgt: str,
    typ: str,
    inferred_from: str,
    evidence: str,
    confidence: float,
    protocol: str | None = None,
    port: int | None = None,
) -> None:
    if not src or not tgt or tgt == src:
        return
    key = (src, tgt, typ)
    edge = edge_map.get(key)
    if edge is None:
        edge = {"from": src, "to": tgt, "type": typ}
        if protocol:
            edge["protocol"] = protocol
        if port:
            edge["port"] = port
        edge_map[key] = edge
    _merge_sources(edge, inferred_from, confidence, evidence)


def build_topology(
    pods: List[V1Pod],
    services: List[V1Service],
    app_names: Set[str],
    config_maps: List[V1ConfigMap] | None = None,
    secrets: List[V1Secret] | None = None,
) -> Dict[str, Any]:
    """
    Return service topology with legacy internal edges plus external edge candidates.

    ``edges`` contains only dependencies whose target resolves to a known app.
    ``external_edges`` is additive metadata used by the UI graph builder.
    """
    pod_labels_by_ns: Dict[tuple[str, str], Dict[str, str]] = {}
    for pod in pods:
        if not pod.metadata or not pod.metadata.name:
            continue
        ns = pod.metadata.namespace or ""
        labels = pod.metadata.labels or {}
        pod_labels_by_ns[(ns, pod.metadata.name)] = labels

    services_sorted = sorted(app_names)
    edges_by_key: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    external_edges_by_key: Dict[tuple[str, str, str], Dict[str, Any]] = {}

    for pod in pods:
        src = app_name_for_pod(pod)
        if not src:
            continue
        for dep in _annotation_dependencies(pod):
            host = _dependency_target_name(dep)
            tgt = _target_app_for_hostname(host, app_names, services, pod_labels_by_ns)
            if tgt:
                typ = _edge_type_for_dependency(dep, tgt)
                _add_edge(
                    edges_by_key,
                    src,
                    tgt,
                    typ,
                    "annotation",
                    f"{_DEPENDENCY_ANNOTATION}={dep}",
                    _SOURCE_CONFIDENCE["annotation"],
                )
            elif _is_external_dependency_candidate(host, "annotation", None):
                typ = _type_from_text(dep, default="unknown")
                _add_edge(
                    external_edges_by_key,
                    src,
                    host,
                    typ,
                    "annotation",
                    f"{_DEPENDENCY_ANNOTATION}={dep}",
                    _SOURCE_CONFIDENCE["annotation"],
                )
        for env_name, val, evidence in _env_values(pod, config_maps=config_maps, secrets=secrets):
            for host, source, protocol, port in _candidate_hosts(val):
                tgt = _target_app_for_hostname(host, app_names, services, pod_labels_by_ns)
                if tgt:
                    typ = _type_from_text(val, default=_edge_type_for_target(tgt))
                    _add_edge(
                        edges_by_key,
                        src,
                        tgt,
                        typ,
                        source,
                        evidence,
                        _SOURCE_CONFIDENCE.get(source, 0.65),
                        protocol,
                        port,
                    )
                elif _is_external_dependency_candidate(host, source, port):
                    typ = _type_from_text(val, default="external_api" if "." in host else "unknown")
                    _add_edge(
                        external_edges_by_key,
                        src,
                        host,
                        typ,
                        "external_hostname" if source == "env_url" and "." in host else source,
                        evidence,
                        _SOURCE_CONFIDENCE["external_hostname"] if "." in host else _SOURCE_CONFIDENCE.get(source, 0.45),
                        protocol,
                        port,
                    )

    return {
        "services": services_sorted,
        "edges": sorted(edges_by_key.values(), key=lambda e: (e["from"], e["to"], e["type"])),
        "external_edges": sorted(external_edges_by_key.values(), key=lambda e: (e["from"], e["to"], e["type"])),
    }
