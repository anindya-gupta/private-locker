(() => {
    'use strict';

    const W = () => window.innerWidth;
    const H = () => window.innerHeight;
    let mouse = { x: W() / 2, y: H() / 2, active: false };

    // ====== Subtle particle field ======
    const canvas = document.createElement('canvas');
    canvas.id = 'auth-particles';
    canvas.style.cssText = 'position:fixed;inset:0;z-index:0;pointer-events:none;';
    document.body.prepend(canvas);
    const ctx = canvas.getContext('2d');

    let cw, ch;
    const particles = [];
    const PARTICLE_COUNT = 40;

    function resize() {
        cw = canvas.width = W();
        ch = canvas.height = H();
    }

    class Particle {
        constructor() { this.init(); }

        init() {
            this.x = Math.random() * cw;
            this.y = Math.random() * ch;
            this.r = Math.random() * 1.3 + 0.3;
            this.vx = (Math.random() - 0.5) * 0.2;
            this.vy = (Math.random() - 0.5) * 0.2;
            this.baseAlpha = Math.random() * 0.3 + 0.05;
            this.alpha = this.baseAlpha;
            this.hue = Math.random() > 0.6 ? 258 : 155;
        }

        update() {
            this.x += this.vx;
            this.y += this.vy;

            if (mouse.active) {
                const dx = mouse.x - this.x;
                const dy = mouse.y - this.y;
                const dist = Math.sqrt(dx * dx + dy * dy);
                if (dist < 160) {
                    const force = (160 - dist) / 160;
                    this.alpha = this.baseAlpha + force * 0.4;
                    this.x += dx * force * 0.006;
                    this.y += dy * force * 0.006;
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
            ctx.beginPath();
            ctx.arc(this.x, this.y, this.r, 0, Math.PI * 2);
            ctx.fillStyle = `hsla(${this.hue}, 55%, 65%, ${this.alpha})`;
            ctx.fill();
        }
    }

    function drawLines() {
        for (let i = 0; i < particles.length; i++) {
            for (let j = i + 1; j < particles.length; j++) {
                const dx = particles[i].x - particles[j].x;
                const dy = particles[i].y - particles[j].y;
                const d = Math.sqrt(dx * dx + dy * dy);
                if (d < 100) {
                    ctx.beginPath();
                    ctx.moveTo(particles[i].x, particles[i].y);
                    ctx.lineTo(particles[j].x, particles[j].y);
                    ctx.strokeStyle = `rgba(139, 122, 255, ${(1 - d / 100) * 0.04})`;
                    ctx.lineWidth = 0.4;
                    ctx.stroke();
                }
            }
        }
    }

    function animateParticles() {
        ctx.clearRect(0, 0, cw, ch);
        particles.forEach(p => { p.update(); p.draw(); });
        drawLines();
        requestAnimationFrame(animateParticles);
    }

    resize();
    for (let i = 0; i < PARTICLE_COUNT; i++) particles.push(new Particle());
    requestAnimationFrame(animateParticles);

    window.addEventListener('resize', () => { resize(); particles.forEach(p => p.init()); });
    document.addEventListener('mousemove', (e) => { mouse.x = e.clientX; mouse.y = e.clientY; mouse.active = true; });

    // ====== 3D Parallax Card ======
    const card = document.getElementById('auth-card');

    if (card) {
        let targetRx = 0, targetRy = 0, currentRx = 0, currentRy = 0;
        let cardRaf;

        document.addEventListener('mousemove', (e) => {
            const cx = W() / 2;
            const cy = H() / 2;
            targetRy = ((e.clientX - cx) / cx) * 5;
            targetRx = -((e.clientY - cy) / cy) * 5;
        });

        document.addEventListener('mouseleave', () => { targetRx = 0; targetRy = 0; });

        function animateCard() {
            currentRx += (targetRx - currentRx) * 0.08;
            currentRy += (targetRy - currentRy) * 0.08;
            card.style.transform = `perspective(800px) rotateX(${currentRx}deg) rotateY(${currentRy}deg)`;
            cardRaf = requestAnimationFrame(animateCard);
        }

        cardRaf = requestAnimationFrame(animateCard);
    }

    // ====== Animated Lock Icon ======
    const lockIcon = document.getElementById('lock-icon');
    if (lockIcon) {
        lockIcon.innerHTML = `
            <svg width="32" height="32" viewBox="0 0 40 40" fill="none">
                <rect class="lock-body" x="8" y="18" width="24" height="18" rx="3"
                      stroke="currentColor" stroke-width="2.5" fill="none"/>
                <path class="lock-shackle" d="M13 18V13a7 7 0 0 1 14 0v5"
                      stroke="currentColor" stroke-width="2.5" fill="none"
                      stroke-linecap="round"
                      style="transform-origin: 27px 13px; transition: transform 0.5s cubic-bezier(0.34, 1.56, 0.64, 1);"/>
                <circle class="lock-hole" cx="20" cy="27" r="2.5" fill="currentColor"/>
                <line x1="20" y1="29.5" x2="20" y2="33" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
        `;

        lockIcon.addEventListener('mouseenter', () => {
            const s = lockIcon.querySelector('.lock-shackle');
            if (s) s.style.transform = 'translateY(-2px)';
        });
        lockIcon.addEventListener('mouseleave', () => {
            const s = lockIcon.querySelector('.lock-shackle');
            if (s && !lockIcon.classList.contains('unlocked')) s.style.transform = '';
        });
    }

    // ====== Unlock Animation ======
    window.playUnlockAnimation = function(callback) {
        const shackle = document.querySelector('.lock-shackle');
        const icon = document.getElementById('lock-icon');

        if (shackle && icon) {
            icon.classList.add('unlocked');
            icon.style.color = '#3dd68c';
            icon.style.background = 'rgba(61, 214, 140, 0.1)';
            shackle.style.transform = 'rotate(-30deg) translateY(-4px)';
        }

        spawnConfetti();

        const authContainer = document.querySelector('.auth-container');
        setTimeout(() => {
            if (authContainer) authContainer.style.animation = 'authExit 0.5s ease forwards';
            setTimeout(() => { if (callback) callback(); }, 450);
        }, 650);
    };

    function spawnConfetti() {
        const cx = W() / 2;
        const cy = H() / 2 - 30;
        const colors = ['#8b7aff', '#3dd68c', '#f06090', '#f0b040', '#60c0f0'];

        for (let i = 0; i < 40; i++) {
            const dot = document.createElement('div');
            const angle = (Math.PI * 2 * i) / 40 + (Math.random() - 0.5) * 0.5;
            const dist = 60 + Math.random() * 220;
            const size = 2 + Math.random() * 5;
            const color = colors[Math.floor(Math.random() * colors.length)];
            const duration = 0.7 + Math.random() * 0.5;
            const isRect = Math.random() > 0.5;

            dot.style.cssText = `
                position:fixed; left:${cx}px; top:${cy}px;
                width:${size}px; height:${isRect ? size * 2 : size}px;
                ${isRect ? 'border-radius:2px;' : 'border-radius:50%;'}
                background:${color}; pointer-events:none; z-index:100;
                box-shadow: 0 0 8px ${color}40;
                animation: confettiBurst ${duration}s ease-out forwards;
                --tx: ${Math.cos(angle) * dist}px;
                --ty: ${Math.sin(angle) * dist}px;
                --rot: ${Math.random() * 720 - 360}deg;
            `;
            document.body.appendChild(dot);
            setTimeout(() => dot.remove(), duration * 1000 + 50);
        }
    }

    // ====== Click ripple ======
    document.addEventListener('click', (e) => {
        const ripple = document.createElement('div');
        ripple.style.cssText = `
            position:fixed; left:${e.clientX}px; top:${e.clientY}px;
            width:0; height:0; border-radius:50%; pointer-events:none; z-index:1;
            background: radial-gradient(circle, rgba(139,122,255,0.1), transparent 70%);
            transform: translate(-50%,-50%);
            animation: clickRipple 0.7s ease-out forwards;
        `;
        document.body.appendChild(ripple);
        setTimeout(() => ripple.remove(), 700);
    });

    // ====== Inject Keyframes ======
    const style = document.createElement('style');
    style.textContent = `
        @keyframes clickRipple {
            from { width: 0; height: 0; opacity: 1; }
            to { width: 350px; height: 350px; opacity: 0; }
        }
        @keyframes confettiBurst {
            0% { transform: translate(-50%, -50%) scale(1) rotate(0deg); opacity: 1; }
            100% { transform: translate(calc(-50% + var(--tx)), calc(-50% + var(--ty))) scale(0.3) rotate(var(--rot)); opacity: 0; }
        }
        @keyframes authExit {
            to { opacity: 0; transform: translateY(-30px) scale(0.92); }
        }
    `;
    document.head.appendChild(style);
})();
