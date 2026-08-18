/* ══════════════════════════════════════════════════════════════
   CHRONOS OS — Shell v3
   Rail, sheet mobil, command palette, dock-ul lui Chronos.
   Fără rAF permanent, fără canvas. Polling oprit când tabul e ascuns.
   ══════════════════════════════════════════════════════════════ */
'use strict';

window.Chronos = (function () {

    const $  = (s, r) => (r || document).querySelector(s);
    const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
    const html = document.documentElement;

    const store = {
        get(k, d) { try { const v = localStorage.getItem('chronos.' + k); return v === null ? d : v; } catch (e) { return d; } },
        set(k, v) { try { localStorage.setItem('chronos.' + k, v); } catch (e) {} },
        getJSON(k, d) { try { return JSON.parse(localStorage.getItem('chronos.' + k)) ?? d; } catch (e) { return d; } },
        setJSON(k, v) { try { localStorage.setItem('chronos.' + k, JSON.stringify(v)); } catch (e) {} }
    };

    const esc = s => String(s ?? '')
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');

    function toast(msg, type) {
        const el = $('#flashMsg');
        if (!el) return;
        el.textContent = msg;
        el.className = 'flash-msg show' + (type ? ' ' + type : '');
        clearTimeout(el._t);
        el._t = setTimeout(() => { el.className = 'flash-msg'; }, 3200);
    }

    /* ─────────────── RAIL / FX / SHEET ─────────────── */

    function initShell() {
        const railToggle = $('#railToggle');
        if (railToggle) {
            railToggle.addEventListener('click', () => {
                const mini = html.getAttribute('data-rail') === 'mini';
                html.setAttribute('data-rail', mini ? 'full' : 'mini');
                store.set('rail', mini ? 'full' : 'mini');
            });
        }

        const scrim = $('#sheetScrim'), sheet = $('#sheet');
        const closeSheet = () => { sheet?.classList.remove('open'); scrim?.classList.remove('open'); };
        $('#sheetOpen')?.addEventListener('click', () => { sheet?.classList.add('open'); scrim?.classList.add('open'); });
        scrim?.addEventListener('click', closeSheet);
        $$('#sheet a').forEach(a => a.addEventListener('click', closeSheet));

        $('#sheetFx')?.addEventListener('click', () => {
            const lite = html.getAttribute('data-fx') === 'lite';
            html.setAttribute('data-fx', lite ? 'full' : 'lite');
            store.set('fx', lite ? 'full' : 'lite');
            toast(lite ? '✨ Efecte complete pornite' : '🪶 Mod ușor — economisește resurse', 'info');
            closeSheet();
        });

        $('#sheetCmdk')?.addEventListener('click', () => { closeSheet(); openPalette(); });
        $('#railCmdk')?.addEventListener('click', openPalette);
        $('#topCmdk')?.addEventListener('click', openPalette);
        $('#railChronos')?.addEventListener('click', () => togglePanel(true));
    }

    /* ─────────────── COMMAND PALETTE ─────────────── */

    const NAV_ITEMS = [
        { icon: '🏠', label: 'Acasă',           sub: 'panoul principal',      href: '/' },
        { icon: '📘', label: 'Jurnal',          sub: 'intrări zilnice',       href: '/journal' },
        { icon: '🎯', label: 'Taskuri',         sub: 'targeturi & remindere', href: '/targets' },
        { icon: '💪', label: 'Fitness',         sub: 'greutate & măsurători', href: '/gym' },
        { icon: '💰', label: 'Bani',            sub: 'conturi & investiții',  href: '/bani' },
        { icon: '⚡', label: 'Electronics Lab', sub: 'componente & proiecte', href: '/electronics' },
        { icon: '⌨️', label: 'Terminal',        sub: 'comenzi brute',         href: '/terminal' },
        { icon: '⚙️', label: 'Setări',          sub: 'fișiere & config',      href: '/settings' }
    ];

    // Acțiuni globale + cele înregistrate de pagina curentă
    let pageActions = [];
    function registerActions(list) { pageActions = list || []; }

    function globalActions() {
        return [
            { icon: '🧠', label: 'Vorbește cu Chronos', sub: 'Ctrl+J',  run: () => togglePanel(true) },
            { icon: '💸', label: 'Investiție nouă',      sub: 'Bani',    href: '/bani#invest' },
            { icon: '📥', label: 'Adaugă bani în cont',  sub: 'Bani',    href: '/bani' },
            { icon: '✏️', label: 'Scrie în jurnal',      sub: 'Jurnal',  href: '/journal' },
            { icon: '🎯', label: 'Target nou',           sub: 'Taskuri', href: '/targets' },
            { icon: html.getAttribute('data-fx') === 'lite' ? '✨' : '🪶',
              label: html.getAttribute('data-fx') === 'lite' ? 'Pornește efectele complete' : 'Mod ușor (economie resurse)',
              sub: 'aspect',
              run: () => {
                  const lite = html.getAttribute('data-fx') === 'lite';
                  html.setAttribute('data-fx', lite ? 'full' : 'lite');
                  store.set('fx', lite ? 'full' : 'lite');
                  toast(lite ? '✨ Efecte complete' : '🪶 Mod ușor activat', 'info');
              } }
        ];
    }

    let cmdkOpen = false, cmdkIdx = 0, cmdkRows = [];

    function openPalette() {
        const scrim = $('#cmdkScrim'), box = $('#cmdk'), inp = $('#cmdkInput');
        if (!box) return;
        cmdkOpen = true;
        scrim.classList.add('open'); box.classList.add('open');
        inp.value = ''; renderPalette('');
        setTimeout(() => inp.focus(), 40);
    }
    function closePalette() {
        cmdkOpen = false;
        $('#cmdkScrim')?.classList.remove('open');
        $('#cmdk')?.classList.remove('open');
    }

    function renderPalette(q) {
        const list = $('#cmdkList');
        if (!list) return;
        const query = (q || '').trim().toLowerCase();
        const match = it => !query || (it.label + ' ' + (it.sub || '')).toLowerCase().includes(query);

        const groups = [
            { title: 'Pe pagina asta', items: pageActions.filter(match) },
            { title: 'Acțiuni',        items: globalActions().filter(match) },
            { title: 'Navigare',       items: NAV_ITEMS.filter(match) }
        ].filter(g => g.items.length);

        cmdkRows = [];
        let out = '';
        groups.forEach(g => {
            out += `<div class="cmdk-group">${esc(g.title)}</div>`;
            g.items.forEach(it => {
                const i = cmdkRows.length;
                cmdkRows.push(it);
                out += `<div class="cmdk-item" data-i="${i}" role="option">
                    <span class="ck-ico">${it.icon || '•'}</span>
                    <span>${esc(it.label)}</span>
                    ${it.sub ? `<span class="ck-sub">${esc(it.sub)}</span>` : ''}
                </div>`;
            });
        });

        if (query) {
            const i = cmdkRows.length;
            cmdkRows.push({ icon: '🧠', label: `Întreabă-l pe Chronos: „${q}”`, ask: q });
            out += `<div class="cmdk-group">Chronos</div>
                <div class="cmdk-item" data-i="${i}" role="option">
                    <span class="ck-ico">🧠</span><span>Întreabă-l pe Chronos: „${esc(q)}”</span>
                    <span class="ck-sub">Enter</span>
                </div>`;
        }

        list.innerHTML = out || '<div class="cmdk-empty">Nimic găsit.</div>';
        cmdkIdx = 0;
        highlight();

        $$('.cmdk-item', list).forEach(el => {
            el.addEventListener('mouseenter', () => { cmdkIdx = +el.dataset.i; highlight(); });
            el.addEventListener('click', () => runRow(cmdkRows[+el.dataset.i]));
        });
    }

    function highlight() {
        $$('.cmdk-item').forEach(el => el.setAttribute('aria-selected', (+el.dataset.i === cmdkIdx) ? 'true' : 'false'));
        $(`.cmdk-item[data-i="${cmdkIdx}"]`)?.scrollIntoView({ block: 'nearest' });
    }

    function runRow(row) {
        if (!row) return;
        closePalette();
        if (row.ask)   { togglePanel(true); setTimeout(() => send(row.ask), 220); return; }
        if (row.run)   { row.run(); return; }
        if (row.href)  { location.href = row.href; }
    }

    function initPalette() {
        const inp = $('#cmdkInput');
        $('#cmdkScrim')?.addEventListener('click', closePalette);
        inp?.addEventListener('input', e => renderPalette(e.target.value));
        inp?.addEventListener('keydown', e => {
            if (e.key === 'ArrowDown') { e.preventDefault(); cmdkIdx = Math.min(cmdkIdx + 1, cmdkRows.length - 1); highlight(); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); cmdkIdx = Math.max(cmdkIdx - 1, 0); highlight(); }
            else if (e.key === 'Enter') { e.preventDefault(); runRow(cmdkRows[cmdkIdx]); }
            else if (e.key === 'Escape') { closePalette(); }
        });

        document.addEventListener('keydown', e => {
            const k = (e.key || '').toLowerCase();
            if ((e.ctrlKey || e.metaKey) && k === 'k') { e.preventDefault(); cmdkOpen ? closePalette() : openPalette(); }
            else if ((e.ctrlKey || e.metaKey) && k === 'j') { e.preventDefault(); togglePanel(); }
            else if (e.key === 'Escape') {
                if (cmdkOpen) closePalette();
                else if (panelOpen()) togglePanel(false);
            }
        });
    }

    /* ─────────────── TEMA VIZUALĂ (accent --primary) ───────────────
       Trei surse: manual / culoarea camerei (WLED) / starea lui Chronos.
       Modul „mood" nu are poll propriu — se sincronizează pe refreshState()
       de mai jos (90s), ca să nu dublăm cereri. Modul „wled" are poll propriu,
       mai rapid, pornit doar cât timp e activ și tabul e vizibil. */

    const Theme = (function () {
        const PRESETS = [
            { name: 'Violet',      hex: '#8b7aff' },
            { name: 'Turcoaz',     hex: '#2de2b0' },
            { name: 'Roz',         hex: '#ff6bcb' },
            { name: 'Albastru',    hex: '#4ea8ff' },
            { name: 'Chihlimbar',  hex: '#ffb020' },
            { name: 'Roșu',        hex: '#ff4d6d' },
            { name: 'Verde',       hex: '#34d399' },
            { name: 'Auriu',       hex: '#e8c44d' }
        ];

        let current = { mode: store.get('theme.mode', 'manual'), color: '#8b7aff', resolved: '#8b7aff', live: true };
        let wledTimer = null;

        function hexToRgb(hex) {
            const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || '');
            return m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)] : null;
        }

        function apply(hex, persist = true) {
            const rgb = hexToRgb(hex);
            if (!rgb) return;
            const [r, g, b] = rgb;
            html.style.setProperty('--primary', hex);
            html.style.setProperty('--primary-rgb', `${r},${g},${b}`);
            html.style.setProperty('--primary-dim', `rgba(${r},${g},${b},.10)`);
            html.style.setProperty('--primary-soft', `rgba(${r},${g},${b},.14)`);
            html.style.setProperty('--primary-glow', `rgba(${r},${g},${b},.40)`);
            html.style.setProperty('--border-glow', `rgba(${r},${g},${b},.32)`);
            if (persist) store.set('theme.resolved', hex);
        }

        function scheduleWledPoll() {
            clearInterval(wledTimer);
            if (current.mode === 'wled') {
                wledTimer = setInterval(() => { if (!document.hidden) refresh(); }, 25000);
            }
        }

        async function refresh() {
            try {
                const d = await fetch('/api/theme').then(r => r.json());
                current = d;
                store.set('theme.mode', d.mode);
                apply(d.resolved);
                scheduleWledPoll();
                document.dispatchEvent(new CustomEvent('chronos:theme', { detail: d }));
                return d;
            } catch (e) { return current; }
        }

        async function push(body) {
            try {
                const d = await fetch('/api/theme', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                }).then(r => r.json());
                if (d.status === 'success') {
                    current = d;
                    store.set('theme.mode', d.mode);
                    apply(d.resolved);
                    scheduleWledPoll();
                    document.dispatchEvent(new CustomEvent('chronos:theme', { detail: d }));
                }
                return d;
            } catch (e) { return { status: 'error', message: 'Eroare de rețea' }; }
        }

        // Apelat din applyMood() — dacă tema urmărește starea lui Chronos,
        // aceeași cerere (90s) o hrănește și pe ea, fără poll separat.
        function syncFromMood(moodHex) {
            if (current.mode === 'mood' && moodHex) apply(moodHex);
        }

        return {
            PRESETS,
            init: refresh,
            refresh,
            setMode: mode => push({ mode }),
            setColor: hex => push({ mode: 'manual', color: hex }),
            preview: hex => apply(hex, false),
            syncFromMood,
            get: () => current
        };
    })();

    /* ─────────────── CHRONOS: STARE & CHAT ─────────────── */

    let stateTimer = null;
    let busy = false;

    function applyMood(st) {
        if (!st) return;
        html.style.setProperty('--mood', st.color || '#8b7aff');
        html.style.setProperty('--mood-rgb', st.color_rgb || '139,122,255');
        html.style.setProperty('--mood-bpm', (st.pulse || 5.5) + 's');
        const mt = $('#cpMoodText'); if (mt) mt.textContent = st.mood_label || '—';
        const rm = $('#railMood'); if (rm) rm.textContent = st.mood_short || 'sistem activ';
        const dock = $('#chronosDock'); if (dock) dock.title = 'Chronos — ' + (st.mood_label || '');

        const v = st.values || {};
        const set = (id, val) => { const el = $(id); if (el) el.style.width = Math.max(0, Math.min(100, val || 0)) + '%'; };
        set('#vNerv', v.nervozitate); set('#vBuc', v.bucurie);
        set('#vPlic', v.plictiseala); set('#vAfec', v.afectiune);

        // Chronos se plictisește → punct roșu pe dock
        const badge = $('#chronosBadge');
        if (badge) badge.classList.toggle('hidden', !st.wants_attention);

        // Dacă tema urmărește starea lui, aceeași culoare devine accentul UI
        Theme.syncFromMood(st.color);
    }

    async function refreshState() {
        if (document.hidden) return;
        try {
            const r = await fetch('/api/chronos/state');
            if (r.ok) applyMood(await r.json());
        } catch (e) { /* offline: păstrăm ultima stare */ }
    }

    function panelOpen() { return $('#chronosPanel')?.classList.contains('open'); }

    function togglePanel(force) {
        const p = $('#chronosPanel');
        if (!p) return;
        const open = (force === undefined) ? !panelOpen() : !!force;
        p.classList.toggle('open', open);
        store.set('panel', open ? '1' : '0');
        if (open) {
            $('#chronosBadge')?.classList.add('hidden');
            setTimeout(() => $('#cpInput')?.focus(), 120);
            scrollLog();
        }
    }

    function scrollLog() { const l = $('#cpLog'); if (l) l.scrollTop = l.scrollHeight; }

    function pushMsg(role, text, save = true) {
        const log = $('#cpLog'); if (!log) return;
        const el = document.createElement('div');
        el.className = 'cp-msg ' + role;
        el.textContent = text;
        log.appendChild(el);
        scrollLog();
        if (save) {
            const h = store.getJSON('chat', []);
            h.push({ r: role, t: text });
            store.setJSON('chat', h.slice(-40));
        }
    }

    function pushAction(text) {
        const log = $('#cpLog'); if (!log) return;
        const el = document.createElement('div');
        el.className = 'cp-act';
        el.textContent = '→ ' + text;
        log.appendChild(el);
        scrollLog();
    }

    function restoreChat() {
        const log = $('#cpLog'); if (!log) return;
        const h = store.getJSON('chat', []);
        if (!h.length) {
            pushMsg('sys', 'Chronos e aici. Scrie-i orice — comenzi, întrebări, ce ai pe suflet.', false);
            return;
        }
        h.forEach(m => pushMsg(m.r, m.t, false));
        scrollLog();
    }

    async function send(text) {
        text = (text || '').trim();
        if (!text || busy) return;
        busy = true;

        pushMsg('me', text);
        $('#cpInput').value = '';
        $('#cpInput').style.height = '';
        $('#cpSend').disabled = true;
        $('#chronosDock')?.classList.add('thinking');

        const log = $('#cpLog');
        const typing = document.createElement('div');
        typing.className = 'cp-typing';
        typing.innerHTML = '<i></i><i></i><i></i>';
        log.appendChild(typing); scrollLog();

        try {
            const ctrl = new AbortController();
            const to = setTimeout(() => ctrl.abort(), 60000);
            const res = await fetch('/api/chronos/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text }),
                signal: ctrl.signal
            });
            clearTimeout(to);
            const data = await res.json();
            typing.remove();

            if (data.status === 'error') {
                pushMsg('err', '⚠ ' + (data.message || 'Ceva n-a mers.'));
            } else {
                if (data.reply) pushMsg('ai', data.reply);
                (data.actions || []).forEach(a => pushAction(typeof a === 'string' ? a : (a.text || '')));
                if (!data.reply && !(data.actions || []).length) pushMsg('ai', 'Gata.');
                if (data.state) applyMood(data.state);
            }
        } catch (e) {
            typing.remove();
            pushMsg('err', e.name === 'AbortError'
                ? '⚠ A durat prea mult — Chronos n-a răspuns.'
                : '⚠ Nu ajung la Chronos. Rulează aplicația principală?');
        }

        busy = false;
        $('#cpSend').disabled = false;
        $('#chronosDock')?.classList.remove('thinking');
        $('#cpInput')?.focus();
    }

    function initChronos() {
        $('#chronosDock')?.addEventListener('click', () => togglePanel());
        $('#cpClose')?.addEventListener('click', () => togglePanel(false));
        $('#cpVitalsToggle')?.addEventListener('click', () => $('#cpVitals')?.classList.toggle('hidden'));
        $('#cpClear')?.addEventListener('click', () => {
            store.setJSON('chat', []);
            $('#cpLog').innerHTML = '';
            pushMsg('sys', 'Conversație golită.', false);
        });
        $('#cpSend')?.addEventListener('click', () => send($('#cpInput').value));

        const inp = $('#cpInput');
        inp?.addEventListener('keydown', e => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(inp.value); }
        });
        inp?.addEventListener('input', () => {
            inp.style.height = 'auto';
            inp.style.height = Math.min(inp.scrollHeight, 110) + 'px';
        });

        $$('#cpChips .cp-chip').forEach(c =>
            c.addEventListener('click', () => send(c.dataset.cmd)));

        restoreChat();
        if (store.get('panel') === '1' && window.innerWidth > 900) togglePanel(true);

        refreshState();
        stateTimer = setInterval(refreshState, 90000);
        document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshState(); });
    }

    /* ─────────────── BADGE-URI NAVIGAȚIE ─────────────── */

    async function loadBadges() {
        try {
            const r = await fetch('/api/home/alerts');
            const d = await r.json();
            const n = (d.alerts || []).length;
            const b = $('#navBadgeTargets');
            if (b) { b.textContent = n; b.classList.toggle('hidden', n === 0); }
            $('#bbDot')?.classList.toggle('hidden', n === 0);
        } catch (e) {}
    }

    /* ─────────────── REVEAL LA SCROLL (ieftin) ─────────────── */

    const REVEAL_SEL = [
        '.card', '.surface-card', '.sidebar-card', '.target-card', '.gym-card',
        '.hub-card', '.stat-card', '.fin-card', '.inv-card', '.sale-card',
        '.elab-proj-card', '.fin-acc-card', '.fin-debt-card', '.entry-card',
        '.journal-input-card', '.day-group', '.checklist-card', '.dt-task-card'
    ].join(',');

    let revealObs = null;
    function initReveal() {
        if (!('IntersectionObserver' in window)) return;
        if (!revealObs) {
            revealObs = new IntersectionObserver(entries => {
                entries.forEach(en => {
                    if (!en.isIntersecting) return;
                    const el = en.target;
                    const d = +(el.dataset.animDelay || 0);
                    if (d) setTimeout(() => el.classList.add('anim-visible'), d);
                    else el.classList.add('anim-visible');
                    revealObs.unobserve(el);
                });
            }, { threshold: 0.05, rootMargin: '0px 0px -30px 0px' });
        }
        $$(REVEAL_SEL).forEach((el, i) => {
            if (el.classList.contains('anim-item')) return;
            el.classList.add('anim-item');
            el.dataset.animDelay = Math.min(i * 45, 320);
            revealObs.observe(el);
        });
    }

    /* ─────────────── COUNTERS ─────────────── */

    function initCounters() {
        if (!('IntersectionObserver' in window)) return;
        const obs = new IntersectionObserver(entries => {
            entries.forEach(en => {
                if (!en.isIntersecting) return;
                const el = en.target;
                obs.unobserve(el);
                const target = parseFloat(el.dataset.counter);
                if (isNaN(target)) return;
                const dec = target % 1 !== 0;
                const t0 = performance.now(), dur = 900;
                const step = now => {
                    const p = Math.min((now - t0) / dur, 1);
                    const v = target * (1 - Math.pow(1 - p, 3));
                    el.textContent = dec ? v.toFixed(1) : Math.round(v);
                    if (p < 1) requestAnimationFrame(step);
                };
                requestAnimationFrame(step);
            });
        }, { threshold: 0.4 });
        $$('[data-counter]').forEach(el => obs.observe(el));
    }

    /* ─────────────── BOOT ─────────────── */

    function boot() {
        initShell();
        initPalette();
        initChronos();
        initReveal();
        initCounters();
        loadBadges();
        Theme.init();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
    else boot();

    return {
        toast, esc, store,
        openPalette, closePalette,
        openChronos: () => togglePanel(true),
        ask: t => { togglePanel(true); setTimeout(() => send(t), 200); },
        registerActions,
        refreshReveal: initReveal,
        refreshCounters: initCounters,
        refreshState,
        theme: Theme
    };
})();

/* Compatibilitate cu paginile vechi */
window.chronosAnimations = {
    refreshScroll:  () => window.Chronos.refreshReveal(),
    refreshCounters: () => window.Chronos.refreshCounters(),
    refreshTilt:    () => {},
    refreshStagger: () => window.Chronos.refreshReveal(),
    refreshParticles: () => {}
};
