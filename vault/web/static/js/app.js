(() => {
    'use strict';

    const $ = (sel) => document.querySelector(sel);
    const $$ = (sel) => document.querySelectorAll(sel);

    let selectedFile = null;

    // ====== Ambient Particle Canvas ======
    const canvas = $('#ambient-canvas');
    if (canvas) {
        const ctx = canvas.getContext('2d');
        let particles = [];
        let mouse = { x: -1000, y: -1000 };
        let raf;

        function resizeCanvas() {
            canvas.width = window.innerWidth;
            canvas.height = window.innerHeight;
        }

        class Particle {
            constructor() { this.reset(); }

            reset() {
                this.x = Math.random() * canvas.width;
                this.y = Math.random() * canvas.height;
                this.size = Math.random() * 1.5 + 0.3;
                this.speedX = (Math.random() - 0.5) * 0.15;
                this.speedY = (Math.random() - 0.5) * 0.15;
                this.opacity = Math.random() * 0.4 + 0.1;
                this.hue = Math.random() > 0.6 ? 260 : 170;
                this.pulseSpeed = Math.random() * 0.01 + 0.005;
                this.pulsePhase = Math.random() * Math.PI * 2;
            }

            update(time) {
                this.x += this.speedX;
                this.y += this.speedY;

                const dx = mouse.x - this.x;
                const dy = mouse.y - this.y;
                const dist = Math.sqrt(dx * dx + dy * dy);
                if (dist < 150) {
                    const force = (150 - dist) / 150 * 0.3;
                    this.x -= dx * force * 0.01;
                    this.y -= dy * force * 0.01;
                }

                this.opacity = (Math.sin(time * this.pulseSpeed + this.pulsePhase) + 1) * 0.2 + 0.05;

                if (this.x < -10 || this.x > canvas.width + 10 ||
                    this.y < -10 || this.y > canvas.height + 10) {
                    this.reset();
                }
            }

            draw() {
                ctx.beginPath();
                ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
                ctx.fillStyle = `hsla(${this.hue}, 70%, 70%, ${this.opacity})`;
                ctx.fill();
            }
        }

        function initParticles() {
            const count = Math.min(80, Math.floor(canvas.width * canvas.height / 15000));
            particles = [];
            for (let i = 0; i < count; i++) {
                particles.push(new Particle());
            }
        }

        function drawConnections() {
            for (let i = 0; i < particles.length; i++) {
                for (let j = i + 1; j < particles.length; j++) {
                    const dx = particles[i].x - particles[j].x;
                    const dy = particles[i].y - particles[j].y;
                    const dist = Math.sqrt(dx * dx + dy * dy);

                    if (dist < 120) {
                        const opacity = (1 - dist / 120) * 0.06;
                        ctx.beginPath();
                        ctx.moveTo(particles[i].x, particles[i].y);
                        ctx.lineTo(particles[j].x, particles[j].y);
                        ctx.strokeStyle = `rgba(124, 106, 255, ${opacity})`;
                        ctx.lineWidth = 0.5;
                        ctx.stroke();
                    }
                }
            }
        }

        function animate(time) {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            particles.forEach(p => { p.update(time); p.draw(); });
            drawConnections();
            raf = requestAnimationFrame(animate);
        }

        window.addEventListener('resize', () => { resizeCanvas(); initParticles(); });
        document.addEventListener('mousemove', (e) => { mouse.x = e.clientX; mouse.y = e.clientY; });

        resizeCanvas();
        initParticles();
        raf = requestAnimationFrame(animate);
    }

    // ====== Navigation with View Transitions ======
    const views = $$('.view');
    const navBtns = $$('.nav-btn');

    function switchView(viewName) {
        const currentView = $('.view.active');
        const nextView = $(`#view-${viewName}`);

        if (currentView === nextView) return;

        navBtns.forEach(b => b.classList.remove('active'));
        const activeBtn = $(`.nav-btn[data-view="${viewName}"]`);
        if (activeBtn) activeBtn.classList.add('active');

        if (currentView) {
            currentView.style.animation = 'viewExit 0.2s ease forwards';
            setTimeout(() => {
                currentView.classList.remove('active');
                currentView.style.animation = '';
                nextView.classList.add('active');
                nextView.style.animation = 'viewSwitch 0.4s cubic-bezier(0.22, 1, 0.36, 1) forwards';
            }, 180);
        } else {
            nextView.classList.add('active');
        }

        if (viewName === 'documents') loadDocuments();
        if (viewName === 'credentials') loadCredentials();
        if (viewName === 'memory') loadMemory();
    }

    navBtns.forEach(btn => {
        btn.addEventListener('click', () => switchView(btn.dataset.view));
    });

    const viewExitKeyframes = `
        @keyframes viewExit {
            from { opacity: 1; transform: scale(1); }
            to { opacity: 0; transform: scale(0.97); }
        }
    `;
    const styleTag = document.createElement('style');
    styleTag.textContent = viewExitKeyframes;
    document.head.appendChild(styleTag);

    // ====== Chat ======
    const chatForm = $('#chat-form');
    const messageInput = $('#message-input');
    const messagesEl = $('#messages');
    const fileInput = $('#file-input');
    const filePreview = $('#file-preview');
    const progressWrap = $('#upload-progress');
    const progressBar = $('#upload-progress-bar');

    chatForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const text = messageInput.value.trim();
        if (!text && !selectedFile) return;

        addMessage('user', text || `Uploading: ${selectedFile.name}`);
        messageInput.value = '';

        const formData = new FormData();
        formData.append('message', text);

        const hasFile = !!selectedFile;
        if (selectedFile) {
            formData.append('file', selectedFile);
            clearFileSelection();
        }

        const loadingEl = addMessage('assistant', '<div class="typing-indicator"><span></span><span></span><span></span></div>');

        if (hasFile) {
            progressWrap.classList.remove('hidden');
            progressBar.style.width = '0%';
        }

        try {
            const xhr = new XMLHttpRequest();
            const result = await new Promise((resolve, reject) => {
                xhr.upload.addEventListener('progress', (ev) => {
                    if (ev.lengthComputable && hasFile) {
                        const pct = Math.round((ev.loaded / ev.total) * 90);
                        progressBar.style.width = pct + '%';
                    }
                });

                xhr.addEventListener('load', () => {
                    if (hasFile) progressBar.style.width = '100%';
                    if (xhr.status === 401) {
                        showLockOverlay();
                        loadingEl.remove();
                        reject(new Error('locked'));
                        return;
                    }
                    try {
                        resolve(JSON.parse(xhr.responseText));
                    } catch {
                        reject(new Error('parse'));
                    }
                });

                xhr.addEventListener('error', () => reject(new Error('network')));
                xhr.open('POST', '/api/chat');
                xhr.send(formData);
            });

            setTimeout(() => {
                progressWrap.classList.add('hidden');
                progressBar.style.width = '0%';
            }, 600);

            const contentEl = loadingEl.querySelector('.message-content');
            contentEl.innerHTML = '';
            contentEl.textContent = result.text;

            if (result.file) {
                const dlBtn = document.createElement('a');
                dlBtn.href = `data:application/octet-stream;base64,${result.file.data}`;
                dlBtn.download = result.file.name;
                dlBtn.textContent = `Download ${result.file.name}`;
                dlBtn.style.cssText = 'display:inline-block;margin-top:0.6rem;color:var(--accent-light);text-decoration:none;font-weight:600;transition:color 0.2s;';
                dlBtn.addEventListener('mouseenter', () => { dlBtn.style.color = '#fff'; });
                dlBtn.addEventListener('mouseleave', () => { dlBtn.style.color = ''; });
                contentEl.appendChild(dlBtn);
            }
        } catch (err) {
            progressWrap.classList.add('hidden');
            if (err.message !== 'locked') {
                loadingEl.querySelector('.message-content').textContent = 'Could not reach Vault server.';
            }
        }

        smoothScrollToBottom();
    });

    function addMessage(role, content) {
        const div = document.createElement('div');
        div.className = `message ${role}`;
        const label = role === 'user' ? 'You' : 'Vault';
        div.innerHTML = `<div class="message-label">${label}</div><div class="message-content">${content}</div>`;
        messagesEl.appendChild(div);
        smoothScrollToBottom();
        return div;
    }

    function smoothScrollToBottom() {
        requestAnimationFrame(() => {
            messagesEl.scrollTo({
                top: messagesEl.scrollHeight,
                behavior: 'smooth'
            });
        });
    }

    // ====== File Handling ======
    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length) selectFile(e.target.files[0]);
    });

    function selectFile(file) {
        selectedFile = file;
        const size = file.size < 1024 * 1024
            ? (file.size / 1024).toFixed(1) + ' KB'
            : (file.size / 1024 / 1024).toFixed(1) + ' MB';
        filePreview.classList.remove('hidden');
        filePreview.innerHTML = `
            <svg class="file-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
            <span>${file.name} <span style="color:var(--text-dim)">(${size})</span></span>
            <span class="remove-file" onclick="window._clearFile()">&times;</span>
        `;
    }

    function clearFileSelection() {
        selectedFile = null;
        filePreview.classList.add('hidden');
        filePreview.innerHTML = '';
        fileInput.value = '';
    }

    window._clearFile = clearFileSelection;

    // ====== Drag & Drop ======
    const dropZone = $('#drop-zone');
    const chatContainer = $('.chat-container');

    ['dragenter', 'dragover'].forEach(evt => {
        chatContainer.addEventListener(evt, (e) => {
            e.preventDefault();
            dropZone.classList.remove('hidden');
        });
    });

    ['dragleave', 'drop'].forEach(evt => {
        dropZone.addEventListener(evt, (e) => {
            e.preventDefault();
            dropZone.classList.add('hidden');
        });
    });

    dropZone.addEventListener('drop', (e) => {
        if (e.dataTransfer.files.length) selectFile(e.dataTransfer.files[0]);
    });

    // ====== Document Upload from Documents View ======
    const docUpload = $('#doc-upload-input');
    if (docUpload) {
        docUpload.addEventListener('change', async (e) => {
            if (!e.target.files.length) return;
            const file = e.target.files[0];
            const formData = new FormData();
            formData.append('message', file.name);
            formData.append('file', file);
            try {
                const resp = await fetch('/api/chat', { method: 'POST', body: formData });
                if (resp.ok) {
                    showToast('Document uploaded!', 'success');
                    loadDocuments();
                }
            } catch {
                showToast('Upload failed', 'error');
            }
            docUpload.value = '';
        });
    }

    // ====== Lock ======
    $('#lock-btn').addEventListener('click', async () => {
        await fetch('/api/lock', { method: 'POST' });
        showLockOverlay();
    });

    function showLockOverlay() {
        $('#lock-overlay').classList.remove('hidden');
        setTimeout(() => $('#relock-password').focus(), 100);
    }

    $('#relock-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const pw = $('#relock-password').value;
        const resp = await fetch('/api/unlock', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: pw }),
        });
        if (resp.ok) {
            $('#lock-overlay').classList.add('hidden');
            $('#relock-password').value = '';
            $('#relock-error').textContent = '';
        } else {
            const errField = $('#relock-password');
            $('#relock-error').textContent = 'Incorrect password.';
            errField.value = '';
            errField.focus();
            errField.style.animation = 'none';
            requestAnimationFrame(() => {
                errField.style.animation = 'shake 0.4s ease';
            });
        }
    });

    const shakeKeyframes = `
        @keyframes shake {
            0%, 100% { transform: translateX(0); }
            20% { transform: translateX(-6px); }
            40% { transform: translateX(6px); }
            60% { transform: translateX(-4px); }
            80% { transform: translateX(4px); }
        }
    `;
    const shakeStyle = document.createElement('style');
    shakeStyle.textContent = shakeKeyframes;
    document.head.appendChild(shakeStyle);

    // ====== Settings / Change Password ======
    const settingsBtn = $('#settings-btn');
    if (settingsBtn) {
        settingsBtn.addEventListener('click', () => {
            $('#settings-modal').classList.remove('hidden');
        });
    }

    const settingsClose = $('#settings-cancel');
    if (settingsClose) {
        settingsClose.addEventListener('click', () => {
            $('#settings-modal').classList.add('hidden');
            $('#pw-error').textContent = '';
        });
    }

    const settingsModal = $('#settings-modal');
    if (settingsModal) {
        settingsModal.addEventListener('click', (e) => {
            if (e.target === settingsModal) {
                settingsModal.classList.add('hidden');
                $('#pw-error').textContent = '';
            }
        });
    }

    const pwForm = $('#change-pw-form');
    if (pwForm) {
        pwForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const current = $('#current-pw').value;
            const newPw = $('#new-pw').value;
            const confirm = $('#confirm-pw').value;

            if (newPw.length < 8) {
                $('#pw-error').textContent = 'New password must be at least 8 characters.';
                return;
            }
            if (newPw !== confirm) {
                $('#pw-error').textContent = 'New passwords do not match.';
                return;
            }

            const btn = pwForm.querySelector('.btn-primary');
            btn.disabled = true;
            btn.textContent = 'Changing...';

            try {
                const resp = await fetch('/api/change-password', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ current_password: current, new_password: newPw }),
                });
                const data = await resp.json();
                if (resp.ok) {
                    $('#settings-modal').classList.add('hidden');
                    $('#current-pw').value = '';
                    $('#new-pw').value = '';
                    $('#confirm-pw').value = '';
                    $('#pw-error').textContent = '';
                    showToast('Password changed!', 'success');
                } else {
                    $('#pw-error').textContent = data.detail || 'Failed to change password.';
                }
            } catch {
                $('#pw-error').textContent = 'Connection error.';
            } finally {
                btn.disabled = false;
                btn.textContent = 'Change Password';
            }
        });
    }

    // ====== Toast ======
    function showToast(msg, type = 'success') {
        const existing = $('.toast');
        if (existing) existing.remove();
        const el = document.createElement('div');
        el.className = `toast ${type}`;
        el.textContent = msg;
        document.body.appendChild(el);
        setTimeout(() => el.remove(), 3200);
    }

    // ====== Data Loading with Staggered Animations ======
    async function loadDocuments() {
        const container = $('#documents-list');
        try {
            const resp = await fetch('/api/chat', {
                method: 'POST',
                body: new URLSearchParams({ message: 'list documents' }),
            });
            const data = await resp.json();

            if (data.text.includes('No documents') || data.text.includes('empty')) {
                container.innerHTML = '<p class="empty-state">No documents stored yet.<br><strong>Upload one to get started.</strong></p>';
                return;
            }

            const lines = data.text.split('\n').filter(l => l.trim().startsWith('-'));
            if (!lines.length) {
                container.innerHTML = `<p class="empty-state">${data.text}</p>`;
                return;
            }

            container.innerHTML = '';
            lines.forEach((line, i) => {
                const match = line.match(/\[(.+?)\]\s*(.+)/);
                if (match) {
                    const card = document.createElement('div');
                    card.className = 'item-card';
                    card.style.animationDelay = `${i * 0.06}s`;
                    card.innerHTML = `
                        <div class="category">${match[1]}</div>
                        <div class="name">${match[2]}</div>
                    `;
                    addTiltEffect(card);
                    container.appendChild(card);
                }
            });
        } catch {
            container.innerHTML = '<p class="empty-state">Failed to load documents.</p>';
        }
    }

    async function loadCredentials() {
        const container = $('#credentials-list');
        try {
            const resp = await fetch('/api/chat', {
                method: 'POST',
                body: new URLSearchParams({ message: 'list credentials' }),
            });
            const data = await resp.json();

            if (data.text.includes('No credentials')) {
                container.innerHTML = '<p class="empty-state">No credentials stored yet.<br><strong>Tell Vault a login to save it.</strong></p>';
                return;
            }

            const lines = data.text.split('\n').filter(l => l.trim().startsWith('-'));
            container.innerHTML = '';
            lines.forEach((line, i) => {
                const parts = line.replace(/^\s*-\s*/, '').split(':');
                const item = document.createElement('div');
                item.className = 'list-item';
                item.style.animationDelay = `${i * 0.06}s`;
                item.innerHTML = `
                    <div>
                        <div class="service">${parts[0] || ''}</div>
                        <div class="username">${(parts[1] || '').trim()}</div>
                    </div>
                `;
                container.appendChild(item);
            });
        } catch {
            container.innerHTML = '<p class="empty-state">Failed to load credentials.</p>';
        }
    }

    async function loadMemory() {
        const container = $('#memory-list');
        try {
            const resp = await fetch('/api/chat', {
                method: 'POST',
                body: new URLSearchParams({ message: 'what do you remember' }),
            });
            const data = await resp.json();

            if (data.text.includes('No facts')) {
                container.innerHTML = '<p class="empty-state">No facts stored yet.<br><strong>Tell me something about yourself!</strong></p>';
                return;
            }

            container.innerHTML = '';
            data.text.split('\n').forEach((line, i) => {
                if (!line.trim()) return;
                const item = document.createElement('div');
                item.className = 'list-item';
                item.style.animationDelay = `${i * 0.06}s`;
                if (line.startsWith('[')) {
                    item.innerHTML = `<div style="font-weight:600;color:var(--accent-light)">${line}</div>`;
                } else {
                    item.innerHTML = `<div>${line.trim()}</div>`;
                }
                container.appendChild(item);
            });
        } catch {
            container.innerHTML = '<p class="empty-state">Failed to load memory.</p>';
        }
    }

    // ====== 3D Tilt Effect for Cards ======
    function addTiltEffect(card) {
        card.addEventListener('mousemove', (e) => {
            const rect = card.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;
            const centerX = rect.width / 2;
            const centerY = rect.height / 2;
            const rotateX = (y - centerY) / centerY * -3;
            const rotateY = (x - centerX) / centerX * 3;
            card.style.transform = `perspective(600px) translateY(-4px) rotateX(${rotateX}deg) rotateY(${rotateY}deg)`;
        });

        card.addEventListener('mouseleave', () => {
            card.style.transform = '';
            card.style.transition = 'all 0.4s cubic-bezier(0.22, 1, 0.36, 1)';
            setTimeout(() => { card.style.transition = ''; }, 400);
        });
    }

    // ====== Keyboard Shortcuts ======
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            const modal = $('#settings-modal');
            if (modal && !modal.classList.contains('hidden')) {
                modal.classList.add('hidden');
                $('#pw-error').textContent = '';
            }
        }

        if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
            e.preventDefault();
            messageInput.focus();
        }
    });

    // ====== Send Button Ripple ======
    const sendBtn = $('#send-btn');
    if (sendBtn) {
        sendBtn.addEventListener('click', function(e) {
            const ripple = document.createElement('span');
            const rect = this.getBoundingClientRect();
            ripple.style.cssText = `
                position: absolute; border-radius: 50%; pointer-events: none;
                background: rgba(255,255,255,0.3);
                width: 40px; height: 40px;
                left: ${e.clientX - rect.left - 20}px;
                top: ${e.clientY - rect.top - 20}px;
                animation: rippleOut 0.5s ease forwards;
            `;
            this.style.position = 'relative';
            this.style.overflow = 'hidden';
            this.appendChild(ripple);
            setTimeout(() => ripple.remove(), 500);
        });
    }

    const rippleKeyframes = `
        @keyframes rippleOut {
            from { transform: scale(0); opacity: 1; }
            to { transform: scale(3); opacity: 0; }
        }
    `;
    const rippleStyle = document.createElement('style');
    rippleStyle.textContent = rippleKeyframes;
    document.head.appendChild(rippleStyle);

    // ====== Status Check ======
    setInterval(async () => {
        try {
            const resp = await fetch('/api/status');
            const data = await resp.json();
            if (data.locked) showLockOverlay();
        } catch {}
    }, 30000);
})();
