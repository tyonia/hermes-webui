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
const view = () => ({profile, revision: 'revision', config_path: '/fixture/config.yaml', warnings: [], providers: [{
  ref: 'providers:custom:relay', source: 'providers', provider_id: 'custom:relay', name: 'Gateway',
  base_url: 'https://fixture.invalid/v1', api_mode: '', enabled: true, discover_models: false,
  default_model: '', key_env: '', headers: [], models: [{id: 'vendor/model:free', original_id: 'vendor/model:free', label: 'Selected model', context_length: 8192}],
}]});
const window = {addEventListener() {}, confirm: () => true};
const context = {window, document, console, t: key => key,
  _buildProviderCard: provider => { const card = new Element('div'); card.textContent = 'Configured: ' + provider.has_key; return card; },
  api: async path => path === '/api/provider-manager' ? view() : {profile, providers: managed}};
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
  console.log('PASS selection refresh and profile draft isolation');
})().catch(error => { console.error(error); process.exitCode = 1; });
