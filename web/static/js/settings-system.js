// ===== SETĂRI → SISTEM: repornire + resetări cu backup =====

(function () {
    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    async function api(url, body) {
        const opt = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
        const r = await fetch(url, opt);
        const d = await r.json().catch(() => ({}));
        if (!r.ok || d.status === 'error') throw new Error(d.message || `HTTP ${r.status}`);
        return d;
    }

    function fmtDate(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        return d.toLocaleString('ro-RO', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
    }

    async function load() {
        let d;
        try { d = await api('/api/system/resets'); }
        catch (e) { $('sysResets').innerHTML = `<div class="empty-state">Nu pot încărca: ${esc(e.message)}</div>`; return; }

        $('sysRestartBtn').disabled = !d.can_restart;
        if (!d.can_restart) $('sysRestartBtn').title = 'Dashboard-ul rulează fără Chronos — n-am ce reporni.';

        $('sysResets').innerHTML = d.resets.map((r) => `
            <div class="sys-row ${r.present ? '' : 'absent'}">
                <div class="sys-row-main">
                    <div class="sys-row-title">${esc(r.title)}</div>
                    <div class="sys-row-desc">${esc(r.description)}</div>
                    ${r.needs_restart ? '<div class="sys-row-meta">Repornește Chronos automat după reset.</div>' : ''}
                    ${r.present ? '' : '<div class="sys-row-meta">Deja la valorile inițiale.</div>'}
                </div>
                <button class="btn btn-sm btn-danger" type="button" data-reset="${esc(r.key)}" data-title="${esc(r.title)}" ${r.present ? '' : 'disabled'}>Resetează</button>
            </div>`).join('');

        $('sysBackups').innerHTML = d.backups.length ? d.backups.map((b) => `
            <div class="sys-row">
                <div class="sys-row-main">
                    <div class="sys-row-title">${esc(b.title)}</div>
                    <div class="sys-row-meta">${esc(fmtDate(b.created_at))} · ${esc(b.files.join(', '))}</div>
                </div>
                <button class="btn btn-sm" type="button" data-restore="${esc(b.name)}" data-title="${esc(b.title)}">Restaurează</button>
            </div>`).join('') : '<div class="empty-state">Nicio copie încă — apar aici după primul reset.</div>';
    }

    // ── Repornire: așteaptă să cadă serverul, apoi să revină, apoi reîncarcă ──
    async function waitForRestart(title) {
        $('sysOverlayTitle').textContent = title || 'Repornesc Chronos…';
        $('sysOverlay').classList.remove('hidden');
        const ping = () => fetch('/api/terminal/ping', { cache: 'no-store' }).then((r) => r.ok).catch(() => false);
        const start = Date.now();
        let wentDown = false;
        while (Date.now() - start < 120000) {
            await new Promise((r) => setTimeout(r, 1000));
            const up = await ping();
            if (!up) wentDown = true;
            // După ce a căzut și a revenit (sau după 25s, dacă repornirea a fost prea rapidă ca s-o prindem)
            if (up && (wentDown || Date.now() - start > 25000)) { location.reload(); return; }
            if (Date.now() - start > 30000) $('sysOverlaySub').textContent = 'Durează mai mult decât de obicei… (pe Pi poate dura până la un minut)';
        }
        $('sysOverlayTitle').textContent = 'Nu a revenit încă';
        $('sysOverlaySub').innerHTML = 'Verifică pe Pi: <code>systemctl status chronos</code>. Reîncarcă pagina când e gata.';
    }

    async function restart() {
        if (!confirm('Repornești Chronos? Durează câteva secunde.')) return;
        try {
            await api('/api/system/restart', {});
            waitForRestart();
        } catch (e) { flash(e.message, 'error'); }
    }

    async function doReset(key, title) {
        if (!confirm(`Resetezi „${title.replace(/^\S+\s/, '')}”? Se face o copie înainte.`)) return;
        try {
            const r = await api('/api/system/reset', { key });
            flash(r.message);
            if (r.restarting) waitForRestart('Resetat — repornesc Chronos…');
            else load();
        } catch (e) { flash(e.message, 'error'); }
    }

    async function doRestore(name, title) {
        if (!confirm(`Restaurezi copia pentru „${title.replace(/^\S+\s/, '')}”? Starea de acum se salvează și ea.`)) return;
        try {
            const r = await api('/api/system/restore', { name });
            flash(r.message);
            if (r.restarting) waitForRestart('Restaurat — repornesc Chronos…');
            else load();
        } catch (e) { flash(e.message, 'error'); }
    }

    function init() {
        if (!$('panelSystem')) return;
        $('sysRestartBtn').addEventListener('click', restart);
        $('panelSystem').addEventListener('click', (e) => {
            const r = e.target.closest('[data-reset]');
            const b = e.target.closest('[data-restore]');
            if (r) doReset(r.dataset.reset, r.dataset.title);
            if (b) doRestore(b.dataset.restore, b.dataset.title);
        });
        load();
    }

    document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', init) : init();
})();
