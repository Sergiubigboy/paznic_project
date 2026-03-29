// ===== DAILY STATUS JS — populates status strip on every page =====

let _dayStatus = null;

async function loadDayStatus() {
    try {
        _dayStatus = await fetch('/api/day/status').then(r => r.json());
        renderStatusStrip(_dayStatus);
        document.dispatchEvent(new CustomEvent('dayStatusLoaded', { detail: _dayStatus }));
    } catch (e) {
        // Silence - status strip stays in loading state
    }
}

const FOOD_LABELS = {
    surplus_mare:  'Surplus+',
    mentinere:     'Menținere',
    deficit:       'Deficit',
    deficit_mare:  'Deficit-'
};
const FOOD_PILL_CLASS = {
    surplus_mare: 'food-surplus',
    mentinere:    'food-mentinere',
    deficit:      'food-deficit',
    deficit_mare: 'food-deficit-mare'
};

function fmtMinsShort(m) {
    if (!m) return '0h';
    const h = Math.floor(m / 60), min = m % 60;
    return min > 0 ? `${h}h${min}m` : `${h}h`;
}

function renderStatusStrip(s) {
    if (!s) return;

    // Weight pill
    const spW = document.getElementById('sp-weight');
    const spvW = document.getElementById('spv-weight');
    if (spvW) {
        if (s.weight?.logged) {
            spvW.textContent = s.weight.value + ' kg';
            if (s.weight.trend !== null && s.weight.trend !== undefined) {
                const t = s.weight.trend;
                spvW.textContent += ` (${t > 0 ? '+' : ''}${t})`;
            }
            spW?.classList.add('done');
        } else {
            const lw = s.last_weight_ever;
            spvW.textContent = lw ? `${lw}kg (ieri)` : 'Log ↗';
            spW?.classList.remove('done');
        }
    }

    // Screen time pill
    const spS = document.getElementById('sp-screen');
    const spvS = document.getElementById('spv-screen');
    if (spvS) {
        if (s.screen_time?.logged) {
            spvS.textContent = fmtMinsShort(s.screen_time.minutes);
            spS?.classList.add('done');
        } else {
            spvS.textContent = 'Log ↗';
            spS?.classList.remove('done');
        }
    }

    // Food pill
    const spF = document.getElementById('sp-food');
    const spvF = document.getElementById('spv-food');
    if (spvF) {
        if (s.food_check?.logged) {
            spvF.textContent = FOOD_LABELS[s.food_check.level] || s.food_check.level;
            spF?.classList.add('done');
            const cls = FOOD_PILL_CLASS[s.food_check.level];
            if (cls) spF?.classList.add(cls);
        } else {
            spvF.textContent = 'Bifează ↗';
            spF?.classList.remove('done');
        }
    }

    // Journal pill
    const spJ = document.getElementById('sp-journal');
    const spvJ = document.getElementById('spv-journal');
    if (spvJ) {
        const n = s.journal?.entries_today || 0;
        spvJ.textContent = n > 0 ? `${n} entr${n === 1 ? 'y' : 'ies'}` : 'Scrie ↗';
        if (n > 0) spJ?.classList.add('done');
        else spJ?.classList.remove('done');
    }

    // Progress bar
    const doneCount = [
        s.weight?.logged,
        s.screen_time?.logged,
        s.food_check?.logged,
        (s.journal?.entries_today || 0) > 0
    ].filter(Boolean).length;

    const fill = document.getElementById('dayProgressFill');
    const txt = document.getElementById('dayProgressTxt');
    if (fill) fill.style.width = (doneCount / 4 * 100) + '%';
    if (txt) {
        txt.textContent = `${doneCount}/4`;
        if (doneCount === 4) txt.style.color = 'var(--green)';
    }

    // Measurements due pill — add dynamically
    if (s.measurements_due && !document.getElementById('measDuePill')) {
        const strip = document.getElementById('statusStrip');
        const spacer = document.getElementById('statusPillSpacer') || strip?.querySelector('.status-pill-spacer');
        if (strip && spacer) {
            const pill = document.createElement('a');
            pill.href = '/gym#measurements';
            pill.className = 'status-pill meas-due';
            pill.id = 'measDuePill';
            pill.title = `${s.days_since_measurements || '?'} zile de la ultima măsurătoare`;
            pill.innerHTML = `<span class="sp-icon">⏰</span><span class="sp-val">${s.days_since_measurements || '?'}z</span>`;
            strip.insertBefore(pill, spacer);
        }
    }
}

// Load on page load + refresh every 90s
loadDayStatus();
setInterval(loadDayStatus, 90000);

// Public API for other scripts
window.getDayStatus = () => _dayStatus;
window.refreshDayStatus = loadDayStatus;
