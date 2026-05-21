// ===== TERMINAL JS =====

const output = document.getElementById('terminalOutput');
let cmdHistory = [];
let historyIdx = -1;
let isConnected = false;

function scrollToBottom() {
    output.scrollTop = output.scrollHeight;
}

function appendLine(prefix, text, type = 'info') {
    const ts = new Date().toLocaleTimeString('ro-RO', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const line = document.createElement('div');
    line.className = `term-line ${type}`;
    line.innerHTML = `<span class="term-prefix">${prefix}</span><span class="term-text">${escapeHtml(text)}</span>`;
    output.appendChild(line);
    scrollToBottom();
}

function appendHTML(prefix, html, type = 'system') {
    const line = document.createElement('div');
    line.className = `term-line ${type}`;
    line.innerHTML = `<span class="term-prefix">${prefix}</span><span class="term-text">${html}</span>`;
    output.appendChild(line);
    scrollToBottom();
}

function escapeHtml(str) {
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function showThinking() {
    const el = document.createElement('div');
    el.className = 'term-thinking';
    el.id = 'thinkingEl';
    el.innerHTML = `<span style="color:var(--text-faint)">Procesez</span> <div class="term-dots"><span></span><span></span><span></span></div>`;
    output.appendChild(el);
    scrollToBottom();
    return el;
}

function removeThinking() {
    const el = document.getElementById('thinkingEl');
    if (el) el.remove();
}

function fillCmd(text) {
    const input = document.getElementById('terminalInput');
    input.value = text;
    input.focus();
}

function handleTermKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendCommand();
        return;
    }
    if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (historyIdx < cmdHistory.length - 1) {
            historyIdx++;
            document.getElementById('terminalInput').value = cmdHistory[cmdHistory.length - 1 - historyIdx];
        }
    }
    if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (historyIdx > 0) {
            historyIdx--;
            document.getElementById('terminalInput').value = cmdHistory[cmdHistory.length - 1 - historyIdx];
        } else {
            historyIdx = -1;
            document.getElementById('terminalInput').value = '';
        }
    }
}

async function sendCommand() {
    const input = document.getElementById('terminalInput');
    const text = input.value.trim();
    if (!text) return;

    if (!isConnected) {
        appendLine('ERROR', 'Serverul nu este conectat. Verifică că aplicația Python rulează.', 'error');
        return;
    }

    // Add to history
    cmdHistory.push(text);
    historyIdx = -1;
    input.value = '';
    input.style.height = '';

    // Show user line
    appendLine('You', text, 'user');

    // Show thinking
    showThinking();

    try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 30000); // 30s timeout

        const res = await fetch('/api/terminal/command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text }),
            signal: controller.signal
        }).then(r => {
            clearTimeout(timeout);
            return r.json();
        });

        removeThinking();

        if (res.status === 'error') {
            appendLine('ERROR', res.message || 'Eroare necunoscută', 'error');
            // Show traceback if available
            if (res.traceback) {
                const traceLines = res.traceback.split('\n').filter(l => l.trim());
                traceLines.slice(-3).forEach(l => appendLine('···', l, 'error'));
            }
            return;
        }

        // Show intents
        if (res.intents && res.intents.length) {
            const INTENT_ICONS = {
                led: '💡', music: '🎵', journal: '📘', target: '🎯',
                study_timer: '⏱️', hype_mode: '🔥', general: '🧠', unknown: '❓'
            };
            const badgesHtml = res.intents.map(i =>
                `<span class="intent-badge ${i}">${INTENT_ICONS[i] || ''} ${i}</span>`
            ).join('');
            appendHTML('Intent', badgesHtml, 'info');
        }

        if (res.reply) {
            appendLine('Chronos', res.reply, 'system');
        }

        // Actions with status coloring
        if (res.actions && res.actions.length > 0) {
            res.actions.forEach(a => {
                // Support both old string format and new {text, status} format
                if (typeof a === 'string') {
                    appendLine('→', a, 'info');
                } else {
                    const lineType = a.status === 'error' ? 'error' : (a.status === 'ok' ? 'success' : 'info');
                    appendLine('→', a.text, lineType);
                }
            });
        }

        if (!res.reply && (!res.actions || res.actions.length === 0)) {
            appendLine('→', 'Comandă procesată.', 'info');
        }

    } catch (e) {
        removeThinking();
        if (e.name === 'AbortError') {
            appendLine('ERROR', 'Timeout — serverul nu a răspuns în 30 secunde. E posibil ca procesarea AI să fie lentă.', 'error');
        } else {
            appendLine('ERROR', 'Nu m-am putut conecta la dispatcher. Verifică că aplicația Chronos rulează.', 'error');
            updateStatus(false);
        }
    }
}

function updateStatus(connected) {
    isConnected = connected;
    const dot = document.getElementById('statusDot');
    const txt = document.getElementById('statusText');
    if (dot) {
        dot.style.background = connected ? 'var(--green)' : 'var(--red)';
        dot.style.boxShadow = connected ? '0 0 8px var(--green)' : '0 0 8px var(--red)';
    }
    if (txt) txt.textContent = connected ? 'Conectat' : 'Deconectat';
}

// Ping to check connection
async function pingServer() {
    try {
        const res = await fetch('/api/terminal/ping');
        if (res.ok) {
            updateStatus(true);
        } else {
            updateStatus(false);
        }
    } catch {
        updateStatus(false);
    }
}

// Initial ping + periodic
pingServer();
setInterval(pingServer, 30000);
