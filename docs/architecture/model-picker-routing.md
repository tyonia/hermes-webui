# Model Picker Routing

## Profile-owned chooser membership

`api/provider_catalog_policy.py` applies the provider editor's enabled runtime
owners to both `/api/models` paths (ordinary and session-visit) after every
inventory-cache read, and to `/api/models/live` before a probe and before response
publication. Removed, disabled, shadowed, ambiguous and unmanaged providers do
not enter chooser responses. Profile-default and locally logged-in OAuth providers participate
in the editor and chooser through their registered Hermes identity.

Fixed catalogs return exactly their saved IDs, including an authoritative empty
list, without live requests. Follow catalogs retain saved imports and may add vendor inventory, but pass
the same provider membership and non-chat generation filters. Alias/badge
metadata cannot reintroduce excluded models. The existing inventory builders
remain available to runtime callers; chooser visibility is not credential
revocation and does not rewrite historical session routes.

The browser shares `static/provider_catalog.js` between the composer and the
editor's saved-chat-catalog preview. Main and live data carry profile and opaque
catalog revision. Its live cache is owned by that scope; profile switches,
provider edits and revision changes revoke old data. Each pending live request
also owns its pending slot, so an old completion cannot clear its successor.
Rendering and live merging check membership as well, including temporary
selection-restoration options. Explicit custom-ID entry and existing send-route
authority retain their own contracts.

Current identity, restoration, and selection rules for the model picker.

## Provider-scoped identity

A model option is identified by both its provider and its model ID. The same model ID under two providers must remain two selectable options, and only the option for the selected provider can appear active or carry the Selected badge.

## OpenRouter route identity

OpenRouter route identity is case-insensitive and may include the WebUI's optional `@openrouter:` wrapper. All other route text must match exactly.

Do not replace dashes with dots, split a vendor path, or remove a suffix such as `:free` or `:thinking`. OpenRouter can expose distinct upstream IDs that differ only by dash or dot spelling, and the picker must preserve both routes.

Other providers keep their existing identity normalization for custom-provider compatibility.

## Overflow restoration

When a saved OpenRouter model is present in a provider group's overflow catalog, the picker promotes the exact matching entry into that group and removes the same entry from the hidden overflow list. Later restorations must reuse the promoted option.

Promotion must preserve the catalog model ID, label, provider, and suffix. It must not consume a different entry that normalizes to the same route.

## Synthesized fallback

The picker may synthesize a provider-qualified option only when the exact OpenRouter route is absent from both the visible group and its overflow catalog. Repeated restoration of that missing route must reuse the synthesized option.

The fallback remains routable. It does not add or replace a catalog option with a different route.

## Selection and live-catalog updates

Option deduplication, live-catalog merging, and selected-row rendering use the same provider-aware route identity. A later catalog update must not collapse or select a punctuation-distinct OpenRouter sibling.
