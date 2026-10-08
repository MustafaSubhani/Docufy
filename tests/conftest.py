import pytest

from spark_agent import memory, store


@pytest.fixture(autouse=True)
def isolated_memory(tmp_path):
    token = memory.use(tmp_path / "memory.db")
    yield
    memory.reset(token)


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    """Every test gets its own database and studio folder, so a run never writes to the real workspace."""
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "spark.db")
    monkeypatch.setattr(store, "_conn", None)
    monkeypatch.setenv("SPARK_STUDIO_DIR", str(tmp_path / "studio"))
