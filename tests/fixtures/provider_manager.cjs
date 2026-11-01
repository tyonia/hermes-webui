// Dependency-free DOM fixture: execute the actual module, test selection/draft
// lifetime across API refreshes. This does not claim browser layout fidelity.
const fs = require('fs');
const vm = require('vm');
const assert = require('node:assert/strict');
class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {};
    this.attributes = {}; this.style = {}; this.listeners = {}; this.value = '';
    this.className = ''; this._text = ''; this.disabled = false;
    this.classList = {add() {}, toggle() {}};
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent || '').join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; this._text = ''; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  walk() { return [this, ...this.children.flatMap(c => c.walk ? c.walk() : [])]; }
  querySelectorAll(selector) {
    if (selector === '[data-pm-persist]') return this.walk().filter(e => e.dataset.pmPersist);
    if (selector === 'details[open]') return this.walk().filter(e => e.tagName === 'DETAILS' && e.open);
    return this.walk().filter(e => e.tagName.toLowerCase() === selector);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  focus() { document.activeElement = this; }
}
let root = new Element('div');
const document = {activeElement: null, createElement: tag => new Element(tag), getElementById: () => null};
let profile = 'default';
let managed = [];
let emptyProfile = false;
let savedBuiltin = false;
let keyedBuiltin = false;
let removedBuiltin = false;
let builtinModels = [];
let gatewayDefault = '';
let gatewayModels = [{id: 'vendor/model:free', original_id: 'vendor/model:free', label: 'Selected model', context_length: 8192}];
const posts = [];
let delayManaged = false;
let finishManaged = null;
const builtin = () => ({
  ref: savedBuiltin ? 'providers:opencode-go' : 'builtin:opencode-go', source: savedBuiltin ? 'providers' : 'builtin',
  provider_id: 'opencode-go', name: 'OpenCode Go', base_url: 'https://opencode.ai/zen/go/v1',
  api_mode: 'chat_completions', enabled: true, discover_models: false, default_model: '',
  key_env: 'OPENCODE_GO_API_KEY', credential_configured: keyedBuiltin, headers: [], models: builtinModels,
});
const view = () => ({profile, revision: 'revision', config_path: '/fixture/config.yaml', warnings: [],
  catalog: [{...builtin(), ref: 'builtin:opencode-go', models: []}], providers: emptyProfile ? [] : [{
  ref: 'providers:custom:relay', source: 'providers', provider_id: 'custom:relay', name: 'Gateway',
  base_url: 'https://fixture.invalid/v1', api_mode: '', enabled: true, discover_models: false,
  default_model: gatewayDefault, key_env: '', headers: [], models: gatewayModels,
}].concat(keyedBuiltin && !removedBuiltin ? [builtin()] : [])});
const window = {addEventListener() {}, confirm: () => true};
const context = {window, document, console, t: key => key,
  _buildProviderCard: provider => { const card = new Element('div'); card.textContent = 'Configured: ' + provider.has_key; return card; },
  api: async (path, options) => {
    const owner = profile;
    if (path === '/api/models') return {profile: owner, catalog_revision: 'revision', groups: []};
    if (path === '/api/provider-manager/discover') {
      posts.push(JSON.parse(options.body));
      return {ok: true, models: [{id: 'fixture/discovered:free'}]};
    }
    if (path === '/api/provider-manager') {
      if (options) {
        const body = JSON.parse(options.body); posts.push(body);
        if (body.ref === 'providers:custom:relay') {
          gatewayDefault = body.changes?.default_model ?? gatewayDefault;
          gatewayModels = body.changes?.models || gatewayModels;
          return {...view(), saved_ref: body.ref};
        }
        removedBuiltin = body.op === 'delete';
        if (!removedBuiltin) { savedBuiltin = true; builtinModels = body.changes?.models || builtinModels; }
        return {...view(), saved_ref: removedBuiltin ? body.ref : 'providers:opencode-go'};
      }
      return view();
    }
    if (delayManaged) await new Promise(resolve => { finishManaged = resolve; });
    return {profile: owner, providers: managed};
  }};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context);
const heading = () => root.walk().find(e => e.tagName === 'H3')?.textContent;
const button = text => root.walk().find(e => e.tagName === 'BUTTON' && e.textContent === text);
(async () => {
  await window.HermesProviderManager.mount(root);
  assert.equal(heading(), 'Gateway');
  button('Selected model').listeners.click();
  assert.equal(heading(), 'Selected model');
  await window.HermesProviderManager.mount(root);
  assert.equal(heading(), 'Selected model', 'background config refresh must preserve exact model selection');
  button('Gateway1').listeners.click();
  const label = root.walk().find(e => e.tagName === 'LABEL' && e.textContent === 'pm_name');
  const input = root.walk().find(e => e.id === label.htmlFor);
  input.value = 'Retained draft'; input.listeners.input();
  profile = 'other-profile';
  await window.HermesProviderManager.mount(root);
  assert.equal(input.value, 'Retained draft');
  assert.equal(button('pm_save').disabled, true, 'foreign profile refresh cannot save the old draft');
  assert.ok(root.textContent.includes('pm_stale'));
  root = new Element('div'); profile = 'default';
  managed = [{id: 'openai', display_name: 'OpenAI', configurable: true, has_key: false, models: []}];
  await window.HermesProviderManager.mount(root);
  window.HermesProviderManager.reveal('managed:openai');
  assert.equal(heading(), 'OpenAI');
  assert.ok(root.textContent.includes('Configured: false'));
  managed = [{...managed[0], has_key: true}];
  await window.HermesProviderManager.mount(root);
  assert.equal(heading(), 'OpenAI', 'credential refresh must keep the selected managed provider');
  assert.ok(root.textContent.includes('Configured: true'), 'credential status must refresh after save');
  // Authenticated, OAuth and local built-ins stay in the Add picker; reading
  // or selecting them must not pin them into this profile's saved tree.
  managed = [
    {id: 'copilot', display_name: 'GitHub Copilot', is_oauth: true, has_key: true, key_source: 'oauth', models: []},
    {id: 'openai', display_name: 'OpenAI', configurable: true, has_key: true, key_source: 'env_var', models: []},
    {id: 'ollama', display_name: 'Ollama', is_self_hosted: true, has_key: false, models: []},
  ];
  root = new Element('div');
  await window.HermesProviderManager.mount(root);
  const tree = () => root.walk().find(e => e.tagName === 'NAV');
  for (const name of ['GitHub Copilot', 'OpenAI', 'Ollama']) {
    assert.ok(!tree().textContent.includes(name), `${name} must not appear in the saved-provider tree`);
  }
  assert.ok(window.HermesProviderManager.searchEntries().every(entry => !entry.pmRef.startsWith('managed:')));
  button('pm_add_provider').listeners.click();
  const typeLabel = root.walk().find(e => e.tagName === 'LABEL' && e.textContent === 'pm_provider_type');
  const picker = root.walk().find(e => e.id === typeLabel.htmlFor);
  for (const name of ['GitHub Copilot', 'OpenAI', 'Ollama']) assert.ok(picker.textContent.includes(name));
  picker.value = 'managed:copilot'; picker.listeners.change();
  assert.equal(heading(), 'GitHub Copilot');
  assert.ok(!tree().textContent.includes('GitHub Copilot'), 'opening auth from Add must not pin a built-in');
  await window.HermesProviderManager.mount(root);
  assert.equal(heading(), 'GitHub Copilot');
  assert.ok(!tree().textContent.includes('GitHub Copilot'), 'refresh must not pin the selected built-in');
  emptyProfile = true; root = new Element('div');
  await window.HermesProviderManager.mount(root);
  assert.equal(heading(), undefined, 'an empty config must not auto-open the first built-in');
  assert.ok(root.textContent.includes('pm_empty'));
  root = new Element('div'); delayManaged = true;
  await window.HermesProviderManager.mount(root);
  button('pm_add_provider').listeners.click();
  const idLabel = root.walk().find(e => e.tagName === 'LABEL' && e.textContent === 'pm_provider_id');
  const draftId = root.walk().find(e => e.id === idLabel.htmlFor);
  draftId.value = 'retained-new-id'; draftId.listeners.input();
  const pendingTypeLabel = root.walk().find(e => e.tagName === 'LABEL' && e.textContent === 'pm_provider_type');
  const pendingPicker = root.walk().find(e => e.id === pendingTypeLabel.htmlFor);
  assert.ok(!pendingPicker.textContent.includes('GitHub Copilot'));
  finishManaged(); await new Promise(resolve => setImmediate(resolve));
  assert.ok(pendingPicker.textContent.includes('GitHub Copilot'), 'late catalog must populate an already open Add picker');
  assert.equal(draftId.value, 'retained-new-id', 'late catalog must preserve typed provider settings');
  // Profile-owned .env keys are configured providers even without YAML rows.
  // Ambient/OAuth availability above must still not pin unrelated built-ins.
  delayManaged = true; emptyProfile = false; keyedBuiltin = true;
  managed.push({id: 'opencode-go', display_name: 'OpenCode Go', configurable: true,
    has_key: true, key_source: 'env_file', models: []});
  for (const name of ['default', 'ophelia', 'sisyphus']) {
    profile = name; savedBuiltin = name === 'sisyphus'; root = new Element('div');
    await window.HermesProviderManager.mount(root);
    const matches = tree().walk().filter(e => e.tagName === 'BUTTON' && e.textContent.startsWith('OpenCode Go'));
    assert.equal(matches.length, 1, `${name}: show the configured provider exactly once`);
    const search = window.HermesProviderManager.searchEntries().filter(e => e.label === 'OpenCode Go');
    assert.equal(search.length, 1);
    assert.equal(search[0].pmRef, savedBuiltin ? 'providers:opencode-go' : 'builtin:opencode-go');
    matches[0].listeners.click();
    assert.equal(heading(), 'OpenCode Go');
    for (const action of ['pm_fetch_models', 'pm_add_model', 'pm_delete_provider']) {
      assert.ok(button(action), `${name}: unified editor must offer ${action} before auth status resolves`);
    }
    assert.ok(root.walk().find(e => e.className === 'pm-heading').walk().some(e => e.tagName === 'BUTTON' && e.textContent === 'pm_delete_provider'), 'delete must be visible beside the title, outside Advanced settings');
    assert.ok(!root.textContent.includes('Configured: true'), 'API providers must not use the legacy auth-only card');
    assert.ok(!tree().textContent.includes('GitHub Copilot'));
  }
  delayManaged = false;
  // Fetch/import/delete a single model, save, then delete the whole provider.
  savedBuiltin = false; profile = 'default'; root = new Element('div');
  await window.HermesProviderManager.mount(root);
  button('OpenCode Go0').listeners.click();
  await button('pm_fetch_models').listeners.click();
  assert.equal(posts.at(-1).ref, 'builtin:opencode-go');
  button('pm_select_shown').listeners.click(); button('pm_import_selected').listeners.click();
  await button('pm_save').listeners.click();
  assert.equal(posts.at(-1).op, 'save');
  assert.equal(posts.at(-1).changes.models[0].id, 'fixture/discovered:free');
  button('fixture/discovered:free').listeners.click();
  button('pm_delete_model').listeners.click();
  await button('pm_save').listeners.click();
  assert.equal(posts.at(-1).changes.models.length, 0);
  await button('pm_delete_provider').listeners.click();
  assert.equal(posts.at(-1).op, 'delete');
  assert.ok(!tree().textContent.includes('OpenCode Go'));
  button('pm_add_provider').listeners.click();
  const addLabel = root.walk().find(e => e.tagName === 'LABEL' && e.textContent === 'pm_provider_type');
  const addPicker = root.walk().find(e => e.id === addLabel.htmlFor);
  addPicker.value = 'builtin:opencode-go'; addPicker.listeners.change();
  assert.equal(heading(), 'OpenCode Go');
  assert.ok(!button('pm_save').disabled, 'explicit Add must allow restoring the removed builtin');
  await button('pm_save').listeners.click();
  assert.ok(tree().textContent.includes('OpenCode Go'));
  savedBuiltin = false;
  keyedBuiltin = false;
  managed = managed.map(p => p.id === 'opencode-go' ? {...p, has_key: false, key_source: 'none'} : p);
  await window.HermesProviderManager.mount(root);
  assert.ok(!tree().textContent.includes('OpenCode Go'), 'removing the profile key must remove the managed sidebar row');
  managed = managed.filter(p => p.id !== 'opencode-go');
  profile = 'no-key-profile'; root = new Element('div');
  await window.HermesProviderManager.mount(root);
  assert.ok(!tree().textContent.includes('OpenCode Go'), 'another profile must not inherit a configured card');
  assert.ok(!window.HermesProviderManager.searchEntries().some(e => e.pmRef === 'builtin:opencode-go'));
  gatewayDefault = 'vendor/model:free'; root = new Element('div');
  await window.HermesProviderManager.mount(root);
  button('Selected model').listeners.click(); button('pm_delete_model').listeners.click();
  assert.equal(button('pm_fetch_models').disabled, false, 'model-only drafts must still fetch the saved connection');
  const fetching = button('pm_fetch_models').listeners.click();
  assert.equal(root.walk().find(e => e.tagName === 'FIELDSET').disabled, true, 'connection fields are locked while fetching');
  await fetching;
  assert.equal(root.walk().find(e => e.tagName === 'FIELDSET').disabled, false);
  assert.equal(posts.at(-1).ref, 'providers:custom:relay');
  assert.equal(posts.at(-1).changes, undefined, 'discovery never sends draft endpoint/credential changes');
  button('pm_select_shown').listeners.click(); button('pm_import_selected').listeners.click();
  const defaultLabel = root.walk().find(e => e.tagName === 'LABEL' && e.textContent === 'pm_provider_default');
  const defaultSelect = root.walk().find(e => e.id === defaultLabel.htmlFor);
  assert.equal(defaultSelect.tagName, 'SELECT', 'provider default must be a visible model choice');
  assert.equal(defaultSelect.value, 'vendor/model:free', 'deleting must not silently clear the default');
  assert.ok(defaultSelect.children.some(e => e.value === 'fixture/discovered:free'), 'new imports must populate the default picker without losing discovery');
  const beforeSave = posts.length;
  await button('pm_save').listeners.click();
  assert.equal(posts.length, beforeSave, 'a missing default must ask for an explicit replacement before saving');
  defaultSelect.value = 'fixture/discovered:free'; defaultSelect.listeners.change();
  const urlLabel = root.walk().find(e => e.tagName === 'LABEL' && e.textContent === 'Base URL');
  const urlInput = root.walk().find(e => e.id === urlLabel.htmlFor);
  urlInput.value = 'https://changed.invalid/v1'; urlInput.listeners.input();
  assert.equal(button('pm_fetch_models').disabled, true, 'connection changes must be saved first');
  urlInput.value = 'https://fixture.invalid/v1'; urlInput.listeners.input();
  assert.equal(button('pm_fetch_models').disabled, false, 'restoring the saved connection must allow fetch despite a dirty model draft');
  await button('pm_save').listeners.click();
  assert.equal(posts.at(-1).changes.default_model, 'fixture/discovered:free');
  assert.equal(posts.at(-1).changes.models[0].id, 'fixture/discovered:free');
  gatewayDefault = 'upstream-only-default'; root = new Element('div');
  await window.HermesProviderManager.mount(root);
  const currentNameLabel = root.walk().find(e => e.tagName === 'LABEL' && e.textContent === 'pm_name');
  const currentName = root.walk().find(e => e.id === currentNameLabel.htmlFor);
  currentName.value = 'Edited label'; currentName.listeners.input();
  const beforeLabelSave = posts.length;
  await button('pm_save').listeners.click();
  assert.equal(posts.length, beforeLabelSave + 1, 'an unlisted default that was not deleted must not block unrelated edits');
  assert.equal(posts.at(-1).changes.default_model, undefined);
  console.log('PASS selection refresh and profile draft isolation');
})().catch(error => { console.error(error); process.exitCode = 1; });
