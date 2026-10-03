// ===== ÎNCUIETOAREA JURNALULUI =====
// Scrisul e liber; CITIREA intrărilor cere parola. Deblocarea ține cât ești
// activ pe pagină: după `idle_min` minute fără mouse/tastatură/atingere,
// jurnalul se închide și conținutul dispare din pagină (laptop uitat deschis).
// Cât ești activ, serverul e ținut la curent ca să nu te blocheze în timp ce citești.

window.JournalLock = (function () {
    const $ = (id) => document.getElementById(id);
    let idleMin = 10;
    let lastActivity = Date.now();
    let lastTouch = 0;
    let unlocked = false;
    let hasPassword = true;

    function showLock(info) {
        unlocked = false;
        hasPassword = info ? !!info.has_password : hasPassword;
        $('journalReader').classList.add('hidden');
        $('journalLock').classList.remove('hidden');
        $('journalLockBtn').classList.add('hidden');
        // Ce era afișat dispare cu totul, nu doar se ascunde.
        $('logsContainer').innerHTML = '';
        $('journalPagination').innerHTML = '';
        const first = !hasPassword;
        $('journalLockTitle').textContent = first ? 'Pune o parolă pentru jurnal' : 'Jurnalul e blocat';
        $('journalLockSub').textContent = first
            ? 'O singură dată. După asta, intrările se citesc doar cu ea — scrisul rămâne liber.'
            : 'Poți scrie liniștit mai sus — ca să citești intrările, pune parola.';
        $('journalPw2').classList.toggle('hidden', !first);
        $('journalUnlockBtn').textContent = first ? 'Setează și deschide' : 'Deblochează';
        $('journalPw').value = '';
        $('journalPw2').value = '';
        $('journalLockErr').textContent = '';
    }

    function showReader() {
        unlocked = true;
        lastActivity = Date.now();
        $('journalLock').classList.add('hidden');
        $('journalReader').classList.remove('hidden');
        $('journalLockBtn').classList.remove('hidden');
    }

    async function fetchLogs(month) {
        const r = await fetch(`/api/logs?month=${encodeURIComponent(month)}`);
        if (r.status === 423) {
            showLock(await r.json().catch(() => ({})));
            return null;
        }
        if (!unlocked) showReader();
        return r.json();
    }

    async function lockNow() {
        try { await fetch('/api/journal/lock', { method: 'POST' }); } catch (e) { /* oricum blocăm local */ }
        showLock();
    }

    async function submit(e) {
        e.preventDefault();
        const pw = $('journalPw').value;
        if (!hasPassword && pw !== $('journalPw2').value) {
            $('journalLockErr').textContent = 'Parolele nu sunt la fel.';
            return;
        }
        $('journalUnlockBtn').disabled = true;
        try {
            const r = await fetch('/api/journal/unlock', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ password: pw }),
            });
            const d = await r.json().catch(() => ({}));
            if (!r.ok) {
                $('journalLockErr').textContent = d.message || 'Nu merge.';
                $('journalPw').select();
                return;
            }
            if (d.created) flash('🔒 Parola jurnalului a fost setată.');
            hasPassword = true;
            showReader();
            if (typeof loadLogsForMonth === 'function') loadLogsForMonth(currentMonth);
        } finally {
            $('journalUnlockBtn').disabled = false;
        }
    }

    function onActivity() {
        lastActivity = Date.now();
        // Cât citești (fără cereri noi), ținem deblocarea vie pe server.
        if (unlocked && Date.now() - lastTouch > 120000) {
            lastTouch = Date.now();
            fetch('/api/journal/lock-status?touch=1').then((r) => r.json()).then((d) => {
                if (d.locked) showLock(d);
            }).catch(() => {});
        }
    }

    function checkIdle() {
        if (unlocked && Date.now() - lastActivity > idleMin * 60000) lockNow();
    }

    function init() {
        $('journalLockForm').addEventListener('submit', submit);
        $('journalLockBtn').addEventListener('click', lockNow);
        ['pointerdown', 'keydown', 'scroll', 'touchstart', 'mousemove'].forEach((ev) =>
            document.addEventListener(ev, onActivity, { passive: true }));
        // Verificare rară (o dată la 30s) — și imediat când revii pe tab.
        setInterval(checkIdle, 30000);
        document.addEventListener('visibilitychange', () => { if (!document.hidden) checkIdle(); });
        fetch('/api/journal/lock-status').then((r) => r.json()).then((d) => {
            idleMin = d.idle_min || idleMin;
            hasPassword = !!d.has_password;
        }).catch(() => {});
    }

    document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', init) : init();
    return { fetchLogs, lockNow, showLock };
})();
