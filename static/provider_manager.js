/* Provider/model split editor, inspired by agegr/pi-web's MIT workflow.
 * Owns only its view/draft. The server owns profile identity and persistence.
 */
(function () {
  'use strict';
  const tr = key => t('pm_' + key);
  const clone = value => JSON.parse(JSON.stringify(value));
  let instance = null;
  let fieldId = 0;

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text != null) item.textContent = text;
    return item;
  }
  function button(text, action, className = '') {
    const item = node('button', 'pm-button ' + className, text);
    item.type = 'button';
    item.addEventListener('click', action);
    return item;
  }
  function field(label, value, action, options = {}) {
    const wrap = node('div', 'pm-field');
    const id = 'pm-field-' + (++fieldId);
    const caption = node('label', '', label);
    caption.htmlFor = id;
    const input = node(options.options ? 'select' : 'input');
    input.id = id;
    if (options.options) {
      for (const [v, text] of options.options) {
        const option = node('option', '', text);
        option.value = v;
        input.append(option);
      }
    } else {
      input.type = options.type || 'text';
      input.autocomplete = 'off';
      input.spellcheck = false;
      if (options.placeholder) input.placeholder = options.placeholder;
    }
    input.value = value == null ? '' : value;
    if (options.readOnly) input.readOnly = true;
    input.addEventListener(options.options ? 'change' : 'input', () => action(input.value));
    wrap.append(caption, input);
    if (options.hint) wrap.append(node('small', 'pm-muted', options.hint));
    return wrap;
  }

  class Manager {
    constructor(root) {
      this.root = root;
      this.view = null;
      this.managed = [];
      this.current = null;
      this.index = null;
      this.dirty = false;
      this.busy = false;
      this.stale = false;
      this.query = '';
      this.generation = 0;
      this.discovery = null;
      this.root.classList.add('pm-root');
      window.addEventListener('beforeunload', event => {
        if (this.dirty) { event.preventDefault(); event.returnValue = ''; }
      });
    }
    profileChanged(name) {
      if (this.view && this.view.profile === name) return;
      ++this.generation;
      this.busy = false;
      if (this.form) this.form.disabled = false;
      if (this.dirty) {
        this.stale = true;
        this.message(tr('stale'), true);
        this.updateActions();
      } else this.load();
    }
    message(text, error = false) {
      this.notice.textContent = text;
      this.notice.classList.toggle('pm-error', error);
      this.notice.setAttribute('role', error ? 'alert' : 'status');
    }
    async load() {
      if (this.busy) return;
      const generation = ++this.generation;
      if (!this.view) this.root.replaceChildren(node('p', 'pm-muted', tr('loading')));
      try {
        const managedPromise = api('/api/providers').catch(() => ({providers: []}));
        const view = await api('/api/provider-manager');
        if (generation !== this.generation) return;
        if (this.dirty) {
          if (this.view.profile !== view.profile || this.view.revision !== view.revision) {
            this.stale = true;
            this.message(tr('stale'), true);
            this.updateActions();
          }
          return;
        }
        const sameProfile = this.view?.profile === view.profile;
        const selectedModel = sameProfile && this.index !== null ? this.current?.models?.[this.index]?.id : null;
        const selectedManaged = sameProfile ? this.current?.managed : null;
        this.view = view;
        this.managed = [];
        this.stale = false;
        const oldRef = this.current && this.current.ref;
        const next = view.providers.find(p => p.ref === oldRef) || view.providers[0];
        this.current = selectedManaged ? {ref: 'managed:' + selectedManaged.id, managed: selectedManaged} : next ? this.draft(next) : null;
        const restoredIndex = selectedModel && next ? next.models.findIndex(model => model.id === selectedModel) : -1;
        this.index = restoredIndex >= 0 ? restoredIndex : null;
        this.render();
        // Status/auth probing can be slow. Config editing must become usable
        // without waiting for it, and results must belong to the same profile.
        managedPromise.then(managed => {
          if (this.view !== view || this.stale || managed.profile !== view.profile) return;
          this.managed = (managed.providers || []).filter(p => !p.is_custom && (p.configurable || p.is_oauth || p.is_self_hosted || p.is_plugin_provider));
          if (this.current?.managed && !this.dirty) {
            const refreshed = this.managed.find(provider => provider.id === this.current.managed.id);
            if (refreshed) { this.current.managed = refreshed; this.renderDetail(); }
          }
          this.updateProviderTypes();
          this.renderSidebar();
          if (typeof _refreshProviderManagerSettingsSearch === 'function') _refreshProviderManagerSettingsSearch();
        });
      } catch (error) {
        if (generation !== this.generation) return;
        if (this.view) { this.message(error.message, true); return; }
        this.root.replaceChildren(node('p', 'pm-error', error.message), button(tr('reload'), () => this.load()));
      }
    }
    draft(entry) {
      return {...clone(entry), baseline: clone(entry), credential: {action: 'keep'}, headerDrafts: entry.headers.map(h => ({...h, action: 'keep', value: ''}))};
    }
    payload(op = 'save') {
      return {op, profile: this.view.profile, revision: this.view.revision, ref: this.current.ref};
    }
    markDirty() {
      this.dirty = true;
      this.message(tr('unsaved'));
      this.updateActions();
    }
    updateActions() {
      if (this.save) this.save.disabled = !this.dirty || this.busy || this.stale;
      if (this.discoverButton) this.discoverButton.disabled = this.dirty || this.busy || this.stale || !this.current.enabled || this.current.isNew;
      for (const control of this.root.querySelectorAll('[data-pm-persist]')) control.disabled = this.busy || this.stale || this.dirty;
      this.root.setAttribute('aria-busy', String(this.busy));
    }
    canLeave() { return !this.dirty || window.confirm(tr('discard')); }
    select(ref, index = null) {
      if (this.busy) return;
      if (this.current && this.current.ref === ref) {
        this.index = index;
        this.renderSidebar();
        this.renderDetail();
        this.detail.focus();
        return;
      }
      if (!this.canLeave()) return;
      ++this.generation;
      this.dirty = false;
      this.discovery = null;
      this.index = index;
      const entry = this.view.providers.find(p => p.ref === ref);
      const managed = this.managed.find(p => 'managed:' + p.id === ref);
      this.current = entry ? this.draft(entry) : managed ? {ref, managed} : null;
      this.renderSidebar();
      this.renderDetail();
      this.message('');
      this.detail.focus();
    }
    create() {
      if (this.busy) return;
      if (!this.canLeave()) return;
      ++this.generation;
      this.dirty = false;
      this.index = null;
      this.discovery = null;
      this.current = {ref: 'new', isNew: true, id: '', provider_id: '', name: '', base_url: '', api_mode: 'chat_completions', enabled: true, discover_models: false, default_model: '', models: [], headers: [], headerDrafts: [], credential: {action: 'keep'}, key_env: '', baseline: {}};
      this.renderSidebar();
      this.renderDetail();
      this.message('');
      this.detail.querySelector('input')?.focus();
    }
    render() {
      this.root.replaceChildren();
      const bar = node('div', 'pm-toolbar');
      const heading = node('div', 'pm-owner');
      heading.append(node('strong', '', this.view.profile), node('span', 'pm-muted', tr('profile_config')));
      bar.append(heading, button(tr('reload'), () => {
        if (!this.canLeave()) return;
        this.dirty = false;
        this.load();
      }));
      this.notice = node('p', 'pm-notice');
      this.notice.setAttribute('aria-live', 'polite');
      const split = node('div', 'pm-split');
      const aside = node('aside', 'pm-sidebar');
      aside.setAttribute('aria-label', tr('provider_list'));
      const search = field(tr('search'), this.query, value => { this.query = value; this.renderSidebar(); }, {type: 'search'});
      this.tree = node('nav', 'pm-tree');
      this.tree.setAttribute('aria-label', tr('provider_list'));
      aside.append(search, this.tree, button(tr('add_provider'), () => this.create(), 'pm-add'));
      this.detail = node('section', 'pm-detail');
      this.detail.tabIndex = -1;
      this.detail.setAttribute('aria-label', tr('details'));
      split.append(aside, this.detail);
      const destination = node('p', 'pm-destination');
      destination.append(node('span', 'pm-muted', tr('save_target') + ' '), node('code', '', this.view.config_path));
      this.root.append(bar, this.notice, split, destination);
      for (const warning of this.view.warnings || []) this.root.append(node('p', 'pm-muted', warning));
      const quota = node('details', 'pm-quota');
      quota.append(node('summary', '', tr('usage')));
      quota.addEventListener('toggle', async () => {
        if (!quota.open || quota.dataset.loaded) return;
        quota.dataset.loaded = 'true';
        try {
          const result = await _fetchProviderQuotaStatus(false);
          if (!quota.isConnected) return;
          const card = _buildProviderQuotaCard(result);
          if (card) { quota.append(card); renderProviderCostChart(card); }
        } catch (_) { quota.append(node('p', 'pm-muted', tr('usage_unavailable'))); }
      });
      this.root.append(quota);
      this.renderSidebar();
      this.renderDetail();
    }
    configuredManaged() {
      const owned = new Set(this.view.providers.map(p => p.provider_id));
      // has_key alone includes ambient credentials and automatic OAuth status.
      // env_file identifies an explicit key in this request profile's .env.
      return this.managed.filter(p => !owned.has(p.id) && p.has_key && p.key_source === 'env_file');
    }
    renderSidebar() {
      this.tree.replaceChildren();
      const query = this.query.toLowerCase().trim();
      let count = 0;
      const rows = this.view.providers.map(saved => this.current?.ref === saved.ref && !this.current.managed ? this.current : saved);
      for (const row of rows) {
        const matches = !query || [row.name, row.provider_id, ...row.models.map(m => m.id)].some(s => s.toLowerCase().includes(query));
        if (!matches) continue;
        count++;
        const selected = this.current?.ref === row.ref;
        const item = button('', () => this.select(row.ref), 'pm-provider-row');
        item.setAttribute('aria-current', String(selected && this.index === null));
        item.append(node('span', 'pm-row-name', row.name), node('span', 'pm-count', row.enabled ? String(row.models.length) : tr('disabled')));
        this.tree.append(item);
        if (selected || query) for (let index = 0; index < row.models.length; index++) {
          const model = row.models[index];
          if (query && !model.id.toLowerCase().includes(query) && !row.name.toLowerCase().includes(query)) continue;
          const item = button(model.label || model.id || tr('new_model'), () => this.select(row.ref, index), 'pm-model-row');
          item.title = model.id;
          item.setAttribute('aria-current', String(selected && this.index === index));
          this.tree.append(item);
        }
      }
      for (const provider of this.configuredManaged()) {
        if (query && ![provider.display_name, provider.id].some(s => s.toLowerCase().includes(query))) continue;
        count++;
        const ref = 'managed:' + provider.id;
        const item = button(provider.display_name, () => this.select(ref), 'pm-provider-row');
        item.setAttribute('aria-current', String(this.current?.ref === ref));
        this.tree.append(item);
      }
      if (!count) this.tree.append(node('p', 'pm-empty', tr('no_matches')));
    }
    renderDetail() {
      const advancedOpen = !!this.detail.querySelector('details[open]');
      this.detail.replaceChildren();
      this.save = null;
      this.discoverButton = null;
      this.providerTypeSelect = null;
      const current = this.current;
      if (!current) { this.detail.append(node('p', 'pm-empty', tr('empty'))); return; }
      if (current.managed) { this.renderManaged(current.managed); return; }
      const heading = node('div', 'pm-heading');
      const title = node('div');
      title.append(node('h3', '', this.index === null ? current.isNew ? tr('new_provider') : current.name : current.models[this.index]?.label || current.models[this.index]?.id || tr('new_model')));
      title.append(node('p', 'pm-muted pm-identity', current.provider_id || tr('stable_id')));
      heading.append(title);
      this.detail.append(heading);
      this.form = node('fieldset', 'pm-fields');
      this.form.disabled = this.busy;
      if (this.index === null) this.renderProvider(current);
      else this.renderModel(current, current.models[this.index]);
      this.detail.append(this.form);
      if (advancedOpen && this.form.querySelector('details')) this.form.querySelector('details').open = true;
      const footer = node('div', 'pm-actions');
      this.save = button(tr('save'), () => this.persist(), 'pm-primary');
      footer.append(node('span', 'pm-muted', current.isNew ? tr('create_hint') : tr('save_hint')), this.save);
      this.detail.append(footer);
      this.updateActions();
    }
    providerTypes() {
      return [['', tr('custom_endpoint')], ...this.managed.map(provider => [provider.id, provider.display_name])];
    }
    updateProviderTypes() {
      if (!this.current?.isNew || !this.providerTypeSelect) return;
      const selected = this.providerTypeSelect.value;
      this.providerTypeSelect.replaceChildren(...this.providerTypes().map(([value, label]) => {
        const option = node('option', '', label);
        option.value = value;
        return option;
      }));
      this.providerTypeSelect.value = selected;
    }
    renderProvider(draft) {
      if (draft.isNew) {
        const type = field(tr('provider_type'), '', id => { if (id) this.select('managed:' + id); }, {options: this.providerTypes()});
        this.providerTypeSelect = type.querySelector('select');
        this.form.append(type);
        this.form.append(field(tr('provider_id'), draft.id, value => { draft.id = value; this.markDirty(); }, {placeholder: 'my-gateway', hint: tr('stable_id')}));
      }
      const grid = node('div', 'pm-grid');
      const set = (key, value) => { draft[key] = value; this.markDirty(); };
      grid.append(field(tr('name'), draft.name, value => set('name', value)));
      grid.append(field(tr('protocol'), draft.api_mode, value => set('api_mode', value), {options: [
        ['', tr('inherit_protocol')], ['chat_completions', 'OpenAI Chat Completions'], ['codex_responses', 'OpenAI Responses'], ['anthropic_messages', 'Anthropic Messages'], ['bedrock_converse', 'Bedrock Converse'],
        ...(!['', 'chat_completions', 'codex_responses', 'anthropic_messages', 'bedrock_converse'].includes(draft.api_mode) ? [[draft.api_mode, draft.api_mode]] : []),
      ]}));
      this.form.append(grid, field('Base URL', draft.base_url, value => set('base_url', value), {placeholder: 'https://gateway.example/v1', hint: draft.base_url_redacted ? tr('hidden_url') : tr('url_hint')}));
      const credential = node('div', 'pm-section');
      credential.append(node('h4', '', tr('credentials')));
      const mode = draft.credential.action;
      credential.append(field(tr('credential_action'), mode, value => {
        draft.credential = {action: value, value: ''};
        this.markDirty();
        this.renderDetail();
      }, {options: [['keep', draft.credential_configured ? tr('keep_credential') : tr('no_credential')], ['set', tr('replace_credential')], ['env', tr('env_credential')], ['remove', tr('remove_credential')]]}));
      if (mode === 'set') credential.append(field('API Key', draft.credential.value, value => { draft.credential.value = value; this.markDirty(); }, {type: 'password'}));
      if (mode === 'env') credential.append(field(tr('env_name'), draft.credential.value, value => { draft.credential.value = value; this.markDirty(); }, {placeholder: 'MY_PROVIDER_API_KEY', hint: tr('env_hint')}));
      if (mode === 'keep' && draft.key_env) credential.append(node('p', 'pm-muted', tr('env_name') + ': ' + draft.key_env));
      const managed = this.managed.find(p => p.id === draft.provider_id);
      if (managed) {
        const auth = node('details', 'pm-section');
        auth.append(node('summary', '', tr('managed_auth')));
        auth.append(_buildProviderCard(managed));
        this.form.append(auth);
      } else this.form.append(credential);
      const modelSection = node('div', 'pm-section');
      const modelHead = node('div', 'pm-section-head');
      modelHead.append(node('h4', '', tr('models')));
      modelHead.append(button(tr('add_model'), () => {
        draft.models.push({id: '', label: '', context_length: null});
        this.index = draft.models.length - 1;
        this.markDirty(); this.renderSidebar(); this.renderDetail();
        this.detail.querySelector('input')?.focus();
      }));
      modelSection.append(modelHead);
      modelSection.append(field(tr('catalog_mode'), String(draft.discover_models), value => set('discover_models', value === 'true'), {options: [['false', tr('fixed_catalog')], ['true', tr('follow_catalog')]], hint: tr('catalog_hint')}));
      this.discoverButton = button(tr('fetch_models'), () => this.fetchModels());
      modelSection.append(this.discoverButton, node('p', 'pm-muted', tr('fetch_hint')));
      this.discoveryHost = node('div', 'pm-discovery');
      modelSection.append(this.discoveryHost);
      this.form.append(modelSection);
      if (this.discovery) this.renderDiscovery();
      const advanced = node('details', 'pm-section');
      advanced.append(node('summary', '', tr('advanced')));
      advanced.append(field(tr('provider_default'), draft.default_model, value => set('default_model', value)));
      advanced.append(field(tr('enabled'), String(draft.enabled), value => set('enabled', value === 'true'), {options: [['true', tr('enabled')], ['false', tr('disabled')]]}));
      advanced.append(node('h4', '', tr('headers')));
      const headers = node('div', 'pm-headers');
      for (const header of draft.headerDrafts) {
        const row = node('div', 'pm-header-row');
        row.append(field(tr('header_name'), header.name, value => { header.name = value; this.markDirty(); }, {readOnly: !!header.configured}));
        row.append(field(tr('header_action'), header.action, value => { header.action = value; this.markDirty(); this.renderDetail(); }, {options: [['keep', tr('keep')], ['set', tr('replace')], ['remove', tr('remove')]]}));
        if (header.action === 'set') row.append(field(tr('header_value'), header.value, value => { header.value = value; this.markDirty(); }, {type: 'password'}));
        headers.append(row);
      }
      advanced.append(headers, button(tr('add_header'), () => {
        draft.headerDrafts.push({name: '', action: 'set', value: ''});
        this.markDirty(); this.renderDetail();
        this.detail.querySelector('details').open = true;
      }));
      if (!draft.isNew) advanced.append(button(tr('delete_provider'), () => this.removeProvider(), 'pm-danger'));
      this.form.append(advanced);
    }
    renderModel(draft, model) {
      if (!model) return;
      this.form.append(field(tr('model_id'), model.id, value => { model.id = value; this.markDirty(); }, {hint: tr('model_id_hint')}));
      this.form.append(field(tr('model_label'), model.label, value => { model.label = value; this.markDirty(); }));
      this.form.append(field(tr('context'), model.context_length, value => {
        model.context_length = value === '' ? null : /^\d+$/.test(value) ? Number(value) : value;
        this.markDirty();
      }, {type: 'number', hint: tr('context_hint')}));
      const actions = node('div', 'pm-model-actions');
      const setDefault = button(tr('set_default'), () => this.setDefault(model.id));
      setDefault.dataset.pmPersist = 'true';
      actions.append(setDefault, button(tr('delete_model'), () => {
        draft.models.splice(this.index, 1); this.index = null;
        this.markDirty(); this.renderSidebar(); this.renderDetail();
      }, 'pm-danger'));
      this.form.append(actions, node('p', 'pm-muted', tr('default_hint')));
    }
    renderManaged(provider) {
      const head = node('div', 'pm-heading');
      head.append(node('h3', '', provider.display_name));
      this.detail.append(head, node('p', 'pm-muted', tr('managed_hint')));
      if (typeof _providerCardEls !== 'undefined') _providerCardEls.clear();
      const card = _buildProviderCard(provider);
      card.classList.add('open', 'pm-managed-card');
      this.detail.append(card);
      const models = (provider.models || []).map(model => typeof model === 'string' ? model : model.id).filter(Boolean);
      if (models.length) {
        let picked = models[0];
        this.detail.append(field(tr('model_id'), picked, value => { picked = value; }, {options: models.map(id => [id, id])}));
        const save = button(tr('set_default'), () => this.setDefault(picked));
        save.dataset.pmPersist = 'true';
        this.detail.append(save);
      }
      this.updateActions();
    }
    async persist() {
      if (this.busy || this.stale) return;
      const draft = this.current;
      const changes = {};
      for (const key of ['name', 'base_url', 'api_mode', 'enabled', 'discover_models', 'default_model', 'models']) {
        if (draft.isNew || JSON.stringify(draft[key]) !== JSON.stringify(draft.baseline[key])) changes[key] = draft[key];
      }
      const credential = clone(draft.credential);
      if (credential.action === 'env') { changes.key_env = credential.value; credential.action = 'keep'; delete credential.value; }
      const payload = {...this.payload(draft.isNew ? 'create' : 'save'), id: draft.id, changes, credential,
        headers: draft.headerDrafts.filter(h => h.action !== 'keep').map(h => ({name: h.name, action: h.action, value: h.value}))};
      await this.mutate(payload);
    }
    async mutate(payload) {
      this.busy = true;
      this.updateActions();
      if (this.form) this.form.disabled = true;
      const selected = this.current;
      const generation = ++this.generation;
      try {
        const result = await api('/api/provider-manager', {method: 'POST', body: JSON.stringify(payload)});
        if (generation !== this.generation || selected !== this.current) return;
        this.view = result;
        this.dirty = false;
        this.discovery = null;
        const row = result.providers.find(p => p.ref === result.saved_ref) || (!selected.managed ? result.providers[0] : null);
        this.current = row ? this.draft(row) : selected.managed ? selected : null;
        if (this.index !== null && !this.current?.models?.[this.index]) this.index = null;
        this.renderSidebar(); this.renderDetail();
        this.message(tr('saved'));
        if (typeof _refreshProviderManagerSettingsSearch === 'function') _refreshProviderManagerSettingsSearch();
        if (typeof _refreshModelDropdownsAfterProviderChange === 'function') _refreshModelDropdownsAfterProviderChange();
      } catch (error) {
        if (generation !== this.generation) return;
        this.message(error.message, true);
        if (error.status === 409) this.message(error.message + ' ' + tr('draft_retained'), true);
      } finally {
        if (generation === this.generation) {
          this.busy = false;
          if (this.form) this.form.disabled = false;
          this.updateActions();
        }
      }
    }
    async removeProvider() {
      if (this.busy || this.stale || !window.confirm(tr('confirm_delete'))) return;
      await this.mutate(this.payload('delete'));
    }
    async setDefault(model) {
      if (this.busy || this.stale || this.dirty) return;
      await this.mutate({...this.payload('default'), model});
    }
    async fetchModels() {
      if (this.busy || this.dirty || this.stale || this.current.isNew) return;
      const selected = this.current;
      const generation = ++this.generation;
      this.busy = true; this.updateActions();
      this.message(tr('fetching'));
      try {
        const result = await api('/api/provider-manager/discover', {method: 'POST', body: JSON.stringify(this.payload()), timeoutMs: 20000});
        if (generation !== this.generation || selected !== this.current) return;
        this.discovery = {models: result.models || [], selected: new Set(), query: ''};
        this.renderDiscovery(); this.message(tr('choose_models'));
      } catch (error) { if (generation === this.generation) this.message(error.message, true); }
      finally { if (generation === this.generation) { this.busy = false; this.updateActions(); } }
    }
    renderDiscovery() {
      const state = this.discovery;
      const host = this.discoveryHost;
      if (!state || !host) return;
      host.replaceChildren();
      const list = node('div', 'pm-discovered-list');
      const count = node('p', 'pm-muted');
      const existing = new Set(this.current.models.map(m => m.id));
      const filtered = () => state.models.filter(m => m.id.toLowerCase().includes(state.query.toLowerCase())).slice(0, 300);
      const add = button(tr('import_selected'), () => {
        for (const model of state.models) if (state.selected.has(model.id) && !existing.has(model.id)) this.current.models.push({id: model.id, label: '', context_length: model.context_length || null});
        state.selected.clear(); this.markDirty(); this.renderSidebar(); this.renderDiscovery();
      });
      const repaint = () => {
        list.replaceChildren();
        for (const model of filtered()) {
          const label = node('label', 'pm-discovered-row');
          const check = node('input'); check.type = 'checkbox';
          check.disabled = existing.has(model.id); check.checked = check.disabled || state.selected.has(model.id);
          check.addEventListener('change', () => { if (check.checked) state.selected.add(model.id); else state.selected.delete(model.id); updateCount(); });
          label.append(check, node('span', '', model.id)); list.append(label);
        }
        updateCount();
      };
      const updateCount = () => { count.textContent = state.selected.size + ' / ' + state.models.length + ' ' + tr('selected_count'); add.disabled = !state.selected.size; };
      host.append(field(tr('filter_models'), state.query, value => { state.query = value; repaint(); }, {type: 'search'}), count);
      const actions = node('div', 'pm-inline-actions');
      actions.append(button(tr('select_shown'), () => { for (const model of filtered()) if (!existing.has(model.id)) state.selected.add(model.id); repaint(); }), button(tr('clear_selection'), () => { state.selected.clear(); repaint(); }));
      host.append(actions, list, add);
      if (!state.models.length) host.append(node('p', 'pm-muted', tr('no_models')));
      repaint();
    }
  }

  window.HermesProviderManager = {
    profileChanged(name) { if (instance) instance.profileChanged(name); },
    searchEntries() {
      if (!instance?.view || instance.stale) return [];
      const saved = instance.view.providers.flatMap(provider => [
        {label: provider.name, searchBlob: provider.name + ' ' + provider.provider_id, pmRef: provider.ref},
        ...provider.models.map((model, index) => ({label: provider.name + ' / ' + (model.label || model.id), searchBlob: provider.name + ' ' + provider.provider_id + ' ' + model.id + ' ' + model.label, pmRef: provider.ref, pmModelIndex: index})),
      ]);
      return saved.concat(instance.configuredManaged().map(provider => ({
        label: provider.display_name, searchBlob: provider.display_name + ' ' + provider.id, pmRef: 'managed:' + provider.id,
      })));
    },
    reveal(ref, index) {
      if (!instance || instance.stale) return null;
      instance.select(ref, index == null ? null : index);
      return instance.detail;
    },
    mount(root) {
      if (!root) return;
      if (!instance || instance.root !== root) instance = new Manager(root);
      const legacy = document.getElementById('providersList');
      if (legacy) legacy.style.display = 'none';
      const empty = document.getElementById('providersEmpty');
      if (empty) empty.style.display = 'none';
      return instance.load();
    },
  };
})();
