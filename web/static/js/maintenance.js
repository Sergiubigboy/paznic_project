'use strict';
// ============================================================
//  MAINTENANCE — maintenance.js
//  Chronos OS | Equipment maintenance with countdowns
// ============================================================

let _mntData = { items: [] };

function escHtmlM(s) {
    return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function flashM(msg, type) {
    const el = document.getElementById('flashMsg');
    if (!el) return;
    el.textContent = msg;
    el.className = `flash-msg show${type ? ' '+type : ''}`;
    setTimeout(() => el.className = 'flash-msg', 3000);
}

function daysSince(dateStr) {
    if (!dateStr) return null;
    const d = new Date(dateStr);
    const today = new Date();
    today.setHours(0,0,0,0);
    return Math.round((today - d) / 86400000);
}

function countdownInfo(task) {
    const interval = task.interval_days || 30;
    if (!task.last_done) {
        return { cls: 'never', txt: 'Niciodată', daysLeft: -9999 };
    }
    const since = daysSince(task.last_done);
    const daysLeft = interval - since;
    if (daysLeft < 0) {
        return { cls: 'overdue', txt: `${Math.abs(daysLeft)}z depășit`, daysLeft };
    } else if (daysLeft === 0) {
        return { cls: 'soon', txt: 'Azi!', daysLeft };
    } else if (daysLeft <= 7) {
        return { cls: 'soon', txt: `${daysLeft}z`, daysLeft };
    } else {
        return { cls: 'ok', txt: `${daysLeft}z`, daysLeft };
    }
}

async function loadMaintenance() {
    try {
        const res = await fetch('/api/maintenance');
        _mntData = await res.json();
        renderMaintenance();
    } catch(e) {
        console.error('Maintenance load error:', e);
    }
}

function renderMaintenance() {
    const grid = document.getElementById('mntGrid');
    if (!grid) return;

    const items = _mntData.items || [];
    if (!items.length) {
        grid.innerHTML = `<div class="dt-empty">
            🔧 Niciun echipament. Adaugă primul cu butonul de mai sus!<br>
            <small style="color:var(--text-faint);margin-top:6px;display:block">
                Ex: PC, Imprimantă, Mașină — cu task-uri de mentenanță periodică
            </small>
        </div>`;
        return;
    }

    // Summary stats
    let overdue = 0, total = 0;
    items.forEach(item => {
        (item.tasks || []).forEach(task => {
            total++;
            const info = countdownInfo(task);
            if (info.daysLeft <= 0) overdue++;
        });
    });

    const sub = document.getElementById('mntSubtitle');
    if (sub) {
        sub.textContent = `${items.length} echipamente · ${total} task-uri · ${overdue > 0 ? overdue + ' depășite ⚠️' : 'Totul OK ✅'}`;
    }

    grid.innerHTML = items.map(item => {
        const tasks = item.tasks || [];
        const overdueCount = tasks.filter(t => countdownInfo(t).daysLeft <= 0).length;
        const headerExtra = overdueCount > 0
            ? `<span style="font-size:11px;color:var(--red);font-weight:800">⚠️ ${overdueCount} depășite</span>`
            : `<span style="font-size:11px;color:var(--teal)">${tasks.length} task-uri</span>`;

        const tasksHtml = tasks.length === 0
            ? `<div style="color:var(--text-faint);font-size:12px;font-style:italic;margin-bottom:12px">
                Niciun task de mentenanță. Adaugă cu butonul de mai jos.
               </div>`
            : `<div class="mnt-task-list">
                ${tasks.map(task => {
                    const info = countdownInfo(task);
                    const lastStr = task.last_done
                        ? `Ultima: ${new Date(task.last_done).toLocaleDateString('ro-RO')}`
                        : 'Niciodată';
                    return `
                    <div class="mnt-task-row ${info.cls}" id="mtask-${task.id}">
                        <div class="mnt-task-body">
                            <div class="mnt-task-name">${escHtmlM(task.name)}</div>
                            <div class="mnt-task-interval">La ${task.interval_days} zile · ${lastStr}</div>
                            ${task.notes ? `<div style="font-size:11px;color:var(--text-faint)">${escHtmlM(task.notes)}</div>` : ''}
                        </div>
                        <span class="mnt-countdown ${info.cls}">${info.txt}</span>
                        <button class="mnt-done-btn" onclick="markMntTaskDone('${item.id}','${task.id}')">
                            ✅ Am făcut
                        </button>
                        <button class="mnt-del-task-btn" onclick="deleteMntTask('${item.id}','${task.id}')" title="Șterge task">🗑️</button>
                    </div>`;
                }).join('')}
               </div>`;

        return `
        <div class="mnt-item-card" id="mnt-${item.id}">
            <div class="mnt-item-header" onclick="toggleMntItem('${item.id}')">
                <span class="mnt-item-emoji">${item.emoji || '🔧'}</span>
                <div style="flex:1">
                    <div class="mnt-item-name">${escHtmlM(item.name)}</div>
                    <div class="mnt-item-stats">${headerExtra}</div>
                </div>
                <button class="mnt-expand-btn" onclick="event.stopPropagation();toggleMntItem('${item.id}')">▶</button>
                <button class="mnt-del-item-btn" onclick="event.stopPropagation();deleteMntItem('${item.id}')" title="Șterge echipament">🗑️</button>
            </div>
            <div class="mnt-tasks-body">
                ${tasksHtml}
                <div class="mnt-add-task-row">
                    <button class="btn btn-secondary btn-sm" onclick="openAddMntTaskModal('${item.id}','${escHtmlM(item.name)}')">
                        ＋ Task Mentenanță
                    </button>
                </div>
            </div>
        </div>`;
    }).join('');
}

function toggleMntItem(itemId) {
    const card = document.getElementById(`mnt-${itemId}`);
    if (card) card.classList.toggle('expanded');
}

async function markMntTaskDone(itemId, taskId) {
    const res = await fetch('/api/maintenance/task/done', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ item_id: itemId, task_id: taskId })
    });
    const data = await res.json();
    if (data.status === 'success') {
        // Update local data
        const item = (_mntData.items || []).find(i => i.id === itemId);
        if (item) {
            const task = (item.tasks || []).find(t => t.id === taskId);
            if (task) task.last_done = new Date().toISOString().slice(0,10);
        }
        renderMaintenance();
        // Re-expand the item
        setTimeout(() => {
            const card = document.getElementById(`mnt-${itemId}`);
            if (card) card.classList.add('expanded');
        }, 30);
        flashM('✅ Task marcat ca efectuat! Countdown resetat.', 'success');
    } else {
        flashM(data.message || 'Eroare', 'error');
    }
}

async function deleteMntItem(itemId) {
    const item = (_mntData.items || []).find(i => i.id === itemId);
    if (!confirm(`Ștergi echipamentul "${item?.name}" și toate task-urile sale?`)) return;
    const res = await fetch('/api/maintenance/item/delete', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ id: itemId })
    });
    const data = await res.json();
    if (data.status === 'success') {
        _mntData.items = (_mntData.items || []).filter(i => i.id !== itemId);
        renderMaintenance();
        flashM('🗑️ Echipament șters');
    }
}

async function deleteMntTask(itemId, taskId) {
    const item = (_mntData.items || []).find(i => i.id === itemId);
    const task = (item?.tasks || []).find(t => t.id === taskId);
    if (!confirm(`Ștergi task-ul "${task?.name}"?`)) return;
    const res = await fetch('/api/maintenance/task/delete', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ item_id: itemId, task_id: taskId })
    });
    const data = await res.json();
    if (data.status === 'success') {
        if (item) item.tasks = (item.tasks || []).filter(t => t.id !== taskId);
        renderMaintenance();
        setTimeout(() => {
            const card = document.getElementById(`mnt-${itemId}`);
            if (card) card.classList.add('expanded');
        }, 30);
        flashM('🗑️ Task șters');
    }
}

// ---- ITEM MODAL ----
function openAddMntItemModal() {
    document.getElementById('mntItemName').value = '';
    document.getElementById('mntItemEmoji').value = '🔧';
    document.getElementById('addMntItemModal').classList.add('open');
    setTimeout(() => document.getElementById('mntItemName').focus(), 50);
}
function closeAddMntItemModal() {
    document.getElementById('addMntItemModal').classList.remove('open');
}
async function saveMntItem() {
    const name  = document.getElementById('mntItemName').value.trim();
    const emoji = document.getElementById('mntItemEmoji').value.trim() || '🔧';
    if (!name) { flashM('Introduci un nume!', 'error'); return; }
    const res = await fetch('/api/maintenance/item/add', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ name, emoji })
    });
    const data = await res.json();
    if (data.status === 'success') {
        closeAddMntItemModal();
        await loadMaintenance();
        flashM('✅ Echipament adăugat!');
    } else {
        flashM(data.message || 'Eroare', 'error');
    }
}

// ---- TASK MODAL ----
let _mntTaskTargetItemId = null;
function openAddMntTaskModal(itemId, itemName) {
    _mntTaskTargetItemId = itemId;
    document.getElementById('mntTaskModalTitle').textContent = `⚙️ Task Mentenanță — ${itemName}`;
    document.getElementById('mntTaskItemId').value = itemId;
    document.getElementById('mntTaskName').value = '';
    document.getElementById('mntTaskInterval').value = '30';
    document.getElementById('mntTaskNotes').value = '';
    document.getElementById('addMntTaskModal').classList.add('open');
    setTimeout(() => document.getElementById('mntTaskName').focus(), 50);
}
function closeAddMntTaskModal() {
    document.getElementById('addMntTaskModal').classList.remove('open');
}
async function saveMntTask() {
    const itemId   = document.getElementById('mntTaskItemId').value;
    const name     = document.getElementById('mntTaskName').value.trim();
    const interval = parseInt(document.getElementById('mntTaskInterval').value) || 30;
    const notes    = document.getElementById('mntTaskNotes').value.trim();
    if (!name) { flashM('Introduci un nume pentru task!', 'error'); return; }
    const res = await fetch('/api/maintenance/task/add', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ item_id: itemId, name, interval_days: interval, notes })
    });
    const data = await res.json();
    if (data.status === 'success') {
        closeAddMntTaskModal();
        await loadMaintenance();
        // Re-expand the card
        setTimeout(() => {
            const card = document.getElementById(`mnt-${itemId}`);
            if (card) card.classList.add('expanded');
        }, 50);
        flashM('✅ Task adăugat!');
    } else {
        flashM(data.message || 'Eroare', 'error');
    }
}

// Close on backdrop
document.addEventListener('DOMContentLoaded', () => {
    ['addMntItemModal','addMntTaskModal'].forEach(id => {
        const modal = document.getElementById(id);
        if (modal) {
            modal.addEventListener('click', e => {
                if (e.target === modal) modal.classList.remove('open');
            });
        }
    });
});
