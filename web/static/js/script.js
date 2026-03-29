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
let allData = [];
let lastSavedDate = null;
let currentScoreEditDate = null;
let journalPhotoPending = [null, null, null];
let pendingEntryPhotoFiles = [];
let currentEntryPhotoDate = null;

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
    loadAll();
}

// ===== LOAD =====
function loadAll() {
    Promise.all([
        fetch('/api/logs').then(r => r.json()),
        fetch('/api/targets').then(r => r.json()),
        fetch('/api/gym/measurements').then(r => r.json()).catch(() => []),
        fetch('/api/gym/daily-checks').then(r => r.json()).catch(() => [])
    ]).then(([logs, targets, measurements, checks]) => {
        allData = logs;
        renderLogs(logs);
        renderSidebarTargets(targets.goals || []);
        renderSidebarGym(measurements, checks);
    });
}
loadAll();

// ===== RENDER LOGS =====
function renderLogs(daysArray) {
    const container = document.getElementById('logsContainer');
    container.innerHTML = '';
    const noResults = document.getElementById('noResults');
    noResults.className = daysArray.length === 0 ? 'empty-state no-results visible' : 'empty-state no-results';

    daysArray.forEach(dayObj => {
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
        container.appendChild(group);
    });
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

// ===== SEARCH =====
document.getElementById('searchInput').addEventListener('input', function(e) {
    const q = e.target.value.toLowerCase().trim();
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
            loadAll();
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
            loadAll();
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
    if (res.status === 'success') { flash('✅ Scoruri salvate!'); closeScoreModal(); loadAll(); }
    else flash('❌ ' + (res.message||'Eroare'), 'error');
}

document.getElementById('scoreEditModal').addEventListener('click', function(e) {
    if (e.target === this) closeScoreModal();
});
