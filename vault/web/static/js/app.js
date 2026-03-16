(() => {
    'use strict';

    const $ = (sel) => document.querySelector(sel);
    const $$ = (sel) => document.querySelectorAll(sel);

    let selectedFile = null;

    // ====== Toast System ======
    function showToast(msg, type = 'success') {
        const existing = $('.toast');
        if (existing) existing.remove();
        const el = document.createElement('div');
        el.className = `toast ${type}`;
        el.textContent = msg;
        document.body.appendChild(el);
        setTimeout(() => { if (el.parentNode) el.remove(); }, 3200);
    }

    // ====== Animated Counter ======
    function animateCounter(el, target) {
        const duration = 600;
        const start = parseInt(el.textContent) || 0;
        if (start === target) return;
        const startTime = performance.now();
        function tick(now) {
            const elapsed = now - startTime;
            const progress = Math.min(elapsed / duration, 1);
            const eased = 1 - Math.pow(1 - progress, 3);
            el.textContent = Math.round(start + (target - start) * eased);
            if (progress < 1) requestAnimationFrame(tick);
        }
        requestAnimationFrame(tick);
    }

    // ====== Dashboard Stats ======
    async function loadStats() {
        try {
            const resp = await fetch('/api/stats');
            if (resp.status === 401) { redirectToLogin(); return; }
            const data = await resp.json();
            animateCounter($('#stat-docs'), data.documents);
            animateCounter($('#stat-creds'), data.credentials);
            animateCounter($('#stat-facts'), data.facts);
            animateCounter($('#stat-sessions'), data.active_sessions);

            const bdayCard = $('#birthday-stat-card');
            if (bdayCard) {
                const total = data.total_birthdays || 0;
                const upcoming = data.upcoming_birthdays || 0;
                if (total > 0) {
                    bdayCard.style.display = '';
                    animateCounter($('#stat-birthdays'), total);
                    const label = bdayCard.querySelector('.stat-label');
                    if (label) label.textContent = upcoming > 0 ? `Birthdays (${upcoming} soon)` : 'Birthdays';
                } else {
                    bdayCard.style.display = 'none';
                }
            }
        } catch {}
    }

    loadStats();
    loadBirthdays();

    async function loadBirthdays() {
        const widget = $('#birthday-widget');
        const list = $('#birthday-list');
        if (!widget || !list) return;
        try {
            const resp = await fetch('/api/birthdays');
            if (!resp.ok) return;
            const data = await resp.json();
            const bdays = data.birthdays || [];
            if (bdays.length === 0) { widget.style.display = 'none'; return; }

            widget.style.display = '';
            list.innerHTML = '';
            bdays.forEach(b => {
                const item = document.createElement('div');
                item.className = 'birthday-item';
                let badge = '';
                const d = b.days_until;
                if (d === null) badge = '<span class="bday-badge">?</span>';
                else if (d === 0) badge = '<span class="bday-badge bday-today">TODAY</span>';
                else if (d === 1) badge = '<span class="bday-badge bday-tomorrow">Tomorrow</span>';
                else if (d <= 7) badge = `<span class="bday-badge bday-soon">In ${d} days</span>`;
                else if (d <= 30) badge = `<span class="bday-badge bday-soon">${d}d</span>`;
                else badge = `<span class="bday-badge">${d}d</span>`;

                const isUpcoming = d !== null && d <= 7;
                item.innerHTML = `
                    <div class="bday-icon">${d === 0 ? '&#127874;' : '&#127873;'}</div>
                    <div class="bday-info">
                        <div class="bday-name">${b.name}</div>
                        <div class="bday-date">${b.date}</div>
                    </div>
                    ${badge}
                `;
                if (isUpcoming) item.classList.add('bday-highlight');
                list.appendChild(item);
            });
        } catch {}
    }

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
                this.baseSize = Math.random() * 1.2 + 0.3;
                this.size = this.baseSize;
                this.speedX = (Math.random() - 0.5) * 0.15;
                this.speedY = (Math.random() - 0.5) * 0.15;
                this.baseOpacity = Math.random() * 0.25 + 0.05;
                this.opacity = this.baseOpacity;
                this.hue = Math.random() > 0.65 ? 43 : 155;
                this.pulseSpeed = Math.random() * 0.008 + 0.003;
                this.pulsePhase = Math.random() * Math.PI * 2;
            }
            update(time) {
                this.x += this.speedX;
                this.y += this.speedY;
                const dx = mouse.x - this.x;
                const dy = mouse.y - this.y;
                const dist = Math.sqrt(dx * dx + dy * dy);
                if (dist < 180) {
                    const force = (180 - dist) / 180;
                    this.x += dx * force * 0.008;
                    this.y += dy * force * 0.008;
                    this.opacity = this.baseOpacity + force * 0.35;
                    this.size = this.baseSize + force * 1.5;
                } else {
                    this.opacity += (this.baseOpacity - this.opacity) * 0.04;
                    this.size += (this.baseSize - this.size) * 0.04;
                }
                this.opacity *= (Math.sin(time * this.pulseSpeed + this.pulsePhase) * 0.12 + 0.88);
                if (this.x < -10 || this.x > canvas.width + 10 || this.y < -10 || this.y > canvas.height + 10) {
                    this.reset();
                }
            }
            draw() {
                ctx.beginPath();
                ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
                ctx.fillStyle = `hsla(${this.hue}, 60%, 65%, ${this.opacity})`;
                ctx.fill();
                if (this.size > 1.8) {
                    ctx.beginPath();
                    ctx.arc(this.x, this.y, this.size * 2.5, 0, Math.PI * 2);
                    ctx.fillStyle = `hsla(${this.hue}, 60%, 65%, ${this.opacity * 0.06})`;
                    ctx.fill();
                }
            }
        }

        function initParticles() {
            const count = Math.min(50, Math.floor(canvas.width * canvas.height / 25000));
            particles = [];
            for (let i = 0; i < count; i++) particles.push(new Particle());
        }

        function drawConnections() {
            for (let i = 0; i < particles.length; i++) {
                for (let j = i + 1; j < particles.length; j++) {
                    const dx = particles[i].x - particles[j].x;
                    const dy = particles[i].y - particles[j].y;
                    const dist = Math.sqrt(dx * dx + dy * dy);
                    if (dist < 110) {
                        const opacity = (1 - dist / 110) * 0.04;
                        ctx.beginPath();
                        ctx.moveTo(particles[i].x, particles[i].y);
                        ctx.lineTo(particles[j].x, particles[j].y);
                        ctx.strokeStyle = `rgba(201, 168, 76, ${opacity})`;
                        ctx.lineWidth = 0.4;
                        ctx.stroke();
                    }
                }
                if (mouse.x > 0) {
                    const dx = mouse.x - particles[i].x;
                    const dy = mouse.y - particles[i].y;
                    const dist = Math.sqrt(dx * dx + dy * dy);
                    if (dist < 150) {
                        const opacity = (1 - dist / 150) * 0.06;
                        ctx.beginPath();
                        ctx.moveTo(particles[i].x, particles[i].y);
                        ctx.lineTo(mouse.x, mouse.y);
                        ctx.strokeStyle = `rgba(61, 214, 140, ${opacity})`;
                        ctx.lineWidth = 0.4;
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

    // ====== Navigation with pushState ======
    const views = $$('.view');
    const navBtns = $$('.nav-btn');
    const tabBtns = $$('.tab-btn');
    const viewOrder = ['chat', 'documents', 'credentials', 'memory', 'database'];

    const pathToView = {
        '/app': 'chat',
        '/app/chat': 'chat',
        '/app/docs': 'documents',
        '/app/credentials': 'credentials',
        '/app/memory': 'memory',
        '/app/database': 'database',
    };

    const viewToPath = {
        'chat': '/app/chat',
        'documents': '/app/docs',
        'credentials': '/app/credentials',
        'memory': '/app/memory',
        'database': '/app/database',
    };

    function switchView(viewName, pushState = true) {
        const currentView = $('.view.active');
        const nextView = $(`#view-${viewName}`);
        if (currentView === nextView) return;

        navBtns.forEach(b => b.classList.toggle('active', b.dataset.view === viewName));
        tabBtns.forEach(b => b.classList.toggle('active', b.dataset.view === viewName));

        if (currentView) currentView.classList.remove('active');
        if (nextView) nextView.classList.add('active');

        if (pushState && viewToPath[viewName]) {
            history.pushState({ view: viewName }, '', viewToPath[viewName]);
        }

        const statsEl = $('#dashboard-stats');
        if (statsEl) statsEl.style.display = viewName === 'chat' ? '' : 'none';

        if (viewName === 'documents') loadDocuments();
        if (viewName === 'credentials') loadCredentials();
        if (viewName === 'memory') loadMemory();
        if (viewName === 'database') loadDatabase();
        if (viewName === 'chat') { loadStats(); loadBirthdays(); }
    }

    // Restore view from current URL
    const initialView = pathToView[window.location.pathname] || 'chat';
    if (initialView !== 'chat') switchView(initialView, false);

    window.addEventListener('popstate', (e) => {
        const view = e.state?.view || pathToView[window.location.pathname] || 'chat';
        switchView(view, false);
    });

    navBtns.forEach(btn => btn.addEventListener('click', () => switchView(btn.dataset.view)));
    tabBtns.forEach(btn => btn.addEventListener('click', () => switchView(btn.dataset.view)));

    // ====== Swipe Navigation (mobile) ======
    let touchStartX = 0;
    let touchStartY = 0;

    document.addEventListener('touchstart', (e) => {
        touchStartX = e.touches[0].clientX;
        touchStartY = e.touches[0].clientY;
    }, { passive: true });

    document.addEventListener('touchend', (e) => {
        const dx = e.changedTouches[0].clientX - touchStartX;
        const dy = e.changedTouches[0].clientY - touchStartY;
        if (Math.abs(dx) < 60 || Math.abs(dy) > Math.abs(dx) * 0.7) return;
        const activeBtn = $('.tab-btn.active') || $('.nav-btn.active');
        if (!activeBtn) return;
        const currentIdx = viewOrder.indexOf(activeBtn.dataset.view);
        if (currentIdx === -1) return;
        const nextIdx = dx < 0 ? currentIdx + 1 : currentIdx - 1;
        if (nextIdx >= 0 && nextIdx < viewOrder.length) {
            switchView(viewOrder[nextIdx]);
        }
    }, { passive: true });

    // ====== Auth Redirects ======
    function redirectToLogin() {
        window.location.href = '/login';
    }

    // ====== Chat ======
    const chatForm = $('#chat-form');
    const messageInput = $('#message-input');
    const messagesEl = $('#messages');
    const fileInput = $('#file-input');
    const filePreview = $('#file-preview');
    const progressWrap = $('#upload-progress');
    const progressBar = $('#upload-progress-bar');

    if (chatForm) chatForm.addEventListener('submit', async (e) => {
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
                        progressBar.style.width = Math.round((ev.loaded / ev.total) * 90) + '%';
                    }
                });
                xhr.addEventListener('load', () => {
                    if (hasFile) progressBar.style.width = '100%';
                    if (xhr.status === 401) {
                        redirectToLogin();
                        reject(new Error('locked'));
                        return;
                    }
                    try { resolve(JSON.parse(xhr.responseText)); }
                    catch { reject(new Error('parse')); }
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
                dlBtn.style.cssText = 'display:inline-block;margin-top:0.6rem;color:var(--accent);text-decoration:none;font-weight:600;';
                contentEl.appendChild(dlBtn);
            }

            loadStats();
            loadBirthdays();
        } catch (err) {
            progressWrap.classList.add('hidden');
            if (err.message !== 'locked') {
                loadingEl.querySelector('.message-content').textContent = 'Could not reach Vault server.';
            }
        }

        smoothScrollToBottom();
    });

    const AVATAR_SVG = '<span class="message-avatar"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg></span>';

    function addMessage(role, content) {
        const div = document.createElement('div');
        div.className = `message ${role}`;
        const label = role === 'user' ? 'You' : `${AVATAR_SVG}Vault`;
        div.innerHTML = `<div class="message-label">${label}</div><div class="message-content">${content}</div>`;
        messagesEl.appendChild(div);
        smoothScrollToBottom();
        return div;
    }

    function smoothScrollToBottom() {
        requestAnimationFrame(() => {
            messagesEl.scrollTo({ top: messagesEl.scrollHeight, behavior: 'smooth' });
        });
    }

    // ====== File Handling ======
    if (fileInput) fileInput.addEventListener('change', (e) => {
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
            <span>${file.name} <span style="color:var(--text-3)">(${size})</span></span>
            <span class="remove-file" onclick="window._clearFile()">&times;</span>
        `;
    }

    function clearFileSelection() {
        selectedFile = null;
        filePreview.classList.add('hidden');
        filePreview.innerHTML = '';
        if (fileInput) fileInput.value = '';
    }

    window._clearFile = clearFileSelection;

    // ====== Drag & Drop ======
    const dropZone = $('#drop-zone');
    const chatContainer = $('.chat-container');

    if (chatContainer && dropZone) {
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
    }

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
                if (resp.status === 401) { redirectToLogin(); return; }
                if (resp.ok) {
                    showToast('Document uploaded!', 'success');
                    loadDocuments();
                    loadStats();
                    loadBirthdays();
                }
            } catch {
                showToast('Upload failed', 'error');
            }
            docUpload.value = '';
        });
    }

    // ====== Lock & Logout ======
    const lockBtn = $('#lock-btn');
    if (lockBtn) lockBtn.addEventListener('click', async () => {
        await fetch('/api/lock', { method: 'POST' });
        showToast('Vault locked', 'success');
        setTimeout(() => { window.location.href = '/login?mode=lock'; }, 400);
    });

    const logoutBtn = $('#logout-btn');
    if (logoutBtn) logoutBtn.addEventListener('click', async () => {
        await fetch('/api/logout', { method: 'POST' });
        showToast('Logged out', 'success');
        setTimeout(() => { window.location.href = '/login'; }, 400);
    });

    function showLockOverlay() {
        const overlay = $('#lock-overlay');
        if (overlay) {
            overlay.classList.remove('hidden');
            setTimeout(() => { const pw = $('#relock-password'); if (pw) pw.focus(); }, 100);
        }
    }

    const relockForm = $('#relock-form');
    if (relockForm) relockForm.addEventListener('submit', async (e) => {
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
            const errEl = $('#relock-error'); if (errEl) errEl.textContent = '';
            showToast('Unlocked!', 'success');
        } else if (resp.status === 401) {
            redirectToLogin();
        } else {
            const errField = $('#relock-password');
            const errEl = $('#relock-error'); if (errEl) errEl.textContent = 'Incorrect password.';
            errField.value = '';
            errField.focus();
            shakeElement(errField);
        }
    });

    function shakeElement(el) {
        if (!el) return;
        el.style.animation = 'none';
        requestAnimationFrame(() => { el.style.animation = 'shake 0.4s ease'; });
    }

    const dynStyles = document.createElement('style');
    dynStyles.textContent = `
        @keyframes shake {
            0%, 100% { transform: translateX(0); }
            20% { transform: translateX(-6px); }
            40% { transform: translateX(6px); }
            60% { transform: translateX(-4px); }
            80% { transform: translateX(4px); }
        }
        @keyframes rippleOut {
            from { transform: scale(0); opacity: 1; }
            to { transform: scale(3); opacity: 0; }
        }
    `;
    document.head.appendChild(dynStyles);

    // ====== Settings / Change Password ======
    const settingsBtn = $('#settings-btn');
    if (settingsBtn) settingsBtn.addEventListener('click', () => {
        const modal = $('#settings-modal'); if (modal) modal.classList.remove('hidden');
    });

    const settingsClose = $('#settings-cancel');
    if (settingsClose) settingsClose.addEventListener('click', () => {
        const modal = $('#settings-modal'); if (modal) modal.classList.add('hidden');
        const err = $('#pw-error'); if (err) err.textContent = '';
    });

    const settingsModal = $('#settings-modal');
    if (settingsModal) settingsModal.addEventListener('click', (e) => {
        if (e.target === settingsModal) {
            settingsModal.classList.add('hidden');
            const err = $('#pw-error'); if (err) err.textContent = '';
        }
    });

    const pwForm = $('#change-pw-form');
    if (pwForm) pwForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const current = $('#current-pw').value;
        const newPw = $('#new-pw').value;
        const confirm = $('#confirm-pw').value;

        if (newPw.length < 8) { $('#pw-error').textContent = 'New password must be at least 8 characters.'; return; }
        if (newPw !== confirm) { $('#pw-error').textContent = 'New passwords do not match.'; return; }

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

    // ====== Delete Confirmation ======
    let _pendingDelete = null;

    function confirmDelete(type, id, name) {
        _pendingDelete = { type, id };
        const modal = $('#delete-modal');
        const typeEl = $('#delete-item-type');
        const nameEl = $('#delete-item-name');
        if (!modal) return;
        typeEl.textContent = type;
        nameEl.textContent = name;
        modal.classList.remove('hidden');
    }

    const deleteConfirmBtn = $('#delete-confirm');
    if (deleteConfirmBtn) deleteConfirmBtn.addEventListener('click', async () => {
        if (!_pendingDelete) return;
        const { type, id } = _pendingDelete;
        const btn = deleteConfirmBtn;
        btn.disabled = true;
        btn.textContent = 'Deleting...';

        try {
            const endpoint = type === 'document' ? `/api/documents/${id}`
                : type === 'credential' ? `/api/credentials/${id}`
                : `/api/facts/${id}`;
            const resp = await fetch(endpoint, { method: 'DELETE' });
            if (resp.status === 401) { redirectToLogin(); return; }
            if (resp.ok) {
                showToast(`${type.charAt(0).toUpperCase() + type.slice(1)} deleted`, 'success');
                if (type === 'document') loadDocuments();
                else if (type === 'credential') loadCredentials();
                else loadMemory();
                loadStats();
            } else {
                const data = await resp.json();
                showToast(data.detail || 'Delete failed', 'error');
            }
        } catch {
            showToast('Delete failed', 'error');
        } finally {
            btn.disabled = false;
            btn.textContent = 'Delete';
            _pendingDelete = null;
            $('#delete-modal').classList.add('hidden');
        }
    });

    const deleteCancelBtn = $('#delete-cancel');
    if (deleteCancelBtn) deleteCancelBtn.addEventListener('click', () => {
        _pendingDelete = null;
        $('#delete-modal').classList.add('hidden');
    });

    const deleteModal = $('#delete-modal');
    if (deleteModal) deleteModal.addEventListener('click', (e) => {
        if (e.target === deleteModal) {
            _pendingDelete = null;
            deleteModal.classList.add('hidden');
        }
    });

    // ====== Data Loading with Skeleton States ======
    let _allDocs = [];
    let _allCreds = [];
    let _allFacts = [];

    async function loadDocuments() {
        const container = $('#documents-list');
        if (!container) return;
        container.innerHTML = '<div class="skeleton-grid"><div class="skeleton-card"></div><div class="skeleton-card"></div><div class="skeleton-card"></div></div>';
        try {
            const resp = await fetch('/api/documents');
            if (resp.status === 401) { redirectToLogin(); return; }
            const data = await resp.json();

            _allDocs = data.documents || [];
            if (!_allDocs.length) {
                container.innerHTML = '<p class="empty-state">No documents stored yet.<br><strong>Upload one to get started.</strong></p>';
                return;
            }
            renderDocuments(_allDocs);
        } catch {
            container.innerHTML = '<p class="empty-state">Failed to load documents.</p>';
        }
    }

    function renderDocuments(docs) {
        const container = $('#documents-list');
        if (!container) return;
        if (!docs.length) {
            container.innerHTML = '<p class="empty-state">No matching documents.</p>';
            return;
        }
        container.innerHTML = '';
        docs.forEach((doc, i) => {
            const card = document.createElement('div');
            card.className = 'item-card';
            card.style.animationDelay = `${i * 0.06}s`;

            const subTag = (doc.tags || []).find(t => t.startsWith('sub:'));
            const subLabel = subTag ? `<span class="sub-tag">${subTag.slice(4)}</span>` : '';

            card.innerHTML = `
                <button class="delete-btn" title="Delete document" data-type="document" data-id="${doc.id}" data-name="${doc.name}">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                </button>
                <div class="category">${doc.category} ${subLabel}</div>
                <div class="name">${doc.name}</div>
            `;
            card.querySelector('.delete-btn').addEventListener('click', (e) => {
                e.stopPropagation();
                confirmDelete('document', doc.id, doc.name);
            });
            addTiltEffect(card);
            container.appendChild(card);
        });
    }

    async function loadCredentials() {
        const container = $('#credentials-list');
        if (!container) return;
        container.innerHTML = '<div class="skeleton-list"><div class="skeleton-item"></div><div class="skeleton-item"></div><div class="skeleton-item"></div></div>';
        try {
            const resp = await fetch('/api/credentials');
            if (resp.status === 401) { redirectToLogin(); return; }
            const data = await resp.json();

            _allCreds = data.credentials || [];
            if (!_allCreds.length) {
                container.innerHTML = '<p class="empty-state">No credentials stored yet.<br><strong>Tell Vault a login to save it.</strong></p>';
                return;
            }
            renderCredentials(_allCreds);
        } catch {
            container.innerHTML = '<p class="empty-state">Failed to load credentials.</p>';
        }
    }

    function renderCredentials(creds) {
        const container = $('#credentials-list');
        if (!container) return;
        if (!creds.length) {
            container.innerHTML = '<p class="empty-state">No matching credentials.</p>';
            return;
        }
        container.innerHTML = '';
        creds.forEach((cred, i) => {
            const item = document.createElement('div');
            item.className = 'list-item collapsible';
            item.style.animationDelay = `${i * 0.06}s`;
            item.innerHTML = `
                <div class="list-item-content">
                    <div><div class="service">${cred.service}</div><div class="username">${cred.username || ''}</div></div>
                    <div class="list-item-actions">
                        <button class="delete-btn" title="Delete credential">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                        </button>
                        <svg class="chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
                    </div>
                </div>`;
            item.querySelector('.delete-btn').addEventListener('click', (e) => {
                e.stopPropagation();
                confirmDelete('credential', cred.id, cred.service);
            });
            item.addEventListener('click', (e) => {
                if (!e.target.closest('.delete-btn')) item.classList.toggle('expanded');
            });
            container.appendChild(item);
        });
    }

    async function loadMemory() {
        const container = $('#memory-list');
        if (!container) return;
        container.innerHTML = '<div class="skeleton-list"><div class="skeleton-item"></div><div class="skeleton-item"></div><div class="skeleton-item"></div></div>';
        try {
            const resp = await fetch('/api/facts');
            if (resp.status === 401) { redirectToLogin(); return; }
            const data = await resp.json();

            _allFacts = data.facts || [];
            if (!_allFacts.length) {
                container.innerHTML = '<p class="empty-state">No facts stored yet.<br><strong>Tell me something about yourself!</strong></p>';
                return;
            }
            renderFacts(_allFacts);
        } catch {
            container.innerHTML = '<p class="empty-state">Failed to load memory.</p>';
        }
    }

    function renderFacts(facts) {
        const container = $('#memory-list');
        if (!container) return;
        if (!facts.length) {
            container.innerHTML = '<p class="empty-state">No matching facts.</p>';
            return;
        }
        container.innerHTML = '';

        const grouped = {};
        facts.forEach(f => {
            const cat = f.category || 'general';
            if (!grouped[cat]) grouped[cat] = [];
            grouped[cat].push(f);
        });

        Object.entries(grouped).forEach(([category, items]) => {
            const header = document.createElement('div');
            header.className = 'fact-category-header';
            header.textContent = `[${category.toUpperCase()}]`;
            container.appendChild(header);

            items.forEach((fact, i) => {
                const item = document.createElement('div');
                item.className = 'list-item fact-item';
                item.style.animationDelay = `${i * 0.06}s`;
                item.innerHTML = `
                    <div class="list-item-content">
                        <div><span class="fact-key">${fact.key}:</span> ${fact.value}</div>
                        <button class="delete-btn" title="Delete fact">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                        </button>
                    </div>`;
                item.querySelector('.delete-btn').addEventListener('click', (e) => {
                    e.stopPropagation();
                    confirmDelete('fact', fact.id, `${fact.key}: ${fact.value}`);
                });
                container.appendChild(item);
            });
        });
    }

    // ====== Search-as-you-type ======
    const docSearch = $('#doc-search');
    if (docSearch) docSearch.addEventListener('input', () => {
        const q = docSearch.value.toLowerCase();
        renderDocuments(q ? _allDocs.filter(d =>
            d.name.toLowerCase().includes(q) ||
            d.category.toLowerCase().includes(q) ||
            (d.tags || []).some(t => t.toLowerCase().includes(q))
        ) : _allDocs);
    });

    const credSearch = $('#cred-search');
    if (credSearch) credSearch.addEventListener('input', () => {
        const q = credSearch.value.toLowerCase();
        renderCredentials(q ? _allCreds.filter(c => c.service.toLowerCase().includes(q) || (c.username || '').toLowerCase().includes(q)) : _allCreds);
    });

    const factSearch = $('#fact-search');
    if (factSearch) factSearch.addEventListener('input', () => {
        const q = factSearch.value.toLowerCase();
        renderFacts(q ? _allFacts.filter(f => f.key.toLowerCase().includes(q) || f.value.toLowerCase().includes(q) || (f.category || '').toLowerCase().includes(q)) : _allFacts);
    });

    // ====== Database Viewer ======
    const dbTabs = $$('.db-tab');

    async function loadDatabase() {
        const summaryEl = $('#db-summary');
        const tableEl = $('#db-table-container');
        if (!summaryEl || !tableEl) return;

        try {
            const resp = await fetch('/api/db-viewer');
            if (resp.status === 401) { redirectToLogin(); return; }
            const data = await resp.json();

            summaryEl.innerHTML = '';
            Object.entries(data.tables).forEach(([name, info]) => {
                const chip = document.createElement('div');
                chip.className = 'db-chip';
                chip.innerHTML = `<strong>${name}</strong> &mdash; ${info.count} rows, ${info.columns.length} cols`;
                summaryEl.appendChild(chip);
            });

            const activeTab = $('.db-tab.active');
            if (activeTab) loadDbTable(activeTab.dataset.table);
        } catch {
            summaryEl.innerHTML = '<p class="empty-state">Failed to load database info.</p>';
        }
    }

    async function loadDbTable(table) {
        const container = $('#db-table-container');
        if (!container) return;
        container.innerHTML = '<div class="skeleton-list"><div class="skeleton-item"></div><div class="skeleton-item"></div></div>';

        try {
            const resp = await fetch(`/api/db-viewer/${table}`);
            if (resp.status === 401) { redirectToLogin(); return; }
            const data = await resp.json();

            if (!data.rows || !data.rows.length) {
                container.innerHTML = '<p class="empty-state">No data in this table.</p>';
                return;
            }

            const cols = Object.keys(data.rows[0]);
            let html = '<div class="db-table-scroll"><table class="db-table"><thead><tr>';
            cols.forEach(c => { html += `<th>${c}</th>`; });
            html += '</tr></thead><tbody>';
            data.rows.forEach(row => {
                html += '<tr>';
                cols.forEach(c => {
                    let val = row[c];
                    if (val === null || val === undefined) val = '<span class="null-val">null</span>';
                    else if (typeof val === 'object') val = JSON.stringify(val);
                    else if (typeof val === 'number' && (c.includes('_at') || c === 'created_at' || c === 'updated_at')) {
                        val = new Date(val * 1000).toLocaleString();
                    }
                    else val = String(val);
                    if (val.length > 80) val = val.substring(0, 80) + '...';
                    html += `<td>${val}</td>`;
                });
                html += '</tr>';
            });
            html += '</tbody></table></div>';
            container.innerHTML = html;
        } catch {
            container.innerHTML = '<p class="empty-state">Failed to load table data.</p>';
        }
    }

    dbTabs.forEach(tab => tab.addEventListener('click', () => {
        dbTabs.forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        loadDbTable(tab.dataset.table);
    }));

    // ====== Reindex Button ======
    const reindexBtn = $('#reindex-btn');
    if (reindexBtn) reindexBtn.addEventListener('click', async () => {
        reindexBtn.disabled = true;
        const origText = reindexBtn.innerHTML;
        reindexBtn.innerHTML = '<svg class="spin" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg> Reindexing...';

        try {
            const resp = await fetch('/api/reindex', { method: 'POST' });
            if (resp.status === 401) { redirectToLogin(); return; }
            const data = await resp.json();
            if (resp.ok) {
                showToast(`Reindexed ${data.updated}/${data.total} documents` + (data.errors ? ` (${data.errors} errors)` : ''), data.errors ? 'error' : 'success');
                loadDatabase();
                loadDocuments();
            } else {
                showToast(data.detail || 'Reindex failed', 'error');
            }
        } catch {
            showToast('Reindex failed', 'error');
        } finally {
            reindexBtn.disabled = false;
            reindexBtn.innerHTML = origText;
        }
    });

    // ====== 3D Tilt for Cards ======
    function addTiltEffect(card) {
        card.addEventListener('mousemove', (e) => {
            const rect = card.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;
            const rotateX = (y - rect.height / 2) / (rect.height / 2) * -3;
            const rotateY = (x - rect.width / 2) / (rect.width / 2) * 3;
            card.style.transform = `perspective(600px) translateY(-3px) rotateX(${rotateX}deg) rotateY(${rotateY}deg)`;
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
            const delModal = $('#delete-modal');
            if (delModal && !delModal.classList.contains('hidden')) {
                _pendingDelete = null;
                delModal.classList.add('hidden');
                return;
            }
            const modal = $('#settings-modal');
            if (modal && !modal.classList.contains('hidden')) {
                modal.classList.add('hidden');
                const err = $('#pw-error'); if (err) err.textContent = '';
            }
        }
        if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
            e.preventDefault();
            if (messageInput) messageInput.focus();
        }
    });

    // ====== Send Button Ripple ======
    const sendBtn = $('#send-btn');
    if (sendBtn) sendBtn.addEventListener('click', function(e) {
        const ripple = document.createElement('span');
        const rect = this.getBoundingClientRect();
        ripple.style.cssText = `
            position:absolute;border-radius:50%;pointer-events:none;
            background:rgba(255,255,255,0.3);width:40px;height:40px;
            left:${e.clientX - rect.left - 20}px;top:${e.clientY - rect.top - 20}px;
            animation:rippleOut 0.5s ease forwards;
        `;
        this.style.position = 'relative';
        this.style.overflow = 'hidden';
        this.appendChild(ripple);
        setTimeout(() => ripple.remove(), 500);
    });

    // ====== Status Check ======
    setInterval(async () => {
        try {
            const resp = await fetch('/api/status');
            const data = await resp.json();
            if (data.locked) redirectToLogin();
        } catch {}
    }, 30000);
})();
