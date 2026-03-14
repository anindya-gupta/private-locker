(() => {
    'use strict';

    // ====== Mouse-reactive particle canvas ======
    const canvas = document.createElement('canvas');
    canvas.id = 'auth-particles';
    canvas.style.cssText = 'position:fixed;inset:0;z-index:0;pointer-events:none;';
    document.body.prepend(canvas);
    const ctx = canvas.getContext('2d');

    let W, H;
    let mouse = { x: -999, y: -999 };
    const particles = [];
    const PARTICLE_COUNT = 60;

    function resize() {
        W = canvas.width = window.innerWidth;
        H = canvas.height = window.innerHeight;
    }

    class Particle {
        constructor() { this.init(); }

        init() {
            this.x = Math.random() * W;
            this.y = Math.random() * H;
            this.r = Math.random() * 2 + 0.5;
            this.vx = (Math.random() - 0.5) * 0.3;
            this.vy = (Math.random() - 0.5) * 0.3;
            this.baseAlpha = Math.random() * 0.5 + 0.1;
            this.alpha = this.baseAlpha;
            this.hue = Math.random() > 0.5 ? 258 : 172;
        }

        update() {
            this.x += this.vx;
            this.y += this.vy;

            const dx = mouse.x - this.x;
            const dy = mouse.y - this.y;
            const dist = Math.sqrt(dx * dx + dy * dy);

            if (dist < 200) {
                const force = (200 - dist) / 200;
                this.alpha = this.baseAlpha + force * 0.5;
                this.x += dx * force * 0.008;
                this.y += dy * force * 0.008;
            } else {
                this.alpha += (this.baseAlpha - this.alpha) * 0.05;
            }

            if (this.x < -20) this.x = W + 20;
            if (this.x > W + 20) this.x = -20;
            if (this.y < -20) this.y = H + 20;
            if (this.y > H + 20) this.y = -20;
        }

        draw() {
            ctx.beginPath();
            ctx.arc(this.x, this.y, this.r, 0, Math.PI * 2);
            ctx.fillStyle = `hsla(${this.hue}, 80%, 70%, ${this.alpha})`;
            ctx.fill();
        }
    }

    function drawLines() {
        for (let i = 0; i < particles.length; i++) {
            for (let j = i + 1; j < particles.length; j++) {
                const dx = particles[i].x - particles[j].x;
                const dy = particles[i].y - particles[j].y;
                const d = Math.sqrt(dx * dx + dy * dy);
                if (d < 130) {
                    ctx.beginPath();
                    ctx.moveTo(particles[i].x, particles[i].y);
                    ctx.lineTo(particles[j].x, particles[j].y);
                    ctx.strokeStyle = `rgba(124, 106, 255, ${(1 - d / 130) * 0.08})`;
                    ctx.lineWidth = 0.5;
                    ctx.stroke();
                }
            }
        }

        if (mouse.x > 0) {
            particles.forEach(p => {
                const dx = mouse.x - p.x;
                const dy = mouse.y - p.y;
                const d = Math.sqrt(dx * dx + dy * dy);
                if (d < 180) {
                    ctx.beginPath();
                    ctx.moveTo(p.x, p.y);
                    ctx.lineTo(mouse.x, mouse.y);
                    ctx.strokeStyle = `rgba(94, 234, 212, ${(1 - d / 180) * 0.12})`;
                    ctx.lineWidth = 0.5;
                    ctx.stroke();
                }
            });
        }
    }

    function animate() {
        ctx.clearRect(0, 0, W, H);
        particles.forEach(p => { p.update(); p.draw(); });
        drawLines();
        requestAnimationFrame(animate);
    }

    resize();
    for (let i = 0; i < PARTICLE_COUNT; i++) particles.push(new Particle());
    requestAnimationFrame(animate);

    window.addEventListener('resize', () => {
        resize();
        particles.forEach(p => p.init());
    });

    document.addEventListener('mousemove', (e) => {
        mouse.x = e.clientX;
        mouse.y = e.clientY;
    });

    // ====== Click ripple on background ======
    document.addEventListener('click', (e) => {
        const ripple = document.createElement('div');
        ripple.style.cssText = `
            position:fixed; left:${e.clientX}px; top:${e.clientY}px;
            width:0; height:0; border-radius:50%; pointer-events:none; z-index:1;
            background: radial-gradient(circle, rgba(124,106,255,0.15), transparent 70%);
            transform: translate(-50%,-50%);
            animation: clickRipple 0.8s ease-out forwards;
        `;
        document.body.appendChild(ripple);
        setTimeout(() => ripple.remove(), 800);
    });

    // ====== Card parallax on mouse move ======
    const card = document.querySelector('.auth-card');
    const container = document.querySelector('.auth-container');

    if (card && container) {
        document.addEventListener('mousemove', (e) => {
            const cx = W / 2;
            const cy = H / 2;
            const dx = (e.clientX - cx) / cx;
            const dy = (e.clientY - cy) / cy;
            card.style.transform = `perspective(800px) rotateY(${dx * 4}deg) rotateX(${-dy * 4}deg)`;
        });

        document.addEventListener('mouseleave', () => {
            card.style.transition = 'transform 0.6s ease';
            card.style.transform = 'perspective(800px) rotateY(0) rotateX(0)';
            setTimeout(() => { card.style.transition = ''; }, 600);
        });
    }

    // ====== Animated lock SVG ======
    const lockIcon = document.querySelector('.vault-icon');
    if (lockIcon) {
        lockIcon.innerHTML = `
            <svg width="32" height="32" viewBox="0 0 40 40" fill="none">
                <rect class="lock-body" x="8" y="18" width="24" height="18" rx="3"
                      stroke="currentColor" stroke-width="2.5" fill="none"/>
                <path class="lock-shackle" d="M13 18V13a7 7 0 0 1 14 0v5"
                      stroke="currentColor" stroke-width="2.5" fill="none"
                      stroke-linecap="round"
                      style="transform-origin: 27px 13px; transition: transform 0.5s cubic-bezier(0.34, 1.56, 0.64, 1), opacity 0.3s;"/>
                <circle class="lock-hole" cx="20" cy="27" r="2.5" fill="currentColor"/>
                <line class="lock-line" x1="20" y1="29.5" x2="20" y2="33"
                      stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
        `;

        lockIcon.addEventListener('mouseenter', () => {
            const shackle = lockIcon.querySelector('.lock-shackle');
            if (shackle) shackle.style.transform = 'translateY(-2px)';
        });
        lockIcon.addEventListener('mouseleave', () => {
            const shackle = lockIcon.querySelector('.lock-shackle');
            if (shackle && !lockIcon.classList.contains('unlocked')) {
                shackle.style.transform = '';
            }
        });
    }

    // ====== Unlock animation: shackle opens + particle burst ======
    window.playUnlockAnimation = function(callback) {
        const shackle = document.querySelector('.lock-shackle');
        const icon = document.querySelector('.vault-icon');

        if (shackle && icon) {
            icon.classList.add('unlocked');
            icon.style.color = '#34d399';
            shackle.style.transform = 'rotate(-30deg) translateY(-4px)';
            shackle.style.transition = 'transform 0.5s cubic-bezier(0.34, 1.56, 0.64, 1)';

            icon.style.animation = 'none';
            requestAnimationFrame(() => {
                icon.style.animation = 'unlockPop 0.5s cubic-bezier(0.34, 1.56, 0.64, 1)';
            });
        }

        spawnBurst();

        const authContainer = document.querySelector('.auth-container');
        setTimeout(() => {
            if (authContainer) {
                authContainer.style.animation = 'authExit 0.5s ease forwards';
            }
            setTimeout(() => { if (callback) callback(); }, 400);
        }, 600);
    };

    function spawnBurst() {
        const cx = W / 2;
        const cy = H / 2;
        const colors = ['#7c6aff', '#5eead4', '#f471b5', '#fbbf24', '#34d399'];

        for (let i = 0; i < 30; i++) {
            const dot = document.createElement('div');
            const angle = (Math.PI * 2 * i) / 30 + Math.random() * 0.3;
            const dist = 80 + Math.random() * 200;
            const size = 3 + Math.random() * 5;
            const color = colors[Math.floor(Math.random() * colors.length)];
            const duration = 0.6 + Math.random() * 0.4;

            dot.style.cssText = `
                position:fixed; left:${cx}px; top:${cy}px;
                width:${size}px; height:${size}px; border-radius:50%;
                background:${color}; pointer-events:none; z-index:100;
                box-shadow: 0 0 6px ${color};
                animation: burstParticle ${duration}s ease-out forwards;
                --tx: ${Math.cos(angle) * dist}px;
                --ty: ${Math.sin(angle) * dist}px;
            `;
            document.body.appendChild(dot);
            setTimeout(() => dot.remove(), duration * 1000);
        }
    }

    // ====== Floating background shapes ======
    for (let i = 0; i < 5; i++) {
        const shape = document.createElement('div');
        const size = 20 + Math.random() * 40;
        const x = Math.random() * 100;
        const y = Math.random() * 100;
        const dur = 15 + Math.random() * 20;
        const delay = -Math.random() * dur;
        const isCircle = Math.random() > 0.5;

        shape.style.cssText = `
            position:fixed; pointer-events:none; z-index:0;
            left:${x}%; top:${y}%;
            width:${size}px; height:${size}px;
            border: 1px solid rgba(124,106,255,0.08);
            ${isCircle ? 'border-radius:50%;' : 'border-radius:4px; transform:rotate(45deg);'}
            animation: floatShape ${dur}s ${delay}s ease-in-out infinite;
            opacity: 0.4;
        `;
        document.body.appendChild(shape);
    }

    // ====== Inject keyframes ======
    const style = document.createElement('style');
    style.textContent = `
        @keyframes clickRipple {
            from { width: 0; height: 0; opacity: 1; }
            to { width: 400px; height: 400px; opacity: 0; }
        }
        @keyframes burstParticle {
            0% { transform: translate(-50%, -50%) scale(1); opacity: 1; }
            100% { transform: translate(calc(-50% + var(--tx)), calc(-50% + var(--ty))) scale(0); opacity: 0; }
        }
        @keyframes unlockPop {
            0% { transform: scale(1); }
            40% { transform: scale(1.3); }
            100% { transform: scale(1); }
        }
        @keyframes floatShape {
            0%, 100% { transform: translate(0, 0) rotate(0deg); }
            25% { transform: translate(20px, -30px) rotate(90deg); }
            50% { transform: translate(-15px, -50px) rotate(180deg); }
            75% { transform: translate(25px, -20px) rotate(270deg); }
        }
        .auth-card {
            transition: box-shadow 0.3s ease;
            will-change: transform;
        }
        .auth-card:hover {
            box-shadow: 0 20px 60px -15px rgba(0,0,0,0.7), 0 0 40px rgba(124,106,255,0.1);
        }
    `;
    document.head.appendChild(style);
})();
