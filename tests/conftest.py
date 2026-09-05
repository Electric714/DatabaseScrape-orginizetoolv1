import pytest
from app import database as db
from app.models import SourceCreate


@pytest.fixture
async def database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    await db.init_db()
    return db


@pytest.fixture
async def source(database):
    data = SourceCreate(name="Fixture", start_url="https://fixture.test/", delay_ms=0, render_mode="http").model_dump(mode="json")
    return await database.create_source(data)
