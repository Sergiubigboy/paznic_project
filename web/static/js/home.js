'use strict';
// ============================================================
//  HOME DASHBOARD — home.js
//  Chronos OS | Smart home page with alerts, stats, menu, scenes
// ============================================================

const DAYS_RO = ['Duminică','Luni','Marți','Miercuri','Joi','Vineri','Sâmbătă'];
const MONTHS_RO = ['ianuarie','februarie','martie','aprilie','mai','iunie',
                   'iulie','august','septembrie','octombrie','noiembrie','decembrie'];

// ---- CLOCK & DATE ----
function updateClock() {
    const now = new Date();
    const h = String(now.getHours()).padStart(2,'0');
    const m = String(now.getMinutes()).padStart(2,'0');
    const el = document.getElementById('heroTime');
    if (el) el.textContent = `${h}:${m}`;
}

function setGreeting() {
    const h = new Date().getHours();
    let greet = 'Bun venit';
    if (h >= 5  && h < 12) greet = 'Bună dimineața ☀️';
    else if (h >= 12 && h < 17) greet = 'Bună ziua 🌤️';
    else if (h >= 17 && h < 21) greet = 'Bună seara 🌆';
    else greet = 'Noapte bună 🌙';
    const el = document.getElementById('homeGreeting');
    if (el) el.textContent = greet;
}

function setDate() {
    const now = new Date();
    const dayName = DAYS_RO[now.getDay()];
    const day = now.getDate();
    const month = MONTHS_RO[now.getMonth()];
    const year = now.getFullYear();
    const el = document.getElementById('homeDate');
    if (el) el.textContent = `${dayName}, ${day} ${month} ${year}`;
}

// ---- ALERTS ----
async function loadAlerts() {
    try {
        const res  = await fetch('/api/home/alerts');
        const data = await res.json();
        const alerts = data.alerts || [];

        const panel = document.getElementById('alertsPanel');
        const list  = document.getElementById('alertsList');
        const badge = document.getElementById('alertsCount');

        if (!alerts.length) {
            if (panel) panel.style.display = 'none';
            return;
        }

        if (badge) badge.textContent = alerts.length;
        if (panel) panel.style.display = '';

        if (list) {
            list.innerHTML = alerts.map(a => `
                <a href="${a.link || '/targets'}" class="home-alert-item ${a.severity || 'info'}">
                    <span class="hai-icon">${a.icon || '⚠️'}</span>
                    <div class="hai-body">
                        <div class="hai-title">${escHtml(a.title)}</div>
                        ${a.detail ? `<div class="hai-detail">${escHtml(a.detail)}</div>` : ''}
                    </div>
                    <span class="hai-link">→</span>
                </a>
            `).join('');
        }
    } catch(e) { console.error('Alerts error:', e); }
}

// ---- DAY STATUS ----
async function loadDayStatus() {
    try {
        const res  = await fetch('/api/day/status');
        const data = await res.json();

        // Weight
        const qWeight = document.getElementById('qWeight');
        const qWeightVal = document.getElementById('qWeightVal');
        if (data.weight?.logged) {
            qWeightVal.textContent = data.weight.value + ' kg';
            qWeight.classList.remove('loading');
            qWeight.classList.add('done');
        } else {
            qWeightVal.textContent = data.last_weight_ever ? (data.last_weight_ever + ' kg') : '—';
            qWeight.classList.remove('loading');
        }

        // Targets
        const targets = data.targets || [];
        const qTargets = document.getElementById('qTargets');
        const qTargetsVal = document.getElementById('qTargetsVal');
        qTargetsVal.textContent = targets.length;
        qTargets.classList.remove('loading');

        // Render active targets section
        if (targets.length) {
            const sect = document.getElementById('activeTargetsSection');
            const list = document.getElementById('activeTargetsList');
            if (sect) sect.style.display = '';
            if (list) {
                const sorted = [...targets].sort((a,b) => {
                    const order = {High:0, Med:1, Low:2};
                    return (order[a.priority]??1) - (order[b.priority]??1);
                });
                list.innerHTML = sorted.map(g => {
                    const prog = g.progress || 0;
                    const dl = g.deadline ? (() => {
                        const d = Math.round((new Date(g.deadline) - new Date()) / 86400000);
                        return d < 0 ? `<span style="color:var(--red)">⚠️ Expirat ${Math.abs(d)}z</span>` :
                               d === 0 ? `<span style="color:var(--orange)">🔥 Azi!</span>` :
                               `📅 ${d}z rămase`;
                    })() : '';
                    return `<a href="/targets" class="home-target-item">
                        <div class="hti-body">
                            <div class="hti-title">${escHtml(g.title || '')}</div>
                            <div class="hti-meta">${dl}</div>
                        </div>
                        <div class="hti-prog-wrap">
                            <div class="hti-prog-bar"><div class="hti-prog-fill" style="width:${prog}%"></div></div>
                            <div class="hti-prog-pct">${prog}%</div>
                        </div>
                    </a>`;
                }).join('');
            }
        }

        // Journal stat
        const jStat = document.getElementById('statJournal');
        const jCount = data.journal?.entries_today || 0;
        if (jStat) jStat.textContent = jCount > 0 ? `${jCount} întrări azi` : 'Nicio intrare azi';

        // Gym stat
        const gymStat = document.getElementById('statGym');
        if (gymStat) {
            if (data.weight?.logged) {
                gymStat.textContent = `${data.weight.value} kg azi`;
            } else if (data.last_weight_ever) {
                gymStat.textContent = `Ultimul: ${data.last_weight_ever} kg`;
            } else {
                gymStat.textContent = 'Nicio înregistrare';
            }
        }

        // Targets stat
        const tStat = document.getElementById('statTargets');
        if (tStat) tStat.textContent = `${targets.length} targeturi active`;

    } catch(e) { console.error('Day status error:', e); }
}

// ---- DAILY TASKS ----
async function loadDailyTasks() {
    try {
        const res  = await fetch('/api/daily-tasks');
        const data = await res.json();
        const today = new Date().toISOString().slice(0,10);
        const total   = (data.tasks || []).length;
        const checked = ((data.checks || {})[today] || []).length;

        const qTasks = document.getElementById('qTasks');
        const qTasksVal = document.getElementById('qTasksVal');

        if (total === 0) {
            qTasksVal.textContent = '—';
            qTasks.classList.remove('loading');
        } else {
            qTasksVal.textContent = `${checked}/${total}`;
            qTasks.classList.remove('loading');
            if (checked === total) qTasks.classList.add('done');
        }
    } catch(e) { console.error('Tasks error:', e); }
}

// ---- MAINTENANCE ----
async function loadMaintenance() {
    try {
        const res  = await fetch('/api/maintenance');
        const data = await res.json();
        const today = new Date();

        const upcoming = [];
        let overdueCount = 0;
        let totalPending = 0;

        (data.items || []).forEach(item => {
            (item.tasks || []).forEach(task => {
                const interval = task.interval_days || 30;
                let daysLeft;
                if (task.last_done) {
                    const last = new Date(task.last_done);
                    const daysSince = Math.round((today - last) / 86400000);
                    daysLeft = interval - daysSince;
                } else {
                    daysLeft = -999; // Never done
                }

                if (daysLeft <= 14) {
                    upcoming.push({ item, task, daysLeft });
                    if (daysLeft <= 0) overdueCount++;
                    totalPending++;
                }
            });
        });

        // Update quick stat
        const qMaint = document.getElementById('qMaint');
        const qMaintVal = document.getElementById('qMaintVal');
        qMaint.classList.remove('loading');
        if (overdueCount > 0) {
            qMaintVal.textContent = overdueCount + ' depășite';
            qMaint.classList.add('alert');
        } else if (totalPending > 0) {
            qMaintVal.textContent = totalPending + ' curând';
        } else {
            qMaintVal.textContent = 'OK';
            qMaint.classList.add('done');
        }

        // Render upcoming section
        if (upcoming.length) {
            const sect = document.getElementById('upcomingMaintSection');
            const list = document.getElementById('upcomingMaintList');
            if (sect) sect.style.display = '';
            if (list) {
                upcoming.sort((a,b) => a.daysLeft - b.daysLeft);
                list.innerHTML = upcoming.slice(0,6).map(({ item, task, daysLeft }) => {
                    const cls = daysLeft < 0 ? 'overdue' : daysLeft <= 3 ? 'soon' : 'ok';
                    const txt = daysLeft < 0 ? `${Math.abs(daysLeft)}z dep.` :
                                daysLeft === 0 ? 'Azi!' :
                                `${daysLeft}z`;
                    return `<a href="/targets" class="home-maint-item ${cls}">
                        <span class="hmi-emoji">${item.emoji || '🔧'}</span>
                        <div class="hmi-body">
                            <div class="hmi-name">${escHtml(item.name)}</div>
                            <div class="hmi-task">${escHtml(task.name)} · la ${task.interval_days} zile</div>
                        </div>
                        <span class="hmi-countdown ${cls}">${txt}</span>
                    </a>`;
                }).join('');
            }
        }
    } catch(e) { console.error('Maintenance error:', e); }
}

// ---- ELECTRONICS STAT ----
async function loadElectronicsStat() {
    try {
        const res  = await fetch('/api/electronics/data');
        const data = await res.json();
        const el = document.getElementById('statElec');
        if (el) {
            const comps = (data.components || []).length;
            const projs = (data.projects  || []).length;
            const active = (data.projects || []).filter(p => p.status === 'activ').length;
            el.textContent = `${comps} comp. · ${active}/${projs} proiecte active`;
        }
    } catch(e) {}
}

// ================================================================
//  SCENES SYSTEM
// ================================================================

// Color palettes per card index for visual variety
const SCENE_PALETTES = [
    { color: 'rgba(139,92,246,0.15)',   border: 'rgba(139,92,246,0.35)'   }, // purple
    { color: 'rgba(236,72,153,0.14)',   border: 'rgba(236,72,153,0.35)'   }, // pink
    { color: 'rgba(14,165,233,0.14)',   border: 'rgba(14,165,233,0.35)'   }, // sky
    { color: 'rgba(34,197,94,0.12)',    border: 'rgba(34,197,94,0.3)'     }, // green
    { color: 'rgba(245,158,11,0.14)',   border: 'rgba(245,158,11,0.35)'   }, // amber
    { color: 'rgba(239,68,68,0.13)',    border: 'rgba(239,68,68,0.32)'    }, // red
    { color: 'rgba(20,184,166,0.13)',   border: 'rgba(20,184,166,0.3)'    }, // teal
    { color: 'rgba(249,115,22,0.14)',   border: 'rgba(249,115,22,0.33)'   }, // orange
];

let _scenes = [];
let _capturedLights = null;
let _manualOpen = false;
let _emojiOpen = false;

// ── Load & render scenes
async function loadScenes() {
    try {
        const res = await fetch('/api/scenes');
        const data = await res.json();
        _scenes = data.scenes || [];
        renderScenes();
    } catch(e) { console.error('Scenes error:', e); }
}

function renderScenes() {
    const grid = document.getElementById('scenesGrid');
    const empty = document.getElementById('scenesEmpty');
    if (!grid) return;

    if (!_scenes.length) {
        grid.innerHTML = '';
        if (empty) { empty.style.display = ''; grid.appendChild(empty); }
        return;
    }

    if (empty) empty.style.display = 'none';

    grid.innerHTML = _scenes.map((scene, i) => {
        const pal = SCENE_PALETTES[i % SCENE_PALETTES.length];
        const hasLights = scene.lights && (scene.lights.main || scene.lights.floor);
        const lightsLabel = hasLights ? '💡 Lumini setate' : '💡 Fără lumini';
        const musicLabel = scene.music_prompt
            ? escHtml(scene.music_prompt.slice(0, 45)) + (scene.music_prompt.length > 45 ? '…' : '')
            : '<span style="opacity:0.4">Fără muzică</span>';
        return `
        <div class="scene-card" id="scene-card-${scene.id}"
             style="--sc-color:${pal.color};--sc-border:${pal.border}">
            <div class="scene-card-emoji">${scene.emoji || '🎬'}</div>
            <div class="scene-card-name">${escHtml(scene.name)}</div>
            <div class="scene-card-music">${musicLabel}</div>
            <div class="scene-card-lights">${lightsLabel}</div>
            <div class="scene-card-actions">
                <button class="scene-play-btn" id="scene-play-${scene.id}"
                        onclick="activateScene('${scene.id}')" title="Activează scena">
                    ▶ Activează
                </button>
                <button class="scene-icon-btn" onclick="openSceneModal('${scene.id}')" title="Editează">✏</button>
                <button class="scene-icon-btn del" onclick="deleteScene('${scene.id}')" title="Șterge">🗑</button>
            </div>
        </div>`;
    }).join('');
}

// ── Open modal (new or edit)
function openSceneModal(sceneId) {
    const overlay = document.getElementById('sceneModalOverlay');
    const titleEl = document.getElementById('sceneModalTitle');
    const editIdEl = document.getElementById('sceneEditId');

    // Reset state
    _capturedLights = null;
    _manualOpen = false;
    _emojiOpen = false;
    document.getElementById('sceneManualPanel').style.display = 'none';
    document.getElementById('sceneManualToggle').classList.remove('active');
    document.getElementById('sceneEmojiGrid').classList.remove('open');
    document.getElementById('sceneLightsStatus').style.display = 'none';
    document.getElementById('sceneLoadingOverlay').style.display = 'none';

    if (sceneId) {
        const scene = _scenes.find(s => s.id === sceneId);
        if (!scene) return;
        titleEl.textContent = '✏ Editează Scena';
        editIdEl.value = sceneId;
        document.getElementById('sceneEmojiBtn').textContent = scene.emoji || '🎬';
        document.getElementById('sceneNameInput').value = scene.name || '';
        document.getElementById('sceneMusicPrompt').value = scene.music_prompt || '';

        // Populate lights if available
        if (scene.lights) {
            _capturedLights = scene.lights;
            showLightsStatus('ok', '✅ Configurație lumini salvată');
            populateManualFromLights(scene.lights);
        }
    } else {
        titleEl.textContent = '✨ Scenă Nouă';
        editIdEl.value = '';
        document.getElementById('sceneEmojiBtn').textContent = '🎬';
        document.getElementById('sceneNameInput').value = '';
        document.getElementById('sceneMusicPrompt').value = '';
        resetManualFields();
    }

    overlay.classList.add('active');
    setTimeout(() => document.getElementById('sceneNameInput').focus(), 100);
}

function closeSceneModal(event, force) {
    if (!force && event && event.target !== document.getElementById('sceneModalOverlay')) return;
    const overlay = document.getElementById('sceneModalOverlay');
    overlay.classList.remove('active');
    document.getElementById('sceneEmojiGrid').classList.remove('open');
    _emojiOpen = false;
}

// ── Emoji picker
function toggleEmojiPicker() {
    _emojiOpen = !_emojiOpen;
    document.getElementById('sceneEmojiGrid').classList.toggle('open', _emojiOpen);
}

// Attach emoji grid click via event delegation
document.addEventListener('DOMContentLoaded', () => {
    const emojiGrid = document.getElementById('sceneEmojiGrid');
    if (emojiGrid) {
        emojiGrid.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-emoji]');
            if (!btn) return;
            pickEmoji(btn.dataset.emoji);
        });
    }

    // Close emoji on outside click
    document.addEventListener('click', (e) => {
        if (_emojiOpen && !e.target.closest('.scene-emoji-picker')) {
            _emojiOpen = false;
            const grid = document.getElementById('sceneEmojiGrid');
            if (grid) grid.classList.remove('open');
        }
    });

    // Modal close button
    const closeBtn = document.getElementById('sceneModalClose');
    if (closeBtn) closeBtn.addEventListener('click', () => closeSceneModal(null, true));

    // Cancel button
    const cancelBtn = document.getElementById('sceneCancelBtn');
    if (cancelBtn) cancelBtn.addEventListener('click', () => closeSceneModal(null, true));

    // Save button
    const saveBtn = document.getElementById('sceneSaveBtn');
    if (saveBtn) saveBtn.addEventListener('click', saveScene);

    // Capture button
    const captureBtn = document.getElementById('sceneCaptureBtn');
    if (captureBtn) captureBtn.addEventListener('click', captureFromWLED);

    // Manual toggle button
    const manualToggle = document.getElementById('sceneManualToggle');
    if (manualToggle) manualToggle.addEventListener('click', toggleManualLights);

    // Overlay click to close
    const overlay = document.getElementById('sceneModalOverlay');
    if (overlay) overlay.addEventListener('click', (e) => {
        if (e.target === overlay) closeSceneModal(null, true);
    });

    // Slider live update for manual fields
    setupSliderSync('mainBri',  'mainBriVal');
    setupSliderSync('mainSx',   'mainSxVal');
    setupSliderSync('mainIx',   'mainIxVal');
    setupSliderSync('floorBri', 'floorBriVal');
    setupSliderSync('floorSx',  'floorSxVal');
    setupSliderSync('floorIx',  'floorIxVal');

    // Init
    setGreeting();
    setDate();
    updateClock();
    setInterval(updateClock, 30000);

    loadAlerts();
    loadDayStatus();
    loadDailyTasks();
    loadMaintenance();
    loadElectronicsStat();
    loadScenes();
});

function setupSliderSync(sliderId, valId) {
    const slider = document.getElementById(sliderId);
    const valEl  = document.getElementById(valId);
    if (slider && valEl) {
        slider.addEventListener('input', () => { valEl.textContent = slider.value; });
    }
}

function pickEmoji(emoji) {
    document.getElementById('sceneEmojiBtn').textContent = emoji;
    _emojiOpen = false;
    document.getElementById('sceneEmojiGrid').classList.remove('open');
}

// ── Toggle manual lights panel
function toggleManualLights() {
    _manualOpen = !_manualOpen;
    const panel  = document.getElementById('sceneManualPanel');
    const toggle = document.getElementById('sceneManualToggle');
    panel.style.display = _manualOpen ? '' : 'none';
    toggle.classList.toggle('active', _manualOpen);
}

// ── Capture from WLED
async function captureFromWLED() {
    const btn = document.getElementById('sceneCaptureBtn');
    btn.classList.add('loading');
    btn.innerHTML = '<span>⏳</span><span>Se capturează...</span>';

    try {
        const res = await fetch('/api/scenes/wled-snapshot');
        const data = await res.json();

        if (data.status === 'ok') {
            _capturedLights = { main: data.main, floor: data.floor };

            let parts = [];
            if (data.main)  parts.push(`Main: bri ${data.main.bri ?? '?'}`);
            if (data.floor) parts.push(`Floor: bri ${data.floor.bri ?? '?'}`);
            showLightsStatus('ok', `✅ Capturat! ${parts.join(' · ')}`);

            // Also populate manual fields with captured data
            populateManualFromLights(_capturedLights);
        } else {
            showLightsStatus('error', `❌ ${data.message || 'WLED offline'}`);
        }
    } catch(e) {
        showLightsStatus('error', '❌ Eroare la capturare');
    }

    btn.classList.remove('loading');
    btn.innerHTML = '<span>📸</span><span>Capturează din WLED acum</span>';
}

function populateManualFromLights(lights) {
    if (!lights) return;
    const populate = (prefix, zoneData) => {
        if (!zoneData) return;
        const onEl  = document.getElementById(prefix + 'On');
        const briEl = document.getElementById(prefix + 'Bri');
        const briVal= document.getElementById(prefix + 'BriVal');
        if (onEl)  onEl.checked = zoneData.on !== false;
        if (briEl) { briEl.value = zoneData.bri ?? 128; if (briVal) briVal.textContent = briEl.value; }

        const seg = (zoneData.seg || [])[0] || {};
        const fxEl  = document.getElementById(prefix + 'Fx');
        const palEl = document.getElementById(prefix + 'Pal');
        const sxEl  = document.getElementById(prefix + 'Sx');
        const sxVal = document.getElementById(prefix + 'SxVal');
        const ixEl  = document.getElementById(prefix + 'Ix');
        const ixVal = document.getElementById(prefix + 'IxVal');
        if (fxEl)  fxEl.value  = seg.fx  ?? 0;
        if (palEl) palEl.value = seg.pal ?? 0;
        if (sxEl)  { sxEl.value = seg.sx ?? 128; if (sxVal) sxVal.textContent = sxEl.value; }
        if (ixEl)  { ixEl.value = seg.ix ?? 128; if (ixVal) ixVal.textContent = ixEl.value; }
    };
    populate('main',  lights.main);
    populate('floor', lights.floor);
}

function showLightsStatus(type, msg) {
    const el = document.getElementById('sceneLightsStatus');
    if (!el) return;
    el.className = `scene-lights-status ${type}`;
    el.textContent = msg;
    el.style.display = '';
}

function resetManualFields() {
    const defaults = {
        mainOn: true, mainBri: 128, mainBriVal: '128',
        mainFx: 0, mainPal: 0, mainSx: 128, mainSxVal: '128', mainIx: 128, mainIxVal: '128',
        floorOn: true, floorBri: 180, floorBriVal: '180',
        floorFx: 0, floorPal: 0, floorSx: 128, floorSxVal: '128', floorIx: 128, floorIxVal: '128',
    };
    for (const [id, val] of Object.entries(defaults)) {
        const el = document.getElementById(id);
        if (!el) continue;
        if (el.type === 'checkbox') el.checked = val;
        else el.value = typeof val === 'string' ? val : String(val);
        // Also update textContent for val spans
        if (typeof val === 'string') el.textContent = val;
    }
}

// Build lights payload from manual form
function buildLightsFromManual() {
    const zone = (prefix) => ({
        on:  document.getElementById(prefix + 'On')?.checked ?? true,
        bri: parseInt(document.getElementById(prefix + 'Bri')?.value ?? '128'),
        seg: [{
            fx:  parseInt(document.getElementById(prefix + 'Fx')?.value  ?? '0'),
            pal: parseInt(document.getElementById(prefix + 'Pal')?.value ?? '0'),
            sx:  parseInt(document.getElementById(prefix + 'Sx')?.value  ?? '128'),
            ix:  parseInt(document.getElementById(prefix + 'Ix')?.value  ?? '128'),
        }]
    });
    return { main: zone('main'), floor: zone('floor') };
}

// ── Save scene
async function saveScene() {
    const name = (document.getElementById('sceneNameInput')?.value || '').trim();
    if (!name) { showToast('⚠️ Introdu un nume pentru scenă', 'error'); return; }

    const emoji = document.getElementById('sceneEmojiBtn')?.textContent?.trim() || '🎬';
    const musicPrompt = (document.getElementById('sceneMusicPrompt')?.value || '').trim();
    const editId = document.getElementById('sceneEditId')?.value || '';

    // Determine lights config
    let lights = null;
    if (_capturedLights) {
        lights = _capturedLights;
    } else if (_manualOpen) {
        lights = buildLightsFromManual();
    }

    const overlay = document.getElementById('sceneLoadingOverlay');
    const loadTxt = document.getElementById('sceneLoadingText');
    if (overlay) overlay.style.display = '';
    if (loadTxt) loadTxt.textContent = 'Se salvează...';

    try {
        const payload = { name, emoji, music_prompt: musicPrompt, lights };
        if (editId) payload.id = editId;

        const res = await fetch('/api/scenes/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();

        if (data.status === 'success') {
            await loadScenes();
            closeSceneModal(null, true);
            showToast(`✅ Scena "${name}" salvată!`, 'success');
        } else {
            showToast('❌ Eroare la salvare', 'error');
        }
    } catch(e) {
        showToast('❌ Eroare la salvare', 'error');
    }

    if (overlay) overlay.style.display = 'none';
}

// ── Activate scene
async function activateScene(sceneId) {
    const playBtn = document.getElementById(`scene-play-${sceneId}`);
    if (playBtn) {
        playBtn.classList.add('playing');
        playBtn.textContent = '⏳ Se activează...';
        playBtn.disabled = true;
    }

    try {
        const res = await fetch('/api/scenes/activate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: sceneId })
        });
        const data = await res.json();

        if (data.status === 'success') {
            const r = data.results || {};
            const parts = [];
            if (r.lights === 'ok') parts.push('💡 LED OK');
            else if (r.lights) parts.push(`💡 ${r.lights}`);
            if (r.music  === 'ok' || r.music === 'success') parts.push('🎵 Muzică OK');
            else if (r.music) parts.push(`🎵 ${r.music}`);
            showToast(`▶ ${escHtml(data.scene_name)} — ${parts.join(' · ')}`, 'success');
        } else {
            showToast('❌ Eroare la activare', 'error');
        }
    } catch(e) {
        showToast('❌ Eroare de conexiune', 'error');
    }

    if (playBtn) {
        setTimeout(() => {
            playBtn.classList.remove('playing');
            playBtn.textContent = '▶ Activează';
            playBtn.disabled = false;
        }, 2500);
    }
}

// ── Delete scene
async function deleteScene(sceneId) {
    const scene = _scenes.find(s => s.id === sceneId);
    if (!confirm(`Ștergi scena "${scene?.name || ''}"?`)) return;

    try {
        await fetch('/api/scenes/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: sceneId })
        });
        await loadScenes();
        showToast('🗑 Scenă ștearsă', 'success');
    } catch(e) {
        showToast('❌ Eroare la ștergere', 'error');
    }
}

// ── Toast
function showToast(msg, type = 'success') {
    const toast = document.getElementById('sceneToast');
    if (!toast) return;
    toast.textContent = msg;
    toast.className = `scene-toast ${type}`;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 3500);
}

function escHtml(s) {
    return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
