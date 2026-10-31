"""Profile-owned provider editor; projections are never written back as config.

Only explicit field patches touch the original record. Credential values and
unknown fields stay on the server. No pi SDK or secondary configuration store.
"""
from __future__ import annotations

import copy
import hashlib
import os
import re
import stat
from urllib.parse import urlsplit

from api import config, profiles, yaml_compat as yaml

MAX_CONFIG_BYTES = 4 * 1024 * 1024
MAX_MODELS = 2000
MODES = {"", "chat_completions", "codex_responses", "anthropic_messages", "bedrock_converse"}


class EditorError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _profile():
    name = str(profiles.get_active_profile_name() or "default")
    if name != "default" and not profiles._PROFILE_ID_RE.fullmatch(name):
        raise EditorError("Invalid profile owner.", 403)
    if name != "default" and not profiles.get_active_hermes_home().is_dir():
        raise EditorError("Profile no longer exists. Select an existing profile.", 409)
    return name


def _snapshot(path, profile):
    """Strict, bounded regular-file read: a parse error can never become {}."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            st = os.fstat(stream.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_CONFIG_BYTES:
                raise EditorError("Configuration must be a regular file under 4 MiB.", 422)
            raw = stream.read(MAX_CONFIG_BYTES + 1)
            if len(raw) > MAX_CONFIG_BYTES:
                raise EditorError("Configuration is too large.", 422)
    except FileNotFoundError:
        if path.is_symlink():
            raise EditorError("Configuration symlink is dangling; repair it before editing.", 422) from None
        raw = b""
    except OSError:
        raise EditorError("Configuration cannot be read; repair file permissions before editing.", 422) from None
    try:
        data = yaml.safe_load(raw.decode("utf-8-sig")) if raw.strip() else {}
    except Exception:
        raise EditorError("Configuration YAML cannot be parsed; repair it before editing.", 422) from None
    if not isinstance(data, dict):
        raise EditorError("Configuration must contain a YAML mapping.", 422)
    identity = f"{profile}\0{path.absolute()}\0{path.resolve()}\0".encode()
    revision = hashlib.sha256(identity + raw).hexdigest()
    return data, revision


def _records(data):
    native = data.get("providers", {})
    if isinstance(native, dict):
        for key, entry in native.items():
            if isinstance(key, str) and isinstance(entry, dict):
                yield "providers:" + key, "providers", key, entry
    legacy = data.get("custom_providers", [])
    if isinstance(legacy, list):
        for index, entry in enumerate(legacy):
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                yield f"custom_providers:{index}", "custom_providers", index, entry


def _provider_id(source, key, entry):
    if source == "custom_providers":
        return config._custom_provider_slug_from_name(entry.get("name"))
    if config._is_native_custom_provider_key(key):
        return config._custom_provider_slug_from_name(key)
    return str(key)


def _text(value, label, limit=2048):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise EditorError(f"Invalid {label}.")
    return value.strip()


def _public_url(raw):
    text = str(raw or "")
    try:
        parts = urlsplit(text)
        # Some older configs carry secrets in URLs; retain those on disk but
        # never ship them to the browser as part of the editable projection.
        if parts.username or parts.password or parts.query or parts.fragment:
            return "", True
    except ValueError:
        return "", True
    return text, False


def _models(entry):
    raw = entry.get("models", {})
    if isinstance(raw, str):
        from api.routes import _parse_config_string_list
        raw = _parse_config_string_list(raw)
    if isinstance(raw, dict):
        pairs = list(raw.items())
    elif isinstance(raw, list):
        pairs = [(item.get("id") or item.get("model") or item.get("name"), item)
                 if isinstance(item, dict) else (item, {}) for item in raw]
    else:
        pairs = []
    result = []
    seen = set()
    for mid, meta in pairs:
        if not isinstance(mid, str) or not mid.strip() or mid.startswith("__") or mid in seen:
            continue
        seen.add(mid)
        meta = meta if isinstance(meta, dict) else {}
        result.append({"id": mid, "original_id": mid, "label": str(meta.get("label") or ""),
                       "context_length": meta.get("context_length") if type(meta.get("context_length")) is int else None})
    return result


def _view(data, revision, path, profile):
    entries = []
    for ref, source, key, entry in _records(data):
        pid = _provider_id(source, key, entry)
        if not pid:
            continue
        url = config._normalized_raw_provider_record(entry, str(key)).get("base_url") if source == "providers" else entry.get("base_url")
        base_url, redacted = _public_url(url)
        key_ref = str(entry.get("key_env") or entry.get("api_key_env") or "")
        raw_key = entry.get("api_key")
        if isinstance(raw_key, str) and re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", raw_key):
            key_ref = raw_key[2:-1]
        headers = entry.get("extra_headers")
        entries.append({
            "ref": ref, "source": source, "provider_id": pid,
            "name": str(entry.get("display_name") or entry.get("name") or key),
            "base_url": base_url, "base_url_redacted": redacted,
            "api_mode": config._custom_record_api_mode(entry) or "",
            "enabled": config._raw_provider_record_enabled(entry),
            "discover_models": entry.get("discover_models") is not False,
            "default_model": str(entry.get("default_model") or entry.get("model") or ""),
            "credential_configured": bool(raw_key or key_ref or entry.get("key_cmd") or entry.get("credential_pool")),
            "key_env": key_ref,
            "headers": [{"name": str(name), "configured": True} for name in headers] if isinstance(headers, dict) else [],
            "models": _models(entry),
        })
    main = data.get("model")
    warnings = []
    if "custom_providers" in data and not data.get("custom_providers"):
        warnings.append("The legacy custom_providers store suppresses native custom catalogs. New providers will use the legacy store.")
    return {"profile": profile, "config_path": str(path), "revision": revision,
            "providers": entries, "default": {"provider": main.get("provider", ""), "model": main.get("default", "")} if isinstance(main, dict) else {},
            "warnings": warnings}


def read_editor():
    with config._cfg_lock:
        path = config._get_config_path()
        profile = _profile()
        data, revision = _snapshot(path, profile)
        return _view(data, revision, path, profile)


def _find(data, ref):
    for row in _records(data):
        if row[0] == ref:
            return row
    raise EditorError("Provider no longer exists. Reload the editor.", 409)


def _uses(data, pid, mid=None):
    """Configuration references outside the edited provider; never rewrite them."""
    def walk(value, path):
        if isinstance(value, dict):
            provider = value.get("provider") or value.get("model_provider")
            model = value.get("default") or value.get("model")
            if isinstance(provider, str) and (provider == pid or config._custom_provider_slug_from_name(provider) == pid):
                if mid is None or model == mid:
                    yield path or "config"
            for key, child in value.items():
                if not path and key in {"providers", "custom_providers"}:
                    continue
                yield from walk(child, f"{path}.{key}".strip("."))
        elif isinstance(value, list):
            for i, child in enumerate(value):
                yield from walk(child, f"{path}[{i}]")
        elif mid is None and value == pid:
            yield path
    return list(walk(data, ""))


def _patch_models(entry, rows, data, pid):
    if not isinstance(rows, list) or len(rows) > MAX_MODELS:
        raise EditorError("Expected at most 2000 model rows.")
    old_raw = entry.get("models", {})
    if isinstance(old_raw, str):
        from api.routes import _parse_config_string_list
        old_raw = _parse_config_string_list(old_raw)
    old = old_raw if isinstance(old_raw, dict) else {}
    if isinstance(old_raw, list):
        old = {}
        for item in old_raw:
            if isinstance(item, dict):
                mid = item.get("id") or item.get("model") or item.get("name")
                if isinstance(mid, str):
                    old[mid] = {k: v for k, v in item.items() if k not in {"id", "model", "name"}}
            elif isinstance(item, str):
                old[item] = {}
    models = {}
    for row in rows:
        if not isinstance(row, dict):
            raise EditorError("Invalid model row.")
        mid = _text(row.get("id"), "model ID", 512)
        if not mid or mid.startswith("__") or mid in models:
            raise EditorError("Model IDs must be non-empty and unique within this provider.")
        original_id = row.get("original_id") or mid
        if not isinstance(original_id, str):
            raise EditorError("Invalid original model ID.")
        metadata = copy.deepcopy(old.get(original_id, {}))
        if not isinstance(metadata, dict):
            raise EditorError("This model has unsupported metadata; edit it in config.yaml.", 422)
        if "label" in row:
            label = _text(row["label"], "model label", 256)
            if label:
                metadata["label"] = label
            else:
                metadata.pop("label", None)
        if "context_length" in row:
            length = row["context_length"]
            if length is None or length == "":
                metadata.pop("context_length", None)
            elif type(length) is not int or not 1 <= length <= 100_000_000:
                raise EditorError("Context length must be a positive integer.")
            else:
                metadata["context_length"] = length
        models[mid] = metadata
    removed = {row["id"] for row in _models(entry)} - models.keys()
    for mid in removed:
        if mid in {entry.get("default_model"), entry.get("model")} or _uses(data, pid, mid):
            raise EditorError("This model is a configured default or auxiliary model. Change its reference before deleting it.", 409)
    # Keep list-shaped allowlists as lists; a mapping has different discovery
    # semantics in Hermes. Metadata is carried in object rows where needed.
    if isinstance(old_raw, (list, str)):
        entry["models"] = [{"id": mid, **meta} if meta else mid for mid, meta in models.items()]
    else:
        entry["models"] = {**{k: v for k, v in old.items() if str(k).startswith("__")}, **models}


def _patch(entry, changes, source, data, pid):
    allowed = {"name", "base_url", "api_mode", "enabled", "discover_models", "default_model", "models", "key_env"}
    if not isinstance(changes, dict) or set(changes) - allowed:
        raise EditorError("Unsupported provider field.")
    for field, value in changes.items():
        if field == "models":
            continue
        if field in {"enabled", "discover_models"}:
            if type(value) is not bool:
                raise EditorError(f"{field} must be a boolean.")
        else:
            value = _text(value, field)
        target = field
        if field == "name":
            target = "display_name" if source == "custom_providers" else "name"
        elif field == "base_url":
            if value:
                # URL templates are preserved; only concrete URLs are parsed.
                if "${" not in value:
                    try:
                        url = urlsplit(value)
                        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
                            raise ValueError
                    except ValueError:
                        raise EditorError("Base URL must be HTTP(S), without embedded credentials, query, or fragment.") from None
            for alias in ("api", "url"):
                entry.pop(alias, None)
        elif field == "api_mode":
            if value not in MODES:
                raise EditorError("Unsupported transport; preserve it or edit config.yaml directly.")
            entry.pop("transport", None)
        elif field == "default_model" and source == "custom_providers":
            target = "model"
        elif field == "key_env":
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
                raise EditorError("Invalid credential environment variable name.")
            entry.pop("api_key_env", None)
            for old_source in ("api_key", "key_cmd", "credential_pool"):
                entry.pop(old_source, None)
        if value == "":
            entry.pop(target, None)
        else:
            entry[target] = value
    if "models" in changes:
        _patch_models(entry, changes["models"], data, pid)


def _secrets(entry, body):
    credential = body.get("credential", {"action": "keep"})
    if not isinstance(credential, dict) or credential.get("action") not in {"keep", "set", "remove"}:
        raise EditorError("Invalid credential operation.")
    action = credential["action"]
    if action == "set":
        value = _text(credential.get("value"), "API key", 16384)
        if not value:
            raise EditorError("Enter a replacement API key.")
        entry["api_key"] = value
        # Make replacement explicit rather than retaining a higher-priority source.
        for key in ("key_env", "api_key_env", "key_cmd", "credential_pool"):
            entry.pop(key, None)
    elif action == "remove":
        for key in ("api_key", "key_env", "api_key_env", "key_cmd", "credential_pool"):
            entry.pop(key, None)
    header_changes = body.get("headers", [])
    if not isinstance(header_changes, list) or len(header_changes) > 100:
        raise EditorError("Invalid header operations.")
    if header_changes:
        headers = copy.deepcopy(entry.get("extra_headers") or {})
        if not isinstance(headers, dict):
            raise EditorError("Existing headers are malformed; repair config.yaml first.", 422)
        for header in header_changes:
            if not isinstance(header, dict):
                raise EditorError("Invalid header operation.")
            name = _text(header.get("name"), "header name", 128)
            if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
                raise EditorError("Invalid header name.")
            if header.get("action") == "remove":
                headers.pop(name, None)
            elif header.get("action") == "set":
                headers[name] = _text(header.get("value"), "header value", 16384)
            elif header.get("action") != "keep":
                raise EditorError("Invalid header operation.")
        if headers:
            entry["extra_headers"] = headers
        else:
            entry.pop("extra_headers", None)


def write_editor(body):
    if not isinstance(body, dict):
        raise EditorError("Expected an editor operation.")
    if body.get("op") == "default":
        return _set_default(body)
    with config._cfg_lock:
        path = config._get_config_path()
        profile = _profile()
        data, revision = _snapshot(path, profile)
        if body.get("profile") != profile or body.get("revision") != revision:
            raise EditorError("Profile or configuration changed. Reload the editor; your draft has not been saved.", 409)
        op = body.get("op")
        if op == "create":
            slug = _text(body.get("id"), "provider ID", 100)
            if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", slug):
                raise EditorError("Provider ID must use lowercase letters, digits, dots, underscores, or dashes.")
            pid = "custom:" + slug
            if any(_provider_id(source, key, row) == pid for _, source, key, row in _records(data)):
                raise EditorError("A provider with that identity already exists.", 409)
            entry = {"base_url": "", "discover_models": False, "models": {}}
            if "custom_providers" in data:
                if not isinstance(data["custom_providers"], list):
                    raise EditorError("Legacy provider store is malformed; repair it before adding providers.", 422)
                source = "custom_providers"
                key = len(data[source])
                entry["name"] = slug
                data[source].append(entry)
            else:
                if not isinstance(data.get("providers", {}), dict):
                    raise EditorError("Native provider store is malformed.", 422)
                source, key = "providers", pid
                data.setdefault(source, {})[key] = entry
            ref = f"{source}:{key}"
        else:
            ref = body.get("ref")
            _, source, key, original = _find(data, ref)
            pid = _provider_id(source, key, original)
            entry = copy.deepcopy(original)
            data[source][key] = entry
        if op in {"create", "save"}:
            _patch(entry, body.get("changes", {}), source, data, pid)
            _secrets(entry, body)
            if entry.get("enabled") is False and _uses(data, pid):
                raise EditorError("Change configured references before disabling this provider.", 409)
            if op == "create" and not entry.get("base_url"):
                raise EditorError("A new custom provider needs a Base URL.")
        elif op == "delete":
            if _uses(data, pid):
                raise EditorError("This provider is referenced by model, auxiliary, or fallback configuration. Change those references first.", 409)
            if source == "providers":
                del data[source][key]
            else:
                del data[source][key]
        else:
            raise EditorError("Unknown editor operation.")
        # Optimistic validation under the same lock used by WebUI's other YAML
        # writers. Recheck source identity immediately before the shared writer.
        if _snapshot(path, profile)[1] != revision:
            raise EditorError("Configuration changed while saving. Reload and retry.", 409)
        config._save_yaml_config_file(path, data)
    # Never reload, probe, or emit HTTP responses under the registry/config lock.
    config.reload_config()
    config.invalidate_models_cache()
    from api.providers import invalidate_providers_cache
    invalidate_providers_cache()
    from api.routes import _clear_live_models_cache
    _clear_live_models_cache()
    result = read_editor()
    result.update(ok=True, saved_ref=ref)
    return result


def _set_default(body):
    """Delegate routing/default persistence to the existing canonical writer."""
    model = _text(body.get("model"), "model ID", 512)
    if not model:
        raise EditorError("Select a model first.")
    with config._cfg_lock:
        path = config._get_config_path()
        profile = _profile()
        data, revision = _snapshot(path, profile)
        if body.get("profile") != profile or body.get("revision") != revision:
            raise EditorError("Profile or configuration changed. Reload the editor.", 409)
        ref = body.get("ref")
        if isinstance(ref, str) and ref.startswith("managed:"):
            pid = ref[len("managed:"):]
            if pid.startswith("custom") or not config._is_known_model_provider(pid):
                raise EditorError("Unknown managed provider.")
        else:
            _, source, key, entry = _find(data, ref)
            pid = _provider_id(source, key, entry)
            if not config._raw_provider_record_enabled(entry):
                raise EditorError("Enable this provider before setting a default.", 409)

    def guard(selected_path):
        if selected_path != path or _profile() != profile or _snapshot(selected_path, profile)[1] != revision:
            raise EditorError("Profile or configuration changed. Reload the editor.", 409)

    config.set_hermes_default_model(model, provider=pid, config_guard=guard)
    result = read_editor()
    result.update(ok=True, saved_ref=body.get("ref"))
    return result


def discovery_record(body):
    """Capture just one saved owner before doing network I/O outside the lock."""
    if not isinstance(body, dict):
        raise EditorError("Expected a discovery operation.")
    with config._cfg_lock:
        path = config._get_config_path()
        profile = _profile()
        data, revision = _snapshot(path, profile)
        if body.get("profile") != profile or body.get("revision") != revision:
            raise EditorError("Profile or configuration changed. Reload before fetching models.", 409)
        _, source, key, entry = _find(data, body.get("ref"))
        if not config._raw_provider_record_enabled(entry):
            raise EditorError("Enable this provider before fetching models.", 409)
        # Validate the same identity the runtime would use. A shadowed native
        # row must never probe with a legacy row's credential (or vice versa).
        pid = _provider_id(source, key, entry)
        selected = config._normalized_raw_provider_record(entry, str(key)) if source == "providers" else entry
        if pid.startswith("custom:"):
            try:
                owned, owned_source, _, _ = config._select_custom_provider_record(pid, config._custom_provider_slug_key(pid), data)
            except ValueError:
                raise EditorError("Provider identity is ambiguous. Repair conflicting entries before discovery.", 409) from None
            if owned is None or owned_source != source or owned != selected:
                raise EditorError("This record is disabled, ambiguous, or shadowed by another provider store.", 409)
        return copy.deepcopy(selected)
