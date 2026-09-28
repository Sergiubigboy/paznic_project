// ===== AUTOMATIZĂRI =====
// Un declanșator + o listă de acțiuni. Validarea finală e pe server
// (core/automations.py normalize) — aici doar construim obiectul.

(function () {
    const TRIGGERS = [
        { type: 'time',        icon: '🕐', label: 'La o oră' },
        { type: 'sun',         icon: '🌅', label: 'Soare' },
        { type: 'alarm',       icon: '⏰', label: 'Alarmă' },
        { type: 'music_start', icon: '🎵', label: 'Pornește muzica' },
        { type: 'wake_word',   icon: '🎙️', label: '„Jarvis”' },
    ];
    const ACTIONS = {
        lights:        { icon: '💡', label: 'Lumini',          make: () => ({ type: 'lights', mode: 'color', color: '#ffb46b', brightness: 70, fade_s: 0, zone: 'all' }) },
        scene:         { icon: '🎬', label: 'Scenă',           make: () => ({ type: 'scene', name: STATE.scenes[0] || '' }) },
        music:         { icon: '🎧', label: 'Pune muzică',     make: () => ({ type: 'music', prompt: '' }) },
        music_control: { icon: '⏯️', label: 'Pauză / reia',    make: () => ({ type: 'music_control', action: 'pause' }) },
        volume:        { icon: '🔊', label: 'Volum',           make: () => ({ type: 'volume', percent: 40 }) },
        wait:          { icon: '⏳', label: 'Așteaptă',        make: () => ({ type: 'wait', seconds: 30 }) },
        ring:          { icon: '🔔', label: 'Sunet de alarmă', make: () => ({ type: 'ring' }) },
        ac:            { icon: '❄️', label: 'Aer condiționat', make: () => ({ type: 'ac', on: true }) },
        notify:        { icon: '📨', label: 'Mesaj Telegram',  make: () => ({ type: 'notify', text: '' }) },
    };
    const DAYS = ['Lu', 'Ma', 'Mi', 'Jo', 'Vi', 'Sâ', 'Du'];
    const ICONS = ['⚡', '🌅', '🌇', '🌙', '☀️', '⏰', '🎵', '🎧', '💡', '🛏️', '☕', '🏋️', '📚', '🎮', '🍿', '🧘', '🚿', '🔥', '❄️', '🎙️'];

    const TEMPLATES = [
        { icon: '🌅', name: 'Trezire', sub: '06:40 L-V · lumină caldă în fade, alarmă, muzică',
          trigger: { type: 'time', at: '06:40' }, days: [0, 1, 2, 3, 4],
          actions: [
              { type: 'lights', mode: 'color', color: '#ffb46b', brightness: 80, fade_s: 600, zone: 'all' },
              { type: 'wait', seconds: 600 },
              { type: 'ring' },
              { type: 'music', prompt: 'ceva energic, bun de trezit dimineața' },
              { type: 'volume', percent: 35 },
          ] },
        { icon: '🌇', name: 'Seara la apus', sub: 'Cu 15 min înainte de apus · lumină caldă',
          trigger: { type: 'sun', event: 'sunset', offset_min: -15 },
          actions: [{ type: 'lights', mode: 'color', color: '#ff9a4d', brightness: 55, fade_s: 300, zone: 'all' }] },
        { icon: '⏰', name: 'Alarma aprinde lumina', sub: 'Când sună o alarmă pusă din voce',
          trigger: { type: 'alarm', label: '' },
          actions: [{ type: 'lights', mode: 'on', brightness: 100, fade_s: 5, zone: 'all' }] },
        { icon: '🎵', name: 'Vibe de muzică', sub: 'Pornește muzica → lumini ambientale',
          trigger: { type: 'music_start' }, between: ['18:00', '02:00'],
          actions: [{ type: 'lights', mode: 'color', color: '#8b5cff', brightness: 45, fade_s: 3, zone: 'all' }] },
        { icon: '🌙', name: 'Jarvis noaptea', sub: 'Wake word între 23-06 → lumină slabă jos',
          trigger: { type: 'wake_word' }, between: ['23:00', '06:00'],
          actions: [{ type: 'lights', mode: 'color', color: '#ff7a3d', brightness: 8, fade_s: 1, zone: 'floor' }] },
        { icon: '🛏️', name: 'Noapte bună', sub: '00:30 · pauză muzică, stinge în 1 min',
          trigger: { type: 'time', at: '00:30' },
          actions: [{ type: 'music_control', action: 'pause' }, { type: 'lights', mode: 'off', fade_s: 60, zone: 'all' }] },
    ];

    const STATE = { items: [], scenes: [], editing: null };
    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const clone = (o) => JSON.parse(JSON.stringify(o));

    async function api(url, body) {
        const opt = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
        const r = await fetch(url, opt);
        const d = await r.json().catch(() => ({}));
        if (!r.ok || d.status === 'error') throw new Error(d.message || `HTTP ${r.status}`);
        return d;
    }

    // ─────────────── LISTĂ ───────────────

    async function load() {
        try {
            const d = await api('/api/automations');
            STATE.items = d.automations || [];
            STATE.scenes = d.scenes || [];
            $('auSunrise').textContent = d.sun?.sunrise || '—';
            $('auSunset').textContent = d.sun?.sunset || '—';
            $('auEngineOff').classList.toggle('hidden', !!d.engine);
            renderGrid();
        } catch (e) {
            $('auGrid').innerHTML = `<div class="au-empty">Nu pot încărca automatizările: ${esc(e.message)}</div>`;
        }
    }

    function fmtNext(iso) {
        const d = new Date(iso), now = new Date();
        const hm = d.toTimeString().slice(0, 5);
        const days = Math.round((new Date(d.toDateString()) - new Date(now.toDateString())) / 864e5);
        if (days === 0) return `azi la ${hm}`;
        if (days === 1) return `mâine la ${hm}`;
        return `${['Du', 'Lu', 'Ma', 'Mi', 'Jo', 'Vi', 'Sâ'][d.getDay()]} ${hm}`;
    }

    function fmtAgo(iso) {
        const s = (Date.now() - new Date(iso)) / 1000;
        if (s < 90) return 'acum';
        if (s < 3600) return `acum ${Math.round(s / 60)} min`;
        if (s < 86400) return `acum ${Math.round(s / 3600)} h`;
        return new Date(iso).toLocaleDateString('ro-RO', { day: 'numeric', month: 'short' });
    }

    function renderGrid() {
        const grid = $('auGrid');
        if (!STATE.items.length) {
            grid.innerHTML = '<div class="au-empty">Nicio automatizare încă. Alege un șablon de mai jos sau apasă „Automatizare nouă”.</div>';
        } else {
            grid.innerHTML = STATE.items.map((a) => {
                const st = a.state || {};
                const foot = [];
                if (a.enabled && a.next_run) foot.push(`⏭ ${fmtNext(a.next_run)}`);
                if (st.last_run) foot.push(st.ok ? `✓ ${fmtAgo(st.last_run)}` : `<span class="bad" title="${esc(st.message)}">✗ ${fmtAgo(st.last_run)}</span>`);
                return `
                <div class="au-card ${a.enabled ? '' : 'off'}" data-trig="${esc(a.trigger.type)}" data-id="${esc(a.id)}" role="button" tabindex="0">
                    <div class="au-card-top">
                        <div class="au-card-icon">${esc(a.icon)}</div>
                        <div class="au-card-ctrl">
                            <button class="au-run" data-run="${esc(a.id)}" title="Rulează acum" type="button">▶</button>
                            <label class="au-switch" title="Activă"><input type="checkbox" data-toggle="${esc(a.id)}" ${a.enabled ? 'checked' : ''}><span></span></label>
                        </div>
                    </div>
                    <div>
                        <div class="au-card-name">${esc(a.name)}</div>
                        <div class="au-card-when">${esc(a.trigger_text)}</div>
                    </div>
                    <div class="au-card-steps">${(a.actions_text || []).map((t) => `<span class="au-card-step">${esc(t)}</span>`).join('')}</div>
                    ${foot.length ? `<div class="au-card-foot">${foot.join(' · ')}</div>` : ''}
                </div>`;
            }).join('');
        }

        const upcoming = STATE.items.filter((a) => a.enabled && a.next_run).sort((a, b) => a.next_run.localeCompare(b.next_run))[0];
        $('auNext').innerHTML = upcoming ? `Urmează <b>${esc(upcoming.name)}</b> ${esc(fmtNext(upcoming.next_run))}` : 'Nimic programat';
    }

    function renderTemplates() {
        $('auTemplates').innerHTML = TEMPLATES.map((t, i) => `
            <button class="au-tpl" data-tpl="${i}" type="button">
                <span class="au-tpl-ic">${t.icon}</span>
                <span><div class="au-tpl-name">${esc(t.name)}</div><div class="au-tpl-sub">${esc(t.sub)}</div></span>
            </button>`).join('');
    }

    // ─────────────── EDITOR ───────────────

    function blank() {
        return { name: '', icon: '⚡', enabled: true, trigger: { type: 'time', at: '07:00' }, days: [], between: null, actions: [] };
    }

    function openEditor(auto) {
        STATE.editing = clone(auto);
        const a = STATE.editing;
        $('auName').value = a.name || '';
        $('auIcon').textContent = a.icon || '⚡';
        $('auEnabled').checked = a.enabled !== false;
        $('auDelete').classList.toggle('hidden', !a.id);
        $('auBetweenOn').checked = !!a.between;
        $('auBetween').classList.toggle('hidden', !a.between);
        if (a.between) { $('auBetweenA').value = a.between[0]; $('auBetweenB').value = a.between[1]; }
        $('auIconGrid').classList.add('hidden');
        $('auAddMenu').classList.add('hidden');
        renderTriggers(); renderDays(); renderActions();
        $('auModal').classList.add('open');
        if (!a.name) setTimeout(() => $('auName').focus(), 50);
    }

    function closeEditor() {
        $('auModal').classList.remove('open');
        STATE.editing = null;
    }

    function renderTriggers() {
        const t = STATE.editing.trigger;
        $('auTrigGrid').innerHTML = TRIGGERS.map((x) => `
            <button type="button" class="au-trig ${t.type === x.type ? 'active' : ''}" data-trig="${x.type}">
                <span class="ti">${x.icon}</span>${x.label}
            </button>`).join('');

        const o = $('auTrigOpts');
        if (t.type === 'time') {
            o.innerHTML = `La ora <input type="time" data-t="at" value="${esc(t.at || '07:00')}">`;
        } else if (t.type === 'sun') {
            const off = t.offset_min || 0;
            o.innerHTML = `
                <div class="au-seg">
                    <button type="button" data-sun="sunrise" class="${t.event === 'sunrise' ? 'on' : ''}">🌅 Răsărit</button>
                    <button type="button" data-sun="sunset" class="${t.event !== 'sunrise' ? 'on' : ''}">🌇 Apus</button>
                </div>
                <input type="number" class="au-num" data-t="offset_abs" min="0" max="180" value="${Math.abs(off)}"> min
                <select class="au-sel" data-t="offset_dir">
                    <option value="-1" ${off < 0 ? 'selected' : ''}>înainte</option>
                    <option value="1" ${off >= 0 ? 'selected' : ''}>după</option>
                </select>
                <div class="form-hint">Azi: răsărit ${esc($('auSunrise').textContent)}, apus ${esc($('auSunset').textContent)}. Se recalculează zilnic.</div>`;
        } else if (t.type === 'alarm') {
            o.innerHTML = `
                <input type="text" class="grow" data-t="label" value="${esc(t.label || '')}" placeholder="Doar alarmele cu eticheta… (gol = oricare)" style="flex:1">
                <div class="form-hint">Pentru alarmele puse din voce („pune-mi alarmă la 7”). Timerele nu contează. Dacă vrei ca automatizarea să fie ea însăși alarma, alege „La o oră” + acțiunea „Sunet de alarmă”.</div>`;
        } else if (t.type === 'music_start') {
            o.innerHTML = '<div class="form-hint">Când pornește muzica prin Chronos (DJ, „pune muzică”, reia, scene). Muzica pornită chiar de o automatizare nu declanșează alte automatizări.</div>';
        } else {
            o.innerHTML = '<div class="form-hint">De fiecare dată când aude „Jarvis” (cel mult o dată la 20 de secunde). Combină cu un interval orar, ca să nu se întâmple ziua.</div>';
        }
    }

    function readTrigger() {
        const t = STATE.editing.trigger, o = $('auTrigOpts');
        const v = (k) => o.querySelector(`[data-t="${k}"]`)?.value;
        if (t.type === 'time') t.at = v('at') || '07:00';
        if (t.type === 'sun') t.offset_min = (parseInt(v('offset_abs'), 10) || 0) * parseInt(v('offset_dir') || '1', 10);
        if (t.type === 'alarm') t.label = (v('label') || '').trim();
    }

    function renderDays() {
        const days = STATE.editing.days || [];
        const all = !days.length;
        $('auDays').innerHTML =
            DAYS.map((d, i) => `<button type="button" class="au-day ${all || days.includes(i) ? 'on' : ''}" data-day="${i}">${d}</button>`).join('') +
            `<button type="button" class="au-day au-day-quick" data-days="work">L-V</button>` +
            `<button type="button" class="au-day au-day-quick" data-days="all">Zilnic</button>`;
    }

    function actionBody(a, i) {
        const seg = (key, opts) => `<div class="au-seg">${opts.map(([v, l]) =>
            `<button type="button" data-a="${i}" data-k="${key}" data-v="${esc(v)}" class="${String(a[key]) === String(v) ? 'on' : ''}">${l}</button>`).join('')}</div>`;
        switch (a.type) {
            case 'lights': {
                const fadeMin = Math.floor((a.fade_s || 0) / 60), fadeSec = (a.fade_s || 0) % 60;
                return seg('mode', [['on', 'Aprinde'], ['color', 'Culoare'], ['off', 'Stinge']]) +
                    seg('zone', [['all', 'Toate'], ['main', 'Sus'], ['floor', 'Jos']]) +
                    (a.mode === 'color' ? `<input type="color" data-a="${i}" data-k="color" value="${esc(a.color || '#ffb46b')}">` : '') +
                    (a.mode !== 'off' ? `<input type="range" min="1" max="100" data-a="${i}" data-k="brightness" value="${a.brightness || 70}"><span class="au-val" data-val="${i}">${a.brightness || 70}%</span>` : '') +
                    `<span>fade</span><input type="number" class="au-num" min="0" max="100" data-a="${i}" data-k="fade_min" value="${fadeMin}"> min
                     <input type="number" class="au-num" min="0" max="59" data-a="${i}" data-k="fade_sec" value="${fadeSec}"> s`;
            }
            case 'scene':
                return STATE.scenes.length
                    ? `<select class="au-sel grow" data-a="${i}" data-k="name">${STATE.scenes.map((s) => `<option ${s === a.name ? 'selected' : ''}>${esc(s)}</option>`).join('')}</select>`
                    : '<span class="form-hint">Nu ai scene salvate — le creezi din Acasă.</span>';
            case 'music':
                return `<input type="text" class="grow" data-a="${i}" data-k="prompt" value="${esc(a.prompt)}" placeholder="ex: jazz liniștit, trap românesc, o piesă anume…">`;
            case 'music_control':
                return seg('action', [['pause', '⏸ Pauză'], ['resume', '▶ Reia'], ['next', '⏭ Următoarea']]);
            case 'volume':
                return `<input type="range" min="0" max="100" data-a="${i}" data-k="percent" value="${a.percent}"><span class="au-val" data-val="${i}">${a.percent}%</span>`;
            case 'wait':
                return `<input type="number" class="au-num" min="0" max="60" data-a="${i}" data-k="wait_min" value="${Math.floor(a.seconds / 60)}"> min
                        <input type="number" class="au-num" min="0" max="59" data-a="${i}" data-k="wait_sec" value="${a.seconds % 60}"> s`;
            case 'ac':
                return seg('on', [['true', 'Pornește'], ['false', 'Oprește']]);
            case 'notify':
                return `<input type="text" class="grow" data-a="${i}" data-k="text" value="${esc(a.text)}" placeholder="Mesajul trimis pe Telegram">`;
            default:
                return '';
        }
    }

    function renderActions() {
        const list = STATE.editing.actions;
        $('auActions').innerHTML = list.map((a, i) => {
            const meta = ACTIONS[a.type] || { icon: '?', label: a.type };
            return `
            <div class="au-act">
                <div class="au-act-head">
                    <span class="au-act-ic">${meta.icon}</span>
                    <span class="au-act-title">${meta.label}</span>
                    <span class="au-act-ctrl">
                        <button type="button" data-move="${i}" data-dir="-1" ${i === 0 ? 'disabled' : ''} title="Mută sus">↑</button>
                        <button type="button" data-move="${i}" data-dir="1" ${i === list.length - 1 ? 'disabled' : ''} title="Mută jos">↓</button>
                        <button type="button" data-remove="${i}" title="Șterge">✕</button>
                    </span>
                </div>
                <div class="au-act-body">${actionBody(a, i)}</div>
            </div>`;
        }).join('');
    }

    function onActionInput(el) {
        const i = +el.dataset.a, k = el.dataset.k, a = STATE.editing.actions[i];
        if (!a) return;
        const num = () => parseInt(el.value, 10) || 0;
        if (k === 'fade_min') a.fade_s = num() * 60 + (a.fade_s || 0) % 60;
        else if (k === 'fade_sec') a.fade_s = Math.floor((a.fade_s || 0) / 60) * 60 + num();
        else if (k === 'wait_min') a.seconds = Math.max(1, num() * 60 + a.seconds % 60);
        else if (k === 'wait_sec') a.seconds = Math.max(1, Math.floor(a.seconds / 60) * 60 + num());
        else if (k === 'brightness' || k === 'percent') {
            a[k] = num();
            const v = document.querySelector(`[data-val="${i}"]`);
            if (v) v.textContent = `${a[k]}%`;
        } else a[k] = el.value;
    }

    async function save() {
        const a = STATE.editing;
        readTrigger();
        a.name = $('auName').value.trim();
        a.icon = $('auIcon').textContent;
        a.enabled = $('auEnabled').checked;
        a.between = $('auBetweenOn').checked ? [$('auBetweenA').value, $('auBetweenB').value] : null;
        if (!a.name) { flash('Dă-i un nume.', 'error'); $('auName').focus(); return; }
        try {
            await api('/api/automations/save', a);
            flash(`„${a.name}” salvată.`);
            closeEditor();
            load();
        } catch (e) { flash(e.message, 'error'); }
    }

    // ─────────────── EVENIMENTE ───────────────

    function wire() {
        $('autoNew').addEventListener('click', () => openEditor(blank()));
        $('auClose').addEventListener('click', closeEditor);
        $('auModal').addEventListener('click', (e) => { if (e.target.id === 'auModal') closeEditor(); });
        document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && STATE.editing) closeEditor(); });
        $('auSave').addEventListener('click', save);

        $('auTemplates').addEventListener('click', (e) => {
            const b = e.target.closest('[data-tpl]');
            if (!b) return;
            const t = clone(TEMPLATES[+b.dataset.tpl]);
            delete t.sub;
            openEditor({ ...blank(), ...t });
        });

        $('auGrid').addEventListener('click', async (e) => {
            const run = e.target.closest('[data-run]');
            if (run) {
                e.stopPropagation();
                try { flash((await api('/api/automations/run', { id: run.dataset.run })).message); setTimeout(load, 4000); }
                catch (err) { flash(err.message, 'error'); }
                return;
            }
            if (e.target.closest('.au-switch')) return;
            const card = e.target.closest('.au-card');
            if (card) openEditor(STATE.items.find((a) => a.id === card.dataset.id));
        });
        $('auGrid').addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && e.target.classList.contains('au-card')) e.target.click();
        });
        $('auGrid').addEventListener('change', async (e) => {
            const t = e.target.closest('[data-toggle]');
            if (!t) return;
            try { await api('/api/automations/toggle', { id: t.dataset.toggle, enabled: t.checked }); load(); }
            catch (err) { flash(err.message, 'error'); t.checked = !t.checked; }
        });

        $('auDelete').addEventListener('click', async () => {
            const a = STATE.editing;
            if (!a?.id || !confirm(`Ștergi „${a.name}”?`)) return;
            try { await api('/api/automations/delete', { id: a.id }); closeEditor(); load(); }
            catch (e) { flash(e.message, 'error'); }
        });

        // iconiță
        $('auIconGrid').innerHTML = ICONS.map((i) => `<button type="button" data-icon="${i}">${i}</button>`).join('');
        $('auIcon').addEventListener('click', () => $('auIconGrid').classList.toggle('hidden'));
        $('auIconGrid').addEventListener('click', (e) => {
            const b = e.target.closest('[data-icon]');
            if (!b) return;
            $('auIcon').textContent = b.dataset.icon;
            $('auIconGrid').classList.add('hidden');
        });

        // declanșator
        $('auTrigGrid').addEventListener('click', (e) => {
            const b = e.target.closest('[data-trig]');
            if (!b) return;
            readTrigger();
            const type = b.dataset.trig, prev = STATE.editing.trigger;
            if (prev.type === type) return;
            STATE.editing.trigger = type === 'time' ? { type, at: prev.at || '07:00' }
                : type === 'sun' ? { type, event: 'sunset', offset_min: 0 }
                : type === 'alarm' ? { type, label: '' } : { type };
            renderTriggers();
        });
        $('auTrigOpts').addEventListener('click', (e) => {
            const b = e.target.closest('[data-sun]');
            if (!b) return;
            readTrigger();
            STATE.editing.trigger.event = b.dataset.sun;
            renderTriggers();
        });

        // zile
        $('auDays').addEventListener('click', (e) => {
            const b = e.target.closest('button');
            if (!b) return;
            const a = STATE.editing;
            if (b.dataset.days === 'all') a.days = [];
            else if (b.dataset.days === 'work') a.days = [0, 1, 2, 3, 4];
            else {
                const d = +b.dataset.day;
                let set = new Set(a.days.length ? a.days : [0, 1, 2, 3, 4, 5, 6]);
                set.has(d) ? set.delete(d) : set.add(d);
                if (!set.size) set = new Set([d]);            // măcar o zi
                a.days = set.size === 7 ? [] : [...set].sort();
            }
            renderDays();
        });
        $('auBetweenOn').addEventListener('change', (e) => $('auBetween').classList.toggle('hidden', !e.target.checked));

        // acțiuni
        $('auAddMenu').innerHTML = Object.entries(ACTIONS).map(([k, v]) =>
            `<button type="button" data-add="${k}"><span>${v.icon}</span>${v.label}</button>`).join('');
        $('auAddBtn').addEventListener('click', () => $('auAddMenu').classList.toggle('hidden'));
        $('auAddMenu').addEventListener('click', (e) => {
            const b = e.target.closest('[data-add]');
            if (!b) return;
            STATE.editing.actions.push(ACTIONS[b.dataset.add].make());
            $('auAddMenu').classList.add('hidden');
            renderActions();
        });
        $('auActions').addEventListener('click', (e) => {
            const list = STATE.editing.actions;
            const mv = e.target.closest('[data-move]'), rm = e.target.closest('[data-remove]');
            const sg = e.target.closest('.au-seg [data-k]');
            if (mv) {
                const i = +mv.dataset.move, j = i + +mv.dataset.dir;
                [list[i], list[j]] = [list[j], list[i]];
                renderActions();
            } else if (rm) {
                list.splice(+rm.dataset.remove, 1);
                renderActions();
            } else if (sg) {
                const a = list[+sg.dataset.a], k = sg.dataset.k;
                a[k] = k === 'on' ? sg.dataset.v === 'true' : sg.dataset.v;
                if (k === 'mode' && a.mode !== 'off' && !a.brightness) a.brightness = 70;
                renderActions();
            }
        });
        $('auActions').addEventListener('input', (e) => { if (e.target.dataset.k) onActionInput(e.target); });
        $('auActions').addEventListener('change', (e) => { if (e.target.dataset.k) onActionInput(e.target); });
    }

    wire();
    renderTemplates();
    load();
})();
