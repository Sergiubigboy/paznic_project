// ===== TERMINAL JS =====

const output = document.getElementById('terminalOutput');
let cmdHistory = [];
let historyIdx = -1;
let pendingEntryPhotoFiles = [];
let currentEntryPhotoDate = null;

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

    // Add to history
    cmdHistory.push(text);
    historyIdx = -1;
    input.value = '';
    input.style.height = '';

    // Show user line
    appendLine('You', text, 'user');

    // Show thinking
    const thinking = showThinking();

    try {
        const res = await fetch('/api/terminal/command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text })
        }).then(r => r.json());

        removeThinking();

        if (res.status === 'error') {
            appendLine('ERROR', res.message || 'Eroare necunoscută', 'error');
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

        // Show response
        if (res.reply) {
            appendLine('Chronos', res.reply, 'system');
        }

        if (res.actions) {
            res.actions.forEach(a => appendLine('→', a, 'info'));
        }

        if (!res.reply && !res.actions) {
            appendLine('→', 'Comandă procesată.', 'info');
        }

    } catch (e) {
        removeThinking();
        appendLine('ERROR', 'Nu m-am putut conecta la dispatcher.', 'error');
        updateStatus(false);
    }
}

function updateStatus(connected) {
    const dot = document.getElementById('statusDot');
    const txt = document.getElementById('statusText');
    if (dot) dot.style.background = connected ? 'var(--green)' : 'var(--red)';
    if (txt) txt.textContent = connected ? 'Conectat' : 'Deconectat';
}

// Ping to check connection
async function pingServer() {
    try {
        await fetch('/api/terminal/ping');
        updateStatus(true);
    } catch {
        updateStatus(false);
    }
}
pingServer();
setInterval(pingServer, 30000);
