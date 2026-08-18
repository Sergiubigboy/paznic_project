'use strict';
/* ══════════════════════════════════════════════════════════════
   CHRONOS OS — Bani v3
   Investiții cu cantități + vânzări în așteptare („aștept banii”).
   ══════════════════════════════════════════════════════════════ */

let _accounts = [], _transactions = [], _debts = [];
let _inventory = [], _sales = [], _investmentLog = [], _inv = {}, _summary = {};
let _txFilter = 'all';
let _selectedIcon = '💰', _selectedColor = '#7c6aff';
let _lineChart = null, _donutChart = null;
let _sellMode = 'instant';
let _sellItem = null, _restockItem = null, _settleSale = null;

const $ = id => document.getElementById(id);
const todayStr = () => new Date().toISOString().slice(0, 10);

function money(n) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    return (n < 0 ? '-' : '') + Math.abs(n).toLocaleString('ro-RO',
        { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' RON';
}
function moneyShort(n) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    const a = Math.abs(n);
    if (a >= 10000) return (n / 1000).toLocaleString('ro-RO', { maximumFractionDigits: 1 }) + 'k';
    return Math.round(n).toLocaleString('ro-RO');
}
function fmtDate(d) {
    if (!d) return '';
    const p = String(d).split('-');
    return p.length === 3 ? `${p[2]}.${p[1]}.${p[0]}` : d;
}
function daysSince(d) {
    if (!d) return 0;
    return Math.max(0, Math.round((new Date() - new Date(d)) / 86400000));
}
function esc(s) {
    return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function flash(msg, type) {
    if (window.Chronos) return window.Chronos.toast(msg, type);
    const el = $('flashMsg');
    if (!el) return;
    el.textContent = msg;
    el.className = `flash-msg show${type ? ' ' + type : ''}`;
    setTimeout(() => el.className = 'flash-msg', 3000);
}
function openModal(id) { $(id)?.classList.add('open'); }

async function post(url, body) {
    const r = await fetch(url, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });
    return r.json();
}

/* Stepper +/- pentru câmpurile de cantitate */
function stepQty(id, delta) {
    const el = $(id);
    if (!el) return;
    const min = el.min === '' ? 1 : parseInt(el.min);
    const max = el.max === '' ? Infinity : parseInt(el.max);
    el.value = Math.max(min, Math.min(max, (parseInt(el.value) || 0) + delta));
    el.dispatchEvent(new Event('input'));
}

/* ═══════════════ ÎNCĂRCARE ═══════════════ */
async function loadAll() {
    try {
        const d = await fetch('/api/finance/data').then(r => r.json());
        _accounts      = d.accounts || [];
        _transactions  = d.transactions || [];
        _debts         = d.debts || [];
        _inventory     = d.inventory || [];
        _sales         = d.sales || [];
        _investmentLog = d.investment_log || [];
        _inv           = d.inv_summary || {};
        _summary       = d.summary || {};

        renderNetStrip();
        renderTabCounts();
        renderAccounts();
        renderTxTable();
        renderDebts();
        renderInvestments();
        renderSales();
        populateChartAccFilter();
        reloadChart();
        registerPaletteActions();
    } catch (e) {
        console.error('finance load', e);
        flash('Nu pot încărca datele financiare', 'error');
    }
}

/* ═══════════════ BILANȚ ═══════════════ */
function renderNetStrip() {
    const s = _summary, inv = _inv;
    const nw = $('netWorth');
    nw.textContent = money(s.net_worth);
    nw.classList.toggle('neg', (s.net_worth || 0) < 0);

    $('npCash').textContent  = moneyShort(s.total);
    $('npStock').textContent = moneyShort(inv.stock_cost);
    $('npRoad').textContent  = moneyShort(inv.pending_total);
    $('npDebt').textContent  = moneyShort((s.debt_owed || 0) - (s.debt_owing || 0));

    $('sumTotal').textContent = money(s.total);
    $('sumIn').textContent    = money(s.total_in);
    $('sumOut').textContent   = money(s.total_out);
}

function renderTabCounts() {
    const setC = (id, n) => {
        const el = $(id);
        if (!el) return;
        el.textContent = n;
        el.style.display = n > 0 ? '' : 'none';
    };
    setC('cntStock', _inv.units_in_stock || 0);
    setC('cntPending', _inv.pending_count || 0);
    setC('cntDebts', _debts.filter(d => !d.settled).length);
}

/* ═══════════════ CONTURI ═══════════════ */
function renderAccounts() {
    const grid = $('accountsGrid');
    grid.innerHTML = _accounts.map(acc => {
        const b = acc.balance || 0;
        return `<div class="fin-acc-card" style="--acc-color:${acc.color || '#7c6aff'}">
            <div class="acc-overlay" id="ov-${acc.id}">
                <div class="acc-overlay-title" id="ov-t-${acc.id}">Adaugă</div>
                <input type="number" class="form-input" id="ov-a-${acc.id}" placeholder="0"
                       min="0.01" step="0.01" inputmode="decimal" onkeydown="overlayKey(event,'${acc.id}')">
                <input type="text" class="form-input" id="ov-n-${acc.id}" placeholder="Notă opțională…">
                <div class="row2">
                    <button class="acc-ok" id="ov-ok-${acc.id}" onclick="confirmQuick('${acc.id}')">OK</button>
                    <button class="acc-cancel" onclick="cancelOverlay('${acc.id}')">✕</button>
                </div>
            </div>
            <div class="acc-top">
                <span class="acc-icon">${acc.icon || '💰'}</span>
                <span class="acc-name">${esc(acc.name)}</span>
                <button class="acc-edit" onclick="openEditAccountModal('${acc.id}')" title="Editează">⚙️</button>
            </div>
            <div class="acc-balance ${b < 0 ? 'negative' : ''}">${money(b)}</div>
            <div class="acc-actions">
                <button class="acc-btn add" onclick="openOverlay('${acc.id}','in')">＋ Adaugă</button>
                <button class="acc-btn sub" onclick="openOverlay('${acc.id}','out')">− Scade</button>
            </div>
        </div>`;
    }).join('') +
    `<div class="acc-add" onclick="openNewAccountModal()"><span>＋</span><div>Cont nou</div></div>`;
}

let _overlayType = {};
function openOverlay(id, type) {
    _overlayType[id] = type;
    const t = $(`ov-t-${id}`), ok = $(`ov-ok-${id}`), a = $(`ov-a-${id}`);
    t.textContent = type === 'in' ? '＋ Adaugă bani' : '− Scade bani';
    t.className = `acc-overlay-title ${type === 'in' ? 'add' : 'sub'}`;
    ok.className = `acc-ok ${type === 'in' ? 'add' : 'sub'}`;
    a.value = ''; $(`ov-n-${id}`).value = '';
    $(`ov-${id}`).classList.add('open');
    setTimeout(() => a.focus(), 50);
}
function cancelOverlay(id) { $(`ov-${id}`).classList.remove('open'); }
function overlayKey(e, id) {
    if (e.key === 'Enter') confirmQuick(id);
    if (e.key === 'Escape') cancelOverlay(id);
}
async function confirmQuick(id) {
    const amt = parseFloat($(`ov-a-${id}`).value);
    const note = $(`ov-n-${id}`).value.trim();
    if (!amt || amt <= 0) { flash('Introdu o sumă validă', 'error'); return; }
    const type = _overlayType[id] || 'in';
    const d = await post('/api/finance/transaction/add',
        { account_id: id, amount: amt, type, note, date: todayStr() });
    if (d.status === 'success') {
        cancelOverlay(id);
        flash(`${type === 'in' ? '+' : '−'} ${money(amt)}`, 'success');
        await loadAll();
    } else flash(d.message || 'Eroare', 'error');
}

/* ═══════════════ TRANZACȚII ═══════════════ */
function setTxFilter(f) {
    _txFilter = f;
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    $({ all: 'filterAll', in: 'filterIn', out: 'filterOut', transfer: 'filterTransfer' }[f])?.classList.add('active');
    renderTxTable();
}

function renderTxTable() {
    const body = $('txTableBody');
    let txs = _transactions;
    if (_txFilter === 'transfer') txs = txs.filter(t => t.tag === 'transfer');
    else if (_txFilter !== 'all') txs = txs.filter(t => t.type === _txFilter && t.tag !== 'transfer');

    if (!txs.length) {
        body.innerHTML = '<tr><td colspan="5" class="empty-state">Nicio tranzacție</td></tr>';
        return;
    }
    const accMap = Object.fromEntries(_accounts.map(a => [a.id, a]));
    body.innerHTML = txs.slice(0, 80).map(tx => {
        const acc = accMap[tx.account_id] || { name: '?', color: '#888', icon: '?' };
        const tag = tx.tag ? `<span class="tx-tag ${tx.tag}">${
            { transfer: '⇄', invest: '📦', recover: '💰' }[tx.tag] || tx.tag}</span>` : '';
        return `<tr>
            <td class="nowrap faint">${fmtDate(tx.date)}</td>
            <td class="nowrap"><span class="tx-dot" style="background:${acc.color}"></span>${esc(acc.icon)} ${esc(acc.name)}</td>
            <td class="faint">${tag}${esc(tx.note || '')}</td>
            <td><span class="tx-amt ${tx.type}">${tx.type === 'in' ? '+' : '−'}${money(Math.abs(tx.amount))}</span></td>
            <td><button class="tx-del" onclick="deleteTx('${tx.id}')" title="Șterge">🗑️</button></td>
        </tr>`;
    }).join('');
}

async function deleteTx(id) {
    if (!confirm('Ștergi tranzacția?')) return;
    const d = await post('/api/finance/transaction/delete', { id });
    if (d.status === 'success') { flash('Tranzacție ștearsă', 'success'); await loadAll(); }
}

/* ═══════════════ GRAFICE ═══════════════ */
function populateChartAccFilter() {
    const sel = $('chartAccFilter');
    while (sel.options.length > 1) sel.remove(1);
    _accounts.forEach(a => sel.appendChild(new Option(`${a.icon} ${a.name}`, a.id)));
}

async function reloadChart() {
    if (typeof Chart === 'undefined') { setTimeout(reloadChart, 400); return; }
    const accId = $('chartAccFilter').value;
    const days = $('chartDaysFilter').value;
    try {
        const d = await fetch(`/api/finance/history?account_id=${accId}&days=${days}`).then(r => r.json());
        renderLineChart(d.labels, d.values, accId);
        renderDonutChart();
    } catch (e) {}
}

function renderLineChart(labels, values, accId) {
    const ctx = $('lineChart')?.getContext('2d');
    if (!ctx) return;
    if (_lineChart) _lineChart.destroy();
    let color = '#8b7aff';
    if (accId && accId !== 'all') { const a = _accounts.find(x => x.id === accId); if (a) color = a.color; }
    _lineChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels.map(l => { const p = l.split('-'); return `${p[2]}.${p[1]}`; }),
            datasets: [{
                data: values, borderColor: color, backgroundColor: color + '1f',
                fill: true, tension: .35, pointRadius: 0, pointHoverRadius: 5, borderWidth: 2
            }]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            animation: { duration: 500 },
            interaction: { intersect: false, mode: 'index' },
            plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => money(c.parsed.y) } } },
            scales: {
                x: { grid: { color: 'rgba(255,255,255,.035)' }, ticks: { color: '#55567a', font: { size: 10 }, maxTicksLimit: 8 } },
                y: { grid: { color: 'rgba(255,255,255,.035)' }, ticks: { color: '#55567a', font: { size: 10 }, callback: v => v.toLocaleString('ro-RO') } }
            }
        }
    });
}

function renderDonutChart() {
    const ctx = $('donutChart')?.getContext('2d');
    if (!ctx) return;
    if (_donutChart) _donutChart.destroy();
    const pos = _accounts.filter(a => (a.balance || 0) > 0);
    if (!pos.length) return;
    _donutChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: pos.map(a => `${a.icon} ${a.name}`),
            datasets: [{
                data: pos.map(a => a.balance),
                backgroundColor: pos.map(a => a.color + 'cc'),
                borderColor: 'rgba(0,0,0,0)', borderWidth: 2, hoverOffset: 6
            }]
        },
        options: {
            responsive: true, maintainAspectRatio: false, cutout: '62%',
            animation: { duration: 500 },
            plugins: {
                legend: { position: 'bottom', labels: { color: '#8384a8', font: { size: 10 }, boxWidth: 9, padding: 8 } },
                tooltip: { callbacks: { label: c => `${c.label}: ${money(c.parsed)}` } }
            }
        }
    });
}

/* ═══════════════ CONT: MODAL ═══════════════ */
function openNewAccountModal() {
    _selectedIcon = '💰'; _selectedColor = '#7c6aff';
    $('accNameInput').value = ''; $('accEditId').value = '';
    $('accountModalTitle').textContent = 'Cont nou';
    $('deleteAccountBtn').style.display = 'none';
    syncPickers();
    openModal('accountModal');
    setTimeout(() => $('accNameInput').focus(), 80);
}
function openEditAccountModal(id) {
    const a = _accounts.find(x => x.id === id); if (!a) return;
    _selectedIcon = a.icon || '💰'; _selectedColor = a.color || '#7c6aff';
    $('accNameInput').value = a.name; $('accEditId').value = id;
    $('accountModalTitle').textContent = 'Editează cont';
    $('deleteAccountBtn').style.display = '';
    syncPickers();
    openModal('accountModal');
}
function syncPickers() {
    document.querySelectorAll('.pick-ico').forEach(e => e.classList.toggle('selected', e.dataset.icon === _selectedIcon));
    document.querySelectorAll('.pick-col').forEach(e => e.classList.toggle('selected', e.dataset.color === _selectedColor));
}
function selectIcon(el, i) { _selectedIcon = i; syncPickers(); }
function selectColor(el, c) { _selectedColor = c; syncPickers(); }

async function saveAccount() {
    const name = $('accNameInput').value.trim();
    if (!name) { flash('Introdu un nume', 'error'); return; }
    const editId = $('accEditId').value;
    const payload = { name, icon: _selectedIcon, color: _selectedColor };
    if (editId) payload.id = editId;
    const d = await post(editId ? '/api/finance/account/edit' : '/api/finance/account/add', payload);
    if (d.status === 'success') {
        closeModal('accountModal');
        flash(editId ? 'Cont actualizat' : 'Cont creat', 'success');
        await loadAll();
    } else flash(d.message || 'Eroare', 'error');
}

async function deleteAccount() {
    const id = $('accEditId').value; if (!id) return;
    const a = _accounts.find(x => x.id === id);
    if (!confirm(`Ștergi contul „${a?.name}” și toate tranzacțiile lui?`)) return;
    const d = await post('/api/finance/account/delete', { id });
    if (d.status === 'success') { closeModal('accountModal'); flash('Cont șters', 'success'); await loadAll(); }
}

/* ═══════════════ TRANSFER ═══════════════ */
function openTransferModal() {
    fillAccounts('transferSrc'); fillAccounts('transferDst');
    if ($('transferDst').options.length > 1) $('transferDst').selectedIndex = 1;
    $('transferAmount').value = ''; $('transferNote').value = '';
    openModal('transferModal');
}
async function saveTransfer() {
    const src = $('transferSrc').value, dst = $('transferDst').value;
    const amount = parseFloat($('transferAmount').value);
    if (!amount || amount <= 0) { flash('Sumă invalidă', 'error'); return; }
    if (src === dst) { flash('Alege conturi diferite', 'error'); return; }
    const d = await post('/api/finance/transfer', {
        source_account_id: src, dest_account_id: dst, amount,
        note: $('transferNote').value.trim(), date: todayStr()
    });
    if (d.status === 'success') {
        closeModal('transferModal'); flash(`⇄ ${money(amount)} transferat`, 'success'); await loadAll();
    } else flash(d.message || 'Eroare', 'error');
}

function fillAccounts(selId, selected) {
    const sel = $(selId); if (!sel) return;
    sel.innerHTML = _accounts.map(a =>
        `<option value="${a.id}" ${a.id === selected ? 'selected' : ''}>${a.icon} ${esc(a.name)} — ${money(a.balance)}</option>`
    ).join('');
}

/* ═══════════════ INVESTIȚIE NOUĂ ═══════════════ */
function openInvestModal() {
    fillAccounts('investSrcAccount');
    $('investName').value = '';
    $('investQty').value = 1;
    $('investUnitCost').value = '';
    $('investEstValue').value = '';
    $('investNote').value = '';
    $('investDate').value = todayStr();
    updateInvestPreview();
    openModal('investModal');
    setTimeout(() => $('investName').focus(), 80);
}

function updateInvestPreview() {
    const qty = Math.max(1, parseInt($('investQty').value) || 1);
    const unit = parseFloat($('investUnitCost').value) || 0;
    const est = parseFloat($('investEstValue').value) || 0;
    const total = unit * qty;

    $('investCalc').innerHTML = unit > 0
        ? `<span>Total investit: <strong>${money(total)}</strong></span>
           <span class="sub">${qty} buc. × ${money(unit)}</span>`
        : 'Total: —';

    const p = $('investProfitPreview');
    if (est > 0 && unit > 0) {
        const profit = (est - unit) * qty;
        const roi = ((est - unit) / unit * 100).toFixed(1);
        p.className = 'profit-preview ' + (profit >= 0 ? 'profit' : 'loss');
        p.innerHTML = `${profit >= 0 ? '▲' : '▼'} Profit potențial: <strong>${money(profit)}</strong> · ROI ${roi}%`;
    } else { p.className = 'profit-preview'; p.innerHTML = ''; }
}

async function saveInvest() {
    const name = $('investName').value.trim();
    const qty = Math.max(1, parseInt($('investQty').value) || 1);
    const unit = parseFloat($('investUnitCost').value);
    if (!name) { flash('Dă-i un nume produsului', 'error'); return; }
    if (!unit || unit <= 0) { flash('Preț pe bucată invalid', 'error'); return; }

    const d = await post('/api/finance/invest', {
        source_account_id: $('investSrcAccount').value,
        name, quantity: qty, unit_cost: unit,
        estimated_value: parseFloat($('investEstValue').value) || unit,
        note: $('investNote').value.trim(),
        date: $('investDate').value || todayStr()
    });
    if (d.status === 'success') {
        closeModal('investModal');
        flash(`📦 ${name}${qty > 1 ? ` ×${qty}` : ''} adăugat pe stoc`, 'success');
        switchTab('investitii');
        await loadAll();
    } else flash(d.message || 'Eroare', 'error');
}

/* ═══════════════ MAI MULTE BUCĂȚI ═══════════════ */
function openRestockModal(invId) {
    _restockItem = _inventory.find(i => i.id === invId);
    if (!_restockItem) return;
    const it = _restockItem;
    $('restockInvId').value = invId;
    $('restockInfo').innerHTML = `
        <div class="pi-name">${esc(it.name)}</div>
        <div class="pi-row"><span>Pe stoc acum</span><strong>${it.qty_remaining} buc.</strong></div>
        <div class="pi-row"><span>Cost mediu actual</span><strong>${money(it.unit_cost)}/buc.</strong></div>`;
    $('restockQty').value = 1;
    $('restockUnitCost').value = it.unit_cost;
    $('restockDate').value = todayStr();
    fillAccounts('restockAccount', it.source_account_id);
    updateRestockPreview();
    openModal('restockModal');
}

function updateRestockPreview() {
    if (!_restockItem) return;
    const qty = Math.max(1, parseInt($('restockQty').value) || 1);
    const unit = parseFloat($('restockUnitCost').value) || 0;
    $('restockCalc').innerHTML = unit > 0
        ? `<span>Plătești: <strong>${money(unit * qty)}</strong></span><span class="sub">${qty} buc. × ${money(unit)}</span>`
        : 'Total: —';

    const oldQty = _restockItem.quantity || 0;
    const oldCost = _restockItem.unit_cost || 0;
    const newAvg = (oldQty + qty) ? ((oldCost * oldQty) + unit * qty) / (oldQty + qty) : unit;
    $('restockAvgHint').textContent = unit > 0
        ? `Cost mediu nou: ${money(newAvg)}/buc. · stoc după: ${(_restockItem.qty_remaining || 0) + qty} buc.`
        : '';
}

async function saveRestock() {
    const qty = Math.max(1, parseInt($('restockQty').value) || 1);
    const unit = parseFloat($('restockUnitCost').value);
    if (!unit || unit <= 0) { flash('Preț pe bucată invalid', 'error'); return; }
    const d = await post('/api/finance/inventory/add-units', {
        id: $('restockInvId').value,
        quantity: qty, unit_cost: unit,
        source_account_id: $('restockAccount').value,
        date: $('restockDate').value || todayStr()
    });
    if (d.status === 'success') {
        closeModal('restockModal');
        flash(`📦 +${qty} buc. pe stoc`, 'success');
        await loadAll();
    } else flash(d.message || 'Eroare', 'error');
}

/* ═══════════════ VÂNZARE ═══════════════ */
function openSellModal(invId) {
    _sellItem = _inventory.find(i => i.id === invId);
    if (!_sellItem) return;
    const it = _sellItem;

    $('sellInvId').value = invId;
    $('sellModalTitle').textContent = `💰 Vinde: ${it.name}`;
    $('sellProductInfo').innerHTML = `
        <div class="pi-name">${esc(it.name)}</div>
        <div class="pi-row"><span>Pe stoc</span><strong>${it.qty_remaining} buc.</strong></div>
        <div class="pi-row"><span>Cost / bucată</span><strong>${money(it.unit_cost)}</strong></div>
        <div class="pi-row"><span>Estimare / bucată</span><strong>${money(it.estimated_value)}</strong></div>`;

    const q = $('sellQty');
    q.value = 1; q.max = it.qty_remaining;
    $('sellUnitPrice').value = it.estimated_value || '';
    $('sellBuyer').value = '';
    $('sellNote').value = '';
    $('sellDate').value = todayStr();
    $('sellExpected').value = '';
    fillAccounts('sellDstAccount');
    setSellMode('instant');
    updateSellPreview();
    openModal('sellModal');
    setTimeout(() => $('sellUnitPrice').focus(), 80);
}

function sellAllUnits() {
    if (!_sellItem) return;
    $('sellQty').value = _sellItem.qty_remaining;
    updateSellPreview();
}

function setSellMode(mode) {
    _sellMode = mode;
    document.querySelectorAll('.mode-opt').forEach(b =>
        b.classList.toggle('active', b.dataset.mode === mode));
    $('sellInstantFields').hidden = mode !== 'instant';
    $('sellPendingFields').hidden = mode !== 'pending';
    $('sellConfirmBtn').textContent = mode === 'instant'
        ? '✅ Confirmă vânzarea' : '🚚 Marchează vândut, aștept banii';
}

function updateSellPreview() {
    if (!_sellItem) return;
    const max = _sellItem.qty_remaining;
    let qty = parseInt($('sellQty').value) || 1;
    if (qty > max) { qty = max; $('sellQty').value = max; }
    if (qty < 1) { qty = 1; $('sellQty').value = 1; }

    const price = parseFloat($('sellUnitPrice').value) || 0;
    const total = price * qty;
    const cost = (_sellItem.unit_cost || 0) * qty;
    const profit = total - cost;

    $('sellCalc').innerHTML = price > 0
        ? `<span>Încasezi: <strong>${money(total)}</strong></span>
           <span class="sub">${qty} buc. × ${money(price)} · cost ${money(cost)}</span>`
        : 'Încasezi: —';

    const p = $('sellProfitPreview');
    if (price > 0) {
        const roi = cost > 0 ? (profit / cost * 100).toFixed(1) : '—';
        p.className = 'profit-preview ' + (profit >= 0 ? 'profit' : 'loss');
        p.innerHTML = `${profit >= 0 ? '▲' : '▼'} Profit ${_sellMode === 'pending' ? 'la încasare' : ''}: <strong>${money(profit)}</strong> · ROI ${roi}%`;
    } else { p.className = 'profit-preview'; p.innerHTML = ''; }
}

async function saveSell() {
    const qty = Math.max(1, parseInt($('sellQty').value) || 1);
    const price = parseFloat($('sellUnitPrice').value);
    if (!price || price <= 0) { flash('Preț de vânzare invalid', 'error'); return; }

    const body = {
        inventory_id: $('sellInvId').value,
        qty, unit_price: price, mode: _sellMode,
        note: $('sellNote').value.trim(),
        date: $('sellDate').value || todayStr()
    };
    if (_sellMode === 'instant') body.dest_account_id = $('sellDstAccount').value;
    else {
        body.buyer = $('sellBuyer').value.trim();
        body.expected_date = $('sellExpected').value || '';
    }

    const d = await post('/api/finance/sell', body);
    if (d.status !== 'success') { flash(d.message || 'Eroare', 'error'); return; }

    closeModal('sellModal');
    if (_sellMode === 'pending') {
        flash(`🚚 Marcat vândut — ${money(d.sale.total)} de încasat`, 'success');
    } else {
        flash(d.profit >= 0
            ? `✅ Vândut! Profit +${money(d.profit)}`
            : `⚠️ Vândut sub cost: ${money(d.profit)}`, d.profit >= 0 ? 'success' : 'error');
    }
    await loadAll();
}

/* ═══════════════ ÎNCASARE ═══════════════ */
function openSettleModal(saleId) {
    _settleSale = _sales.find(s => s.id === saleId);
    if (!_settleSale) return;
    const s = _settleSale;
    $('settleSaleId').value = saleId;
    $('settleInfo').innerHTML = `
        <div class="pi-name">${esc(s.name)} ${s.qty > 1 ? `×${s.qty}` : ''}</div>
        <div class="pi-row"><span>Preț stabilit</span><strong>${money(s.total)}</strong></div>
        <div class="pi-row"><span>Cost marfă</span><strong>${money(s.cost_total)}</strong></div>
        ${s.buyer ? `<div class="pi-row"><span>De la</span><strong>${esc(s.buyer)}</strong></div>` : ''}
        <div class="pi-row"><span>Vândut acum</span><strong>${daysSince(s.date_sold)} zile</strong></div>`;
    $('settleAmount').value = s.total;
    $('settleDate').value = todayStr();
    fillAccounts('settleAccount');
    updateSettlePreview();
    openModal('settleModal');
}

function updateSettlePreview() {
    if (!_settleSale) return;
    const amt = parseFloat($('settleAmount').value) || 0;
    const profit = amt - (_settleSale.cost_total || 0);
    const p = $('settleProfitPreview');
    if (amt > 0) {
        p.className = 'profit-preview ' + (profit >= 0 ? 'profit' : 'loss');
        p.innerHTML = `${profit >= 0 ? '▲' : '▼'} Profit realizat: <strong>${money(profit)}</strong>`;
    } else { p.className = 'profit-preview'; p.innerHTML = ''; }
}

async function saveSettle() {
    const amt = parseFloat($('settleAmount').value);
    if (!amt || amt <= 0) { flash('Sumă invalidă', 'error'); return; }
    const d = await post('/api/finance/sale/settle', {
        id: $('settleSaleId').value,
        dest_account_id: $('settleAccount').value,
        amount: amt,
        date: $('settleDate').value || todayStr()
    });
    if (d.status === 'success') {
        closeModal('settleModal');
        flash(`💰 ${money(amt)} încasat`, 'success');
        await loadAll();
    } else flash(d.message || 'Eroare', 'error');
}

async function cancelSale(saleId) {
    const s = _sales.find(x => x.id === saleId);
    if (!confirm(`Anulezi vânzarea „${s?.name}”? Bucățile se întorc pe stoc.`)) return;
    const d = await post('/api/finance/sale/cancel', { id: saleId });
    if (d.status === 'success') { flash('Vânzare anulată, marfa e înapoi pe stoc', 'success'); await loadAll(); }
    else flash(d.message || 'Eroare', 'error');
}

/* ═══════════════ INVESTIȚII: RANDARE ═══════════════ */
function renderInvestments() {
    const s = _inv;
    $('invStockCost').textContent   = money(s.stock_cost);
    $('invStockUnits').textContent  = `${s.units_in_stock || 0} bucăți · ${s.active_count || 0} produse`;
    $('invStockValue').textContent  = money(s.stock_value);
    $('invStockProfit').textContent = `${(s.stock_potential || 0) >= 0 ? '+' : ''}${money(s.stock_potential)} potențial`;
    $('invStockRoi').textContent    = `${s.stock_roi_pct || 0}%`;

    $('invTotalInvested').textContent  = money(s.total_invested);
    $('invTotalRecovered').textContent = money(s.total_recovered);

    const rp = $('invRealizedProfit');
    rp.textContent = money(s.realized_profit);
    rp.className = 'at-val ' + ((s.realized_profit || 0) >= 0 ? 'pos' : 'neg');

    const pj = $('invProjected');
    pj.textContent = money(s.projected_profit);
    pj.className = 'at-val ' + ((s.projected_profit || 0) >= 0 ? 'pos' : 'neg');

    $('invTotalRoi').textContent = `${s.total_roi_pct || 0}%`;

    // Risc
    const card = $('riskCard'), lbl = $('riskLabel'), val = $('invRisk');
    if ((s.current_risk || 0) <= 0) {
        card.classList.add('safe');
        lbl.textContent = 'Joci din profit 🎉';
        val.textContent = money(0);
    } else {
        card.classList.remove('safe');
        lbl.textContent = 'Expunere (capital afară)';
        val.textContent = money(s.current_risk);
    }

    // Banner „pe drum"
    const banner = $('roadBanner');
    if ((s.pending_count || 0) > 0) {
        banner.hidden = false;
        $('rbCount').textContent = s.pending_count;
        $('rbAmount').textContent = money(s.pending_total);
    } else banner.hidden = true;

    renderInventory();
    renderInvLog();
}

function renderInventory() {
    const grid = $('inventoryGrid');
    const showAll = $('showSoldItems')?.checked;
    const items = showAll ? _inventory : _inventory.filter(i => (i.qty_remaining || 0) > 0);

    if (!items.length) {
        grid.innerHTML = `<div class="empty-state empty-state-lg" style="grid-column:1/-1">
            <span class="empty-state-icon">📦</span>
            <div class="empty-state-title">${showAll ? 'Niciun produs înregistrat' : 'Stocul e gol'}</div>
            <div>Adaugă o investiție ca să începi să urmărești marfa.</div>
            <button class="btn btn-primary btn-sm" style="margin-top:14px" onclick="openInvestModal()">＋ Investiție nouă</button>
        </div>`;
        return;
    }

    grid.innerHTML = items.map(it => {
        const rem = it.qty_remaining || 0;
        const total = it.quantity || 0;
        const sold = total - rem;
        const unitProfit = (it.estimated_value || 0) - (it.unit_cost || 0);
        const stockProfit = unitProfit * rem;
        const cls = stockProfit >= 0 ? 'profit' : 'loss';
        const pendingHere = _sales.filter(s => s.inventory_id === it.id && s.status === 'pending');
        const pendCount = pendingHere.reduce((a, s) => a + (s.qty || 0), 0);

        return `<div class="inv-card ${rem === 0 ? 'depleted' : ''}">
            <div class="inv-top">
                <span class="qty-pill ${rem === 0 ? 'zero' : ''}">${rem}<small>/${total} buc.</small></span>
                ${sold > 0 ? `<span class="chip teal">${sold} vândute</span>` : ''}
                ${pendCount > 0 ? `<span class="chip pending">🚚 ${pendCount} de încasat</span>` : ''}
                <span class="inv-top-actions">
                    <button class="inv-ico-btn" onclick="openEditInvModal('${it.id}')" title="Editează">✏️</button>
                    <button class="inv-ico-btn del" onclick="deleteInventoryItem('${it.id}')" title="Șterge">🗑️</button>
                </span>
            </div>
            <div class="inv-name">${esc(it.name)}</div>
            <div class="inv-meta">Cumpărat ${fmtDate(it.date_bought)}</div>

            <div class="inv-rows">
                <div class="inv-row"><span>Cost / buc.</span><strong>${money(it.unit_cost)}</strong></div>
                <div class="inv-row"><span>Estimare / buc.</span><strong>${money(it.estimated_value)}</strong></div>
                <div class="inv-row total"><span>Valoare stoc</span><strong>${money((it.estimated_value || 0) * rem)}</strong></div>
                <div class="inv-row ${cls}"><span>Profit potențial</span>
                    <strong>${stockProfit >= 0 ? '+' : ''}${money(stockProfit)}</strong></div>
            </div>

            ${it.note ? `<div class="inv-note">${esc(it.note)}</div>` : ''}

            <div class="inv-actions">
                <button class="inv-btn more" onclick="openRestockModal('${it.id}')">＋ Mai multe</button>
                <button class="inv-btn sell" onclick="openSellModal('${it.id}')" ${rem === 0 ? 'disabled' : ''}>
                    ${rem === 0 ? 'Epuizat' : '💰 Vinde'}
                </button>
            </div>
        </div>`;
    }).join('');
}

function renderInvLog() {
    const body = $('invLogBody');
    if (!_investmentLog.length) {
        body.innerHTML = '<tr><td colspan="6" class="empty-state">Niciun eveniment</td></tr>';
        return;
    }
    const LBL = {
        invest:  '<span class="tx-tag invest">📦 Cumpărat</span>',
        recover: '<span class="tx-tag recover">💰 Încasat</span>',
        pending: '<span class="tx-tag pending">🚚 Pe drum</span>'
    };
    body.innerHTML = _investmentLog.slice(0, 60).map(l => `<tr>
        <td class="nowrap faint">${fmtDate(l.date)}</td>
        <td class="nowrap">${LBL[l.type] || l.type}</td>
        <td>${esc(l.name || '')}</td>
        <td class="tnum">${l.qty || 1}</td>
        <td><span class="tx-amt ${l.type === 'invest' ? 'out' : 'in'}">${money(l.amount)}</span></td>
        <td>${l.profit != null
            ? `<span class="tx-amt ${l.profit >= 0 ? 'in' : 'out'}">${l.profit >= 0 ? '+' : ''}${money(l.profit)}</span>`
            : '<span class="faint">—</span>'}</td>
    </tr>`).join('');
}

/* ═══════════════ EDITARE PRODUS ═══════════════ */
function openEditInvModal(id) {
    const it = _inventory.find(x => x.id === id); if (!it) return;
    $('editInvId').value = id;
    $('editInvName').value = it.name || '';
    $('editInvUnitCost').value = it.unit_cost || 0;
    $('editInvEst').value = it.estimated_value || 0;
    $('editInvRemaining').value = it.qty_remaining || 0;
    $('editInvNote').value = it.note || '';
    openModal('editInvModal');
}

async function saveEditInv() {
    const d = await post('/api/finance/inventory/edit', {
        id: $('editInvId').value,
        name: $('editInvName').value.trim(),
        unit_cost: parseFloat($('editInvUnitCost').value) || 0,
        estimated_value: parseFloat($('editInvEst').value) || 0,
        qty_remaining: parseInt($('editInvRemaining').value) || 0,
        note: $('editInvNote').value.trim()
    });
    if (d.status === 'success') { closeModal('editInvModal'); flash('Produs actualizat', 'success'); await loadAll(); }
    else flash(d.message || 'Eroare', 'error');
}

async function deleteInventoryItem(id) {
    if (!confirm('Ștergi produsul din stoc? Tranzacția de cumpărare NU se anulează.')) return;
    const d = await post('/api/finance/inventory/delete', { id });
    if (d.status === 'success') { flash('Produs șters', 'success'); await loadAll(); }
    else flash(d.message || 'Nu pot șterge', 'error');
}

/* ═══════════════ VÂNZĂRI: RANDARE ═══════════════ */
function renderSales() {
    const pending = _sales.filter(s => s.status === 'pending')
        .sort((a, b) => (a.date_sold || '').localeCompare(b.date_sold || ''));
    const settled = _sales.filter(s => s.status === 'settled')
        .sort((a, b) => (b.settled_at || b.date_sold || '').localeCompare(a.settled_at || a.date_sold || ''));

    $('pdTotal').textContent = money(_inv.pending_total);
    $('pdCount').textContent = `${_inv.pending_count || 0} ${(_inv.pending_count === 1) ? 'vânzare neîncasată' : 'vânzări neîncasate'}`;
    $('pdProfit').textContent = money(_inv.pending_profit);
    $('pdOldest').textContent = pending.length ? `${daysSince(pending[0].date_sold)} zile` : '—';

    $('pendingGrid').innerHTML = pending.length ? pending.map(s => {
        const wait = daysSince(s.date_sold);
        const late = s.expected_date && new Date(s.expected_date) < new Date();
        const cls = s.profit >= 0 ? 'profit' : 'loss';
        return `<div class="sale-card pending">
            <div class="sale-head">
                <span class="sale-name">${esc(s.name)}${s.qty > 1 ? ` <span class="chip">×${s.qty}</span>` : ''}</span>
                <span class="sale-amount">${money(s.total)}</span>
            </div>
            <div class="sale-meta">
                <span class="wait-badge ${late || wait > 30 ? 'late' : ''}">⏳ ${wait} ${wait === 1 ? 'zi' : 'zile'}</span>
                ${s.buyer ? `<span class="chip">👤 ${esc(s.buyer)}</span>` : ''}
                ${s.expected_date ? `<span class="chip ${late ? 'danger' : ''}">📅 ${fmtDate(s.expected_date)}</span>` : ''}
            </div>
            <div class="sale-rows">
                <div class="r"><span>Vândut la</span><strong>${fmtDate(s.date_sold)}</strong></div>
                <div class="r"><span>Cost marfă</span><strong>${money(s.cost_total)}</strong></div>
                <div class="r ${cls}"><span>Profit la încasare</span>
                    <strong>${s.profit >= 0 ? '+' : ''}${money(s.profit)}</strong></div>
            </div>
            ${s.note ? `<div class="inv-note">${esc(s.note)}</div>` : ''}
            <div class="sale-actions">
                <button class="inv-btn sell" onclick="openSettleModal('${s.id}')">💰 Am primit banii</button>
                <button class="inv-btn" onclick="cancelSale('${s.id}')" title="Anulează vânzarea">↩</button>
            </div>
        </div>`;
    }).join('') : `<div class="empty-state empty-state-lg" style="grid-column:1/-1">
            <span class="empty-state-icon">✅</span>
            <div class="empty-state-title">Nimic în așteptare</div>
            <div>Toți banii din vânzări au ajuns în conturi.</div>
        </div>`;

    const accMap = Object.fromEntries(_accounts.map(a => [a.id, a]));
    $('settledGrid').innerHTML = settled.length ? settled.slice(0, 9).map(s => {
        const acc = accMap[s.account_id];
        const cls = s.profit >= 0 ? 'profit' : 'loss';
        return `<div class="sale-card settled">
            <div class="sale-head">
                <span class="sale-name">${esc(s.name)}${s.qty > 1 ? ` <span class="chip">×${s.qty}</span>` : ''}</span>
                <span class="sale-amount">${money(s.total)}</span>
            </div>
            <div class="sale-meta">
                <span class="chip ok">✅ încasat ${fmtDate(s.settled_at || s.date_sold)}</span>
                ${acc ? `<span class="chip">${acc.icon} ${esc(acc.name)}</span>` : ''}
            </div>
            <div class="sale-rows">
                <div class="r ${cls}"><span>Profit realizat</span>
                    <strong>${s.profit >= 0 ? '+' : ''}${money(s.profit)}</strong></div>
            </div>
        </div>`;
    }).join('') : '<div class="empty-state" style="grid-column:1/-1">Nicio încasare încă.</div>';
}

/* ═══════════════ DATORII ═══════════════ */
function renderDebts() {
    const showSettled = $('showSettled').checked;
    const owed = _debts.filter(d => d.direction === 'owed_to_me' && (showSettled || !d.settled));
    const iOwe = _debts.filter(d => d.direction === 'i_owe' && (showSettled || !d.settled));
    const owedSum = _debts.filter(d => d.direction === 'owed_to_me' && !d.settled).reduce((s, d) => s + d.amount, 0);
    const iOweSum = _debts.filter(d => d.direction === 'i_owe' && !d.settled).reduce((s, d) => s + d.amount, 0);
    const net = owedSum - iOweSum;

    $('owedToMeSum').textContent = money(owedSum);
    $('iOweSum').textContent = money(iOweSum);
    const netEl = $('debtNetVal');
    netEl.textContent = money(Math.abs(net));
    netEl.className = `dn-val ${net > 0 ? 'positive' : net < 0 ? 'negative' : ''}`;
    $('debtNetSub').textContent = net > 0 ? 'Ai de primit mai mult decât datorezi'
        : net < 0 ? 'Datorezi mai mult decât ai de primit' : 'Ești la zero';

    const card = (d, side) => `<div class="fin-debt-card ${d.settled ? 'settled' : ''}">
        <div class="debt-top">
            <div>
                <div class="debt-name">${esc(d.name)}${d.settled ? ' <span class="chip ok">reglat</span>' : ''}</div>
                <div class="debt-meta">${fmtDate(d.date)}</div>
                ${d.reason ? `<div class="debt-reason">${esc(d.reason)}</div>` : ''}
            </div>
            <div class="debt-amount ${side}">${money(d.amount)}</div>
        </div>
        <div class="debt-actions">
            <button class="debt-btn" onclick="settleDebt('${d.id}')">${d.settled ? '↩️ Anulează' : '✅ Reglat'}</button>
            <button class="debt-btn del" onclick="deleteDebt('${d.id}')">🗑️</button>
        </div>
    </div>`;

    $('owedToMeList').innerHTML = owed.length ? owed.map(d => card(d, 'owed')).join('')
        : '<div class="empty-state">Nimeni nu îți datorează 🎉</div>';
    $('iOweList').innerHTML = iOwe.length ? iOwe.map(d => card(d, 'owing')).join('')
        : '<div class="empty-state">Nu ești dator la nimeni 👌</div>';
}

function openDebtModal() {
    ['debtName', 'debtReason'].forEach(id => $(id).value = '');
    $('debtAmount').value = '';
    $('debtDirection').value = 'owed_to_me';
    $('debtDate').value = todayStr();
    openModal('debtModal');
    setTimeout(() => $('debtName').focus(), 80);
}

async function saveDebt() {
    const name = $('debtName').value.trim();
    const amount = parseFloat($('debtAmount').value);
    if (!name) { flash('Introdu un nume', 'error'); return; }
    if (!amount || amount <= 0) { flash('Sumă invalidă', 'error'); return; }
    const d = await post('/api/finance/debt/add', {
        name, amount,
        direction: $('debtDirection').value,
        reason: $('debtReason').value.trim(),
        date: $('debtDate').value
    });
    if (d.status === 'success') { closeModal('debtModal'); flash('Datorie salvată', 'success'); await loadAll(); }
}

async function settleDebt(id) { await post('/api/finance/debt/settle', { id }); await loadAll(); }
async function deleteDebt(id) {
    if (!confirm('Ștergi datoria?')) return;
    await post('/api/finance/debt/delete', { id });
    flash('Datorie ștearsă', 'success');
    await loadAll();
}

/* ═══════════════ TABURI ═══════════════ */
const TABS = ['conturi', 'investitii', 'pedrum', 'datorii'];
const capId = s => s.charAt(0).toUpperCase() + s.slice(1);

function switchTab(name) {
    if (!TABS.includes(name)) return;
    TABS.forEach(t => {
        $('panel' + capId(t))?.classList.toggle('active', t === name);
        $('tab' + capId(t))?.classList.toggle('active', t === name);
    });
    try { history.replaceState(null, '', '#' + name); } catch (e) {}
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

/* ═══════════════ PALETA DE COMENZI ═══════════════ */
function registerPaletteActions() {
    if (!window.Chronos) return;
    window.Chronos.registerActions([
        { icon: '📦', label: 'Investiție nouă', sub: 'bani', run: openInvestModal },
        { icon: '⇄', label: 'Transfer între conturi', sub: 'bani', run: openTransferModal },
        { icon: '💳', label: 'Cont nou', sub: 'bani', run: openNewAccountModal },
        { icon: '🤝', label: 'Datorie nouă', sub: 'bani', run: openDebtModal },
        { icon: '🚚', label: `Bani pe drum (${_inv.pending_count || 0})`, sub: 'tab', run: () => switchTab('pedrum') },
        { icon: '📦', label: 'Vezi stocul', sub: 'tab', run: () => switchTab('investitii') },
        ..._inventory.filter(i => (i.qty_remaining || 0) > 0).slice(0, 12).map(i => ({
            icon: '💰', label: `Vinde: ${i.name}`, sub: `${i.qty_remaining} buc.`,
            run: () => { switchTab('investitii'); setTimeout(() => openSellModal(i.id), 200); }
        }))
    ]);
}

/* ═══════════════ BOOT ═══════════════ */
document.addEventListener('DOMContentLoaded', () => {
    ['debtDate', 'investDate', 'sellDate', 'settleDate', 'restockDate'].forEach(id => {
        const el = $(id); if (el) el.value = todayStr();
    });

    document.querySelectorAll('.net-part').forEach(p =>
        p.addEventListener('click', () => switchTab(p.dataset.go)));

    // #hash: /bani#investitii, /bani#pedrum, /bani#invest (deschide direct modalul)
    const h = (location.hash || '').slice(1);
    if (h === 'invest') { switchTab('investitii'); setTimeout(openInvestModal, 350); }
    else if (TABS.includes(h)) switchTab(h);

    loadAll();
});
