'use strict';
// ============================================================
//  REMINDERS — reminders.js
//  Chronos OS | Todo-style reminders without deadlines
// ============================================================

let _reminders = [];

function escHtmlR(s) {
    return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function flashR(msg, type) {
    const el = document.getElementById('flashMsg');
    if (!el) return;
    el.textContent = msg;
    el.className = `flash-msg show${type ? ' '+type : ''}`;
    setTimeout(() => el.className = 'flash-msg', 3000);
}

async function loadReminders() {
    try {
        const res = await fetch('/api/reminders');
        const data = await res.json();
        _reminders = data.reminders || [];
        renderReminders();
    } catch(e) {
        console.error('Reminders load error:', e);
    }
}

function renderReminders() {
    const grid = document.getElementById('remGrid');
    if (!grid) return;

    if (!_reminders.length) {
        grid.innerHTML = `<div class="dt-empty">
            📌 Niciun reminder. Adaugă primul reminder cu butonul de mai sus!
        </div>`;
        return;
    }

    // Sort: unchecked first, then by priority
    const order = {High:0, Med:1, Low:2};
    const sorted = [..._reminders].sort((a,b) => {
        if (a.checked !== b.checked) return a.checked ? 1 : -1;
        return (order[a.priority]??1) - (order[b.priority]??1);
    });

    grid.innerHTML = sorted.map(rem => {
        const lastChecked = rem.last_checked
            ? `Ultima bifă: ${new Date(rem.last_checked).toLocaleDateString('ro-RO')}`
            : 'Niciodată bifat';
        return `
        <div class="rem-card ${rem.checked ? 'rem-done' : ''}" id="rem-${rem.id}">
            <button class="rem-check-btn ${rem.checked ? 'checked' : ''}"
                    onclick="toggleReminder('${rem.id}', ${!rem.checked})"
                    title="${rem.checked ? 'Debifează' : 'Bifează'}">
                ${rem.checked ? '✓' : ''}
            </button>
            <span class="rem-emoji">${rem.emoji || '📌'}</span>
            <div class="rem-body">
                <div class="rem-title" style="${rem.checked ? 'text-decoration:line-through;opacity:0.6' : ''}">${escHtmlR(rem.title)}</div>
                ${rem.description ? `<div class="rem-desc">${escHtmlR(rem.description)}</div>` : ''}
                <div class="rem-meta">${lastChecked}</div>
            </div>
            <span class="rem-prio ${rem.priority || 'Med'}">${rem.priority || 'Med'}</span>
            <div class="rem-actions">
                <button class="rem-btn" title="Editează" onclick="openEditReminderModal('${rem.id}')">✏️</button>
                <button class="rem-btn" title="Șterge" onclick="deleteReminder('${rem.id}')">🗑️</button>
            </div>
        </div>`;
    }).join('');

    // Update subtitle
    const sub = document.getElementById('remSubtitle');
    if (sub) {
        const done = _reminders.filter(r => r.checked).length;
        sub.textContent = `${_reminders.length} remindere · ${done} bifate`;
    }
}

async function toggleReminder(id, checked) {
    const res = await fetch('/api/reminders/check', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ id, checked })
    });
    const data = await res.json();
    if (data.status === 'success') {
        const rem = _reminders.find(r => r.id === id);
        if (rem) {
            rem.checked = checked;
            rem.last_checked = checked ? new Date().toISOString() : rem.last_checked;
        }
        renderReminders();
    }
}

async function deleteReminder(id) {
    const rem = _reminders.find(r => r.id === id);
    if (!confirm(`Ștergi reminder-ul "${rem?.title}"?`)) return;
    const res = await fetch('/api/reminders/delete', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ id })
    });
    const data = await res.json();
    if (data.status === 'success') {
        _reminders = _reminders.filter(r => r.id !== id);
        renderReminders();
        flashR('🗑️ Reminder șters');
    }
}

// ---- MODALS ----
function openAddReminderModal() {
    document.getElementById('remModalTitle').textContent = '📌 Reminder Nou';
    document.getElementById('remEditId').value = '';
    document.getElementById('remTitle').value = '';
    document.getElementById('remDesc').value = '';
    document.getElementById('remEmoji').value = '📌';
    document.getElementById('remPriority').value = 'Med';
    document.getElementById('addReminderModal').classList.add('open');
    setTimeout(() => document.getElementById('remTitle').focus(), 50);
}

function openEditReminderModal(id) {
    const rem = _reminders.find(r => r.id === id);
    if (!rem) return;
    document.getElementById('remModalTitle').textContent = '✏️ Editează Reminder';
    document.getElementById('remEditId').value = id;
    document.getElementById('remTitle').value = rem.title || '';
    document.getElementById('remDesc').value = rem.description || '';
    document.getElementById('remEmoji').value = rem.emoji || '📌';
    document.getElementById('remPriority').value = rem.priority || 'Med';
    document.getElementById('addReminderModal').classList.add('open');
    setTimeout(() => document.getElementById('remTitle').focus(), 50);
}

function closeAddReminderModal() {
    document.getElementById('addReminderModal').classList.remove('open');
}

async function saveReminder() {
    const editId = document.getElementById('remEditId').value;
    const title    = document.getElementById('remTitle').value.trim();
    const desc     = document.getElementById('remDesc').value.trim();
    const emoji    = document.getElementById('remEmoji').value.trim() || '📌';
    const priority = document.getElementById('remPriority').value;

    if (!title) { flashR('Titlul este obligatoriu!', 'error'); return; }

    const url = editId ? '/api/reminders/edit' : '/api/reminders/add';
    const payload = { title, description: desc, emoji, priority };
    if (editId) payload.id = editId;

    const res = await fetch(url, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (data.status === 'success') {
        closeAddReminderModal();
        flashR(editId ? '✏️ Reminder actualizat!' : '✅ Reminder adăugat!');
        await loadReminders();
    } else {
        flashR(data.message || 'Eroare', 'error');
    }
}

// Close modal on backdrop click
document.addEventListener('DOMContentLoaded', () => {
    const modal = document.getElementById('addReminderModal');
    if (modal) {
        modal.addEventListener('click', e => {
            if (e.target === modal) closeAddReminderModal();
        });
    }
});
