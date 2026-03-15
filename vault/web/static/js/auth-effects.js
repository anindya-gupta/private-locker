(() => {
    'use strict';

    const W = () => window.innerWidth;
    const H = () => window.innerHeight;
    const isMobile = () => W() < 768;
    const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    let mouse = { x: W() / 2, y: H() / 2, active: false };

    // ====== Particle Canvas (background ambient particles) ======
    const pCanvas = document.createElement('canvas');
    pCanvas.id = 'auth-particles';
    pCanvas.style.cssText = 'position:fixed;inset:0;z-index:0;pointer-events:none;';
    document.body.prepend(pCanvas);
    const pCtx = pCanvas.getContext('2d');

    let cw, ch;
    const bgParticles = [];
    const BG_COUNT = isMobile() ? 20 : 40;

    function resizeCanvas() {
        cw = pCanvas.width = W();
        ch = pCanvas.height = H();
    }

    class BGParticle {
        constructor() { this.init(); }
        init() {
            this.x = Math.random() * cw;
            this.y = Math.random() * ch;
            this.r = Math.random() * 1.2 + 0.3;
            this.vx = (Math.random() - 0.5) * 0.15;
            this.vy = (Math.random() - 0.5) * 0.15;
            this.baseAlpha = Math.random() * 0.25 + 0.05;
            this.alpha = this.baseAlpha;
            this.hue = Math.random() > 0.6 ? 43 : 155;
        }
        update() {
            this.x += this.vx;
            this.y += this.vy;
            if (mouse.active) {
                const dx = mouse.x - this.x;
                const dy = mouse.y - this.y;
                const dist = Math.sqrt(dx * dx + dy * dy);
                if (dist < 150) {
                    const force = (150 - dist) / 150;
                    this.alpha = this.baseAlpha + force * 0.3;
                    this.x += dx * force * 0.004;
                    this.y += dy * force * 0.004;
                } else {
                    this.alpha += (this.baseAlpha - this.alpha) * 0.04;
                }
            } else {
                this.alpha += (this.baseAlpha - this.alpha) * 0.04;
            }
            if (this.x < -20) this.x = cw + 20;
            if (this.x > cw + 20) this.x = -20;
            if (this.y < -20) this.y = ch + 20;
            if (this.y > ch + 20) this.y = -20;
        }
        draw() {
            pCtx.beginPath();
            pCtx.arc(this.x, this.y, this.r, 0, Math.PI * 2);
            pCtx.fillStyle = `hsla(${this.hue}, 55%, 65%, ${this.alpha})`;
            pCtx.fill();
        }
    }

    // ====== Burst particles (for unlock shockwave) ======
    const burstParticles = [];

    class BurstParticle {
        constructor(x, y) {
            this.x = x; this.y = y;
            const angle = Math.random() * Math.PI * 2;
            const speed = 3 + Math.random() * 10;
            this.vx = Math.cos(angle) * speed;
            this.vy = Math.sin(angle) * speed;
            this.life = 1;
            this.decay = 0.008 + Math.random() * 0.018;
            this.size = 1 + Math.random() * 3;
            this.hue = Math.random() > 0.5 ? 155 : 43;
        }
        update() {
            this.x += this.vx;
            this.y += this.vy;
            this.vx *= 0.995;
            this.vy *= 0.995;
            this.life -= this.decay;
        }
        draw() {
            if (this.life <= 0) return;
            pCtx.beginPath();
            pCtx.arc(this.x, this.y, this.size * this.life, 0, Math.PI * 2);
            pCtx.fillStyle = `hsla(${this.hue}, 65%, 60%, ${this.life * 0.7})`;
            pCtx.fill();
        }
    }

    function spawnShockwave() {
        const orbEl = document.getElementById('vault-orb');
        if (!orbEl) return;
        const rect = orbEl.getBoundingClientRect();
        const cx = rect.left + rect.width / 2;
        const cy = rect.top + rect.height / 2;
        const count = isMobile() ? 60 : 120;
        for (let i = 0; i < count; i++) {
            burstParticles.push(new BurstParticle(cx, cy));
        }
    }

    function drawLines() {
        for (let i = 0; i < bgParticles.length; i++) {
            for (let j = i + 1; j < bgParticles.length; j++) {
                const dx = bgParticles[i].x - bgParticles[j].x;
                const dy = bgParticles[i].y - bgParticles[j].y;
                const d = Math.sqrt(dx * dx + dy * dy);
                if (d < 90) {
                    pCtx.beginPath();
                    pCtx.moveTo(bgParticles[i].x, bgParticles[i].y);
                    pCtx.lineTo(bgParticles[j].x, bgParticles[j].y);
                    pCtx.strokeStyle = `rgba(201, 168, 76, ${(1 - d / 90) * 0.03})`;
                    pCtx.lineWidth = 0.4;
                    pCtx.stroke();
                }
            }
        }
    }

    function animateAll() {
        pCtx.clearRect(0, 0, cw, ch);
        bgParticles.forEach(p => { p.update(); p.draw(); });
        drawLines();
        for (let i = burstParticles.length - 1; i >= 0; i--) {
            burstParticles[i].update();
            burstParticles[i].draw();
            if (burstParticles[i].life <= 0) burstParticles.splice(i, 1);
        }
        requestAnimationFrame(animateAll);
    }

    resizeCanvas();
    for (let i = 0; i < BG_COUNT; i++) bgParticles.push(new BGParticle());
    if (!prefersReduced) requestAnimationFrame(animateAll);

    window.addEventListener('resize', () => { resizeCanvas(); bgParticles.forEach(p => p.init()); });
    document.addEventListener('mousemove', (e) => { mouse.x = e.clientX; mouse.y = e.clientY; mouse.active = true; });

    // ====== Orbiting dots ======
    const orbScene = document.getElementById('vault-orb');
    const orbitDots = [];

    if (orbScene && !prefersReduced) {
        const dotCount = isMobile() ? 4 : 6;
        for (let i = 0; i < dotCount; i++) {
            const dot = document.createElement('div');
            dot.className = 'orbit-dot';
            orbScene.appendChild(dot);
            const baseR = isMobile() ? 70 : 110;
            orbitDots.push({
                el: dot,
                angle: (Math.PI * 2 * i) / dotCount,
                radius: baseR + (i % 3) * 30,
                speed: 0.003 + i * 0.001,
                baseSpeed: 0.003 + i * 0.001
            });
        }

        function animateDots() {
            const rect = orbScene.getBoundingClientRect();
            const cx = rect.width / 2;
            const cy = rect.height / 2;
            orbitDots.forEach(d => {
                d.angle += d.speed;
                const x = cx + Math.cos(d.angle) * d.radius - 2;
                const y = cy + Math.sin(d.angle) * d.radius - 2;
                d.el.style.left = x + 'px';
                d.el.style.top = y + 'px';
            });
            requestAnimationFrame(animateDots);
        }
        animateDots();
    }

    // ====== 3D Parallax on card ======
    const card = document.getElementById('auth-card');
    if (card && !isMobile()) {
        let targetRx = 0, targetRy = 0, currentRx = 0, currentRy = 0;
        document.addEventListener('mousemove', (e) => {
            const cx = W() / 2;
            const cy = H() / 2;
            targetRy = ((e.clientX - cx) / cx) * 4;
            targetRx = -((e.clientY - cy) / cy) * 4;
        });
        document.addEventListener('mouseleave', () => { targetRx = 0; targetRy = 0; });

        function animateCard() {
            currentRx += (targetRx - currentRx) * 0.06;
            currentRy += (targetRy - currentRy) * 0.06;
            card.style.transform = `perspective(900px) rotateX(${currentRx}deg) rotateY(${currentRy}deg)`;
            requestAnimationFrame(animateCard);
        }
        requestAnimationFrame(animateCard);
    }

    // ====== Parallax on orb scene ======
    if (orbScene && !isMobile()) {
        document.addEventListener('mousemove', (e) => {
            const cx = W() / 2;
            const cy = H() / 2;
            const dx = (e.clientX - cx) / cx;
            const dy = (e.clientY - cy) / cy;
            orbScene.style.transform = `translate(${dx * 18}px, ${dy * 18}px)`;
        });
    }

    // ====== Touch parallax via device orientation (mobile) ======
    if (orbScene && isMobile() && window.DeviceOrientationEvent) {
        window.addEventListener('deviceorientation', (e) => {
            if (e.gamma === null) return;
            const dx = (e.gamma / 45) * 10;
            const dy = ((e.beta - 45) / 45) * 10;
            orbScene.style.transform = `translate(${dx}px, ${dy}px)`;
        }, { passive: true });
    }

    // ====== Input-reactive ring effects ======
    const passwordField = document.getElementById('password');
    const ringSvg = document.getElementById('ring-svg');
    const orbGlow = document.getElementById('orb-glow');
    const centerIcon = document.getElementById('center-icon');

    function updateRingIntensity(inputLength) {
        const intensity = Math.min(inputLength / 10, 1);

        if (ringSvg) {
            const core = ringSvg.querySelector('.ring-core');
            if (core) {
                core.style.strokeWidth = 2.5 + intensity * 3;
                core.style.opacity = 0.4 + intensity * 0.5;
            }
        }

        orbitDots.forEach(d => {
            d.speed = d.baseSpeed + intensity * 0.015;
        });

        if (orbGlow) {
            const glowIntensity = 0.06 + intensity * 0.15;
            orbGlow.style.background = `radial-gradient(circle, rgba(201,168,76,${glowIntensity}), transparent 70%)`;
            if (intensity > 0.5) {
                orbGlow.style.width = `${45 + intensity * 15}%`;
                orbGlow.style.height = `${45 + intensity * 15}%`;
            }
        }
    }

    if (passwordField) {
        passwordField.addEventListener('input', () => {
            updateRingIntensity(passwordField.value.length);
        });
    }

    // ====== Wrong password rejection animation ======
    window.playRejectAnimation = function() {
        if (ringSvg) {
            ringSvg.classList.add('reject');
            setTimeout(() => ringSvg.classList.remove('reject'), 800);
        }
        if (centerIcon) {
            centerIcon.classList.add('reject');
            setTimeout(() => centerIcon.classList.remove('reject'), 800);
        }
        orbitDots.forEach(d => {
            d.el.classList.add('reject');
            setTimeout(() => d.el.classList.remove('reject'), 800);
        });
        if (orbGlow) {
            orbGlow.classList.add('reject');
            setTimeout(() => orbGlow.classList.remove('reject'), 800);
        }
    };

    // ====== Unlock Animation (full choreographed sequence) ======
    window.playUnlockAnimation = function(callback) {
        const ringSvgEl = document.getElementById('ring-svg');
        const orbGlowEl = document.getElementById('orb-glow');
        const centerIconEl = document.getElementById('center-icon');
        const authContainer = document.querySelector('.auth-split');
        const shackle = document.getElementById('shackle-path');

        // Phase 1: Accelerate rings
        if (ringSvgEl) {
            ringSvgEl.querySelectorAll('.ring').forEach(r => {
                r.style.animationDuration = '1.5s';
            });
        }

        // Phase 2: Color shift to green + shackle lift
        setTimeout(() => {
            if (ringSvgEl) ringSvgEl.classList.add('unlocked');
            if (orbGlowEl) orbGlowEl.classList.add('unlocked');
            if (centerIconEl) centerIconEl.classList.add('unlocked');
            orbitDots.forEach(d => d.el.classList.add('unlocked'));
            if (shackle) {
                shackle.style.transition = 'all 0.8s cubic-bezier(0.34, 1.56, 0.64, 1)';
                shackle.style.transform = 'translateY(-6px) rotate(-20deg)';
                shackle.style.transformOrigin = '34px 16px';
            }
        }, 400);

        // Phase 3: Shockwave particle burst
        setTimeout(spawnShockwave, 700);

        // Phase 4: Rings expand outward
        setTimeout(() => {
            if (ringSvgEl) ringSvgEl.classList.add('expanding');
        }, 900);

        // Phase 5: Auth container fades/scales out
        setTimeout(() => {
            if (authContainer) {
                authContainer.style.transition = 'all 0.6s cubic-bezier(0.22, 1, 0.36, 1)';
                authContainer.style.opacity = '0';
                authContainer.style.transform = 'translateY(-30px) scale(0.92)';
            }
        }, 1000);

        // Phase 6: Callback to navigate
        setTimeout(() => {
            if (callback) callback();
        }, 1600);
    };

    // ====== Click ripple ======
    document.addEventListener('click', (e) => {
        if (prefersReduced) return;
        const ripple = document.createElement('div');
        ripple.style.cssText = `
            position:fixed; left:${e.clientX}px; top:${e.clientY}px;
            width:0; height:0; border-radius:50%; pointer-events:none; z-index:1;
            background: radial-gradient(circle, rgba(201,168,76,0.06), transparent 70%);
            transform: translate(-50%,-50%);
            animation: clickRipple 0.6s ease-out forwards;
        `;
        document.body.appendChild(ripple);
        setTimeout(() => ripple.remove(), 600);
    });

    // ====== Inject keyframes ======
    const style = document.createElement('style');
    style.textContent = `
        @keyframes clickRipple {
            from { width: 0; height: 0; opacity: 1; }
            to { width: 300px; height: 300px; opacity: 0; }
        }
    `;
    document.head.appendChild(style);
})();
