"""Explicit model discovery from one saved provider; no redirects or config writes."""
import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit

from api import config
from api.provider_manager import EditorError, discovery_record

MAX_RESPONSE = 2 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise EditorError("The model endpoint redirected. Configure its final URL before retrying.", 502)


def _expand(value):
    def replace(match):
        resolved = config._thread_local_env_value(match.group(1))
        if not resolved:
            raise EditorError("A credential or endpoint environment reference is unresolved.")
        return resolved
    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, str(value or ""))


def discover(body):
    entry = discovery_record(body)
    if entry.get("oauth_provider"):
        from api.provider_catalog_policy import chat_models
        from hermes_cli.models import provider_model_ids
        try:
            rows = chat_models(provider_model_ids(entry["oauth_provider"]) or [], entry["oauth_provider"])[:2000]
        except Exception:
            raise EditorError("Could not read the OAuth model catalog. Check Hermes authentication.", 502) from None
        # OAuth refreshes must not publish results after an owner/config change.
        discovery_record(body)
        return {"ok": True, "models": rows, "count": len(rows)}
    base = _expand(entry.get("base_url"))
    try:
        url = urlsplit(base)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError
    except ValueError:
        raise EditorError("Discovery needs an HTTP(S) Base URL without embedded credentials or query parameters.") from None
    if entry.get("key_cmd") or entry.get("credential_pool"):
        raise EditorError("Discovery for command-based credentials or credential pools is not supported in this editor yet. Use Hermes CLI discovery.")
    mode = config._custom_record_api_mode(entry) or "chat_completions"
    if mode not in {"chat_completions", "codex_responses", "anthropic_messages"}:
        raise EditorError("This transport does not expose a supported model-list endpoint.")
    path = url.path.rstrip("/")
    if not path.endswith("/models"):
        if mode == "anthropic_messages" and not re.search(r"/v\d+(?:beta)?$", path):
            path += "/v1"
        path += "/models"
    endpoint = urlunsplit((url.scheme, url.netloc, path, "", ""))
    raw_headers = entry.get("extra_headers", {})
    if not isinstance(raw_headers, dict):
        raise EditorError("Configured request headers are malformed.")
    headers = {str(k): _expand(v) for k, v in raw_headers.items()}
    if any(not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", k) or "\r" in v or "\n" in v for k, v in headers.items()):
        raise EditorError("Configured request headers are invalid.")
    key = _expand(entry.get("api_key"))
    if not key and entry.get("key_env"):
        key = config._thread_local_env_value(str(entry["key_env"]))
        if not key:
            raise EditorError("The configured credential environment variable is empty.")
    names = {k.lower() for k in headers}
    if key and key != "no-key-required":
        name = "x-api-key" if mode == "anthropic_messages" else "Authorization"
        if name.lower() not in names:
            headers[name] = key if mode == "anthropic_messages" else "Bearer " + key
    if mode == "anthropic_messages" and "anthropic-version" not in names:
        headers["anthropic-version"] = "2023-06-01"
    if "accept" not in names:
        headers["Accept"] = "application/json"
    # Some catalogs (including OpenCode Go) reject Python-urllib's default
    # User-Agent. Identify this client while honoring saved headers, with
    # case-insensitive names just like the authentication headers above.
    if "user-agent" not in names:
        headers["User-Agent"] = "HermesWebUI/1.0"
    # These are deliberate requests to the saved endpoint, including local/LAN
    # model servers. Never route them through an ambient HTTP proxy or forward
    # credentials to a redirect destination.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        request = urllib.request.Request(endpoint, headers=headers)
        with opener.open(request, timeout=10) as response:
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise EditorError("Upstream model list exceeds 2 MiB.", 502)
        payload = json.loads(raw)
    except EditorError:
        raise
    except urllib.error.HTTPError as exc:
        raise EditorError(f"Model discovery returned HTTP {exc.code}. Check endpoint and credentials.", 502) from None
    except Exception:
        # Neither upstream response bodies nor exception URLs are safe to echo:
        # they may contain credentials from headers or error reflection.
        raise EditorError("Could not read the upstream model list. Check endpoint, protocol, TLS and connectivity.", 502) from None
    items = payload if isinstance(payload, list) else payload.get("data", payload.get("models", [])) if isinstance(payload, dict) else []
    if not isinstance(items, list):
        raise EditorError("Upstream returned an unsupported model-list shape.", 502)
    found = {}
    for item in items:
        mid = item.get("id") or item.get("model") or item.get("name") if isinstance(item, dict) else item
        if not isinstance(mid, str) or not mid.strip() or len(mid) > 512:
            continue
        row = {"id": mid.strip()}
        if isinstance(item, dict):
            for key_name in ("context_length", "context_window", "contextWindow"):
                length = item.get(key_name)
                if type(length) is int and 0 < length <= 100_000_000:
                    row["context_length"] = length
                    break
        found[row["id"]] = row
        if len(found) >= 2000:
            break
    from api.provider_catalog_policy import chat_models
    models = chat_models(list(found.values()))
    return {"ok": True, "models": sorted(models, key=lambda row: row["id"].lower()), "count": len(models)}
