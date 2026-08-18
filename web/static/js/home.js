'use strict';
/* ══════════════════════════════════════════════════════════════
   CHRONOS OS — Acasă v3
   Hero, pulsul zilei, scene, meniu. Un singur ciclu de încărcare,
   toate fetch-urile în paralel, ceas pe interval de 1s doar
   când tabul e vizibil.
   ══════════════════════════════════════════════════════════════ */

const DAYS_RO = ['duminică', 'luni', 'marți', 'miercuri', 'joi', 'vineri', 'sâmbătă'];
const MONTHS_RO = ['ianuarie', 'februarie', 'martie', 'aprilie', 'mai', 'iunie',
    'iulie', 'august', 'septembrie', 'octombrie', 'noiembrie', 'decembrie'];

function escHtml(s) {
    return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
const $id = id => document.getElementById(id);

function money(n) {
    if (n === null || n === undefined || isNaN(n)) return '—';
    const abs = Math.abs(n);
    if (abs >= 10000) return (n / 1000).toLocaleString('ro-RO', { maximumFractionDigits: 1 }) + 'k';
    return Math.round(n).toLocaleString('ro-RO');
}

/* ─────────── CEAS & SALUT ─────────── */
let clockTimer = null;

function tickClock() {
    const now = new Date();
    const t = $id('heroTime');
    const s = $id('heroSecs');
    if (t) t.textContent = String(now.getHours()).padStart(2, '0') + ':' + String(now.getMinutes()).padStart(2, '0');
    if (s) s.textContent = String(now.getSeconds()).padStart(2, '0');
}

function startClock() {
    tickClock();
    clearInterval(clockTimer);
    clockTimer = setInterval(() => { if (!document.hidden) tickClock(); }, 1000);
}

function setGreeting() {
    const h = new Date().getHours();
    const g = h >= 5 && h < 12 ? 'Bună dimineața'
        : h >= 12 && h < 18 ? 'Bună ziua'
        : h >= 18 && h < 23 ? 'Bună seara'
        : 'Noapte bună';
    const el = $id('homeGreeting');
    if (el) el.textContent = g + ', Sergiu';

    const now = new Date();
    const d = $id('homeDate');
    if (d) d.textContent = `${DAYS_RO[now.getDay()]}, ${now.getDate()} ${MONTHS_RO[now.getMonth()]} ${now.getFullYear()}`;
}

/* Linia de sub salut se compune din ce s-a încărcat efectiv */
const brief = { tasks: null, alerts: 0, pending: 0, targets: null };
function renderLine() {
    const el = $id('homeLine');
    if (!el) return;
    const bits = [];
    if (brief.alerts > 0) bits.push(`${brief.alerts} ${brief.alerts === 1 ? 'lucru cere' : 'lucruri cer'} atenție`);
    if (brief.tasks && brief.tasks.total > 0) {
        bits.push(brief.tasks.done === brief.tasks.total
            ? 'task-urile de azi sunt bifate'
            : `${brief.tasks.done}/${brief.tasks.total} task-uri bifate`);
    }
    if (brief.targets > 0) bits.push(`${brief.targets} ${brief.targets === 1 ? 'target activ' : 'targeturi active'}`);
    if (brief.pending > 0) bits.push(`${money(brief.pending)} RON pe drum`);
    el.textContent = bits.length
        ? bits.join(' · ').replace(/^./, c => c.toUpperCase()) + '.'
        : 'Totul e liniștit. Nimic urgent pe azi.';
}

/* ─────────── ALERTE ─────────── */
async function loadAlerts() {
    try {
        const data = await fetch('/api/home/alerts').then(r => r.json());
        const alerts = data.alerts || [];
        brief.alerts = alerts.length;
        renderLine();

        const panel = $id('alertsPanel');
        if (!alerts.length) { if (panel) panel.hidden = true; return; }
        if (panel) panel.hidden = false;
        $id('alertsCount').textContent = alerts.length;
        $id('alertsList').innerHTML = alerts.map(a => `
            <a href="${a.link || '/targets'}" class="alert-item ${a.severity || 'info'}">
                <span class="ai-ico">${a.icon || '⚠️'}</span>
                <span class="mi-body">
                    <span class="ai-t" style="display:block">${escHtml(a.title)}</span>
                    ${a.detail ? `<span class="ai-d" style="display:block">${escHtml(a.detail)}</span>` : ''}
                </span>
                <span class="ai-go">→</span>
            </a>`).join('');
    } catch (e) { console.error('alerts', e); }
}

/* ─────────── PULS: ZI + TARGETURI ─────────── */
/* Nume propriu, ca să nu se bată cu loadDayStatus() din daily-status.js */
async function loadTodayPulse() {
    try {
        const d = await fetch('/api/day/status').then(r => r.json());

        // Greutate
        const w = $id('pWeight'), wv = $id('pWeightVal');
        w.classList.remove('loading');
        if (d.weight?.logged) { wv.textContent = d.weight.value + ' kg'; w.classList.add('done'); }
        else wv.textContent = d.last_weight_ever ? d.last_weight_ever + ' kg' : '—';

        // Targeturi
        const targets = d.targets || [];
        brief.targets = targets.length;
        $id('pTargets').classList.remove('loading');
        $id('pTargetsVal').textContent = targets.length;

        // Statistici pentru cardurile de meniu
        $id('statGym').textContent = d.weight?.logged
            ? `${d.weight.value} kg logat azi`
            : (d.last_weight_ever ? `Ultima: ${d.last_weight_ever} kg` : 'Nicio înregistrare');
        const jc = d.journal?.entries_today || 0;
        $id('statJournal').textContent = jc > 0 ? `${jc} ${jc === 1 ? 'intrare' : 'intrări'} azi` : 'Nicio intrare azi';
        $id('statTargets').textContent = `${targets.length} ${targets.length === 1 ? 'target activ' : 'targeturi active'}`;

        // Lista de targeturi
        if (targets.length) {
            const sect = $id('activeTargetsSection');
            sect.hidden = false;
            const order = { High: 0, Med: 1, Low: 2 };
            $id('activeTargetsList').innerHTML = [...targets]
                .sort((a, b) => (order[a.priority] ?? 1) - (order[b.priority] ?? 1))
                .slice(0, 6)
                .map(g => {
                    const prog = g.progress || 0;
                    let meta = '';
                    if (g.deadline) {
                        const days = Math.round((new Date(g.deadline) - new Date()) / 86400000);
                        meta = days < 0 ? `⚠️ expirat de ${Math.abs(days)}z`
                             : days === 0 ? '🔥 azi!'
                             : `📅 ${days}z rămase`;
                    }
                    return `<a href="/targets" class="mini-item">
                        <span class="mi-ico">🎯</span>
                        <span class="mi-body">
                            <span class="mi-t" style="display:block">${escHtml(g.title || '')}</span>
                            <span class="mi-s" style="display:block">${meta}</span>
                        </span>
                        <span class="mi-prog">
                            <span class="mi-bar"><i style="width:${prog}%"></i></span>
                            <span class="mi-pct">${prog}%</span>
                        </span>
                    </a>`;
                }).join('');
        }
        renderLine();
    } catch (e) { console.error('day status', e); }
}

/* ─────────── PULS: TASK-URI ZILNICE ─────────── */
async function loadDailyTasks() {
    try {
        const d = await fetch('/api/daily-tasks').then(r => r.json());
        const today = new Date().toISOString().slice(0, 10);
        const total = (d.tasks || []).length;
        const done = ((d.checks || {})[today] || []).length;

        const card = $id('pTasks'), val = $id('pTasksVal'), bar = $id('pTasksBar');
        card.classList.remove('loading');
        if (!total) { val.textContent = '—'; return; }
        val.textContent = `${done}/${total}`;
        bar.style.width = Math.round(done / total * 100) + '%';
        if (done === total) card.classList.add('done');

        brief.tasks = { done, total };
        renderLine();
    } catch (e) { console.error('tasks', e); }
}

/* ─────────── PULS: MENTENANȚĂ ─────────── */
async function loadMaintenance() {
    try {
        const d = await fetch('/api/maintenance').then(r => r.json());
        const today = new Date();
        const upcoming = [];
        let overdue = 0;

        (d.items || []).forEach(item => (item.tasks || []).forEach(task => {
            const interval = task.interval_days || 30;
            const daysLeft = task.last_done
                ? interval - Math.round((today - new Date(task.last_done)) / 86400000)
                : -999;
            if (daysLeft <= 14) {
                upcoming.push({ item, task, daysLeft });
                if (daysLeft <= 0) overdue++;
            }
        }));

        const card = $id('pMaint'), val = $id('pMaintVal');
        card.classList.remove('loading');
        if (overdue > 0) { val.textContent = overdue + ' depășite'; card.classList.add('alert'); }
        else if (upcoming.length) { val.textContent = upcoming.length + ' curând'; card.classList.add('warn'); }
        else { val.textContent = 'OK'; card.classList.add('done'); }

        if (upcoming.length) {
            $id('upcomingMaintSection').hidden = false;
            upcoming.sort((a, b) => a.daysLeft - b.daysLeft);
            $id('upcomingMaintList').innerHTML = upcoming.slice(0, 6).map(({ item, task, daysLeft }) => {
                const cls = daysLeft < 0 ? 'overdue' : daysLeft <= 3 ? 'soon' : '';
                const txt = daysLeft < -900 ? 'niciodată'
                    : daysLeft < 0 ? `${Math.abs(daysLeft)}z depășit`
                    : daysLeft === 0 ? 'azi!' : `${daysLeft}z`;
                return `<a href="/targets" class="mini-item ${cls}">
                    <span class="mi-ico">${item.emoji || '🔧'}</span>
                    <span class="mi-body">
                        <span class="mi-t" style="display:block">${escHtml(item.name)}</span>
                        <span class="mi-s" style="display:block">${escHtml(task.name)} · la ${task.interval_days} zile</span>
                    </span>
                    <span class="mi-tag">${txt}</span>
                </a>`;
            }).join('');
        }
    } catch (e) { console.error('maintenance', e); }
}

/* ─────────── PULS: BANI ─────────── */
async function loadFinance() {
    try {
        const d = await fetch('/api/finance/data').then(r => r.json());
        const s = d.summary || {}, inv = d.inv_summary || {};

        $id('pCash').classList.remove('loading');
        $id('pCashVal').textContent = money(s.total) + ' RON';

        const road = $id('pRoad');
        road.classList.remove('loading');
        $id('pRoadVal').textContent = inv.pending_total > 0 ? money(inv.pending_total) + ' RON' : '—';
        if (inv.pending_total > 0) road.classList.add('warn');

        brief.pending = inv.pending_total || 0;

        const bits = [`${money(s.total)} RON lichid`];
        if (inv.units_in_stock > 0) bits.push(`${inv.units_in_stock} buc. pe stoc`);
        if (inv.pending_count > 0) bits.push(`${inv.pending_count} de încasat`);
        $id('statFinance').textContent = bits.join(' · ');
        renderLine();
    } catch (e) {
        $id('pCash')?.classList.remove('loading');
        $id('pRoad')?.classList.remove('loading');
    }
}

/* ─────────── STAT: ELECTRONICS ─────────── */
async function loadElectronicsStat() {
    try {
        const d = await fetch('/api/electronics/data').then(r => r.json());
        const comps = (d.components || []).length;
        const projs = (d.projects || []);
        const active = projs.filter(p => p.status === 'activ').length;
        $id('statElec').textContent = `${comps} componente · ${active}/${projs.length} proiecte active`;
    } catch (e) { $id('statElec').textContent = '—'; }
}

/* ══════════════════════════════════════════════════════════════
   SCENE
   ══════════════════════════════════════════════════════════════ */

const SCENE_PALETTES = [
    { color: 'rgba(139,92,246,.15)', border: 'rgba(139,92,246,.35)' },
    { color: 'rgba(236,72,153,.14)', border: 'rgba(236,72,153,.35)' },
    { color: 'rgba(14,165,233,.14)', border: 'rgba(14,165,233,.35)' },
    { color: 'rgba(34,197,94,.12)',  border: 'rgba(34,197,94,.30)'  },
    { color: 'rgba(245,158,11,.14)', border: 'rgba(245,158,11,.35)' },
    { color: 'rgba(239,68,68,.13)',  border: 'rgba(239,68,68,.32)'  },
    { color: 'rgba(20,184,166,.13)', border: 'rgba(20,184,166,.30)' },
    { color: 'rgba(249,115,22,.14)', border: 'rgba(249,115,22,.33)' },
];

let _scenes = [], _capturedLights = null, _manualOpen = false, _emojiOpen = false;

async function loadScenes() {
    try {
        const d = await fetch('/api/scenes').then(r => r.json());
        _scenes = d.scenes || [];
        renderScenes();
        registerSceneActions();
    } catch (e) { console.error('scenes', e); }
}

function renderScenes() {
    const grid = $id('scenesGrid'), empty = $id('scenesEmpty');
    if (!grid) return;

    if (!_scenes.length) {
        grid.innerHTML = '';
        if (empty) { empty.style.display = ''; grid.appendChild(empty); }
        return;
    }
    if (empty) empty.style.display = 'none';

    grid.innerHTML = _scenes.map((s, i) => {
        const p = SCENE_PALETTES[i % SCENE_PALETTES.length];
        const hasLights = s.lights && (s.lights.main || s.lights.floor);
        const music = s.music_prompt
            ? escHtml(s.music_prompt.slice(0, 40)) + (s.music_prompt.length > 40 ? '…' : '')
            : '<span style="opacity:.4">fără muzică</span>';
        return `<div class="scene-card" style="--sc-color:${p.color};--sc-border:${p.border}">
            <div class="scene-card-emoji">${s.emoji || '🎬'}</div>
            <div class="scene-card-name">${escHtml(s.name)}</div>
            <div class="scene-card-music">🎵 ${music}</div>
            <div class="scene-card-lights">${hasLights ? '💡 lumini setate' : '💡 fără lumini'}</div>
            <div class="scene-card-actions">
                <button class="scene-play-btn" id="scene-play-${s.id}" onclick="activateScene('${s.id}')">▶ Activează</button>
                <button class="scene-icon-btn" onclick="openSceneModal('${s.id}')" title="Editează">✏</button>
                <button class="scene-icon-btn del" onclick="deleteScene('${s.id}')" title="Șterge">🗑</button>
            </div>
        </div>`;
    }).join('');
}

/* Scenele devin comenzi în paleta Ctrl+K */
function registerSceneActions() {
    if (!window.Chronos) return;
    window.Chronos.registerActions([
        { icon: '✨', label: 'Scenă nouă', sub: 'acasă', run: () => openSceneModal() },
        ..._scenes.map(s => ({
            icon: s.emoji || '🎬',
            label: `Activează „${s.name}”`,
            sub: 'scenă',
            run: () => activateScene(s.id)
        }))
    ]);
}

function openSceneModal(sceneId) {
    const ov = $id('sceneModalOverlay');
    _capturedLights = null; _manualOpen = false; _emojiOpen = false;
    $id('sceneManualPanel').style.display = 'none';
    $id('sceneManualToggle').classList.remove('active');
    $id('sceneEmojiGrid').classList.remove('open');
    $id('sceneLightsStatus').style.display = 'none';
    $id('sceneLoadingOverlay').style.display = 'none';

    if (sceneId) {
        const s = _scenes.find(x => x.id === sceneId);
        if (!s) return;
        $id('sceneModalTitle').textContent = '✏ Editează scena';
        $id('sceneEditId').value = sceneId;
        $id('sceneEmojiBtn').textContent = s.emoji || '🎬';
        $id('sceneNameInput').value = s.name || '';
        $id('sceneMusicPrompt').value = s.music_prompt || '';
        if (s.lights) {
            _capturedLights = s.lights;
            showLightsStatus('ok', '✅ Configurație de lumini salvată');
            populateManualFromLights(s.lights);
        }
    } else {
        $id('sceneModalTitle').textContent = '✨ Scenă nouă';
        $id('sceneEditId').value = '';
        $id('sceneEmojiBtn').textContent = '🎬';
        $id('sceneNameInput').value = '';
        $id('sceneMusicPrompt').value = '';
        resetManualFields();
    }

    ov.classList.add('active');
    setTimeout(() => $id('sceneNameInput').focus(), 110);
}

function closeSceneModal() {
    $id('sceneModalOverlay').classList.remove('active');
    $id('sceneEmojiGrid').classList.remove('open');
    _emojiOpen = false;
}

function toggleEmojiPicker() {
    _emojiOpen = !_emojiOpen;
    $id('sceneEmojiGrid').classList.toggle('open', _emojiOpen);
}

function pickEmoji(e) {
    $id('sceneEmojiBtn').textContent = e;
    _emojiOpen = false;
    $id('sceneEmojiGrid').classList.remove('open');
}

function toggleManualLights() {
    _manualOpen = !_manualOpen;
    $id('sceneManualPanel').style.display = _manualOpen ? '' : 'none';
    $id('sceneManualToggle').classList.toggle('active', _manualOpen);
}

async function captureFromWLED() {
    const btn = $id('sceneCaptureBtn');
    btn.disabled = true;
    btn.innerHTML = '<span>⏳</span><span>Se capturează…</span>';
    try {
        const d = await fetch('/api/scenes/wled-snapshot').then(r => r.json());
        if (d.status === 'ok') {
            _capturedLights = { main: d.main, floor: d.floor };
            const parts = [];
            if (d.main) parts.push(`Main: bri ${d.main.bri ?? '?'}`);
            if (d.floor) parts.push(`Floor: bri ${d.floor.bri ?? '?'}`);
            showLightsStatus('ok', `✅ Capturat! ${parts.join(' · ')}`);
            populateManualFromLights(_capturedLights);
        } else {
            showLightsStatus('error', `❌ ${d.message || 'WLED offline'}`);
        }
    } catch (e) { showLightsStatus('error', '❌ Eroare la capturare'); }
    btn.disabled = false;
    btn.innerHTML = '<span>📸</span><span>Capturează din WLED acum</span>';
}

function populateManualFromLights(lights) {
    if (!lights) return;
    const fill = (p, z) => {
        if (!z) return;
        const on = $id(p + 'On'), bri = $id(p + 'Bri'), briV = $id(p + 'BriVal');
        if (on) on.checked = z.on !== false;
        if (bri) { bri.value = z.bri ?? 128; if (briV) briV.textContent = bri.value; }
        const seg = (z.seg || [])[0] || {};
        const set = (id, v, valId) => {
            const el = $id(id); if (!el) return;
            el.value = v; const vEl = valId && $id(valId); if (vEl) vEl.textContent = v;
        };
        set(p + 'Fx', seg.fx ?? 0);
        set(p + 'Pal', seg.pal ?? 0);
        set(p + 'Sx', seg.sx ?? 128, p + 'SxVal');
        set(p + 'Ix', seg.ix ?? 128, p + 'IxVal');
    };
    fill('main', lights.main);
    fill('floor', lights.floor);
}

function showLightsStatus(type, msg) {
    const el = $id('sceneLightsStatus');
    if (!el) return;
    el.className = `scene-lights-status ${type}`;
    el.textContent = msg;
    el.style.display = '';
}

function resetManualFields() {
    const defaults = {
        mainOn: true, mainBri: 128, mainFx: 0, mainPal: 0, mainSx: 128, mainIx: 128,
        floorOn: true, floorBri: 180, floorFx: 0, floorPal: 0, floorSx: 128, floorIx: 128
    };
    for (const [id, v] of Object.entries(defaults)) {
        const el = $id(id); if (!el) continue;
        if (el.type === 'checkbox') el.checked = v; else el.value = String(v);
    }
    ['mainBriVal', 'mainSxVal', 'mainIxVal', 'floorBriVal', 'floorSxVal', 'floorIxVal'].forEach(id => {
        const src = $id(id.replace('Val', '')); const el = $id(id);
        if (src && el) el.textContent = src.value;
    });
}

function buildLightsFromManual() {
    const zone = p => ({
        on: $id(p + 'On')?.checked ?? true,
        bri: parseInt($id(p + 'Bri')?.value ?? '128'),
        seg: [{
            fx: parseInt($id(p + 'Fx')?.value ?? '0'),
            pal: parseInt($id(p + 'Pal')?.value ?? '0'),
            sx: parseInt($id(p + 'Sx')?.value ?? '128'),
            ix: parseInt($id(p + 'Ix')?.value ?? '128')
        }]
    });
    return { main: zone('main'), floor: zone('floor') };
}

async function saveScene() {
    const name = ($id('sceneNameInput')?.value || '').trim();
    if (!name) { showToast('⚠️ Dă-i un nume scenei', 'error'); return; }

    const payload = {
        name,
        emoji: $id('sceneEmojiBtn')?.textContent?.trim() || '🎬',
        music_prompt: ($id('sceneMusicPrompt')?.value || '').trim(),
        lights: _capturedLights || (_manualOpen ? buildLightsFromManual() : null)
    };
    const editId = $id('sceneEditId')?.value || '';
    if (editId) payload.id = editId;

    const ov = $id('sceneLoadingOverlay');
    ov.style.display = '';
    $id('sceneLoadingText').textContent = 'Se salvează…';

    try {
        const d = await fetch('/api/scenes/save', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        }).then(r => r.json());
        if (d.status === 'success') {
            await loadScenes();
            closeSceneModal();
            showToast(`✅ Scena „${name}” salvată`, 'success');
        } else showToast('❌ Eroare la salvare', 'error');
    } catch (e) { showToast('❌ Eroare la salvare', 'error'); }

    ov.style.display = 'none';
}

async function activateScene(id) {
    const btn = $id(`scene-play-${id}`);
    if (btn) { btn.classList.add('playing'); btn.textContent = '⏳ Pornesc…'; btn.disabled = true; }
    try {
        const d = await fetch('/api/scenes/activate', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id })
        }).then(r => r.json());

        if (d.status === 'success') {
            const r = d.results || {}, parts = [];
            if (r.lights) parts.push(r.lights === 'ok' ? '💡 LED OK' : `💡 ${r.lights}`);
            if (r.music) parts.push((r.music === 'ok' || r.music === 'success') ? '🎵 Muzică OK' : `🎵 ${r.music}`);
            showToast(`▶ ${escHtml(d.scene_name)} — ${parts.join(' · ')}`, 'success');
        } else showToast('❌ Eroare la activare', 'error');
    } catch (e) { showToast('❌ Eroare de conexiune', 'error'); }

    if (btn) setTimeout(() => {
        btn.classList.remove('playing'); btn.textContent = '▶ Activează'; btn.disabled = false;
    }, 2200);
}

async function deleteScene(id) {
    const s = _scenes.find(x => x.id === id);
    if (!confirm(`Ștergi scena „${s?.name || ''}”?`)) return;
    try {
        await fetch('/api/scenes/delete', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id })
        });
        await loadScenes();
        showToast('🗑 Scenă ștearsă', 'success');
    } catch (e) { showToast('❌ Eroare la ștergere', 'error'); }
}

function showToast(msg, type = 'success') {
    const t = $id('sceneToast');
    if (!t) return;
    t.textContent = msg;
    t.className = `scene-toast ${type} show`;
    clearTimeout(t._t);
    t._t = setTimeout(() => t.classList.remove('show'), 3400);
}

/* ══════════════════════════════════════════════════════════════
   BOOT
   ══════════════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
    setGreeting();
    startClock();

    // Butoanele din hero
    $id('heroAsk')?.addEventListener('click', () => window.Chronos?.openChronos());
    $id('heroSearch')?.addEventListener('click', () => window.Chronos?.openPalette());

    // Modalul de scenă
    $id('sceneModalClose')?.addEventListener('click', closeSceneModal);
    $id('sceneCancelBtn')?.addEventListener('click', closeSceneModal);
    $id('sceneSaveBtn')?.addEventListener('click', saveScene);
    $id('sceneCaptureBtn')?.addEventListener('click', captureFromWLED);
    $id('sceneManualToggle')?.addEventListener('click', toggleManualLights);
    $id('sceneModalOverlay')?.addEventListener('click', e => {
        if (e.target === e.currentTarget) closeSceneModal();
    });
    $id('sceneEmojiGrid')?.addEventListener('click', e => {
        const b = e.target.closest('[data-emoji]');
        if (b) pickEmoji(b.dataset.emoji);
    });
    document.addEventListener('click', e => {
        if (_emojiOpen && !e.target.closest('.scene-emoji-picker')) {
            _emojiOpen = false;
            $id('sceneEmojiGrid')?.classList.remove('open');
        }
    });
    [['mainBri', 'mainBriVal'], ['mainSx', 'mainSxVal'], ['mainIx', 'mainIxVal'],
     ['floorBri', 'floorBriVal'], ['floorSx', 'floorSxVal'], ['floorIx', 'floorIxVal']]
        .forEach(([s, v]) => {
            const sl = $id(s), val = $id(v);
            if (sl && val) sl.addEventListener('input', () => { val.textContent = sl.value; });
        });

    // Datele — toate în paralel
    loadAlerts();
    loadTodayPulse();
    loadDailyTasks();
    loadMaintenance();
    loadFinance();
    loadElectronicsStat();
    loadScenes();
});
