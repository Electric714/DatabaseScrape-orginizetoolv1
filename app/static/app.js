const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeUrl = value => { try { const u = new URL(value); return ['http:', 'https:'].includes(u.protocol) ? u.href : '#'; } catch { return '#'; } };
const state = {sources: [], sourceSignature: '', jobsSignature: '', events: [], cursor: 0, paused: false, follow: true, level: 'all', offset: 0, total: 0, refreshing: false, online: null, deleteId: null, recordSequence: 0, detailSequence: 0, batchUpdating: false, editingId: null};
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
function updateCollectionButton() {
  const available = state.sources.filter(s => !isCollecting(s)).length;
  $('updateSites').disabled = state.batchUpdating || available === 0;
  $('updateSites').textContent = state.batchUpdating ? 'Queuing collections…' : 'Update configured sites';
  $('updateSites').title = available ? 'Collect updated records from ' + available + ' available research sites' : (state.sources.length ? 'All configured sites are already collecting' : 'Set up a research site first');
}
async function loadSources() {
  const sources = await api('/api/sources');
  state.sources = sources;
  $('sourceCount').textContent = sources.length < 6 ? sources.length + ' / 6' : sources.length + ' configured';
  $('navSources').textContent = sources.length < 6 ? sources.length + '/6' : sources.length;
  $('sitesHint').textContent = sources.length < 6 ? (6 - sources.length) + ' site' + (sources.length === 5 ? '' : 's') + ' awaiting setup' : 'Ready for collection and source review';
  $('siteSetupNote').textContent = sources.length < 6 ? 'Six research sites are planned. Their names and addresses will be filled in when available.' : 'Collect from each site, then search its saved records above.';
  updateCollectionButton();
  const signature = JSON.stringify(sources);
  if (signature === state.sourceSignature) return;
  state.sourceSignature = signature;
  const selection = $('sourceFilter').value;
  $('sourceFilter').innerHTML = '<option value="">All research sites</option>' + sources.map(s => '<option value="' + s.id + '">' + esc(s.name) + '</option>').join('');
  $('sourceFilter').value = sources.some(s => String(s.id) === selection) ? selection : '';
  const configured = sources.map((s, index) => {
    const running = isCollecting(s);
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
  for (const [key, id] of [['sources','statSources'],['records','statRecords'],['changed_24h','statChanged'],['running_jobs','statRunning']]) $(id).textContent = stats[key].toLocaleString();
  $('runningHint').textContent = stats.running_jobs ? 'Saving information from research sites' : 'Ready to collect from configured sites';
}
async function loadJobs() {
  const jobs = await api('/api/jobs?limit=8');
  const signature = JSON.stringify(jobs);
  if (signature === state.jobsSignature) return;
  state.jobsSignature = signature;
  $('jobs').innerHTML = jobs.length ? jobs.map(j => '<article class="job"><div class="job-head"><strong>' + esc(j.source_name) + '</strong><span class="tag ' + esc(j.status) + '">' + esc(j.status) + '</span></div><span class="job-time">' + esc(humanDate(j.started_at, true)) + '</span><p class="job-message">' + esc(j.message || 'Waiting to begin…') + '</p><div class="job-stats"><span><b>' + j.pages_processed + '/' + j.pages_discovered + '</b> pages</span><span><b>' + j.records_found + '</b> found</span><span><b>' + j.records_new + '</b> new</span><span><b>' + j.records_updated + '</b> updated</span><span><b>' + j.errors + '</b> errors</span></div></article>').join('') : '<div class="job"><p class="job-message">No collections yet. Set up a research site, then choose Collect records.</p></div>';
}
function recordParams(extra = {}) {
  const params = new URLSearchParams({...extra, q: $('search').value.trim(), field: $('fieldFilter').value});
  if ($('sourceFilter').value) params.set('source_id', $('sourceFilter').value);
  if ($('oshaFilter').value) params.set('osha_status', $('oshaFilter').value);
  return params;
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
  const data = await api('/api/bidder-records?' + params);
  if (sequence !== state.recordSequence) return;
  state.total = data.total;
  if (state.offset && state.offset >= data.total) { state.offset = 0; return loadRecords(); }
  const filtered = params.get('q') || params.get('source_id') || params.get('osha_status');
  $('recordCount').textContent = data.total.toLocaleString() + ' bidder database records' + (filtered ? ' matching your filters.' : ' saved locally.');
  $('pageInfo').textContent = data.total ? (state.offset + 1) + '–' + (state.offset + data.items.length) + ' of ' + data.total.toLocaleString() : 'No matching saved records';
  $('prevPage').disabled = state.offset === 0;
  $('nextPage').disabled = state.offset + PAGE_SIZE >= data.total;
  $('records').innerHTML = data.items.length ? data.items.map(r => {
    const cells = BIDDER_COLUMNS.map(column => {
      const value = r[column] ?? '';
      if (column === 'contractor_name') {
        return '<td><button class="record-button bidder-record-link" data-record="' + esc(r._record_id) + '" aria-label="View source evidence for ' + esc(value || 'this contractor') + '">' + esc(value || '—') + '</button></td>';
      }
      return '<td>' + esc(value === '' ? '—' : value) + '</td>';
    }).join('');
    return '<tr>' + cells + '</tr>';
  }).join('') : '<tr><td colspan="' + BIDDER_COLUMNS.length + '" class="table-empty"><strong>' + (filtered ? 'No bidder records match these filters.' : 'Your bidder database is ready.') + '</strong><p>' + (filtered ? 'Try another term or clear the filters.' : 'Set up a research site below and choose Collect records. Fields that a source does not provide will remain blank.') + '</p></td></tr>';
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
    const results = await Promise.allSettled([loadSources(), loadStats(), loadJobs(), loadRecords(), loadLogs()]);
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
  $('sourceDialog').querySelector('.modal-intro').textContent = existing ? 'Update the name, schedule, or collection limits for this site. Its saved research records are preserved.' : 'Connect one of your public-record research websites. Records collected from it are saved locally.';
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
  const available = state.sources.filter(s => !isCollecting(s));
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
    const response = await fetch('/api/export?' + params, {signal:AbortSignal.timeout(60000)});
    if (!response.ok) { const error = await response.json(); throw new Error(error.detail || 'Export failed'); }
    saveBlob(await response.blob(), 'research-records-' + new Date().toISOString().slice(0,10) + '.' + format);
    notify('Matching research records exported with their source details.');
  } catch (error) { notify(error.message, true); localEvent('ERROR', 'Record export failed: ' + error.message); }
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
  if (button.hasAttribute('data-open-source')) openSource(button.dataset.openSource);
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
  if (button.dataset.logLevel) {
    state.level = button.dataset.logLevel;
    document.querySelectorAll('[data-log-level]').forEach(b => { b.classList.toggle('selected', b === button); b.setAttribute('aria-pressed', b === button); });
    renderLogs();
  }
});
$('sourceForm').addEventListener('submit', addSource);
$('confirmDelete').addEventListener('click', removeSource);
$('refreshSources').addEventListener('click', refreshAll);
$('helpButton').addEventListener('click', () => $('helpDialog').showModal());
const search = () => { state.offset = 0; loadRecords().catch(e => notify(e.message,true)); };
$('recordSearchForm').addEventListener('submit', event => { event.preventDefault(); search(); });
['sourceFilter', 'fieldFilter', 'oshaFilter'].forEach(id => $(id).addEventListener('change', search));
function clearFilters() {
  $('search').value = '';
  $('fieldFilter').value = 'all';
  $('sourceFilter').value = '';
  $('oshaFilter').value = '';
}
$('clearFilters').addEventListener('click', () => { clearFilters(); search(); $('search').focus(); });
$('findRecords').addEventListener('click', () => { $('recordsSection').scrollIntoView({block:'start'}); $('search').focus({preventScroll:true}); });
$('reviewOsha').addEventListener('click', () => {
  clearFilters();
  $('oshaFilter').value = 'open';
  search();
  $('recordsSection').scrollIntoView({block:'start'});
  $('oshaFilter').focus({preventScroll:true});
  notify('Showing saved records with an open OSHA status explicitly reported by their source.');
});
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
