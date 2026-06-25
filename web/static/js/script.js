// ===== JOURNAL JS =====

const SCORE_META = {
    energie:         { label: "Energie",    inverted: false },
    stres:           { label: "Stres",      inverted: true  },
    dopamina:        { label: "Dopamină",   inverted: false },
    disciplina:      { label: "Disciplină", inverted: false },
    social:          { label: "Social",     inverted: false },
    somn:            { label: "Somn",       inverted: false },
    claritate:       { label: "Claritate",  inverted: false },
    progres_scopuri: { label: "Progres",    inverted: false },
    dispozitie:      { label: "Dispoziție", inverted: false },
    corp:            { label: "Corp",       inverted: false }
};

function scoreClass(val, inverted) {
    if (val === null || val === undefined) return '';
    const n = Number(val);
    if (inverted) {
        return n >= 8 ? 'score-inv-hi' : n >= 5 ? 'score-inv-mid' : 'score-inv-lo';
    }
    return n >= 7 ? 'score-hi' : n >= 4 ? 'score-mid' : 'score-lo';
}

// ===== STATE =====
let allData = [];          // data for current loaded month
let availableMonths = [];  // ['2026-06', '2026-05', ...]
let currentMonth = '';     // currently displayed month
let lastSavedDate = null;
let currentScoreEditDate = null;
let journalPhotoPending = [null, null, null];
let pendingEntryPhotoFiles = [];
let currentEntryPhotoDate = null;
let currentColumn = 0;
let currentRenderedData = [];

// ===== MONTH NAMES =====
const MONTH_NAMES_RO = [
    '', 'Ianuarie', 'Februarie', 'Martie', 'Aprilie', 'Mai', 'Iunie',
    'Iulie', 'August', 'Septembrie', 'Octombrie', 'Noiembrie', 'Decembrie'
];
function fmtMonth(ym) {
    const [y, m] = ym.split('-');
    return `${MONTH_NAMES_RO[parseInt(m)]} ${y}`;
}

// ===== DATE PICKER =====
function setJournalQuickDate(offsetDays, btn) {
    document.querySelectorAll('.journal-date-section .date-quick-btn').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    const dateStr = offsetDate(offsetDays);
    document.getElementById('entryDate').value = offsetDays === 0 ? '' : dateStr;
    const display = document.getElementById('entryDateDisplay');
    const labels = { 0: '📅 Azi', '-1': '📅 Ieri', '-2': '📅 Alaltăieri', '-3': '📅 Acum 3 zile' };
    display.textContent = labels[offsetDays] || '📅 ' + fmtDateShort(dateStr);
}

function onManualDateChange() {
    const val = document.getElementById('entryDate').value;
    document.querySelectorAll('.journal-date-section .date-quick-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('entryDateDisplay').textContent = val ? '📅 ' + fmtDate(val) : '📅 Azi';
}

// ===== PHOTO SLOTS (INPUT FORM) =====
function triggerJournalPhotoInput(slot) {
    document.getElementById(`jPhotoInput${slot}`).click();
}

function handleJournalPhotoSelect(event, slot) {
    const file = event.target.files[0];
    if (!file || !file.type.startsWith('image/')) return;
    journalPhotoPending[slot] = file;
    const reader = new FileReader();
    reader.onload = e => {
        const slotEl = document.getElementById(`jSlot${slot}`);
        slotEl.innerHTML = `
            <img src="${e.target.result}" alt="">
            <button class="jslot-remove" onclick="event.stopPropagation();removeJournalPhoto(${slot})">✕</button>
        `;
    };
    reader.readAsDataURL(file);
    const count = journalPhotoPending.filter(Boolean).length;
    document.getElementById('journalPhotoStatus').textContent = count ? `${count} poză(e) selectată(e)` : '';
}

function removeJournalPhoto(slot) {
    journalPhotoPending[slot] = null;
    const el = document.getElementById(`jSlot${slot}`);
    el.innerHTML = `<div class="jslot-icon">+</div><div class="jslot-label">Poză ${slot+1}</div>`;
    const count = journalPhotoPending.filter(Boolean).length;
    document.getElementById('journalPhotoStatus').textContent = count ? `${count} poză(e)` : '';
}

async function uploadJournalPhotos(date) {
    const files = journalPhotoPending.filter(Boolean);
    for (const file of files) {
        const fd = new FormData();
        fd.append('photo', file); fd.append('date', date);
        await fetch('/api/journal/photos/upload', { method: 'POST', body: fd });
    }
    journalPhotoPending = [null, null, null];
    [0,1,2].forEach(i => {
        document.getElementById(`jSlot${i}`).innerHTML = `<div class="jslot-icon">+</div><div class="jslot-label">Poză ${i+1}</div>`;
    });
    document.getElementById('journalPhotoStatus').textContent = '';
}

// ===== ENTRY PHOTO (on log cards) =====
function openEntryPhotoModal(date) {
    currentEntryPhotoDate = date;
    pendingEntryPhotoFiles = [];
    document.getElementById('entryPhotoPreviewGrid').innerHTML = '';
    document.getElementById('entryUploadProgress').style.display = 'none';
    document.getElementById('entryPhotoDate').textContent = '📅 ' + fmtDate(date);
    document.getElementById('entryPhotoModal').classList.add('open');
}

function handleEntryPhotoSelect(event) {
    Array.from(event.target.files).forEach(f => {
        if (!f.type.startsWith('image/')) return;
        const idx = pendingEntryPhotoFiles.length;
        pendingEntryPhotoFiles.push(f);
        const reader = new FileReader();
        reader.onload = e => {
            const grid = document.getElementById('entryPhotoPreviewGrid');
            const div = document.createElement('div');
            div.className = 'upload-preview-item';
            div.id = `ep_${idx}`;
            div.innerHTML = `<img src="${e.target.result}" alt=""><button class="upload-preview-remove" onclick="removeEntryPhoto(${idx})">✕</button>`;
            grid.appendChild(div);
        };
        reader.readAsDataURL(f);
    });
    event.target.value = '';
}

function removeEntryPhoto(idx) {
    pendingEntryPhotoFiles[idx] = null;
    const el = document.getElementById(`ep_${idx}`);
    if (el) el.remove();
}

async function doEntryPhotoUpload() {
    const files = pendingEntryPhotoFiles.filter(Boolean);
    if (!files.length) { flash('❌ Selectează cel puțin o poză', 'error'); return; }
    const date = currentEntryPhotoDate;
    document.getElementById('entryUploadProgress').style.display = 'block';
    const fill = document.getElementById('entryUploadFill');
    let done = 0;
    for (const f of files) {
        const fd = new FormData();
        fd.append('photo', f); fd.append('date', date);
        await fetch('/api/journal/photos/upload', { method: 'POST', body: fd });
        done++;
        fill.style.width = Math.round((done/files.length)*100) + '%';
    }
    closeModal('entryPhotoModal');
    flash(`✅ ${done} poze adăugate la ${date}!`);
    pendingEntryPhotoFiles = [];
    loadLogsForMonth(currentMonth);
}

// ===== MONTH PICKER =====
function renderMonthPicker(months) {
    let container = document.getElementById('monthPickerContainer');
    if (!container) return;
    if (!months || months.length === 0) {
        container.innerHTML = '';
        return;
    }

    const tabs = months.map(m => {
        const isActive = m === currentMonth;
        return `<button class="month-tab ${isActive ? 'active' : ''}" onclick="switchMonth('${m}')">${fmtMonth(m)}</button>`;
    }).join('');

    container.innerHTML = `<div class="month-tabs-row">${tabs}</div>`;
}

function switchMonth(month) {
    if (month === currentMonth) return;
    currentMonth = month;
    currentColumn = 0;
    // Update search box
    const si = document.getElementById('searchInput');
    if (si) si.value = '';
    loadLogsForMonth(month);
    renderMonthPicker(availableMonths);
}

// ===== LOAD =====
async function loadAll() {
    // First load the available months list
    try {
        const mRes = await fetch('/api/logs/months');
        const mData = await mRes.json();
        availableMonths = mData.months || [];
    } catch(e) {
        availableMonths = [];
    }

    // Default to current month
    const nowMonth = offsetDate(0).slice(0, 7); // YYYY-MM
    if (!currentMonth) {
        currentMonth = availableMonths.includes(nowMonth) ? nowMonth : (availableMonths[0] || nowMonth);
    }

    renderMonthPicker(availableMonths);

    // Load logs, targets, gym data in parallel
    const [logs, targets, measurements, checks] = await Promise.all([
        fetch(`/api/logs?month=${currentMonth}`).then(r => r.json()),
        fetch('/api/targets').then(r => r.json()),
        fetch('/api/gym/measurements').then(r => r.json()).catch(() => []),
        fetch('/api/gym/daily-checks').then(r => r.json()).catch(() => [])
    ]);

    allData = logs;
    renderLogs(logs);
    renderSidebarTargets(targets.goals || []);
    renderSidebarGym(measurements, checks);
}

async function loadLogsForMonth(month) {
    currentColumn = 0;
    const container = document.getElementById('logsContainer');
    container.innerHTML = '<div style="color:var(--text-faint);font-size:13px;padding:24px 0;text-align:center">Se încarcă...</div>';

    try {
        const logs = await fetch(`/api/logs?month=${month}`).then(r => r.json());
        allData = logs;
        renderLogs(logs);
    } catch(e) {
        container.innerHTML = '<div style="color:var(--text-faint);font-size:13px;padding:24px 0">Eroare la încărcare.</div>';
    }
}

loadAll();

function renderLogs(daysArray) {
    currentRenderedData = daysArray;
    const container = document.getElementById('logsContainer');
    const noResults = document.getElementById('noResults');
    const pagContainer = document.getElementById('journalPagination');

    noResults.className = daysArray.length === 0 ? 'empty-state no-results visible' : 'empty-state no-results';

    if (daysArray.length === 0) {
        container.innerHTML = '';
        if (pagContainer) pagContainer.innerHTML = '';
        return;
    }

    const COLUMN_SIZE = 16;
    const totalColumns = Math.ceil(daysArray.length / COLUMN_SIZE);

    if (currentColumn >= totalColumns) {
        currentColumn = 0;
    }

    const startIndex = currentColumn * COLUMN_SIZE;
    const endIndex = Math.min(startIndex + COLUMN_SIZE, daysArray.length);
    const displayedDays = daysArray.slice(startIndex, endIndex);

    const fragment = document.createDocumentFragment();

    displayedDays.forEach(dayObj => {
        const group = document.createElement('div');
        group.className = 'day-group';

        group.innerHTML = `
            <div class="day-header">
                <span class="day-date-str">${fmtDate(dayObj.date)}</span>
                <div class="day-actions">
                    <button class="btn btn-secondary btn-xs" onclick="triggerRejudge('${dayObj.date}',this)">🔄 Re-judecă</button>
                    <button class="btn btn-ghost btn-xs" onclick="openEntryPhotoModal('${dayObj.date}')">📷</button>
                </div>
            </div>
        `;

        // Day photos
        const photos = dayObj.journal_photos || [];
        if (photos.length) {
            const row = document.createElement('div');
            row.className = 'journal-day-photos';
            row.innerHTML = photos.map(fn => `
                <div class="journal-day-photo" onclick="openLightbox('/media/journal/${fn}','${dayObj.date}')">
                    <img src="/media/journal/${fn}" loading="lazy" alt="">
                </div>
            `).join('');
            group.appendChild(row);
        }

        dayObj.logs.forEach(log => {
            const card = document.createElement('div');

            if (log.type === 'daily_summary') {
                card.className = 'summary-card';
                const a = log.analysis || {};
                const scores = a.scores || {};
                const tags = a.tags || [];

                let scoresHtml = '<div class="scores-grid">';
                for (const [key, meta] of Object.entries(SCORE_META)) {
                    const val = scores[key];
                    const cls = scoreClass(val, meta.inverted);
                    scoresHtml += `<div class="score-item"><div class="score-val ${cls}">${val ?? '?'}</div><div class="score-lbl">${meta.label}</div></div>`;
                }
                scoresHtml += '</div>';

                const tagsHtml = tags.map(t => `<span class="tag">${t}</span>`).join('');
                const psychFb = a.psychologist_feedback || '';
                const whatWell = a.what_went_well || '';
                const patternAlert = a.pattern_alert || '';
                const showAlert = patternAlert && !patternAlert.includes('Niciun');
                const editedTag = log.scores_manually_edited ? `<span class="summary-edited-tag">✏️ editat</span>` : '';

                card.innerHTML = `
                    <div class="summary-top">
                        <span class="summary-label">🧠 Analiză Psihologică</span>
                        <div style="display:flex;align-items:center;gap:8px">
                            ${editedTag}
                            <button class="btn btn-ghost btn-xs" onclick="openScoreEdit('${dayObj.date}',${JSON.stringify(scores).replace(/"/g,'&quot;')})">✏️ Scoruri</button>
                        </div>
                    </div>
                    <div class="summary-text">${a.short_summary || '—'}</div>
                    ${scoresHtml}
                    ${tagsHtml ? `<div class="tags-row">${tagsHtml}</div>` : ''}
                    ${psychFb ? `<div class="feedback-block"><div class="feedback-lbl">💭 Feedback</div><div class="feedback-txt">${psychFb}</div></div>` : ''}
                    ${whatWell ? `<div class="feedback-block good"><div class="feedback-lbl">✅ Ce a mers bine</div><div class="feedback-txt">${whatWell}</div></div>` : ''}
                    ${showAlert ? `<div class="feedback-block warn"><div class="feedback-lbl">⚠️ Pattern</div><div class="feedback-txt">${patternAlert}</div></div>` : ''}
                `;
            } else {
                card.className = 'entry-card';
                const src = log.source === 'web' ? '🌐 Web' : '🎤 Vocal';
                card.innerHTML = `
                    <div class="entry-header">
                        <span class="entry-time">${log.display_time || ''}</span>
                        <span class="entry-badge">${src}</span>
                    </div>
                    <div class="entry-text">"${log.raw_text || ''}"</div>
                    <button class="entry-photo-btn" onclick="openEntryPhotoModal('${dayObj.date}')">📷 Adaugă poze</button>
                `;
            }
            group.appendChild(card);
        });
        fragment.appendChild(group);
    });

    // Single DOM write
    container.innerHTML = '';
    container.appendChild(fragment);

    // Render pagination controls
    if (pagContainer) {
        if (totalColumns <= 1) {
            pagContainer.innerHTML = '';
            return;
        }

        let html = '';
        
        // Prev button
        html += `<button class="pag-btn" ${currentColumn === 0 ? 'disabled' : ''} onclick="changeColumn(${currentColumn - 1})">← Precedentă</button>`;
        
        // Column numbers
        for (let c = 0; c < totalColumns; c++) {
            const isActive = c === currentColumn;
            html += `<button class="pag-btn num ${isActive ? 'active' : ''}" onclick="changeColumn(${c})">${c + 1}</button>`;
        }

        // Next button
        html += `<button class="pag-btn" ${currentColumn === totalColumns - 1 ? 'disabled' : ''} onclick="changeColumn(${currentColumn + 1})">Următoarea →</button>`;

        pagContainer.innerHTML = `<div class="pag-wrapper">${html}</div>`;
    }
}

function changeColumn(index) {
    currentColumn = index;
    renderLogs(currentRenderedData);
    const container = document.getElementById('logsContainer');
    if (container) {
        window.scrollTo({
            top: container.getBoundingClientRect().top + window.pageYOffset - 100,
            behavior: 'smooth'
        });
    }
}

// ===== SIDEBAR =====
function renderSidebarTargets(goals) {
    const el = document.getElementById('sidebarTargets');
    if (!goals.length) { el.innerHTML = '<div class="empty-state" style="padding:14px 0">Niciun target activ</div>'; return; }
    el.innerHTML = goals.slice(0,5).map(g => {
        const prog = Number(g.progress || 0);
        return `<div class="target-mini">
            <div class="target-mini-title">${g.title}</div>
            <div class="target-mini-meta">${g.deadline ? '📅 '+g.deadline : ''} ${g.priority ? '· '+g.priority : ''}</div>
            <div class="prog-bar"><div class="prog-bar-fill ${prog>=100?'done':''}" style="width:${prog}%"></div></div>
        </div>`;
    }).join('');
}

function renderSidebarGym(measurements, checks) {
    const el = document.getElementById('sidebarGymSummary');
    if (!el) return;
    const today = offsetDate(0);
    const latest = [...measurements].reverse()[0];
    const todayCheck = checks.find(c => c.date === today);
    const LABELS = { surplus_mare: '💪 Surplus Mare', mentinere: '✅ Menținere', deficit: '⬇️ Deficit', deficit_mare: '❌ Deficit Mare' };
    let html = '';
    if (latest) {
        html += `<div class="gym-stat"><div class="gym-stat-val">${latest.weight || '—'} <span class="gym-stat-unit">kg</span></div>
            <div class="gym-stat-lbl">Ultima: ${latest.date}</div></div>`;
    } else {
        html += '<div style="color:var(--text-faint);font-size:12px">Nicio măsurătoare</div>';
    }
    if (todayCheck) {
        html += `<div><span class="food-pill ${todayCheck.level}">${LABELS[todayCheck.level]}</span></div>`;
    }
    el.innerHTML = html || '<div style="color:var(--text-faint);font-size:12px">—</div>';
}

// ===== SEARCH (debounced, searches current month) =====
let _searchTimer = null;
document.getElementById('searchInput').addEventListener('input', function(e) {
    clearTimeout(_searchTimer);
    _searchTimer = setTimeout(() => {
        const q = e.target.value.toLowerCase().trim();
        currentColumn = 0;
        if (!q) { renderLogs(allData); return; }
        const filtered = allData.map(d => {
            const matching = d.logs.filter(log => {
                const a = log.analysis || {};
                return [log.raw_text, a.short_summary, a.psychologist_feedback, a.what_went_well, a.pattern_alert, (a.tags||[]).join(' ')]
                    .filter(Boolean).join(' ').toLowerCase().includes(q);
            });
            return matching.length ? { ...d, logs: matching } : null;
        }).filter(Boolean);
        renderLogs(filtered);
    }, 250);
});

// ===== SUBMIT ENTRY =====
document.getElementById('submitEntryBtn').addEventListener('click', async function() {
    const text = document.getElementById('newEntryText').value.trim();
    if (!text) return;
    const dateInput = document.getElementById('entryDate').value;
    const today = offsetDate(0);
    const isPast = dateInput && dateInput < today;
    this.disabled = true; this.textContent = 'Se salvează...';
    try {
        const res = await fetch('/api/journal/entry', {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ text, date: dateInput || '' })
        }).then(r => r.json());
        this.disabled = false; this.textContent = '💾 Salvează';
        if (res.status === 'success') {
            const logicalDate = res.logical_date || dateInput || today;
            document.getElementById('newEntryText').value = '';
            lastSavedDate = logicalDate;
            flash('✅ Înregistrat!');
            await uploadJournalPhotos(logicalDate);

            // If the entry is for the currently displayed month, reload
            const entryMonth = logicalDate.slice(0, 7);
            if (entryMonth !== currentMonth) {
                // Switch to that month
                currentMonth = entryMonth;
                currentColumn = 0;
                if (!availableMonths.includes(entryMonth)) {
                    availableMonths.unshift(entryMonth);
                    availableMonths.sort((a,b) => b.localeCompare(a));
                }
                renderMonthPicker(availableMonths);
            } else {
                currentColumn = 0;
            }

            if (isPast) {
                const rb = document.getElementById('rejudgeAfterSaveBtn');
                rb.style.display = 'inline-flex'; rb.dataset.date = lastSavedDate;
                const st = document.getElementById('entryStatus');
                st.style.display = 'block';
                st.textContent = `Log adăugat pentru ${lastSavedDate}. Apasă Re-judecă pentru a analiza ziua.`;
            } else {
                document.getElementById('rejudgeAfterSaveBtn').style.display = 'none';
                document.getElementById('entryStatus').style.display = 'none';
            }
            loadLogsForMonth(currentMonth);
        } else flash('❌ ' + (res.message || 'Eroare'), 'error');
    } catch { this.disabled = false; this.textContent = '💾 Salvează'; flash('❌ Eroare rețea', 'error'); }
});

document.getElementById('rejudgeAfterSaveBtn').addEventListener('click', function() {
    triggerRejudge(this.dataset.date || lastSavedDate, this);
});

function triggerRejudge(date, btn) {
    const orig = btn.textContent;
    btn.textContent = '⏳...'; btn.disabled = true;
    fetch('/api/journal/rejudge', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ date })
    }).then(r => r.json()).then(d => {
        btn.textContent = orig; btn.disabled = false;
        if (d.status === 'success') {
            flash('✅ Zi re-analizată!');
            document.getElementById('rejudgeAfterSaveBtn').style.display = 'none';
            document.getElementById('entryStatus').style.display = 'none';
            loadLogsForMonth(currentMonth);
        } else flash('❌ ' + (d.message||'Eroare'), 'error');
    }).catch(() => { btn.textContent = orig; btn.disabled = false; flash('❌ Eroare rețea','error'); });
}

// ===== SCORE EDIT MODAL =====
function openScoreEdit(date, currentScores) {
    currentScoreEditDate = date;
    document.getElementById('scoreEditDate').textContent = '📅 ' + fmtDate(date);
    const container = document.getElementById('scoreSliders');
    container.innerHTML = '';
    for (const [key, meta] of Object.entries(SCORE_META)) {
        const val = currentScores[key] !== undefined ? Number(currentScores[key]) : 5;
        container.innerHTML += `
            <div class="score-slider-row">
                <div class="score-slider-lbl">${meta.label}</div>
                <input type="range" class="score-slider" id="sl_${key}" min="1" max="10" step="1" value="${val}"
                    oninput="document.getElementById('slv_${key}').textContent=this.value">
                <div class="score-slider-val" id="slv_${key}">${val}</div>
            </div>`;
    }
    document.getElementById('scoreEditModal').classList.add('open');
}

function closeScoreModal() {
    document.getElementById('scoreEditModal').classList.remove('open');
    currentScoreEditDate = null;
}

async function saveScores() {
    if (!currentScoreEditDate) return;
    const scores = {};
    for (const key of Object.keys(SCORE_META)) {
        const sl = document.getElementById(`sl_${key}`);
        if (sl) scores[key] = parseInt(sl.value);
    }
    const res = await fetch('/api/journal/update-scores', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ date: currentScoreEditDate, scores })
    }).then(r => r.json());
    if (res.status === 'success') { flash('✅ Scoruri salvate!'); closeScoreModal(); loadLogsForMonth(currentMonth); }
    else flash('❌ ' + (res.message||'Eroare'), 'error');
}

document.getElementById('scoreEditModal').addEventListener('click', function(e) {
    if (e.target === this) closeScoreModal();
});
