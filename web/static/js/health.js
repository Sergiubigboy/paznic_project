// ===== HEALTH =====
// Programul zilnic (mese, suplimente, sală) + idei de mâncare. Logica de
// bază e în tools/health.py; aici doar afișăm și trimitem modificările.
// Tabul „Corp” e fosta pagină Fitness și are propriul gym.js.

(function () {
    const S = { data: null, editing: null };
    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const KIND_ORDER = ['meal', 'supplement', 'gym', 'routine'];
    const GROUP_LABEL = { mic_dejun: 'Mic dejun', scoala: 'Școală', pranz: 'Prânz', snack: 'Gustări', shake: 'Shake', cina: 'Cina' };

    async function api(url, body) {
        const opt = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
        const r = await fetch(url, opt);
        const d = await r.json().catch(() => ({}));
        if (!r.ok || d.status === 'error') throw new Error(d.message || `HTTP ${r.status}`);
        return d;
    }

    async function load() {
        try {
            S.data = await api('/api/health');
        } catch (e) {
            $('hlTimeline').innerHTML = `<div class="hl-empty">Nu pot încărca: ${esc(e.message)}</div>`;
            return;
        }
        renderConsistency();
        renderToday();
        renderPlan();
        renderIdeas();
    }

    // ─────────────── CONSECVENȚĂ ───────────────

    function renderConsistency() {
        const { stats, kinds } = S.data;
        const today = S.data.today.date;
        $('hlConsistency').innerHTML = ['meal', 'supplement', 'gym'].map((k) => {
            const pct = stats.pct[k];
            const cells = stats.days.map((d) => {
                let cls = '';
                const c = d.cats[k];
                if (d.vacation) cls = 'vac';
                else if (c && c[1]) cls = c[0] === c[1] ? 'full' : c[0] === 0 ? 'zero' : 'part';
                const tip = d.vacation ? `${d.date}: vacanță` : c ? `${d.date}: ${c[0]}/${c[1]}` : `${d.date}: fără date`;
                return `<span class="hl-cell ${cls} ${d.date === today ? 'today' : ''}" title="${esc(tip)}"></span>`;
            }).join('');
            return `
            <div class="hl-cons">
                <div class="hl-cons-top">
                    <span>${kinds[k].icon}</span><span class="hl-cons-label">${esc(kinds[k].label)}</span>
                    <span class="hl-cons-pct">${pct == null ? '—' : pct}<small>${pct == null ? '' : '%'}</small></span>
                </div>
                <div class="hl-cells">${cells}</div>
                <div class="hl-cons-foot"><span>acum 4 săpt.</span><span>azi</span></div>
            </div>`;
        }).join('');
    }

    // ─────────────── AZI ───────────────

    function statusChip(it) {
        if (it.status === 'missed') return '<span class="hl-st bad">✗ n-am</span>';
        if (it.status === 'done') return '<span class="hl-st ok">✓ făcut</span>';
        if (it.status === 'auto' || it.passed) return '<span class="hl-st auto">✓</span>';
        return '<span class="hl-st">urmează</span>';
    }

    function renderToday() {
        const t = S.data.today;
        const d = new Date(t.date + 'T12:00:00');
        $('hlTodayDate').textContent = d.toLocaleDateString('ro-RO', { weekday: 'long', day: 'numeric', month: 'long' })
            + (t.weekend ? ' · weekend' : ' · zi de școală');
        $('hlVacation').checked = !!S.data.vacation_now;
        $('hlBotWarn').classList.toggle('hidden', !!S.data.bot);

        if (S.data.vacation_now) {
            const v = S.data.plan.vacation || {};
            $('hlTimeline').innerHTML = `<div class="hl-vac-banner">🏖️ Ești în vacanță${v.to ? ' până pe ' + esc(v.to.split('-').reverse().slice(0, 2).join('.')) : ''}. Nu primești remindere și zilele astea nu contează la consecvență.</div>`;
            return;
        }
        if (!t.items.length) {
            $('hlTimeline').innerHTML = '<div class="hl-empty">Nimic programat azi. Adaugă iteme din tabul „Program”.</div>';
            return;
        }
        const icon = (k) => S.data.kinds[k]?.icon || '•';
        $('hlTimeline').innerHTML = t.items.map((it) => `
            <button type="button" class="hl-row ${it.status === 'missed' ? 'missed' : ''} ${!it.passed && !it.status ? 'future' : ''}" data-id="${esc(it.id)}">
                <span class="hl-time">${esc(it.time)}</span>
                <span class="hl-ic">${icon(it.kind)}</span>
                <span class="hl-main">
                    <div class="hl-title">${esc(it.title)}</div>
                    ${it.details ? `<div class="hl-det">${esc(it.details)}</div>` : ''}
                </span>
                ${statusChip(it)}
            </button>`).join('');
    }

    async function toggleStatus(id) {
        const it = S.data.today.items.find((x) => x.id === id);
        if (!it) return;
        const next = it.status === 'missed' ? 'done' : 'missed';
        try {
            await api('/api/health/status', { date: S.data.today.date, id, status: next });
            it.status = next;
            renderToday();
            load();          // reîmprospătează și procentele
        } catch (e) { flash(e.message, 'error'); }
    }

    // ─────────────── PROGRAM ───────────────

    function renderPlan() {
        const { plan, kinds } = S.data;
        $('hlRecapTime').value = plan.recap_time || '21:45';
        const byKind = {};
        plan.items.forEach((it) => (byKind[it.kind] = byKind[it.kind] || []).push(it));
        const sortKey = (it) => it.times.weekday || it.times.weekend || '99';
        $('hlPlanList').innerHTML = KIND_ORDER.filter((k) => byKind[k]).map((k) => `
            <div class="hl-group-label">${kinds[k].icon} ${esc(kinds[k].label)}</div>
            <div class="hl-plan-head"><span></span><span></span><span>L-V</span><span>Weekend</span></div>
            ${byKind[k].sort((a, b) => sortKey(a).localeCompare(sortKey(b))).map((it) => `
                <button type="button" class="hl-plan-item ${it.enabled ? '' : 'off'}" data-edit="${esc(it.id)}">
                    <span>${kinds[it.kind].icon}</span>
                    <span><div class="hl-title">${esc(it.title)}</div>${it.details ? `<div class="hl-det">${esc(it.details)}</div>` : ''}</span>
                    <span class="t ${it.times.weekday ? '' : 'none'}">${esc(it.times.weekday || '—')}</span>
                    <span class="t ${it.times.weekend ? '' : 'none'}">${esc(it.times.weekend || '—')}</span>
                </button>`).join('')}
        `).join('') || '<div class="hl-empty">Niciun item. Apasă „Item nou” sau rulează pe Pi <code>python -m tools.health seed</code>.</div>';
    }

    function openItem(it) {
        S.editing = it ? { ...it } : { kind: 'meal', title: '', details: '', times: { weekday: '', weekend: '' }, ideas: null, keywords: [], enabled: true };
        const e = S.editing;
        $('hlItemTitle').textContent = it ? 'Editează item' : 'Item nou';
        $('hlItName').value = e.title;
        $('hlItKind').innerHTML = KIND_ORDER.map((k) => `<option value="${k}" ${e.kind === k ? 'selected' : ''}>${S.data.kinds[k].icon} ${esc(S.data.kinds[k].label)}</option>`).join('');
        $('hlItWeekday').value = e.times.weekday || '';
        $('hlItWeekend').value = e.times.weekend || '';
        $('hlItDetails').value = e.details || '';
        const groups = Object.keys(S.data.ideas || {});
        $('hlItIdeas').innerHTML = '<option value="">— fără idei —</option>' +
            groups.map((g) => `<option value="${esc(g)}" ${e.ideas === g ? 'selected' : ''}>${esc(GROUP_LABEL[g] || g)}</option>`).join('');
        $('hlItKeywords').value = (e.keywords || []).join(', ');
        $('hlItEnabled').checked = e.enabled !== false;
        $('hlItDelete').classList.toggle('hidden', !it);
        $('hlItemModal').classList.add('open');
        if (!it) setTimeout(() => $('hlItName').focus(), 50);
    }

    function closeItem() { $('hlItemModal').classList.remove('open'); S.editing = null; }

    async function saveItem() {
        const e = S.editing;
        const body = {
            id: e.id, title: $('hlItName').value.trim(), kind: $('hlItKind').value,
            times: { weekday: $('hlItWeekday').value || null, weekend: $('hlItWeekend').value || null },
            details: $('hlItDetails').value.trim(), ideas: $('hlItIdeas').value || null,
            keywords: $('hlItKeywords').value.split(',').map((x) => x.trim()).filter(Boolean),
            enabled: $('hlItEnabled').checked,
        };
        try {
            await api('/api/health/item', body);
            flash('Salvat.');
            closeItem();
            load();
        } catch (err) { flash(err.message, 'error'); }
    }

    // ─────────────── IDEI ───────────────

    function renderIdeas() {
        const ideas = S.data.ideas || {};
        $('hlIdeas').innerHTML = Object.entries(ideas).map(([g, list]) => ideaGroup(g, list)).join('') ||
            '<div class="hl-empty">Nicio idee încă.</div>';
    }

    function ideaRow(it) {
        return `<div class="hl-idea">
            <input type="text" class="form-input" data-f="text" value="${esc(it.text)}" placeholder="ex: Ovăz cu lapte și banană">
            <input type="number" class="form-input" data-f="kcal" min="0" max="3000" value="${it.kcal || ''}" placeholder="kcal">
            <button type="button" class="hl-x" data-rm-idea title="Scoate">✕</button>
        </div>`;
    }

    function ideaGroup(g, list) {
        return `<div class="hl-idea-group" data-group="${esc(g)}">
            <div class="hl-idea-head">
                <input type="text" class="form-input" data-f="group" value="${esc(g)}" title="Numele grupului (îl alegi la fiecare masă)">
                <span class="form-hint" style="margin:0;white-space:nowrap">${esc(GROUP_LABEL[g] || '')}</span>
            </div>
            <div data-list>${list.map(ideaRow).join('')}</div>
            <button type="button" class="btn btn-sm btn-ghost" data-add-idea>＋ idee</button>
        </div>`;
    }

    function collectIdeas() {
        const out = {};
        document.querySelectorAll('.hl-idea-group').forEach((el) => {
            const g = el.querySelector('[data-f="group"]').value.trim();
            if (!g) return;
            out[g] = [...el.querySelectorAll('.hl-idea')].map((r) => ({
                text: r.querySelector('[data-f="text"]').value.trim(),
                kcal: parseInt(r.querySelector('[data-f="kcal"]').value, 10) || 0,
            })).filter((x) => x.text);
        });
        return out;
    }

    // ─────────────── TABURI ───────────────

    function showTab(name) {
        document.querySelectorAll('#hlTabs .seg-tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
        document.querySelectorAll('.hl-panel').forEach((p) => p.classList.toggle('active', p.id === `hlPanel-${name}`));
        try { localStorage.setItem('chronos.health.tab', name); } catch (e) { /* fără storage */ }
        // Graficele din Corp au fost desenate cât tabul era ascuns (lățime 0).
        if (name === 'body') window.dispatchEvent(new Event('resize'));
    }

    // ─────────────── EVENIMENTE ───────────────

    function wire() {
        $('hlTabs').addEventListener('click', (e) => {
            const b = e.target.closest('[data-tab]');
            if (b) showTab(b.dataset.tab);
        });
        $('hlTimeline').addEventListener('click', (e) => {
            const r = e.target.closest('[data-id]');
            if (r) toggleStatus(r.dataset.id);
        });
        $('hlVacation').addEventListener('change', async (e) => {
            try {
                const r = await api('/api/health/vacation', { on: e.target.checked });
                flash(r.message);
                load();
            } catch (err) { flash(err.message, 'error'); e.target.checked = !e.target.checked; }
        });

        $('hlRecapTime').addEventListener('change', async (e) => {
            try { await api('/api/health/recap-time', { time: e.target.value }); flash('Recap la ' + e.target.value); }
            catch (err) { flash(err.message, 'error'); }
        });
        $('hlAddItem').addEventListener('click', () => openItem(null));
        $('hlPlanList').addEventListener('click', (e) => {
            const b = e.target.closest('[data-edit]');
            if (b) openItem(S.data.plan.items.find((x) => x.id === b.dataset.edit));
        });
        $('hlItSave').addEventListener('click', saveItem);
        $('hlItCancel').addEventListener('click', closeItem);
        $('hlItemModal').addEventListener('click', (e) => { if (e.target.id === 'hlItemModal') closeItem(); });
        document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && S.editing) closeItem(); });
        $('hlItDelete').addEventListener('click', async () => {
            if (!S.editing?.id || !confirm(`Ștergi „${S.editing.title}”?`)) return;
            try { await api('/api/health/item/delete', { id: S.editing.id }); closeItem(); load(); }
            catch (err) { flash(err.message, 'error'); }
        });

        $('hlIdeas').addEventListener('click', (e) => {
            if (e.target.closest('[data-rm-idea]')) e.target.closest('.hl-idea').remove();
            const add = e.target.closest('[data-add-idea]');
            if (add) {
                const list = add.parentElement.querySelector('[data-list]');
                list.insertAdjacentHTML('beforeend', ideaRow({ text: '', kcal: '' }));
                list.lastElementChild.querySelector('input').focus();
            }
        });
        $('hlAddGroup').addEventListener('click', () => {
            const name = (prompt('Numele grupului (ex: post_sala):') || '').trim();
            if (name) $('hlIdeas').insertAdjacentHTML('beforeend', ideaGroup(name, [{ text: '', kcal: '' }]));
        });
        $('hlSaveIdeas').addEventListener('click', async () => {
            try {
                const r = await api('/api/health/ideas', { ideas: collectIdeas() });
                S.data.ideas = r.ideas;
                renderIdeas();
                flash('Ideile au fost salvate.');
            } catch (err) { flash(err.message, 'error'); }
        });
    }

    wire();
    let start = 'today';
    try { start = localStorage.getItem('chronos.health.tab') || 'today'; } catch (e) { /* fără storage */ }
    if (location.hash === '#measurements' || location.hash === '#corp') start = 'body';
    showTab(start);
    load();
})();
