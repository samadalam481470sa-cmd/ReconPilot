/* ReconPilot dashboard — zero-build vanilla JavaScript client. */
'use strict';

const $ = (id) => document.getElementById(id);
const state = { view: 'overview', summary: null, transactionsPage: 0, exceptionsPage: 0, transactionsTotal: 0, exceptionsTotal: 0, exceptionRecords: [], currentDetail: null };
const pageSize = 12;
const statusNames = { matched: 'Matched', amount_mismatch: 'Amount mismatch', currency_mismatch: 'Currency mismatch', missing_settlement: 'Missing settlement', pending: 'Pending', failed: 'Failed' };
const statusColors = { matched: '#11aa86', amount_mismatch: '#f1b762', currency_mismatch: '#e88971', missing_settlement: '#e5a448', pending: '#7694d8', failed: '#b5bbc7' };

const money = (cents, compact = false) => {
  const value = Number(cents || 0) / 100;
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: compact ? (value >= 1e6 ? 2 : 1) : 2, notation: compact ? 'compact' : 'standard' }).format(value);
};
const clean = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmtDate = (value, long = false) => {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return '—';
  return new Intl.DateTimeFormat('en-US', long ? { dateStyle: 'medium', timeStyle: 'short' } : { month: 'short', day: 'numeric', year: 'numeric' }).format(date);
};
const badge = (status) => `<span class="status-badge status-${clean(status)}">${clean(statusNames[status] || status)}</span>`;

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { ...(options.headers || {}) } });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try { const error = await response.json(); detail = typeof error.detail === 'string' ? error.detail : detail; } catch (_) { /* use fallback */ }
    throw new Error(detail);
  }
  return response.json();
}
let toastTimer;
function toast(message, isError = false) {
  const element = $('toast');
  element.textContent = message;
  element.className = `toast visible${isError ? ' error' : ''}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { element.className = 'toast'; }, 4000);
}
function handleError(error) { console.error(error); toast(error.message || 'Something went wrong', true); }

const pages = {
  overview: ['Overview', 'A clearer picture of every payment, settlement, and exception.'],
  transactions: ['Transactions', 'Search and inspect payment activity across your ledger.'],
  exceptions: ['Exceptions', 'Find reconciliation breaks and track operational review.'],
  audit: ['Audit trail', 'A transparent log of imports and analyst review actions.'],
};
function navigate(view) {
  if (!pages[view]) view = 'overview';
  state.view = view;
  document.querySelectorAll('.nav-item').forEach((btn) => btn.classList.toggle('active', btn.dataset.view === view));
  document.querySelectorAll('.view').forEach((section) => section.classList.toggle('active', section.id === `view-${view}`));
  $('pageTitle').innerHTML = `${clean(pages[view][0])} <span class="title-period">.</span>`;
  $('pageDescription').textContent = pages[view][1];
  $('currentBreadcrumb').textContent = pages[view][0];
  window.location.hash = view;
  closeMenu();
  if (view === 'transactions') loadTransactions().catch(handleError);
  if (view === 'exceptions') loadExceptions().catch(handleError);
  if (view === 'audit') loadAudit().catch(handleError);
}
function closeMenu() { $('sidebar').classList.remove('open'); $('backdrop').hidden = true; }

async function loadSummary() {
  const summary = await api('/api/summary');
  state.summary = summary;
  const t = summary.totals;
  $('metricProcessed').textContent = money(t.processed_volume_cents, true);
  $('metricMatchRate').textContent = `${t.match_rate_pct.toFixed(1)}%`;
  $('metricExceptions').textContent = t.open_exceptions.toLocaleString();
  $('metricExposure').textContent = money(t.unmatched_volume_cents, true);
  $('highRiskCount').textContent = `${t.high_risk_open} high priority`;
  $('navExceptionCount').textContent = t.open_exceptions;
  $('exceptionsViewCount').textContent = Object.entries(summary.status_counts).filter(([key]) => ['amount_mismatch', 'currency_mismatch', 'missing_settlement'].includes(key)).reduce((s, [, n]) => s + n, 0);
  $('exceptionsViewHigh').textContent = t.high_risk_open;
  $('exceptionsViewVolume').textContent = money(t.unmatched_volume_cents, true);
  renderChart(summary.daily_volume);
  renderDistribution(summary.status_counts);
  renderRecentExceptions(summary.recent_exceptions);
  renderMerchants(summary.merchants);
}
function renderChart(points) {
  const container = $('volumeChart');
  if (!points.length) { container.textContent = 'No data yet'; return; }
  const width = 720, height = 230, L = 60, R = 15, T = 12, B = 35;
  const max = Math.max(...points.map((p) => p.volume_cents), 1) * 1.16;
  const x = (i) => L + (width - L - R) * i / Math.max(points.length - 1, 1);
  const y = (v) => T + (height - T - B) * (1 - v / max);
  const coords = (key) => points.map((p, i) => [x(i), y(p[key])]);
  const path = (list) => list.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
  const processed = coords('volume_cents');
  const matched = coords('matched_cents');
  const area = `${path(processed)} L${x(points.length - 1)},${height - B} L${x(0)},${height - B} Z`;
  const guides = Array.from({ length: 5 }, (_, i) => {
    const val = max * (4 - i) / 4;
    const yy = y(val);
    return `<line class="gridline" x1="${L}" x2="${width - R}" y1="${yy}" y2="${yy}"/><text class="axis-label" x="${L - 12}" y="${yy + 3}" text-anchor="end">${clean(money(val, true))}</text>`;
  }).join('');
  const labels = points.map((p, i) => i % 2 === 0 || i === points.length - 1 ? `<text class="axis-label" x="${x(i)}" y="${height - 9}" text-anchor="middle">${clean(new Date(p.date + 'T12:00:00Z').toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }))}</text>` : '').join('');
  const dots = matched.map((p, i) => `<circle class="volume-dot" cx="${p[0]}" cy="${p[1]}" r="3"><title>${clean(points[i].date)}: reconciled ${clean(money(points[i].matched_cents))}; processed ${clean(money(points[i].volume_cents))}</title></circle>`).join('');
  container.innerHTML = `<svg role="img" aria-label="Fourteen-day chart showing processed and reconciled payment volumes" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none"><defs><linearGradient id="chartGradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#30bea3" stop-opacity="0.16"/><stop offset="1" stop-color="#30bea3" stop-opacity="0"/></linearGradient></defs>${guides}<path class="chart-fill" d="${area}"/><path class="line-processed" d="${path(processed)}"/><path class="line-matched" d="${path(matched)}"/>${dots}${labels}</svg>`;
}
function renderDistribution(counts) {
  const order = ['matched', 'amount_mismatch', 'currency_mismatch', 'missing_settlement', 'pending', 'failed'];
  const total = order.reduce((n, status) => n + (counts[status] || 0), 0);
  let progress = 0;
  const segments = order.map((status) => {
    const size = total ? (counts[status] || 0) / total * 100 : 0;
    const seg = `${statusColors[status]} ${progress.toFixed(2)}% ${(progress + size).toFixed(2)}%`;
    progress += size;
    return seg;
  });
  $('statusDonut').style.background = `conic-gradient(${segments.join(',')})`;
  $('donutPercent').textContent = `${total ? Math.round(counts.matched / total * 100) : 0}%`;
  $('statusLegend').innerHTML = order.map((status) => `<div class="status-item"><div class="left"><span class="status-swatch" style="background:${statusColors[status]}"></span>${clean(statusNames[status])}</div><strong>${(counts[status] || 0).toLocaleString()}</strong></div>`).join('');
}
function renderRecentExceptions(items) {
  $('recentExceptions').innerHTML = items.length ? items.map((item) => `<div class="exception-row"><div class="exception-left"><div class="exception-symbol">!</div><div><strong>${clean(item.merchant)}</strong><small>${clean(statusNames[item.status])} · ${clean(item.id)}</small></div></div><div class="exception-right"><strong>${money(item.amount_cents)}</strong><button data-detail="${clean(item.id)}">Review ↗</button></div></div>`).join('') : '<div class="loading-inline">All caught up. No open exceptions.</div>';
}
function renderMerchants(items) {
  const max = Math.max(...items.map((item) => item.volume_cents), 1);
  $('merchantChart').innerHTML = items.slice(0, 6).map((item) => `<div class="merchant-row"><span class="merchant-name" title="${clean(item.name)}">${clean(item.name)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.max(2, item.volume_cents / max * 100).toFixed(2)}%"></div></div><span class="merchant-value">${money(item.volume_cents, true)}</span></div>`).join('');
}
function transactionRow(item, exception = false) {
  return `<tr><td><span class="cell-primary">${clean(item.id)}</span><span class="cell-secondary">${fmtDate(item.created_at)}</span></td><td>${clean(item.merchant)}</td><td><span class="money-cell">${money(item.amount_cents)}</span><span class="cell-secondary">${clean(item.currency)} · ${clean(item.channel)}</span></td><td>${badge(item.status)}</td><td><span class="risk-label risk-${clean(item.risk)}">● ${clean(item.risk)}</span></td><td>${exception ? `<button class="row-action" data-detail="${clean(item.id)}">${item.reviewed ? 'Reviewed ✓' : 'Review ↗'}</button>` : `<button class="row-action" data-detail="${clean(item.id)}">Details ↗</button>`}</td></tr>`;
}
async function loadTransactions() {
  const params = new URLSearchParams({ status: $('txStatus').value, q: $('txSearch').value, limit: String(pageSize), offset: String(state.transactionsPage * pageSize) });
  const data = await api(`/api/transactions?${params}`);
  state.transactionsTotal = data.total;
  $('transactionsBody').innerHTML = data.items.length ? data.items.map((item) => transactionRow(item)).join('') : '<tr><td colspan="6" class="empty-state">No transactions matched your filters.</td></tr>';
  $('transactionCount').textContent = `${data.total.toLocaleString()} records`;
  const from = data.total ? state.transactionsPage * pageSize + 1 : 0;
  $('txPageInfo').textContent = `Showing ${from}–${Math.min((state.transactionsPage + 1) * pageSize, data.total)} of ${data.total}`;
  $('txPrev').disabled = !state.transactionsPage;
  $('txNext').disabled = (state.transactionsPage + 1) * pageSize >= data.total;
}
async function loadExceptions() {
  const result = await api(`/api/transactions?status=exceptions&q=${encodeURIComponent($('exSearch').value)}&limit=500`);
  state.exceptionRecords = result.items.filter((item) => $('exFilter').value === 'all' || ($('exFilter').value === 'open' ? !item.reviewed : item.reviewed));
  const items = state.exceptionRecords.slice(state.exceptionsPage * pageSize, (state.exceptionsPage + 1) * pageSize);
  $('exceptionsBody').innerHTML = items.length ? items.map((item) => transactionRow(item, true)).join('') : '<tr><td colspan="6" class="empty-state">No exceptions found for this filter.</td></tr>';
  const total = state.exceptionRecords.length;
  $('exPageInfo').textContent = `Showing ${total ? state.exceptionsPage * pageSize + 1 : 0}–${Math.min((state.exceptionsPage + 1) * pageSize, total)} of ${total}`;
  $('exPrev').disabled = !state.exceptionsPage;
  $('exNext').disabled = (state.exceptionsPage + 1) * pageSize >= total;
}
async function loadAudit() {
  const { items } = await api('/api/audit?limit=80');
  $('auditItems').innerHTML = items.length ? items.map((event) => `<div class="audit-event"><div><strong>${clean(event.action.replaceAll('_', ' '))}</strong><p>${clean(event.details)}</p><small>${fmtDate(event.created_at, true)} · ${clean(event.actor)}</small></div></div>`).join('') : '<div class="loading-inline">No activity recorded.</div>';
}
function modalField(label, value) { return `<div class="detail-field"><span>${clean(label)}</span><strong>${clean(value)}</strong></div>`; }
async function showDetails(id) {
  try {
    const item = await api(`/api/transactions/${encodeURIComponent(id)}`);
    state.currentDetail = item;
    $('detailTitle').textContent = item.id;
    $('detailContent').innerHTML = `<div>${badge(item.status)} <span class="risk-label risk-${clean(item.risk)}" style="margin-left:9px">● ${clean(item.risk)} risk</span></div><div class="detail-grid">${modalField('Merchant', item.merchant)}${modalField('Payment amount', money(item.amount_cents))}${modalField('Recorded at', fmtDate(item.created_at, true))}${modalField('Payment state', item.payment_status)}${modalField('Settlement amount', item.settlement_amount_cents == null ? 'Not found' : money(item.settlement_amount_cents))}${modalField('Settlement currency', item.settlement_currency || '—')}${modalField('Bank reference', item.bank_reference || '—')}${modalField('Payment channel', item.channel)}${modalField('Analyst review', item.reviewed ? 'Reviewed' : 'Not reviewed')}${modalField('Match difference', item.status === 'amount_mismatch' ? money(item.amount_gap_cents) : '—')}</div><p class="modal-note">${item.is_exception ? 'Exception flagged automatically by amount, currency, or missing-settlement rules. Marking as reviewed acknowledges the record but does not change payment or settlement data.' : 'This is a synthetic payment record. Review status is an operational acknowledgement only.'}</p>`;
    $('reviewButton').textContent = item.reviewed ? 'Reopen review' : 'Mark reviewed';
    $('detailModal').hidden = false;
    $('closeModal').focus();
  } catch (error) { handleError(error); }
}
function closeModal() { $('detailModal').hidden = true; state.currentDetail = null; }
async function toggleReview() {
  const item = state.currentDetail;
  if (!item) return;
  $('reviewButton').disabled = true;
  try {
    await api(`/api/transactions/${encodeURIComponent(item.id)}/review`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reviewed: !item.reviewed }) });
    toast(item.reviewed ? 'Review reopened' : 'Transaction marked reviewed');
    closeModal();
    await Promise.all([loadSummary(), state.view === 'exceptions' ? loadExceptions() : state.view === 'transactions' ? loadTransactions() : Promise.resolve()]);
  } catch (error) { handleError(error); }
  finally { $('reviewButton').disabled = false; }
}
async function importCsv(file) {
  if (!file) return;
  if (file.size > 100000) { toast('CSV must be smaller than 100 KB', true); return; }
  try {
    const result = await api('/api/settlements/import', { method: 'POST', headers: { 'Content-Type': 'text/csv' }, body: await file.text() });
    toast(`${result.inserted} settlement(s) imported; ${result.skipped_duplicates} duplicate(s) skipped`);
    await loadSummary();
    if (state.view === 'transactions') await loadTransactions();
    if (state.view === 'exceptions') await loadExceptions();
    if (state.view === 'audit') await loadAudit();
  } catch (error) { handleError(error); }
  finally { $('csvFile').value = ''; }
}
function debounce(callback, ms) {
  let timer;
  return () => { clearTimeout(timer); timer = setTimeout(callback, ms); };
}
function setup() {
  $('year').textContent = new Date().getFullYear();
  document.querySelectorAll('[data-view]').forEach((button) => button.addEventListener('click', () => navigate(button.dataset.view)));
  document.querySelectorAll('[data-go]').forEach((button) => button.addEventListener('click', () => navigate(button.dataset.go)));
  $('menuButton').addEventListener('click', () => { $('sidebar').classList.add('open'); $('backdrop').hidden = false; });
  $('backdrop').addEventListener('click', closeMenu);
  $('txSearch').addEventListener('input', debounce(() => { state.transactionsPage = 0; loadTransactions().catch(handleError); }, 250));
  $('txStatus').addEventListener('change', () => { state.transactionsPage = 0; loadTransactions().catch(handleError); });
  $('txPrev').addEventListener('click', () => { state.transactionsPage--; loadTransactions().catch(handleError); });
  $('txNext').addEventListener('click', () => { state.transactionsPage++; loadTransactions().catch(handleError); });
  $('exSearch').addEventListener('input', debounce(() => { state.exceptionsPage = 0; loadExceptions().catch(handleError); }, 250));
  $('exFilter').addEventListener('change', () => { state.exceptionsPage = 0; loadExceptions().catch(handleError); });
  $('exPrev').addEventListener('click', () => { state.exceptionsPage--; loadExceptions().catch(handleError); });
  $('exNext').addEventListener('click', () => { state.exceptionsPage++; loadExceptions().catch(handleError); });
  document.addEventListener('click', (event) => { const target = event.target.closest('[data-detail]'); if (target) showDetails(target.dataset.detail); });
  $('closeModal').addEventListener('click', closeModal);
  $('modalDismiss').addEventListener('click', closeModal);
  $('detailModal').addEventListener('click', (event) => { if (event.target === $('detailModal')) closeModal(); });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') { closeModal(); closeMenu(); } });
  $('reviewButton').addEventListener('click', toggleReview);
  $('importButton').addEventListener('click', () => $('csvFile').click());
  $('csvFile').addEventListener('change', (event) => importCsv(event.target.files[0]));
  $('exportButton').addEventListener('click', () => { window.location.href = '/api/export/transactions.csv'; });
  window.addEventListener('hashchange', () => { const hash = window.location.hash.slice(1); if (hash !== state.view) navigate(hash); });
  navigate(window.location.hash.slice(1) || 'overview');
  loadSummary().catch(handleError);
}
window.addEventListener('DOMContentLoaded', setup);
