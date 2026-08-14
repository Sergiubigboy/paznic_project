// ===== DAY PAGE JS — Hub Central =====

const TODAY = offsetDate(0);
let scheduleEvents = [];
let statusData = null;

// ===== INIT =====
async function init() {
    setHeroHeader();
    // Listen for status loaded from daily-status.js
    document.addEventListener('dayStatusLoaded', (e) => {
        statusData = e.detail;
        renderChecklist(statusData);
        renderWeightSidebar(statusData);
        renderFoodCheckSidebar(statusData);
        renderJournalFeed(statusData);
    });
    // Also reload schedule independently
    await loadSchedule();
    // Prefill today's date inputs
    setHeroHeader();
}

function setHeroHeader() {
    const now = new Date();
    const WEEKDAYS = ['Duminică','Luni','Marți','Miercuri','Joi','Vineri','Sâmbătă'];
    const MONTHS = ['ian','feb','mar','apr','mai','iun','iul','aug','sep','oct','nov','dec'];
    const greetings = {
        1: 'Luni. Săptămâna începe.', 2: 'Marți. Continuă ritmul.',
        3: 'Miercuri. Jumătatea drumului.', 4: 'Joi. Aproape de final.',
        5: 'Vineri. Ultima împingere.', 6: 'Sâmbătă. Ziua ta.', 0: 'Duminică. Reîncarcă-te.'
    };
    document.getElementById('dayTitle').textContent = `🌅 ${WEEKDAYS[now.getDay()]}, ${now.getDate()} ${MONTHS[now.getMonth()]}`;
    document.getElementById('daySubtitle').textContent = greetings[now.getDay()] || 'Ziua progresului tău.';
}

// ===== CHECKLIST (auto from status) =====
function renderChecklist(s) {
    const items = document.getElementById('checklistItems');
    const fill = document.getElementById('clProgFill');
    const txt = document.getElementById('clProgTxt');

    const weightLogged = s.weight?.logged;
    const weightVal = weightLogged ? `${s.weight.value} kg` : (s.last_weight_ever ? `${s.last_weight_ever} kg (ieri)` : null);
    const foodLogged = s.food_check?.logged;
    const foodVal = foodLogged ? FOOD_LABELS_SHORT[s.food_check.level] : null;
    const journalLogged = (s.journal?.entries_today || 0) > 0;
    const journalVal = journalLogged ? `${s.journal.entries_today} entr${s.journal.entries_today > 1 ? 'ies' : 'y'}` : null;
    const briefingDone = !!document.getElementById('briefingContent')?.style?.display !== 'none'
        && document.getElementById('briefingGreeting')?.textContent;

    const LIST = [
        {
            done: weightLogged, icon: '⚖️', text: 'Loghează greutatea',
            val: weightVal, href: null, action: () => document.getElementById('wqInput')?.focus()
        },
        {
            done: journalLogged, icon: '📝', text: 'Scrie în jurnal',
            val: journalVal, href: null, action: () => document.getElementById('quickJournalText')?.focus()
        },
        {
            done: foodLogged, icon: '🍽️', text: 'Bifează alimentația',
            val: foodVal, href: null, action: () => document.querySelector('.fc-btn')?.focus()
        },
        {
            done: false, icon: '✨', text: 'Generează briefingul AI',
            val: null, href: null, action: generateBriefing
        }
    ];

    const doneCount = LIST.slice(0, 4).filter(i => i.done).length;
    const total = LIST.length;

    fill.style.width = (doneCount / total * 100) + '%';
    txt.textContent = `${doneCount}/${total}`;
    if (doneCount === total - 1) txt.style.color = 'var(--yellow)';
    if (doneCount === total) txt.style.color = 'var(--green)';

    items.innerHTML = '';
    LIST.forEach(item => {
        const div = document.createElement('div');
        div.className = `cl-item${item.done ? ' done' : ''}`;
        div.innerHTML = `
            <div class="cl-check">${item.done ? '✓' : ''}</div>
            <span class="cl-item-icon">${item.icon}</span>
            <span class="cl-item-text">${item.text}</span>
            ${item.val ? `<span class="cl-item-val">${item.val}</span>` : ''}
            ${!item.done ? '<span class="cl-item-arrow">→</span>' : ''}
        `;
        if (!item.done && item.action) {
            div.onclick = () => { item.action(); div.style.borderColor = 'var(--primary)'; setTimeout(() => div.style.borderColor = '', 1000); };
        }
        items.appendChild(div);
    });
}

const FOOD_LABELS_SHORT = {
    surplus_mare: 'Surplus+',
    mentinere: 'Menținere',
    deficit: 'Deficit',
    deficit_mare: 'Deficit-'
};

function fmtMins(m) {
    if (!m) return '0h';
    const h = Math.floor(m / 60), min = m % 60;
    return min > 0 ? `${h}h ${min}m` : `${h}h`;
}

// ===== JOURNAL FEED =====
function renderJournalFeed(s) {
    const el = document.getElementById('journalFeedItems');
    const entries = s.journal?.entries || [];
    if (!entries.length) {
        el.innerHTML = `<div class="jf-empty">Nicio intrare azi. <a href="/" style="color:var(--primary)">Deschide jurnalul complet →</a></div>`;
        return;
    }
    el.innerHTML = `<div class="jf-items">` +
        entries.map(e => `
            <div class="jf-item">
                <div class="jf-time">${e.time || '—'}</div>
                <div class="jf-text">${escHtml(e.text)}</div>
            </div>
        `).join('') + `</div>`;
}

function escHtml(str) {
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function quickJournalSave() {
    const text = document.getElementById('quickJournalText').value.trim();
    if (!text) return;
    const res = await fetch('/api/journal/entry', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ text, date: '' })
    }).then(r => r.json());
    if (res.status === 'success') {
        document.getElementById('quickJournalText').value = '';
        flash('✅ Adăugat în jurnal!');
        await window.refreshDayStatus?.();
    } else flash('❌ ' + (res.message || 'Eroare'), 'error');
}

// ===== WEIGHT SIDEBAR =====
function renderWeightSidebar(s) {
    const trend = document.getElementById('wqTrend');
    const hist = document.getElementById('wqHist');

    // Pre-fill if already logged today
    if (s.weight?.logged) {
        document.getElementById('wqInput').value = s.weight.value;
    }

    // Trend
    if (trend) {
        if (s.weight?.logged && s.weight?.trend !== null && s.weight?.trend !== undefined) {
            const t = s.weight.trend;
            const col = t > 0 ? 'var(--red)' : t < 0 ? 'var(--green)' : 'var(--text-faint)';
            const arrow = t > 0 ? '↑' : t < 0 ? '↓' : '→';
            trend.innerHTML = `<span style="color:${col};font-weight:800;font-size:14px">${arrow} ${Math.abs(t)} kg</span> <span>față de ieri</span>`;
        } else if (s.last_weight_ever) {
            trend.innerHTML = `<span style="color:var(--text-faint)">Ultimul: ${s.last_weight_ever} kg</span>`;
        }
    }

    // History
    if (hist) {
        const weights = s.recent_weights || [];
        if (!weights.length) { hist.innerHTML = ''; return; }
        hist.innerHTML = weights.slice(0, 5).map((w, i) => {
            const prev = weights[i + 1];
            let diffHtml = '';
            if (prev) {
                const d = (w.weight - prev.weight).toFixed(1);
                const col = d > 0 ? 'var(--red)' : d < 0 ? 'var(--green)' : 'var(--text-faint)';
                diffHtml = `<span style="color:${col};font-weight:800;font-size:10px">${d > 0 ? '+' : ''}${d}</span>`;
            }
            return `<div class="wq-hist-item">
                <span class="wq-hist-date">${fmtDateShort(w.date)}</span>
                <span class="wq-hist-val">${w.weight} kg</span>
                ${diffHtml}
            </div>`;
        }).join('');
    }
}

async function saveWeightQuick() {
    const val = document.getElementById('wqInput').value;
    if (!val) { flash('❌ Introdu greutatea', 'error'); return; }
    const res = await fetch('/api/gym/weight', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ date: TODAY, weight: parseFloat(val) })
    }).then(r => r.json());
    if (res.status === 'success') {
        flash(`✅ ${val} kg salvat!`);
        await window.refreshDayStatus?.();
    } else flash('❌ Eroare', 'error');
}

// ===== FOOD CHECK SIDEBAR =====
function renderFoodCheckSidebar(s) {
    if (s.food_check?.logged) {
        const btn = document.querySelector(`.fc-btn[data-level="${s.food_check.level}"]`);
        if (btn) btn.classList.add('selected');
        const st = document.getElementById('fcStatus');
        if (st) st.textContent = `✓ Bifat: ${FOOD_LABELS_SHORT[s.food_check.level]}`;
    }
}

async function quickFoodCheck(level, btn) {
    document.querySelectorAll('.fc-btn').forEach(b => b.classList.remove('selected'));
    btn.classList.add('selected');
    const res = await fetch('/api/gym/daily-check', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ date: TODAY, level })
    }).then(r => r.json());
    if (res.status === 'success') {
        const st = document.getElementById('fcStatus');
        if (st) st.textContent = `✓ Bifat: ${FOOD_LABELS_SHORT[level]}`;
        flash('✅ Alimentație salvată!');
        await window.refreshDayStatus?.();
    }
}

// ===== AGENDA =====
async function loadSchedule() {
    scheduleEvents = await fetch(`/api/day/schedule?date=${TODAY}`).then(r => r.json());
    renderAgenda();
}

function renderAgenda() {
    const el = document.getElementById('agendaList');
    if (!scheduleEvents.length) {
        el.innerHTML = '<div style="color:var(--text-faint);font-size:12px">Niciun eveniment</div>';
        return;
    }
    const sorted = [...scheduleEvents].sort((a, b) => (a.time || '').localeCompare(b.time || ''));
    el.innerHTML = sorted.map((ev, i) => `
        <div class="agenda-item">
            <span class="agenda-time">${ev.time || '—'}</span>
            <span class="agenda-text">${escHtml(ev.text)}</span>
            <button class="agenda-rm" onclick="removeAgendaItem(${i})">✕</button>
        </div>
    `).join('');
}

function addAgendaItem() {
    const time = document.getElementById('agendaTime').value;
    const text = document.getElementById('agendaText').value.trim();
    if (!text) { flash('❌ Scrie evenimentul', 'error'); return; }
    scheduleEvents.push({ time, text });
    document.getElementById('agendaText').value = '';
    document.getElementById('agendaTime').value = '';
    saveSchedule();
}

function removeAgendaItem(idx) {
    const sorted = [...scheduleEvents].sort((a, b) => (a.time || '').localeCompare(b.time || ''));
    const item = sorted[idx];
    scheduleEvents = scheduleEvents.filter(e => e !== item);
    saveSchedule();
}

async function saveSchedule() {
    await fetch('/api/day/schedule', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ date: TODAY, events: scheduleEvents })
    });
    renderAgenda();
}

// ===== AI BRIEFING =====
async function generateBriefing() {
    const btn = document.getElementById('briefingBtn');
    btn.textContent = '⏳ Generez...';
    btn.disabled = true;

    const emptyEl = document.getElementById('briefingEmpty');
    const contentEl = document.getElementById('briefingContent');
    emptyEl.innerHTML = `
        <div class="term-thinking" style="justify-content:center;padding:20px 0">
            <span style="font-size:22px">🤖</span>
            <span style="color:var(--text-muted)">Chronos analizează ziua ta...</span>
            <div class="term-dots"><span></span><span></span><span></span></div>
        </div>
    `;
    contentEl.style.display = 'none';

    try {
        const events = scheduleEvents.map(e => `${e.time ? e.time+': ' : ''}${e.text}`);
        // Include recent journal scores for context
        const lastScores = statusData?.journal?.last_scores;
        const res = await fetch('/api/day/briefing', {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ events, last_scores: lastScores })
        }).then(r => r.json());

        if (res.status === 'success') {
            renderBriefing(res.briefing);
            flash('✨ Briefing generat!');
            // Update checklist: briefing done
            await window.refreshDayStatus?.();
        } else {
            emptyEl.innerHTML = `<span style="color:var(--red)">❌ ${res.message}</span>`;
            flash('❌ Eroare briefing', 'error');
        }
    } catch {
        emptyEl.innerHTML = `<span style="color:var(--red)">❌ Eroare rețea</span>`;
        flash('❌ Eroare rețea', 'error');
    }

    btn.textContent = '🔄 Regenerează';
    btn.disabled = false;
}

function renderBriefing(b) {
    document.getElementById('briefingEmpty').style.display = 'none';
    const c = document.getElementById('briefingContent');
    c.style.display = 'block';

    document.getElementById('bGreeting').textContent = b.greeting || '';
    document.getElementById('bFocus').textContent = b.focus || '';
    document.getElementById('bFitness').textContent = b.fitness_tip || '—';
    document.getElementById('bMotivation').textContent = `"${b.motivation || ''}"`;

    const actions = b.top3_actions || [];
    document.getElementById('bActions').innerHTML = actions.map((a, i) => `
        <li class="action-item">
            <div class="action-num">${i+1}</div>
            <div class="action-text">${a}</div>
        </li>
    `).join('');

    const energy = b.energy_level || 'medium';
    document.getElementById('bEnergy').innerHTML = `
        <span class="energy-dot energy-${energy}"></span>
        <span>${b.mood_vibe || ''}</span>
    `;

    if (b.generated_at) {
        const t = new Date(b.generated_at);
        document.getElementById('bGenTime').textContent = `Generat la ${t.toLocaleTimeString('ro-RO', {hour:'2-digit',minute:'2-digit'})}`;
    }
}

init();
