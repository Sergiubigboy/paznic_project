'use strict';

// ============================================================
//  ELECTRONICS LAB — electronics.js
//  Chronos OS | Full CRUD: components, projects, devlog,
//  reservations, wishlist, buy-transfer
// ============================================================

// ---- STATE ----
let _components = [];
let _projects   = [];
let _wishlist   = [];
let _catFilter  = 'all';
let _projFilter = 'all';
let _wishFilter = 'all';

// ---- UTILS ----
function todayStr() { return new Date().toISOString().slice(0, 10); }

function fmtDate(d) {
    if (!d) return '';
    const p = d.split('-');
    return p.length === 3 ? `${p[2]}.${p[1]}.${p[0]}` : d;
}

function escHtml(s) {
    return String(s ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function flash(msg, type) {
    const el = document.getElementById('flashMsg');
    if (!el) return;
    el.textContent = msg;
    el.className = `flash-msg show${type ? ' ' + type : ''}`;
    setTimeout(() => el.className = 'flash-msg', 2800);
}

function catIcon(cat) {
    const map = {
        'Rezistențe': '⬜', 'Condensatoare': '⚡', 'MCU': '🧠',
        'Senzori': '👁️', 'Module': '📡', 'Altele': '🔩'
    };
    return map[cat] || '📦';
}

function prioLabel(p) {
    if (p === 'urgent') return '🔴 Urgent';
    if (p === 'normal') return '🟡 Normal';
    return '🟢 Când apare';
}

function statusLabel(s) {
    if (s === 'idee')      return '💡 Idee';
    if (s === 'activ')     return '⚡ Activ';
    if (s === 'finalizat') return '✅ Finalizat';
    return s;
}

// Map frontend status values to backend values (backend uses 'idea','active','done')
function statusToBackend(s) {
    if (s === 'idee')      return 'idee';
    if (s === 'activ')     return 'activ';
    if (s === 'finalizat') return 'finalizat';
    return s;
}

// Map backend status to frontend
function statusFromBackend(s) {
    return s; // They are the same in this implementation
}

async function postJSON(url, payload) {
    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
    return res.json();
}

// ---- TAB SWITCHING ----
const ALL_TABS = ['inventar', 'proiecte', 'wishlist'];

function switchTab(name) {
    ALL_TABS.forEach(t => {
        const capT = t.charAt(0).toUpperCase() + t.slice(1);
        document.getElementById('panel' + capT)?.classList.toggle('active', t === name);
        document.getElementById('tab'   + capT)?.classList.toggle('active', t === name);
    });
}

// ============================================================
//  INIT & LOAD
// ============================================================
document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('devlogDate').value = todayStr();
    loadAll();

    // Close modals on backdrop click
    document.addEventListener('click', e => {
        if (e.target.classList.contains('modal-overlay')) {
            document.querySelectorAll('.modal-overlay').forEach(m => m.classList.remove('open'));
        }
    });
});

async function loadAll() {
    try {
        const res  = await fetch('/api/electronics/data');
        const data = await res.json();
        _components = data.components || [];
        _projects   = data.projects   || [];
        _wishlist   = data.wishlist   || [];
        renderHeaderStats();
        renderCompTable();
        renderProjectsList();
        renderWishTable();
    } catch (e) {
        console.error('Electronics load error:', e);
        flash('Eroare la încărcarea datelor', 'error');
    }
}

// ============================================================
//  HEADER STATS
// ============================================================
function renderHeaderStats() {
    document.getElementById('hStatComponents').textContent = _components.length;
    document.getElementById('hStatProjects').textContent   = _projects.length;
    document.getElementById('hStatWishlist').textContent   = _wishlist.length;
}

// ============================================================
//  TAB 1 — INVENTORY / COMPONENTS
// ============================================================

// --- Category filter ---
function setCatFilter(cat) {
    _catFilter = cat;
    document.querySelectorAll('.elab-category-filters .elab-cat-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.cat === cat);
    });
    renderCompTable();
}

// --- Render inventory table ---
function renderCompTable() {
    const tbody = document.getElementById('compTableBody');
    let comps = _catFilter === 'all'
        ? _components
        : _components.filter(c => c.category === _catFilter);

    if (!comps.length) {
        tbody.innerHTML = `<tr><td colspan="6" class="empty-state">
            ${_catFilter === 'all' ? 'Nicio componentă. Apasă <strong>＋ Adaugă componentă</strong>.' : 'Nicio componentă în această categorie.'}
        </td></tr>`;
        return;
    }

    tbody.innerHTML = comps.map(comp => {
        // reserved & available come from backend or calculate locally
        const reserved = comp.reserved ?? getReservedTotal(comp.id);
        const avail    = comp.available ?? Math.max(0, (comp.qty || 0) - reserved);
        const availCls = avail <= 0 ? 'none' : avail < 3 ? 'low' : 'ok';

        // Build reservation badges
        const reservBadges = buildReservBadges(comp.id);

        return `<tr>
            <td>
                <div class="elab-comp-name">${escHtml(comp.name)}</div>
                ${comp.notes ? `<div class="elab-comp-notes">${escHtml(comp.notes)}</div>` : ''}
                ${reservBadges}
            </td>
            <td>
                <span class="elab-cat-badge">${catIcon(comp.category)} ${escHtml(comp.category)}</span>
            </td>
            <td><span class="elab-qty-total">${comp.qty ?? 0}</span></td>
            <td><span class="elab-qty-avail ${availCls}">${avail}</span></td>
            <td><div class="elab-specs-cell">${escHtml(comp.specs || '—')}</div></td>
            <td>
                <div class="elab-row-actions">
                    <button class="elab-btn-reserve" onclick="openReserveModal('${comp.id}')">📌</button>
                    <button class="elab-btn-edit"    onclick="openCompModal('${comp.id}')">✏️</button>
                    <button class="elab-btn-del"     onclick="deleteCompConfirm('${comp.id}')">🗑️</button>
                </div>
            </td>
        </tr>`;
    }).join('');
}

// Calculate total reserved quantity for a component across all projects
function getReservedTotal(compId) {
    let total = 0;
    _projects.forEach(proj => {
        (proj.reservations || []).forEach(r => {
            if (r.component_id === compId) total += (r.qty || 0);
        });
    });
    return total;
}

// Build reservation badges string
function buildReservBadges(compId) {
    const badges = [];
    _projects.forEach(proj => {
        (proj.reservations || []).forEach(r => {
            if (r.component_id === compId && r.qty > 0) {
                badges.push(`<span class="elab-reserv-badge">📌 ${r.qty} → ${escHtml(proj.name)}</span>`);
            }
        });
    });
    if (!badges.length) return '';
    return `<div class="elab-reserv-list">${badges.join('')}</div>`;
}

// ---- Add/Edit Component Modal ----
function openCompModal(compId = null) {
    const editComp = compId ? _components.find(c => c.id === compId) : null;
    document.getElementById('compModalTitle').textContent = editComp ? '✏️ Editează componentă' : '➕ Componentă nouă';
    document.getElementById('compEditId').value  = editComp ? compId : '';
    document.getElementById('compName').value    = editComp?.name     || '';
    document.getElementById('compCat').value     = editComp?.category || 'Altele';
    document.getElementById('compQty').value     = editComp?.qty      ?? '';
    document.getElementById('compSpecs').value   = editComp?.specs    || '';
    document.getElementById('compNotes').value   = editComp?.notes    || '';
    document.getElementById('compDeleteBtn').style.display = editComp ? '' : 'none';
    document.getElementById('compModal').classList.add('open');
    document.getElementById('compName').focus();
}

function closeCompModal() { document.getElementById('compModal').classList.remove('open'); }

async function saveComp() {
    const name     = document.getElementById('compName').value.trim();
    const category = document.getElementById('compCat').value;
    const qty      = parseInt(document.getElementById('compQty').value) || 0;
    const specs    = document.getElementById('compSpecs').value.trim();
    const notes    = document.getElementById('compNotes').value.trim();
    const editId   = document.getElementById('compEditId').value;

    if (!name) { flash('Introdu un nume!', 'error'); return; }
    if (qty < 0) { flash('Cantitatea trebuie să fie ≥ 0', 'error'); return; }

    const payload = { name, category, qty, specs, notes };
    if (editId) payload.id = editId;

    const url  = editId ? '/api/electronics/component/edit' : '/api/electronics/component/add';
    const data = await postJSON(url, payload);
    if (data.status === 'success') {
        closeCompModal();
        flash(editId ? '✏️ Componentă actualizată!' : '✅ Componentă adăugată!', 'success');
        await loadAll();
    } else {
        flash(data.message || 'Eroare la salvare', 'error');
    }
}

async function deleteComp() {
    const id   = document.getElementById('compEditId').value;
    const comp = _components.find(c => c.id === id);
    if (!confirm(`Ștergi "${comp?.name}"? Rezervările din proiecte vor fi șterse și ele.`)) return;
    const data = await postJSON('/api/electronics/component/delete', { id });
    if (data.status === 'success') {
        closeCompModal();
        flash('🗑️ Componentă ștearsă', 'success');
        await loadAll();
    } else {
        flash(data.message || 'Eroare la ștergere', 'error');
    }
}

async function deleteCompConfirm(id) {
    const comp = _components.find(c => c.id === id);
    if (!confirm(`Ștergi "${comp?.name}"?`)) return;
    const data = await postJSON('/api/electronics/component/delete', { id });
    if (data.status === 'success') {
        flash('🗑️ Componentă ștearsă', 'success');
        await loadAll();
    } else {
        flash(data.message || 'Eroare la ștergere', 'error');
    }
}

// ---- Reserve Component Modal ----
function openReserveModal(compId) {
    const comp = _components.find(c => c.id === compId);
    if (!comp) return;

    const reserved = comp.reserved ?? getReservedTotal(compId);
    const avail    = comp.available ?? Math.max(0, (comp.qty || 0) - reserved);

    document.getElementById('reserveCompId').value  = compId;
    document.getElementById('reserveCompInfo').innerHTML =
        `<strong>${escHtml(comp.name)}</strong><br>
         Total: <strong>${comp.qty}</strong> · Rezervat: <strong>${reserved}</strong> · 
         <span style="color:var(--teal)">Disponibil: <strong>${avail}</strong></span>`;

    // Populate project select
    const sel = document.getElementById('reserveProjSel');
    const projs = _projects.filter(p => p.status !== 'finalizat');
    if (!projs.length) {
        flash('Nu ai proiecte active. Creează mai întâi un proiect.', 'error');
        return;
    }
    sel.innerHTML = projs.map(p =>
        `<option value="${p.id}">${statusLabel(p.status)} ${escHtml(p.name)}</option>`
    ).join('');

    document.getElementById('reserveQty').value = '';
    document.getElementById('reserveModal').classList.add('open');
    document.getElementById('reserveQty').focus();
}

function closeReserveModal() { document.getElementById('reserveModal').classList.remove('open'); }

async function saveReserve() {
    const compId  = document.getElementById('reserveCompId').value;
    const projId  = document.getElementById('reserveProjSel').value;
    const qty     = parseInt(document.getElementById('reserveQty').value);
    const comp    = _components.find(c => c.id === compId);

    if (!qty || qty <= 0) { flash('Cantitate invalidă', 'error'); return; }

    const avail = comp?.available ?? Math.max(0, (comp?.qty || 0) - getReservedTotal(compId));
    if (qty > avail) {
        flash(`Disponibil doar ${avail} bucăți!`, 'error');
        return;
    }

    const data = await postJSON('/api/electronics/reserve', { component_id: compId, project_id: projId, qty });
    if (data.status === 'success') {
        closeReserveModal();
        flash(`📌 ${qty} × ${comp?.name} rezervate!`, 'success');
        await loadAll();
    } else {
        flash(data.message || 'Eroare', 'error');
    }
}

// ============================================================
//  TAB 2 — PROJECTS
// ============================================================

// --- Project status filter ---
function setProjFilter(status) {
    _projFilter = status;
    document.querySelectorAll('.elab-proj-status-filters .elab-cat-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.status === status);
    });
    renderProjectsList();
}

// --- Render projects list ---
function renderProjectsList() {
    const container = document.getElementById('projectsList');
    let projs = _projFilter === 'all'
        ? _projects
        : _projects.filter(p => p.status === _projFilter);

    if (!projs.length) {
        container.innerHTML = `<div class="empty-state" style="padding:48px 0">
            ${_projFilter === 'all' ? '🔧 Niciun proiect. Apasă <strong>＋ Proiect Nou</strong>.' : 'Niciun proiect cu statusul selectat.'}
        </div>`;
        return;
    }

    container.innerHTML = projs.map(proj => projCardHtml(proj)).join('');
}

function projCardHtml(proj) {
    const techTags = (proj.technologies || []).map(t =>
        `<span class="elab-tech-tag">${escHtml(t)}</span>`
    ).join('');

    const reservCount = (proj.reservations || []).length;
    const logCount    = (proj.devlog       || []).length;
    const statusCls   = `elab-status-${proj.status || 'idee'}`;

    return `<div class="elab-proj-card" id="proj-${proj.id}">
        <div class="elab-proj-header" onclick="toggleProject('${proj.id}')">
            <span class="elab-proj-expand-icon">▶</span>
            <span class="elab-proj-status-badge ${statusCls}">${statusLabel(proj.status)}</span>
            <div style="flex:1">
                <div class="elab-proj-title">${escHtml(proj.name)}</div>
                ${techTags ? `<div class="elab-proj-tech-row">${techTags}</div>` : ''}
            </div>
            <div class="elab-proj-meta-chips">
                ${reservCount ? `<span style="font-size:11px;color:var(--text-faint);margin-right:6px">⚙️ ${reservCount}</span>` : ''}
                ${logCount    ? `<span style="font-size:11px;color:var(--text-faint);margin-right:6px">📝 ${logCount}</span>`    : ''}
            </div>
            <div class="elab-proj-header-actions" onclick="event.stopPropagation()">
                <button class="elab-btn-edit" onclick="openProjModal('${proj.id}')">✏️ Edit</button>
            </div>
        </div>
        <div class="elab-proj-body">
            ${projBodyHtml(proj)}
        </div>
    </div>`;
}

function projBodyHtml(proj) {
    const linksHtml  = buildProjLinks(proj);
    const allocHtml  = buildAllocList(proj);
    const devlogHtml = buildDevlog(proj);

    return `
    <div class="elab-proj-body-grid">
        <div>
            <div class="elab-proj-section-title">📋 Descriere</div>
            <div class="elab-proj-desc">${proj.description ? escHtml(proj.description) : '<span style="color:var(--text-faint);font-style:italic">Fără descriere.</span>'}</div>
            ${linksHtml}
        </div>
        <div>
            <div class="elab-proj-section-title">
                ⚙️ Componente rezervate
                <button class="btn btn-xs btn-secondary" onclick="openAllocModal('${proj.id}')">＋ Rezervă</button>
            </div>
            ${allocHtml}
        </div>
    </div>
    <div class="elab-devlog-section elab-devlog-full">
        <div class="elab-proj-section-title" style="margin-bottom:12px">
            📝 Devlog — jurnal tehnic
            <button class="btn btn-xs btn-secondary" onclick="openDevlogModal('${proj.id}')">＋ Intrare nouă</button>
        </div>
        ${devlogHtml}
    </div>`;
}

// Build links from project.links array (backend stores [{type, url}] or from separate github/youtube fields)
function buildProjLinks(proj) {
    const links = [];
    // Support both old-style object and links array
    if (proj.github)  links.push(`<a href="${escHtml(proj.github)}"  class="elab-proj-link" target="_blank" rel="noopener">🐙 GitHub</a>`);
    if (proj.youtube) links.push(`<a href="${escHtml(proj.youtube)}" class="elab-proj-link" target="_blank" rel="noopener">▶️ YouTube</a>`);
    (proj.links || []).forEach(l => {
        const url   = typeof l === 'string' ? l : l.url;
        const label = typeof l === 'string' ? '🔗 Link' : (l.label || '🔗 Link');
        if (url) links.push(`<a href="${escHtml(url)}" class="elab-proj-link" target="_blank" rel="noopener">${escHtml(label)}</a>`);
    });
    if (!links.length) return '';
    return `<div class="elab-proj-links" style="margin-top:12px">${links.join('')}</div>`;
}

function buildAllocList(proj) {
    const reservations = proj.reservations || [];
    if (!reservations.length) {
        return `<div style="color:var(--text-faint);font-size:12px;font-style:italic;margin-top:4px">Nicio componentă rezervată.</div>`;
    }
    return `<div class="elab-alloc-list">
        ${reservations.map(r => {
            const comp = _components.find(c => c.id === r.component_id);
            return `<div class="elab-alloc-item">
                <span class="elab-alloc-name">${escHtml(comp?.name || '(componentă ștearsă)')}</span>
                <div style="display:flex;align-items:center;gap:8px">
                    <span class="elab-alloc-qty">×${r.qty}</span>
                    <button class="elab-alloc-del" onclick="removeAlloc('${proj.id}','${r.component_id}')" title="Elimină">✕</button>
                </div>
            </div>`;
        }).join('')}
    </div>`;
}

function buildDevlog(proj) {
    const entries = (proj.devlog || []).slice().sort((a, b) => (b.date || '').localeCompare(a.date || ''));
    if (!entries.length) {
        return `<div style="color:var(--text-faint);font-size:12px;font-style:italic">Nicio intrare devlog încă. Apasă <strong>＋ Intrare nouă</strong>.</div>`;
    }
    return `<div class="elab-devlog-timeline">
        ${entries.map(e => `
        <div class="elab-devlog-entry">
            <div class="elab-devlog-entry-header">
                <span class="elab-devlog-date">${fmtDate(e.date)}</span>
                <span class="elab-devlog-title">${escHtml(e.title)}</span>
                <div class="elab-devlog-actions">
                    <button class="elab-devlog-btn"     onclick="openDevlogModal('${proj.id}','${e.id}')">✏️</button>
                    <button class="elab-devlog-btn del" onclick="deleteDevlogEntry('${proj.id}','${e.id}')">🗑️</button>
                </div>
            </div>
            ${e.text ? `<div class="elab-devlog-text">${escHtml(e.text)}</div>` : ''}
        </div>`).join('')}
    </div>`;
}

function toggleProject(projId) {
    const card = document.getElementById(`proj-${projId}`);
    if (!card) return;
    card.classList.toggle('expanded');
}

// ---- Add/Edit Project Modal ----
function openProjModal(projId = null) {
    const editProj = projId ? _projects.find(p => p.id === projId) : null;
    document.getElementById('projModalTitle').textContent = editProj ? '✏️ Editează proiect' : '🔧 Proiect nou';
    document.getElementById('projEditId').value = editProj ? projId : '';
    document.getElementById('projTitle').value   = editProj?.name         || '';
    document.getElementById('projDesc').value    = editProj?.description  || '';
    document.getElementById('projStatus').value  = editProj?.status       || 'idee';
    document.getElementById('projTech').value    = (editProj?.technologies || []).join(', ');

    // Extract github/youtube from links array or direct fields
    let github = editProj?.github || '';
    let youtube = editProj?.youtube || '';
    (editProj?.links || []).forEach(l => {
        const url   = typeof l === 'string' ? l : l.url;
        const label = typeof l === 'object' ? (l.label || '') : '';
        if (label.toLowerCase().includes('github') || url.includes('github'))  github  = github  || url;
        if (label.toLowerCase().includes('youtube') || url.includes('youtube')) youtube = youtube || url;
    });
    document.getElementById('projGithub').value  = github;
    document.getElementById('projYoutube').value = youtube;
    document.getElementById('projDeleteBtn').style.display = editProj ? '' : 'none';
    document.getElementById('projModal').classList.add('open');
    document.getElementById('projTitle').focus();
}

function closeProjModal() { document.getElementById('projModal').classList.remove('open'); }

async function saveProj() {
    const name        = document.getElementById('projTitle').value.trim();
    const description = document.getElementById('projDesc').value.trim();
    const status      = document.getElementById('projStatus').value;
    const techStr     = document.getElementById('projTech').value.trim();
    const github      = document.getElementById('projGithub').value.trim();
    const youtube     = document.getElementById('projYoutube').value.trim();
    const editId      = document.getElementById('projEditId').value;
    const technologies = techStr ? techStr.split(',').map(t => t.trim()).filter(Boolean) : [];

    if (!name) { flash('Introdu un titlu!', 'error'); return; }

    // Build links array
    const links = [];
    if (github)  links.push({ label: '🐙 GitHub',  url: github });
    if (youtube) links.push({ label: '▶️ YouTube', url: youtube });

    const payload = { name, description, status, technologies, links };
    if (editId) payload.id = editId;

    const url  = editId ? '/api/electronics/project/edit' : '/api/electronics/project/add';
    const data = await postJSON(url, payload);
    if (data.status === 'success') {
        closeProjModal();
        flash(editId ? '✏️ Proiect actualizat!' : '✅ Proiect creat!', 'success');
        await loadAll();
    } else {
        flash(data.message || 'Eroare la salvare', 'error');
    }
}

async function deleteProj() {
    const id   = document.getElementById('projEditId').value;
    const proj = _projects.find(p => p.id === id);
    if (!confirm(`Ștergi proiectul "${proj?.name}"? Devlog-ul și rezervările vor fi șterse.`)) return;
    const data = await postJSON('/api/electronics/project/delete', { id });
    if (data.status === 'success') {
        closeProjModal();
        flash('🗑️ Proiect șters', 'success');
        await loadAll();
    } else {
        flash(data.message || 'Eroare', 'error');
    }
}

// ---- Allocate Component Modal (Reserve) ----
function openAllocModal(projId) {
    document.getElementById('allocProjId').value = projId;
    const sel = document.getElementById('allocCompSel');
    sel.innerHTML = _components.map(c => {
        const avail = c.available ?? Math.max(0, (c.qty || 0) - getReservedTotal(c.id));
        return `<option value="${c.id}">${escHtml(c.name)} (disponibil: ${avail})</option>`;
    }).join('');
    document.getElementById('allocQty').value = '1';
    document.getElementById('allocModal').classList.add('open');
    document.getElementById('allocQty').focus();
}

function closeAllocModal() { document.getElementById('allocModal').classList.remove('open'); }

async function saveAlloc() {
    const projId = document.getElementById('allocProjId').value;
    const compId = document.getElementById('allocCompSel').value;
    const qty    = parseInt(document.getElementById('allocQty').value);
    const comp   = _components.find(c => c.id === compId);
    if (!qty || qty <= 0) { flash('Cantitate invalidă', 'error'); return; }

    const avail = comp?.available ?? Math.max(0, (comp?.qty || 0) - getReservedTotal(compId));
    if (qty > avail) { flash(`Disponibil doar ${avail} bucăți!`, 'error'); return; }

    const data = await postJSON('/api/electronics/reserve', { component_id: compId, project_id: projId, qty });
    if (data.status === 'success') {
        closeAllocModal();
        flash(`⚙️ ${comp?.name} rezervat pentru proiect!`, 'success');
        await loadAll();
        // Re-expand the project card after reload
        setTimeout(() => {
            const card = document.getElementById(`proj-${projId}`);
            if (card) card.classList.add('expanded');
        }, 50);
    } else {
        flash(data.message || 'Eroare', 'error');
    }
}

async function removeAlloc(projId, compId) {
    const comp = _components.find(c => c.id === compId);
    if (!confirm(`Elimini rezervarea componentei "${comp?.name}" din proiect?`)) return;
    const data = await postJSON('/api/electronics/reserve', { component_id: compId, project_id: projId, qty: 0 });
    if (data.status === 'success') {
        flash('Rezervare eliminată', 'success');
        await loadAll();
        setTimeout(() => {
            const card = document.getElementById(`proj-${projId}`);
            if (card) card.classList.add('expanded');
        }, 50);
    } else {
        flash(data.message || 'Eroare', 'error');
    }
}

// ---- Devlog Modal ----
function openDevlogModal(projId, entryId = null) {
    const proj  = _projects.find(p => p.id === projId);
    const entry = entryId ? (proj?.devlog || []).find(e => e.id === entryId) : null;
    document.getElementById('devlogModalTitle').textContent = entry ? '✏️ Editează intrare devlog' : '📝 Intrare devlog nouă';
    document.getElementById('devlogProjId').value  = projId;
    document.getElementById('devlogEditId').value  = entry ? entryId : '';
    document.getElementById('devlogTitle').value   = entry?.title || '';
    document.getElementById('devlogDate').value    = entry?.date  || todayStr();
    document.getElementById('devlogText').value    = entry?.text  || '';
    document.getElementById('devlogDeleteBtn').style.display = entry ? '' : 'none';
    document.getElementById('devlogModal').classList.add('open');
    document.getElementById('devlogTitle').focus();
}

function closeDevlogModal() { document.getElementById('devlogModal').classList.remove('open'); }

async function saveDevlog() {
    const projId = document.getElementById('devlogProjId').value;
    const editId = document.getElementById('devlogEditId').value;
    const title  = document.getElementById('devlogTitle').value.trim();
    const date   = document.getElementById('devlogDate').value;
    const text   = document.getElementById('devlogText').value.trim();

    if (!title) { flash('Introdu un titlu!', 'error'); return; }
    if (!date)  { flash('Selectează o dată!', 'error'); return; }

    const payload = { project_id: projId, title, date, text };
    if (editId) payload.entry_id = editId;

    const url  = editId ? '/api/electronics/project/devlog/edit' : '/api/electronics/project/devlog/add';
    const data = await postJSON(url, payload);
    if (data.status === 'success') {
        closeDevlogModal();
        flash(editId ? '✏️ Intrare actualizată!' : '📝 Intrare devlog adăugată!', 'success');
        await loadAll();
        setTimeout(() => {
            const card = document.getElementById(`proj-${projId}`);
            if (card) card.classList.add('expanded');
        }, 50);
    } else {
        flash(data.message || 'Eroare', 'error');
    }
}

async function deleteDevlog() {
    const projId = document.getElementById('devlogProjId').value;
    const editId = document.getElementById('devlogEditId').value;
    if (!confirm('Ștergi această intrare devlog?')) return;
    const data = await postJSON('/api/electronics/project/devlog/delete', { project_id: projId, entry_id: editId });
    if (data.status === 'success') {
        closeDevlogModal();
        flash('🗑️ Intrare ștearsă', 'success');
        await loadAll();
        setTimeout(() => {
            const card = document.getElementById(`proj-${projId}`);
            if (card) card.classList.add('expanded');
        }, 50);
    } else {
        flash(data.message || 'Eroare', 'error');
    }
}

async function deleteDevlogEntry(projId, entryId) {
    if (!confirm('Ștergi această intrare devlog?')) return;
    const data = await postJSON('/api/electronics/project/devlog/delete', { project_id: projId, entry_id: entryId });
    if (data.status === 'success') {
        flash('🗑️ Intrare ștearsă', 'success');
        await loadAll();
        setTimeout(() => {
            const card = document.getElementById(`proj-${projId}`);
            if (card) card.classList.add('expanded');
        }, 50);
    } else {
        flash(data.message || 'Eroare', 'error');
    }
}

// ============================================================
//  TAB 3 — WISHLIST
// ============================================================

// --- Priority filter ---
function setWishFilter(prio) {
    _wishFilter = prio;
    document.querySelectorAll('.elab-wish-priority-filters .elab-cat-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.prio === prio);
    });
    renderWishTable();
}

// --- Render wishlist table ---
function renderWishTable() {
    const tbody = document.getElementById('wishTableBody');
    let items = _wishFilter === 'all'
        ? _wishlist
        : _wishlist.filter(w => w.priority === _wishFilter);

    if (!items.length) {
        tbody.innerHTML = `<tr><td colspan="6" class="empty-state">
            ${_wishFilter === 'all' ? '🛒 Coșul e gol. Apasă <strong>＋ Adaugă în coș</strong>.' : 'Niciun item cu această prioritate.'}
        </td></tr>`;
        return;
    }

    // Sort: urgent first, then normal, then cand
    const prioOrder = { urgent: 0, normal: 1, cand: 2 };
    const sorted = items.slice().sort((a, b) => (prioOrder[a.priority] ?? 3) - (prioOrder[b.priority] ?? 3));

    tbody.innerHTML = sorted.map(item => {
        const prioCls  = `elab-prio-${item.priority || 'normal'}`;
        const prioText = prioLabel(item.priority);
        const linkHtml = item.link
            ? `<a href="${escHtml(item.link)}" class="elab-wish-link" target="_blank" rel="noopener">🔗 Link</a>`
            : '<span style="color:var(--text-faint)">—</span>';

        return `<tr>
            <td><strong>${escHtml(item.name)}</strong></td>
            <td><span style="font-size:15px;font-weight:900;color:#fff">${item.qty}</span></td>
            <td><span class="elab-prio-badge ${prioCls}">${prioText}</span></td>
            <td>${linkHtml}</td>
            <td style="color:var(--text-muted);font-size:12px">${escHtml(item.reason || '—')}</td>
            <td>
                <div class="elab-row-actions">
                    <button class="elab-btn-buy"  onclick="openBuyModal('${item.id}')">✅ Cumpărat</button>
                    <button class="elab-btn-edit" onclick="openWishModal('${item.id}')">✏️</button>
                    <button class="elab-btn-del"  onclick="deleteWish('${item.id}')">🗑️</button>
                </div>
            </td>
        </tr>`;
    }).join('');
}

// ---- Add/Edit Wishlist Modal ----
function openWishModal(wishId = null) {
    const editItem = wishId ? _wishlist.find(w => w.id === wishId) : null;
    document.getElementById('wishModalTitle').textContent = editItem ? '✏️ Editează item' : '🛒 Adaugă în coș';
    document.getElementById('wishEditId').value = editItem ? wishId : '';
    document.getElementById('wishName').value   = editItem?.name     || '';
    document.getElementById('wishQty').value    = editItem?.qty      ?? '1';
    document.getElementById('wishPrio').value   = editItem?.priority || 'normal';
    document.getElementById('wishLink').value   = editItem?.link     || '';
    document.getElementById('wishReason').value = editItem?.reason   || '';
    document.getElementById('wishModal').classList.add('open');
    document.getElementById('wishName').focus();
}

function closeWishModal() { document.getElementById('wishModal').classList.remove('open'); }

async function saveWish() {
    const name     = document.getElementById('wishName').value.trim();
    const qty      = parseInt(document.getElementById('wishQty').value) || 1;
    const priority = document.getElementById('wishPrio').value;
    const link     = document.getElementById('wishLink').value.trim();
    const reason   = document.getElementById('wishReason').value.trim();
    const editId   = document.getElementById('wishEditId').value;

    if (!name) { flash('Introdu un nume!', 'error'); return; }
    if (qty < 1) { flash('Cantitatea trebuie să fie ≥ 1', 'error'); return; }

    // Backend wishlist/add handles both add and edit (it always creates new if no id match)
    // For edit: we delete old and add new, or just call add with same name
    if (editId) {
        // Delete existing first, then re-add
        await postJSON('/api/electronics/wishlist/delete', { id: editId });
    }

    const payload = { name, qty, priority, link, reason };
    const data = await postJSON('/api/electronics/wishlist/add', payload);
    if (data.status === 'success') {
        closeWishModal();
        flash(editId ? '✏️ Item actualizat!' : '🛒 Adăugat în coș!', 'success');
        await loadAll();
    } else {
        flash(data.message || 'Eroare la salvare', 'error');
    }
}

async function deleteWish(id) {
    const item = _wishlist.find(w => w.id === id);
    if (!confirm(`Ștergi "${item?.name}" din coș?`)) return;
    const data = await postJSON('/api/electronics/wishlist/delete', { id });
    if (data.status === 'success') {
        flash('🗑️ Item șters din coș', 'success');
        await loadAll();
    } else {
        flash(data.message || 'Eroare', 'error');
    }
}

// ---- Buy Wishlist Item Modal ----
function openBuyModal(wishId) {
    const item = _wishlist.find(w => w.id === wishId);
    if (!item) return;

    document.getElementById('buyWishId').value = wishId;
    document.getElementById('buyItemInfo').innerHTML =
        `<strong>${escHtml(item.name)}</strong>
         <span class="elab-prio-badge elab-prio-${item.priority || 'normal'}" style="margin-left:8px">${prioLabel(item.priority)}</span><br>
         <span style="font-size:12px;color:var(--text-muted);margin-top:4px;display:block">Cantitate în coș: <strong style="color:#fff">${item.qty}</strong></span>
         ${item.reason ? `<span style="font-size:12px;color:var(--text-faint)">Motiv: ${escHtml(item.reason)}</span>` : ''}`;

    document.getElementById('buyQty').value   = item.qty;
    document.getElementById('buyCat').value   = 'Altele';
    document.getElementById('buySpecs').value = '';
    document.getElementById('buyModal').classList.add('open');
    document.getElementById('buyQty').focus();
}

function closeBuyModal() { document.getElementById('buyModal').classList.remove('open'); }

async function saveBuy() {
    const wishId   = document.getElementById('buyWishId').value;
    const qty      = parseInt(document.getElementById('buyQty').value);
    const category = document.getElementById('buyCat').value;
    const specs    = document.getElementById('buySpecs').value.trim();
    const item     = _wishlist.find(w => w.id === wishId);

    if (!qty || qty < 1) { flash('Cantitate invalidă', 'error'); return; }

    // Backend endpoint: /api/electronics/wishlist/buy
    // Payload: { id, qty, category, specs }  (backend uses qty_bought internally but accepts 'qty' via body.get('qty'))
    const data = await postJSON('/api/electronics/wishlist/buy', {
        id: wishId,
        qty: qty,
        category,
        specs
    });
    if (data.status === 'success') {
        closeBuyModal();
        flash(`✅ ${item?.name} adăugat în inventar! (${qty} buc)`, 'success');
        await loadAll();
    } else {
        flash(data.message || 'Eroare', 'error');
    }
}
