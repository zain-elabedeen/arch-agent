from unittest.mock import Mock

from agent.app.connectors.kubernetes.collector import collect


def test_collector_never_requests_kubernetes_secret_payloads():
    apis = Mock()
    apis.core.list_pod_for_all_namespaces.return_value.items = []
    apis.apps.list_deployment_for_all_namespaces.return_value.items = []
    apis.core.list_service_for_all_namespaces.return_value.items = []
    apis.core.list_config_map_for_all_namespaces.return_value.items = []
    apis.custom.list_cluster_custom_object.return_value = {"items": []}
    apis.autoscaling.list_horizontal_pod_autoscaler_for_all_namespaces.return_value.items = []

    collect(apis)

    assert not apis.core.list_secret_for_all_namespaces.called
