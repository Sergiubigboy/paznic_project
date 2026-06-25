/* =====================================================
   CHRONOS OS — Global Animations Engine
   Premium 2026 Visual Effects
   ===================================================== */

(function() {
    'use strict';

    // ═══════════════════════════════════════════════════
    // 1. BACKGROUND PARTICLE SYSTEM
    // ═══════════════════════════════════════════════════
    const ParticleSystem = {
        canvas: null,
        ctx: null,
        particles: [],
        mouse: { x: -1000, y: -1000 },
        animFrame: null,

        init() {
            this.canvas = document.getElementById('bgParticles');
            if (!this.canvas) return;
            this.ctx = this.canvas.getContext('2d');
            this.resize();
            this.createParticles();
            this.animate();

            window.addEventListener('resize', () => this.resize());
            document.addEventListener('mousemove', (e) => {
                this.mouse.x = e.clientX;
                this.mouse.y = e.clientY;
            });
        },

        resize() {
            this.canvas.width = window.innerWidth;
            this.canvas.height = document.documentElement.scrollHeight;
        },

        createParticles() {
            const count = Math.min(Math.floor((window.innerWidth * window.innerHeight) / 18000), 80);
            this.particles = [];
            for (let i = 0; i < count; i++) {
                this.particles.push({
                    x: Math.random() * this.canvas.width,
                    y: Math.random() * this.canvas.height,
                    size: Math.random() * 2 + 0.5,
                    speedX: (Math.random() - 0.5) * 0.3,
                    speedY: (Math.random() - 0.5) * 0.2,
                    opacity: Math.random() * 0.5 + 0.1,
                    hue: Math.random() > 0.7 ? 260 : (Math.random() > 0.5 ? 170 : 220),
                    pulse: Math.random() * Math.PI * 2,
                    pulseSpeed: Math.random() * 0.02 + 0.005
                });
            }
        },

        animate() {
            this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
            const scrollY = window.scrollY;

            for (const p of this.particles) {
                p.x += p.speedX;
                p.y += p.speedY;
                p.pulse += p.pulseSpeed;

                // Wrap around
                if (p.x < 0) p.x = this.canvas.width;
                if (p.x > this.canvas.width) p.x = 0;
                if (p.y < 0) p.y = this.canvas.height;
                if (p.y > this.canvas.height) p.y = 0;

                // Mouse interaction — subtle push
                const dx = p.x - this.mouse.x;
                const dy = p.y - (this.mouse.y + scrollY);
                const dist = Math.sqrt(dx * dx + dy * dy);
                if (dist < 120) {
                    const force = (120 - dist) / 120 * 0.5;
                    p.x += (dx / dist) * force;
                    p.y += (dy / dist) * force;
                }

                const alpha = p.opacity * (0.6 + 0.4 * Math.sin(p.pulse));
                this.ctx.beginPath();
                this.ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
                this.ctx.fillStyle = `hsla(${p.hue}, 80%, 70%, ${alpha})`;
                this.ctx.fill();

                // Glow
                this.ctx.beginPath();
                this.ctx.arc(p.x, p.y, p.size * 3, 0, Math.PI * 2);
                this.ctx.fillStyle = `hsla(${p.hue}, 80%, 70%, ${alpha * 0.15})`;
                this.ctx.fill();
            }

            // Draw connections between close particles
            for (let i = 0; i < this.particles.length; i++) {
                for (let j = i + 1; j < this.particles.length; j++) {
                    const a = this.particles[i];
                    const b = this.particles[j];
                    const dx = a.x - b.x;
                    const dy = a.y - b.y;
                    const dist = Math.sqrt(dx * dx + dy * dy);
                    if (dist < 100) {
                        const alpha = (1 - dist / 100) * 0.08;
                        this.ctx.beginPath();
                        this.ctx.moveTo(a.x, a.y);
                        this.ctx.lineTo(b.x, b.y);
                        this.ctx.strokeStyle = `hsla(260, 60%, 60%, ${alpha})`;
                        this.ctx.lineWidth = 0.5;
                        this.ctx.stroke();
                    }
                }
            }

            this.animFrame = requestAnimationFrame(() => this.animate());
        }
    };

    // ═══════════════════════════════════════════════════
    // 2. SCROLL-TRIGGERED ANIMATIONS (IntersectionObserver)
    // ═══════════════════════════════════════════════════
    const ScrollAnimations = {
        init() {
            const observer = new IntersectionObserver((entries) => {
                entries.forEach((entry, index) => {
                    if (entry.isIntersecting) {
                        const el = entry.target;
                        const delay = el.dataset.animDelay || 0;
                        setTimeout(() => {
                            el.classList.add('anim-visible');
                        }, delay);
                        observer.unobserve(el);
                    }
                });
            }, { threshold: 0.08, rootMargin: '0px 0px -40px 0px' });

            // Auto-detect elements to animate
            const selectors = [
                '.card', '.gym-card', '.sidebar-card', '.target-card',
                '.home-menu-card', '.home-quick-card', '.home-alert-item',
                '.home-maint-item', '.home-target-item',
                '.elab-proj-card', '.elab-table-wrap',
                '.fin-acc-card', '.fin-sum-card', '.fin-inv-card', '.fin-debt-card',
                '.fin-chart-section', '.fin-inv-stat-card',
                '.section-header', '.home-section-label',
                '.phase-banner', '.summary-card',
                '.journal-input-card', '.day-group',
                '.entry-card'
            ];

            document.querySelectorAll(selectors.join(',')).forEach((el, i) => {
                if (!el.classList.contains('anim-item')) {
                    el.classList.add('anim-item');
                    el.dataset.animDelay = Math.min(i * 60, 400);
                    observer.observe(el);
                }
            });
        }
    };

    // ═══════════════════════════════════════════════════
    // 3. ANIMATED COUNTERS
    // ═══════════════════════════════════════════════════
    const AnimatedCounters = {
        init() {
            const observer = new IntersectionObserver((entries) => {
                entries.forEach(entry => {
                    if (entry.isIntersecting) {
                        this.animateCounter(entry.target);
                        observer.unobserve(entry.target);
                    }
                });
            }, { threshold: 0.5 });

            document.querySelectorAll('[data-counter]').forEach(el => {
                observer.observe(el);
            });
        },

        animateCounter(el) {
            const target = parseFloat(el.dataset.counter);
            const duration = 1200;
            const start = performance.now();
            const isDecimal = target % 1 !== 0;

            const step = (now) => {
                const elapsed = now - start;
                const progress = Math.min(elapsed / duration, 1);
                // Ease out cubic
                const eased = 1 - Math.pow(1 - progress, 3);
                const current = target * eased;
                el.textContent = isDecimal ? current.toFixed(1) : Math.round(current);
                if (progress < 1) requestAnimationFrame(step);
            };
            requestAnimationFrame(step);
        }
    };

    // ═══════════════════════════════════════════════════
    // 4. CURSOR GLOW FOLLOWER
    // ═══════════════════════════════════════════════════
    const CursorGlow = {
        glow: null,

        init() {
            // Don't init on mobile/touch
            if ('ontouchstart' in window || window.innerWidth < 768) return;

            this.glow = document.createElement('div');
            this.glow.className = 'cursor-glow';
            document.body.appendChild(this.glow);

            let rafId = null;
            let targetX = 0, targetY = 0;
            let currentX = 0, currentY = 0;

            document.addEventListener('mousemove', (e) => {
                targetX = e.clientX;
                targetY = e.clientY;
            });

            const render = () => {
                currentX += (targetX - currentX) * 0.08;
                currentY += (targetY - currentY) * 0.08;
                this.glow.style.transform = `translate(${currentX - 200}px, ${currentY - 200}px)`;
                rafId = requestAnimationFrame(render);
            };
            render();
        }
    };

    // ═══════════════════════════════════════════════════
    // 5. CARD TILT EFFECT (3D Perspective)
    // ═══════════════════════════════════════════════════
    const CardTilt = {
        init() {
            if ('ontouchstart' in window) return;

            document.querySelectorAll('.tilt-card').forEach(card => {
                card.addEventListener('mousemove', (e) => {
                    const rect = card.getBoundingClientRect();
                    const x = e.clientX - rect.left;
                    const y = e.clientY - rect.top;
                    const centerX = rect.width / 2;
                    const centerY = rect.height / 2;
                    const rotateX = (y - centerY) / centerY * -4;
                    const rotateY = (x - centerX) / centerX * 4;

                    card.style.transform = `perspective(800px) rotateX(${rotateX}deg) rotateY(${rotateY}deg) translateY(-4px)`;
                });

                card.addEventListener('mouseleave', () => {
                    card.style.transform = 'perspective(800px) rotateX(0deg) rotateY(0deg) translateY(0)';
                });
            });
        }
    };

    // ═══════════════════════════════════════════════════
    // 6. STAGGER LIST ITEMS
    // ═══════════════════════════════════════════════════
    const StaggerLists = {
        init() {
            const observer = new IntersectionObserver((entries) => {
                entries.forEach(entry => {
                    if (entry.isIntersecting) {
                        const container = entry.target;
                        const items = container.querySelectorAll('.stagger-item');
                        items.forEach((item, i) => {
                            setTimeout(() => {
                                item.classList.add('anim-visible');
                            }, i * 80);
                        });
                        observer.unobserve(container);
                    }
                });
            }, { threshold: 0.1 });

            document.querySelectorAll('.stagger-list').forEach(list => {
                observer.observe(list);
            });
        }
    };

    // ═══════════════════════════════════════════════════
    // 7. SMOOTH PAGE LOAD
    // ═══════════════════════════════════════════════════
    const PageLoad = {
        init() {
            document.body.classList.add('page-loading');
            requestAnimationFrame(() => {
                requestAnimationFrame(() => {
                    document.body.classList.remove('page-loading');
                    document.body.classList.add('page-loaded');
                });
            });
        }
    };

    // ═══════════════════════════════════════════════════
    // 8. MAGNETIC BUTTONS
    // ═══════════════════════════════════════════════════
    const MagneticButtons = {
        init() {
            if ('ontouchstart' in window) return;

            document.querySelectorAll('.btn-primary, .btn-teal').forEach(btn => {
                btn.addEventListener('mousemove', (e) => {
                    const rect = btn.getBoundingClientRect();
                    const x = e.clientX - rect.left - rect.width / 2;
                    const y = e.clientY - rect.top - rect.height / 2;
                    btn.style.transform = `translate(${x * 0.15}px, ${y * 0.15}px)`;
                });
                btn.addEventListener('mouseleave', () => {
                    btn.style.transform = '';
                });
            });
        }
    };

    // ═══════════════════════════════════════════════════
    // 9. RESIZE PARTICLE CANVAS ON SCROLL
    // ═══════════════════════════════════════════════════
    let resizeTimeout;
    const handleResize = () => {
        clearTimeout(resizeTimeout);
        resizeTimeout = setTimeout(() => {
            if (ParticleSystem.canvas) {
                ParticleSystem.canvas.height = document.documentElement.scrollHeight;
            }
        }, 200);
    };

    // ═══════════════════════════════════════════════════
    // INIT ALL
    // ═══════════════════════════════════════════════════
    function initAnimations() {
        PageLoad.init();
        ParticleSystem.init();
        ScrollAnimations.init();
        AnimatedCounters.init();
        CursorGlow.init();
        CardTilt.init();
        StaggerLists.init();
        MagneticButtons.init();

        window.addEventListener('scroll', handleResize, { passive: true });

        // Re-init on dynamic content load (for AJAX pages)
        window.chronosAnimations = {
            refreshScroll: () => ScrollAnimations.init(),
            refreshCounters: () => AnimatedCounters.init(),
            refreshTilt: () => CardTilt.init(),
            refreshStagger: () => StaggerLists.init(),
            refreshParticles: () => {
                if (ParticleSystem.canvas) {
                    ParticleSystem.resize();
                }
            }
        };
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initAnimations);
    } else {
        initAnimations();
    }

})();
