/* ═══════════════════════════════════════════════════════════════
   Chronos — ecranul de pe Pi (/screen)

   Un terminal: un bloc de stare care se rescrie pe loc, un log care
   curge, un prompt. Controalele și evenimentele vin ca pop-up-uri.

   Bucle de interogare:
     • /api/screen/state     ~1.5s  vocea, alarma, timerele, starea lui Chronos
     • /api/screen/music     4s     (15s cu ecranul stins)
     • /api/screen/overview  60s    vremea, ziua, remindere, scene
     • /api/screen/lights    30s
   Plus un ceas local la fiecare secundă pentru tot ce curge.
   ═══════════════════════════════════════════════════════════════ */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const qsa = (sel, root) => Array.from((root || document).querySelectorAll(sel));
    const pad = (n) => String(n).padStart(2, '0');
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const low = (s) => String(s || '').toLowerCase();
    const hhmm = (d) => pad(d.getHours()) + ':' + pad(d.getMinutes());

    if (new URLSearchParams(location.search).has('kiosk')) document.body.classList.add('kiosk');

    // Preferințele și logul rămân în browserul de pe Pi; pagina merge și fără ele.
    const pref = {
        get(k, d) { try { const v = localStorage.getItem('screen.' + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
        set(k, v) { try { localStorage.setItem('screen.' + k, JSON.stringify(v)); } catch (e) { /* fără stocare */ } },
    };

    const IDLE_CLOSE_MS = 60 * 1000;     // pop-up-ul uitat deschis se închide singur
    const IDLE_SLEEP_MS = 2 * 60 * 1000; // noaptea, după atâta timp fără atingere, ecranul se stinge
    const ARM_MS = 3000;                 // fereastra pentru a doua atingere (bifări)
    const LOG_MAX = 150;

    const S = {
        boot: null,
        fails: 0,
        offline: false,
        voice: null,
        voiceAvailable: false,
        voicePending: 0,
        ringing: false,
        ringLabel: '',
        timers: [],
        overview: null,
        music: null,
        musicAt: 0,
        musicBusy: 0,
        lights: null,
        pop: null,          // pop-up-ul deschis de om (muzică, lumini…)
        popAt: 0,
        console: null,      // ce e selectat pe TFT-ul consolei fizice
        sleeping: false,
        sleepAuto: false,
        lastTouch: Date.now(),
        autoSleep: pref.get('autoSleep', true),
        backlight: null,
        blBeforeSleep: null,
        // ce s-a scris deja în log, ca reîncărcarea să nu dubleze
        seenTurn: pref.get('seenTurn', 0),
        seenTrack: pref.get('seenTrack', ''),
        seenNow: pref.get('seenNow', ''),
    };

    // ── API ─────────────────────────────────────────────────────
    async function api(path, body) {
        const opt = body === undefined
            ? { cache: 'no-store' }
            : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
        const r = await fetch(path, opt);
        let data = {};
        try { data = await r.json(); } catch (e) { /* răspuns fără JSON */ }
        data._ok = r.ok;
        if (!r.ok && !data.message) data.message = 'eroare ' + r.status;
        return data;
    }

    /** Acțiune cu urmă în log: `ok …` la reușită, `err …` la eșec. */
    async function act(path, body, okMsg) {
        try {
            const d = await api(path, body);
            if (!d._ok || d.status === 'error') { log('err', low(d.message || 'n-a mers'), true); return null; }
            if (okMsg) log('ok', typeof okMsg === 'function' ? okMsg(d) : okMsg);
            return d;
        } catch (e) {
            log('err', 'chronos nu răspunde', true);
            return null;
        }
    }

    function loop(fn, msFn) {
        const run = async () => {
            try { await fn(); } catch (e) { /* următoarea tură reîncearcă */ }
            setTimeout(run, typeof msFn === 'function' ? msFn() : msFn);
        };
        run();
    }

    // ── Logul ───────────────────────────────────────────────────
    let lines = pref.get('log', []);

    function lnHtml(l) {
        return '<div class="ln ' + esc(l.c || '') + '"><span class="t">' + esc(l.t) + '</span><span class="k">' +
            esc(l.k) + '</span><span class="x">' + esc(l.x) + '</span></div>';
    }

    function renderLog() {
        const el = $('log');
        el.innerHTML = lines.map(lnHtml).join('');
        el.scrollTop = el.scrollHeight;
    }

    /** O linie nouă în log; `popup` o arată și ca notificare scurtă. */
    function log(kind, text, popup) {
        const cls = { err: 'err', tu: 'tu', '!!': 'alarm' }[kind] || '';
        const l = { t: hhmm(new Date()), k: kind, x: text, c: cls };
        lines.push(l);
        if (lines.length > LOG_MAX) lines = lines.slice(-LOG_MAX);
        pref.set('log', lines);
        const el = $('log');
        el.insertAdjacentHTML('beforeend', lnHtml(l));
        while (el.children.length > LOG_MAX) el.removeChild(el.firstChild);
        el.scrollTop = el.scrollHeight;
        // La pornire logul se umple dintr-odată — pop-up-uri doar pentru ce se întâmplă după.
        if (popup && (S.ready || kind === 'err')) note(kind + '  ' + text, kind === 'err');
    }

    function note(text, err) {
        const n = document.createElement('div');
        n.className = 'note' + (err ? ' err' : '');
        n.textContent = text;
        $('notes').appendChild(n);
        setTimeout(() => n.remove(), 3500);
        while ($('notes').children.length > 3) $('notes').firstChild.remove();
    }

    /** Prima atingere „armează", a doua (în ARM_MS) confirmă — pe un
     *  touchscreen o atingere din greșeală nu are voie să bifeze ceva. */
    function armed(el) {
        if (el.classList.contains('arm')) { el.classList.remove('arm'); return true; }
        qsa('.arm').forEach((x) => x.classList.remove('arm'));
        el.classList.add('arm');
        setTimeout(() => el.classList.remove('arm'), ARM_MS);
        return false;
    }

    const bar = (frac, n) => {
        const k = Math.max(0, Math.min(n, Math.round(frac * n)));
        return '[' + '#'.repeat(k) + '-'.repeat(n - k) + ']';
    };

    // ── Pop-up-uri ──────────────────────────────────────────────
    // Alarma bate vocea, vocea bate ce a deschis omul. Când alarma/vocea
    // se termină, revine pop-up-ul de dedesubt (sau niciunul).
    function renderPops() {
        let top = null;
        if (S.offline) top = 'offline';
        else if (S.ringing) top = 'alarm';
        else if (voiceShown()) top = 'voice';
        // Consola fizică bate pop-up-ul atins pe ecran doar dacă e mai nouă decât el.
        else if (S.console && (!S.pop || S.console.ts * 1000 > S.popAt)) top = 'console';
        else top = S.pop;
        qsa('.pop').forEach((p) => p.classList.toggle('show', p.dataset.pop === top));
    }

    function openPop(name) {
        S.pop = name;
        S.popAt = Date.now();
        if (name === 'music') pollMusic();
        if (name === 'lights') pollLights();
        renderAll();
        renderPops();
    }

    function closePop() {
        S.pop = null;
        renderPops();
    }

    // ── Ceasul ──────────────────────────────────────────────────
    const ZILE = ['duminică', 'luni', 'marți', 'miercuri', 'joi', 'vineri', 'sâmbătă'];
    const LUNI = ['ianuarie', 'februarie', 'martie', 'aprilie', 'mai', 'iunie', 'iulie',
                  'august', 'septembrie', 'octombrie', 'noiembrie', 'decembrie'];

    function tick() {
        const d = new Date();
        $('clock').textContent = hhmm(d) + ':' + pad(d.getSeconds());
        $('sClock').textContent = hhmm(d);
        $('date').textContent = ZILE[d.getDay()] + ' ' + d.getDate() + ' ' + LUNI[d.getMonth()] + ' ' + d.getFullYear();
        renderTimers();
        renderDayStatus();
        renderMusic();
        idleCheck();
    }

    // ── Starea rapidă: voce, alarmă, timere, dispoziție ─────────
    async function pollState() {
        let d;
        try {
            d = await api('/api/screen/state');
            if (!d._ok) throw new Error(d.message);
        } catch (e) {
            S.fails += 1;
            if (S.fails >= 3 && !S.offline) { S.offline = true; log('err', 'conexiunea cu chronos a căzut'); renderPops(); }
            return;
        }
        S.fails = 0;
        if (S.offline) { S.offline = false; log('sys', 'chronos e înapoi'); renderPops(); }

        // Chronos a repornit (poate cu cod nou după un git pull) → luăm și CSS/JS noi.
        if (S.boot && d.boot !== S.boot) { log('sys', 'chronos a repornit · reîncarc'); location.reload(); return; }
        S.boot = d.boot;

        if (d.mood) {
            $('mood').textContent = low(d.mood.mood_short || d.mood.mood || '');
            if (d.mood.color) document.documentElement.style.setProperty('--mood', d.mood.color);
        }
        applyConsole(d.console);
        applyTimers(d.timers || []);
        applyAlarm(d.ringing);
        applyVoice(d.voice);
    }

    function stateDelay() {
        if (S.fails) return 3000;
        const v = S.voice && S.voice.state;
        if (S.voicePending || S.ringing || v === 'listening' || v === 'speaking') return 600;
        return S.sleeping ? 2500 : 1500;
    }

    // ── Vocea ───────────────────────────────────────────────────
    const voiceActive = () => !!S.voice && (S.voice.state === 'listening' || S.voice.state === 'speaking');
    const voiceShown = () => voiceActive() || (S.voicePending && Date.now() - S.voicePending < 9000);

    function applyVoice(v) {
        S.voice = v;
        S.voiceAvailable = !!(v && v.available);
        const prompt = $('prompt');
        prompt.classList.toggle('off', !S.voiceAvailable);

        // Fiecare tur încheiat ajunge în log: tu > …, ch > …
        const last = v && v.last;
        if (last && last.ts > S.seenTurn) {
            S.seenTurn = last.ts;
            pref.set('seenTurn', last.ts);
            if (last.user) log('tu', last.user);
            if (last.ai) log('ch', last.ai);
        }

        if (voiceActive()) {
            S.voicePending = 0;
            wake();
            const speak = v.state === 'speaking';
            $('vState').className = speak ? 'speak' : 'listen';
            $('vState').textContent = speak ? '◆ chronos vorbește' : '● ascult';
            $('vUser').textContent = v.user ? 'tu> ' + v.user : '';
            $('vAi').textContent = v.ai ? 'ch> ' + v.ai : '';
            $('promptTxt').textContent = speak ? 'chronos vorbește' : 'ascult';
        } else if (S.voicePending && Date.now() - S.voicePending >= 9000) {
            S.voicePending = 0;
            log('err', 'sesiunea vocală n-a pornit', true);
        }
        if (!voiceActive()) {
            $('promptTxt').textContent = S.voiceAvailable ? 'atinge pentru voce' : 'voce oprită (fără microfon sau gemini)';
        }
        renderPops();
    }

    async function startVoice() {
        if (!S.voiceAvailable) { log('err', 'vocea nu e pornită pe pi', true); return; }
        if (voiceActive()) return;
        S.voicePending = Date.now();
        $('vState').className = 'listen';
        $('vState').textContent = '● mă conectez';
        $('vUser').textContent = '';
        $('vAi').textContent = '';
        renderPops();
        if (!(await act('/api/screen/voice', { action: 'wake' }))) { S.voicePending = 0; renderPops(); }
    }

    async function stopVoice() {
        S.voicePending = 0;
        if (S.voice) S.voice.state = 'idle';
        renderPops();
        await act('/api/screen/voice', { action: 'stop' });
    }

    // ── Consola fizică ──────────────────────────────────────────
    // Navighezi cu encoderul pe TFT; aici apare aceeași listă, cu tot textul.
    function applyConsole(c) {
        const prev = S.console;
        S.console = c || null;
        if (!c) return;
        if (!prev || prev.ts !== c.ts) wake();
        const items = c.items || [];
        $('cTitle').textContent = 'CONSOLĂ · ' + c.title;
        $('cPos').textContent = items.length ? (c.sel + 1) + '/' + items.length : '';
        $('cList').innerHTML = items.length
            ? items.map((it, i) => '<div class="it' + (i === c.sel ? ' now' : '') + (it.d ? ' gata' : '') + '"><span>' +
                (it.d ? '[x]' : '[ ]') + '</span><span class="t">' + esc(low(it.x)) + '</span>' +
                (it.n && c.view === 'targets' ? '<span class="end">' + it.n + '%</span>' : '') + '</div>').join('')
            : '<p class="dim">lista e goală.</p>';
        const sel = $('cList').children[c.sel];
        if (sel) sel.scrollIntoView({ block: 'nearest' });
    }

    // ── Alarma ──────────────────────────────────────────────────
    function applyAlarm(ringing) {
        const was = S.ringing;
        S.ringing = !!ringing;
        if (S.ringing) {
            wake();
            $('aTime').textContent = hhmm(new Date());
            $('aLabel').textContent = S.ringLabel || 'alarmă';
            if (!was) log('!!', 'sună: ' + (S.ringLabel || 'alarmă'));
        } else if (was) {
            S.ringLabel = '';
        }
        renderPops();
    }

    // ── Timere ──────────────────────────────────────────────────
    const timerName = (t) => low(t.label) || (t.kind === 'alarm' ? 'alarmă' : 'timer');

    function timerLeft(t) {
        const due = new Date(t.due);
        if (t.kind === 'alarm') return 'la ' + hhmm(due);
        let s = Math.max(0, Math.round((due - Date.now()) / 1000));
        const h = Math.floor(s / 3600);
        s %= 3600;
        const m = Math.floor(s / 60);
        s %= 60;
        return h ? h + ':' + pad(m) + ':' + pad(s) : m + ':' + pad(s);
    }

    function applyTimers(list) {
        const before = new Map(S.timers.map((t) => [t.id, t]));
        const now = new Set(list.map((t) => t.id));
        list.forEach((t) => { if (S.timersSeen && !before.has(t.id)) log('ok', 'timer nou: ' + timerName(t) + ' ' + timerLeft(t)); });
        before.forEach((t, id) => {
            // a dispărut la scadență → a sunat; altfel a fost anulat
            if (!now.has(id) && new Date(t.due) - Date.now() < 3000) S.ringLabel = timerName(t);
        });
        S.timers = list;
        S.timersSeen = true;
        renderTimers();
    }

    function renderTimers() {
        const items = S.timers.filter((t) => new Date(t.due).getTime() > Date.now() - 1000);
        $('stTimers').textContent = items.length
            ? items.map((t) => timerName(t) + ' ' + timerLeft(t)).join('  ·  ')
            : '—';
        if (S.pop !== 'timers') return;
        const el = $('timers');
        const key = items.map((t) => t.id).join('|');
        // Rescriem lista doar când se schimbă — altfel butonul ar fi recreat sub deget.
        if (el.dataset.key !== key) {
            el.dataset.key = key;
            el.innerHTML = items.length
                ? items.map((t) => '<button class="it" type="button" data-id="' + esc(t.id) + '"><span class="t">' +
                    esc(timerName(t)) + ' <b></b></span><span class="end">[anulează]</span></button>').join('')
                : '<p class="dim">niciun timer activ. „jarvis, pune un timer de 10 minute”.</p>';
        }
        qsa('.it', el).forEach((row, i) => { row.querySelector('b').textContent = timerLeft(items[i]); });
    }

    // ── Prezentare generală: vremea, ziua, remindere, scene ─────
    async function pollOverview() {
        const d = await api('/api/screen/overview');
        if (!d._ok) return;
        S.overview = d;
        if (d.backlight != null) {
            S.backlight = d.backlight;
            // Pagina s-a reîncărcat cât ecranul era stins → lumina a rămas la minim.
            const restore = pref.get('blRestore', null);
            if (restore != null && !S.sleeping) {
                pref.set('blRestore', null);
                S.backlight = restore;
                backlight(restore);
            }
        } else {
            S.backlight = null;
        }
        renderAll();
    }

    function renderAll() {
        renderWeather();
        renderDayStatus();
        renderPlan();
        renderReminders();
        renderScenes();
        renderLights();
        renderMusic();
        renderSys();
        renderTimers();
    }

    const deg = (v) => (v == null ? '–' : Math.round(v) + '°');

    function renderWeather() {
        const w = S.overview && S.overview.weather;
        if (!w) { $('wx').textContent = 'vreme: indisponibil'; return; }
        const next = (w.zile || []).filter((z) => z.zi !== 'azi').slice(0, 2)
            .map((z) => z.zi + ' ' + deg(z.maxim) + '/' + deg(z.minim)).join('  ');
        $('wx').textContent = deg(w.temp) + ' ' + low(w.stare) + (next ? '  ·  ' + next : '');
    }

    function hmToMin(hm) {
        const m = /^(\d{1,2}):(\d{2})/.exec(hm || '');
        return m ? +m[1] * 60 + +m[2] : null;
    }

    /** Blocurile zilei cu minute absolute; cele de după miezul nopții trec în ziua următoare. */
    function timedBlocks() {
        const b = (S.overview && S.overview.day && S.overview.day.blocuri) || [];
        let prev = -1, offset = 0;
        return b.map((x) => {
            let s = hmToMin(x.start), e = hmToMin(x.end);
            if (s == null) return Object.assign({}, x, { s: null, e: null });
            if (s + offset < prev) offset += 1440;
            s += offset;
            e = e == null ? s : e + offset;
            if (e < s) e += 1440;
            prev = s;
            return Object.assign({}, x, { s: s, e: e });
        });
    }

    function nowMin() {
        const d = new Date();
        return d.getHours() * 60 + d.getMinutes() + d.getSeconds() / 60;
    }

    function renderDayStatus() {
        if (!S.overview) return;
        const blocks = timedBlocks();
        const todo = blocks.filter((b) => !b.gata);
        const now = nowMin();
        const cur = todo.find((b) => b.s != null && b.s <= now && now < b.e);
        const next = todo.find((b) => b.s != null && b.s > now && b !== cur);
        let nowTxt, nextTxt = '—';

        if (!blocks.length) nowTxt = 'nimic planificat';
        else if (!todo.length) nowTxt = 'gata tot · ' + blocks.length + '/' + blocks.length;
        else if (cur) nowTxt = bar((now - cur.s) / Math.max(1, cur.e - cur.s), 10) + ' ' + low(cur.titlu) + ' → ' + cur.end;
        else if (blocks.some((b) => b.s != null)) nowTxt = 'pauză';
        else nowTxt = (blocks.length - todo.length) + '/' + blocks.length + ' bifate · ' + low(todo[0].titlu);

        if (next) {
            const m = Math.round(next.s - now);
            nextTxt = next.start + ' ' + low(next.titlu) + '  (' + (m >= 60 ? Math.floor(m / 60) + 'h' + pad(m % 60) : m + 'm') + ')';
        }
        $('stNow').textContent = nowTxt;
        $('stNext').textContent = nextTxt;

        const key = cur ? cur.titlu + cur.start : '';
        if (key && key !== S.seenNow) {
            S.seenNow = key;
            pref.set('seenNow', key);
            log('zi', 'acum: ' + low(cur.titlu) + ' (' + cur.start + '–' + cur.end + ')', true);
        }
    }

    function renderPlan() {
        const el = $('plan');
        const blocks = timedBlocks();
        if (!blocks.length) { el.innerHTML = '<p class="dim">n-ai niciun plan pe azi.</p>'; return; }
        const now = nowMin();
        el.innerHTML = blocks.map((b) => {
            const pasiv = b.gen === 'masa' || b.gen === 'ocupat';
            const cls = ['it', b.gata ? 'gata' : '', pasiv ? 'pasiv' : '',
                (!b.gata && b.s != null && b.s <= now && now < b.e) ? 'now' : ''].filter(Boolean).join(' ');
            const box = pasiv ? '   ' : (b.gata ? '[x]' : '[ ]');
            return '<button class="' + esc(cls) + '" type="button" data-titlu="' + esc(b.titlu) + '"' +
                (pasiv || b.gata ? ' data-lock="1"' : '') + '><span>' + box + '</span><span class="h">' +
                esc(b.start || '') + '</span><span class="t">' + esc(low(b.titlu)) + '</span></button>';
        }).join('');
    }

    function renderReminders() {
        const rem = (S.overview && S.overview.reminders) || [];
        const pr = { High: '!!', Med: '! ', Low: '  ' };
        $('stRem').textContent = rem.length
            ? rem.length + ' deschise · ' + rem.slice(0, 3).map((r) => low(r.title)).join(' · ')
            : 'nimic deschis';
        $('rems').innerHTML = rem.length
            ? rem.map((r) => '<button class="it" type="button" data-id="' + esc(r.id) + '"><span>[ ]</span><span class="h">' +
                esc(pr[r.priority] || '  ') + '</span><span class="t">' + esc(low(r.title)) + '</span></button>').join('')
            : '<p class="dim">niciun reminder deschis.</p>';
    }

    function renderScenes() {
        const sc = (S.overview && S.overview.scenes) || [];
        $('scenes').innerHTML = sc.length
            ? sc.map((s, i) => '<button class="it" type="button" data-id="' + esc(s.id) + '" data-name="' + esc(s.name) +
                '"><span class="h">[' + (i + 1) + ']</span><span class="t">' + esc(low(s.name)) + '</span></button>').join('')
            : '<p class="dim">nicio scenă salvată (le creezi din dashboard).</p>';
    }

    // ── Muzica ──────────────────────────────────────────────────
    async function pollMusic() {
        if (Date.now() - S.musicBusy < 900) return;   // tocmai am apăsat ceva; Spotify încă se actualizează
        const d = await api('/api/screen/music');
        const key = d.track ? low(d.artist + ' — ' + d.track) : '';
        if (key && key !== S.seenTrack) {
            S.seenTrack = key;
            pref.set('seenTrack', key);
            log('♪', key, true);
        }
        S.music = d;
        S.musicAt = Date.now();
        renderMusic();
    }

    const fmtMs = (ms) => { const s = Math.max(0, Math.floor((ms || 0) / 1000)); return Math.floor(s / 60) + ':' + pad(s % 60); };

    function musicPos() {
        const m = S.music;
        return Math.min(m.duration_ms || 0, (m.progress_ms || 0) + (m.playing ? Date.now() - S.musicAt : 0));
    }

    function renderMusic() {
        const m = S.music;
        if (!m) return;
        const has = m.status === 'ok' && !!m.track;
        if (!has) {
            const txt = m.available === false ? 'spotify neconectat · python -m tools.spotify_api auth' : 'nu cântă nimic';
            $('stMusic').textContent = txt;
            $('mTrack').textContent = '—';
            $('mArtist').textContent = txt;
            $('mBar').textContent = bar(0, 20) + ' 0:00/0:00';
            $('mToggle').textContent = 'play';
            return;
        }
        const pos = musicPos();
        const frac = m.duration_ms ? pos / m.duration_ms : 0;
        $('stMusic').textContent = (m.playing ? '▶ ' : '❚❚ ') + low(m.artist) + ' — ' + low(m.track) + '  ' + fmtMs(pos) + '/' + fmtMs(m.duration_ms);
        $('mTrack').textContent = low(m.track);
        $('mArtist').textContent = low(m.artist) + (m.album ? ' · ' + low(m.album) : '');
        $('mBar').textContent = bar(frac, 20) + ' ' + fmtMs(pos) + '/' + fmtMs(m.duration_ms);
        $('mToggle').textContent = m.playing ? 'pauză' : 'play';
        $('mVol').textContent = m.volume != null ? m.volume + '%' : '–';
        // Piesa s-a terminat între două interogări → întrebăm imediat ce urmează.
        if (m.playing && m.duration_ms && pos >= m.duration_ms && Date.now() - S.musicAt > 1500) {
            S.musicAt = Date.now();
            setTimeout(pollMusic, 800);
        }
    }

    async function musicCmd(action, value) {
        const m = S.music || {};
        if (action === 'toggle') action = m.playing ? 'pause' : 'play';
        // Optimist: textul se schimbă imediat, Spotify confirmă la următoarea interogare.
        if ((action === 'pause' || action === 'play') && m.track) {
            m.progress_ms = musicPos();
            S.musicAt = Date.now();
            m.playing = action === 'play';
        }
        if (action === 'set_volume') m.volume = value;
        renderMusic();
        S.musicBusy = Date.now();
        const names = { play: 'play', pause: 'pauză', next: 'următoarea', previous: 'înapoi', set_volume: 'volum ' + value + '%' };
        await act('/api/screen/music', { action: action, value: value },
            action === 'like' ? (r) => low(r.message || 'am reținut') : 'muzică: ' + (names[action] || action));
        S.musicBusy = 0;
        setTimeout(pollMusic, 700);
    }

    // ── Lumini ──────────────────────────────────────────────────
    async function pollLights() {
        S.lights = await api('/api/screen/lights');
        renderLights();
    }

    function renderLights() {
        const l = S.lights;
        if (!l) return;
        const txt = l.available === false ? 'offline (wled nu răspunde)' : (l.on ? 'aprinse ' + l.bri + '%' : 'stinse');
        $('stLights').textContent = txt;
        $('lState').textContent = txt;
        $('lBri').textContent = l.on ? l.bri + '%' : '–';
        qsa('[data-light]').forEach((b) => b.classList.toggle('on', l.available !== false && (b.dataset.light === 'on') === !!l.on));
    }

    // ── Sys ─────────────────────────────────────────────────────
    function renderSys() {
        $('blRow').style.display = S.backlight == null ? 'none' : '';
        if (S.backlight != null && !S.sleeping) $('blVal').textContent = S.backlight + '%';
        $('autoSleep').textContent = S.autoSleep ? 'da' : 'nu';
        $('autoSleep').classList.toggle('on', S.autoSleep);
        const somn = S.overview && S.overview.day && S.overview.day.somn;
        $('sleepHint').textContent = somn ? somn.culcare + '–' + somn.trezire : '';
    }

    // ── Noaptea / somnul ecranului ──────────────────────────────
    function inNight() {
        const somn = S.overview && S.overview.day && S.overview.day.somn;
        if (!somn) return false;
        const c = hmToMin(somn.culcare), t = hmToMin(somn.trezire), now = nowMin();
        if (c == null || t == null) return false;
        return c < t ? now >= c && now < t : now >= c || now < t;
    }

    function backlight(level) {
        if (S.backlight == null) return;
        api('/api/screen/backlight', { level: level }).catch(() => {});
    }

    function sleep(auto) {
        if (S.sleeping) return;
        S.sleeping = true;
        S.sleepAuto = !!auto;
        closePop();
        $('sleep').classList.add('show');
        if (S.backlight != null) {
            S.blBeforeSleep = S.backlight;
            pref.set('blRestore', S.backlight);
            backlight(2);
        }
    }

    function wake() {
        S.lastTouch = Date.now();
        if (!S.sleeping) return;
        S.sleeping = false;
        $('sleep').classList.remove('show');
        if (S.blBeforeSleep != null) {
            backlight(S.blBeforeSleep);
            S.backlight = S.blBeforeSleep;
            S.blBeforeSleep = null;
            pref.set('blRestore', null);
        }
        pollMusic().catch(() => {});
    }

    function idleCheck() {
        const idle = Date.now() - S.lastTouch;
        if (S.sleeping) {
            if (S.sleepAuto && !inNight()) wake();   // dimineața se aprinde singur
            return;
        }
        if (idle > IDLE_CLOSE_MS && S.pop) closePop();
        if (S.autoSleep && !S.ringing && !voiceShown() && idle > IDLE_SLEEP_MS && inNight()) sleep(true);
    }

    // ── Evenimente ──────────────────────────────────────────────
    // Orice atingere resetează contorul de inactivitate; pe ecranul stins,
    // prima atingere doar îl aprinde (nu apasă și ce e dedesubt).
    document.addEventListener('pointerdown', (e) => {
        if (S.sleeping) { e.preventDefault(); e.stopPropagation(); wake(); return; }
        S.lastTouch = Date.now();
    }, true);
    document.addEventListener('contextmenu', (e) => e.preventDefault());

    qsa('[data-open]').forEach((b) => b.addEventListener('click', () => openPop(b.dataset.open)));
    qsa('.pop .x').forEach((b) => b.addEventListener('click', closePop));
    // atingere pe fundalul întunecat = închide
    qsa('.pop').forEach((p) => p.addEventListener('click', (e) => {
        if (e.target === p && !['voice', 'alarm', 'offline'].includes(p.dataset.pop)) closePop();
    }));

    $('prompt').addEventListener('click', startVoice);
    $('vStop').addEventListener('click', stopVoice);
    $('aStop').addEventListener('click', () => {
        applyAlarm(false);
        act('/api/screen/timer', { action: 'stop' }, 'alarmă oprită');
    });
    $('btnSleep').addEventListener('click', () => sleep(false));

    $('timers').addEventListener('click', async (e) => {
        const row = e.target.closest('.it');
        if (!row) return;
        const t = S.timers.find((x) => x.id === row.dataset.id);
        if (await act('/api/screen/timer', { action: 'cancel', id: row.dataset.id }, 'anulat: ' + (t ? timerName(t) : 'timer'))) {
            S.timers = S.timers.filter((x) => x.id !== row.dataset.id);
            renderTimers();
        }
    });

    $('rems').addEventListener('click', async (e) => {
        const row = e.target.closest('.it');
        if (!row || !armed(row)) return;
        const r = S.overview.reminders.find((x) => x.id === row.dataset.id);
        if (await act('/api/reminders/check', { id: row.dataset.id, checked: true }, 'bifat: ' + low(r ? r.title : 'reminder'))) {
            S.overview.reminders = S.overview.reminders.filter((x) => x.id !== row.dataset.id);
            renderReminders();
        }
    });

    $('plan').addEventListener('click', async (e) => {
        const row = e.target.closest('.it');
        if (!row || row.dataset.lock || !armed(row)) return;
        if (await act('/api/screen/plan/done', { titlu: row.dataset.titlu }, (d) => low(d.message || 'bifat'))) pollOverview();
    });

    qsa('[data-music]').forEach((b) => b.addEventListener('click', () => musicCmd(b.dataset.music)));
    $('mToggle').addEventListener('click', () => musicCmd('toggle'));
    qsa('[data-vol]').forEach((b) => b.addEventListener('click', () => {
        const cur = (S.music && S.music.volume) || 0;
        musicCmd('set_volume', Math.max(0, Math.min(100, cur + +b.dataset.vol)));
    }));

    qsa('[data-light]').forEach((b) => b.addEventListener('click', async () => {
        const on = b.dataset.light === 'on';
        if (await act('/api/screen/lights', { action: on ? 'on' : 'off' }, 'lumini ' + (on ? 'aprinse' : 'stinse'))) {
            S.lights = Object.assign({}, S.lights, { on: on });
            renderLights();
            setTimeout(pollLights, 600);
        }
    }));
    qsa('[data-bri]').forEach((b) => b.addEventListener('click', async () => {
        const cur = (S.lights && S.lights.on && S.lights.bri) || 0;
        const v = Math.max(1, Math.min(100, cur + +b.dataset.bri));
        if (await act('/api/screen/lights', { action: 'brightness', value: v }, 'lumini ' + v + '%')) {
            S.lights = Object.assign({}, S.lights, { available: true, on: true, bri: v });
            renderLights();
        }
    }));
    $('scenes').addEventListener('click', async (e) => {
        const b = e.target.closest('.it');
        if (!b) return;
        await act('/api/scenes/activate', { id: b.dataset.id }, 'scenă: ' + low(b.dataset.name));
        setTimeout(pollLights, 900);
    });

    qsa('[data-bl]').forEach((b) => b.addEventListener('click', async () => {
        const v = Math.max(5, Math.min(100, (S.backlight || 0) + +b.dataset.bl));
        if (await act('/api/screen/backlight', { level: v })) { S.backlight = v; renderSys(); }
    }));
    $('autoSleep').addEventListener('click', () => {
        S.autoSleep = !S.autoSleep;
        pref.set('autoSleep', S.autoSleep);
        renderSys();
    });
    $('actReload').addEventListener('click', () => location.reload());
    $('actRestart').addEventListener('click', async (e) => {
        const b = e.currentTarget;
        if (!armed(b)) { b.textContent = 'sigur? atinge iar'; setTimeout(() => { b.textContent = 'repornește chronos'; }, ARM_MS); return; }
        b.textContent = 'repornește chronos';
        closePop();
        await act('/api/system/restart', {}, 'repornesc chronos');
    });

    // ── Pornire ─────────────────────────────────────────────────
    renderLog();
    log('sys', 'ecran pornit · ' + location.host);
    tick();
    setInterval(tick, 1000);
    loop(pollState, stateDelay);
    loop(pollOverview, 60000);
    loop(pollMusic, () => (S.sleeping ? 15000 : 4000));
    loop(pollLights, 30000);
    setTimeout(() => { S.ready = true; }, 5000);
})();
