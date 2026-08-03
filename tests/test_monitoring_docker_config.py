from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_full_compose_prometheus_scrapes_qa_platform_service():
    """The all-Docker recording stack must use Compose DNS, not the host gateway."""
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    prometheus_config = (
        REPO_ROOT / "infra" / "prometheus" / "prometheus.docker.yml"
    ).read_text(encoding="utf-8")

    assert "./infra/prometheus/prometheus.docker.yml:/etc/prometheus/prometheus.yml:ro" in compose
    assert 'targets: ["qa-platform:8000"]' in prometheus_config
    assert "host.docker.internal" not in prometheus_config


def test_local_monitoring_stack_keeps_host_scrape_target():
    """The separate monitoring-only stack still supports a host-run Uvicorn app."""
    local_config = (
        REPO_ROOT / "infra" / "prometheus" / "prometheus.yml"
    ).read_text(encoding="utf-8")

    assert 'targets: ["host.docker.internal:8000"]' in local_config
