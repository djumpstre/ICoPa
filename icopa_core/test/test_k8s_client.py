from icopa_core.connectors.k8s_client import K8sClient


def test_resolve_zenoh_router_port_from_open_ports() -> None:
    client = K8sClient(namespace="")
    networking = {
        "public": True,
        "openPorts": [
            {"proto": "tcp", "port": 22, "purpose": "ssh"},
            {"proto": "tcp", "port": 9099, "purpose": "zenoh-router"},
        ],
    }

    assert client.resolve_zenoh_router_port(networking) == 9099


def test_resolve_zenoh_router_port_from_legacy_firewall_shape() -> None:
    client = K8sClient(namespace="")
    networking = {
        "firewall": {
            "openPorts": [
                {"proto": "tcp", "port": "7447", "purpose": "zenoh-router"},
            ]
        }
    }

    assert client.resolve_zenoh_router_port(networking) == 7447


def test_resolve_zenoh_router_port_uses_default_when_not_found() -> None:
    client = K8sClient(namespace="")

    assert client.resolve_zenoh_router_port({"openPorts": []}) == 7447
