"""Chooser membership is profile-owned, including cache and live return paths."""
import copy
import json
import sys
import types

import pytest

from api import config, provider_manager as editor
from api.provider_catalog_policy import CatalogScope
from tests import test_provider_manager as editor_tests

store = editor_tests.store


@pytest.fixture
def catalog_store(store, monkeypatch):
    path, data, events = store
    registry = {
        "opencode-go": types.SimpleNamespace(auth_type="api_key", name="OpenCode Go", api_key_env_vars=("OPENCODE_GO_API_KEY",), inference_base_url="https://go.invalid/v1"),
        "xai-oauth": types.SimpleNamespace(auth_type="oauth_browser", name="xAI OAuth", inference_base_url="https://xai.invalid/v1"),
        "gemini": types.SimpleNamespace(auth_type="api_key", name="Google", api_key_env_vars=("GEMINI_API_KEY",), inference_base_url="https://google.invalid/v1"),
    }
    monkeypatch.setitem(sys.modules, "hermes_cli.auth", types.SimpleNamespace(PROVIDER_REGISTRY=registry))
    monkeypatch.setitem(config._PROVIDER_MODELS, "xai-oauth", [{"id": "grok-chat", "label": "Grok"}])
    return path, data, events


def test_active_oauth_appears_as_editable_without_exposing_credentials(catalog_store):
    path, data, _ = catalog_store
    data["model"] = {"provider": "xai-oauth", "default": "grok-current"}
    path.write_text(editor.yaml.safe_dump(data))
    (path.parent / "auth.json").write_text(json.dumps({"providers": {"xai-oauth": {"access_token": "synthetic-oauth-secret"}}}))
    before = path.read_bytes()
    view = editor.read_editor()
    row = next(p for p in view["providers"] if p["provider_id"] == "xai-oauth")
    assert row["is_oauth"] and row["ref"] == "builtin:xai-oauth"
    assert {m["id"] for m in row["models"]} == {"grok-current", "grok-chat"}
    assert "synthetic-oauth-secret" not in json.dumps(view)
    assert path.read_bytes() == before


@pytest.mark.parametrize("section,entry", [
    ("providers", {"tokens": {"access_token": "synthetic-oauth-secret"}}),
    ("credential_pool", [{"auth_type": "oauth", "refresh_token": "synthetic-oauth-secret"}]),
])
def test_profile_oauth_login_is_visible_without_being_default(catalog_store, monkeypatch, tmp_path, section, entry):
    path, data, _ = catalog_store
    auth = path.parent / "auth.json"
    auth.write_text(json.dumps({section: {"xai-oauth": entry}}))
    row = next(row for row in editor.read_editor()["providers"] if row["provider_id"] == "xai-oauth")
    assert row["is_oauth"] and row["credential_configured"]
    assert "synthetic-oauth-secret" not in json.dumps(editor.read_editor())
    data["webui"] = {"provider_manager": {"removed_providers": ["xai-oauth"]}}
    path.write_text(editor.yaml.safe_dump(data))
    assert not any(row["provider_id"] == "xai-oauth" for row in editor.read_editor()["providers"])
    assert "synthetic-oauth-secret" in auth.read_text(), "removal must not revoke login"
    data.pop("webui")
    path.write_text(editor.yaml.safe_dump(data))
    other_home = tmp_path / "other-profile"
    other_home.mkdir()
    monkeypatch.setattr(editor.profiles, "get_active_hermes_home", lambda: other_home)
    assert not any(row["provider_id"] == "xai-oauth" for row in editor.read_editor()["providers"]), "never inherit another profile's OAuth"


def test_primary_and_live_share_removal_enabled_and_fixed_membership(catalog_store):
    path, data, _ = catalog_store
    (path.parent / ".env").write_text("GEMINI_API_KEY=synthetic-google\nOPENCODE_GO_API_KEY=synthetic-go\n")
    data["webui"] = {"provider_manager": {"removed_providers": ["gemini"]}}
    data["providers"]["opencode-go"] = {"models": {"exact/model:free": {}}, "discover_models": False}
    data["providers"]["custom:relay"]["enabled"] = False
    path.write_text(editor.yaml.safe_dump(data))
    scope = CatalogScope.capture()
    payload = {"active_provider": "opencode-go", "default_model": "exact/model:free", "groups": [
        {"provider_id": "gemini", "models": [{"id": "hidden"}]},
        {"provider_id": "openai-codex", "models": [{"id": "ambient-auth"}]},
        {"provider_id": "custom:relay", "models": [{"id": "disabled"}]},
        {"provider_id": "opencode-go", "models": [{"id": "unimported"}]}]}
    original = copy.deepcopy(payload)
    result = scope.main(payload)
    assert [g["provider_id"] for g in result["groups"]] == ["opencode-go"]
    assert [m["id"] for m in result["groups"][0]["models"]] == ["exact/model:free"]
    assert scope.live("opencode-go", {"models": [{"id": "unimported"}]})["models"][0]["id"] == "exact/model:free"
    assert scope.live("gemini", {"models": [{"id": "hidden"}]})["models"] == []
    assert result["catalog_scope"]["profile"] == result["profile"] == "fixture-profile"
    assert payload == original


def test_oauth_presence_revokes_scope_but_token_rotation_does_not(catalog_store):
    path, _, _ = catalog_store
    auth = path.parent / "auth.json"
    auth.write_text(json.dumps({"providers": {"xai-oauth": {"tokens": {"access_token": "synthetic-first"}}}}))
    scope = CatalogScope.capture()
    auth.write_text(json.dumps({"providers": {"xai-oauth": {"tokens": {"access_token": "synthetic-rotated"}}}}))
    scope.assert_current()
    auth.write_text(json.dumps({"providers": {"xai-oauth": {"tokens": {}}}}))
    with pytest.raises(editor.EditorError):
        scope.assert_current()


def test_follow_mode_filters_generation_and_does_not_collapse_model_routes(catalog_store):
    path, data, _ = catalog_store
    data["providers"]["custom:relay"]["discover_models"] = True
    path.write_text(editor.yaml.safe_dump(data))
    scope = CatalogScope.capture()
    rows = [{"id": "vendor/model:free"}, {"id": "vendor/model:thinking"}, {"id": "grok-imagine-video"}, {"id": "grok-imagine-image"}, {"id": "vision-vl-chat"}]
    assert [r["id"] for r in scope.live("custom:relay", {"models": rows})["models"]] == ["vendor/model:free", "vendor/model:thinking", "vision-vl-chat"]


def test_empty_fixed_catalog_never_falls_back_to_vendor_models(catalog_store):
    path, data, _ = catalog_store
    data["providers"]["custom:relay"].update(models={}, discover_models=False)
    path.write_text(editor.yaml.safe_dump(data))
    scope = CatalogScope.capture()
    assert scope.live("custom:relay", {"models": [{"id": "unexpected"}]})["models"] == []
    assert scope.main({"groups": [{"provider_id": "custom:relay", "models": [{"id": "unexpected"}]}]})["groups"] == []


def test_follow_catalog_keeps_saved_imports_when_inventory_is_partial(catalog_store):
    path, data, _ = catalog_store
    data["providers"]["custom:relay"].update(discover_models=True, models={"imported/new": {}, "tail/model": {}})
    path.write_text(editor.yaml.safe_dump(data))
    result = CatalogScope.capture().main({"groups": [{"provider_id": "custom:relay", "models": [{"id": "vendor/live", "supports_fast_tier": True}], "extra_models": [{"id": "tail/model"}]}]})
    group = result["groups"][0]
    assert [model["id"] for model in group["models"]] == ["vendor/live", "imported/new"]
    assert group["models"][0]["supports_fast_tier"]
    assert [model["id"] for model in group["extra_models"]] == ["tail/model"]


def test_scope_revokes_inflight_catalog_after_profile_or_config_change(catalog_store, monkeypatch):
    path, _, _ = catalog_store
    scope = CatalogScope.capture()
    path.write_text(path.read_text() + "\n# changed\n")
    with pytest.raises(editor.EditorError) as exc:
        scope.assert_current()
    assert exc.value.status == 409
    scope = CatalogScope.capture()
    monkeypatch.setattr(editor, "_profile", lambda: "other-profile")
    with pytest.raises(editor.EditorError):
        scope.assert_current()


def test_oauth_discovery_uses_agent_and_rejects_changed_owner(catalog_store, monkeypatch):
    from api import provider_manager_probe as probe
    import hermes_cli.models
    path, data, _ = catalog_store
    data["model"] = {"provider": "xai-oauth", "default": "grok-current"}
    path.write_text(editor.yaml.safe_dump(data))
    view = editor.read_editor()
    body = {"profile": view["profile"], "revision": view["revision"], "ref": "builtin:xai-oauth"}
    calls = []
    def models(provider):
        calls.append((provider, editor.profiles.get_active_hermes_home()))
        return ["grok-chat", "grok-imagine-image", "grok-imagine-video"]
    monkeypatch.setattr(hermes_cli.models, "provider_model_ids", models)
    assert [row["id"] for row in probe.discover(body)["models"]] == ["grok-chat"]
    assert calls == [("xai-oauth", path.parent)]
    def changed(provider):
        path.write_text(path.read_text() + "\n# config changed during probe\n")
        return ["must-not-publish"]
    monkeypatch.setattr(hermes_cli.models, "provider_model_ids", changed)
    with pytest.raises(editor.EditorError) as exc:
        probe.discover(body)
    assert exc.value.status == 409


@pytest.mark.parametrize("freshness", ["", "session_visit"])
def test_main_http_route_filters_cached_inventory(catalog_store, monkeypatch, freshness):
    from api import routes
    from urllib.parse import urlparse
    monkeypatch.setattr(routes, "_handle_extension_sidecar_proxy", lambda *_: False)
    monkeypatch.setattr(routes, "j", lambda handler, value, **kwargs: value)
    raw = {"active_provider": "openai", "default_model": "hidden-default", "groups": [
        {"provider_id": "openai", "models": [{"id": "ambient-model"}]},
        {"provider_id": "custom:relay", "models": [{"id": "vendor/model:free"}]}]}
    monkeypatch.setattr(routes, "get_available_models", lambda: copy.deepcopy(raw))
    monkeypatch.setattr(routes, "get_available_models_for_session_visit", lambda: copy.deepcopy(raw))
    result = routes.handle_get(types.SimpleNamespace(), urlparse("/api/models" + ("?freshness=" + freshness if freshness else "")))
    assert [group["provider_id"] for group in result["groups"]] == ["custom:relay"]
    assert result["profile"] == "fixture-profile" and result["catalog_scope"]["revision"] == result["catalog_revision"]


def test_fixed_routes_preserve_punctuation_and_metadata(catalog_store):
    path, data, _ = catalog_store
    ids = ["vendor/model-a:free", "vendor/model.a:free", "vendor/model-a:thinking"]
    data["providers"]["custom:relay"].update(discover_models=False, models={mid: {} for mid in ids})
    path.write_text(editor.yaml.safe_dump(data, sort_keys=False))
    scope = CatalogScope.capture()
    rows = [{"id": "@custom:relay:" + mid, "label": mid, "supports_fast_tier": True} for mid in ids]
    result = scope.main({"groups": [{"provider_id": "custom:relay", "models": rows}]})
    assert [row["id"] for row in result["groups"][0]["models"]] == ids
    assert all(row["supports_fast_tier"] for row in result["groups"][0]["models"])


def test_legacy_allowlist_can_empty_or_explicitly_follow(catalog_store):
    path, data, _ = catalog_store
    data["providers"]["custom:relay"]["models"] = ["vendor/model:free"]
    path.write_text(editor.yaml.safe_dump(data))
    view = editor.read_editor()
    row = view["providers"][0]
    assert row["discover_models"] is False
    body = {"op": "save", "profile": view["profile"], "revision": view["revision"], "ref": row["ref"]}
    result = editor.write_editor({**body, "changes": {"models": []}})
    assert result["providers"][0]["discover_models"] is False
    assert CatalogScope.capture().live("custom:relay", {"models": [{"id": "must-not-reappear"}]})["models"] == []
    result = editor.write_editor({**body, "revision": result["revision"], "changes": {"discover_models": True}})
    assert result["providers"][0]["discover_models"] is True
    assert editor.yaml.safe_load(path.read_text())["providers"]["custom:relay"]["models_discovered"] is True


def test_oauth_fixed_save_preserves_auth_and_filters_both_catalogs(catalog_store):
    path, data, _ = catalog_store
    data["model"] = {"provider": "xai-oauth", "default": "grok-current"}
    path.write_text(editor.yaml.safe_dump(data))
    auth = path.parent / "auth.json"
    auth.write_text(json.dumps({"providers": {"xai-oauth": {"access_token": "synthetic-token-retain"}}}))
    before = auth.read_bytes()
    view = editor.read_editor()
    result = editor.write_editor({"op": "save", "profile": view["profile"], "revision": view["revision"],
        "ref": "builtin:xai-oauth", "changes": {"discover_models": False, "models": [{"id": "grok-current"}]}})
    assert result["saved_ref"] == "providers:xai-oauth"
    saved = editor.yaml.safe_load(path.read_text())["providers"]["xai-oauth"]
    assert "api_key" not in saved and auth.read_bytes() == before
    scope = CatalogScope.capture()
    assert [row["id"] for row in scope.live("xai-oauth", {"models": [{"id": "unimported"}]})["models"]] == ["grok-current"]
    data = editor.yaml.safe_load(path.read_text())
    data["providers"]["xai-oauth"]["base_url"] = "https://unverified-destination.invalid/v1"
    path.write_text(editor.yaml.safe_dump(data))
    current = editor.read_editor()
    with pytest.raises(editor.EditorError):
        editor.discovery_record({"profile": current["profile"], "revision": current["revision"], "ref": "providers:xai-oauth"})
