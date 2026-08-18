// ===== SETTINGS JS =====

let currentFile = null;
let originalContent = '';
let isDirty = false;

// ============ INIT ============
document.addEventListener('DOMContentLoaded', () => {
    loadFileTree();
    initAppearance();
    const h = (location.hash || '').slice(1);
    if (h === 'appearance') switchSettingsTab('appearance');
});

// ============ TABURI ============
const SETTINGS_TABS = ['files', 'appearance'];
function _capTab(s) { return s.charAt(0).toUpperCase() + s.slice(1); }
function switchSettingsTab(name) {
    if (!SETTINGS_TABS.includes(name)) return;
    SETTINGS_TABS.forEach(t => {
        document.getElementById('panel' + _capTab(t))?.classList.toggle('active', t === name);
        document.getElementById('tab' + _capTab(t))?.classList.toggle('active', t === name);
    });
    try { history.replaceState(null, '', '#' + name); } catch (e) {}
}

async function loadFileTree() {
    try {
        const res = await fetch('/api/settings/tree');
        const data = await res.json();
        renderConfigTree(data.config_files || []);
        renderDataTree(data.data_files || []);
    } catch (e) {
        console.error('Error loading file tree:', e);
    }
}

function renderConfigTree(files) {
    const el = document.getElementById('configTree');
    el.innerHTML = files.map(f => fileTreeItem(f)).join('');
}

function renderDataTree(files) {
    const el = document.getElementById('dataTree');
    el.innerHTML = buildTreeHtml(files, 0);
}

function buildTreeHtml(items, depth) {
    if (!items) return '';
    return items.map(item => {
        if (item.type === 'dir') {
            const isOpen = depth === 0; // auto-expand top level
            return `
                <div class="file-tree-dir ${isOpen ? 'open' : ''}" data-depth="${depth}">
                    <div class="file-tree-dir-header" onclick="toggleDir(this)">
                        <span class="ftd-arrow">${isOpen ? '▾' : '▸'}</span>
                        <span class="ftd-icon">📁</span>
                        <span class="ftd-name">${item.name}/</span>
                    </div>
                    <div class="file-tree-children" ${isOpen ? '' : 'style="display:none"'}>
                        ${buildTreeHtml(item.children, depth + 1)}
                    </div>
                </div>`;
        } else {
            return fileTreeItem(item);
        }
    }).join('');
}

function fileTreeItem(f) {
    const icon = getFileIcon(f.name, f.ext);
    return `
        <div class="file-tree-item" data-path="${f.path}" onclick="openFile('${f.path}', '${f.ext}')">
            <span class="fti-icon">${icon}</span>
            <span class="fti-name">${f.name}</span>
            ${f.size ? `<span class="fti-size">${formatSize(f.size)}</span>` : ''}
        </div>`;
}

function getFileIcon(name, ext) {
    if (ext === 'json') return '📋';
    if (ext === 'py') return '🐍';
    if (ext === 'jsonl') return '📜';
    if (ext === 'txt') return '📝';
    return '📄';
}

function formatSize(bytes) {
    if (bytes < 1024) return bytes + 'B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + 'KB';
    return (bytes / 1024 / 1024).toFixed(1) + 'MB';
}

function toggleDir(header) {
    const dir = header.parentElement;
    const children = dir.querySelector('.file-tree-children');
    const arrow = header.querySelector('.ftd-arrow');
    const isOpen = dir.classList.contains('open');
    dir.classList.toggle('open', !isOpen);
    children.style.display = isOpen ? 'none' : '';
    arrow.textContent = isOpen ? '▸' : '▾';
}

// ============ FILE OPEN ============
async function openFile(path, ext) {
    if (isDirty) {
        const ok = confirm('Ai modificări nesalvate. Continui fără să salvezi?');
        if (!ok) return;
    }

    // Highlight active
    document.querySelectorAll('.file-tree-item').forEach(el => el.classList.remove('active'));
    const activeEl = document.querySelector(`.file-tree-item[data-path="${CSS.escape(path)}"]`);
    if (activeEl) activeEl.classList.add('active');

    try {
        const res = await fetch(`/api/settings/file?path=${encodeURIComponent(path)}`);
        const data = await res.json();

        if (data.status === 'error') {
            showFlash(data.message, 'error');
            return;
        }

        currentFile = { path, ext, name: path.split(/[\\/]/).pop() };
        originalContent = data.content;
        isDirty = false;

        // Show editor
        document.getElementById('editorEmpty').style.display = 'none';
        document.getElementById('editorActive').style.display = 'flex';

        // Set metadata
        document.getElementById('editorFilePath').textContent = path;
        document.getElementById('editorFileIcon').textContent = getFileIcon(currentFile.name, ext);
        document.getElementById('editorFileType').textContent = ext.toUpperCase();
        document.getElementById('editorSize').textContent = formatSize(data.content.length);
        document.getElementById('editorLastSaved').textContent = 'Nesalvat';
        document.getElementById('editorStatus').textContent = '';
        document.getElementById('editorStatus').className = 'editor-status';

        const textarea = document.getElementById('editorTextarea');
        textarea.value = data.content;
        textarea.focus();

        updateLineNumbers();
        validateContent();
        updateCursorPos();

        // Show/hide format button
        document.getElementById('btnFormat').style.display = ext === 'json' ? '' : 'none';

    } catch (e) {
        showFlash('Eroare la deschiderea fișierului: ' + e.message, 'error');
    }
}

// ============ EDITOR LOGIC ============
function onEditorChange() {
    isDirty = true;
    updateLineNumbers();
    validateContent();
    updateCursorPos();

    const status = document.getElementById('editorStatus');
    status.textContent = '● Modificat';
    status.className = 'editor-status dirty';
}

function handleEditorKey(e) {
    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        saveFile();
        return;
    }
    // Tab → insert 2 spaces
    if (e.key === 'Tab') {
        e.preventDefault();
        const ta = document.getElementById('editorTextarea');
        const start = ta.selectionStart;
        const end = ta.selectionEnd;
        ta.value = ta.value.substring(0, start) + '  ' + ta.value.substring(end);
        ta.selectionStart = ta.selectionEnd = start + 2;
        onEditorChange();
    }
}

function updateLineNumbers() {
    const ta = document.getElementById('editorTextarea');
    const lines = ta.value.split('\n').length;
    const lineNums = document.getElementById('lineNums');
    lineNums.innerHTML = Array.from({length: lines}, (_, i) => `<div>${i + 1}</div>`).join('');
}

function syncScroll() {
    const ta = document.getElementById('editorTextarea');
    const ln = document.getElementById('lineNums');
    ln.scrollTop = ta.scrollTop;
}

function updateCursorPos() {
    const ta = document.getElementById('editorTextarea');
    const text = ta.value.substring(0, ta.selectionStart);
    const lines = text.split('\n');
    const line = lines.length;
    const col = lines[lines.length - 1].length + 1;
    document.getElementById('editorLineCol').textContent = `Ln ${line}, Col ${col}`;
}

function validateContent() {
    if (!currentFile || currentFile.ext !== 'json') {
        document.getElementById('editorWarning').style.display = 'none';
        return true;
    }
    const ta = document.getElementById('editorTextarea');
    const warn = document.getElementById('editorWarning');
    try {
        JSON.parse(ta.value);
        warn.style.display = 'none';
        return true;
    } catch (e) {
        warn.style.display = 'flex';
        document.getElementById('editorWarningText').textContent = 'JSON invalid: ' + e.message;
        return false;
    }
}

function formatEditor() {
    if (!currentFile || currentFile.ext !== 'json') return;
    const ta = document.getElementById('editorTextarea');
    try {
        const parsed = JSON.parse(ta.value);
        ta.value = JSON.stringify(parsed, null, 2);
        onEditorChange();
        showFlash('JSON formatat cu succes!', 'success');
    } catch (e) {
        showFlash('Nu pot formata — JSON invalid: ' + e.message, 'error');
    }
}

// ============ SAVE ============
async function saveFile() {
    if (!currentFile) return;

    if (currentFile.ext === 'json' && !validateContent()) {
        const ok = confirm('JSON-ul e invalid. Salvezi oricum?');
        if (!ok) return;
    }

    const ta = document.getElementById('editorTextarea');
    const content = ta.value;

    try {
        const res = await fetch('/api/settings/file', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: currentFile.path, content })
        });
        const data = await res.json();

        if (data.status === 'success') {
            originalContent = content;
            isDirty = false;
            const status = document.getElementById('editorStatus');
            status.textContent = '✓ Salvat';
            status.className = 'editor-status saved';
            const now = new Date().toLocaleTimeString('ro-RO', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
            document.getElementById('editorLastSaved').textContent = `Salvat la ${now}`;
            document.getElementById('editorSize').textContent = formatSize(content.length);
            showFlash('Fișier salvat cu succes!', 'success');
        } else {
            showFlash('Eroare la salvare: ' + data.message, 'error');
        }
    } catch (e) {
        showFlash('Eroare rețea: ' + e.message, 'error');
    }
}

// ============ FLASH ============
function showFlash(msg, type = 'success') {
    const el = document.getElementById('flashMsg');
    if (!el) return;
    el.textContent = msg;
    el.className = `flash-msg show ${type}`;
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove('show'), 3000);
}

// ============ ASPECT (culoarea interfeței) ============
const HEX_RE = /^#[0-9a-f]{6}$/i;

function initAppearance() {
    // Panoul rulează pe Chronos.theme din chronos-ui.js — un singur loc care
    // ține starea, persistă pe server și pornește/oprește poll-ul pentru WLED.
    if (!window.Chronos?.theme) { setTimeout(initAppearance, 200); return; }

    const grid = document.getElementById('themeSwatches');
    if (grid) {
        grid.innerHTML = window.Chronos.theme.PRESETS.map(p =>
            `<div class="theme-swatch" style="background:${p.hex}" data-hex="${p.hex}" title="${p.name}" onclick="pickThemeSwatch('${p.hex}')"></div>`
        ).join('');
    }

    const picker = document.getElementById('themeColorPicker');
    const hexInput = document.getElementById('themeColorHex');

    picker?.addEventListener('input', () => {
        hexInput.value = picker.value;
        window.Chronos.theme.preview(picker.value);
        markActiveSwatch(picker.value);
    });
    picker?.addEventListener('change', () => commitThemeColor(picker.value));

    hexInput?.addEventListener('input', () => {
        const v = hexInput.value.trim();
        if (HEX_RE.test(v)) { picker.value = v; window.Chronos.theme.preview(v); markActiveSwatch(v); }
    });
    hexInput?.addEventListener('keydown', e => { if (e.key === 'Enter') commitThemeColor(hexInput.value.trim()); });
    hexInput?.addEventListener('blur', () => commitThemeColor(hexInput.value.trim()));

    document.addEventListener('chronos:theme', e => renderThemeState(e.detail));

    // Cerere proprie (idempotentă, aceeași sursă ca shell-ul) — nu ne bazăm
    // pe ordinea de execuție față de Chronos.theme.init() din chronos-ui.js.
    window.Chronos.theme.refresh().then(renderThemeState);
}

function renderThemeState(state) {
    if (!state) return;
    document.querySelectorAll('.theme-mode-opt').forEach(b =>
        b.classList.toggle('active', b.dataset.mode === state.mode));

    const manualPanel = document.getElementById('themeManualPanel');
    manualPanel?.classList.toggle('disabled', state.mode !== 'manual');

    const picker = document.getElementById('themeColorPicker');
    const hexInput = document.getElementById('themeColorHex');
    if (picker) picker.value = state.color;
    if (hexInput) hexInput.value = state.color;
    markActiveSwatch(state.color);

    const dot = document.getElementById('themePreviewDot');
    if (dot) dot.style.background = state.resolved;

    const status = document.getElementById('themeStatus');
    if (!status) return;
    status.classList.toggle('warn', !state.live);

    let msg;
    if (state.mode === 'manual') {
        msg = 'Culoare fixă — rămâne așa până o schimbi.';
    } else if (state.mode === 'wled') {
        msg = state.live
            ? 'Live din benzile LED, se actualizează la 25 secunde.'
            : 'WLED e stins sau offline — momentan folosesc ultima culoare aleasă.';
    } else {
        msg = state.live
            ? 'Urmărește starea lui Chronos, se actualizează când vorbești cu el.'
            : 'Nu pot citi starea lui Chronos acum — folosesc ultima culoare aleasă.';
    }
    status.innerHTML = `<span class="ts-dot"></span> ${msg}`;
}

function markActiveSwatch(hex) {
    document.querySelectorAll('.theme-swatch').forEach(s =>
        s.classList.toggle('active', s.dataset.hex.toLowerCase() === (hex || '').toLowerCase()));
}

async function setThemeMode(mode) {
    const d = await window.Chronos.theme.setMode(mode);
    if (d.status === 'success') {
        renderThemeState(d);
        showFlash(
            mode === 'manual' ? '🎨 Mod manual' :
            mode === 'wled'   ? '💡 Urmărește culoarea camerei' :
                                 '🧠 Urmărește starea lui Chronos',
            'success'
        );
    } else showFlash(d.message || 'Eroare', 'error');
}

function pickThemeSwatch(hex) {
    document.getElementById('themeColorPicker').value = hex;
    document.getElementById('themeColorHex').value = hex;
    commitThemeColor(hex);
}

async function commitThemeColor(hex) {
    if (!HEX_RE.test(hex || '')) { showFlash('Cod de culoare invalid', 'error'); return; }
    const d = await window.Chronos.theme.setColor(hex);
    if (d.status === 'success') { renderThemeState(d); showFlash('🎨 Culoare salvată', 'success'); }
    else showFlash(d.message || 'Eroare', 'error');
}
