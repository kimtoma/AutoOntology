"""Real local Postgres + Ollama. Run with: AUTOONTOLOGY_INTEGRATION=1 uv run pytest -m integration"""

import httpx
import pytest

from autoontology import doctor
from autoontology.config import Settings
from autoontology.engine import pg
from autoontology.engine.migrate import current_revision, head_revision, run_migrations
from autoontology.engine.supervisor import Supervisor

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def settings():
    return Settings.from_env()


def test_setup_db_is_idempotent(settings):
    first = pg.setup_database(settings)
    second = pg.setup_database(settings)
    assert "database autoontology" in first and "(exists)" in second
    assert pg.database_exists(settings)
    assert pg.extension_version(settings, "vector")
    assert pg.extension_version(settings, "pg_trgm")


def test_migrate_reaches_head(settings):
    run_migrations(settings)
    run_migrations(settings)  # idempotent
    assert current_revision(settings) == head_revision() is not None


def test_doctor_all_green_with_models(settings):
    checks = doctor.run_checks(settings)
    print("\n" + doctor.format_checks(checks))
    failing = [c for c in checks if c.status in ("FAIL", "WARN")]
    # Ports may be legitimately owned by an already running AutoOntology.
    assert failing == [], doctor.format_checks(failing)
    embed = next(c for c in checks if c.name == "embedding endpoint")
    assert "dim 1024" in embed.detail


def test_up_health_down(settings):
    sup = Supervisor.from_settings(settings)
    if any(row.running for row in sup.status()):
        pytest.skip("AutoOntology is already running; not touching a live instance")
    try:
        sup.up()
        backend = httpx.get(f"{settings.backend_url}/api/health", timeout=10)
        assert backend.status_code == 200, backend.text
        assert backend.json()["migrations"]["status"] == "ok"
        service = httpx.get(f"{settings.service_url}/health", timeout=10)
        assert service.status_code == 200, service.text
        assert all(row.healthy for row in sup.status())
        sup.up()  # idempotent
    finally:
        sup.down()
    assert not any(row.running for row in sup.status())
    with pytest.raises(httpx.ConnectError):
        httpx.get(f"{settings.service_url}/health/live", timeout=2)
