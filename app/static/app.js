const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let sources = [];
const safeUrl = (v) => { try { const u = new URL(v); return ['http:', 'https:'].includes(u.protocol) ? u.href : '#'; } catch (_) { return '#'; } };

async function api(path, options = {}) {
  const res = await fetch(path, {...options, headers: {'Content-Type':'application/json', ...(options.headers || {})}, signal: AbortSignal.timeout(30000)});
  if (!res.ok) {
    let detail = res.status + ' ' + res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (res.status === 204) return null;
  return res.json();
}

function fmtDate(v) {
  if (!v) return '—';
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? v : d.toLocaleString();
}

async function checkHealth() {
  try { await api('/api/health'); $('health').textContent = 'Backend online'; $('health').className = 'pill good'; }
  catch (_) { $('health').textContent = 'Backend offline'; $('health').className = 'pill'; }
}

async function loadStats() {
  const s = await api('/api/stats');
  $('statSources').textContent = s.sources.toLocaleString();
  $('statRecords').textContent = s.records.toLocaleString();
  $('statChanged').textContent = s.changed_24h.toLocaleString();
  $('statRunning').textContent = s.running_jobs.toLocaleString();
}

async function loadSources() {
  sources = await api('/api/sources');
  const root = $('sources');
  const selection = $('sourceFilter').value;
  $('sourceFilter').innerHTML = '<option value="">All sources</option>' + sources.map((s) => '<option value="' + s.id + '">' + esc(s.name) + '</option>').join('');
  $('sourceFilter').value = sources.some(s => String(s.id) === selection) ? selection : '';
  if (!sources.length) { root.className = 'source-grid empty'; root.textContent = 'No sources yet.'; return; }
  root.className = 'source-grid';
  root.innerHTML = sources.map((s) =>
    '<div class="source-card">' +
      '<div class="source-top"><div><h3>' + esc(s.name) + '</h3><div class="url">' + esc(s.start_url) + '</div></div><span class="pill">' + (s.auto_scan ? 'AUTO' : 'MANUAL') + '</span></div>' +
      '<div class="source-meta"><div><span>Last scan</span><strong>' + esc(fmtDate(s.last_scan_at)) + '</strong></div><div><span>Last new</span><strong>' + (s.last_new ?? '—') + '</strong></div><div><span>Last updated</span><strong>' + (s.last_updated ?? '—') + '</strong></div></div>' +
      '<div class="actions"><button class="primary" data-scan="' + s.id + '">Scan</button><button class="ghost" data-full="' + s.id + '">Force full</button><button class="danger" data-delete="' + s.id + '">Delete</button></div>' +
    '</div>'
  ).join('');
}

async function addSource(e) {
  e.preventDefault();
  $('formMessage').textContent = 'Adding source…';
  const body = {
    name: $('sourceName').value.trim(), start_url: $('sourceUrl').value.trim(),
    auto_scan: $('autoScan').value === 'true', interval_minutes: +$('intervalMinutes').value,
    max_pages: +$('maxPages').value, max_depth: +$('maxDepth').value,
    concurrency: +$('concurrency').value, delay_ms: +$('delayMs').value,
    render_mode: $('renderMode').value, respect_robots: $('respectRobots').value === 'true'
  };
  try {
    await api('/api/sources', {method:'POST', body:JSON.stringify(body)});
    $('formMessage').textContent = 'Source added.';
    $('sourceForm').reset();
    $('maxPages').value = 5000; $('maxDepth').value = 12; $('concurrency').value = 6;
    $('delayMs').value = 350; $('intervalMinutes').value = 60;
    await refreshAll();
  } catch (e2) { $('formMessage').textContent = e2.message; }
}

async function scan(id, forceFull) {
  try { await api('/api/sources/' + id + '/scan', {method:'POST', body:JSON.stringify({force_full: forceFull})}); await refreshAll(); }
  catch (e) { alert(e.message); }
}

async function removeSource(id) {
  if (!confirm('Delete this source and all collected records/history?')) return;
  try { await api('/api/sources/' + id, {method:'DELETE'}); await refreshAll(); }
  catch (e) { alert(e.message); }
}

async function loadJobs() {
  const jobs = await api('/api/jobs?limit=30');
  const root = $('jobs');
  if (!jobs.length) { root.className = 'jobs empty'; root.textContent = 'No crawl jobs yet.'; return; }
  root.className = 'jobs';
  root.innerHTML = jobs.map((j) =>
    '<div class="job"><div><strong>' + esc(j.source_name) + '</strong><small class="status-' + esc(j.status) + '">' + esc(j.status) + ' · ' + esc(fmtDate(j.started_at)) + '</small><small>' + esc(j.message || '') + '</small></div>' +
    '<span><small>Pages</small>' + j.pages_processed + '/' + j.pages_discovered + '</span>' +
    '<span><small>Found</small>' + j.records_found + '</span><span><small>New</small>' + j.records_new + '</span>' +
    '<span><small>Updated</small>' + j.records_updated + '</span><span><small>Errors</small>' + j.errors + '</span></div>'
  ).join('');
}

async function loadRecords() {
  const q = encodeURIComponent($('search').value.trim());
  const sid = $('sourceFilter').value;
  const params = 'q=' + q + (sid ? '&source_id=' + sid : '') + '&limit=250';
  const data = await api('/api/records?' + params);
  $('recordCount').textContent = data.total.toLocaleString() + ' matching records (showing up to 250; exports include all matches)';
  const root = $('records');
  if (!data.items.length) { root.innerHTML = '<tr><td colspan="7" class="muted">No matching records.</td></tr>'; return; }
  root.innerHTML = data.items.map((r) =>
    '<tr><td>' + esc(r.name || '—') + '</td><td>' + esc(r.company || '—') + '</td><td>' + esc(r.phone || '—') + '</td><td>' + esc(r.address || '—') + '</td><td>' + esc(r.date || '—') + '</td>' +
    '<td><a href="' + esc(safeUrl(r.source_url)) + '" target="_blank" rel="noreferrer">' + esc(r.source_name) + '</a></td><td>' + esc(fmtDate(r.last_changed)) + '</td></tr>'
  ).join('');
}

function exportData(fmt) {
  const q = encodeURIComponent($('search').value.trim());
  const sid = $('sourceFilter').value;
  window.location.href = '/api/export?format=' + fmt + '&q=' + q + (sid ? '&source_id=' + sid : '');
}

async function refreshAll() { try { await loadSources(); await Promise.all([checkHealth(), loadStats(), loadJobs(), loadRecords()]); } catch (e) { $('formMessage').textContent = e.message; } }
$('sourceForm').addEventListener('submit', addSource);
$('refreshSources').addEventListener('click', refreshAll);
$('searchBtn').addEventListener('click', loadRecords);
$('search').addEventListener('keydown', (e) => { if (e.key === 'Enter') loadRecords(); });
$('sourceFilter').addEventListener('change', loadRecords);
document.querySelectorAll('[data-export]').forEach((b) => b.addEventListener('click', () => exportData(b.dataset.export)));
$('sources').addEventListener('click', (event) => {
  const b = event.target.closest('button');
  if (!b) return;
  if (b.dataset.scan) scan(+b.dataset.scan, false);
  if (b.dataset.full) scan(+b.dataset.full, true);
  if (b.dataset.delete) removeSource(+b.dataset.delete);
});
async function poll() { await refreshAll(); setTimeout(poll, 3000); }
poll();
