'use strict';
// ============================================================
//  HOME DASHBOARD — home.js
//  Chronos OS | Smart home page with alerts, stats, menu
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

function escHtml(s) {
    return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ---- INIT ----
document.addEventListener('DOMContentLoaded', () => {
    setGreeting();
    setDate();
    updateClock();
    setInterval(updateClock, 30000);

    loadAlerts();
    loadDayStatus();
    loadDailyTasks();
    loadMaintenance();
    loadElectronicsStat();
});
