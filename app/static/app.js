const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeUrl = value => { try { const u = new URL(value); return ['http:', 'https:'].includes(u.protocol) ? u.href : '#'; } catch { return '#'; } };
const state = {sources: [], sourceSignature: '', jobsSignature: '', events: [], cursor: 0, paused: false, follow: true, level: 'all', offset: 0, total: 0, refreshing: false, online: null, deleteId: null, recordSequence: 0};
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
  if (!value) return 'Not scanned yet';
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
async function loadSources() {
  const sources = await api('/api/sources');
  state.sources = sources;
  $('sourceCount').textContent = sources.length;
  $('navSources').textContent = sources.length;
  const signature = JSON.stringify(sources);
  if (signature === state.sourceSignature) return;
  state.sourceSignature = signature;
  const selection = $('sourceFilter').value;
  $('sourceFilter').innerHTML = '<option value="">All sources</option>' + sources.map(s => '<option value="' + s.id + '">' + esc(s.name) + '</option>').join('');
  $('sourceFilter').value = sources.some(s => String(s.id) === selection) ? selection : '';
  if (!sources.length) {
    $('sources').innerHTML = '<div class="empty-state"><div class="empty-orb">◎</div><h3>Your next discovery starts here.</h3><p>Connect a public website to begin collecting<br>and following the information that matters.</p><button class="button subtle small" data-open-source>＋ Add your first website</button></div>';
    return;
  }
  $('sources').innerHTML = '<div class="source-list">' + sources.map(s => {
    const running = ['queued','running'].includes(s.last_status);
    return '<article class="source-row"><div class="source-avatar">' + esc(s.name.charAt(0).toUpperCase()) + '</div><div class="source-info"><h3>' + esc(s.name) + '</h3><a class="source-url" href="' + esc(safeUrl(s.start_url)) + '" target="_blank" rel="noreferrer">' + esc(s.start_url) + '</a><div class="source-details"><span class="tag ' + esc(s.last_status || '') + '">' + esc(s.last_status || 'Ready') + '</span><span>' + (s.auto_scan ? 'Every ' + s.interval_minutes + ' min' : 'Manual') + '</span><span>· ' + esc(humanDate(s.last_scan_at)) + '</span></div></div><div class="source-controls"><button class="scan-button" data-scan="' + s.id + '"' + (running ? ' disabled' : '') + '>' + (running ? 'Scanning…' : 'Scan') + '</button><button class="icon-button" data-full="' + s.id + '" aria-label="Full scan of ' + esc(s.name) + '" title="Full scan: ignore cached pages"' + (running ? ' disabled' : '') + '>↻</button><button class="icon-button" data-delete="' + s.id + '" aria-label="Delete ' + esc(s.name) + '" title="Remove source"' + (running ? ' disabled' : '') + '>×</button></div></article>';
  }).join('') + '</div>';
}
async function loadStats() {
  const stats = await api('/api/stats');
  for (const [key, id] of [['sources','statSources'],['records','statRecords'],['changed_24h','statChanged'],['running_jobs','statRunning']]) $(id).textContent = stats[key].toLocaleString();
  $('runningHint').textContent = stats.running_jobs ? 'Following the latest information' : 'Ready when you are';
}
async function loadJobs() {
  const jobs = await api('/api/jobs?limit=8');
  const signature = JSON.stringify(jobs);
  if (signature === state.jobsSignature) return;
  state.jobsSignature = signature;
  $('jobs').innerHTML = jobs.length ? jobs.map(j => '<article class="job"><div class="job-head"><strong>' + esc(j.source_name) + '</strong><span class="tag ' + esc(j.status) + '">' + esc(j.status) + '</span></div><span class="job-time">' + esc(humanDate(j.started_at, true)) + '</span><p class="job-message">' + esc(j.message || 'Waiting to begin…') + '</p><div class="job-stats"><span><b>' + j.pages_processed + '/' + j.pages_discovered + '</b> pages</span><span><b>' + j.records_found + '</b> found</span><span><b>' + j.records_new + '</b> new</span><span><b>' + j.records_updated + '</b> updated</span><span><b>' + j.errors + '</b> errors</span></div></article>').join('') : '<div class="job"><p class="job-message">No scans yet. Choose Scan on a source when you’re ready.</p></div>';
}
async function loadRecords() {
  const sequence = ++state.recordSequence;
  const params = new URLSearchParams({q: $('search').value.trim(), limit: PAGE_SIZE, offset: state.offset});
  if ($('sourceFilter').value) params.set('source_id', $('sourceFilter').value);
  const data = await api('/api/records?' + params);
  if (sequence !== state.recordSequence) return;
  state.total = data.total;
  if (state.offset && state.offset >= data.total) { state.offset = 0; return loadRecords(); }
  $('recordCount').textContent = data.total.toLocaleString() + ' records' + (params.get('q') || params.get('source_id') ? ' matching your filters.' : ', organized and ready to explore.');
  $('pageInfo').textContent = data.total ? (state.offset + 1) + '–' + (state.offset + data.items.length) + ' of ' + data.total.toLocaleString() : 'No matching records';
  $('prevPage').disabled = state.offset === 0;
  $('nextPage').disabled = state.offset + PAGE_SIZE >= data.total;
  $('records').innerHTML = data.items.length ? data.items.map(r => '<tr><td><strong>' + esc(r.name || r.company || 'Unnamed record') + '</strong>' + (r.name && r.company ? '<small>' + esc(r.company) + '</small>' : '') + (!r.active ? '<span class="tag partial">Not seen recently</span>' : '') + '</td><td>' + esc(r.phone || '—') + '</td><td>' + esc(r.address || '—') + '</td><td>' + esc(r.date || '—') + '</td><td><a href="' + esc(safeUrl(r.source_url)) + '" target="_blank" rel="noreferrer">' + esc(r.source_name) + ' ↗</a></td><td>' + esc(humanDate(r.last_changed)) + '</td></tr>').join('') : '<tr><td colspan="6" class="table-empty">' + (params.get('q') ? 'No matches. Try a different name or keyword.' : 'Your collected records will appear here after a scan.') + '</td></tr>';
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
  }).join('') : '<div class="console-empty"><span>›_</span><strong>' + (search || state.level !== 'all' ? 'Nothing here matches.' : 'Ready to listen.') + '</strong><p>' + (search || state.level !== 'all' ? 'Try another filter or search.' : 'Startup, scans, updates, and errors<br>appear here automatically.') + '</p></div>';
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
function openSource() { $('formMessage').textContent = ''; $('sourceDialog').showModal(); $('sourceName').focus(); }
async function addSource(event) {
  event.preventDefault();
  const button = $('saveSource');
  button.disabled = true;
  $('formMessage').textContent = 'Checking this website…';
  try {
    const body = {name: $('sourceName').value.trim(), start_url: $('sourceUrl').value.trim(), auto_scan: $('autoScan').value === 'true', interval_minutes: +$('intervalMinutes').value, max_pages: +$('maxPages').value, max_depth: +$('maxDepth').value, concurrency: +$('concurrency').value, delay_ms: +$('delayMs').value, render_mode: $('renderMode').value, respect_robots: $('respectRobots').value === 'true'};
    await api('/api/sources', {method:'POST', body: JSON.stringify(body)});
    $('sourceDialog').close();
    $('sourceForm').reset();
    notify('Source added. You’re ready to start a scan.');
    await refreshAll();
  } catch (error) { $('formMessage').textContent = error.message; localEvent('ERROR', 'Adding source failed: ' + error.message); }
  finally { button.disabled = false; }
}
async function scan(id, full) {
  try {
    await api('/api/sources/' + id + '/scan', {method:'POST',body:JSON.stringify({force_full:full})});
    notify(full ? 'Full scan queued. Follow its progress in the console.' : 'Scan queued. Follow its progress in the console.');
    await refreshAll();
  } catch (error) { notify(error.message, true); localEvent('ERROR', 'Could not start scan: ' + error.message); }
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
    const params = new URLSearchParams({format, q:$('search').value.trim()});
    if ($('sourceFilter').value) params.set('source_id', $('sourceFilter').value);
    const response = await fetch('/api/export?' + params, {signal:AbortSignal.timeout(60000)});
    if (!response.ok) { const error = await response.json(); throw new Error(error.detail || 'Export failed'); }
    saveBlob(await response.blob(), 'records-' + new Date().toISOString().slice(0,10) + '.' + format);
    notify('Records exported.');
  } catch (error) { notify(error.message, true); localEvent('ERROR', 'Record export failed: ' + error.message); }
  finally { button.disabled = false; }
}
async function exportLogs() {
  $('exportLogs').disabled = true;
  try {
    const response = await fetch('/api/activity/export', {signal:AbortSignal.timeout(15000)});
    if (!response.ok) throw new Error('Diagnostic export failed');
    saveBlob(await response.blob(), 'public-data-monitor-diagnostics.zip');
    notify('Diagnostic report downloaded. Review it before sharing.');
    await loadLogs();
  } catch {
    saveBlob(new Blob([JSON.stringify({note:'Backend unavailable. Browser-visible events only.',created_at:new Date().toISOString(),events:state.events}, null, 2)], {type:'application/json'}), 'public-data-monitor-offline-logs.json');
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
  if (button.hasAttribute('data-open-source')) openSource();
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
$('searchBtn').addEventListener('click', search);
$('search').addEventListener('keydown', e => { if (e.key === 'Enter') search(); });
$('sourceFilter').addEventListener('change', search);
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
