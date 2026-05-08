from pathlib import Path

from agent.app.services.pattern_loader import PatternStore


def test_pattern_store_loads_all_patterns():
    patterns_path = Path(__file__).resolve().parents[1] / "agent" / "app" / "patterns"
    store = PatternStore.load_patterns(str(patterns_path))

    assert len(store.get_all()) >= 15
    assert store.get_by_id("read_replicas") is not None
    assert store.get_by_id("queue_partitioning") is not None


def test_pattern_store_smell_mapping_is_ranked_and_deterministic():
    patterns_path = Path(__file__).resolve().parents[1] / "agent" / "app" / "patterns"
    store = PatternStore.load_patterns(str(patterns_path))

    ranked = store.get_ranked_patterns_for_smell("queue_backlog")
    ranked_ids = [item["pattern"].id for item in ranked]
    priorities = [item["priority"] for item in ranked]

    assert ranked_ids == ["queue_partitioning", "backpressure", "async_processing"]
    assert priorities == sorted(priorities)
    assert store.get_patterns_for_smell("unknown_smell") == []


def test_kubernetes_native_smells_map_to_existing_patterns():
    patterns_path = Path(__file__).resolve().parents[1] / "agent" / "app" / "patterns"
    store = PatternStore.load_patterns(str(patterns_path))

    for smell in (
        "memory_pressure",
        "restart_instability",
        "replica_unavailability",
        "autoscaling_pressure",
        "single_instance_risk",
    ):
        assert store.get_patterns_for_smell(smell), smell


def test_log_backed_smells_map_to_existing_patterns():
    patterns_path = Path(__file__).resolve().parents[1] / "agent" / "app" / "patterns"
    store = PatternStore.load_patterns(str(patterns_path))

    for smell in (
        "error_burst",
        "timeout_pressure",
        "dependency_instability",
        "probe_instability",
        "crash_loop_signal",
    ):
        assert store.get_patterns_for_smell(smell), smell
