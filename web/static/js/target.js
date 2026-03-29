// ============ HELPERS ============
function flash(msg, type = 'success') {
    const el = document.getElementById('flashMsg');
    el.textContent = msg;
    el.className = `flash-msg show ${type}`;
    setTimeout(() => el.className = 'flash-msg', 3500);
}

const PRIORITY_CLASS = { High: 'priority-high', Med: 'priority-med', Low: 'priority-low' };
const CAT_ICONS = {
    Sanatate: '💪', Proiecte: '🔧', Scoala: '📚',
    Personal: '🧠', Social: '👥', General: '📌'
};

// ============ LOAD ============
let allGoals = [];

function loadTargets() {
    fetch('/api/targets')
        .then(r => r.json())
        .then(data => {
            allGoals = data.goals || [];
            renderTargets(allGoals);
            renderSummary(allGoals);
        });
}

function renderSummary(goals) {
    const el = document.getElementById('targetSummary');
    if (!el) return;
    const high = goals.filter(g => g.priority === 'High').length;
    const expiring = goals.filter(g => g.deadline && daysUntil(g.deadline) <= 7 && daysUntil(g.deadline) >= 0).length;
    let txt = `${goals.length} targeturi active`;
    if (high) txt += ` · ${high} urgente 🔴`;
    if (expiring) txt += ` · ${expiring} expiră curând ⚠️`;
    el.textContent = txt;
}



loadTargets();

// ============ RENDER ============
function renderTargets(goals) {
    const grid = document.getElementById('targetsGrid');
    if (!goals.length) {
        grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1">Niciun target activ. Adaugă primul! 🚀</div>';
        return;
    }

    // Sort: High first, then Med, then Low
    const sorted = [...goals].sort((a, b) => {
        const order = { High: 0, Med: 1, Low: 2 };
        return (order[a.priority] ?? 1) - (order[b.priority] ?? 1);
    });

    grid.innerHTML = sorted.map(g => {
        const prog = Number(g.progress || 0);
        const pClass = PRIORITY_CLASS[g.priority] || 'priority-med';
        const catIcon = CAT_ICONS[g.category] || '📌';
        const daysLeft = g.deadline ? daysUntil(g.deadline) : null;
        const daysStr = daysLeft !== null
            ? (daysLeft < 0 ? `<span style="color:var(--accent-red)">⚠️ Expirat acum ${Math.abs(daysLeft)}z</span>`
                : daysLeft === 0 ? `<span style="color:var(--accent-orange)">🔥 Azi!</span>`
                : `📅 ${daysLeft}z rămase`)
            : '';

        return `
        <div class="target-card" data-id="${g.id}">
            <div class="target-card-header">
                <h3>${catIcon} ${g.title || ''}</h3>
                <span class="priority-badge ${pClass}">${g.priority || 'Med'}</span>
            </div>
            ${g.description ? `<div class="target-desc">${g.description}</div>` : ''}
            <div class="target-meta">
                ${daysStr}
                ${g.category ? `<span>${g.category}</span>` : ''}
            </div>
            <div class="target-progress-label">
                <span>Progres</span>
                <span id="progLabel_${g.id}">${prog}%</span>
            </div>
            <div class="target-progress-bar">
                <div class="progress-bar-fill ${prog >= 100 ? 'complete' : ''}" 
                     id="progBar_${g.id}" style="width:${prog}%"></div>
            </div>
            <div class="target-controls">
                <input type="number" class="progress-input" id="progInput_${g.id}"
                       value="${prog}" min="0" max="100">
                <button class="btn btn-secondary btn-sm" onclick="saveProgress('${g.id}')">Salvează</button>
                <button class="btn btn-teal btn-sm" onclick="markDone('${g.id}')">✅ Finalizat</button>
                <button class="btn btn-danger" onclick="deleteTarget('${g.id}', this)">🗑️</button>
            </div>
        </div>`;
    }).join('');
}

function daysUntil(dateStr) {
    const d = new Date(dateStr);
    const now = new Date();
    now.setHours(0,0,0,0);
    d.setHours(0,0,0,0);
    return Math.round((d - now) / 86400000);
}

// ============ ACTIONS ============
function saveProgress(id) {
    const val = parseInt(document.getElementById(`progInput_${id}`).value, 10);
    if (isNaN(val)) return;

    fetch('/api/targets/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, progress: val })
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success') {
            flash('✅ Progres actualizat!');
            // Update UI without full reload
            const bar = document.getElementById(`progBar_${id}`);
            const label = document.getElementById(`progLabel_${id}`);
            if (bar) { bar.style.width = val + '%'; bar.className = `progress-bar-fill ${val >= 100 ? 'complete' : ''}`; }
            if (label) label.textContent = val + '%';
            if (val >= 100) {
                flash('🎉 Target completat și arhivat!');
                setTimeout(loadTargets, 1500);
            }
        } else {
            flash('❌ Eroare: ' + data.message, 'error');
        }
    });
}

function markDone(id) {
    fetch('/api/targets/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, progress: 100 })
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success') {
            flash('🎉 Target completat și arhivat!');
            setTimeout(loadTargets, 800);
        } else {
            flash('❌ Eroare', 'error');
        }
    });
}

function deleteTarget(id, btn) {
    if (!confirm('Ștergi acest target?')) return;
    btn.disabled = true;
    fetch('/api/targets/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id })
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success') {
            flash('🗑️ Target șters.');
            loadTargets();
        } else {
            flash('❌ Eroare', 'error');
            btn.disabled = false;
        }
    });
}

// ============ ADD MODAL ============
document.getElementById('openAddModal').addEventListener('click', () => {
    document.getElementById('addModal').classList.add('open');
});
document.getElementById('closeAddModal').addEventListener('click', () => {
    document.getElementById('addModal').classList.remove('open');
});
document.getElementById('addModal').addEventListener('click', function(e) {
    if (e.target === this) this.classList.remove('open');
});

document.getElementById('confirmAddBtn').addEventListener('click', function () {
    const title = document.getElementById('newTitle').value.trim();
    if (!title) { flash('❌ Titlul e obligatoriu!', 'error'); return; }

    this.disabled = true;
    this.textContent = 'Se salvează...';

    fetch('/api/targets/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            title,
            description: document.getElementById('newDesc').value.trim(),
            deadline: document.getElementById('newDeadline').value,
            priority: document.getElementById('newPriority').value,
            category: document.getElementById('newCategory').value
        })
    })
    .then(r => r.json())
    .then(data => {
        this.disabled = false;
        this.textContent = 'Salvează Targetul';
        if (data.status === 'success') {
            document.getElementById('addModal').classList.remove('open');
            document.getElementById('newTitle').value = '';
            document.getElementById('newDesc').value = '';
            document.getElementById('newDeadline').value = '';
            flash('✅ Target adăugat!');
            loadTargets();
        } else {
            flash('❌ Eroare: ' + data.message, 'error');
        }
    });
});