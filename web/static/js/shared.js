// ===== SHARED UTILITIES =====

// Flash/Toast
function flash(msg, type = 'success') {
    const el = document.getElementById('flashMsg');
    el.textContent = msg;
    el.className = `flash-msg show ${type}`;
    setTimeout(() => el.className = 'flash-msg', 3500);
}

// Lightbox
function openLightbox(url, caption) {
    const lb = document.getElementById('lightbox');
    if (!lb) return;
    document.getElementById('lightboxImg').src = url;
    document.getElementById('lightboxCaption').textContent = caption || '';
    lb.classList.add('open');
}
function closeLightbox() {
    const lb = document.getElementById('lightbox');
    if (lb) lb.classList.remove('open');
}
const lb = document.getElementById('lightbox');
if (lb) lb.addEventListener('click', e => { if (e.target === lb) closeLightbox(); });

// Close modal
function closeModal(id) {
    const el = document.getElementById(id);
    if (el) el.classList.remove('open');
}

// Offset date
function offsetDate(days) {
    const d = new Date();
    d.setDate(d.getDate() + days);
    // Corectie pentru fusul orar local
    const tzOffset = d.getTimezoneOffset() * 60000;
    const localISOTime = (new Date(d - tzOffset)).toISOString().slice(0, 10);
    return localISOTime;
}


// Format date
function fmtDate(dateStr) {
    if (!dateStr) return '';
    const [y, m, d] = dateStr.split('-').map(Number);
    return new Date(y, m - 1, d).toLocaleDateString('ro-RO', {
        weekday: 'long', year: 'numeric', month: 'long', day: 'numeric'
    });
}
function fmtDateShort(dateStr) {
    if (!dateStr) return '';
    const [y, m, d] = dateStr.split('-').map(Number);
    return new Date(y, m - 1, d).toLocaleDateString('ro-RO', {
        day: 'numeric', month: 'short', year: 'numeric'
    });
}

// Hamburger nav
const hamburgerBtn = document.getElementById('hamburgerBtn');
const mobileNav = document.getElementById('mobileNav');
if (hamburgerBtn && mobileNav) {
    hamburgerBtn.addEventListener('click', () => {
        hamburgerBtn.classList.toggle('open');
        mobileNav.classList.toggle('open');
    });
    // Close on nav link click
    mobileNav.querySelectorAll('a').forEach(a => {
        a.addEventListener('click', () => {
            hamburgerBtn.classList.remove('open');
            mobileNav.classList.remove('open');
        });
    });
    // Close on outside click
    document.addEventListener('click', (e) => {
        if (!hamburgerBtn.contains(e.target) && !mobileNav.contains(e.target)) {
            hamburgerBtn.classList.remove('open');
            mobileNav.classList.remove('open');
        }
    });
}

// Auto-resize textarea
document.querySelectorAll('textarea').forEach(ta => {
    ta.addEventListener('input', function() {
        if (this.id === 'terminalInput') {
            this.style.height = 'auto';
            this.style.height = Math.min(this.scrollHeight, 120) + 'px';
        }
    });
});

// Close any modal-overlay on bg click
document.querySelectorAll('.modal-overlay').forEach(o => {
    o.addEventListener('click', function(e) {
        if (e.target === this) this.classList.remove('open');
    });
});
