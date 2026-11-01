/* Profile-owned chooser policy shared by the composer and provider editor. */
(function () {
  'use strict';
  let scope = null, epoch = 0, groups = [];
  const cache = new Map();
  const owner = () => String((typeof S !== 'undefined' && S.activeProfile) || 'default');
  const bare = (id, provider) => String(id).slice(0, provider.length + 2).toLowerCase() === ('@' + provider + ':').toLowerCase() ? String(id).slice(provider.length + 2) : String(id);
  const generation = id => /(?:^|[/_.-])(?:image|video)(?:$|[/_.-])/i.test(id);
  function rule(provider) {
    return scope?.providers.find(row => row.id === provider || (row.aliases || []).includes(provider));
  }
  function allowed(provider, id) {
    const row = rule(provider);
    if (!row) return false;
    const mid = bare(bare(id, provider), row.id);
    if (!mid || mid.startsWith('@') || generation(mid) || /(?:^|[/_.-])(?:tts|whisper|embeddings?)(?:$|[/_.-])/i.test(mid)) return false;
    const identity = value => row.id === 'openrouter' ? value.toLowerCase() : value;
    return row.discover_models || row.models.some(model => identity(model) === identity(mid));
  }
  function snapshot() {
    return scope ? {profile: scope.profile, catalog_revision: scope.revision, editor_revision: scope.editor_revision, groups: JSON.parse(JSON.stringify(groups))} : null;
  }
  function publish() {
    if (typeof window.dispatchEvent === 'function' && typeof CustomEvent === 'function') {
      window.dispatchEvent(new CustomEvent('hermes-provider-catalog', {detail: snapshot()}));
    }
  }
  function capture(provider) {
    return scope && scope.profile === owner() && rule(provider) ? {profile: scope.profile, revision: scope.revision, epoch, provider} : null;
  }
  function current(token) {
    return !!token && !!scope && token.epoch === epoch && token.profile === owner() && token.profile === scope.profile && token.revision === scope.revision;
  }
  function acceptLive(token, data) {
    if (!current(token) || !data || !Array.isArray(data.models) || data.profile !== token.profile || data.catalog_revision !== token.revision || rule(data.provider)?.id !== rule(token.provider)?.id) return null;
    const models = JSON.parse(JSON.stringify((data.models || []).filter(model => allowed(token.provider, model.id))));
    cache.set(token.provider, models);
    const row = rule(token.provider);
    let group = groups.find(group => group.provider_id === row.id || (row.aliases || []).includes(group.provider_id));
    if (!group) { group = {provider_id: row.id, models: []}; groups.push(group); }
    const ids = new Set(group.models.map(model => bare(model.id, group.provider_id)));
    for (const model of models) if (!ids.has(bare(model.id, token.provider))) { group.models.push(model); ids.add(bare(model.id, token.provider)); }
    publish();
    return JSON.parse(JSON.stringify(models));
  }
  window.HermesProviderCatalog = {
    owner, allowed, capture, current,
    begin() { return {profile: owner(), epoch}; },
    acceptsLive(provider) { return !!rule(provider)?.discover_models; },
    accept(data, expectedProfile = owner(), request = null) {
      const next = data.catalog_scope;
      if (!next || expectedProfile !== owner() || next.profile !== expectedProfile || data.profile !== expectedProfile || next.revision !== data.catalog_revision ||
          typeof next.revision !== 'string' || !next.revision || !Array.isArray(next.providers) || !next.providers.every(row => typeof row.id === 'string' && typeof row.discover_models === 'boolean' && Array.isArray(row.models) && row.models.every(model => typeof model === 'string'))) return false;
      if (request && (request.profile !== expectedProfile || (request.epoch !== epoch && (scope?.profile !== next.profile || scope?.revision !== next.revision)))) return false;
      if (!scope || scope.profile !== next.profile || scope.revision !== next.revision) { epoch++; cache.clear(); }
      scope = JSON.parse(JSON.stringify(next));
      groups = JSON.parse(JSON.stringify(data.groups || []));
      for (const [provider, models] of cache) {
        const token = capture(provider);
        if (token) acceptLive(token, {profile: scope.profile, catalog_revision: scope.revision, provider, models});
      }
      publish();
      return true;
    },
    cached(token) { const rows = current(token) ? cache.get(token.provider) : null; return rows ? JSON.parse(JSON.stringify(rows)) : null; },
    acceptLive, snapshot,
    invalidate() { epoch++; scope = null; groups = []; cache.clear(); publish(); },
  };
})();
