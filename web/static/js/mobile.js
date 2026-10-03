// ===== mobile.js — comportamente pentru telefon =====
// Mic și fără bucle: doar ascultători de evenimente (bugetul de CPU pe Pi
// nu e afectat — totul rulează în browserul telefonului, și doar la acțiuni).
//
// 1. Cât e deschis un modal, pagina din spate nu se mai derulează.
// 2. Cât scrii (tastatura e sus), bulina Chronos dispare ca să nu acopere.
// 3. Tabul ales se centrează în bara de taburi; estomparea din dreapta
//    dispare când ai ajuns la capăt.

(function () {
    const root = document.documentElement;
    const OVERLAYS = '.modal-overlay.open, .scene-modal-overlay.open, .scene-modal-overlay.active, .chronos-panel.open, .sheet.open';

    // ── 1. Blocarea derulării sub modale ──
    function syncModal() {
        root.classList.toggle('modal-open', !!document.querySelector(OVERLAYS));
    }
    new MutationObserver(syncModal).observe(document.body, {
        subtree: true, attributes: true, attributeFilter: ['class'],
    });

    // Atingerea pe fundalul întunecat închide modalul (pe telefon nu ai ESC).
    document.addEventListener('click', (e) => {
        const ov = e.target;
        if (!ov.classList || !ov.classList.contains('modal-overlay') || !ov.classList.contains('open')) return;
        if (typeof closeModal === 'function' && ov.id) closeModal(ov.id);
        else ov.classList.remove('open');
    });

    // ── 2. Tastatura ──
    const isField = (el) => el && (el.tagName === 'TEXTAREA' || el.isContentEditable ||
        (el.tagName === 'INPUT' && !['checkbox', 'radio', 'range', 'button', 'submit', 'color', 'file'].includes(el.type)));
    document.addEventListener('focusin', (e) => { if (isField(e.target)) root.classList.add('kb-open'); });
    document.addEventListener('focusout', () => setTimeout(() => {
        if (!isField(document.activeElement)) root.classList.remove('kb-open');
    }, 50));

    // ── 3. Barele de taburi ──
    const BARS = '.seg-tabs, .page-tab-bar, .fin-tabs, .elab-tabs';
    function markEnd(bar) {
        bar.classList.toggle('at-end', bar.scrollLeft + bar.clientWidth >= bar.scrollWidth - 4);
    }
    document.querySelectorAll(BARS).forEach((bar) => {
        markEnd(bar);
        bar.addEventListener('scroll', () => markEnd(bar), { passive: true });
    });
    document.addEventListener('click', (e) => {
        const tab = e.target.closest('.seg-tab, .page-tab, .fin-tab, .elab-tab');
        const bar = tab && tab.closest(BARS);
        if (!bar || bar.scrollWidth <= bar.clientWidth) return;
        bar.scrollTo({ left: tab.offsetLeft - (bar.clientWidth - tab.offsetWidth) / 2, behavior: 'smooth' });
    });
})();
