'use strict';

const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const safeUrl = value => { try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : '#'; } catch { return '#'; } };
const PAGE_SIZE = 50;
const EVIDENCE_PAGE_SIZE = 25;
const VALID_VIEWS = new Set(['dashboard','database','import','research','comparison','evidence','sources','activity','settings']);
const VIEW_TITLES = {dashboard:'Dashboard',database:'Bidder database',import:'Import',research:'Research',comparison:'Comparison review',evidence:'Evidence',sources:'Sources',activity:'Activity',settings:'Settings'};
const OSHA_HOSTS = new Set(['www.osha.gov','apiprod.dol.gov','api.dol.gov']);
const SAM_HOSTS = new Set(['sam.gov','www.sam.gov','api.sam.gov','api-alpha.sam.gov']);
const BBB_HOSTS = new Set(['bbb.org','www.bbb.org']);
const OSHA_API_URL = 'https://apiprod.dol.gov/v4/get/OSHA/inspection/json';
const SAM_API_URL = 'https://api.sam.gov/entity-information/v4/exclusions';
const BBB_URL = 'https://www.bbb.org/search';
const BIDDER_COLUMNS = ['id','contractor_name','related_companies','address_1','city','state','zip','additional_address','additional_address_city','additional_address_state','additional_address_zip','dfi','wc','wc_date','osha_severe_violations','years','osha','state_federal_debarment','mndol_ineligibility','public_works_projects_budget_time_quality_complaint','federal_court','circuit_court','ccap_show150','environmental_violations','prevailing_wage_violations','dwd','dwd_substance_abuse_plan','better_business_bureau_complaints','misc_violations','tax_liability'];
const EVIDENCE_FIELDS = [['DFI','dfi'],['WC','wc'],['OSHA','osha'],['OSHA severe','osha_severe_violations'],['Years','years'],['Debarment','state_federal_debarment'],['MN DOL','mndol_ineligibility'],['Federal court','federal_court'],['Circuit court','circuit_court'],['CCAP','ccap_show150'],['Environmental','environmental_violations'],['Prevailing wage','prevailing_wage_violations'],['DWD','dwd'],['BBB complaints','better_business_bureau_complaints'],['Misc.','misc_violations'],['Tax liability','tax_liability']];

const state = {
  sources: [], catalog: [], jobs: [], events: [], cursor: 0, paused: false, follow: true, level: 'all',
  offset: 0, total: 0, evidenceOffset: 0, evidenceTotal: 0, refreshing: false, online: null,
  deleteId: null, editingId: null, bidderStatus: null, dolStatus: null, samStatus: null,
  proposals: [], reviewMode: 'changes', lastDialogTrigger: null, recordSequence: 0, detailSequence: 0,
  batchUpdating: false, researchSourceInitialized: false
};
let toastTimer;

function notify(message, error = false) {
  const toast = $('toast');
  if (!toast) return;
  toast.textContent = message;
  toast.className = 'toast' + (error ? ' error' : '');
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, 5000);
}

function localEvent(level, message, details = {}) {
  state.events.push({id:'local-' + Date.now() + '-' + Math.random().toString(36).slice(2), created_at:new Date().toISOString(), level, component:'browser', message, details});
  state.events = state.events.slice(-1000);
  renderLogs();
}

async function api(path, options = {}) {
  const {timeout = 20000, ...fetchOptions} = options;
  const headers = {...(fetchOptions.headers || {})};
  if (fetchOptions.body && !(fetchOptions.body instanceof Blob) && !(fetchOptions.body instanceof FormData) && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, {...fetchOptions, headers, signal:AbortSignal.timeout(timeout)});
  if (!response.ok) {
    let detail = `Request failed (${response.status}).`;
    try {
      const body = await response.json();
      detail = Array.isArray(body.detail) ? body.detail.map(item => item.msg || String(item)).join('; ') : body.detail || detail;
    } catch {}
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

function humanDate(value, short = false) {
  if (!value) return 'Not collected yet';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return short ? date.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}) : date.toLocaleString([], {month:'short', day:'numeric', year:'numeric', hour:'2-digit', minute:'2-digit'});
}

function setOnline(online) {
  if (state.online !== online) localEvent(online ? 'INFO' : 'WARNING', online ? 'Connected to the local research workspace.' : 'Connection lost. Saved activity remains visible while the app reconnects.');
  state.online = online;
  const health = $('health');
  if (!health) return;
  health.className = 'connection' + (online ? '' : ' offline');
  health.innerHTML = `<i></i><span>${online ? 'Workspace online' : 'Reconnecting'}</span>`;
}

function switchView(view, updateHash = true) {
  if (!VALID_VIEWS.has(view)) view = 'dashboard';
  document.body.dataset.activeView = view;
  document.querySelectorAll('[data-view-section]').forEach(section => { section.hidden = section.dataset.viewSection !== view; });
  document.querySelectorAll('[data-view-link]').forEach(link => {
    const active = link.dataset.viewLink === view;
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current','page'); else link.removeAttribute('aria-current');
  });
  $('deskPageTitle').textContent = VIEW_TITLES[view];
  document.body.classList.remove('nav-open');
  $('deskMenuButton').setAttribute('aria-expanded','false');
  if (updateHash && location.hash !== `#${view}`) history.pushState(null, '', `#${view}`);
  if (view === 'evidence') loadEvidence().catch(renderEvidenceError);
  if (view === 'research') renderResearchSources();
  requestAnimationFrame(() => $('mainContent')?.focus({preventScroll:true}));
  window.scrollTo({top:0, behavior:'instant'});
}

function showDialog(id, trigger = null, focusSelector = null) {
  const dialog = $(id);
  if (!dialog) return;
  state.lastDialogTrigger = trigger || document.activeElement;
  dialog.showModal();
  requestAnimationFrame(() => {
    const focusTarget = focusSelector ? dialog.querySelector(focusSelector) : dialog.querySelector('input, select, button, [href]');
    focusTarget?.focus();
  });
}

function closeDialog(id) { const dialog = $(id); if (dialog?.open) dialog.close(); }

document.querySelectorAll('dialog').forEach(dialog => {
  dialog.addEventListener('close', () => {
    const target = state.lastDialogTrigger;
    state.lastDialogTrigger = null;
    if (target && document.contains(target) && typeof target.focus === 'function') target.focus();
  });
});

function isCollecting(source) { return ['queued','running'].includes(String(source.last_status || '').toLowerCase()); }
function hostOf(source) { try { return new URL(source.start_url).hostname.toLowerCase(); } catch { return ''; } }
function isOshaSource(source) { return OSHA_HOSTS.has(hostOf(source)); }
function isSamSource(source) { return SAM_HOSTS.has(hostOf(source)); }
function isBbbSource(source) { return BBB_HOSTS.has(hostOf(source)); }
function sourceReady(source) {
  return !isCollecting(source)
    && (!isOshaSource(source) || Boolean(state.dolStatus?.configured))
    && (!isSamSource(source) || Boolean(state.samStatus?.configured));
}

function catalogForSource(source) {
  const host = hostOf(source);
  return state.catalog.find(item => (item.hosts || []).includes(host)) || state.catalog.find(item => item.name === source.name) || null;
}

function sourceDisplayName(source) {
  if (isOshaSource(source)) return 'OSHA / DOL Enforcement API';
  if (isSamSource(source)) return 'SAM.gov Federal Debarment / Exclusions';
  if (isBbbSource(source)) return 'BBB Business Profiles / Complaints';
  return source.name;
}

function sourceDisplayUrl(source) {
  if (isOshaSource(source)) return OSHA_API_URL;
  if (isSamSource(source)) return SAM_API_URL;
  if (isBbbSource(source)) return BBB_URL;
  return source.start_url;
}

function sourceReadiness(source) {
  if (isOshaSource(source) && !state.dolStatus?.configured) return {label:'Needs API key', cls:'needs-key', ready:false};
  if (isSamSource(source) && !state.samStatus?.configured) return {label:'Needs API key', cls:'needs-key', ready:false};
  if (isCollecting(source)) return {label:source.last_status, cls:String(source.last_status).toLowerCase(), ready:false};
  const status = String(source.last_status || '').toLowerCase();
  if (['partial','failed','error','blocked','interrupted','cancelled'].includes(status)) return {label:source.last_status, cls:status, ready:true};
  return {label:'Ready', cls:'', ready:true};
}

function catalogJurisdiction(item) {
  const key = item.key || '';
  if (key === 'sam') return 'Federal / nationwide exclusions';
  if (key === 'osha' || key === 'dol') return 'Federal DOL / nationwide';
  if (key === 'state_mn') return 'Minnesota';
  if (key === 'state_il') return 'Illinois';
  if (key === 'state_wi') return 'Wisconsin';
  if (key === 'bbb') return 'FL, IL, MN, MO, OH, WI profile coverage in current POC';
  if (key === 'pacer') return 'Federal courts; excluded from current POC';
  return 'See source limitation';
}

function catalogCredential(item) {
  if (item.key === 'osha') return 'DOL Open Data API key';
  if (item.key === 'sam') return 'SAM.gov Public API key';
  return 'None stored by this integration';
}

function renderSourceCatalog() {
  const container = $('sourceCatalog');
  if (!container) return;
  if (!state.catalog.length) {
    container.innerHTML = '<div class="empty-state">No source catalog is available.</div>';
    return;
  }
  container.innerHTML = state.catalog.map(item => `<article class="catalog-card"><h3>${esc(item.name)}</h3><div class="catalog-meta"><span class="tag ${esc(String(item.status || '').toLowerCase().replace(/\s+/g,'-'))}">${esc(item.status || 'Status unknown')}</span><span class="tag">${esc(item.method || 'Method unknown')}</span></div><dl><div><dt>Fields owned</dt><dd>${esc((item.fields || []).join(', ') || 'No direct field writes')}</dd></div><div><dt>Coverage</dt><dd>${esc(catalogJurisdiction(item))}</dd></div><div><dt>Credential</dt><dd>${esc(catalogCredential(item))}</dd></div><div><dt>Limitations</dt><dd>${esc(item.note || 'None documented')}</dd></div></dl></article>`).join('');
}

function renderConfiguredSources() {
  const container = $('sources');
  if (!container) return;
  if (!state.sources.length) {
    container.innerHTML = '<div class="empty-state">No research sources are configured.</div>';
    return;
  }
  container.innerHTML = `<div class="source-list">${state.sources.map(source => {
    const readiness = sourceReadiness(source);
    const running = isCollecting(source);
    const name = sourceDisplayName(source);
    const url = sourceDisplayUrl(source);
    const builtInKeyButton = isOshaSource(source) ? '<button class="settings-button" type="button" data-dol-key>Set API key</button>' : isSamSource(source) ? '<button class="settings-button" type="button" data-sam-key>Set API key</button>' : '';
    const collectLabel = isOshaSource(source) ? 'Collect OSHA' : isSamSource(source) ? 'Collect federal debarment' : isBbbSource(source) ? 'Collect BBB complaints' : 'Collect records';
    const edit = (!isOshaSource(source) && !isSamSource(source) && !isBbbSource(source)) ? `<button class="settings-button" type="button" data-edit="${source.id}">Edit settings</button>` : '';
    const remove = (!isOshaSource(source) && !isSamSource(source) && !isBbbSource(source)) ? `<button class="icon-button" type="button" data-delete="${source.id}" aria-label="Delete ${esc(name)}" title="Remove research site">×</button>` : '';
    return `<article class="source-row"><div class="source-info"><h3>${esc(name)}</h3><a class="source-url" href="${esc(safeUrl(url))}" target="_blank" rel="noreferrer">${esc(url)}</a><div class="source-details"><span class="tag ${esc(readiness.cls)}">${esc(readiness.label)}</span><span>${esc(catalogForSource(source)?.method || (source.auto_scan ? `Every ${source.interval_minutes} min` : 'Manual collection'))}</span><span>Last: ${esc(humanDate(source.last_scan_at))}</span></div></div><div class="source-controls">${builtInKeyButton}<button class="scan-button" type="button" data-scan="${source.id}" ${(!readiness.ready || running) ? 'disabled' : ''}>${running ? 'Collecting…' : collectLabel}</button><button class="icon-button" type="button" data-full="${source.id}" aria-label="Recollect ${esc(name)} ignoring cache" ${(!readiness.ready || running) ? 'disabled' : ''}>↻</button>${edit}${remove}</div></article>`;
  }).join('')}</div>`;
}

function renderResearchSources() {
  const container = $('deskResearchSources');
  if (!container) return;
  const checkedBefore = new Set(Array.from(container.querySelectorAll('[data-research-source]:checked'), input => input.value));
  const hadChoices = container.querySelector('[data-research-source]');
  if (!state.sources.length) {
    container.innerHTML = '<div class="empty-state">No research sources are configured.</div>';
    return;
  }
  container.innerHTML = state.sources.map(source => {
    const readiness = sourceReadiness(source);
    const catalog = catalogForSource(source);
    const checked = hadChoices ? checkedBefore.has(String(source.id)) : readiness.ready;
    return `<label class="research-source-option"><input type="checkbox" data-research-source value="${source.id}" ${checked ? 'checked' : ''} ${readiness.ready ? '' : 'disabled'}><span><strong>${esc(sourceDisplayName(source))}</strong><small>${esc((catalog?.fields || []).join(', ') || 'Evidence-only / no direct field ownership documented')}</small><small>${esc(catalog?.note || source.start_url)}</small></span><span class="source-state ${esc(readiness.cls)}">${esc(readiness.label)}</span></label>`;
  }).join('');
  state.researchSourceInitialized = true;
}

function applyResearchMode() {
  const mode = document.querySelector('input[name="deskResearchMode"]:checked')?.value || 'all';
  const selection = $('researchSelection');
  const chooser = $('researchContractors');
  if (!selection || !chooser) return;
  selection.hidden = mode === 'all';
  if (mode === 'all') Array.from(chooser.options).forEach(option => { option.selected = true; });
}

function selectedResearchIds() {
  const chooser = $('researchContractors');
  const mode = document.querySelector('input[name="deskResearchMode"]:checked')?.value || 'all';
  if (mode === 'all') Array.from(chooser.options).forEach(option => { option.selected = true; });
  const ids = Array.from(chooser.selectedOptions, option => Number(option.value));
  if (!ids.length) throw new Error('Select at least one approved contractor before collecting.');
  if (ids.length > 250) throw new Error('Select at most 250 contractors per proof-of-concept run.');
  return ids;
}

async function loadSources() {
  const [sources, dolStatus, samStatus, catalog, contractors] = await Promise.all([
    api('/api/sources'), api('/api/integrations/dol'), api('/api/integrations/sam'), api('/api/source-catalog'), api('/api/bidder/master?limit=1000')
  ]);
  state.sources = sources;
  state.dolStatus = dolStatus;
  state.samStatus = samStatus;
  state.catalog = catalog.sources || [];
  $('oshaApiKeyButton').textContent = dolStatus.configured ? 'Change OSHA API key' : 'Set OSHA API key';
  $('samApiKeyButton').textContent = samStatus.configured ? 'Change SAM API key' : 'Set SAM API key';
  $('sourceCount').textContent = `${sources.length} collectors`;
  $('navSources').textContent = String(sources.length);
  const chooser = $('researchContractors');
  const selected = new Set(Array.from(chooser.selectedOptions, option => option.value));
  chooser.innerHTML = contractors.items.map(record => `<option value="${record._master_id}" ${selected.has(String(record._master_id)) ? 'selected' : ''}>${esc(`${record.contractor_name} — ${record.city || ''}${record.city ? ', ' : ''}${record.state || ''}`)}</option>`).join('');
  if ((document.querySelector('input[name="deskResearchMode"]:checked')?.value || 'all') === 'all') Array.from(chooser.options).forEach(option => { option.selected = true; });
  $('researchSelectionNote').textContent = contractors.total ? (contractors.total > contractors.items.length ? `Showing the first ${contractors.items.length.toLocaleString()} of ${contractors.total.toLocaleString()} approved contractors.` : `${contractors.total.toLocaleString()} approved contractors available for targeted research.`) : 'Import a master CSV to choose contractors.';
  renderSourceCatalog();
  renderConfiguredSources();
  renderResearchSources();
  updateCollectionButton();
  applyResearchMode();
  populateEvidenceSources();
}

function updateCollectionButton() {
  const button = $('updateSites');
  if (!button) return;
  const available = state.sources.filter(sourceReady).length;
  button.disabled = state.batchUpdating || available === 0;
  button.textContent = state.batchUpdating ? 'Queuing collections…' : 'Update ready sources';
}

async function loadStats() {
  const stats = await api('/api/stats');
  $('dashRunningCount').textContent = Number(stats.running_jobs || 0).toLocaleString();
  if (!state.bidderStatus) await loadBidderStatus();
}

async function loadBidderStatus() {
  const status = await api('/api/bidder/status');
  state.bidderStatus = status;
  const master = Number(status.master_total || 0);
  const evidence = Number(status.active_source_records || 0);
  const pending = Number(status.pending_updates || 0);
  $('masterCount').textContent = master.toLocaleString();
  $('sourceRecordCount').textContent = evidence.toLocaleString();
  $('pendingUpdateCount').textContent = pending.toLocaleString();
  $('comparisonMasterCount').textContent = master.toLocaleString();
  $('comparisonEvidenceCount').textContent = evidence.toLocaleString();
  $('comparisonPendingCount').textContent = pending.toLocaleString();
  $('dashMasterCount').textContent = master.toLocaleString();
  $('dashEvidenceCount').textContent = evidence.toLocaleString();
  $('dashPendingCount').textContent = pending.toLocaleString();
  $('navEvidence').textContent = evidence.toLocaleString();
  $('navPending').textContent = pending.toLocaleString();
  $('lastImport').textContent = status.latest_import ? new Date(status.latest_import.imported_at).toLocaleDateString() : 'Never';
  $('baselineStatus').textContent = status.latest_import ? `${master.toLocaleString()} approved contractors loaded from ${status.latest_import.filename}. Outside-source findings remain separate until review.` : 'Upload the existing bidder CSV to use it as the approved master starting database.';
  $('masterImportSummary').textContent = status.latest_import ? `${master.toLocaleString()} approved contractors are currently loaded from ${status.latest_import.filename}. Importing another CSV refreshes matching baseline rows without treating missing contractors as automatic deletions.` : 'Upload the existing 30-column bidder CSV to establish the approved master.';
  $('uploadCsvButton').textContent = status.latest_import ? 'Import another CSV' : 'Upload master CSV';
  $('compareButton').disabled = master === 0 || evidence === 0;
  renderNextActions();
  return status;
}

function renderNextActions() {
  const status = state.bidderStatus;
  if (!status) return;
  const actions = [];
  if (!status.master_total) actions.push(['import','Import the approved master CSV','Research is intentionally blocked until contractors exist in the master.']);
  else if (!status.active_source_records) actions.push(['research','Run targeted research','Collect evidence for approved contractors without editing the master.']);
  if (status.active_source_records) actions.push(['comparison','Compare research with the master','Generate field-level proposals from retained evidence.']);
  if (status.pending_updates) actions.unshift(['comparison',`Review ${status.pending_updates} pending decision${status.pending_updates === 1 ? '' : 's'}`,'Approve or dismiss each proposed field change.']);
  actions.push(['database','Review or export approved data','Exports contain only current approved master values.']);
  $('nextActions').innerHTML = actions.slice(0,3).map(([view,title,copy], index) => `<button type="button" data-open-view="${view}"><strong>${index + 1}. ${esc(title)}</strong><span>${esc(copy)}</span></button>`).join('');
}

async function importBidderCsv(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.csv')) { notify('Please choose a CSV file.', true); return; }
  const button = $('uploadCsvButton');
  button.disabled = true;
  $('importResult').className = 'result-panel';
  $('importResult').textContent = `Importing ${file.name}…`;
  try {
    const response = await fetch('/api/bidder/import', {method:'POST', headers:{'Content-Type':'text/csv','X-Filename':file.name}, body:file, signal:AbortSignal.timeout(60000)});
    if (!response.ok) {
      let detail = 'CSV import failed.';
      try { detail = (await response.json()).detail || detail; } catch {}
      throw new Error(detail);
    }
    const result = await response.json();
    const summary = `Imported ${result.rows_total} rows: ${result.rows_inserted} new, ${result.rows_updated} updated, ${result.rows_unchanged} unchanged.`;
    $('importResult').textContent = summary + (result.warnings?.length ? ` Warnings: ${result.warnings.join(' ')}` : ' Evidence remains separate from the approved baseline.');
    notify(summary);
    localEvent('INFO', `Bidder CSV imported: ${file.name}`);
    state.offset = 0;
    state.evidenceOffset = 0;
    await Promise.all([loadBidderStatus(), loadRecords(), loadProposals(), loadSources()]);
  } catch (error) {
    $('importResult').className = 'result-panel error';
    $('importResult').textContent = `Import failed: ${error.message}`;
    notify(error.message, true);
    localEvent('ERROR', `Bidder CSV import failed: ${error.message}`);
    await loadBidderStatus().catch(() => {});
  } finally {
    button.disabled = false;
    $('csvUpload').value = '';
  }
}

function recordParams(extra = {}) { return new URLSearchParams({...extra, q:$('search').value.trim(), field:$('fieldFilter').value}); }

async function loadRecords() {
  const sequence = ++state.recordSequence;
  const params = recordParams({limit:PAGE_SIZE, offset:state.offset});
  const data = await api('/api/bidder/master?' + params);
  if (sequence !== state.recordSequence) return;
  state.total = data.total;
  if (state.offset && state.offset >= data.total) { state.offset = 0; return loadRecords(); }
  const filtered = params.get('q') || $('fieldFilter').value !== 'all';
  $('recordCount').textContent = `${data.total.toLocaleString()} approved bidder record${data.total === 1 ? '' : 's'}${filtered ? ' matching these filters.' : ' in the master database.'}`;
  $('pageInfo').textContent = data.total ? `${state.offset + 1}–${state.offset + data.items.length} of ${data.total.toLocaleString()}` : 'No matching approved bidder records';
  $('prevPage').disabled = state.offset === 0;
  $('nextPage').disabled = state.offset + PAGE_SIZE >= data.total;
  $('records').innerHTML = data.items.length ? data.items.map(record => `<tr>${BIDDER_COLUMNS.map(column => `<td>${column === 'contractor_name' ? `<strong>${esc(record[column] || '—')}</strong>` : esc(record[column] === '' || record[column] == null ? '—' : record[column])}</td>`).join('')}</tr>`).join('') : `<tr><td colspan="${BIDDER_COLUMNS.length}" class="table-empty"><strong>${filtered ? 'No approved bidder records match these filters.' : 'No master bidder database has been imported yet.'}</strong></td></tr>`;
}

function proposalBucket(proposal) {
  if (proposal.status !== 'pending') return 'resolved';
  if (['ambiguous','new_record'].includes(proposal.proposal_type)) return 'identity';
  return 'changes';
}

function proposalContractor(proposal) { return proposal.master_contractor || proposal.proposed?.contractor_name || 'Unknown contractor'; }

function proposalSource(proposal) {
  const label = proposal.source_name || 'Saved source record';
  return proposal.source_url ? `<a href="${esc(safeUrl(proposal.source_url))}" target="_blank" rel="noreferrer">${esc(label)} ↗</a>` : esc(label);
}

function proposalContext(proposal) {
  const proposed = proposal.proposed || {};
  const candidates = [proposal.match_reason, proposal.reason, proposal.context, proposed.match_reason, proposed.match_basis].filter(Boolean);
  if (proposal.proposal_type === 'new_record') candidates.push('Outside-source research cannot add a contractor to the approved master. Review the identity and dismiss this research-only proposal.');
  if (proposal.proposal_type === 'ambiguous') candidates.push('The source record could not be conservatively tied to one approved contractor. No master field can be updated from this match.');
  return candidates.join(' · ') || 'Source-backed field difference retained for review.';
}

function renderProposals() {
  const bucket = state.reviewMode;
  const rows = state.proposals.filter(proposal => proposalBucket(proposal) === bucket);
  const pending = state.proposals.filter(proposal => proposal.status === 'pending');
  const changes = pending.filter(proposal => proposalBucket(proposal) === 'changes');
  const identity = pending.filter(proposal => proposalBucket(proposal) === 'identity');
  const resolved = state.proposals.filter(proposal => proposalBucket(proposal) === 'resolved');
  $('deskChangeCount').textContent = `(${changes.length})`;
  $('deskIdentityCount').textContent = `(${identity.length})`;
  $('deskResolvedCount').textContent = `(${resolved.length})`;
  $('proposalStatus').textContent = pending.length ? `${pending.length} proposed decision${pending.length === 1 ? '' : 's'} waiting for review.` : 'No proposed updates waiting for review.';
  $('comparisonPendingCount').textContent = pending.length.toLocaleString();
  $('pendingUpdateCount').textContent = pending.length.toLocaleString();
  $('dashPendingCount').textContent = pending.length.toLocaleString();
  $('navPending').textContent = pending.length.toLocaleString();
  const empty = $('deskReviewEmpty');
  empty.hidden = rows.length > 0;
  empty.textContent = bucket === 'identity' ? 'No ambiguous or outside-master identities are waiting for review.' : bucket === 'resolved' ? 'No resolved comparison decisions are available yet.' : 'No field changes are waiting for approval.';
  if (!rows.length) {
    $('proposals').innerHTML = '<tr><td colspan="7" class="table-empty">Nothing to show in this review view.</td></tr>';
    return;
  }
  $('proposals').innerHTML = rows.map(proposal => {
    const contractor = proposalContractor(proposal);
    const source = proposalSource(proposal);
    const context = proposalContext(proposal);
    let field = proposal.field_name || proposal.proposal_type || 'Research proposal';
    let oldValue = proposal.old_value || 'blank / unknown';
    let newValue = proposal.new_value || 'blank / unknown';
    let decision = '';
    let rowClass = '';
    if (proposal.proposal_type === 'new_record') {
      rowClass = 'proposal-new';
      field = 'OUTSIDE MASTER';
      oldValue = 'Contractor is not in approved master';
      newValue = [proposal.proposed?.address_1, proposal.proposed?.city, proposal.proposed?.state, proposal.proposed?.zip].filter(Boolean).join(', ') || 'Research-only contractor identity';
      decision = proposal.status === 'pending' ? `<div class="decision-actions"><span class="resolved-badge dismissed">Cannot add from research</span><button class="text-button" type="button" data-dismiss-proposal="${proposal.id}">Dismiss</button></div>` : `<span class="resolved-badge ${esc(proposal.status)}">${esc(proposal.status)}</span>`;
    } else if (proposal.proposal_type === 'ambiguous') {
      rowClass = 'proposal-ambiguous';
      field = 'AMBIGUOUS IDENTITY';
      oldValue = 'No single approved contractor confirmed';
      newValue = 'Manual identity review required';
      decision = proposal.status === 'pending' ? `<div class="decision-actions"><span class="resolved-badge dismissed">No master edit allowed</span><button class="text-button" type="button" data-dismiss-proposal="${proposal.id}">Dismiss</button></div>` : `<span class="resolved-badge ${esc(proposal.status)}">${esc(proposal.status)}</span>`;
    } else if (proposal.status === 'pending') {
      decision = `<div class="decision-actions"><button class="button primary tiny" type="button" data-apply-proposal="${proposal.id}">Approve change</button><button class="text-button" type="button" data-dismiss-proposal="${proposal.id}">Keep approved value</button></div>`;
    } else {
      decision = `<span class="resolved-badge ${esc(proposal.status)}">${esc(proposal.status)}</span>`;
    }
    return `<tr class="${rowClass}"><td data-label="Contractor"><strong>${esc(contractor)}</strong></td><td data-label="Field"><span class="change-field">${esc(field)}</span></td><td data-label="Current approved"><span class="value-box old-value">${esc(oldValue)}</span></td><td data-label="New research"><span class="value-box new-value">${esc(newValue)}</span></td><td data-label="Source">${source}</td><td data-label="Evidence / context">${esc(context)}</td><td data-label="Decision">${decision}</td></tr>`;
  }).join('');
}

async function loadProposals() {
  state.proposals = await api('/api/bidder/proposals?status=all&limit=1000');
  renderProposals();
}

async function compareBidderData() {
  const button = $('compareButton');
  button.disabled = true;
  $('comparisonState').textContent = 'Comparing…';
  $('proposalStatus').textContent = 'Comparing retained evidence with the approved master…';
  try {
    const result = await api('/api/bidder/compare', {method:'POST'});
    $('comparisonState').textContent = 'Complete';
    notify(`Comparison complete: ${result.field_changes} field changes and ${result.ambiguous_matches} ambiguous matches.`);
    await Promise.all([loadBidderStatus(), loadProposals()]);
  } catch (error) {
    $('comparisonState').textContent = 'Error';
    notify(error.message, true);
    localEvent('ERROR', `Bidder comparison failed: ${error.message}`);
  } finally {
    const status = await loadBidderStatus().catch(() => state.bidderStatus);
    button.disabled = !status || status.master_total === 0 || status.active_source_records === 0;
  }
}

async function applyProposal(id, button) {
  button.disabled = true;
  try {
    await api(`/api/bidder/proposals/${id}/apply`, {method:'POST'});
    notify('Approved field change applied to the master bidder database.');
    await Promise.all([loadBidderStatus(), loadProposals(), loadRecords()]);
  } catch (error) { notify(error.message, true); }
  finally { button.disabled = false; }
}

async function dismissProposal(id, button) {
  button.disabled = true;
  try {
    await api(`/api/bidder/proposals/${id}/dismiss`, {method:'POST'});
    notify('Research proposal dismissed. The approved master value was left unchanged.');
    await Promise.all([loadBidderStatus(), loadProposals()]);
  } catch (error) { notify(error.message, true); }
  finally { button.disabled = false; }
}

function parseExtra(record) {
  if (record.extra && typeof record.extra === 'object') return record.extra;
  try { return JSON.parse(record.extra_json || '{}'); } catch { return {}; }
}

function evidenceState(record, extra) {
  const explicit = extra.collection_state || extra.result_state || extra.source_state || extra.status;
  if (explicit) return String(explicit);
  if (extra.complete === false || extra.incomplete === true) return 'incomplete';
  const match = String(extra.match_status || extra.identity_status || '').toLowerCase();
  if (match.includes('ambig')) return 'ambiguous';
  if (record.active === false) return 'inactive';
  return 'complete';
}

function evidenceMatchBasis(extra) { return extra.match_basis || extra.match_reason || extra.identity_reason || extra.match || extra.match_status || extra.identity_status || ''; }
function evidenceNarrative(record, extra) { return extra.narrative || extra.summary || extra.note || record.osha_details || ''; }
function evidenceValueChips(record, extra) {
  const values = {...extra, ...record};
  return EVIDENCE_FIELDS.filter(([,key]) => values[key] !== undefined && values[key] !== null && String(values[key]).trim() !== '').map(([label,key]) => `<span>${esc(label)}: ${esc(values[key])}</span>`).join('');
}

function populateEvidenceSources() {
  const select = $('deskEvidenceSource');
  if (!select) return;
  const current = select.value;
  select.innerHTML = '<option value="">All sources</option>' + state.sources.map(source => `<option value="${source.id}">${esc(sourceDisplayName(source))}</option>`).join('');
  if (state.sources.some(source => String(source.id) === current)) select.value = current;
}

async function loadEvidence() {
  const list = $('deskEvidenceList');
  if (!list) return;
  populateEvidenceSources();
  const params = new URLSearchParams({limit:String(EVIDENCE_PAGE_SIZE), offset:String(state.evidenceOffset)});
  const query = $('deskEvidenceSearch').value.trim();
  if (query) params.set('q', query);
  if ($('deskEvidenceSource').value) params.set('source_id', $('deskEvidenceSource').value);
  list.innerHTML = '<div class="loading-state">Loading source evidence…</div>';
  const data = await api('/api/records?' + params.toString());
  state.evidenceTotal = Number(data.total || 0);
  if (state.evidenceOffset && state.evidenceOffset >= state.evidenceTotal) { state.evidenceOffset = 0; return loadEvidence(); }
  $('evidencePrevPage').disabled = state.evidenceOffset === 0;
  $('evidenceNextPage').disabled = state.evidenceOffset + EVIDENCE_PAGE_SIZE >= state.evidenceTotal;
  $('evidencePageInfo').textContent = state.evidenceTotal ? `${state.evidenceOffset + 1}–${state.evidenceOffset + data.items.length} of ${state.evidenceTotal.toLocaleString()} evidence records` : 'No matching evidence records';
  if (!data.items.length) {
    list.innerHTML = '<div class="empty-state">No source evidence matches this view. Run research first or change the evidence filters.</div>';
    return;
  }
  list.innerHTML = data.items.map(record => {
    const extra = parseExtra(record);
    const contractor = record.company || record.contractor_name || extra.contractor_name || record.name || record.owner || `Research record #${record.id}`;
    const sourceName = record.source_name || extra.source_name || 'Research source';
    const match = evidenceMatchBasis(extra);
    const stateLabel = evidenceState(record, extra);
    const narrative = evidenceNarrative(record, extra);
    const chips = evidenceValueChips(record, extra);
    const url = safeUrl(record.source_url || extra.source_url || '');
    const when = record.last_seen || record.first_seen || record.updated_at || extra.retrieved_at || '';
    return `<article class="evidence-card-item"><h3>${esc(contractor)}</h3><div class="evidence-meta"><span class="evidence-badge">${esc(sourceName)}</span><span class="evidence-state ${esc(stateLabel.toLowerCase())}">${esc(stateLabel)}</span>${when ? `<span>Retrieved ${esc(humanDate(when))}</span>` : ''}${match ? `<span>Match basis: ${esc(match)}</span>` : '<span>Match basis: not supplied</span>'}</div>${narrative ? `<div class="evidence-context">${esc(narrative)}</div>` : '<div class="evidence-context">No additional source narrative was supplied.</div>'}${chips ? `<div class="evidence-values" aria-label="Extracted proposed fields">${chips}</div>` : '<div class="evidence-context">No bidder-field values were extracted from this record.</div>'}<div class="evidence-actions"><button class="button subtle small" type="button" data-record="${record.id}">View detailed evidence</button>${url !== '#' ? `<a class="text-button" href="${esc(url)}" target="_blank" rel="noreferrer">Open source ↗</a>` : ''}</div></article>`;
  }).join('');
}

function renderEvidenceError(error) {
  $('deskEvidenceList').innerHTML = `<div class="empty-state">Could not load evidence: ${esc(error.message)}</div>`;
}

async function viewRecord(id, trigger = null) {
  const sequence = ++state.detailSequence;
  $('recordDialogTitle').textContent = 'Research record';
  $('recordDetail').innerHTML = '<p class="loading-state">Loading saved evidence and provenance…</p>';
  showDialog('recordDialog', trigger);
  try {
    const record = await api(`/api/records/${id}`);
    if (sequence !== state.detailSequence) return;
    const bidder = record.bidder || {};
    const extra = parseExtra(record);
    const contractor = bidder.contractor_name || record.company || record.name || record.owner || `Research record #${id}`;
    $('recordDialogTitle').textContent = contractor;
    const field = (label,value) => `<div><dt>${esc(label)}</dt><dd>${esc(value || 'Not reported')}</dd></div>`;
    const bidderFields = BIDDER_COLUMNS.map(column => field(column, bidder[column])).join('');
    const collectedFrom = typeof extra.collected_from_url === 'string' ? extra.collected_from_url : '';
    const stateLabel = evidenceState(record, extra);
    const match = evidenceMatchBasis(extra) || 'Not supplied';
    $('recordDetail').innerHTML = `<p class="muted">Evidence record #${record.id} · ${esc(record.source_name || 'Research source')}</p><section class="record-osha"><h3>Evidence context</h3><dl class="record-fields">${field('Collection state',stateLabel)}${field('Match basis',match)}${field('Person / contact',record.name)}${field('Owner',record.owner)}${field('Source record ID',record.external_id)}</dl><p>${esc(evidenceNarrative(record, extra) || 'No additional source narrative was collected for this record.')}</p></section><section class="record-bidder"><h3>Extracted bidder fields</h3><dl class="record-fields">${bidderFields}</dl></section><section class="record-provenance"><h3>Source and retrieval provenance</h3>${record.source_url ? `<a class="evidence-source" href="${esc(safeUrl(record.source_url))}" target="_blank" rel="noreferrer">Open source record ↗<small>${esc(record.source_url)}</small></a>` : ''}${collectedFrom && collectedFrom !== record.source_url ? `<a class="evidence-source" href="${esc(safeUrl(collectedFrom))}" target="_blank" rel="noreferrer">Open collection page ↗<small>${esc(collectedFrom)}</small></a>` : ''}<dl class="record-fields">${field('First retrieved',record.first_seen ? humanDate(record.first_seen) : '')}${field('Last retrieved',record.last_seen ? humanDate(record.last_seen) : '')}${field('Last changed locally',record.last_changed ? humanDate(record.last_changed) : '')}${field('Active evidence',record.active === false ? 'Not seen in a later complete collection' : 'Retained as present')}</dl></section>`;
  } catch (error) {
    if (sequence === state.detailSequence) $('recordDetail').innerHTML = `<p class="form-message">${esc(error.message)}</p>`;
  }
}

function problemStatuses() { return new Set(['partial','failed','error','blocked','interrupted','cancelled','incomplete']); }

function renderDashboardProblems() {
  const problems = state.jobs.filter(job => problemStatuses().has(String(job.status || '').toLowerCase()));
  $('dashProblemCount').textContent = problems.length.toLocaleString();
  $('dashboardProblems').innerHTML = problems.length ? problems.slice(0,5).map(job => `<div class="compact-item"><strong>${esc(job.source_name || 'Research job')}</strong><span class="tag ${esc(String(job.status).toLowerCase())}">${esc(job.status)}</span><small>${esc(job.message || 'No diagnostic message supplied')} · ${esc(humanDate(job.started_at))}</small></div>`).join('') : '<div class="empty-state">No recent partial or failed research jobs.</div>';
}

function failureSummary(job) {
  const failure = job.failure || {};
  if (!failure.stage && !failure.category) return '';
  const retry = failure.retryable ? `retryable; attempt ${failure.attempt_no || '?'}` : 'not retryable';
  const status = failure.upstream_status ? `HTTP ${failure.upstream_status}` : 'no HTTP status';
  return `${failure.source_name || job.source_name || 'Research source'} · ${failure.stage || 'unknown stage'} · ${status} · ${failure.category || 'internal_error'} · ${retry} · ${failure.message || 'Collection failed'}`;
}

async function loadJobs() {
  const jobs = await api('/api/jobs?limit=20');
  state.jobs = jobs;
  const running = jobs.filter(job => ['queued','running'].includes(String(job.status || '').toLowerCase())).length;
  $('dashRunningCount').textContent = running.toLocaleString();
  renderDashboardProblems();
  $('jobs').innerHTML = jobs.length ? jobs.map(job => `<article class="job"><div class="job-head"><strong>${esc(job.source_name || 'Research source')}</strong><span class="tag ${esc(String(job.status || '').toLowerCase())}">${esc(job.status || 'unknown')}</span></div><span class="job-time">${esc(humanDate(job.started_at))}</span><p class="job-message">${esc(failureSummary(job) || job.message || 'No status message supplied.')}</p><div class="job-stats"><span><b>${job.pages_processed ?? 0}/${job.pages_discovered ?? 0}</b> pages</span><span><b>${job.records_found ?? 0}</b> found</span><span><b>${job.records_new ?? 0}</b> new</span><span><b>${job.records_updated ?? 0}</b> updated</span><span><b>${job.errors ?? 0}</b> errors</span></div></article>`).join('') : '<div class="empty-state">No research jobs have run yet.</div>';
  if (document.body.dataset.activeView === 'research' && running) $('deskResearchStatus').textContent = `${running} research job${running === 1 ? '' : 's'} currently running. Evidence remains separate until comparison review.`;
}

async function loadLogs() {
  if (state.paused) return;
  const data = await api(`/api/activity?after=${state.cursor}&limit=500`);
  if (data.reset) { state.cursor = 0; state.events = []; return; }
  if (data.items.length) {
    const ids = new Set(state.events.map(item => item.id));
    state.events.push(...data.items.filter(item => !ids.has(item.id)));
    state.events.sort((a,b) => String(a.created_at).localeCompare(String(b.created_at)));
    state.events = state.events.slice(-1000);
    state.cursor = data.cursor;
    renderLogs();
  }
}

function renderLogs() {
  const search = $('logSearch').value.toLowerCase().trim();
  const filtered = state.events.filter(event => (state.level === 'all' || event.level === state.level || (state.level === 'ERROR' && event.level === 'CRITICAL')) && `${event.message} ${JSON.stringify(event.details)} ${event.job_id || ''}`.toLowerCase().includes(search));
  $('errorCount').textContent = state.events.filter(event => ['ERROR','CRITICAL'].includes(event.level)).length;
  $('logCount').textContent = `${filtered.length} events in view${state.paused ? ' · paused' : ''}`;
  const feed = $('consoleFeed');
  const oldScroll = feed.scrollTop;
  feed.innerHTML = filtered.length ? filtered.map(event => {
    const time = new Date(event.created_at).toLocaleTimeString([], {hour12:false});
    const details = Object.entries(event.details || {}).map(([key,value]) => `${key}: ${value}`).join(' · ');
    return `<div class="log-entry ${esc(event.level)}"><div class="log-line"><time title="${esc(event.created_at)}">${esc(time)}</time><span class="log-level">${esc(event.level === 'WARNING' ? 'WARN' : event.level)}</span>${event.job_id ? `<span>job #${esc(event.job_id)}</span>` : ''}</div><div class="log-message">${esc(event.message)}</div>${details ? `<div class="log-context">${esc(details)}</div>` : ''}</div>`;
  }).join('') : `<div class="console-empty"><strong>${search || state.level !== 'all' ? 'Nothing here matches.' : 'Ready to collect.'}</strong><p>${search || state.level !== 'all' ? 'Try another filter or search.' : 'Collection progress, warnings, and errors appear here.'}</p></div>`;
  feed.scrollTop = state.follow ? feed.scrollHeight : oldScroll;
}

async function openDolKeyDialog(trigger = null) {
  $('dolKeyForm').reset();
  $('dolKeyMessage').textContent = '';
  $('dolApiKeyStatus').textContent = 'Checking current DOL API key status…';
  showDialog('dolKeyDialog', trigger, '#dolApiKey');
  try {
    const status = await api('/api/integrations/dol');
    $('dolApiKeyStatus').textContent = status.configured ? 'A DOL API key is stored locally. Enter a new key only to replace it; the stored key itself is never displayed.' : 'No DOL API key is stored yet. Paste a key below; it will be tested before saving.';
  } catch (error) { $('dolApiKeyStatus').textContent = 'Could not check current credential status.'; $('dolKeyMessage').textContent = error.message; }
}

async function saveDolApiKey(event) {
  event.preventDefault();
  const button = $('saveDolKey');
  const key = $('dolApiKey').value.trim();
  if (!key) return;
  button.disabled = true;
  $('dolKeyMessage').textContent = 'Testing key with the DOL Open Data API…';
  try {
    const result = await api('/api/integrations/dol', {method:'POST', body:JSON.stringify({api_key:key})});
    if (!result.validated) throw new Error('DOL API key could not be validated.');
    closeDialog('dolKeyDialog');
    notify('DOL API key tested and saved locally. OSHA research is ready.');
    await refreshAll();
  } catch (error) { $('dolKeyMessage').textContent = error.message; localEvent('ERROR', `DOL API key test failed: ${error.message}`); }
  finally { button.disabled = false; }
}

async function openSamKeyDialog(trigger = null) {
  $('samKeyForm').reset();
  $('samKeyMessage').textContent = '';
  $('samApiKeyStatus').textContent = 'Checking current SAM.gov API key status…';
  showDialog('samKeyDialog', trigger, '#samApiKey');
  try {
    const status = await api('/api/integrations/sam');
    $('samApiKeyStatus').textContent = status.configured ? 'A SAM.gov API key is stored locally. Enter a new key only to replace it; the stored key itself is never displayed.' : 'No SAM.gov Public API Key is stored yet. Paste one below; it will be validated before saving.';
  } catch (error) { $('samApiKeyStatus').textContent = 'Could not check current credential status.'; $('samKeyMessage').textContent = error.message; }
}

async function saveSamApiKey(event) {
  event.preventDefault();
  const button = $('saveSamKey');
  const key = $('samApiKey').value.trim();
  if (!key) return;
  button.disabled = true;
  $('samKeyMessage').textContent = 'Testing key with the SAM.gov production API…';
  try {
    const result = await api('/api/integrations/sam', {method:'POST', body:JSON.stringify({api_key:key})});
    if (!result.validated) throw new Error('SAM.gov API key could not be validated.');
    closeDialog('samKeyDialog');
    notify(result.warning || 'SAM.gov API key tested and saved locally. Federal debarment research is ready.');
    await refreshAll();
  } catch (error) { $('samKeyMessage').textContent = error.message; localEvent('ERROR', `SAM.gov API key test failed: ${error.message}`); }
  finally { button.disabled = false; }
}

function openSource(existing = null, trigger = null) {
  state.editingId = existing?.id || null;
  $('sourceForm').reset();
  $('formMessage').textContent = '';
  $('sourceUrl').readOnly = Boolean(existing);
  $('saveSource').textContent = existing ? 'Save site settings' : 'Save research site';
  $('sourceDialogTitle').textContent = existing ? 'Edit research site settings' : 'Add a research site';
  $('sourceSlotLabel').textContent = existing ? 'RESEARCH SITE / SETTINGS' : 'SET UP A RESEARCH SITE';
  if (existing) {
    const fields = {sourceName:'name',sourceUrl:'start_url',maxPages:'max_pages',maxDepth:'max_depth',concurrency:'concurrency',delayMs:'delay_ms',renderMode:'render_mode',intervalMinutes:'interval_minutes'};
    Object.entries(fields).forEach(([id,key]) => { $(id).value = existing[key]; });
    $('autoScan').value = String(Boolean(existing.auto_scan));
    $('respectRobots').value = String(Boolean(existing.respect_robots));
    $('sourceDialog').querySelector('.advanced').open = true;
  }
  showDialog('sourceDialog', trigger, '#sourceName');
}

async function saveSource(event) {
  event.preventDefault();
  const button = $('saveSource');
  button.disabled = true;
  $('formMessage').textContent = state.editingId ? 'Saving collection settings…' : 'Checking this website…';
  try {
    const body = {name:$('sourceName').value.trim(), start_url:$('sourceUrl').value.trim(), auto_scan:$('autoScan').value === 'true', interval_minutes:+$('intervalMinutes').value, max_pages:+$('maxPages').value, max_depth:+$('maxDepth').value, concurrency:+$('concurrency').value, delay_ms:+$('delayMs').value, render_mode:$('renderMode').value, respect_robots:$('respectRobots').value === 'true'};
    if (state.editingId) delete body.start_url;
    await api(state.editingId ? `/api/sources/${state.editingId}` : '/api/sources', {method:state.editingId ? 'PATCH' : 'POST', body:JSON.stringify(body)});
    closeDialog('sourceDialog');
    notify(state.editingId ? 'Research site settings saved. Existing evidence is preserved.' : 'Research site saved. It can now collect evidence for approved contractors.');
    await refreshAll();
  } catch (error) { $('formMessage').textContent = error.message; localEvent('ERROR', `Saving source failed: ${error.message}`); }
  finally { button.disabled = false; }
}

async function scanSource(id, forceFull = false) {
  try {
    const masterIds = selectedResearchIds();
    await api(`/api/sources/${id}/scan`, {method:'POST', body:JSON.stringify({force_full:forceFull, master_ids:masterIds})});
    notify(forceFull ? 'Full research collection queued. Evidence will remain separate from the master.' : 'Research collection queued. Follow progress in Activity.');
    await refreshAll();
  } catch (error) { notify(error.message, true); localEvent('ERROR', `Could not start research: ${error.message}`); }
}

async function runResearch() {
  const button = $('deskRunResearch');
  const sourceIds = Array.from(document.querySelectorAll('[data-research-source]:checked:not(:disabled)'), input => Number(input.value));
  if (!sourceIds.length) { notify('Select at least one ready research source.', true); return; }
  let masterIds;
  try { masterIds = selectedResearchIds(); } catch (error) { notify(error.message, true); return; }
  const forceFull = $('deskBypassCache').checked;
  button.disabled = true;
  $('deskResearchStatus').textContent = `Queuing ${sourceIds.length} source${sourceIds.length === 1 ? '' : 's'} for ${masterIds.length} approved contractor${masterIds.length === 1 ? '' : 's'}…`;
  let queued = 0;
  const errors = [];
  for (const sourceId of sourceIds) {
    try { await api(`/api/sources/${sourceId}/scan`, {method:'POST', body:JSON.stringify({force_full:forceFull, master_ids:masterIds})}); queued += 1; }
    catch (error) { errors.push(error.message); }
  }
  button.disabled = false;
  if (queued) notify(`${queued} research collection${queued === 1 ? '' : 's'} queued. Follow progress in Activity.`);
  if (errors.length) {
    $('deskResearchStatus').textContent = `${queued} queued; ${errors.length} could not start. ${errors[0]}`;
    notify(errors[0], true);
  } else {
    $('deskResearchStatus').textContent = `${queued} collection${queued === 1 ? '' : 's'} queued. Results will appear as Evidence, not automatic master edits.`;
  }
  await Promise.allSettled([loadJobs(), loadSources()]);
}

async function updateSites() {
  if (state.batchUpdating) return;
  const available = state.sources.filter(sourceReady);
  if (!available.length) return;
  let masterIds;
  try { masterIds = selectedResearchIds(); } catch (error) { notify(error.message, true); return; }
  state.batchUpdating = true;
  updateCollectionButton();
  try {
    const results = await Promise.allSettled(available.map(source => api(`/api/sources/${source.id}/scan`, {method:'POST', body:JSON.stringify({force_full:false, master_ids:masterIds})})));
    const successful = results.filter(result => result.status === 'fulfilled').length;
    results.forEach((result,index) => { if (result.status === 'rejected') localEvent('ERROR', `Collection could not start for ${available[index].name}: ${result.reason.message}`); });
    notify(`${successful} ready source collection${successful === 1 ? '' : 's'} queued.${successful < results.length ? ' Some sources could not start; see Activity.' : ''}`, successful < results.length);
    await refreshAll();
  } finally { state.batchUpdating = false; updateCollectionButton(); }
}

async function removeSource() {
  const button = $('confirmDelete');
  button.disabled = true;
  try {
    await api(`/api/sources/${state.deleteId}`, {method:'DELETE'});
    closeDialog('deleteDialog');
    notify('Research source removed.');
    await refreshAll();
  } catch (error) { notify(error.message, true); }
  finally { button.disabled = false; }
}

function saveBlob(blob, filename) {
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 60000);
}

async function exportRecords(format, button) {
  button.disabled = true;
  try {
    const response = await fetch('/api/bidder/export?' + recordParams({format}), {signal:AbortSignal.timeout(60000)});
    if (!response.ok) { const error = await response.json(); throw new Error(error.detail || 'Export failed'); }
    saveBlob(await response.blob(), `bidder-database-${new Date().toISOString().slice(0,10)}.${format}`);
    notify('Approved bidder database exported. Unresolved evidence was not included.');
  } catch (error) { notify(error.message, true); localEvent('ERROR', `Bidder export failed: ${error.message}`); }
  finally { button.disabled = false; }
}

async function exportLogs() {
  $('exportLogs').disabled = true;
  try {
    const response = await fetch('/api/activity/export', {signal:AbortSignal.timeout(15000)});
    if (!response.ok) throw new Error('Diagnostic export failed');
    saveBlob(await response.blob(), 'paralegal-research-diagnostics.zip');
    notify('Diagnostic report downloaded. Review it before sharing.');
    await loadLogs();
  } catch {
    saveBlob(new Blob([JSON.stringify({note:'Backend unavailable. Browser-visible events only.',created_at:new Date().toISOString(),events:state.events}, null, 2)], {type:'application/json'}), 'paralegal-research-offline-logs.json');
    notify('Backend unavailable. Downloaded the activity visible in this browser instead.');
  } finally { $('exportLogs').disabled = false; }
}

async function captureSnapshot() {
  $('snapshotButton').disabled = true;
  try { await api('/api/activity/snapshot', {method:'POST'}); notify('Snapshot saved to the activity log.'); if (!state.paused) await loadLogs(); }
  catch { localEvent('WARNING', 'Offline snapshot captured in this browser. Export diagnostics to save it.'); notify('Offline snapshot captured. Export diagnostics to save it.'); }
  finally { $('snapshotButton').disabled = false; }
}

async function refreshAll() {
  if (state.refreshing) return;
  state.refreshing = true;
  try {
    const health = await api('/api/health');
    setOnline(true);
    $('appVersion').textContent = health.version;
    const results = await Promise.allSettled([loadSources(), loadBidderStatus(), loadStats(), loadJobs(), loadRecords(), loadProposals(), loadLogs()]);
    results.forEach(result => { if (result.status === 'rejected') localEvent('ERROR', `Could not refresh a workspace panel: ${result.reason.message}`); });
    $('lastSync').textContent = `Updated ${new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}`;
  } catch {
    setOnline(false);
    $('lastSync').textContent = 'Retrying connection…';
  } finally { state.refreshing = false; }
}

function clearRecordFilters() { $('search').value = ''; $('fieldFilter').value = 'all'; }

function installEvents() {
  document.addEventListener('click', event => {
    const openView = event.target.closest('[data-open-view]');
    if (openView) { event.preventDefault(); switchView(openView.dataset.openView); return; }
    const viewLink = event.target.closest('[data-view-link]');
    if (viewLink) { event.preventDefault(); switchView(viewLink.dataset.viewLink); return; }
    const button = event.target.closest('button');
    if (!button) return;
    if (button.dataset.close) { closeDialog(button.dataset.close); return; }
    if (button.hasAttribute('data-dol-key')) { openDolKeyDialog(button); return; }
    if (button.hasAttribute('data-sam-key')) { openSamKeyDialog(button); return; }
    if (button.dataset.record) { viewRecord(Number(button.dataset.record), button); return; }
    if (button.dataset.edit) { openSource(state.sources.find(source => source.id === Number(button.dataset.edit)), button); return; }
    if (button.dataset.scan) { scanSource(Number(button.dataset.scan), false); return; }
    if (button.dataset.full) { scanSource(Number(button.dataset.full), true); return; }
    if (button.dataset.delete) {
      state.deleteId = Number(button.dataset.delete);
      const source = state.sources.find(item => item.id === state.deleteId);
      $('deleteDescription').textContent = `“${source?.name || 'This source'}” and its collected evidence/history will be removed. This cannot be undone.`;
      showDialog('deleteDialog', button);
      return;
    }
    if (button.dataset.export) { exportRecords(button.dataset.export, button); return; }
    if (button.dataset.applyProposal) { applyProposal(Number(button.dataset.applyProposal), button); return; }
    if (button.dataset.dismissProposal) { dismissProposal(Number(button.dataset.dismissProposal), button); return; }
    if (button.dataset.reviewMode) {
      state.reviewMode = button.dataset.reviewMode;
      document.querySelectorAll('[data-review-mode]').forEach(tab => { const active = tab === button; tab.classList.toggle('active', active); tab.setAttribute('aria-selected', String(active)); });
      renderProposals();
      return;
    }
    if (button.dataset.logLevel) {
      state.level = button.dataset.logLevel;
      document.querySelectorAll('[data-log-level]').forEach(item => { const active = item === button; item.classList.toggle('selected', active); item.setAttribute('aria-pressed', String(active)); });
      renderLogs();
    }
  });

  $('deskMenuButton').addEventListener('click', () => {
    const open = document.body.classList.toggle('nav-open');
    $('deskMenuButton').setAttribute('aria-expanded', String(open));
  });
  document.querySelector('.desk-nav-backdrop').addEventListener('click', () => { document.body.classList.remove('nav-open'); $('deskMenuButton').setAttribute('aria-expanded','false'); });
  $('helpButton').addEventListener('click', event => showDialog('helpDialog', event.currentTarget));
  $('deskOpenHelp').addEventListener('click', event => showDialog('helpDialog', event.currentTarget));
  $('addSourceButton').addEventListener('click', event => openSource(null, event.currentTarget));
  $('sourceForm').addEventListener('submit', saveSource);
  $('dolKeyForm').addEventListener('submit', saveDolApiKey);
  $('samKeyForm').addEventListener('submit', saveSamApiKey);
  $('oshaApiKeyButton').addEventListener('click', event => openDolKeyDialog(event.currentTarget));
  $('samApiKeyButton').addEventListener('click', event => openSamKeyDialog(event.currentTarget));
  $('confirmDelete').addEventListener('click', removeSource);
  $('refreshSources').addEventListener('click', () => refreshAll());
  $('updateSites').addEventListener('click', updateSites);
  $('uploadCsvButton').addEventListener('click', () => $('csvUpload').click());
  $('csvUpload').addEventListener('change', () => importBidderCsv($('csvUpload').files?.[0]));
  $('recordSearchForm').addEventListener('submit', event => { event.preventDefault(); state.offset = 0; loadRecords().catch(error => notify(error.message,true)); });
  $('fieldFilter').addEventListener('change', () => { state.offset = 0; loadRecords().catch(error => notify(error.message,true)); });
  $('clearFilters').addEventListener('click', () => { clearRecordFilters(); state.offset = 0; loadRecords().catch(error => notify(error.message,true)); $('search').focus(); });
  $('prevPage').addEventListener('click', () => { state.offset = Math.max(0, state.offset - PAGE_SIZE); loadRecords().catch(error => notify(error.message,true)); });
  $('nextPage').addEventListener('click', () => { state.offset += PAGE_SIZE; loadRecords().catch(error => notify(error.message,true)); });
  $('toggleAllFields').addEventListener('click', event => {
    const expanded = $('recordsSection').classList.toggle('show-all-fields');
    event.currentTarget.textContent = expanded ? 'Compact view' : 'Show all 30 fields';
    event.currentTarget.setAttribute('aria-pressed', String(expanded));
  });
  document.querySelectorAll('input[name="deskResearchMode"]').forEach(input => input.addEventListener('change', applyResearchMode));
  $('selectAllContractors').addEventListener('click', () => Array.from($('researchContractors').options).forEach(option => { option.selected = true; }));
  $('clearContractors').addEventListener('click', () => Array.from($('researchContractors').options).forEach(option => { option.selected = false; }));
  $('deskRunResearch').addEventListener('click', () => runResearch().catch(error => { notify(error.message,true); $('deskResearchStatus').textContent = error.message; }));
  $('compareButton').addEventListener('click', compareBidderData);
  $('refreshProposals').addEventListener('click', () => Promise.all([loadBidderStatus(), loadProposals()]));
  $('deskEvidenceForm').addEventListener('submit', event => { event.preventDefault(); state.evidenceOffset = 0; loadEvidence().catch(renderEvidenceError); });
  $('deskEvidenceSource').addEventListener('change', () => { state.evidenceOffset = 0; loadEvidence().catch(renderEvidenceError); });
  $('clearEvidenceFilters').addEventListener('click', () => { $('deskEvidenceSearch').value = ''; $('deskEvidenceSource').value = ''; state.evidenceOffset = 0; loadEvidence().catch(renderEvidenceError); });
  $('evidencePrevPage').addEventListener('click', () => { state.evidenceOffset = Math.max(0, state.evidenceOffset - EVIDENCE_PAGE_SIZE); loadEvidence().catch(renderEvidenceError); });
  $('evidenceNextPage').addEventListener('click', () => { state.evidenceOffset += EVIDENCE_PAGE_SIZE; loadEvidence().catch(renderEvidenceError); });
  $('logSearch').addEventListener('input', renderLogs);
  $('pauseLogs').addEventListener('click', () => {
    state.paused = !state.paused;
    $('pauseLogs').textContent = state.paused ? '▶' : 'Ⅱ';
    $('pauseLogs').setAttribute('aria-label', state.paused ? 'Resume live activity' : 'Pause live activity');
    $('consoleLive').className = 'live-label' + (state.paused ? ' paused' : '');
    $('consoleLive').innerHTML = `<i></i>${state.paused ? 'PAUSED' : 'LIVE'}`;
    renderLogs();
    if (!state.paused) loadLogs().catch(() => {});
  });
  $('autoScroll').addEventListener('click', () => { state.follow = !state.follow; $('autoScroll').setAttribute('aria-pressed', String(state.follow)); renderLogs(); });
  $('snapshotButton').addEventListener('click', captureSnapshot);
  $('exportLogs').addEventListener('click', exportLogs);
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-test-api]');
    if (!button) return;
    button.disabled = true;
    try { await api(`/api/integrations/${button.dataset.testApi}/test`, {method:'POST'}); notify('API connection test succeeded.'); }
    catch (error) { notify(error.message, true); }
    finally { button.disabled = false; }
  });
  window.addEventListener('hashchange', () => switchView(VALID_VIEWS.has(location.hash.slice(1)) ? location.hash.slice(1) : 'dashboard', false));
  window.addEventListener('popstate', () => switchView(VALID_VIEWS.has(location.hash.slice(1)) ? location.hash.slice(1) : 'dashboard', false));
  window.addEventListener('error', event => localEvent('ERROR', `Browser error: ${event.message}`));
  window.addEventListener('unhandledrejection', event => localEvent('ERROR', `Browser operation failed: ${event.reason?.message || 'Unknown error'}`));
}

async function poll() {
  await refreshAll();
  setTimeout(poll, 2500);
}

function init() {
  installEvents();
  const requested = location.hash.slice(1);
  switchView(VALID_VIEWS.has(requested) ? requested : 'dashboard', false);
  applyResearchMode();
  poll();
}

init();
