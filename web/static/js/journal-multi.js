// ===== JURNAL PE MAI MULTE ZILE =====
// Scrii o dată despre o perioadă → /api/journal/split propune textul pe zile →
// îl verifici aici → /api/journal/bulk le salvează și le analizează în fundal.

(function () {
    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
    const DRAFT_KEY = 'chronos.journal.multiDraft';

    function setMode(mode) {
        document.querySelectorAll('.jm-mode-btn').forEach((b) => b.classList.toggle('active', b.dataset.jmode === mode));
        $('jmMulti').classList.toggle('hidden', mode !== 'multi');
        $('jmSingle').classList.toggle('hidden', mode === 'multi');
        if (mode === 'multi' && !$('jmFrom').value) setRange('thisweek');
    }

    function setRange(kind) {
        const today = new Date();
        const monday = addDays(today, -((today.getDay() + 6) % 7));
        let from = today, to = today;
        if (kind === 'thisweek') { from = monday; }
        if (kind === 'lastweek') { from = addDays(monday, -7); to = addDays(monday, -1); }
        if (kind === 'last3') { from = addDays(today, -2); }
        $('jmFrom').value = iso(from);
        $('jmTo').value = iso(to);
        document.querySelectorAll('[data-jrange]').forEach((b) => b.classList.toggle('active', b.dataset.jrange === kind));
    }

    function saveDraft() {
        try { localStorage.setItem(DRAFT_KEY, $('jmText').value); } catch (e) { /* fără storage */ }
    }

    async function split() {
        const text = $('jmText').value.trim();
        if (!text) { flash('Scrie ceva despre zilele astea.', 'error'); return; }
        const btn = $('jmSplitBtn');
        btn.disabled = true; btn.textContent = '✨ Împart pe zile…';
        try {
            const r = await fetch('/api/journal/split', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text, from: $('jmFrom').value, to: $('jmTo').value }),
            });
            const d = await r.json().catch(() => ({}));
            if (!r.ok) throw new Error(d.message || 'Nu merge.');
            renderDays(d.days);
        } catch (e) {
            flash('❌ ' + e.message, 'error');
        } finally {
            btn.disabled = false; btn.textContent = '✨ Împarte pe zile';
        }
    }

    function renderDays(days) {
        $('jmDays').innerHTML = days.map((d) => `
            <div class="jm-day ${d.text ? '' : 'empty'}" data-date="${esc(d.date)}">
                <div class="jm-day-label">${esc(d.label)}<span class="jm-state">${d.text ? '' : 'nimic — nu se salvează'}</span></div>
                <textarea class="form-input" placeholder="Nimic despre ziua asta. Scrie ceva dacă vrei.">${esc(d.text)}</textarea>
            </div>`).join('');
        $('jmPreview').classList.remove('hidden');
        updateSaveLabel();
        $('jmPreview').scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    function collected() {
        return [...document.querySelectorAll('#jmDays .jm-day')].map((el) => ({
            date: el.dataset.date, text: el.querySelector('textarea').value.trim(),
        })).filter((d) => d.text);
    }

    function updateSaveLabel() {
        const n = collected().length;
        $('jmSaveBtn').textContent = `💾 Salvează ${n} ${n === 1 ? 'zi' : 'zile'}`;
        $('jmSaveBtn').disabled = n === 0;
    }

    async function save() {
        const days = collected();
        if (!days.length) return;
        const btn = $('jmSaveBtn');
        btn.disabled = true; btn.textContent = 'Se salvează…';
        try {
            const r = await fetch('/api/journal/bulk', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ days, judge: $('jmJudge').checked }),
            });
            const d = await r.json().catch(() => ({}));
            if (!r.ok) throw new Error(d.message || 'Nu merge.');
            flash(`✅ Am salvat ${d.saved} zile` + ($('jmJudge').checked ? ' — le analizez în fundal.' : '.'));
            $('jmText').value = '';
            saveDraft();
            $('jmDays').innerHTML = '';
            $('jmPreview').classList.add('hidden');
            setMode('single');
            if (typeof loadLogsForMonth === 'function' && typeof currentMonth !== 'undefined') loadLogsForMonth(currentMonth);
        } catch (e) {
            flash('❌ ' + e.message, 'error');
            updateSaveLabel();
        }
    }

    function init() {
        if (!$('jmMulti')) return;
        document.querySelectorAll('.jm-mode-btn').forEach((b) => b.addEventListener('click', () => setMode(b.dataset.jmode)));
        document.querySelectorAll('[data-jrange]').forEach((b) => b.addEventListener('click', () => setRange(b.dataset.jrange)));
        ['jmFrom', 'jmTo'].forEach((id) => $(id).addEventListener('change', () =>
            document.querySelectorAll('[data-jrange]').forEach((b) => b.classList.remove('active'))));
        $('jmSplitBtn').addEventListener('click', split);
        $('jmSaveBtn').addEventListener('click', save);
        $('jmBackBtn').addEventListener('click', () => { $('jmPreview').classList.add('hidden'); $('jmText').focus(); });
        $('jmDays').addEventListener('input', (e) => {
            const day = e.target.closest('.jm-day');
            if (day) {
                const has = !!e.target.value.trim();
                day.classList.toggle('empty', !has);
                day.querySelector('.jm-state').textContent = has ? '' : 'nimic — nu se salvează';
            }
            updateSaveLabel();
        });
        // Ciorna rămâne dacă închizi pagina din greșeală (doar pe dispozitivul tău).
        $('jmText').addEventListener('input', saveDraft);
        try {
            const draft = localStorage.getItem(DRAFT_KEY);
            if (draft) $('jmText').value = draft;
        } catch (e) { /* fără storage */ }
    }

    document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', init) : init();
})();
