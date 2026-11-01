"""The profile editor owns chooser membership; inventory caches are not authority.

Apply this after every main-cache read and before/after every live probe. This
keeps the existing inventory rebuild/cache machinery and routing identities.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

from api import config, provider_manager as editor


def chat_models(rows, provider=""):
    """Keep exact IDs, excluding generation-only rows using Agent's classifier."""
    try:
        from hermes_cli.chat_catalog import catalog_item_is_generation, is_known_non_chat_model
    except ImportError:
        catalog_item_is_generation = lambda row: False
        is_known_non_chat_model = lambda mid: bool(re.search(r"(?:^|[/_.-])(?:image|video)(?:$|[/_.-])", mid, re.I))
    found = {}
    for raw in rows or []:
        row = {"id": raw, "label": raw} if isinstance(raw, str) else copy.deepcopy(raw)
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        mid = bare_id(row["id"], provider)
        non_chat = bool(re.search(r"(?:^|[/_.-])(?:tts|whisper|embeddings?)(?:$|[/_.-])", mid, re.I))
        if not mid or (provider and mid.startswith("@")) or non_chat or catalog_item_is_generation({**row, "id": mid}) or is_known_non_chat_model(mid):
            continue
        identity = mid.lower() if editor._identity(provider) == "openrouter" else mid
        row["id"] = mid
        row["label"] = row.get("label") or mid
        found.setdefault(identity, row)
    return list(found.values())


def bare_id(mid, provider):
    aliases = [str(provider), editor._identity(provider)]
    aliases += [alias for alias, target in config._PROVIDER_ALIASES.items() if editor._identity(target) == editor._identity(provider)]
    for alias in aliases:
        prefix = "@" + alias + ":"
        if isinstance(mid, str) and mid[:len(prefix)].lower() == prefix.lower():
            return mid[len(prefix):]
    return mid


class CatalogScope:
    def __init__(self, view):
        self.view = view
        self.profile = view["profile"]
        self.editor_revision = view["revision"]
        self.sources = copy.deepcopy(config._models_cache_source_fingerprint())
        self.revision = hashlib.sha256(json.dumps([self.editor_revision, self.sources], sort_keys=True).encode()).hexdigest()
        self.rows = {editor._identity(row["provider_id"]): row for row in view["providers"] if row["enabled"] and row.get("runtime_visible", True)}

    @classmethod
    def capture(cls):
        return cls(editor.read_editor())

    def assert_current(self):
        current = editor.read_editor()
        if (current["profile"], current["config_path"], current["revision"]) != (
            self.profile, self.view["config_path"], self.editor_revision
        ) or config._models_cache_source_fingerprint() != self.sources:
            raise editor.EditorError("The profile catalog changed. Reload the model list.", 409)

    def rule(self, provider):
        return self.rows.get(editor._identity(provider))

    def metadata(self):
        return {"profile": self.profile, "revision": self.revision, "editor_revision": self.editor_revision,
                "providers": [{"id": pid, "discover_models": row["discover_models"],
                               "aliases": sorted({row["provider_id"], *[alias for alias, target in config._PROVIDER_ALIASES.items() if editor._identity(target) == pid]}),
                               "models": [m["id"] for m in chat_models(row["models"], row["provider_id"])]}
                              for pid, row in self.rows.items()]}

    def models(self, provider, incoming):
        row = self.rule(provider)
        if row is None:
            return []
        candidates = incoming if row["discover_models"] else row["models"]
        return chat_models(candidates, provider)

    def accepts(self, provider, mid):
        row = self.rule(provider)
        if row is None or not chat_models([mid], provider):
            return False
        key = lambda value: bare_id(value, provider).lower() if editor._identity(provider) == "openrouter" else bare_id(value, provider)
        return row["discover_models"] or key(mid) in {key(model["id"]) for model in row["models"]}

    def live(self, provider, payload):
        result = dict(payload)
        result.update(provider=provider, models=self.models(provider, payload.get("models", [])),
                      profile=self.profile, catalog_revision=self.revision, editor_revision=self.editor_revision)
        result["count"] = len(result["models"])
        return result

    def main(self, payload):
        result = copy.deepcopy(payload)
        existing = {editor._identity(group.get("provider_id", "")): group for group in result.get("groups", [])}
        groups = []
        for pid in dict.fromkeys([*existing, *self.rows]):
            row = self.rows.get(pid)
            if row is None:
                continue
            old = existing.get(pid, {})
            group = {**old, "provider": row["name"], "provider_id": row["provider_id"]}
            if row["discover_models"]:
                incoming = list(old.get("models", []))
                advertised = {bare_id(model["id"], row["provider_id"]) for model in incoming + old.get("extra_models", [])}
                # A partial or old inventory must not hide explicit imports.
                # Follow mode adds advertised models to the saved catalog.
                incoming += [model for model in row["models"] if model["id"] not in advertised]
                models = chat_models(incoming, row["provider_id"])
                extra = chat_models(old.get("extra_models", []), row["provider_id"])
                if pid == editor._identity(result.get("active_provider")) and self.accepts(row["provider_id"], result.get("default_model", "")):
                    models = chat_models(models + [{"id": result["default_model"]}], row["provider_id"])
            else:
                old_rows = {bare_id(m["id"], row["provider_id"]): m for m in old.get("models", []) + old.get("extra_models", [])}
                labels = {model["id"]: model.get("label") for model in row["models"]}
                models = []
                for saved in chat_models(row["models"], row["provider_id"]):
                    prior = old_rows.get(saved["id"], {})
                    models.append({**prior, **saved, "label": labels.get(saved["id"]) or prior.get("label") or saved["id"]})
                extra = []
            group["models"] = config._apply_provider_prefix(models, row["provider_id"], result.get("active_provider"))
            group["extra_models"] = extra
            if models or extra:
                groups.append(group)
        config._deduplicate_model_ids(groups)
        result["groups"] = groups
        if not self.rule(result.get("active_provider")):
            result["active_provider"] = None
            result["default_model"] = ""
        else:
            rule = self.rule(result["active_provider"])
            default = result.get("default_model", "")
            if not self.accepts(rule["provider_id"], default):
                result["default_model"] = ""
        # Badges/aliases must not synthesize hidden providers back into the UI.
        for key in ("configured_model_badges", "aliases"):
            if isinstance(result.get(key), dict):
                result[key] = {mid: value for mid, value in result[key].items()
                               if (isinstance(value, dict) and self.accepts(value.get("provider") or result.get("active_provider"), value.get("model") or mid))
                               or (key == "aliases" and isinstance(value, str) and self.accepts(result.get("active_provider"), value))}
        result.update(profile=self.profile, catalog_revision=self.revision, editor_revision=self.editor_revision, catalog_scope=self.metadata())
        return result
