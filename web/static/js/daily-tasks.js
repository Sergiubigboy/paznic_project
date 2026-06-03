// ===== DAILY TASKS JS =====

let dtTasks = [];       // lista task-urilor
let dtChecks = {};      // { "2026-04-15": ["id1", "id2"], ... }
let dtHistory = [];     // history enriched per task (cu streak data)
let dtCurrentDate = offsetDate(0);
let dtSelectedEmoji = '✅';

// Tab switching is handled by target.js switchTab()
// initDailyTasks() is called lazily from there when the daily tab is selected


// ===== INIT =====
async function initDailyTasks() {
    await loadDtHistory();
    updateDtDateDisplay();
    renderDtList();
}

// ===== DATE HELPERS =====
function setDtDate(days, btn) {
    const row = document.querySelector('.dt-date-bar');
    if (row) row.querySelectorAll('.date-quick-btn').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    dtCurrentDate = offsetDate(days);
    document.getElementById('dtDateInput').value = dtCurrentDate;
    updateDtDateDisplay();
    renderDtList();
}

function onDtDateChange() {
    const val = document.getElementById('dtDateInput').value;
    if (!val) return;
    dtCurrentDate = val;
    // Deactivate quick buttons
    document.querySelectorAll('.dt-date-bar .date-quick-btn').forEach(b => b.classList.remove('active'));
    updateDtDateDisplay();
    renderDtList();
}

function updateDtDateDisplay() {
    const el = document.getElementById('dtDateDisplay');
    if (!el) return;
    const today = offsetDate(0);
    const yesterday = offsetDate(-1);
    let label = '';
    if (dtCurrentDate === today) label = '📅 Azi — ' + fmtDateShort(dtCurrentDate);
    else if (dtCurrentDate === yesterday) label = '📅 Ieri — ' + fmtDateShort(dtCurrentDate);
    else label = '📅 ' + fmtDateShort(dtCurrentDate);
    el.textContent = label;

    // Update subtitle
    const sub = document.getElementById('dtSubtitle');
    if (sub) {
        const checkedCount = (dtChecks[dtCurrentDate] || []).length;
        const total = dtTasks.length;
        sub.textContent = total
            ? `${checkedCount}/${total} bifate pentru ${dtCurrentDate === today ? 'azi' : fmtDateShort(dtCurrentDate)}`
            : 'Adaugă primul task zilnic';
    }
}

// ===== LOAD DATA =====
async function loadDtHistory() {
    try {
        const res = await fetch('/api/daily-tasks/history?days=30').then(r => r.json());
        dtHistory = res.tasks || [];
        // Extract base task list and checks
        const baseData = await fetch('/api/daily-tasks').then(r => r.json());
        dtTasks = baseData.tasks || [];
        dtChecks = baseData.checks || {};
    } catch (e) {
        console.error('Error loading daily tasks:', e);
    }
}

// ===== RENDER =====
function renderDtList() {
    const el = document.getElementById('dtList');
    if (!el) return;

    if (!dtTasks.length) {
        el.innerHTML = `<div class="dt-empty">
            <div style="font-size:40px;margin-bottom:12px">✅</div>
            <div style="font-weight:700;color:var(--text);margin-bottom:6px">Niciun task zilnic</div>
            <div>Adaugă task-uri pe care vrei să le faci zilnic (sală, citit, etc)</div>
        </div>`;
        updateDtDateDisplay();
        return;
    }

    const today = offsetDate(0);
    const checkedTodayIds = dtChecks[dtCurrentDate] || [];

    el.innerHTML = dtTasks.map(task => {
        const isChecked = checkedTodayIds.includes(task.id);
        const histTask = dtHistory.find(h => h.id === task.id);
        const streakHtml = buildStreakHtml(histTask, task);
        const daysDone = histTask ? histTask.days_done : 0;
        const daysPossible = histTask ? histTask.days_possible : 0;
        const pct = histTask ? histTask.streak_pct : 0;
        const pctColor = pct >= 70 ? 'var(--green)' : pct >= 40 ? 'var(--yellow)' : 'var(--red)';
        const approxPer = daysPossible > 0
            ? (daysDone / daysPossible * 7).toFixed(1) + '×/săpt'
            : '—';

        return `
        <div class="dt-task-card ${isChecked ? 'dt-checked' : ''}" id="dtCard_${task.id}">
            <div class="dt-task-header">
                <button class="dt-check-btn ${isChecked ? 'checked' : ''}"
                        id="dtChk_${task.id}"
                        onclick="toggleDtCheck('${task.id}')"
                        title="${isChecked ? 'Debifează' : 'Bifează'}">
                    ${isChecked ? '✓' : ''}
                </button>
                <span class="dt-task-emoji">${task.emoji || '✅'}</span>
                <span class="dt-task-name">${escapeHtml(task.name)}</span>
                <div class="dt-task-meta">de la ${fmtDateShort(task.created_at)}</div>
                <button class="dt-delete-btn" onclick="deleteDtTask('${task.id}')" title="Șterge task">🗑️</button>
            </div>

            <div class="dt-stats-row">
                <div class="dt-stat">
                    <strong style="color:${pctColor}">${pct}%</strong>
                    <span style="color:var(--text-faint)"> consistency</span>
                </div>
                <div class="dt-stat">
                    <strong style="color:var(--primary)">${daysDone}</strong>
                    <span style="color:var(--text-faint)">/${daysPossible} zile</span>
                </div>
                <div class="dt-stat">
                    <strong style="color:var(--teal)">${approxPer}</strong>
                </div>
            </div>

            ${streakHtml}
        </div>`;
    }).join('');

    updateDtDateDisplay();
}

function buildStreakHtml(histTask, task) {
    if (!histTask) return '';
    const dateRange = histTask.date_range || [];
    const checkedSet = new Set(histTask.checked_dates || []);
    const today = offsetDate(0);
    const created = task.created_at;

    // Show last 30 days
    const last30 = dateRange.slice(-30);

    const bars = last30.map(d => {
        if (d > today) return `<div class="dt-streak-day future" title="${d}"></div>`;
        if (d < created) return `<div class="dt-streak-day no-task" title="${d}"></div>`;
        const done = checkedSet.has(d);
        return `<div class="dt-streak-day ${done ? 'done' : 'miss'}"
                     style="${done ? 'height:100%' : 'height:40%'}"
                     title="${d}: ${done ? 'Bifat ✓' : 'Nebisat ✗'}"></div>`;
    }).join('');

    return `<div class="dt-streak-row" title="Ultimele 30 zile">${bars}</div>
            <div style="font-size:10px;color:var(--text-faint);margin-top:3px;display:flex;justify-content:space-between">
                <span>${last30[0] ? fmtDateShort(last30[0]).split(' ').slice(0,2).join(' ') : ''}</span>
                <span>azi</span>
            </div>`;
}

function escapeHtml(str) {
    return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ===== TOGGLE CHECK =====
async function toggleDtCheck(taskId) {
    const checkedIds = dtChecks[dtCurrentDate] || [];
    const isNowChecked = !checkedIds.includes(taskId);

    // Optimistic update
    if (isNowChecked) {
        if (!dtChecks[dtCurrentDate]) dtChecks[dtCurrentDate] = [];
        dtChecks[dtCurrentDate].push(taskId);
    } else {
        dtChecks[dtCurrentDate] = dtChecks[dtCurrentDate].filter(id => id !== taskId);
    }
    renderDtList();

    try {
        const res = await fetch('/api/daily-tasks/check', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ id: taskId, date: dtCurrentDate, done: isNowChecked })
        }).then(r => r.json());

        if (res.status !== 'success') {
            flash('❌ Eroare la salvare', 'error');
            // Revert
            await loadDtHistory();
            renderDtList();
        } else {
            // Reload history for updated streak
            await loadDtHistory();
            renderDtList();
        }
    } catch (e) {
        flash('❌ Eroare conexiune', 'error');
    }
}

// ===== ADD TASK =====
function openAddTaskModal() {
    const area = document.getElementById('addTaskArea');
    area.style.display = 'flex';
    document.getElementById('newTaskName').value = '';
    dtSelectedEmoji = '✅';
    document.querySelectorAll('.dt-emoji-opt').forEach(b => b.classList.remove('selected'));
    const defaultBtn = document.querySelector('.dt-emoji-opt[data-emoji="✅"]');
    if (defaultBtn) defaultBtn.classList.add('selected');
    document.getElementById('newTaskName').focus();
    area.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function closeAddTaskModal() {
    document.getElementById('addTaskArea').style.display = 'none';
}

function selectEmoji(emoji, btn) {
    dtSelectedEmoji = emoji;
    document.querySelectorAll('.dt-emoji-opt').forEach(b => b.classList.remove('selected'));
    if (btn) btn.classList.add('selected');
}

async function confirmAddTask() {
    const name = document.getElementById('newTaskName').value.trim();
    if (!name) { flash('❌ Introdu un nume pentru task', 'error'); return; }

    try {
        const res = await fetch('/api/daily-tasks/add', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ name, emoji: dtSelectedEmoji })
        }).then(r => r.json());

        if (res.status === 'success') {
            flash('✅ Task adăugat!');
            closeAddTaskModal();
            await loadDtHistory();
            renderDtList();
        } else {
            flash('❌ ' + res.message, 'error');
        }
    } catch (e) {
        flash('❌ Eroare conexiune', 'error');
    }
}

// ===== DELETE TASK =====
async function deleteDtTask(taskId) {
    const task = dtTasks.find(t => t.id === taskId);
    if (!confirm(`Ștergi task-ul "${task?.name || taskId}"? Istoricul bifărilor se va șterge și el.`)) return;

    try {
        const res = await fetch('/api/daily-tasks/delete', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ id: taskId })
        }).then(r => r.json());

        if (res.status === 'success') {
            flash('🗑️ Task șters');
            await loadDtHistory();
            renderDtList();
        } else {
            flash('❌ ' + res.message, 'error');
        }
    } catch (e) {
        flash('❌ Eroare conexiune', 'error');
    }
}

// ===== INIT DATE INPUT =====
document.addEventListener('DOMContentLoaded', () => {
    const dtInput = document.getElementById('dtDateInput');
    if (dtInput) dtInput.value = dtCurrentDate;
});
