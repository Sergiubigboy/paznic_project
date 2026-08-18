/* ══════════════════════════════════════════════════════════════
   CHRONOS OS — animations.js (v3)
   ──────────────────────────────────────────────────────────────
   Motorul vechi (canvas cu particule + O(n²) linii de legătură,
   cursor-glow și magnetic buttons pe rAF permanent) a fost ELIMINAT:
   ținea un core ocupat non-stop, inacceptabil pe Raspberry Pi.

   Tot ce mai era util — reveal la scroll și contoarele animate —
   trăiește acum în chronos-ui.js, pe IntersectionObserver, one-shot.
   Fișierul rămâne ca shim pentru paginile care încă îl încarcă.
   ══════════════════════════════════════════════════════════════ */
'use strict';

(function () {
    if (window.chronosAnimations) return;   // chronos-ui.js l-a definit deja
    window.chronosAnimations = {
        refreshScroll: function () {},
        refreshCounters: function () {},
        refreshTilt: function () {},
        refreshStagger: function () {},
        refreshParticles: function () {}
    };
})();
