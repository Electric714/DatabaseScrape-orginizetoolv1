(() => {
  'use strict';

  const VIEW_TITLES = {
    dashboard: 'Dashboard',
    database: 'Bidder database',
    import: 'Import',
    research: 'Research',
    comparison: 'Comparison review',
    evidence: 'Evidence',
    sources: 'Sources',
    activity: 'Activity',
    settings: 'Settings'
  };
  const VALID_VIEWS = new Set(Object.keys(VIEW_TITLES));
  let evidenceLoaded = false;
  let reviewMode = 'changes';

  function addShellStyles() {
    if (document.querySelector('link[href="/static/research-desk.css"]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/static/research-desk.css';
    document.head.appendChild(link);
  }

  async function request(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: {'Content-Type': 'application/json', ...(options.headers || {})},
      signal: AbortSignal.timeout(20000)
    });
    if (!response.ok) {
      let message = `Request failed (${response.status}).`;
      try {
        const body = await response.json();
        message = body.detail || message;
      } catch {}
      throw new Error(Array.isArray(message) ? message.map(item => item.msg || item).join('; ') : message);
    }
    return response.status === 204 ? null : response.json();
  }

  function htmlEscape(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  }

  function safeHttpUrl(value) {
    try {
      const url = new URL(value);
      return ['http:', 'https:'].includes(url.protocol) ? url.href : '#';
    } catch {
      return '#';
    }
  }

  function tell(message, isError = false) {
    if (typeof notify === 'function') {
      notify(message, isError);
      return;
    }
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = message;
    toast.className = 'toast' + (isError ? ' error' : '');
    toast.hidden = false;
    setTimeout(() => { toast.hidden = true; }, 4500);
  }

  function pageHeader(title, description, actions = '') {
    return `<div class="desk-page-header"><h1>${htmlEscape(title)}</h1><p>${htmlEscape(description)}</p>${actions ? `<div class="desk-page-actions">${actions}</div>` : ''}</div>`;
  }

  function installNavigation() {
    const brand = document.querySelector('.brand');
    if (brand) {
      brand.href = '#dashboard';
      brand.innerHTML = '<img src="/static/mark.svg" alt="" width="44" height="44"><span>Paralegal<br><strong>Research Desk</strong></span>';
    }

    const nav = document.querySelector('.sidebar nav');
    if (nav) {
      const items = [
        ['dashboard', '▦', 'Dashboard', ''],
        ['database', '▤', 'Bidder database', ''],
        ['import', '⇧', 'Import', ''],
        ['research', '⌕', 'Research', ''],
        ['comparison', '☑', 'Comparison review', '<span id="navPending" class="nav-count">0</span>'],
        ['evidence', '▱', 'Evidence', '<span id="navEvidence" class="nav-count">0</span>'],
        ['sources', '▣', 'Sources', '<span id="navSources" class="nav-count">0</span>'],
        ['activity', '⌁', 'Activity', ''],
        ['settings', '⚙', 'Settings', '']
      ];
      nav.innerHTML = items.map(([view, icon, label, trailing]) =>
        `<a href="#${view}" class="nav-link" data-view-link="${view}"><span class="nav-icon" aria-hidden="true">${icon}</span>${label}${trailing}</a>`
      ).join('');
    }

    const topbar = document.querySelector('.topbar');
    const breadcrumb = document.querySelector('.breadcrumb');
    if (topbar && !document.getElementById('deskMenuButton')) {
      const menu = document.createElement('button');
      menu.type = 'button';
      menu.id = 'deskMenuButton';
      menu.className = 'desk-menu-button';
      menu.setAttribute('aria-label', 'Open navigation');
      menu.textContent = '☰';
      topbar.insertBefore(menu, topbar.firstChild);
    }
    if (breadcrumb) breadcrumb.innerHTML = '<span id="deskPageTitle">Dashboard</span>';

    if (!document.querySelector('.desk-nav-backdrop')) {
      const backdrop = document.createElement('button');
      backdrop.type = 'button';
      backdrop.className = 'desk-nav-backdrop';
      backdrop.setAttribute('aria-label', 'Close navigation');
      document.body.appendChild(backdrop);
    }
  }

  function markExistingViews() {
    const hero = document.querySelector('.hero');
    const metrics = document.querySelector('.metrics');
    const records = document.getElementById('recordsSection');
    const comparison = document.getElementById('compareSection');
    const sources = document.getElementById('sourceSection');
    const scans = document.getElementById('scanSection');
    const consoleColumn = document.getElementById('consoleSection');

    if (hero) hero.dataset.viewSection = 'dashboard';
    if (metrics) metrics.dataset.viewSection = 'dashboard';
    if (records) records.dataset.viewSection = 'database';
    if (comparison) comparison.dataset.viewSection = 'comparison';
    if (sources) sources.dataset.viewSection = 'sources';
    if (scans) scans.dataset.viewSection = 'activity';
    if (consoleColumn) consoleColumn.dataset.viewSection = 'activity';
  }

  function installDashboard(mainColumn) {
    if (document.getElementById('dashboardSection')) return;
    const section = document.createElement('section');
    section.id = 'dashboardSection';
    section.dataset.viewSection = 'dashboard';
    section.innerHTML = `
      <div class="desk-dashboard-grid">
        <button class="desk-dashboard-card" type="button" data-open-view="database"><span>Approved master</span><strong><span id="dashMasterCount">0</span> contractors</strong><small>Search the bidder database without mixing in unapproved research.</small></button>
        <button class="desk-dashboard-card" type="button" data-open-view="research"><span>Targeted research</span><strong>Run source checks</strong><small>Choose contractors and public-record sources, then collect evidence.</small></button>
        <button class="desk-dashboard-card" type="button" data-open-view="comparison"><span>Waiting for review</span><strong><span id="dashPendingCount">0</span> changes</strong><small>Compare current master values against newly collected source values.</small></button>
        <button class="desk-dashboard-card" type="button" data-open-view="evidence"><span>Source evidence</span><strong><span id="dashEvidenceCount">0</span> records</strong><small>Inspect what each source actually returned before approving anything.</small></button>
        <button class="desk-dashboard-card" type="button" data-open-view="activity"><span>Collection status</span><strong><span id="dashRunningCount">0</span> running</strong><small>See collection history, warnings, errors, and diagnostic logs.</small></button>
        <button class="desk-dashboard-card" type="button" data-open-view="import"><span>Master baseline</span><strong>Import CSV</strong><small>Load or refresh the firm's approved 30-column bidder database.</small></button>
      </div>`;
    mainColumn.insertBefore(section, mainColumn.firstChild);
  }

  function installImportView(mainColumn) {
    if (document.getElementById('importSection')) return;
    const section = document.createElement('section');
    section.id = 'importSection';
    section.dataset.viewSection = 'import';
    section.innerHTML = pageHeader(
      'Import master database',
      'The uploaded bidder CSV is the approved baseline. Research records stay separate until a field change is explicitly approved.'
    ) + `<div class="desk-import-layout"><div id="deskImportMount"></div><aside class="desk-info-card"><h3>What this import does</h3><p>It loads the firm's exact 30-column bidder structure and uses those contractors as the only query list for research.</p><div class="desk-guardrail"><span>✓</span><span>Research cannot silently overwrite the master database.</span></div><div class="desk-guardrail"><span>✓</span><span>Blank or failed source checks never erase an existing master value.</span></div><div class="desk-guardrail"><span>✓</span><span>New findings move to Comparison review before they can become approved data.</span></div></aside></div>`;
    mainColumn.insertBefore(section, document.getElementById('recordsSection'));
    const mount = section.querySelector('#deskImportMount');
    const importBar = document.getElementById('masterImportBar');
    if (mount && importBar) mount.appendChild(importBar);
  }

  function installDatabaseEnhancements() {
    const records = document.getElementById('recordsSection');
    if (!records) return;
    records.classList.add('compact-master');
    const heading = records.querySelector('.section-heading');
    const exports = records.querySelector('.export-buttons');
    if (heading && !document.getElementById('deskDatabaseActions')) {
      const actions = document.createElement('div');
      actions.id = 'deskDatabaseActions';
      actions.className = 'desk-database-actions';
      actions.innerHTML = '<button class="button primary small" type="button" data-open-view="import">Import file</button><button class="button subtle small" type="button" id="toggleAllFields">Show all 30 fields</button>';
      if (exports) actions.appendChild(exports);
      heading.appendChild(actions);
    }
    const note = records.querySelector('.evidence-note');
    if (note) note.textContent = 'Blank cells remain unknown until an approved source supplies a value. Newly researched values stay separate and appear in the Comparison review tab before the master can change.';
  }

  function installResearchView(mainColumn) {
    if (document.getElementById('researchSection')) return;
    const section = document.createElement('section');
    section.id = 'researchSection';
    section.dataset.viewSection = 'research';
    section.innerHTML = pageHeader(
      'Research',
      'Only companies already in the bidder database are queried. Each source writes evidence, not master fields.'
    ) + `
      <div class="surface desk-query-plan">
        <h2>Query plan</h2>
        <div class="desk-query-options" role="group" aria-label="Contractors to research">
          <label><input type="radio" name="deskResearchMode" value="all" checked> All contractors</label>
          <label><input type="radio" name="deskResearchMode" value="selected"> Selected contractors</label>
          <label><input type="checkbox" id="deskBypassCache"> Bypass cache</label>
        </div>
        <div id="deskResearchSelectionMount"></div>
        <div class="desk-research-sources" id="deskResearchSources"><div class="loading-state">Loading research sources…</div></div>
        <div class="desk-run-bar"><p id="deskResearchStatus">Choose the sources to query, then run research.</p><div class="desk-page-actions"><button class="button subtle" type="button" data-open-view="activity">View activity</button><button class="button primary" type="button" id="deskRunResearch">Run research</button></div></div>
      </div>`;
    mainColumn.insertBefore(section, document.getElementById('compareSection'));
    const mount = document.getElementById('deskResearchSelectionMount');
    const selection = document.querySelector('.research-selection');
    if (mount && selection) mount.appendChild(selection);
  }

  function installComparisonEnhancements() {
    const comparison = document.getElementById('compareSection');
    if (!comparison) return;
    const title = document.getElementById('compareTitle');
    if (title) title.textContent = 'Comparison review';
    const kicker = comparison.querySelector('.section-kicker');
    if (kicker) kicker.textContent = 'CURRENT MASTER / NEW RESEARCH / DECISION';
    const headingText = comparison.querySelector('.section-heading p');
    if (headingText && headingText.id !== 'baselineStatus') headingText.textContent = 'Research never writes the master database. Approve a field to update it or keep the existing value.';
    const toolbar = comparison.querySelector('.proposal-toolbar');
    if (toolbar && !document.getElementById('deskReviewTabs')) {
      const tabs = document.createElement('div');
      tabs.id = 'deskReviewTabs';
      tabs.className = 'desk-review-tabs';
      tabs.innerHTML = '<button type="button" class="desk-review-tab active" data-review-mode="changes">Proposed changes <span id="deskChangeCount">(0)</span></button><button type="button" class="desk-review-tab" data-review-mode="identity">Identity review <span id="deskIdentityCount">(0)</span></button>';
      toolbar.parentNode.insertBefore(tabs, toolbar);
      const empty = document.createElement('div');
      empty.id = 'deskReviewEmpty';
      empty.className = 'desk-evidence-empty';
      empty.hidden = true;
      comparison.querySelector('.proposal-table-wrap').after(empty);
    }
  }

  function installEvidenceView(mainColumn) {
    if (document.getElementById('evidenceSection')) return;
    const section = document.createElement('section');
    section.id = 'evidenceSection';
    section.dataset.viewSection = 'evidence';
    section.innerHTML = pageHeader(
      'Evidence',
      'Inspect the source record, query result, match context, and extracted values independently from the approved master database.'
    ) + `
      <form class="desk-evidence-toolbar" id="deskEvidenceForm">
        <input id="deskEvidenceSearch" type="search" placeholder="Search contractor or source record" aria-label="Search evidence">
        <select id="deskEvidenceSource" aria-label="Filter evidence by source"><option value="">All sources</option></select>
        <button class="button primary" type="submit">Search</button>
      </form>
      <div class="desk-evidence-list" id="deskEvidenceList"><div class="loading-state">Loading source evidence…</div></div>`;
    mainColumn.insertBefore(section, document.getElementById('sourceSection'));
  }

  function installSourcesView() {
    const sources = document.getElementById('sourceSection');
    if (!sources) return;
    const title = document.getElementById('sourcesTitle');
    if (title) {
      const count = document.getElementById('sourceCount');
      title.childNodes[0].nodeValue = 'Sources ';
      if (count) title.appendChild(count);
    }
    const kicker = sources.querySelector('.section-kicker');
    if (kicker) kicker.textContent = 'SOURCE STATUS / FIELD OWNERSHIP';
    const note = document.getElementById('siteSetupNote');
    if (note) note.textContent = 'Each adapter owns specific fields. A failed check remains unknown rather than being displayed as a clean result.';
  }

  function installSettingsView(mainColumn) {
    if (document.getElementById('settingsSection')) return;
    const section = document.createElement('section');
    section.id = 'settingsSection';
    section.dataset.viewSection = 'settings';
    section.innerHTML = pageHeader(
      'Settings',
      'Configure local API credentials and workspace help. Keys remain on this computer and are not written to bidder exports.'
    ) + `
      <div class="desk-settings-grid">
        <div class="desk-setting-card"><h3>OSHA / DOL API</h3><p>The official Department of Labor API requires a key for OSHA inspection and violation research.</p><div id="deskDolKeyMount"></div></div>
        <div class="desk-setting-card"><h3>SAM.gov exclusions</h3><p>The federal exclusions collector uses a SAM.gov Public API Key and stores it only in the local runtime folder.</p><div id="deskSamKeyMount"></div></div>
        <div class="desk-setting-card"><h3>Workflow help</h3><p>Review the import → research → evidence → comparison → approval workflow and the safety rules around unknown results.</p><button class="button subtle" type="button" id="deskOpenHelp">Open workflow help</button></div>
        <div class="desk-setting-card"><h3>Diagnostics</h3><p>Collection errors and warnings stay in Activity. Export a diagnostic report there when we need to troubleshoot a failed run.</p><button class="button subtle" type="button" data-open-view="activity">Open activity</button></div>
      </div>`;
    mainColumn.appendChild(section);

    const dolMount = document.getElementById('deskDolKeyMount');
    const samMount = document.getElementById('deskSamKeyMount');
    const dolButton = document.getElementById('oshaApiKeyButton');
    const samButton = document.getElementById('samApiKeyButton');
    if (dolMount && dolButton) dolMount.appendChild(dolButton);
    if (samMount && samButton) samMount.appendChild(samButton);
  }

  function installViewHeaders() {
    const records = document.getElementById('recordsSection');
    if (records && !records.previousElementSibling?.classList.contains('desk-page-header')) {
      records.insertAdjacentHTML('beforebegin', pageHeader('Bidder database', 'This is the approved master record. Research cannot overwrite it.'));
      const header = records.previousElementSibling;
      header.dataset.viewSection = 'database';
    }
    const comparison = document.getElementById('compareSection');
    if (comparison && !comparison.previousElementSibling?.classList.contains('desk-page-header')) {
      comparison.insertAdjacentHTML('beforebegin', pageHeader('Comparison review', 'Current master values are shown against newly researched source values. Nothing changes until you decide.'));
      comparison.previousElementSibling.dataset.viewSection = 'comparison';
    }
    const sources = document.getElementById('sourceSection');
    if (sources && !sources.previousElementSibling?.classList.contains('desk-page-header')) {
      sources.insertAdjacentHTML('beforebegin', pageHeader('Sources', 'See which collectors are ready, what they provide, and whether credentials or source access are blocking research.'));
      sources.previousElementSibling.dataset.viewSection = 'sources';
    }
    const scans = document.getElementById('scanSection');
    if (scans && !scans.previousElementSibling?.classList.contains('desk-page-header')) {
      scans.insertAdjacentHTML('beforebegin', pageHeader('Activity', 'Collection progress, completed runs, warnings, errors, and diagnostic tools.'));
      scans.previousElementSibling.dataset.viewSection = 'activity';
    }
  }

  function switchView(view, updateHash = true) {
    if (!VALID_VIEWS.has(view)) view = 'dashboard';
    document.body.dataset.activeView = view;
    document.querySelectorAll('[data-view-section]').forEach(element => {
      element.hidden = element.dataset.viewSection !== view;
    });
    document.querySelectorAll('[data-view-link]').forEach(link => {
      const active = link.dataset.viewLink === view;
      link.classList.toggle('active', active);
      if (active) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
    const title = document.getElementById('deskPageTitle');
    if (title) title.textContent = VIEW_TITLES[view];
    document.body.classList.remove('nav-open');
    if (updateHash && location.hash !== `#${view}`) history.pushState(null, '', `#${view}`);
    if (view === 'evidence') loadEvidence().catch(error => renderEvidenceError(error));
    if (view === 'research') refreshResearchSources().catch(error => setResearchStatus(error.message, true));
    window.scrollTo({top:0, behavior:'instant'});
  }

  function syncMirror(sourceId, targetId) {
    const source = document.getElementById(sourceId);
    const target = document.getElementById(targetId);
    if (!source || !target) return;
    const update = () => { target.textContent = source.textContent.trim() || '0'; };
    update();
    new MutationObserver(update).observe(source, {childList:true, characterData:true, subtree:true});
  }

  function installMirrors() {
    syncMirror('statRecords', 'dashMasterCount');
    syncMirror('pendingUpdateCount', 'dashPendingCount');
    syncMirror('sourceRecordCount', 'dashEvidenceCount');
    syncMirror('statRunning', 'dashRunningCount');
    syncMirror('pendingUpdateCount', 'navPending');
    syncMirror('sourceRecordCount', 'navEvidence');
  }

  function applyResearchMode() {
    const chooser = document.getElementById('researchContractors');
    const selection = document.querySelector('.research-selection');
    const mode = document.querySelector('input[name="deskResearchMode"]:checked')?.value || 'all';
    if (!chooser || !selection) return;
    selection.hidden = mode === 'all';
    if (mode === 'all') Array.from(chooser.options).forEach(option => { option.selected = true; });
  }

  async function refreshResearchSources() {
    const container = document.getElementById('deskResearchSources');
    if (!container) return;
    const [sources, dol, sam] = await Promise.all([
      request('/api/sources'),
      request('/api/integrations/dol'),
      request('/api/integrations/sam')
    ]);
    const checkedBefore = new Set(Array.from(container.querySelectorAll('input[data-research-source]:checked'), input => input.value));
    const firstRender = container.querySelector('.loading-state');
    container.innerHTML = sources.map(source => {
      let host = '';
      try { host = new URL(source.start_url).hostname.toLowerCase(); } catch {}
      const isDol = ['www.osha.gov','apiprod.dol.gov','api.dol.gov'].includes(host);
      const isSam = ['sam.gov','www.sam.gov','api.sam.gov','api-alpha.sam.gov'].includes(host);
      const running = ['queued','running'].includes(source.last_status);
      const missingKey = (isDol && !dol.configured) || (isSam && !sam.configured);
      const disabled = missingKey || running;
      const status = missingKey ? 'needs API key' : (running ? source.last_status : 'ready');
      const statusClass = missingKey ? 'needs-key' : (running ? 'running' : '');
      const shouldCheck = firstRender || checkedBefore.has(String(source.id));
      return `<label class="desk-research-source"><input type="checkbox" data-research-source value="${source.id}" ${shouldCheck ? 'checked' : ''} ${disabled ? 'disabled' : ''}><span><strong>${htmlEscape(source.name)}</strong><small>${htmlEscape(source.start_url)}</small></span><span class="desk-source-state ${statusClass}">${htmlEscape(status)}</span></label>`;
    }).join('') || '<div class="desk-evidence-empty">No research sources are configured.</div>';
    applyResearchMode();
  }

  function setResearchStatus(message, error = false) {
    const status = document.getElementById('deskResearchStatus');
    if (!status) return;
    status.textContent = message;
    status.style.color = error ? '#9a3f38' : '';
  }

  async function runResearch() {
    const runButton = document.getElementById('deskRunResearch');
    const chooser = document.getElementById('researchContractors');
    const mode = document.querySelector('input[name="deskResearchMode"]:checked')?.value || 'all';
    const sourceIds = Array.from(document.querySelectorAll('[data-research-source]:checked:not(:disabled)'), input => Number(input.value));
    if (!chooser) return;
    if (!sourceIds.length) {
      tell('Select at least one research source.', true);
      return;
    }
    if (mode === 'all') Array.from(chooser.options).forEach(option => { option.selected = true; });
    const masterIds = Array.from(chooser.selectedOptions, option => Number(option.value));
    if (!masterIds.length) {
      tell('Select at least one contractor.', true);
      return;
    }
    if (masterIds.length > 250) {
      tell('This proof of concept currently limits a research run to 250 contractors.', true);
      return;
    }
    const forceFull = Boolean(document.getElementById('deskBypassCache')?.checked);
    runButton.disabled = true;
    setResearchStatus(`Queuing ${sourceIds.length} source${sourceIds.length === 1 ? '' : 's'} for ${masterIds.length} contractor${masterIds.length === 1 ? '' : 's'}…`);
    let queued = 0;
    const errors = [];
    for (const sourceId of sourceIds) {
      try {
        await request(`/api/sources/${sourceId}/scan`, {method:'POST', body:JSON.stringify({force_full:forceFull, master_ids:masterIds})});
        queued += 1;
      } catch (error) {
        errors.push(error.message);
      }
    }
    runButton.disabled = false;
    if (queued) tell(`${queued} research collection${queued === 1 ? '' : 's'} queued. Follow progress in Activity.`);
    if (errors.length) {
      setResearchStatus(`${queued} queued; ${errors.length} could not start. ${errors[0]}`, true);
      tell(errors[0], true);
    } else {
      setResearchStatus(`${queued} collection${queued === 1 ? '' : 's'} queued. Research evidence will remain separate until Comparison review.`);
    }
    setTimeout(() => refreshResearchSources().catch(() => {}), 800);
  }

  function reviewRows() {
    return Array.from(document.querySelectorAll('#proposals tr')).filter(row => !row.querySelector('.table-empty'));
  }

  function updateReviewFilter() {
    const rows = reviewRows();
    const identityRows = rows.filter(row => row.classList.contains('proposal-ambiguous'));
    const changeRows = rows.filter(row => !row.classList.contains('proposal-ambiguous'));
    const changeCount = document.getElementById('deskChangeCount');
    const identityCount = document.getElementById('deskIdentityCount');
    if (changeCount) changeCount.textContent = `(${changeRows.length})`;
    if (identityCount) identityCount.textContent = `(${identityRows.length})`;
    rows.forEach(row => {
      row.hidden = reviewMode === 'identity' ? !row.classList.contains('proposal-ambiguous') : row.classList.contains('proposal-ambiguous');
    });
    const empty = document.getElementById('deskReviewEmpty');
    if (empty) {
      const visibleCount = reviewMode === 'identity' ? identityRows.length : changeRows.length;
      empty.hidden = visibleCount > 0 || rows.length === 0;
      empty.textContent = reviewMode === 'identity'
        ? 'No ambiguous identity matches are waiting for review.'
        : 'No field changes are waiting for approval.';
    }
  }

  function installProposalObserver() {
    const proposals = document.getElementById('proposals');
    if (!proposals) return;
    new MutationObserver(updateReviewFilter).observe(proposals, {childList:true, subtree:true});
    updateReviewFilter();
  }

  function parseExtra(record) {
    if (record.extra && typeof record.extra === 'object') return record.extra;
    try { return JSON.parse(record.extra_json || '{}'); } catch { return {}; }
  }

  function evidenceValueChips(record) {
    const fields = [
      ['DFI', 'dfi'], ['WC', 'wc'], ['OSHA', 'osha'], ['OSHA severe', 'osha_severe_violations'],
      ['Debarment', 'state_federal_debarment'], ['MN DOL', 'mndol_ineligibility'], ['Federal court', 'federal_court'],
      ['Circuit court', 'circuit_court'], ['CCAP', 'ccap_show150'], ['Environmental', 'environmental_violations'],
      ['Prevailing wage', 'prevailing_wage_violations'], ['DWD', 'dwd'], ['BBB complaints', 'better_business_bureau_complaints'],
      ['Misc.', 'misc_violations'], ['Tax liability', 'tax_liability']
    ];
    return fields.filter(([, key]) => record[key] !== undefined && record[key] !== null && String(record[key]).trim() !== '')
      .map(([label, key]) => `<span>${htmlEscape(label)}: ${htmlEscape(record[key])}</span>`).join('');
  }

  async function loadEvidence() {
    const list = document.getElementById('deskEvidenceList');
    const sourceSelect = document.getElementById('deskEvidenceSource');
    if (!list || !sourceSelect) return;
    if (!evidenceLoaded || sourceSelect.options.length <= 1) {
      const sources = await request('/api/sources');
      const current = sourceSelect.value;
      sourceSelect.innerHTML = '<option value="">All sources</option>' + sources.map(source => `<option value="${source.id}">${htmlEscape(source.name)}</option>`).join('');
      sourceSelect.value = current;
    }
    const query = document.getElementById('deskEvidenceSearch')?.value.trim() || '';
    const params = new URLSearchParams({limit:'200', offset:'0'});
    if (query) params.set('q', query);
    if (sourceSelect.value) params.set('source_id', sourceSelect.value);
    list.innerHTML = '<div class="loading-state">Loading source evidence…</div>';
    const data = await request('/api/records?' + params.toString());
    evidenceLoaded = true;
    if (!data.items?.length) {
      list.innerHTML = '<div class="desk-evidence-empty">No source evidence matches this view. Run research first or change the evidence filters.</div>';
      return;
    }
    list.innerHTML = data.items.map(record => {
      const extra = parseExtra(record);
      const contractor = record.company || record.contractor_name || extra.contractor_name || record.name || record.owner || `Research record #${record.id}`;
      const sourceName = record.source_name || extra.source_name || 'Research source';
      const match = extra.match_status || extra.match || extra.identity_status || extra.match_reason || '';
      const narrative = extra.narrative || record.osha_details || extra.summary || extra.note || '';
      const chips = evidenceValueChips(record);
      const url = safeHttpUrl(record.source_url || extra.source_url || '');
      const when = record.last_seen || record.first_seen || record.updated_at || '';
      return `<article class="desk-evidence-card"><h3>${htmlEscape(contractor)}</h3><div class="desk-evidence-meta"><span class="desk-evidence-badge">${htmlEscape(sourceName)}</span>${match ? `<span>Match: ${htmlEscape(match)}</span>` : ''}${when ? `<span>${htmlEscape(new Date(when).toLocaleString())}</span>` : ''}</div>${narrative ? `<div class="desk-evidence-summary">${htmlEscape(narrative)}</div>` : ''}${chips ? `<div class="desk-evidence-values">${chips}</div>` : ''}<div class="desk-evidence-actions"><button class="button subtle small" type="button" data-record="${record.id}">View evidence</button>${url !== '#' ? `<a class="text-button" href="${htmlEscape(url)}" target="_blank" rel="noreferrer">Open source ↗</a>` : ''}</div></article>`;
    }).join('');
  }

  function renderEvidenceError(error) {
    const list = document.getElementById('deskEvidenceList');
    if (list) list.innerHTML = `<div class="desk-evidence-empty">Could not load evidence: ${htmlEscape(error.message)}</div>`;
  }

  function installEvents() {
    document.addEventListener('click', event => {
      const openView = event.target.closest('[data-open-view]');
      if (openView) {
        event.preventDefault();
        switchView(openView.dataset.openView);
        return;
      }
      const review = event.target.closest('[data-review-mode]');
      if (review) {
        reviewMode = review.dataset.reviewMode;
        document.querySelectorAll('[data-review-mode]').forEach(button => button.classList.toggle('active', button === review));
        updateReviewFilter();
      }
    });

    document.getElementById('deskMenuButton')?.addEventListener('click', () => document.body.classList.toggle('nav-open'));
    document.querySelector('.desk-nav-backdrop')?.addEventListener('click', () => document.body.classList.remove('nav-open'));
    document.getElementById('toggleAllFields')?.addEventListener('click', event => {
      const records = document.getElementById('recordsSection');
      const expanded = records.classList.toggle('show-all-fields');
      event.currentTarget.textContent = expanded ? 'Compact view' : 'Show all 30 fields';
    });
    document.querySelectorAll('input[name="deskResearchMode"]').forEach(input => input.addEventListener('change', applyResearchMode));
    document.getElementById('deskRunResearch')?.addEventListener('click', () => runResearch().catch(error => {
      tell(error.message, true);
      setResearchStatus(error.message, true);
    }));
    document.getElementById('deskEvidenceForm')?.addEventListener('submit', event => {
      event.preventDefault();
      loadEvidence().catch(renderEvidenceError);
    });
    document.getElementById('deskEvidenceSource')?.addEventListener('change', () => loadEvidence().catch(renderEvidenceError));
    document.getElementById('deskOpenHelp')?.addEventListener('click', () => document.getElementById('helpDialog')?.showModal());
    document.getElementById('findRecords')?.addEventListener('click', () => switchView('database'));
    document.getElementById('reviewOsha')?.addEventListener('click', () => switchView('database'));

    window.addEventListener('hashchange', () => {
      const requested = location.hash.replace(/^#/, '');
      switchView(VALID_VIEWS.has(requested) ? requested : 'dashboard', false);
    });
    window.addEventListener('popstate', () => {
      const requested = location.hash.replace(/^#/, '');
      switchView(VALID_VIEWS.has(requested) ? requested : 'dashboard', false);
    });

    const chooser = document.getElementById('researchContractors');
    if (chooser) new MutationObserver(applyResearchMode).observe(chooser, {childList:true});
  }

  function install() {
    addShellStyles();
    document.body.classList.add('research-desk-shell');
    installNavigation();
    markExistingViews();
    const mainColumn = document.querySelector('.main-column');
    if (!mainColumn) return;
    installDashboard(mainColumn);
    installImportView(mainColumn);
    installDatabaseEnhancements();
    installResearchView(mainColumn);
    installComparisonEnhancements();
    installEvidenceView(mainColumn);
    installSourcesView();
    installSettingsView(mainColumn);
    installViewHeaders();
    installMirrors();
    installProposalObserver();
    installEvents();
    applyResearchMode();
    const requested = location.hash.replace(/^#/, '');
    switchView(VALID_VIEWS.has(requested) ? requested : 'dashboard', false);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, {once:true});
  else install();
})();
