'use strict';

// ---- STATE ----
let _accounts = [], _transactions = [], _debts = [];
let _inventory = [], _investmentLog = [], _investSummary = {};
let _txFilter = 'all';
let _selectedIcon = '💰', _selectedColor = '#7c6aff';
let _lineChart = null, _donutChart = null;

// ---- UTILS ----
function todayStr() { return new Date().toISOString().slice(0, 10); }

function fmtMoney(n) {
    if (n === null || n === undefined) return '—';
    const sign = n < 0 ? '-' : '';
    return sign + Math.abs(n).toLocaleString('ro-RO', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' RON';
}

function fmtDate(d) {
    if (!d) return '';
    const p = d.split('-');
    return p.length === 3 ? `${p[2]}.${p[1]}.${p[0]}` : d;
}

function escHtml(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function flash(msg, type) {
    const el = document.getElementById('flashMsg');
    if (!el) return;
    el.textContent = msg;
    el.className = `flash-msg show${type ? ' ' + type : ''}`;
    setTimeout(() => el.className = 'flash-msg', 2800);
}

// ---- INIT ----
document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('debtDate').value = todayStr();
    document.getElementById('investDate').value = todayStr();
    document.getElementById('recoverDate').value = todayStr();
    loadAll();
});

// ---- LOAD ----
async function loadAll() {
    try {
        const res = await fetch('/api/finance/data');
        const data = await res.json();
        _accounts       = data.accounts || [];
        _transactions   = data.transactions || [];
        _debts          = data.debts || [];
        _inventory      = data.inventory || [];
        _investmentLog  = data.investment_log || [];
        _investSummary  = data.inv_summary || {};
        renderSummary(data.summary);
        renderAccounts();
        renderTxTable();
        renderDebts();
        renderInventoryTab();
        populateChartAccFilter();
        reloadChart();
    } catch (e) { console.error('Finance load error:', e); }
}

// ---- SUMMARY ----
function renderSummary(s) {
    document.getElementById('sumTotal').textContent = fmtMoney(s.total);
    document.getElementById('sumIn').textContent    = fmtMoney(s.total_in);
    document.getElementById('sumOut').textContent   = fmtMoney(s.total_out);
}

// ---- ACCOUNTS ----
function renderAccounts() {
    const grid = document.getElementById('accountsGrid');
    const addCard = document.getElementById('addAccountCard');
    grid.innerHTML = '';
    _accounts.forEach(acc => {
        const balance = acc.balance || 0;
        const card = document.createElement('div');
        card.className = 'fin-acc-card';
        card.style.setProperty('--acc-color', acc.color || '#7c6aff');
        card.dataset.id = acc.id;
        card.innerHTML = `
            <div class="fin-quick-overlay" id="overlay-${acc.id}">
                <div class="fin-quick-overlay-title" id="overlay-title-${acc.id}">Adaugă</div>
                <input type="number" class="fin-quick-amount-input" id="overlay-amt-${acc.id}"
                    placeholder="0" min="0.01" step="0.01" inputmode="decimal"
                    onkeydown="overlayKey(event,'${acc.id}')">
                <input type="text" class="fin-quick-note-input" id="overlay-note-${acc.id}"
                    placeholder="Notă opțională...">
                <div class="fin-quick-btns-row">
                    <button class="fin-quick-confirm" id="overlay-confirm-${acc.id}" onclick="confirmQuick('${acc.id}')">OK</button>
                    <button class="fin-quick-cancel" onclick="cancelOverlay('${acc.id}')">✕</button>
                </div>
            </div>
            <div class="fin-acc-top">
                <div class="fin-acc-icon-wrap">
                    <span class="fin-acc-icon">${acc.icon || '💰'}</span>
                    <span class="fin-acc-name">${escHtml(acc.name)}</span>
                </div>
                <button class="fin-acc-edit-btn" onclick="openEditAccountModal('${acc.id}')" title="Editează">⚙️</button>
            </div>
            <div class="fin-acc-balance ${balance < 0 ? 'negative' : ''}">${fmtMoney(balance)}</div>
            <div class="fin-acc-actions">
                <button class="fin-quick-btn add" onclick="openOverlay('${acc.id}','in')">＋ Adaugă</button>
                <button class="fin-quick-btn sub" onclick="openOverlay('${acc.id}','out')">－ Scade</button>
            </div>`;
        grid.appendChild(card);
    });
    grid.appendChild(addCard);
}

// ---- QUICK OVERLAY ----
let _overlayType = {};
function openOverlay(accId, type) {
    _overlayType[accId] = type;
    const title   = document.getElementById(`overlay-title-${accId}`);
    const confirm = document.getElementById(`overlay-confirm-${accId}`);
    const amt     = document.getElementById(`overlay-amt-${accId}`);
    document.getElementById(`overlay-note-${accId}`).value = '';
    title.textContent = type === 'in' ? '＋ Adaugă bani' : '－ Scade bani';
    title.className   = `fin-quick-overlay-title ${type === 'in' ? 'add' : 'sub'}`;
    confirm.className = `fin-quick-confirm ${type === 'in' ? 'add' : 'sub'}`;
    amt.value = '';
    document.getElementById(`overlay-${accId}`).classList.add('open');
    setTimeout(() => amt.focus(), 50);
}
function cancelOverlay(accId) { document.getElementById(`overlay-${accId}`).classList.remove('open'); }
function overlayKey(e, accId) { if (e.key === 'Enter') confirmQuick(accId); if (e.key === 'Escape') cancelOverlay(accId); }

async function confirmQuick(accId) {
    const amt  = parseFloat(document.getElementById(`overlay-amt-${accId}`).value);
    const note = document.getElementById(`overlay-note-${accId}`).value.trim();
    if (!amt || amt <= 0) { flash('Introdu o sumă validă', 'error'); return; }
    const type = _overlayType[accId] || 'in';
    try {
        const res  = await fetch('/api/finance/transaction/add', {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ account_id: accId, amount: amt, type, note, date: todayStr() })
        });
        const data = await res.json();
        if (data.status === 'success') {
            cancelOverlay(accId);
            flash(`${type === 'in' ? '+' : '-'} ${fmtMoney(amt)} salvat!`, 'success');
            await loadAll();
        } else { flash(data.message || 'Eroare', 'error'); }
    } catch { flash('Eroare rețea', 'error'); }
}

// ---- TRANSACTION TABLE ----
function setTxFilter(f) {
    _txFilter = f;
    document.querySelectorAll('.fin-filter-btn').forEach(b => b.classList.remove('active'));
    const idMap = { all:'filterAll', in:'filterIn', out:'filterOut', transfer:'filterTransfer' };
    document.getElementById(idMap[f])?.classList.add('active');
    renderTxTable();
}

function renderTxTable() {
    const tbody = document.getElementById('txTableBody');
    let txs = _transactions;
    if (_txFilter === 'transfer') {
        txs = txs.filter(t => t.tag === 'transfer');
    } else if (_txFilter !== 'all') {
        txs = txs.filter(t => t.type === _txFilter && t.tag !== 'transfer');
    }
    if (!txs.length) {
        tbody.innerHTML = `<tr><td colspan="5" class="empty-state">Nicio tranzacție</td></tr>`;
        return;
    }
    const accMap = {};
    _accounts.forEach(a => accMap[a.id] = a);
    tbody.innerHTML = txs.slice(0, 80).map(tx => {
        const acc = accMap[tx.account_id] || { name:'?', color:'#888', icon:'?' };
        const tagBadge = tx.tag === 'transfer' ? '<span class="fin-tx-tag transfer">⇄</span>'
                       : tx.tag === 'invest'   ? '<span class="fin-tx-tag invest">📦</span>'
                       : tx.tag === 'recover'  ? '<span class="fin-tx-tag recover">💰</span>'
                       : '';
        const amtClass = tx.type === 'in' ? 'in' : 'out';
        const sign     = tx.type === 'in' ? '+' : '-';
        return `<tr>
            <td style="white-space:nowrap">${fmtDate(tx.date)}</td>
            <td><span class="fin-tx-acc-dot" style="background:${acc.color}"></span>${escHtml(acc.icon)} ${escHtml(acc.name)}</td>
            <td style="color:var(--text-faint)">${tagBadge} ${escHtml(tx.note || '')}</td>
            <td><span class="fin-tx-amount ${amtClass}">${sign}${fmtMoney(Math.abs(tx.amount))}</span></td>
            <td><button class="fin-tx-delete" onclick="deleteTx('${tx.id}')" title="Șterge">🗑️</button></td>
        </tr>`;
    }).join('');
}

async function deleteTx(id) {
    if (!confirm('Ștergi tranzacția?')) return;
    const res = await fetch('/api/finance/transaction/delete', {
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id})
    });
    if ((await res.json()).status === 'success') { flash('Tranzacție ștearsă','success'); await loadAll(); }
}

// ---- CHARTS ----
function populateChartAccFilter() {
    const sel = document.getElementById('chartAccFilter');
    while (sel.options.length > 1) sel.remove(1);
    _accounts.forEach(acc => {
        const opt = new Option(`${acc.icon} ${acc.name}`, acc.id);
        sel.appendChild(opt);
    });
}

async function reloadChart() {
    const accId = document.getElementById('chartAccFilter').value;
    const days  = document.getElementById('chartDaysFilter').value;
    const res   = await fetch(`/api/finance/history?account_id=${accId}&days=${days}`);
    const data  = await res.json();
    renderLineChart(data.labels, data.values, accId);
    renderDonutChart();
}

function renderLineChart(labels, values, accId) {
    const ctx = document.getElementById('lineChart').getContext('2d');
    if (_lineChart) _lineChart.destroy();
    let color = '#7c6aff';
    if (accId && accId !== 'all') { const a = _accounts.find(a => a.id === accId); if (a) color = a.color; }
    const shortLabels = labels.map(l => { const p = l.split('-'); return `${p[2]}.${p[1]}`; });
    _lineChart = new Chart(ctx, {
        type: 'line',
        data: { labels: shortLabels, datasets: [{ label:'Sold', data: values,
            borderColor: color, backgroundColor: color+'22', fill:true, tension:0.35,
            pointRadius:2, pointHoverRadius:5, borderWidth:2 }] },
        options: { responsive:true, maintainAspectRatio:false,
            plugins: { legend:{display:false}, tooltip:{ callbacks:{ label: c => fmtMoney(c.parsed.y) } } },
            scales: {
                x: { grid:{color:'rgba(255,255,255,0.04)'}, ticks:{color:'#6a6a90',font:{size:10},maxTicksLimit:8} },
                y: { grid:{color:'rgba(255,255,255,0.04)'}, ticks:{color:'#6a6a90',font:{size:10}, callback:v=>v.toLocaleString('ro-RO')+' RON'} }
            }
        }
    });
}

function renderDonutChart() {
    const ctx = document.getElementById('donutChart').getContext('2d');
    if (_donutChart) _donutChart.destroy();
    const pos = _accounts.filter(a => (a.balance||0) > 0);
    if (!pos.length) return;
    _donutChart = new Chart(ctx, {
        type:'doughnut',
        data:{ labels: pos.map(a=>`${a.icon} ${a.name}`),
               datasets:[{ data:pos.map(a=>a.balance), backgroundColor:pos.map(a=>a.color+'cc'),
                           borderColor:pos.map(a=>a.color), borderWidth:2, hoverOffset:6 }] },
        options:{ responsive:true, maintainAspectRatio:false,
            plugins:{ legend:{position:'bottom',labels:{color:'#6a6a90',font:{size:10},boxWidth:10,padding:8}},
                      tooltip:{ callbacks:{ label:c=>`${c.label}: ${fmtMoney(c.parsed)}` } } } }
    });
}

// ---- ACCOUNT MODAL ----
function openNewAccountModal() {
    _selectedIcon = '💰'; _selectedColor = '#7c6aff';
    document.getElementById('accNameInput').value = '';
    document.getElementById('accEditId').value = '';
    document.getElementById('accountModalTitle').textContent = 'Cont nou';
    document.getElementById('deleteAccountBtn').style.display = 'none';
    document.querySelectorAll('.fin-icon-opt').forEach(e => e.classList.remove('selected'));
    document.querySelectorAll('.fin-color-opt').forEach(e => e.classList.remove('selected'));
    document.querySelector('[data-icon="💰"]')?.classList.add('selected');
    document.querySelector('[data-color="#7c6aff"]')?.classList.add('selected');
    document.getElementById('accountModal').classList.add('open');
    document.getElementById('accNameInput').focus();
}
function openEditAccountModal(accId) {
    const acc = _accounts.find(a => a.id === accId); if (!acc) return;
    _selectedIcon = acc.icon || '💰'; _selectedColor = acc.color || '#7c6aff';
    document.getElementById('accNameInput').value = acc.name;
    document.getElementById('accEditId').value = accId;
    document.getElementById('accountModalTitle').textContent = 'Editează cont';
    document.getElementById('deleteAccountBtn').style.display = '';
    document.querySelectorAll('.fin-icon-opt').forEach(e => e.classList.toggle('selected', e.dataset.icon === _selectedIcon));
    document.querySelectorAll('.fin-color-opt').forEach(e => e.classList.toggle('selected', e.dataset.color === _selectedColor));
    document.getElementById('accountModal').classList.add('open');
    document.getElementById('accNameInput').focus();
}
function closeAccountModal() { document.getElementById('accountModal').classList.remove('open'); }
function selectIcon(el, icon) { _selectedIcon = icon; document.querySelectorAll('.fin-icon-opt').forEach(e => e.classList.remove('selected')); el.classList.add('selected'); }
function selectColor(el, color) { _selectedColor = color; document.querySelectorAll('.fin-color-opt').forEach(e => e.classList.remove('selected')); el.classList.add('selected'); }

async function saveAccount() {
    const name = document.getElementById('accNameInput').value.trim();
    if (!name) { flash('Introdu un nume','error'); return; }
    const editId  = document.getElementById('accEditId').value;
    const payload = { name, icon: _selectedIcon, color: _selectedColor };
    if (editId) payload.id = editId;
    const res  = await fetch(editId ? '/api/finance/account/edit' : '/api/finance/account/add', {
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)
    });
    const data = await res.json();
    if (data.status === 'success') { closeAccountModal(); flash(editId?'Cont actualizat!':'Cont creat!','success'); await loadAll(); }
    else flash(data.message||'Eroare','error');
}

async function deleteAccount() {
    const id  = document.getElementById('accEditId').value; if (!id) return;
    const acc = _accounts.find(a => a.id === id);
    if (!confirm(`Ștergi contul "${acc?.name}"?`)) return;
    const res = await fetch('/api/finance/account/delete', {
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id})
    });
    if ((await res.json()).status === 'success') { closeAccountModal(); flash('Cont șters','success'); await loadAll(); }
}

// ---- TRANSFER MODAL ----
function openTransferModal() {
    populateAccountDropdown('transferSrc');
    populateAccountDropdown('transferDst');
    document.getElementById('transferAmount').value = '';
    document.getElementById('transferNote').value = '';
    document.getElementById('transferModal').classList.add('open');
}
function closeTransferModal() { document.getElementById('transferModal').classList.remove('open'); }

async function saveTransfer() {
    const src    = document.getElementById('transferSrc').value;
    const dst    = document.getElementById('transferDst').value;
    const amount = parseFloat(document.getElementById('transferAmount').value);
    const note   = document.getElementById('transferNote').value.trim();
    if (!amount || amount <= 0) { flash('Sumă invalidă','error'); return; }
    if (src === dst) { flash('Selectează conturi diferite','error'); return; }
    const res  = await fetch('/api/finance/transfer', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ source_account_id:src, dest_account_id:dst, amount, note, date:todayStr() })
    });
    const data = await res.json();
    if (data.status === 'success') { closeTransferModal(); flash(`Transfer ${fmtMoney(amount)} efectuat!`,'success'); await loadAll(); }
    else flash(data.message||'Eroare','error');
}

// ---- INVEST MODAL ----
function openInvestModal() {
    populateAccountDropdown('investSrcAccount');
    document.getElementById('investAmount').value = '';
    document.getElementById('investName').value = '';
    document.getElementById('investEstValue').value = '';
    document.getElementById('investNote').value = '';
    document.getElementById('investDate').value = todayStr();
    document.getElementById('investModal').classList.add('open');
    document.getElementById('investName').focus();
}
function closeInvestModal() { document.getElementById('investModal').classList.remove('open'); }

async function saveInvest() {
    const acc    = document.getElementById('investSrcAccount').value;
    const amount = parseFloat(document.getElementById('investAmount').value);
    const name   = document.getElementById('investName').value.trim();
    const est    = parseFloat(document.getElementById('investEstValue').value) || amount;
    const note   = document.getElementById('investNote').value.trim();
    const date   = document.getElementById('investDate').value;
    if (!amount || amount <= 0) { flash('Sumă invalidă','error'); return; }
    if (!name) { flash('Introduceți un nume','error'); return; }
    const res  = await fetch('/api/finance/invest', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ source_account_id:acc, amount, name, estimated_value:est, note, date })
    });
    const data = await res.json();
    if (data.status === 'success') { closeInvestModal(); flash(`📦 ${name} adăugat în stoc!`,'success'); await loadAll(); }
    else flash(data.message||'Eroare','error');
}

// ---- RECOVER MODAL ----
let _recoverInvItem = null;
function openRecoverModal(invId) {
    _recoverInvItem = _inventory.find(i => i.id === invId);
    if (!_recoverInvItem) return;
    document.getElementById('recoverInvId').value = invId;
    document.getElementById('recoverModalTitle').textContent = `💰 Vânzare: ${_recoverInvItem.name}`;
    document.getElementById('recoverProductInfo').innerHTML =
        `<div class="fin-recover-info-row"><span>Cost Basis:</span><strong>${fmtMoney(_recoverInvItem.cost_basis)}</strong></div>
         <div class="fin-recover-info-row"><span>Estimare:</span><strong>${fmtMoney(_recoverInvItem.estimated_value)}</strong></div>`;
    document.getElementById('recoverAmount').value = _recoverInvItem.estimated_value || '';
    document.getElementById('recoverNote').value = '';
    document.getElementById('recoverDate').value = todayStr();
    populateAccountDropdown('recoverDstAccount');
    updateProfitPreview();
    document.getElementById('recoverModal').classList.add('open');
    document.getElementById('recoverAmount').focus();
}
function closeRecoverModal() { document.getElementById('recoverModal').classList.remove('open'); }

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('recoverAmount')?.addEventListener('input', updateProfitPreview);
});

function updateProfitPreview() {
    if (!_recoverInvItem) return;
    const amt    = parseFloat(document.getElementById('recoverAmount').value) || 0;
    const profit = amt - parseFloat(_recoverInvItem.cost_basis || 0);
    const el     = document.getElementById('recoverProfitPreview');
    if (amt > 0) {
        const cls  = profit >= 0 ? 'profit' : 'loss';
        const sign = profit >= 0 ? '▲' : '▼';
        el.innerHTML = `<div class="fin-profit-preview ${cls}">${sign} Profit estimat: <strong>${fmtMoney(profit)}</strong></div>`;
    } else { el.innerHTML = ''; }
}

async function saveRecover() {
    const invId  = document.getElementById('recoverInvId').value;
    const dst    = document.getElementById('recoverDstAccount').value;
    const amount = parseFloat(document.getElementById('recoverAmount').value);
    const note   = document.getElementById('recoverNote').value.trim();
    const date   = document.getElementById('recoverDate').value;
    if (!amount || amount <= 0) { flash('Sumă invalidă','error'); return; }
    const res  = await fetch('/api/finance/recover', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ inventory_id:invId, dest_account_id:dst, amount, note, date })
    });
    const data = await res.json();
    if (data.status === 'success') {
        closeRecoverModal();
        const p = data.profit;
        flash(p >= 0 ? `✅ Vânzare! Profit: +${fmtMoney(p)}` : `⚠️ Vânzare sub cost. ${fmtMoney(p)}`, p >= 0 ? 'success' : 'error');
        await loadAll();
    } else flash(data.message||'Eroare','error');
}

// ---- INVENTORY TAB ----
function renderInventoryTab() {
    const s = _investSummary;
    document.getElementById('invCapital').textContent  = fmtMoney(s.capital_invested);
    document.getElementById('invEstValue').textContent = fmtMoney(s.estimated_value);
    document.getElementById('invProfit').textContent   = fmtMoney(s.potential_profit);
    document.getElementById('invRoiPct').textContent   = `ROI: ${s.potential_roi_pct || 0}%`;
    document.getElementById('invActiveCount').textContent = `${s.active_count || 0} produse active`;
    document.getElementById('invSoldCount').textContent   = `${s.sold_count || 0} vândute`;
    renderBreakevenTracker();
    renderInventoryGrid();
    renderInvLog();
}

function renderBreakevenTracker() {
    const s   = _investSummary;
    const pct = Math.min(s.breakeven_pct || 0, 100);
    document.getElementById('breakevenRecovered').textContent = fmtMoney(s.total_recovered);
    document.getElementById('breakevenTotal').textContent     = fmtMoney(s.capital_invested);
    document.getElementById('breakevenPct').textContent       = pct.toFixed(1) + '%';
    const bar = document.getElementById('breakevenBar');
    bar.style.width = pct + '%';
    bar.className = `fin-breakeven-bar ${pct >= 100 ? 'full' : pct >= 50 ? 'mid' : 'low'}`;
    const realized = s.realized_profit || 0;
    document.getElementById('breakevenRealized').innerHTML =
        realized !== 0 ? `<span class="fin-realized ${realized >= 0 ? 'profit':'loss'}">
            Profit realizat: ${realized >= 0 ? '+':''}${fmtMoney(realized)}
        </span>` : '';
}

function renderInventoryGrid() {
    const grid     = document.getElementById('inventoryGrid');
    const showSold = document.getElementById('showSoldItems')?.checked;
    let items      = showSold ? _inventory : _inventory.filter(i => i.status === 'active');
    if (!items.length) {
        grid.innerHTML = `<div class="empty-state" style="padding:40px 0;grid-column:1/-1;text-align:center">
            ${showSold ? 'Niciun produs în stoc sau vândut.' : 'Niciun produs activ. <button onclick="openInvestModal()" style="background:none;border:none;color:var(--primary);cursor:pointer;font-size:inherit">＋ Adaugă</button>'}
        </div>`;
        return;
    }
    grid.innerHTML = items.map(item => {
        const sold     = item.status === 'sold';
        const profit   = sold ? (parseFloat(item.sold_amount||0) - parseFloat(item.cost_basis||0)) : (parseFloat(item.estimated_value||0) - parseFloat(item.cost_basis||0));
        const profitCls = profit >= 0 ? 'profit' : 'loss';
        const profitSign = profit >= 0 ? '+' : '';
        return `<div class="fin-inv-card ${sold ? 'sold' : ''}">
            <div class="fin-inv-card-top">
                <span class="fin-inv-badge ${sold ? 'badge-sold':'badge-active'}">${sold ? '✅ Vândut' : '● Activ'}</span>
                ${!sold ? `<button class="fin-acc-edit-btn" onclick="deleteInventoryItem('${item.id}')" title="Șterge">🗑️</button>` : ''}
            </div>
            <div class="fin-inv-card-name">${escHtml(item.name)}</div>
            <div class="fin-inv-card-meta">Cumpărat: ${fmtDate(item.date_bought)}</div>
            <div class="fin-inv-card-prices">
                <div class="fin-inv-price-row"><span>Cost:</span><strong>${fmtMoney(item.cost_basis)}</strong></div>
                <div class="fin-inv-price-row"><span>${sold ? 'Vândut:' : 'Estimare:'}</span>
                    <strong>${fmtMoney(sold ? item.sold_amount : item.estimated_value)}</strong></div>
                <div class="fin-inv-price-row ${profitCls}"><span>${sold ? 'Profit realizat:' : 'Profit potențial:'}</span>
                    <strong>${profitSign}${fmtMoney(profit)}</strong></div>
            </div>
            ${item.note ? `<div class="fin-inv-card-note">${escHtml(item.note)}</div>` : ''}
            ${!sold ? `<button class="fin-inv-sell-btn" onclick="openRecoverModal('${item.id}')">💰 Marchează Vânzare</button>` : ''}
        </div>`;
    }).join('');
}

function renderInvLog() {
    const tbody = document.getElementById('invLogBody');
    if (!_investmentLog.length) {
        tbody.innerHTML = `<tr><td colspan="5" class="empty-state">Niciun log</td></tr>`;
        return;
    }
    const accMap = {};
    _accounts.forEach(a => accMap[a.id] = a);
    tbody.innerHTML = _investmentLog.slice(0,50).map(l => {
        const typeLbl = l.type === 'invest' ? '<span class="fin-tx-tag invest">📦 Invest</span>' : '<span class="fin-tx-tag recover">💰 Vânzare</span>';
        const profitHtml = l.profit != null ? `<span class="${l.profit >= 0 ? 'fin-tx-amount in' : 'fin-tx-amount out'}">${l.profit >= 0 ? '+' : ''}${fmtMoney(l.profit)}</span>` : '—';
        const amtCls = l.type === 'invest' ? 'out' : 'in';
        return `<tr>
            <td style="white-space:nowrap">${fmtDate(l.date)}</td>
            <td>${typeLbl}</td>
            <td>${escHtml(l.name || '')}</td>
            <td><span class="fin-tx-amount ${amtCls}">${fmtMoney(l.amount)}</span></td>
            <td>${profitHtml}</td>
        </tr>`;
    }).join('');
}

async function deleteInventoryItem(id) {
    if (!confirm('Ștergi produsul din stoc? (tranzacția de investiție NU va fi restaurată automat)')) return;
    const res = await fetch('/api/finance/inventory/delete', {
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id})
    });
    if ((await res.json()).status === 'success') { flash('Produs șters','success'); await loadAll(); }
    else flash('Eroare la ștergere','error');
}

// ---- HELPERS ----
function populateAccountDropdown(selectId) {
    const sel = document.getElementById(selectId);
    if (!sel) return;
    sel.innerHTML = _accounts.map(a => `<option value="${a.id}">${a.icon} ${escHtml(a.name)} (${fmtMoney(a.balance)})</option>`).join('');
}

// ---- DEBTS ----
function renderDebts() {
    const showSettled = document.getElementById('showSettled').checked;
    const owed  = _debts.filter(d => d.direction === 'owed_to_me' && (showSettled || !d.settled));
    const iOwe  = _debts.filter(d => d.direction === 'i_owe'      && (showSettled || !d.settled));
    const owedSum = _debts.filter(d => d.direction === 'owed_to_me' && !d.settled).reduce((s,d)=>s+d.amount,0);
    const iOweSum = _debts.filter(d => d.direction === 'i_owe'      && !d.settled).reduce((s,d)=>s+d.amount,0);
    const net = owedSum - iOweSum;
    document.getElementById('owedToMeSum').textContent = fmtMoney(owedSum);
    document.getElementById('iOweSum').textContent     = fmtMoney(iOweSum);
    const netEl = document.getElementById('debtNetVal');
    netEl.textContent = fmtMoney(Math.abs(net));
    netEl.className   = `fin-debt-net-value ${net > 0 ? 'positive' : net < 0 ? 'negative' : 'zero'}`;
    document.getElementById('debtNetSub').textContent = net > 0 ? 'Ai de primit mai mult — net pozitiv'
        : net < 0 ? 'Datorezi mai mult — atenție!' : 'Ești la zero net';
    document.getElementById('owedToMeList').innerHTML = owed.length
        ? owed.map(d => debtCardHtml(d,'owed')).join('')
        : `<div class="empty-state" style="padding:16px 0;text-align:left">Nimeni nu îți datorează 🎉</div>`;
    document.getElementById('iOweList').innerHTML = iOwe.length
        ? iOwe.map(d => debtCardHtml(d,'owing')).join('')
        : `<div class="empty-state" style="padding:16px 0;text-align:left">Nu ești dator la nimeni 👌</div>`;
}

function debtCardHtml(d, side) {
    return `<div class="fin-debt-card ${d.settled?'settled':''}">
        <div class="fin-debt-header">
            <div>
                <div class="fin-debt-name">${escHtml(d.name)}${d.settled?' <span style="font-size:11px;color:var(--teal)">✅</span>':''}</div>
                <div class="fin-debt-meta">${fmtDate(d.date)}</div>
                ${d.reason ? `<div class="fin-debt-reason">${escHtml(d.reason)}</div>` : ''}
            </div>
            <div class="fin-debt-amount ${side}">${fmtMoney(d.amount)}</div>
        </div>
        <div class="fin-debt-actions">
            <button class="fin-debt-settle-btn" onclick="settleDebt('${d.id}')">${d.settled?'↩️ Anulează':'✅ Reglat'}</button>
            <button class="fin-debt-del-btn"    onclick="deleteDebt('${d.id}')">🗑️</button>
        </div>
    </div>`;
}

function openDebtModal() {
    ['debtName','debtReason'].forEach(id => document.getElementById(id).value = '');
    document.getElementById('debtAmount').value = '';
    document.getElementById('debtDirection').value = 'owed_to_me';
    document.getElementById('debtDate').value = todayStr();
    document.getElementById('debtModal').classList.add('open');
    document.getElementById('debtName').focus();
}
function closeDebtModal() { document.getElementById('debtModal').classList.remove('open'); }

async function saveDebt() {
    const name      = document.getElementById('debtName').value.trim();
    const amount    = parseFloat(document.getElementById('debtAmount').value);
    const direction = document.getElementById('debtDirection').value;
    const reason    = document.getElementById('debtReason').value.trim();
    const date      = document.getElementById('debtDate').value;
    if (!name) { flash('Introdu un nume','error'); return; }
    if (!amount || amount <= 0) { flash('Sumă invalidă','error'); return; }
    const res  = await fetch('/api/finance/debt/add', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({name,amount,direction,reason,date})
    });
    if ((await res.json()).status === 'success') { closeDebtModal(); flash('Datorie salvată!','success'); await loadAll(); }
}

async function settleDebt(id) {
    await fetch('/api/finance/debt/settle', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id}) });
    await loadAll();
}
async function deleteDebt(id) {
    if (!confirm('Ștergi datoria?')) return;
    await fetch('/api/finance/debt/delete', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id}) });
    flash('Datorie ștearsă','success'); await loadAll();
}

// ---- TABS ----
const ALL_TABS = ['conturi','investitii','datorii'];
function cap(s) { return s.charAt(0).toUpperCase() + s.slice(1); }
function switchTab(name) {
    ALL_TABS.forEach(t => {
        document.getElementById('panel' + cap(t)).classList.toggle('active', t === name);
        document.getElementById('tab'   + cap(t)).classList.toggle('active', t === name);
    });
    if (name === 'investitii') renderInventoryTab();
}

// ---- CLOSE MODALS ON BACKDROP ----
document.addEventListener('click', e => {
    if (e.target.classList.contains('modal-overlay')) {
        document.querySelectorAll('.modal-overlay').forEach(m => m.classList.remove('open'));
    }
});
