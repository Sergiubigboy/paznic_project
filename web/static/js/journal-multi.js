// ===== JURNAL PE O PERIOADĂ =====
// O intrare pentru mai multe zile (ex. o săptămână în care n-ai scris):
// rămâne un singur bloc, cu analiză și scoruri pentru toată perioada.
// /api/journal/period o salvează; analiza rulează în fundal.

(function () {
    const $ = (id) => document.getElementById(id);
    const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
    const DRAFT_KEY = 'chronos.journal.periodDraft';
    const LUNI = ['ian', 'feb', 'mar', 'apr', 'mai', 'iun', 'iul', 'aug', 'sep', 'oct', 'nov', 'dec'];

    function setMode(mode) {
        document.querySelectorAll('.jm-mode-btn').forEach((b) => b.classList.toggle('active', b.dataset.jmode === mode));
        $('jmMulti').classList.toggle('hidden', mode !== 'multi');
        $('jmSingle').classList.toggle('hidden', mode === 'multi');
        if (mode === 'multi' && !$('jmFrom').value) setRange('lastweek');
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
        updateLabel();
    }

    function updateLabel() {
        const a = new Date($('jmFrom').value + 'T12:00:00'), b = new Date($('jmTo').value + 'T12:00:00');
        if (isNaN(a) || isNaN(b)) { $('jmRangeLabel').textContent = ''; return; }
        const n = Math.round((b - a) / 864e5) + 1;
        $('jmRangeLabel').textContent = n > 0
            ? `${a.getDate()} ${LUNI[a.getMonth()]} – ${b.getDate()} ${LUNI[b.getMonth()]} · ${n} ${n === 1 ? 'zi' : 'zile'}`
            : 'Intervalul e invers.';
    }

    function saveDraft() {
        try { localStorage.setItem(DRAFT_KEY, $('jmText').value); } catch (e) { /* fără storage */ }
    }

    async function save() {
        const text = $('jmText').value.trim();
        if (!text) { flash('Scrie ceva despre perioada asta.', 'error'); return; }
        const btn = $('jmSaveBtn');
        btn.disabled = true; btn.textContent = 'Se salvează…';
        try {
            const r = await fetch('/api/journal/period', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ from: $('jmFrom').value, to: $('jmTo').value, text, judge: $('jmJudge').checked }),
            });
            const d = await r.json().catch(() => ({}));
            if (!r.ok) throw new Error(d.message || 'Nu merge.');
            flash(`✅ Perioada ${d.label} salvată` + ($('jmJudge').checked ? ' — o analizez în fundal (~30s).' : '.'));
            $('jmText').value = '';
            saveDraft();
            setMode('single');
            if (typeof loadLogsForMonth === 'function' && typeof currentMonth !== 'undefined') {
                loadLogsForMonth(currentMonth);
                // Analiza apare după ce termină AI-ul; reîncărcăm o dată, mai târziu.
                if ($('jmJudge').checked) setTimeout(() => loadLogsForMonth(currentMonth), 35000);
            }
        } catch (e) {
            flash('❌ ' + e.message, 'error');
        } finally {
            btn.disabled = false; btn.textContent = '💾 Salvează perioada';
        }
    }

    function init() {
        if (!$('jmMulti')) return;
        document.querySelectorAll('.jm-mode-btn').forEach((b) => b.addEventListener('click', () => setMode(b.dataset.jmode)));
        document.querySelectorAll('[data-jrange]').forEach((b) => b.addEventListener('click', () => setRange(b.dataset.jrange)));
        ['jmFrom', 'jmTo'].forEach((id) => $(id).addEventListener('change', () => {
            document.querySelectorAll('[data-jrange]').forEach((b) => b.classList.remove('active'));
            updateLabel();
        }));
        $('jmSaveBtn').addEventListener('click', save);
        // Ciorna rămâne dacă închizi pagina din greșeală (doar pe dispozitivul tău).
        $('jmText').addEventListener('input', saveDraft);
        try {
            const draft = localStorage.getItem(DRAFT_KEY);
            if (draft) $('jmText').value = draft;
        } catch (e) { /* fără storage */ }
    }

    document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', init) : init();
})();
