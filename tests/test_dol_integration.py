from app import main
from app.osha_adapter import DOL_INSPECTION_ENDPOINT
from app.models import SourceCreate, SourceUpdate


async def test_builtin_osha_source_is_created_once(database):
    first = await main.ensure_builtin_osha_source()
    second = await main.ensure_builtin_osha_source()
    sources = await database.list_sources()

    assert first["id"] == second["id"]
    assert first["name"] == main.OSHA_SOURCE_NAME
    assert first["start_url"] == DOL_INSPECTION_ENDPOINT
    assert first["render_mode"] == "http"
    assert not bool(first["respect_robots"])
    assert len([source for source in sources if main._is_osha_source(source)]) == 1


async def test_builtin_osha_reuses_legacy_source_without_losing_identity(database):
    legacy = await database.create_source({
        "name": "Old OSHA source",
        "start_url": "https://www.osha.gov/ords/imis/establishment.html",
        "auto_scan": False,
        "interval_minutes": 60,
        "max_pages": 100,
        "max_depth": 12,
        "concurrency": 2,
        "delay_ms": 500,
        "render_mode": "browser",
        "respect_robots": False,
    })

    built_in = await main.ensure_builtin_osha_source()
    sources = await database.list_sources()

    assert built_in["id"] == legacy["id"]
    assert built_in["name"] == main.OSHA_SOURCE_NAME
    assert built_in["render_mode"] == "http"
    assert built_in["max_depth"] == 4
    assert built_in["concurrency"] == 4
    assert len([source for source in sources if main._is_osha_source(source)]) == 1


async def test_api_key_is_tested_before_local_save(database, monkeypatch):
    observed = {}
    saved = {}

    async def fake_validate(value):
        observed["value"] = value

    def fake_save(value):
        saved["value"] = value

    monkeypatch.setattr(main, "validate_dol_api_key", fake_validate)
    monkeypatch.setattr(main, "save_dol_api_key", fake_save)

    payload = main.DolApiKeyPayload(api_key="synthetic-valid-key-12345")
    result = await main.configure_dol_integration(payload)

    assert observed["value"] == "synthetic-valid-key-12345"
    assert saved["value"] == "synthetic-valid-key-12345"
    assert result["configured"] is True
    assert result["validated"] is True

    status = await main.dol_integration_status()
    assert "api_key" not in status
    assert "key" not in {name.lower() for name in status if name.lower() == "api_key"}


async def test_rejected_api_key_is_not_saved(database, monkeypatch):
    saved = []

    async def fake_validate(_value):
        raise ValueError("DOL rejected this API key")

    monkeypatch.setattr(main, "validate_dol_api_key", fake_validate)
    monkeypatch.setattr(main, "save_dol_api_key", lambda value: saved.append(value))

    try:
        await main.configure_dol_integration(
            main.DolApiKeyPayload(api_key="synthetic-invalid-key-12345")
        )
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 422
        assert "rejected" in str(getattr(exc, "detail", "")).lower()
    else:
        raise AssertionError("Invalid DOL key should be rejected")

    assert saved == []


async def test_builtin_osha_cannot_be_edited_deleted_or_duplicated(database):
    source = await main.ensure_builtin_osha_source()

    try:
        await main.patch_source(source["id"], SourceUpdate(name="Changed OSHA"))
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
    else:
        raise AssertionError("Built-in OSHA source should not be editable")

    try:
        await main._delete_source(source["id"])
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
    else:
        raise AssertionError("Built-in OSHA source should not be removable")

    duplicate = SourceCreate(
        name="Duplicate OSHA",
        start_url=DOL_INSPECTION_ENDPOINT,
        render_mode="http",
        respect_robots=False,
    )
    try:
        await main.post_source(duplicate)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
        assert "built in" in str(getattr(exc, "detail", "")).lower()
    else:
        raise AssertionError("Generic source setup should not create a second OSHA source")
