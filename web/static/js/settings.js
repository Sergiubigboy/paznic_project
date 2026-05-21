// ===== SETTINGS JS =====

let currentFile = null;
let originalContent = '';
let isDirty = false;

// ============ INIT ============
document.addEventListener('DOMContentLoaded', () => {
    loadFileTree();
});

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
