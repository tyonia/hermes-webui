"""Config round trips use disposable files, not Agent sessions or credentials."""
import copy
import json
import os
import urllib.error
import urllib.request

import pytest

from api import config, provider_manager as editor, yaml_compat as yaml

_REAL_PROFILE_OWNER = editor._profile


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    monkeypatch.setattr(config, "_get_config_path", lambda: path)
    monkeypatch.setattr(editor, "_profile", lambda: "fixture-profile")
    monkeypatch.setattr(editor.profiles, "get_active_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(config, "reload_config", lambda: None)
    events = []
    monkeypatch.setattr(config, "invalidate_models_cache", lambda: events.append("models"))
    from api import providers, routes
    monkeypatch.setattr(providers, "invalidate_providers_cache", lambda: events.append("providers"))
    monkeypatch.setattr(routes, "_clear_live_models_cache", lambda: events.append("live"))
    data = {
        "model": {"provider": "openai", "default": "untouched-default"},
        "tools": {"custom": "${UNCHANGED_REFERENCE}"},
        "providers": {
            "custom:relay": {"name": "Relay", "api": "https://relay.invalid/v1",
                             "transport": "codex_responses", "api_key": "fixture-secret-keep",
                             "extra_headers": {"X-Secret": "fixture-header-keep"},
                             "ssl_verify": True, "unknown_option": {"keep": 42},
                             "models": {"vendor/model:free": {"context_length": 1234,
                                                            "unknown_metadata": {"preserve": True}}}},
        },
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path, data, events


def operation(op="save", **extra):
    view = editor.read_editor()
    return {"profile": view["profile"], "revision": view["revision"],
            "ref": view["providers"][0]["ref"] if view["providers"] else "",
            "op": op, **extra}


def raw(path):
    return yaml.safe_load(path.read_text())


def test_projection_redacts_secrets_without_resolving_them(store):
    view = editor.read_editor()
    encoded = json.dumps(view)
    assert "fixture-secret" not in encoded
    assert "fixture-header" not in encoded
    assert "unknown_option" not in encoded
    row = view["providers"][0]
    assert row["base_url"] == "https://relay.invalid/v1"
    assert row["api_mode"] == "codex_responses"
    assert row["credential_configured"]
    assert row["headers"] == [{"name": "X-Secret", "configured": True}]


def test_invalid_or_deleted_profile_cannot_fall_back_to_root(tmp_path, monkeypatch):
    from api import profiles
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "../../outside")
    with pytest.raises(editor.EditorError) as exc:
        _REAL_PROFILE_OWNER()
    assert exc.value.status == 403
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "deleted-profile")
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path / "missing")
    with pytest.raises(editor.EditorError) as exc:
        _REAL_PROFILE_OWNER()
    assert exc.value.status == 409


def test_model_rename_keeps_metadata_without_stranding_references(store):
    path, _, _ = store
    editor.write_editor(operation(changes={"models": [{"id": "renamed/model", "original_id": "vendor/model:free"}]}))
    metadata = raw(path)["providers"]["custom:relay"]["models"]["renamed/model"]
    assert metadata["context_length"] == 1234
    assert metadata["unknown_metadata"] == {"preserve": True}


def test_patch_preserves_original_owner_unknown_fields_and_credentials(store):
    path, original, events = store
    result = editor.write_editor(operation(changes={"name": "New display label"}))
    expected = copy.deepcopy(original)
    expected["providers"]["custom:relay"]["name"] = "New display label"
    assert raw(path) == expected
    assert result["providers"][0]["provider_id"] == "custom:relay"
    assert set(events) == {"models", "providers", "live"}


def test_model_edit_preserves_unknown_metadata_and_exact_id(store):
    path, _, _ = store
    editor.write_editor(operation(changes={"models": [{"id": "vendor/model:free", "label": "My model", "context_length": 8192}]}))
    meta = raw(path)["providers"]["custom:relay"]["models"]["vendor/model:free"]
    assert meta == {"context_length": 8192, "label": "My model", "unknown_metadata": {"preserve": True}}


@pytest.mark.parametrize("kind", ["list", "string"])
def test_model_allowlist_shape_does_not_turn_into_unfiltered_mapping(store, kind):
    path, data, _ = store
    models = ["vendor/model:free"]
    data["providers"]["custom:relay"]["models"] = models if kind == "list" else json.dumps(models)
    path.write_text(yaml.safe_dump(data))
    editor.write_editor(operation(changes={"models": [{"id": "vendor/model:free", "context_length": 8192}]}))
    assert raw(path)["providers"]["custom:relay"]["models"] == [{"id": "vendor/model:free", "context_length": 8192}]


def test_legacy_changes_write_original_store_without_migrating(store):
    path, data, _ = store
    entry = data["providers"].pop("custom:relay")
    entry["name"] = "relay"
    data["custom_providers"] = [entry]
    path.write_text(yaml.safe_dump(data))
    editor.write_editor(operation(changes={"name": "New label"}))
    saved = raw(path)
    assert saved["custom_providers"][0]["name"] == "relay"
    assert saved["custom_providers"][0]["display_name"] == "New label"
    assert saved["providers"] == {}


@pytest.mark.parametrize("legacy", [False, True])
def test_create_uses_visible_native_or_legacy_store(store, legacy):
    path, data, _ = store
    if legacy:
        data["custom_providers"] = []
        path.write_text(yaml.safe_dump(data))
    result = editor.write_editor(operation("create", id="local-models", changes={"base_url": "http://127.0.0.1:8000/v1"}))
    row = next(p for p in result["providers"] if p["provider_id"] == "custom:local-models")
    assert row["source"] == ("custom_providers" if legacy else "providers")
    assert row["discover_models"] is False


@pytest.mark.parametrize("changes", [{"api_mode": "unsupported"}, {"models": [{"id": ""}]},
                                     {"models": [{"id": "one"}, {"id": "one"}]},
                                     {"models": [{"id": "one", "context_length": True}]},
                                     {"base_url": "https://user:password@relay.invalid"},
                                     {"unexpected": True}])
def test_invalid_draft_never_changes_file(store, changes):
    path, _, _ = store
    before = path.read_bytes()
    with pytest.raises(editor.EditorError):
        editor.write_editor(operation(changes=changes))
    assert path.read_bytes() == before


def test_stale_revision_and_cross_profile_drafts_are_rejected(store, monkeypatch):
    path, _, _ = store
    body = operation(changes={"name": "stale"})
    path.write_text(path.read_text() + "\n# CLI changed configuration\n")
    before = path.read_bytes()
    with pytest.raises(editor.EditorError, match="Profile or configuration changed"):
        editor.write_editor(body)
    assert path.read_bytes() == before
    body = operation(changes={"name": "foreign"})
    monkeypatch.setattr(editor, "_profile", lambda: "other-profile")
    with pytest.raises(editor.EditorError) as exc:
        editor.write_editor(body)
    assert exc.value.status == 409
    assert path.read_bytes() == before


@pytest.mark.parametrize("contents", ["model: [invalid", "- unexpected-list", "null"])
def test_bad_yaml_cannot_be_saved_as_empty_config(store, contents):
    path, _, _ = store
    path.write_text(contents)
    with pytest.raises(editor.EditorError) as exc:
        editor.write_editor({"op": "create", "profile": "fixture-profile", "revision": "old", "id": "x"})
    assert exc.value.status == 422
    assert path.read_text() == contents


def test_replacing_or_removing_credentials_is_explicit(store):
    path, _, _ = store
    editor.write_editor(operation(credential={"action": "set", "value": "replacement-fixture-key"}))
    assert raw(path)["providers"]["custom:relay"]["api_key"] == "replacement-fixture-key"
    assert "replacement-fixture-key" not in json.dumps(editor.read_editor())
    editor.write_editor(operation(changes={"key_env": "RELAY_KEY"}))
    assert "api_key" not in raw(path)["providers"]["custom:relay"]
    assert raw(path)["providers"]["custom:relay"]["key_env"] == "RELAY_KEY"
    editor.write_editor(operation(credential={"action": "remove"}))
    assert not editor.read_editor()["providers"][0]["credential_configured"]


def test_headers_keep_replace_remove_and_reject_newlines(store):
    path, _, _ = store
    editor.write_editor(operation(headers=[{"name": "X-Secret", "action": "keep"}, {"name": "X-New", "action": "set", "value": "fixture-new"}]))
    assert raw(path)["providers"]["custom:relay"]["extra_headers"]["X-Secret"] == "fixture-header-keep"
    before = path.read_bytes()
    with pytest.raises(editor.EditorError):
        editor.write_editor(operation(headers=[{"name": "X-Bad", "action": "set", "value": "bad\r\nheader"}]))
    assert path.read_bytes() == before
    editor.write_editor(operation(headers=[{"name": "X-Secret", "action": "remove"}]))
    assert "X-Secret" not in raw(path)["providers"]["custom:relay"]["extra_headers"]


@pytest.mark.parametrize("operation_kind", ["delete", "disable", "model-delete"])
def test_configured_references_are_not_stranded(store, operation_kind):
    path, data, _ = store
    data["auxiliary"] = {"vision": {"provider": "custom:relay", "model": "vendor/model:free"}}
    path.write_text(yaml.safe_dump(data))
    body = operation("delete") if operation_kind == "delete" else operation(changes={"enabled": False} if operation_kind == "disable" else {"models": []})
    before = path.read_bytes()
    with pytest.raises(editor.EditorError) as exc:
        editor.write_editor(body)
    assert exc.value.status == 409
    assert path.read_bytes() == before


def test_disabled_rows_remain_editable(store):
    editor.write_editor(operation(changes={"enabled": False}))
    assert editor.read_editor()["providers"][0]["enabled"] is False
    editor.write_editor(operation(changes={"enabled": True}))
    assert editor.read_editor()["providers"][0]["enabled"] is True


def test_clearing_provider_default_does_not_resurrect_alias(store):
    path, data, _ = store
    data["providers"]["custom:relay"].update(default_model="one", model="old-alias")
    path.write_text(yaml.safe_dump(data))
    editor.write_editor(operation(changes={"default_model": ""}))
    row = raw(path)["providers"]["custom:relay"]
    assert "default_model" not in row and "model" not in row
    assert editor.read_editor()["providers"][0]["default_model"] == ""


def test_symlink_is_preserved_and_retargeted_draft_rejected(store, monkeypatch, tmp_path):
    path, data, _ = store
    link = tmp_path / "linked-config.yaml"
    link.symlink_to(path)
    monkeypatch.setattr(config, "_get_config_path", lambda: link)
    editor.write_editor(operation(changes={"name": "Through link"}))
    assert link.is_symlink()
    assert raw(path)["providers"]["custom:relay"]["name"] == "Through link"
    body = operation(changes={"name": "Wrong target"})
    other = tmp_path / "other.yaml"
    other.write_text(yaml.safe_dump(data))
    link.unlink()
    link.symlink_to(other)
    before = other.read_bytes()
    with pytest.raises(editor.EditorError):
        editor.write_editor(body)
    assert other.read_bytes() == before


def test_duplicate_provider_identity_is_rejected(store):
    with pytest.raises(editor.EditorError) as exc:
        editor.write_editor(operation("create", id="relay", changes={"base_url": "https://other.invalid"}))
    assert exc.value.status == 409


def test_default_uses_canonical_writer_with_revision_guard(store, monkeypatch):
    path, _, _ = store
    calls = []
    def setter(model, provider, config_guard):
        config_guard(path)
        calls.append((model, provider))
    monkeypatch.setattr(config, "set_hermes_default_model", setter)
    editor.write_editor(operation("default", model="vendor/model:free"))
    assert calls == [("vendor/model:free", "custom:relay")]


def test_new_default_guard_rejects_intervening_write(store, monkeypatch):
    path, _, _ = store
    def setter(model, provider, config_guard):
        path.write_text(path.read_text() + "\n# concurrent edit")
        config_guard(path)
        pytest.fail("stale default write must not reach persistence")
    monkeypatch.setattr(config, "set_hermes_default_model", setter)
    with pytest.raises(editor.EditorError):
        editor.write_editor(operation("default", model="vendor/model:free"))


@pytest.mark.parametrize("user_agent_header", [None, "User-Agent", "user-agent"])
def test_discovery_probes_only_saved_owner_with_no_redirects(store, user_agent_header):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from api.provider_manager_probe import discover
    path, data, _ = store
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization"), self.headers.get("X-Secret")))
            # OpenCode's public catalog rejects Python-urllib's default UA.
            # Exercise the actual outgoing HTTP headers without a live key.
            if self.headers.get("User-Agent", "").startswith("Python-urllib"):
                self.send_response(403)
                self.end_headers()
                return
            assert self.headers.get("User-Agent") == ("FixtureClient/2.0" if user_agent_header else "HermesWebUI/1.0")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"data":[{"id":"vendor/new","context_length":8192},{"id":"vendor/new"},{"id":"model:free"}]}')
        def log_message(self, *args):
            pass
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        data["providers"]["custom:relay"]["api"] = f"http://127.0.0.1:{server.server_port}/v1"
        if user_agent_header:
            data["providers"]["custom:relay"]["extra_headers"][user_agent_header] = "FixtureClient/2.0"
        path.write_text(yaml.safe_dump(data))
        before = path.read_bytes()
        result = discover(operation())
        assert requests == [("/v1/models", "Bearer fixture-secret-keep", "fixture-header-keep")]
        assert [row["id"] for row in result["models"]] == ["model:free", "vendor/new"]
        assert path.read_bytes() == before
        assert "fixture-secret" not in json.dumps(result)
        stale = operation()
        stale["profile"] = "foreign"
        with pytest.raises(editor.EditorError):
            discover(stale)
        assert len(requests) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_discovery_redirect_refuses_before_following():
    from api.provider_manager_probe import _NoRedirect
    with pytest.raises(editor.EditorError):
        _NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://other.invalid")


def test_editor_http_boundary():
    from tests._pytest_port import BASE
    with urllib.request.urlopen(BASE + "/api/provider-manager") as response:
        view = json.load(response)
    assert len(view["revision"]) == 64
    with urllib.request.urlopen(BASE + "/api/providers", timeout=30) as response:
        status = json.load(response)
    assert status["profile"] == view["profile"]
    request = urllib.request.Request(BASE + "/api/provider-manager", data=json.dumps({"op": "delete", "profile": "foreign", "revision": "stale"}).encode(), headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request)
    assert exc.value.code == 409


@pytest.fixture
def keyed_builtin(store, monkeypatch):
    path, _, _ = store
    env = path.parent / ".env"
    env.write_text("OPENCODE_GO_API_KEY=fixture-profile-key\n")
    def templates(values):
        return {"opencode-go": {"name": "OpenCode Go", "base_url": "https://opencode.ai/zen/go/v1",
                "key_env": "OPENCODE_GO_API_KEY", "api_mode": "chat_completions", "models": {},
                "discover_models": False, "enabled": True}}
    monkeypatch.setattr(editor, "_builtin_templates", templates, raising=False)
    return path, env


def builtin_body(op="save", **extra):
    view = editor.read_editor()
    return {"op": op, "profile": view["profile"], "revision": view["revision"],
            "ref": "builtin:opencode-go", **extra}


def test_profile_key_has_full_editor_immediately_without_catalog_probe(keyed_builtin):
    path, _ = keyed_builtin
    before = path.read_bytes()
    view = editor.read_editor()
    row = next(p for p in view["providers"] if p["provider_id"] == "opencode-go")
    assert row["ref"] == "builtin:opencode-go"
    assert row["models"] == [] and row["key_env"] == "OPENCODE_GO_API_KEY"
    assert row["credential_configured"]
    assert any(p["provider_id"] == "opencode-go" for p in view["catalog"])
    assert "fixture-profile-key" not in json.dumps(view)
    assert path.read_bytes() == before


def test_builtin_model_edits_materialize_original_identity_without_copying_key(keyed_builtin):
    path, env = keyed_builtin
    result = editor.write_editor(builtin_body(changes={"models": [{"id": "vendor/new:free"}]}))
    saved = raw(path)["providers"]["opencode-go"]
    assert saved["key_env"] == "OPENCODE_GO_API_KEY"
    assert "api_key" not in saved and saved["discover_models"] is False
    assert list(saved["models"]) == ["vendor/new:free"]
    assert result["saved_ref"] == "providers:opencode-go"
    assert len([p for p in result["providers"] if p["provider_id"] == "opencode-go"]) == 1
    view_row = next(p for p in result["providers"] if p["provider_id"] == "opencode-go")
    editor.write_editor({"op": "save", "profile": result["profile"], "revision": result["revision"],
                         "ref": view_row["ref"], "changes": {"models": []}})
    assert raw(path)["providers"]["opencode-go"]["models"] == {}
    assert env.read_text() == "OPENCODE_GO_API_KEY=fixture-profile-key\n"


@pytest.mark.parametrize("materialized", [False, True])
def test_delete_builtin_stays_removed_until_explicit_add(keyed_builtin, materialized):
    path, env = keyed_builtin
    body = builtin_body()
    if materialized:
        result = editor.write_editor(body)
        body.update(revision=result["revision"], ref=result["saved_ref"])
    result = editor.write_editor({**body, "op": "delete"})
    assert not any(p["provider_id"] == "opencode-go" for p in result["providers"])
    assert not any(p["provider_id"] == "opencode-go" for p in editor.read_editor()["providers"])
    assert any(p["provider_id"] == "opencode-go" for p in result["catalog"])
    assert "fixture-profile-key" in env.read_text()
    assert "opencode-go" not in raw(path)["providers"]
    result = editor.write_editor(builtin_body())  # Explicit Add + Save
    assert any(p["provider_id"] == "opencode-go" for p in result["providers"])


def test_builtin_rejects_key_rotation_or_other_profile_and_never_uses_ambient_key(keyed_builtin, monkeypatch):
    _, env = keyed_builtin
    body = builtin_body()
    env.write_text("OPENCODE_GO_API_KEY=rotated-fixture-key\n")
    with pytest.raises(editor.EditorError) as exc:
        editor.write_editor(body)
    assert exc.value.status == 409
    with pytest.raises(editor.EditorError):
        editor.discovery_record(body)
    env.write_text("")
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "ambient-wrong-profile-key")
    assert not any(p["provider_id"] == "opencode-go" for p in editor.read_editor()["providers"])
    with pytest.raises(editor.EditorError):
        editor.discovery_record(builtin_body())


def test_builtin_discovery_resolves_only_own_profile_key_and_preserves_owner(keyed_builtin):
    selected = editor.discovery_record(builtin_body())
    assert selected["base_url"] == "https://opencode.ai/zen/go/v1"
    assert selected["api_key"] == "fixture-profile-key"
    result = editor.write_editor(builtin_body())
    with pytest.raises(editor.EditorError):
        editor.write_editor(builtin_body())  # stale virtual ref cannot replace YAML owner
    assert result["saved_ref"] == "providers:opencode-go"


def test_referenced_builtin_cannot_be_deleted(keyed_builtin):
    path, _ = keyed_builtin
    data = raw(path)
    data["model"] = {"provider": "opencode-go", "default": "important-model"}
    path.write_text(yaml.safe_dump(data))
    before = path.read_bytes()
    with pytest.raises(editor.EditorError) as exc:
        editor.write_editor(builtin_body("delete"))
    assert exc.value.status == 409
    assert path.read_bytes() == before


def test_builtin_obeys_profile_relay_before_first_save(keyed_builtin):
    path, _ = keyed_builtin
    data = raw(path)
    data["model"] = {"provider": "opencode-go", "base_url": "https://profile-relay.invalid/v1",
                     "extra_headers": {"X-Relay-Secret": "fixture-relay-secret"}}
    path.write_text(yaml.safe_dump(data))
    view = editor.read_editor()
    assert "fixture-relay-secret" not in json.dumps(view)
    row = next(p for p in view["providers"] if p["provider_id"] == "opencode-go")
    assert row["base_url"] == "https://profile-relay.invalid/v1"
    selected = editor.discovery_record(builtin_body())
    assert selected["base_url"] == row["base_url"]
    assert selected["extra_headers"] == {"X-Relay-Secret": "fixture-relay-secret"}


def test_registry_templates_dedupe_aliases_and_use_profile_environment_only(monkeypatch):
    import sys
    import types
    provider = types.SimpleNamespace(auth_type="api_key", name="Google AI Studio",
        api_key_env_vars=("GOOGLE_API_KEY", "GEMINI_API_KEY"),
        inference_base_url="https://google.invalid/v1beta", base_url_env_var="GEMINI_BASE_URL")
    monkeypatch.setitem(sys.modules, "hermes_cli.auth", types.SimpleNamespace(
        PROVIDER_REGISTRY={"gemini": provider, "google": provider}))
    monkeypatch.setenv("GEMINI_BASE_URL", "https://ambient-wrong-profile.invalid")
    templates = editor._builtin_templates({"GEMINI_API_KEY": "fixture-key"})
    assert list(templates) == ["gemini"]
    assert templates["gemini"]["key_env"] == "GEMINI_API_KEY"
    assert templates["gemini"]["base_url"] == "https://google.invalid/v1beta"


def test_profile_default_survives_first_builtin_catalog_save(keyed_builtin):
    path, _ = keyed_builtin
    data = raw(path)
    data["model"] = {"provider": "opencode-go", "default": "important-default"}
    path.write_text(yaml.safe_dump(data))
    row = next(p for p in editor.read_editor()["providers"] if p["provider_id"] == "opencode-go")
    assert [m["id"] for m in row["models"]] == ["important-default"]
    before = path.read_bytes()
    with pytest.raises(editor.EditorError) as exc:
        editor.write_editor(builtin_body(changes={"models": [{"id": "new-model"}]}))
    assert exc.value.status == 409
    assert path.read_bytes() == before


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("replacement", ["replacement-model", ""])
def test_replace_models_and_provider_default_in_one_save(store, legacy, replacement):
    path, data, _ = store
    entry = data["providers"]["custom:relay"]
    entry["model" if legacy else "default_model"] = "vendor/model:free"
    if legacy:
        data["custom_providers"] = [{**data["providers"].pop("custom:relay"), "name": "relay"}]
    path.write_text(yaml.safe_dump(data))
    editor.write_editor(operation(changes={"default_model": replacement, "models": [{"id": "replacement-model"}]}))
    saved = raw(path)
    record = saved["custom_providers"][0] if legacy else saved["providers"]["custom:relay"]
    assert list(record["models"]) == ["replacement-model"]
    assert (record.get("model") if legacy else record.get("default_model")) == (replacement or None)
    assert saved["model"] == data["model"]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFO")
def test_non_regular_config_is_refused_without_blocking(store, tmp_path, monkeypatch):
    pipe = tmp_path / "config.fifo"
    os.mkfifo(pipe)
    monkeypatch.setattr(config, "_get_config_path", lambda: pipe)
    with pytest.raises(editor.EditorError):
        editor.read_editor()
