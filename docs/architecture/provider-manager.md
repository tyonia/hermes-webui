# Profile provider/model editor

The custom build's Settings → Providers editor is implemented in
`api/provider_manager.py`, `api/provider_manager_probe.py`,
`static/provider_manager.js`, and `static/provider_manager.css`. The existing
routes, settings loader, page shell and service worker have small integration
hooks. There is no React/Next.js dependency or separate provider database.

## Authoritative state and writes

The request's profile and `_get_config_path()` select the owning YAML file,
including an explicit `HERMES_CONFIG_PATH` override. The browser is given the
profile, save destination and an opaque revision bound to the selected path,
resolved target and file contents. Both owner and revision must still match on
every mutation or discovery request. The client never supplies a file path.

`GET /api/provider-manager` strictly reads a bounded regular YAML mapping and
returns a redacted projection. Invalid YAML and dangling links refuse editing.
Disabled records remain visible so they can be enabled again. Unknown fields
and credential values are not included in the projection.

`POST /api/provider-manager` accepts `create`, `save`, `delete`, or `default`.
Edits patch only named fields of the original `providers` mapping record or
`custom_providers` list record. The normalized catalog view must never replace
the original config. Native keys and legacy names retain their identity when
the display label changes. New custom providers use native mappings unless a
legacy store is present; that store is used to avoid the documented
empty-legacy-store catalog suppression rule. No automatic migration occurs.

Model IDs remain exact (including slashes, colons and suffixes). A renamed
model carries its previous metadata using its original ID. Existing metadata
not edited by the form is preserved. List/serialized-list allowlists remain
list shaped; mapping metadata remains mapping shaped. The user explicitly
chooses fixed catalog vs discovery mode through `discover_models`.

API keys and request-header values use explicit keep/replace/remove operations.
Choosing a credential environment variable replaces the record's other
credential declarations; its value is never returned. Built-in credential
cards continue to use the existing profile `.env` and authentication APIs.
The editor does not modify OAuth stores or expose existing keys. References in
the default model, auxiliary and fallback config block provider/model deletion
or provider disable until the reference is changed. Historical chat records
are not migrated or deleted.

Read/patch/write uses the existing `_cfg_lock` and shared YAML writer. Config
formatting/comments may be normalized by that writer; unrelated values and
`${ENV_VAR}` templates are preserved. The revision is rechecked immediately
before write; native symlink follow-through uses the shared writer's checks.
WebUI writers are serialized in-process. External CLI/manual writers do not
share this lock: detected external edits reject the draft, but this is not a
cross-process compare-and-swap protocol. Avoid simultaneous CLI/manual saves.

Default-model routing and persistence delegate to
`set_hermes_default_model`, with an ownership/revision guard under its own
config lock. After persistence, config reload and provider/model/live catalog
invalidation happen outside the write lock. HTTP responses and upstream
requests also stay outside it. Existing picker identity, send precedence and
runtime agent ownership remain unchanged.

## Explicit discovery

`POST /api/provider-manager/discover` accepts only a saved record's owner,
revision and reference. It validates that this exact record owns the runtime
identity before resolving that record's URL, key and headers within the
request profile. It never uses another provider's credentials. The probe
does not write config, start an agent session, or send a completion.

The model list request uses a 10-second timeout, a 2-MiB response limit and
at most 2000 unique model IDs. It supports OpenAI-compatible and Anthropic
model-list endpoints, local/keyless endpoints, and static/environment-referenced
credentials. It refuses redirects and ignores ambient HTTP proxies, including
for local model servers. Errors do not echo upstream bodies or credential
values. Command-based keys and credential pools remain preserved but are not
executed by discovery; use Hermes CLI discovery for those configurations.

The form requires saved provider settings before discovery. Search/select/import
changes only the draft; Save commits it. Results from a superseded selection
are ignored. Navigation is locked during an in-flight mutation, and drafts
survive refused saves. A background refresh restores the selected model by exact
ID within its provider; credential refresh keeps the selected managed provider
and updates its status. No drafts or secrets are stored in browser persistence.

## Verification and remaining scope

`./scripts/test.sh -q tests/test_provider_manager.py` covers disposable config
round trips, strict reads, owner/revision rejection, preserved fields, credential
redaction, model identity, allowlists, reference protection, symlinks, default
delegation and an actual local HTTP discovery request with synthetic credentials.
These do not certify a commercial provider's API or the production profile.

Desktop/narrow/mobile acceptance should cover create/edit/save/reload, importing
discovered models, pending changes, failed saves and keyboard navigation. The
new UI uses the existing theme tokens. Real OAuth login, pi-specific `compat`,
pricing, image limits and per-model protocol overrides are not transplanted;
unsupported Hermes fields remain on disk. Model completion tests and CLI-side
coordination are separate future work.
