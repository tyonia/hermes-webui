const fs = require('fs');
const vm = require('vm');
const assert = require('node:assert/strict');
const events = [];
const context = {window: {dispatchEvent: event => events.push(event.detail)}, S: {activeProfile: 'default'},
  CustomEvent: class {constructor(name, options) {this.detail = options.detail;}}, console,
  URL, document: {baseURI: 'http://fixture.invalid/'}, location: {href: 'http://fixture.invalid/'},
  _modelDropdownRequestSeq: 0, _liveModelCache: {'xai-oauth': [{id: 'legacy-wrong-profile'}]},
  _liveModelFetchPending: new Set(), _liveModelPendingOwners: new Map(), _redirectIfUnauth: () => false};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context);
const catalog = context.window.HermesProviderCatalog;
const main = (profile, revision, fixed = false, id = 'grok-chat') => ({profile, catalog_revision: revision,
  catalog_scope: {profile, revision, providers: [{id: 'xai-oauth', aliases: [], discover_models: !fixed, models: [id]}]},
  groups: [{provider_id: 'xai-oauth', models: [{id}]}]});
const live = (profile, revision, id) => ({profile, catalog_revision: revision, provider: 'xai-oauth', models: [{id}]});
(async () => {
  assert.equal(catalog.accept(main('default', 'A')), true);
  assert.equal(catalog.allowed('hidden-provider', 'grok-chat'), false);
  assert.equal(catalog.allowed('xai-oauth', 'grok-imagine-video'), false);
  const oldToken = catalog.capture('xai-oauth');
  const oldMain = catalog.begin();
  const models = catalog.acceptLive(oldToken, {profile:'default', catalog_revision:'A', provider:'xai-oauth', models:[{id:'grok-new'}, {id:'grok-imagine-image'}]});
  assert.deepEqual(Array.from(models, row => row.id), ['grok-new']);
  catalog.accept(main('default', 'A'));
  assert.ok(catalog.snapshot().groups[0].models.some(row => row.id === 'grok-new'), 'main refresh must retain same-owner live additions in the editor preview');
  catalog.accept(main('default', 'B', true, 'only-imported'));
  assert.equal(catalog.acceptLive(oldToken, live('default','A','late-model')), null);
  assert.equal(catalog.accept(main('default','A'), 'default', oldMain), false);
  assert.equal(catalog.allowed('xai-oauth','grok-new'), false);
  assert.equal(catalog.allowed('xai-oauth','@xai-oauth:only-imported'), true);
  context.S.activeProfile = 'ophelia'; catalog.invalidate();
  assert.equal(catalog.accept(main('default','B'), 'default'), false);
  assert.equal(catalog.accept(main('ophelia','C')), true);
  assert.equal(catalog.cached(catalog.capture('xai-oauth')), null);

  // Execute the actual composer live-fetch function. A pending request from A
  // must neither publish in B nor clear B's pending ownership.
  const ui = fs.readFileSync(process.argv[3], 'utf8');
  const start = ui.indexOf('async function _fetchLiveModels(');
  const end = ui.indexOf('/**', start);
  const requests = [], additions = [];
  context.fetch = () => new Promise(resolve => requests.push(data => resolve({json: async () => data})));
  context._addLiveModelsToSelect = (provider, rows) => {additions.push(...rows.map(row => row.id)); return rows.length;};
  vm.runInContext(ui.slice(start, end), context);
  const pendingA = context._fetchLiveModels('xai-oauth', {});
  assert.equal(requests.length, 1, 'provider-only legacy cache must not bypass a scoped fetch');
  context.S.activeProfile = 'default'; catalog.invalidate(); catalog.accept(main('default','D'));
  const pendingB = context._fetchLiveModels('xai-oauth', {});
  assert.equal(requests.length, 2);
  requests[0](live('ophelia','C','wrong-profile-late')); await pendingA;
  assert.deepEqual(additions, []);
  assert.ok(context._liveModelFetchPending.has('xai-oauth'));
  requests[1](live('default','D','right-profile')); await pendingB;
  assert.deepEqual(additions, ['right-profile']);
  assert.equal(context._liveModelFetchPending.has('xai-oauth'), false);
  catalog.accept(main('default','E',true,'fixed'));
  await context._fetchLiveModels('xai-oauth', {});
  assert.equal(requests.length, 2, 'fixed catalogs do not fetch or append vendor models');
  console.log('PASS profile catalog ownership, fixed models and late live results');
})().catch(error => {console.error(error); process.exitCode = 1;});
