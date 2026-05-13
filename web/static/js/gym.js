// ===== GYM JS (restructured) =====

let allMeasurements = [];
let allDailyChecks = [];
let allWeightLog = [];
let allPhotos = [];
let gymProfile = {};
let currentPhotoCategory = 'progress';
let chartRangeDays = 30;
let weightChart = null;
let bodyChart = null;
let caloriesChart = null;
let compareMode = false;
let compareA = null;
let compareB = null;
let pendingUploadFiles = [];
let currentUploadCat = 'progress';

// ===== INIT =====
async function init() {
    await Promise.all([
        loadPhase(),
        loadProfile(),
        loadWeightLog(),
        loadMeasurements(),
        loadDailyChecks()
    ]);
    await loadPhotos();

    document.getElementById('measureDate').value = offsetDate(0);
    document.getElementById('checkDate').value = offsetDate(0);
    document.getElementById('weightDate').value = offsetDate(0);

    // Highlight check when date changes
    const checkDateEl = document.getElementById('checkDate');
    if (checkDateEl) {
        checkDateEl.addEventListener('change', () => {
            const date = checkDateEl.value;
            const existing = allDailyChecks.find(c => c.date === date);
            if (existing) highlightCheck(existing.level);
            else {
                document.querySelectorAll('.check-btn').forEach(b => b.classList.remove('selected'));
                document.getElementById('checkStatus').textContent = '';
            }
        });
    }
}

// ===== DATE QUICK BUTTONS =====
function setQuickDate(days, btn) {
    const row = document.querySelector('.gym-card .quick-date-row');
    if (row) row.querySelectorAll('.date-quick-btn').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    document.getElementById('measureDate').value = offsetDate(days);
}

// ===== PROFILE (Height) =====
async function loadProfile() {
    gymProfile = await fetch('/api/gym/profile').then(r => r.json());
    renderProfile();
}

function renderProfile() {
    const h = gymProfile.height;
    const badge = document.getElementById('heightBadge');
    const section = document.getElementById('heightInputSection');
    if (h) {
        badge.style.display = 'flex';
        document.getElementById('heightVal').textContent = h + ' cm';
        section.style.display = 'none';
    } else {
        badge.style.display = 'none';
        section.style.display = 'block';
    }
    const gw = gymProfile.goal_weight;
    const gwDisp = document.getElementById('goalWeightDisplay');
    if (gw) {
        document.getElementById('goalWeightInput').value = gw;
        // Calculate diff from last weight
        const lastW = allWeightLog.length ? allWeightLog[allWeightLog.length - 1].weight : null;
        if (lastW) {
            const diff = (lastW - gw).toFixed(1);
            gwDisp.textContent = diff > 0 ? `Ești la ${diff} kg de țintă 📉` : diff < 0 ? `Sub țintă cu ${Math.abs(diff)} kg 🎉` : '✅ La țintă!';
        }
    }
}

function editHeight() {
    document.getElementById('heightBadge').style.display = 'none';
    document.getElementById('heightInputSection').style.display = 'block';
    document.getElementById('heightInput').value = gymProfile.height || '';
}

async function saveHeight() {
    const val = document.getElementById('heightInput').value;
    if (!val) return;
    await fetch('/api/gym/profile', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ height: parseFloat(val) })
    });
    flash(`✅ Înălțime ${val} cm salvată!`);
    await loadProfile();
}

async function saveGoalWeight() {
    const val = document.getElementById('goalWeightInput').value;
    if (!val) return;
    await fetch('/api/gym/profile', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ goal_weight: parseFloat(val) })
    });
    flash(`✅ Greutate țintă: ${val} kg`);
    await loadProfile();
}

// ===== DAILY WEIGHT LOG =====
async function loadWeightLog() {
    allWeightLog = await fetch('/api/gym/weight').then(r => r.json());
    renderWeightRecentLog();
    renderWeightChart();
    renderStatsSummary();
}

async function saveWeightQuick() {
    const date = document.getElementById('weightDate').value || offsetDate(0);
    const weight = document.getElementById('weightQuick').value;
    const note = document.getElementById('weightNote').value;
    if (!weight) { flash('❌ Introdu greutatea', 'error'); return; }

    const res = await fetch('/api/gym/weight', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ date, weight: parseFloat(weight), note })
    }).then(r => r.json());

    if (res.status === 'success') {
        flash(`✅ ${weight} kg salvat pentru ${date}!`);
        document.getElementById('weightQuick').value = '';
        document.getElementById('weightNote').value = '';
        await loadWeightLog();
    } else flash('❌ ' + res.message, 'error');
}

function renderWeightRecentLog() {
    const el = document.getElementById('weightRecentLog');
    const recent = [...allWeightLog].reverse().slice(0, 7);
    if (!recent.length) {
        el.innerHTML = '<div style="color:var(--text-faint);font-size:12px;padding:8px 0">Niciun log de greutate încă.</div>';
        return;
    }
    el.innerHTML = `<table class="meas-table">
        <thead><tr><th>Dată</th><th>Kg</th><th>Δ</th><th></th></tr></thead>
        <tbody>${recent.map((w, i) => {
            const prev = recent[i + 1];
            const diff = prev ? (w.weight - prev.weight).toFixed(1) : null;
            const diffHtml = diff !== null
                ? `<span style="color:${diff > 0 ? 'var(--red)' : diff < 0 ? 'var(--green)' : 'var(--text-faint)'}">${diff > 0 ? '+' : ''}${diff}</span>`
                : '—';
            return `<tr>
                <td>${fmtDateShort(w.date)}</td>
                <td class="val">${w.weight} kg</td>
                <td>${diffHtml}</td>
                <td><button onclick="deleteWeight('${w.date}')" style="background:none;border:none;color:var(--text-faint);cursor:pointer;font-size:12px;padding:0">✕</button></td>
            </tr>`;
        }).join('')}</tbody>
    </table>`;
}

async function deleteWeight(date) {
    await fetch('/api/gym/weight/delete', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ date })
    });
    await loadWeightLog();
}

// ===== MEASUREMENTS (periodic) =====
async function loadMeasurements() {
    allMeasurements = await fetch('/api/gym/measurements').then(r => r.json());
    updateMeasurementReminder();
    renderBodyChart();
    renderRecentMeasurements();
}

function updateMeasurementReminder() {
    const banner = document.getElementById('measDueBanner');
    const label = document.getElementById('lastMeasLabel');
    if (!allMeasurements.length) {
        banner.style.display = 'flex';
        document.getElementById('measDueTxt').textContent = 'Nicio măsurătoare înregistrată! Ia-le acum.';
        label.textContent = 'Prima măsurătoare';
        return;
    }
    const last = allMeasurements[allMeasurements.length - 1];
    const daysSince = Math.floor((new Date() - new Date(last.date)) / 86400000);
    label.textContent = `Ultima: ${fmtDateShort(last.date)} (${daysSince}z ago)`;
    if (daysSince >= 28) {
        banner.style.display = 'flex';
        document.getElementById('measDueTxt').textContent = `${daysSince} de zile de la ultima măsurătoare. E momentul!`;
    } else {
        banner.style.display = 'none';
    }
}

async function saveMeasurement() {
    const date = document.getElementById('measureDate').value;
    if (!date) { flash('❌ Selectează data', 'error'); return; }

    const payload = {
        date,
        brat_relaxat: parseFld('mBratRelaxat'),
        brat_incordat: parseFld('mBratIncordat'),
        antebrat_incordat: parseFld('mAntebrat'),
        piept: parseFld('mPiept'),
        talie: parseFld('mTalie'),
        sold: parseFld('mSold'),
        coapsa: parseFld('mCoapsa'),
        umar: parseFld('mUmar'),
        gat: parseFld('mGat'),
        gamba: parseFld('mGamba'),
        notes: document.getElementById('mNotes').value
    };

    const anyVal = Object.entries(payload).filter(([k]) => k !== 'date' && k !== 'notes').some(([, v]) => v !== null);
    if (!anyVal) { flash('❌ Introdu cel puțin o măsurătoare', 'error'); return; }

    const res = await fetch('/api/gym/measurements', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify(payload)
    }).then(r => r.json());

    if (res.status === 'success') {
        flash('✅ Măsurători salvate!');
        ['mBratRelaxat','mBratIncordat','mAntebrat','mPiept','mTalie','mSold','mCoapsa','mUmar','mGat','mGamba'].forEach(id => {
            document.getElementById(id).value = '';
        });
        document.getElementById('mNotes').value = '';
        await loadMeasurements();
    } else flash('❌ ' + res.message, 'error');
}

function parseFld(id) {
    const v = document.getElementById(id).value;
    return v ? parseFloat(v) : null;
}

function renderRecentMeasurements() {
    const el = document.getElementById('recentMeasTable');
    const recent = [...allMeasurements].reverse().slice(0, 4);
    if (!recent.length) {
        el.innerHTML = '<div class="empty-state" style="padding:14px 0">Nicio măsurătoare</div>';
        return;
    }
    el.innerHTML = `<table class="meas-table">
        <thead><tr><th>Dată</th><th>Piept</th><th>Talie</th><th>Braț↗</th></tr></thead>
        <tbody>${recent.map(e => `<tr>
            <td>${fmtDateShort(e.date)}</td>
            <td class="val">${e.piept ? e.piept+' cm' : '—'}</td>
            <td class="val">${e.talie ? e.talie+' cm' : '—'}</td>
            <td class="val">${e.brat_incordat ? e.brat_incordat+' cm' : '—'}</td>
        </tr>`).join('')}</tbody>
    </table>`;
}

function renderStatsSummary() {
    const card = document.getElementById('statsSummaryCard');
    const el = document.getElementById('statsSummary');
    const weights = allWeightLog.map(w => w.weight);
    if (weights.length < 2) { card.style.display = 'none'; return; }
    card.style.display = 'block';
    const first = weights[0], last = weights[weights.length-1];
    const diff = (last - first).toFixed(1);
    const col = diff > 0 ? 'var(--red)' : 'var(--green)';
    el.innerHTML = `<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
        <div style="text-align:center;padding:12px;background:var(--surface2);border-radius:var(--radius-xs)">
            <div style="font-size:22px;font-weight:900;color:var(--teal)">${last}</div>
            <div style="font-size:10px;color:var(--text-faint);text-transform:uppercase;letter-spacing:0.5px">kg actuali</div>
        </div>
        <div style="text-align:center;padding:12px;background:var(--surface2);border-radius:var(--radius-xs)">
            <div style="font-size:22px;font-weight:900;color:${col}">${diff>0?'+':''}${diff}</div>
            <div style="font-size:10px;color:var(--text-faint);text-transform:uppercase;letter-spacing:0.5px">schimbare total</div>
        </div>
    </div>`;
}

// ===== PHASE =====
async function loadPhase() {
    const data = await fetch('/api/gym/phase').then(r => r.json());
    renderPhase(data);
}

function renderPhase(data) {
    const phase = data.current || 'sustinere';
    const ICONS = { bulk: '📈', sustinere: '⚖️', cut: '📉' };
    const NAMES = { bulk: 'Bulk', sustinere: 'Susținere', cut: 'Cut' };
    document.getElementById('phaseBanner').className = `phase-banner ${phase}`;
    document.getElementById('phaseIcon').textContent = ICONS[phase];
    document.getElementById('phaseName').textContent = NAMES[phase];
}

function openPhaseModal() { document.getElementById('phaseModal').classList.add('open'); }

async function selectPhase(phase, el) {
    document.querySelectorAll('.phase-option').forEach(e => e.classList.remove('selected'));
    if (el) el.classList.add('selected');
    await fetch('/api/gym/phase', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ phase })
    });
    closeModal('phaseModal');
    await loadPhase();
    flash(`✅ Faza: ${phase}`);
}

// ===== CHARTS =====
const CHART_OPT = {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { display: false }, tooltip: {
        backgroundColor: '#12121f', borderColor: '#1e1e30', borderWidth: 1,
        titleColor: '#e2e2f0', bodyColor: '#6a6a90'
    }},
    scales: {
        x: { grid: { color: '#1e1e30' }, ticks: { color: '#3a3a55', maxTicksLimit: 7, font: { size: 10 } } },
        y: { grid: { color: '#1e1e30' }, ticks: { color: '#3a3a55', font: { size: 10 } } }
    }
};

function setChartRange(days, btn) {
    document.querySelectorAll('.chart-range-btn').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    chartRangeDays = days;
    renderWeightChart();
}

function filterRange(data) {
    if (!chartRangeDays) return data;
    const cutoff = offsetDate(-chartRangeDays);
    return data.filter(e => e.date >= cutoff);
}

function renderWeightChart() {
    const filtered = filterRange(allWeightLog);
    if (weightChart) weightChart.destroy();
    const canvas = document.getElementById('weightChart');
    if (!canvas) return;
    
    let empty = document.getElementById('weightEmpty');
    if (!empty) {
        empty = document.createElement('div');
        empty.id = 'weightEmpty';
        empty.className = 'empty-state';
        empty.style.padding = '50px 0';
        empty.innerHTML = 'Loghează zilnic greutatea pentru grafic 💪';
        canvas.parentElement.appendChild(empty);
    }
    
    if (!filtered.length) {
        empty.style.display = 'block';
        canvas.style.display = 'none';
        return;
    }
    
    empty.style.display = 'none';
    canvas.style.display = 'block';
    
    const ctx = canvas.getContext('2d');
    const grad = ctx.createLinearGradient(0,0,0,200);
    grad.addColorStop(0,'rgba(0,212,170,0.25)'); grad.addColorStop(1,'rgba(0,212,170,0.01)');
    weightChart = new Chart(ctx, {
        type: 'line',
        data: { labels: filtered.map(e => fmtDateShort(e.date)),
            datasets: [{ data: filtered.map(e => e.weight), borderColor: '#00d4aa', backgroundColor: grad, borderWidth: 2.5, pointBackgroundColor: '#00d4aa', pointRadius: 3.5, fill: true, tension: 0.4 }] },
        options: { ...CHART_OPT, scales: { ...CHART_OPT.scales, y: { ...CHART_OPT.scales.y, ticks: { ...CHART_OPT.scales.y.ticks, callback: v => v+' kg' } } } }
    });
}

function renderBodyChart() {
    const sel = document.getElementById('bodyMeasSelect');
    if (!sel) return;
    const field = sel.value;
    const filtered = filterRange(allMeasurements).filter(e => e[field] != null);
    if (bodyChart) bodyChart.destroy();
    const canvas = document.getElementById('bodyChart');
    if (!canvas) return;
    
    let empty = document.getElementById('bodyEmpty');
    if (!empty) {
        empty = document.createElement('div');
        empty.id = 'bodyEmpty';
        empty.className = 'empty-state';
        empty.style.padding = '50px 0';
        empty.innerHTML = 'Adaugă măsurători periodice pentru grafic';
        canvas.parentElement.appendChild(empty);
    }
    
    if (!filtered.length) {
        empty.style.display = 'block';
        canvas.style.display = 'none';
        return;
    }
    
    empty.style.display = 'none';
    canvas.style.display = 'block';

    const ctx = canvas.getContext('2d');
    const grad = ctx.createLinearGradient(0,0,0,200);
    grad.addColorStop(0,'rgba(124,106,255,0.25)'); grad.addColorStop(1,'rgba(124,106,255,0.01)');
    bodyChart = new Chart(ctx, {
        type: 'line',
        data: { labels: filtered.map(e => fmtDateShort(e.date)),
            datasets: [{ data: filtered.map(e => e[field]), borderColor: '#7c6aff', backgroundColor: grad, borderWidth: 2.5, pointBackgroundColor: '#7c6aff', pointRadius: 4, fill: true, tension: 0.4 }] },
        options: { ...CHART_OPT, scales: { ...CHART_OPT.scales, y: { ...CHART_OPT.scales.y, ticks: { ...CHART_OPT.scales.y.ticks, callback: v => v+' cm' } } } }
    });
}

function renderCaloriesChart() {
    const cutoff = offsetDate(-28);
    const filtered = allDailyChecks.filter(c => c.date >= cutoff);
    if (caloriesChart) caloriesChart.destroy();
    const canvas = document.getElementById('caloriesChart');
    if (!canvas) return;
    
    let empty = document.getElementById('caloriesEmpty');
    if (!empty) {
        empty = document.createElement('div');
        empty.id = 'caloriesEmpty';
        empty.className = 'empty-state';
        empty.style.padding = '30px 0';
        empty.innerHTML = 'Bifează check-urile zilnice';
        canvas.parentElement.appendChild(empty);
    }
    
    if (!filtered.length) {
        empty.style.display = 'block';
        canvas.style.display = 'none';
        return;
    }
    
    empty.style.display = 'none';
    canvas.style.display = 'block';
    
    const COLORS = { surplus_mare: '#2ed573', mentinere: '#7c6aff', deficit: '#ffd166', deficit_mare: '#ff4d6d' };
    const VALS = { surplus_mare: 4, mentinere: 3, deficit: 2, deficit_mare: 1 };
    const ctx = canvas.getContext('2d');
    caloriesChart = new Chart(ctx, {
        type: 'bar',
        data: { labels: filtered.map(e => fmtDateShort(e.date).split(' ').slice(0,2).join(' ')),
            datasets: [{ data: filtered.map(e => VALS[e.level]||0), backgroundColor: filtered.map(e => COLORS[e.level]||'#1e1e30'), borderRadius: 5, borderSkipped: false }] },
        options: { ...CHART_OPT, scales: { ...CHART_OPT.scales, y: { ...CHART_OPT.scales.y, min:0, max:4, ticks: { ...CHART_OPT.scales.y.ticks, stepSize: 1, callback: v => ['',' Def. Mare','Deficit','Menținere','Surplus'][v] } } } }
    });
}

// ===== DAILY CHECK =====
async function loadDailyChecks() {
    allDailyChecks = await fetch('/api/gym/daily-checks').then(r => r.json());
    const selectedDate = document.getElementById('checkDate')?.value || offsetDate(0);
    const selectedCheck = allDailyChecks.find(c => c.date === selectedDate);
    if (selectedCheck) highlightCheck(selectedCheck.level);
    else {
        document.querySelectorAll('.check-btn').forEach(b => b.classList.remove('selected'));
        const statusEl = document.getElementById('checkStatus');
        if (statusEl) statusEl.textContent = '';
    }
    renderCaloriesChart();
}

function highlightCheck(level) {
    document.querySelectorAll('.check-btn').forEach(b => b.classList.remove('selected'));
    const btn = document.querySelector(`.check-btn[data-level="${level}"]`);
    if (btn) btn.classList.add('selected');
    const LABELS = { surplus_mare: '💪 Surplus Mare', mentinere: '✅ Menținere', deficit: '⬇️ Deficit', deficit_mare: '❌ Deficit Mare' };
    document.getElementById('checkStatus').textContent = LABELS[level] ? `Selectat: ${LABELS[level]}` : '';
}

async function setDailyCheck(level, btn) {
    const date = document.getElementById('checkDate').value || offsetDate(0);
    const res = await fetch('/api/gym/daily-check', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ date, level })
    }).then(r => r.json());
    if (res.status === 'success') {
        highlightCheck(level);
        await loadDailyChecks();
        flash('✅ Check salvat!');
    } else flash('❌ '+res.message, 'error');
}

// ===== PHOTOS (same as before) =====
async function loadPhotos() {
    const year = document.getElementById('photoFilterYear')?.value || '';
    let photos = await fetch(`/api/gym/photos?category=${currentPhotoCategory}`).then(r => r.json());
    if (year) photos = photos.filter(p => p.date?.startsWith(year));
    allPhotos = photos;
    const years = [...new Set(photos.map(p => p.date?.split('-')[0]).filter(Boolean))].sort().reverse();
    const sel = document.getElementById('photoFilterYear');
    const cur = sel?.value;
    if (sel) sel.innerHTML = '<option value="">Toți anii</option>' + years.map(y => `<option value="${y}" ${y===cur?'selected':''}>${y}</option>`).join('');
    renderPhotos(photos);
}

function renderPhotos(photos) {
    const grid = document.getElementById('photoGrid');
    if (!photos.length) { grid.innerHTML = '<div class="empty-state">Nicio poză. Adaugă prima! 📸</div>'; return; }
    grid.innerHTML = photos.map((p, i) => `
        <div class="photo-card" id="photo_${i}" onclick="handlePhotoClick(${i})">
            <div class="compare-badge" id="badge_${i}">A</div>
            <img src="${p.url}" loading="lazy" alt="${p.date||''}">
            <div class="photo-overlay">
                <div class="photo-date">${fmtDateShort(p.date)||p.filename}</div>
                <div class="photo-actions">
                    <button class="photo-action-btn" onclick="event.stopPropagation();openLightbox('${p.url}','${fmtDateShort(p.date)}')">🔍</button>
                    <button class="photo-action-btn" onclick="event.stopPropagation();deletePhoto('${p.filename}','${currentPhotoCategory}',${i})">🗑️</button>
                </div>
            </div>
        </div>
    `).join('');
}

function handlePhotoClick(i) {
    if (!compareMode) { openLightbox(allPhotos[i].url, fmtDateShort(allPhotos[i].date)); return; }
    const card = document.getElementById(`photo_${i}`);
    const badge = document.getElementById(`badge_${i}`);
    if (compareA === i) { card.classList.remove('compare-a'); compareA = null; updateComparePanel(); return; }
    if (compareB === i) { card.classList.remove('compare-b'); compareB = null; updateComparePanel(); return; }
    if (compareA === null) { compareA = i; card.classList.add('compare-a'); badge.textContent = 'A'; }
    else { compareB = i; card.classList.add('compare-b'); badge.textContent = 'B'; }
    updateComparePanel();
}

function updateComparePanel() {
    const slot1 = document.getElementById('compareSlot1');
    const slot2 = document.getElementById('compareSlot2');
    if (compareA !== null) {
        const p = allPhotos[compareA];
        slot1.innerHTML = `<img src="${p.url}" alt="A"><div class="compare-slot-lbl">A — ${fmtDateShort(p.date)}</div>`;
    } else slot1.innerHTML = '<div class="compare-placeholder">Selectează A</div>';
    if (compareB !== null) {
        const p = allPhotos[compareB];
        slot2.innerHTML = `<img src="${p.url}" alt="B"><div class="compare-slot-lbl">B — ${fmtDateShort(p.date)}</div>`;
    } else slot2.innerHTML = '<div class="compare-placeholder">Selectează B</div>';
    if (compareA !== null && compareB !== null) {
        document.getElementById('compareDates').textContent = `${fmtDateShort(allPhotos[compareA].date)} vs ${fmtDateShort(allPhotos[compareB].date)}`;
    }
}

function toggleCompareMode() {
    compareMode = !compareMode;
    const btn = document.getElementById('compareToggleBtn');
    const panel = document.getElementById('comparePanel');
    if (compareMode) { btn.className = 'btn btn-teal btn-sm'; panel.classList.add('visible'); flash('📸 Click pe 2 poze pentru a compara'); }
    else clearCompare();
}

function clearCompare() {
    compareMode = false; compareA = null; compareB = null;
    document.querySelectorAll('.photo-card').forEach(c => c.classList.remove('compare-a','compare-b'));
    document.getElementById('comparePanel').classList.remove('visible');
    document.getElementById('compareToggleBtn').className = 'btn btn-secondary btn-sm';
}

async function deletePhoto(filename, category, i) {
    if (!confirm('Ștergi această poză?')) return;
    await fetch('/api/gym/photos/delete', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ filename, category })
    });
    flash('🗑️ Ștearsă'); await loadPhotos();
}

function switchPhotoTab(cat, btn) {
    currentPhotoCategory = cat;
    document.querySelectorAll('.photo-tab').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    document.getElementById('progressPhotoGuide').style.display = cat === 'progress' ? 'flex' : 'none';
    loadPhotos();
}

function openUploadModal() {
    document.getElementById('uploadModal').classList.add('open');
    pendingUploadFiles = [];
    document.getElementById('uploadPreviewGrid').innerHTML = '';
    document.getElementById('uploadProgress').style.display = 'none';
    selectUploadCat(currentPhotoCategory);
    document.getElementById('uploadDate').value = offsetDate(0);
}

function selectUploadCat(cat, btn) {
    currentUploadCat = cat;
    document.querySelectorAll('.upload-cat-btn').forEach(b => b.classList.remove('active'));
    const b = btn || document.querySelector(`.upload-cat-btn[data-cat="${cat}"]`);
    if (b) b.classList.add('active');
}

function setUploadQuickDate(days, btn) {
    const row = document.querySelector('#uploadModal .quick-date-row');
    if (row) row.querySelectorAll('.date-quick-btn').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    document.getElementById('uploadDate').value = offsetDate(days);
}

function handleFileSelect(e) {
    Array.from(e.target.files).forEach(f => addUploadFile(f));
    e.target.value = '';
}

function addUploadFile(file) {
    const idx = pendingUploadFiles.length;
    pendingUploadFiles.push(file);
    const reader = new FileReader();
    reader.onload = e => {
        const grid = document.getElementById('uploadPreviewGrid');
        const div = document.createElement('div');
        div.className = 'upload-preview-item'; div.id = `pv_${idx}`;
        div.innerHTML = `<img src="${e.target.result}" alt=""><button class="upload-preview-remove" onclick="removeUploadFile(${idx})">✕</button>`;
        grid.appendChild(div);
    };
    reader.readAsDataURL(file);
}

function removeUploadFile(idx) {
    pendingUploadFiles[idx] = null;
    const el = document.getElementById(`pv_${idx}`);
    if (el) el.remove();
}

async function doUpload() {
    const files = pendingUploadFiles.filter(Boolean);
    if (!files.length) { flash('❌ Selectează cel puțin o poză', 'error'); return; }
    const date = document.getElementById('uploadDate').value || offsetDate(0);
    document.getElementById('uploadProgress').style.display = 'block';
    const fill = document.getElementById('uploadProgressFill');
    const txt = document.getElementById('uploadProgressText');
    let done = 0;
    for (const file of files) {
        const fd = new FormData();
        fd.append('photo', file); fd.append('date', date); fd.append('category', currentUploadCat);
        await fetch('/api/gym/photos/upload', { method: 'POST', body: fd });
        done++;
        const pct = Math.round((done/files.length)*100);
        fill.style.width = pct + '%';
        txt.textContent = `${done}/${files.length} (${pct}%)`;
    }
    closeModal('uploadModal');
    flash(`✅ ${done} poze încărcate!`);
    pendingUploadFiles = [];
    await loadPhotos();
}

const dz = document.getElementById('uploadDropZone');
if (dz) {
    dz.addEventListener('dragover', e => { e.preventDefault(); dz.style.borderColor = '#7c6aff'; });
    dz.addEventListener('dragleave', () => { dz.style.borderColor = ''; });
    dz.addEventListener('drop', e => {
        e.preventDefault(); dz.style.borderColor = '';
        Array.from(e.dataTransfer.files).filter(f => f.type.startsWith('image/')).forEach(addUploadFile);
    });
}

init();
