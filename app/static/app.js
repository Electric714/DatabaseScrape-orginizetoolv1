const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeUrl = value => { try { const u = new URL(value); return ['http:', 'https:'].includes(u.protocol) ? u.href : '#'; } catch { return '#'; } };
const state = {sources: [], sourceSignature: '', jobsSignature: '', events: [], cursor: 0, paused: false, follow: true, level: 'all', offset: 0, total: 0, refreshing: false, online: null, deleteId: null, recordSequence: 0, detailSequence: 0, batchUpdating: false, editingId: null, bidderStatus: null, proposalSignature: '', dolStatus: null, samStatus: null};
const OSHA_HOSTS = new Set(['www.osha.gov', 'apiprod.dol.gov', 'api.dol.gov']);
const OSHA_API_URL = 'https://apiprod.dol.gov/v4/get/OSHA/inspection/json';
const SAM_HOSTS = new Set(['sam.gov', 'www.sam.gov', 'api.sam.gov', 'api-alpha.sam.gov']);
const SAM_API_URL = 'https://api-alpha.sam.gov/entity-information/v4/exclusions';
const PAGE_SIZE = 50;
let toastTimer;

function notify(message, error = false) {
  $('toast').textContent = message;
  $('toast').className = 'toast' + (error ? ' error' : '');
  $('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 5000);
}
function localEvent(level, message) {
  state.events.push({id: 'local-' + Date.now(), created_at: new Date().toISOString(), level, component: 'browser', message, details: {}});
  state.events = state.events.slice(-1000);
  renderLogs();
}
async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {'Content-Type': 'application/json', ...options.headers}, signal: AbortSignal.timeout(12000)});
  if (!response.ok) {
    let detail = 'Request failed (' + response.status + ').';
    try { const body = await response.json(); detail = Array.isArray(body.detail) ? body.detail.map(x => x.msg).join('; ') : body.detail || detail; } catch {}
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}
function humanDate(value, short = false) {
  if (!value) return 'Not collected yet';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return short ? date.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}) : date.toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'});
}
function setOnline(online) {
  if (state.online !== online) {
    localEvent(online ? 'INFO' : 'WARNING', online ? 'Connected to your workspace.' : 'Connection lost. Saved activity remains visible. Trying to reconnect…');
  }
  state.online = online;
  $('health').className = 'connection' + (online ? '' : ' offline');
  $('health').innerHTML = '<i></i><span>' + (online ? 'Workspace online' : 'Reconnecting') + '</span>';
}
function isCollecting(source) { return ['queued', 'running'].includes(source.last_status); }
function isOshaSource(source) {
  try { return OSHA_HOSTS.has(new URL(source.start_url).hostname.toLowerCase()); } catch { return false; }
}
function isSamSource(source) {
  try { return SAM_HOSTS.has(new URL(source.start_url).hostname.toLowerCase()); } catch { return false; }
}
function sourceReadyForCollection(source) {
  return !isCollecting(source)
    && (!isOshaSource(source) || Boolean(state.dolStatus?.configured))
    && (!isSamSource(source) || Boolean(state.samStatus?.configured));
}
function updateCollectionButton() {
  const available = state.sources.filter(sourceReadyForCollection).length;
  $('updateSites').disabled = state.batchUpdating || available === 0;
  $('updateSites').textContent = state.batchUpdating ? 'Queuing collections…' : 'Update configured sites';
  $('updateSites').title = available ? 'Collect updated records from ' + available + ' available research sites' : (state.sources.length ? 'All configured sites are already collecting' : 'Set up a research site first');
}
async function loadSources() {
  const [sources, dolStatus, samStatus] = await Promise.all([
    api('/api/sources'),
    api('/api/integrations/dol'),
    api('/api/integrations/sam')
  ]);
  state.sources = sources;
  state.dolStatus = dolStatus;
  state.samStatus = samStatus;
  $('oshaApiKeyButton').textContent = dolStatus.configured ? 'Change OSHA API key' : 'Set OSHA API key';
  $('oshaApiKeyButton').title = dolStatus.configured ? 'Replace and validate the locally saved DOL API key' : 'Enter and validate the DOL API key required for OSHA collection';
  $('samApiKeyButton').textContent = samStatus.configured ? 'Change SAM test API key' : 'Set SAM test API key';
  $('samApiKeyButton').title = samStatus.configured ? 'Replace and validate the locally saved SAM.gov Alpha API key' : 'Enter and validate the SAM.gov Alpha/test API key required for federal debarment collection';
  $('sourceCount').textContent = sources.length < 6 ? sources.length + ' / 6' : sources.length + ' configured';
  $('navSources').textContent = sources.length < 6 ? sources.length + '/6' : sources.length;
  $('sitesHint').textContent = sources.length < 6 ? (6 - sources.length) + ' site' + (sources.length === 5 ? '' : 's') + ' awaiting setup' : 'Ready for collection and source review';
  $('siteSetupNote').textContent = sources.length < 6
    ? 'OSHA/DOL and SAM.gov Federal Debarment are built in. Remaining sources can be added as their adapters are finalized.'
    : 'Collect from each source, then review its saved evidence.';
  updateCollectionButton();

  const signature = JSON.stringify({sources, dolConfigured: dolStatus.configured, samConfigured: samStatus.configured});
  if (signature === state.sourceSignature) return;
  state.sourceSignature = signature;

  const selection = $('sourceFilter').value;
  $('sourceFilter').innerHTML = '<option value="">All research sites</option>' + sources.map(s => '<option value="' + s.id + '">' + esc(s.name) + '</option>').join('');
  $('sourceFilter').value = sources.some(s => String(s.id) === selection) ? selection : '';

  const configured = sources.map((s, index) => {
    const running = isCollecting(s);
    const isOsha = isOshaSource(s);
    const isSam = isSamSource(s);
    if (isOsha) {
      const keyState = dolStatus.configured ? 'API key configured' : 'API key required';
      return '<article class="source-row"><div class="source-avatar" aria-hidden="true">' + (index + 1) + '</div><div class="source-info"><h3>OSHA / DOL Enforcement API</h3><a class="source-url" href="' + OSHA_API_URL + '" target="_blank" rel="noreferrer">' + OSHA_API_URL + '</a><div class="source-details"><span class="tag ' + (dolStatus.configured ? 'completed' : 'partial') + '">' + keyState + '</span><span>Built-in REST source</span><span>· ' + esc(humanDate(s.last_scan_at)) + '</span></div></div><div class="source-controls"><button class="settings-button" data-dol-key>Set API key</button><button class="scan-button" data-scan="' + s.id + '"' + (running || !dolStatus.configured ? ' disabled' : '') + '>' + (running ? 'Collecting…' : 'Collect OSHA') + '</button><button class="icon-button" data-full="' + s.id + '" aria-label="Recollect OSHA API records" title="Recollect OSHA API records, ignoring cached responses"' + (running || !dolStatus.configured ? ' disabled' : '') + '>↻</button></div></article>';
    }
    if (isSam) {
      const keyState = samStatus.configured ? 'API key configured' : 'API key required';
      return '<article class="source-row"><div class="source-avatar" aria-hidden="true">' + (index + 1) + '</div><div class="source-info"><h3>SAM.gov Federal Debarment / Exclusions</h3><a class="source-url" href="' + SAM_API_URL + '" target="_blank" rel="noreferrer">' + SAM_API_URL + '</a><div class="source-details"><span class="tag ' + (samStatus.configured ? 'completed' : 'partial') + '">' + keyState + '</span><span>Built-in REST source · Alpha/test v4</span><span>· ' + esc(humanDate(s.last_scan_at)) + '</span></div></div><div class="source-controls"><button class="settings-button" data-sam-key>Set test API key</button><button class="scan-button" data-scan="' + s.id + '"' + (running || !samStatus.configured ? ' disabled' : '') + '>' + (running ? 'Collecting…' : 'Collect federal debarment') + '</button><button class="icon-button" data-full="' + s.id + '" aria-label="Recollect SAM federal debarment API records" title="Recollect SAM Alpha API records, ignoring cached responses"' + (running || !samStatus.configured ? ' disabled' : '') + '>↻</button></div></article>';
    }
    return '<article class="source-row"><div class="source-avatar" aria-hidden="true">' + (index + 1) + '</div><div class="source-info"><h3>' + esc(s.name) + '</h3><a class="source-url" href="' + esc(safeUrl(s.start_url)) + '" target="_blank" rel="noreferrer">' + esc(s.start_url) + '</a><div class="source-details"><span class="tag ' + esc(s.last_status || '') + '">' + esc(s.last_status || 'Ready to collect') + '</span><span>' + (s.auto_scan ? 'Every ' + s.interval_minutes + ' min' : 'Manual collection') + '</span><span>· ' + esc(humanDate(s.last_scan_at)) + '</span></div></div><div class="source-controls"><button class="scan-button" data-scan="' + s.id + '"' + (running ? ' disabled' : '') + '>' + (running ? 'Collecting…' : 'Collect records') + '</button><button class="settings-button" data-edit="' + s.id + '">Edit settings</button><button class="icon-button" data-full="' + s.id + '" aria-label="Recollect all pages from ' + esc(s.name) + '" title="Recollect all pages, ignoring cached pages"' + (running ? ' disabled' : '') + '>↻</button><button class="icon-button" data-delete="' + s.id + '" aria-label="Delete ' + esc(s.name) + '" title="Remove research site"' + (running ? ' disabled' : '') + '>×</button></div></article>';
  }).join('');

  const pending = Array.from({length: Math.max(0, 6 - sources.length)}, (_, index) => {
    const number = sources.length + index + 1;
    return '<article class="pending-site"><span class="pending-number" aria-hidden="true">' + number + '</span><div><h3>Research site ' + number + '</h3><p>Name and website address pending</p></div><button class="button subtle small" data-open-source="' + number + '">Set up site ' + number + '</button></article>';
  }).join('');
  $('sources').innerHTML = (configured ? '<div class="source-list">' + configured + '</div>' : '') + (pending ? '<div class="pending-sites">' + pending + '</div>' : '') + (sources.length >= 6 ? '<div class="additional-source"><button class="text-button" data-open-source>Add another research site</button></div>' : '');
}
async function loadStats() {
  const stats = await api('/api/stats');
  $('statSources').textContent = stats.sources.toLocaleString();
  $('statChanged').textContent = stats.changed_24h.toLocaleString();
  $('statRunning').textContent = stats.running_jobs.toLocaleString();
  if (!state.bidderStatus) state.bidderStatus = await api('/api/bidder/status');
  $('statRecords').textContent = state.bidderStatus.master_total.toLocaleString();
  $('runningHint').textContent = stats.running_jobs ? 'Saving information from research sites' : 'Ready to collect from configured sites';
}
async function loadBidderStatus() {
  const status = await api('/api/bidder/status');
  state.bidderStatus = status;
  $('masterCount').textContent = status.master_total.toLocaleString();
  $('sourceRecordCount').textContent = status.active_source_records.toLocaleString();
  $('pendingUpdateCount').textContent = status.pending_updates.toLocaleString();
  $('lastImport').textContent = status.latest_import ? new Date(status.latest_import.imported_at).toLocaleDateString() : 'Never';
  $('baselineStatus').textContent = status.latest_import
    ? status.master_total.toLocaleString() + ' contractors loaded from ' + status.latest_import.filename + '. Scraped findings are waiting for comparison/approval.'
    : 'Upload the existing bidder CSV to use it as the master starting database.';
  $('masterImportSummary').textContent = status.latest_import
    ? status.master_total.toLocaleString() + ' approved contractors loaded from ' + status.latest_import.filename + '. Upload another CSV any time to refresh the master baseline.'
    : 'Upload the existing 30-column CSV and it becomes the approved master database used for comparisons.';
  $('uploadCsvButton').textContent = status.latest_import ? 'Import another CSV' : 'Upload master CSV';
  $('compareButton').disabled = status.master_total === 0 || status.active_source_records === 0;
  return status;
}

async function importBidderCsv(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.csv')) { notify('Please choose a CSV file.', true); return; }
  $('uploadCsvButton').disabled = true;
  $('baselineStatus').textContent = 'Importing ' + file.name + '…';
  try {
    const response = await fetch('/api/bidder/import', {
      method: 'POST',
      headers: {'Content-Type':'text/csv', 'X-Filename': file.name},
      body: file,
      signal: AbortSignal.timeout(60000)
    });
    if (!response.ok) {
      let detail = 'CSV import failed.';
      try { detail = (await response.json()).detail || detail; } catch {}
      throw new Error(detail);
    }
    const result = await response.json();
    notify('Imported ' + result.rows_total + ' rows: ' + result.rows_inserted + ' new, ' + result.rows_updated + ' updated, ' + result.rows_unchanged + ' unchanged.');
    localEvent('INFO', 'Bidder CSV imported: ' + file.name);
    state.offset = 0;
    await Promise.all([loadBidderStatus(), loadRecords(), loadProposals()]);
    if (result.warnings?.length) notify(result.warnings.join(' '), true);
  } catch (error) {
    notify(error.message, true);
    localEvent('ERROR', 'Bidder CSV import failed: ' + error.message);
    await loadBidderStatus().catch(() => {});
  } finally {
    $('uploadCsvButton').disabled = false;
    $('csvUpload').value = '';
  }
}

async function compareBidderData() {
  $('compareButton').disabled = true;
  $('proposalStatus').textContent = 'Comparing collected source records with the approved bidder database…';
  try {
    const result = await api('/api/bidder/compare', {method:'POST'});
    notify('Comparison complete: ' + result.field_changes + ' field changes, ' + result.new_contractors + ' new contractors, ' + result.ambiguous_matches + ' ambiguous matches.');
    await Promise.all([loadBidderStatus(), loadProposals()]);
  } catch (error) {
    notify(error.message, true);
    localEvent('ERROR', 'Bidder comparison failed: ' + error.message);
  } finally {
    const status = await loadBidderStatus().catch(() => state.bidderStatus);
    $('compareButton').disabled = !status || status.master_total === 0 || status.active_source_records === 0;
  }
}

function proposalContractor(p) {
  return p.master_contractor || p.proposed?.contractor_name || 'Unknown contractor';
}

async function loadProposals() {
  const proposals = await api('/api/bidder/proposals?status=pending&limit=1000');
  const signature = JSON.stringify(proposals.map(p => [p.id,p.status,p.old_value,p.new_value]));
  if (signature === state.proposalSignature) return;
  state.proposalSignature = signature;
  $('pendingUpdateCount').textContent = proposals.length.toLocaleString();
  $('proposalStatus').textContent = proposals.length
    ? proposals.length + ' proposed change' + (proposals.length === 1 ? '' : 's') + ' waiting for review.'
    : 'No proposed updates waiting for review.';
  $('proposals').innerHTML = proposals.length ? proposals.map(p => {
    const contractor = proposalContractor(p);
    const source = p.source_url
      ? '<a href="' + esc(safeUrl(p.source_url)) + '" target="_blank" rel="noreferrer">' + esc(p.source_name || 'Open source') + ' ↗</a>'
      : esc(p.source_name || 'Saved source record');
    if (p.proposal_type === 'new_record') {
      const summary = [p.proposed?.address_1, p.proposed?.city, p.proposed?.state, p.proposed?.zip].filter(Boolean).join(', ');
      return '<tr class="proposal-new"><td><strong>' + esc(contractor) + '</strong></td><td><span class="change-field">NEW CONTRACTOR</span></td><td><span class="old-value">Not in master database</span></td><td><span class="new-value">' + esc(summary || 'New bidder record found') + '</span></td><td>' + source + '</td><td><button class="button primary tiny" data-apply-proposal="' + p.id + '">Add contractor</button><button class="text-button" data-dismiss-proposal="' + p.id + '">Dismiss</button></td></tr>';
    }
    if (p.proposal_type === 'ambiguous') {
      return '<tr class="proposal-ambiguous"><td><strong>' + esc(contractor) + '</strong></td><td><span class="change-field">AMBIGUOUS MATCH</span></td><td><span class="old-value">Multiple master contractors share this name</span></td><td><span class="new-value">Manual match required</span></td><td>' + source + '</td><td><button class="text-button" data-dismiss-proposal="' + p.id + '">Dismiss</button></td></tr>';
    }
    return '<tr><td><strong>' + esc(contractor) + '</strong></td><td><span class="change-field">' + esc(p.field_name) + '</span></td><td><span class="old-value">' + esc(p.old_value || 'blank') + '</span></td><td><span class="new-value">' + esc(p.new_value || 'blank') + '</span></td><td>' + source + '</td><td><button class="button primary tiny" data-apply-proposal="' + p.id + '">Update</button><button class="text-button" data-dismiss-proposal="' + p.id + '">Keep old</button></td></tr>';
  }).join('') : '<tr><td colspan="6" class="table-empty">Nothing is waiting for approval. Run Compare collected data after a collection finishes.</td></tr>';
}

async function applyProposal(id, button) {
  button.disabled = true;
  try {
    await api('/api/bidder/proposals/' + id + '/apply', {method:'POST'});
    notify('Approved change applied to the master bidder database.');
    state.proposalSignature = '';
    await Promise.all([loadBidderStatus(), loadProposals(), loadRecords()]);
  } catch (error) { notify(error.message, true); }
  finally { button.disabled = false; }
}

async function dismissProposal(id, button) {
  button.disabled = true;
  try {
    await api('/api/bidder/proposals/' + id + '/dismiss', {method:'POST'});
    notify('Proposed change dismissed. The master value was left alone.');
    state.proposalSignature = '';
    await Promise.all([loadBidderStatus(), loadProposals()]);
  } catch (error) { notify(error.message, true); }
  finally { button.disabled = false; }
}

async function loadJobs() {
  const jobs = await api('/api/jobs?limit=8');
  const signature = JSON.stringify(jobs);
  if (signature === state.jobsSignature) return;
  state.jobsSignature = signature;
  $('jobs').innerHTML = jobs.length ? jobs.map(j => '<article class="job"><div class="job-head"><strong>' + esc(j.source_name) + '</strong><span class="tag ' + esc(j.status) + '">' + esc(j.status) + '</span></div><span class="job-time">' + esc(humanDate(j.started_at, true)) + '</span><p class="job-message">' + esc(j.message || 'Waiting to begin…') + '</p><div class="job-stats"><span><b>' + j.pages_processed + '/' + j.pages_discovered + '</b> pages</span><span><b>' + j.records_found + '</b> found</span><span><b>' + j.records_new + '</b> new</span><span><b>' + j.records_updated + '</b> updated</span><span><b>' + j.errors + '</b> errors</span></div></article>').join('') : '<div class="job"><p class="job-message">No collections yet. Set up a research site, then choose Collect records.</p></div>';
}
function recordParams(extra = {}) {
  return new URLSearchParams({...extra, q: $('search').value.trim(), field: $('fieldFilter').value});
}
const OSHA_LABELS = {open: 'Open — source reported', closed: 'Closed — source reported', none_reported: 'None reported by source', unknown: 'Not reported / unknown'};
const BIDDER_COLUMNS = ["id","contractor_name","related_companies","address_1","city","state","zip","additional_address","additional_address_city","additional_address_state","additional_address_zip","dfi","wc","wc_date","osha_severe_violations","years","osha","state_federal_debarment","mndol_ineligibility","public_works_projects_budget_time_quality_complaint","federal_court","circuit_court","ccap_show150","environmental_violations","prevailing_wage_violations","dwd","dwd_substance_abuse_plan","better_business_bureau_complaints","misc_violations","tax_liability"];
function oshaBadge(record) {
  const status = Object.hasOwn(OSHA_LABELS, record.osha_status) ? record.osha_status : 'unknown';
  return '<span class="osha-badge ' + status + '">' + OSHA_LABELS[status] + '</span>';
}
async function loadRecords() {
  const sequence = ++state.recordSequence;
  const params = recordParams({limit: PAGE_SIZE, offset: state.offset});
  const data = await api('/api/bidder/master?' + params);
  if (sequence !== state.recordSequence) return;
  state.total = data.total;
  if (state.offset && state.offset >= data.total) { state.offset = 0; return loadRecords(); }
  const filtered = params.get('q') || $('fieldFilter').value !== 'all';
  $('recordCount').textContent = data.total.toLocaleString() + ' approved bidder records' + (filtered ? ' matching your filters.' : ' in the master database.');
  $('pageInfo').textContent = data.total ? (state.offset + 1) + '–' + (state.offset + data.items.length) + ' of ' + data.total.toLocaleString() : 'No matching bidder records';
  $('prevPage').disabled = state.offset === 0;
  $('nextPage').disabled = state.offset + PAGE_SIZE >= data.total;
  $('records').innerHTML = data.items.length ? data.items.map(r => {
    const cells = BIDDER_COLUMNS.map(column => {
      const value = r[column] ?? '';
      if (column === 'contractor_name') return '<td><strong>' + esc(value || '—') + '</strong></td>';
      return '<td>' + esc(value === '' ? '—' : value) + '</td>';
    }).join('');
    return '<tr>' + cells + '</tr>';
  }).join('') : '<tr><td colspan="' + BIDDER_COLUMNS.length + '" class="table-empty"><strong>' + (filtered ? 'No bidder records match these filters.' : 'No master bidder database has been imported yet.') + '</strong><p>' + (filtered ? 'Try another search.' : 'Use Upload master CSV above to load the law firm’s current database as the starting point.') + '</p></td></tr>';
}

async function viewRecord(id) {
  const sequence = ++state.detailSequence;
  $('recordDialogTitle').textContent = 'Research record';
  $('recordDetail').innerHTML = '<p class="detail-loading">Loading the saved record and its source…</p>';
  $('recordDialog').showModal();
  try {
    const r = await api('/api/records/' + id);
    if (sequence !== state.detailSequence) return;
    const bidder = r.bidder || {};
    $('recordDialogTitle').textContent = bidder.contractor_name || r.company || r.name || r.owner || 'Research record #' + id;
    let extra = r.extra || {};
    try { if (r.extra_json) extra = JSON.parse(r.extra_json); } catch {}
    const collectedFrom = typeof extra.collected_from_url === 'string' ? extra.collected_from_url : '';
    const field = (label, value) => '<div><dt>' + label + '</dt><dd>' + esc(value || 'Not reported') + '</dd></div>';
    const bidderFields = BIDDER_COLUMNS.map(column => field(column, bidder[column])).join('');
    $('recordDetail').innerHTML = '<p class="detail-subtitle">Saved record #' + r.id + ' · ' + esc(r.source_name) + '</p>' +
      '<section class="record-bidder"><h3>Bidder database fields</h3><dl class="record-fields">' + bidderFields + '</dl></section>' +
      '<section class="record-osha"><h3>Additional source details</h3><dl class="record-fields">' + field('Person / contact name', r.name) + field('Owner', r.owner) + field('Phone', r.phone) + field('Source record ID', r.external_id) + '</dl><p>' + esc(r.osha_details || 'No additional source narrative was collected for this record.') + '</p></section>' +
      '<section class="record-provenance"><h3>Source &amp; collection evidence</h3><a class="evidence-source" href="' + esc(safeUrl(r.source_url)) + '" target="_blank" rel="noreferrer">' + (collectedFrom && collectedFrom !== r.source_url ? 'Open record link' : 'Open collection page') + ' <span aria-hidden="true">↗</span><small>' + esc(r.source_url) + '</small></a>' + (collectedFrom && collectedFrom !== r.source_url ? '<a class="evidence-source secondary-evidence" href="' + esc(safeUrl(collectedFrom)) + '" target="_blank" rel="noreferrer">Collected from <span aria-hidden="true">↗</span><small>' + esc(collectedFrom) + '</small></a>' : '') + '<dl class="record-fields">' + field('First collected', r.first_seen ? humanDate(r.first_seen) : '') + field('Last collected', r.last_seen ? humanDate(r.last_seen) : '') + field('Last changed in local database', r.last_changed ? humanDate(r.last_changed) : '') + field('Collection presence', r.active ? 'Retained as present in saved records' : 'Not seen during a later complete collection') + '</dl><p class="detail-note">The 30 bidder fields mirror the law firm’s spreadsheet. Source evidence and collection history are retained separately so the exported database stays clean.</p></section>';
  } catch (error) {
    if (sequence === state.detailSequence) $('recordDetail').innerHTML = '<p class="form-message">' + esc(error.message) + '</p>';
  }
}
async function loadLogs() {
  if (state.paused) return;
  const data = await api('/api/activity?after=' + state.cursor + '&limit=500');
  if (data.reset) { state.cursor = 0; state.events = []; return; }
  if (data.items.length) {
    const ids = new Set(state.events.map(x => x.id));
    state.events.push(...data.items.filter(x => !ids.has(x.id)));
    state.events.sort((a,b) => a.created_at.localeCompare(b.created_at));
    state.events = state.events.slice(-1000);
    state.cursor = data.cursor;
    renderLogs();
  }
}
function renderLogs() {
  const search = $('logSearch').value.toLowerCase().trim();
  const filtered = state.events.filter(e => (state.level === 'all' || e.level === state.level || state.level === 'ERROR' && e.level === 'CRITICAL') && (e.message + ' ' + JSON.stringify(e.details) + ' ' + (e.job_id || '')).toLowerCase().includes(search));
  $('errorCount').textContent = state.events.filter(e => ['ERROR','CRITICAL'].includes(e.level)).length;
  $('logCount').textContent = filtered.length + ' events in view' + (state.paused ? ' · paused' : '');
  const feed = $('consoleFeed');
  const oldScroll = feed.scrollTop;
  feed.innerHTML = filtered.length ? filtered.map(e => {
    const time = new Date(e.created_at).toLocaleTimeString([], {hour12:false});
    const details = Object.entries(e.details || {}).map(([k,v]) => k + ': ' + v).join(' · ');
    return '<div class="log-entry ' + esc(e.level) + '"><div class="log-line"><time class="log-time" title="' + esc(e.created_at) + '">' + esc(time) + '</time><span class="log-level">' + esc(e.level === 'WARNING' ? 'WARN' : e.level) + '</span>' + (e.job_id ? '<span class="log-job">scan #' + e.job_id + '</span>' : '') + '</div><div class="log-message">' + esc(e.message) + '</div>' + (details ? '<div class="log-context">' + esc(details) + '</div>' : '') + '</div>';
  }).join('') : '<div class="console-empty"><span>›_</span><strong>' + (search || state.level !== 'all' ? 'Nothing here matches.' : 'Ready to collect.') + '</strong><p>' + (search || state.level !== 'all' ? 'Try another filter or search.' : 'Startup, scans, updates, and errors<br>appear here automatically.') + '</p></div>';
  feed.scrollTop = state.follow ? feed.scrollHeight : oldScroll;
}
async function refreshAll() {
  if (state.refreshing) return;
  state.refreshing = true;
  try {
    const health = await api('/api/health');
    setOnline(true);
    $('appVersion').textContent = health.version;
    const results = await Promise.allSettled([loadSources(), loadBidderStatus(), loadStats(), loadJobs(), loadRecords(), loadProposals(), loadLogs()]);
    for (const result of results) if (result.status === 'rejected') localEvent('ERROR', 'Could not refresh a workspace panel: ' + result.reason.message);
    $('lastSync').textContent = 'Updated ' + new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
  } catch {
    setOnline(false);
    $('lastSync').textContent = 'Retrying connection…';
  } finally { state.refreshing = false; }
}
function openSource(slot, existing = null) {
  state.editingId = existing?.id || null;
  $('sourceForm').reset();
  $('formMessage').textContent = '';
  $('sourceUrl').readOnly = Boolean(existing);
  $('saveSource').textContent = existing ? 'Save site settings' : 'Save research site';
  $('sourceDialog').querySelector('.modal-intro').textContent = existing ? 'Update the name, schedule, or collection limits for this site. Its saved research records are preserved.' : 'Connect one of your public-record research websites or APIs. Records collected from it are saved locally.';
  $('sourceSlotLabel').textContent = existing ? 'RESEARCH SITE / SETTINGS' : (slot ? 'RESEARCH SITE ' + slot + ' / SETUP' : 'SET UP A RESEARCH SITE');
  $('sourceDialogTitle').textContent = existing ? 'Edit research site settings.' : (slot ? 'Set up research site ' + slot + '.' : 'Add a research site.');

  if (existing) {
    const fields = {sourceName:'name', sourceUrl:'start_url', maxPages:'max_pages', maxDepth:'max_depth', concurrency:'concurrency', delayMs:'delay_ms', renderMode:'render_mode', intervalMinutes:'interval_minutes'};
    Object.entries(fields).forEach(([id, key]) => { $(id).value = existing[key]; });
    $('autoScan').value = String(Boolean(existing.auto_scan));
    $('respectRobots').value = String(Boolean(existing.respect_robots));
  }

  $('sourceDialog').querySelector('.advanced').open = Boolean(existing);
  $('sourceDialog').showModal();
  $('sourceName').focus();
}

async function openDolKeyDialog() {
  $('dolKeyForm').reset();
  $('dolKeyMessage').textContent = '';
  $('dolApiKeyStatus').textContent = 'Checking current DOL API key status…';
  $('dolKeyDialog').showModal();
  try {
    const status = await api('/api/integrations/dol');
    $('dolApiKeyStatus').textContent = status.configured
      ? 'A DOL API key is already saved locally. Enter a new key only if you want to replace it; it will be tested before replacement.'
      : 'No DOL API key is saved yet. Paste the free key below; the app will test it before saving.';
  } catch (error) {
    $('dolApiKeyStatus').textContent = 'Could not check the current key status.';
    $('dolKeyMessage').textContent = error.message;
  }
  $('dolApiKey').focus();
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
    $('dolKeyDialog').close();
    $('dolKeyForm').reset();
    notify('DOL API key tested and saved locally. OSHA collection is ready.');
    state.sourceSignature = '';
    await refreshAll();
  } catch (error) {
    $('dolKeyMessage').textContent = error.message;
    localEvent('ERROR', 'DOL API key test failed: ' + error.message);
  } finally {
    button.disabled = false;
  }
}

async function openSamKeyDialog() {
  $('samKeyForm').reset();
  $('samKeyMessage').textContent = '';
  $('samApiKeyStatus').textContent = 'Checking current SAM.gov Alpha API key status…';
  $('samKeyDialog').showModal();
  try {
    const status = await api('/api/integrations/sam');
    $('samApiKeyStatus').textContent = status.configured
      ? 'A SAM.gov Alpha/test API key is already saved locally. Enter a new key only if you want to replace it; it will be tested before replacement.'
      : 'No SAM.gov Alpha/test API key is saved yet. Paste the test key below; the app will validate it against the official v4 Alpha Exclusions API.';
  } catch (error) {
    $('samApiKeyStatus').textContent = 'Could not check the current SAM API key status.';
    $('samKeyMessage').textContent = error.message;
  }
  $('samApiKey').focus();
}

async function saveSamApiKey(event) {
  event.preventDefault();
  const button = $('saveSamKey');
  const key = $('samApiKey').value.trim();
  if (!key) return;
  button.disabled = true;
  $('samKeyMessage').textContent = 'Testing key with the SAM.gov Alpha Exclusions API…';
  try {
    const result = await api('/api/integrations/sam', {method:'POST', body:JSON.stringify({api_key:key})});
    if (!result.validated) throw new Error('SAM.gov Alpha API key could not be validated.');
    $('samKeyDialog').close();
    $('samKeyForm').reset();
    notify('SAM.gov Alpha API key tested and saved locally. Federal debarment collection is ready.');
    state.sourceSignature = '';
    await refreshAll();
  } catch (error) {
    $('samKeyMessage').textContent = error.message;
    localEvent('ERROR', 'SAM.gov Alpha API key test failed: ' + error.message);
  } finally {
    button.disabled = false;
  }
}

async function addSource(event) {
  event.preventDefault();
  const button = $('saveSource');
  button.disabled = true;
  $('formMessage').textContent = state.editingId ? 'Saving collection settings…' : 'Checking this website…';
  try {
    const body = {name: $('sourceName').value.trim(), start_url: $('sourceUrl').value.trim(), auto_scan: $('autoScan').value === 'true', interval_minutes: +$('intervalMinutes').value, max_pages: +$('maxPages').value, max_depth: +$('maxDepth').value, concurrency: +$('concurrency').value, delay_ms: +$('delayMs').value, render_mode: $('renderMode').value, respect_robots: $('respectRobots').value === 'true'};
    if (state.editingId) delete body.start_url;
    await api(state.editingId ? '/api/sources/' + state.editingId : '/api/sources', {method:state.editingId ? 'PATCH' : 'POST', body: JSON.stringify(body)});
    $('sourceDialog').close();
    $('sourceForm').reset();
    notify(state.editingId ? 'Site settings saved. Existing research records are preserved.' : 'Research site saved. Choose Collect records to add its data to your local database.');
    await refreshAll();
  } catch (error) { $('formMessage').textContent = error.message; localEvent('ERROR', 'Adding source failed: ' + error.message); }
  finally { button.disabled = false; }
}
async function scan(id, full) {
  try {
    await api('/api/sources/' + id + '/scan', {method:'POST',body:JSON.stringify({force_full:full})});
    notify(full ? 'Full collection queued. Progress will appear in the console.' : 'Collection queued. Records will be saved to your local database.');
    await refreshAll();
  } catch (error) { notify(error.message, true); localEvent('ERROR', 'Could not start scan: ' + error.message); }
}
async function updateSites() {
  if (state.batchUpdating) return;
  const available = state.sources.filter(sourceReadyForCollection);
  if (!available.length) return;
  state.batchUpdating = true;
  updateCollectionButton();
  try {
    const results = await Promise.allSettled(available.map(s => api('/api/sources/' + s.id + '/scan', {method:'POST', body: JSON.stringify({force_full: false})})));
    const successful = results.filter(r => r.status === 'fulfilled').length;
    results.forEach((result, index) => { if (result.status === 'rejected') localEvent('ERROR', 'Collection could not start for ' + available[index].name + ': ' + result.reason.message); });
    notify(successful + ' site collection' + (successful === 1 ? '' : 's') + ' queued.' + (successful < results.length ? ' Check the console for sites that could not start.' : ' Saved records will update as collection proceeds.'), successful < results.length);
    await refreshAll();
  } finally {
    state.batchUpdating = false;
    updateCollectionButton();
  }
}
async function removeSource() {
  const button = $('confirmDelete');
  button.disabled = true;
  try { await api('/api/sources/' + state.deleteId, {method:'DELETE'}); $('deleteDialog').close(); notify('Source removed.'); state.offset = 0; await refreshAll(); }
  catch (error) { notify(error.message, true); }
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
    const params = recordParams({format});
    const response = await fetch('/api/bidder/export?' + params, {signal:AbortSignal.timeout(60000)});
    if (!response.ok) { const error = await response.json(); throw new Error(error.detail || 'Export failed'); }
    saveBlob(await response.blob(), 'bidder-database-' + new Date().toISOString().slice(0,10) + '.' + format);
    notify('Approved bidder database exported in the same 30-column layout.');
  } catch (error) { notify(error.message, true); localEvent('ERROR', 'Bidder export failed: ' + error.message); }
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
  catch { localEvent('WARNING', 'Offline snapshot captured in this browser. Export report to save it.'); notify('Offline snapshot captured. Export report to save it.'); }
  finally { $('snapshotButton').disabled = false; }
}
document.addEventListener('click', event => {
  const button = event.target.closest('button');
  if (!button) return;
  if (button.hasAttribute('data-open-source')) openSource(button.dataset.openSource, null);
  if (button.hasAttribute('data-dol-key')) openDolKeyDialog();
  if (button.hasAttribute('data-sam-key')) openSamKeyDialog();
  if (button.dataset.record) viewRecord(+button.dataset.record);
  if (button.dataset.edit) openSource(null, state.sources.find(s => s.id === +button.dataset.edit));
  if (button.dataset.close) $(button.dataset.close).close();
  if (button.dataset.scan) scan(+button.dataset.scan, false);
  if (button.dataset.full) scan(+button.dataset.full, true);
  if (button.dataset.delete) {
    state.deleteId = +button.dataset.delete;
    const source = state.sources.find(s => s.id === state.deleteId);
    $('deleteDescription').textContent = '“' + (source?.name || 'This source') + '” and its collected records and change history will be removed. This cannot be undone.';
    $('deleteDialog').showModal();
  }
  if (button.dataset.export) exportRecords(button.dataset.export, button);
  if (button.dataset.applyProposal) applyProposal(+button.dataset.applyProposal, button);
  if (button.dataset.dismissProposal) dismissProposal(+button.dataset.dismissProposal, button);
  if (button.dataset.logLevel) {
    state.level = button.dataset.logLevel;
    document.querySelectorAll('[data-log-level]').forEach(b => { b.classList.toggle('selected', b === button); b.setAttribute('aria-pressed', b === button); });
    renderLogs();
  }
});
$('sourceForm').addEventListener('submit', addSource);
$('dolKeyForm').addEventListener('submit', saveDolApiKey);
$('samKeyForm').addEventListener('submit', saveSamApiKey);
$('oshaApiKeyButton').addEventListener('click', openDolKeyDialog);
$('samApiKeyButton').addEventListener('click', openSamKeyDialog);
$('confirmDelete').addEventListener('click', removeSource);
$('refreshSources').addEventListener('click', refreshAll);
$('helpButton').addEventListener('click', () => $('helpDialog').showModal());
const search = () => { state.offset = 0; loadRecords().catch(e => notify(e.message,true)); };
$('recordSearchForm').addEventListener('submit', event => { event.preventDefault(); search(); });
$('fieldFilter').addEventListener('change', search);
function clearFilters() {
  $('search').value = '';
  $('fieldFilter').value = 'all';
}
$('clearFilters').addEventListener('click', () => { clearFilters(); search(); $('search').focus(); });
$('findRecords').addEventListener('click', () => { $('recordsSection').scrollIntoView({block:'start'}); $('search').focus({preventScroll:true}); });
$('reviewOsha').addEventListener('click', () => {
  clearFilters();
  $('fieldFilter').value = 'osha';
  $('search').value = 'Y';
  search();
  $('recordsSection').scrollIntoView({block:'start'});
  $('search').focus({preventScroll:true});
  notify('Showing master bidder rows where the OSHA field contains Y.');
});
$('uploadCsvButton').addEventListener('click', () => $('csvUpload').click());
$('csvUpload').addEventListener('change', () => importBidderCsv($('csvUpload').files?.[0]));
$('compareButton').addEventListener('click', compareBidderData);
$('refreshProposals').addEventListener('click', async () => { state.proposalSignature = ''; await Promise.all([loadBidderStatus(), loadProposals()]); });
$('updateSites').addEventListener('click', updateSites);
$('prevPage').addEventListener('click', () => { state.offset = Math.max(0, state.offset - PAGE_SIZE); loadRecords().catch(e => notify(e.message,true)); });
$('nextPage').addEventListener('click', () => { state.offset += PAGE_SIZE; loadRecords().catch(e => notify(e.message,true)); });
$('logSearch').addEventListener('input', renderLogs);
$('pauseLogs').addEventListener('click', () => {
  state.paused = !state.paused;
  $('pauseLogs').textContent = state.paused ? '▶' : 'Ⅱ';
  $('pauseLogs').setAttribute('aria-label', state.paused ? 'Resume live activity' : 'Pause live activity');
  $('consoleLive').className = 'live-label' + (state.paused ? ' paused' : '');
  $('consoleLive').innerHTML = '<i></i>' + (state.paused ? 'PAUSED' : 'LIVE');
  renderLogs();
  if (!state.paused) loadLogs().catch(() => {});
});
$('autoScroll').addEventListener('click', () => { state.follow = !state.follow; $('autoScroll').setAttribute('aria-pressed',state.follow); renderLogs(); });
$('snapshotButton').addEventListener('click', captureSnapshot);
$('exportLogs').addEventListener('click', exportLogs);
window.addEventListener('error', e => localEvent('ERROR', 'Browser error: ' + e.message));
window.addEventListener('unhandledrejection', e => localEvent('ERROR', 'Browser operation failed: ' + (e.reason?.message || 'Unknown error')));
async function poll() { await refreshAll(); setTimeout(poll, 2500); }
poll();
