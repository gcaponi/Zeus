(function () {
    var modal = document.getElementById('zeus-content-modal');
    var badge = document.getElementById('zeus-content-badge');
    var title = document.getElementById('zeus-content-title');
    var meta = document.getElementById('zeus-content-meta');
    var dnaView = document.getElementById('zeus-dna-view');
    var body = document.getElementById('zeus-content-body');
    var shell = document.querySelector('[data-zeus-admin-shell]');
    var dialog = modal.querySelector('[role="dialog"]');
    var previousFocus = null;

    var SECTION_ICONS = {
        'Chi Siamo': '\u{1F3E2}',
        'Mission': '\u{1F3AF}',
        'Settore': '\u{1F4CB}',
        'Mercato': '\u{1F4CA}',
        'Pilastri': '\u{1F3D7}',
    };

    function focusableItems() {
        return Array.from(dialog.querySelectorAll(
            'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
            'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        )).filter(function (item) {
            return item.getClientRects().length > 0;
        });
    }

    function showModal(trigger) {
        previousFocus = trigger || document.activeElement;
        modal.classList.remove('hidden');
        modal.classList.add('flex');
        modal.setAttribute('aria-hidden', 'false');
        shell.inert = true;
        requestAnimationFrame(function () {
            modal.querySelector('[data-modal-initial-focus]').focus();
        });
    }

    function hideModal() {
        if (modal.classList.contains('hidden')) return;
        modal.classList.add('hidden');
        modal.classList.remove('flex');
        modal.setAttribute('aria-hidden', 'true');
        shell.inert = false;
        if (previousFocus && document.contains(previousFocus)) {
            previousFocus.focus();
        }
        previousFocus = null;
    }

    function trapFocus(event) {
        var items = focusableItems();
        if (!items.length) return;
        var first = items[0];
        var last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    }

    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text) e.textContent = text;
        return e;
    }

    function renderTextSection(field) {
        var card = el('div', 'rounded-2xl border border-white/[0.06] bg-white/[0.02] overflow-hidden');
        var hdr = el('div', 'flex items-center gap-3 border-b border-white/[0.05] px-5 py-3');
        var icon = SECTION_ICONS[field.label] || '\u{25C6}';
        hdr.appendChild(el('span', 'text-sm', icon));
        var lbl = el('span', 'text-sm font-bold tracking-wide text-white/90', field.label);
        hdr.appendChild(lbl);
        card.appendChild(hdr);
        var body = el('div', 'px-5 py-4');
        var paras = field.text.split('\n');
        paras.forEach(function (p) {
            if (p.trim()) {
                body.appendChild(el('p', 'text-sm leading-relaxed text-white/65', p.trim()));
            }
        });
        card.appendChild(body);
        return card;
    }

    function renderListSection(section) {
        var card = el('div', 'rounded-2xl border border-white/[0.06] bg-white/[0.02] overflow-hidden');
        var hdr = el('div', 'flex items-center gap-3 border-b border-white/[0.05] px-5 py-3');
        var icon = SECTION_ICONS[section.label] || '\u{25C6}';
        hdr.appendChild(el('span', 'text-sm', icon));
        hdr.appendChild(el('span', 'text-sm font-bold tracking-wide text-white/90', section.label));
        card.appendChild(hdr);
        var body = el('div', 'px-5 py-4 space-y-2');
        section.items.forEach(function (item, idx) {
            var row = el('div', 'flex gap-3 items-start');
            var num = el('span', 'flex-shrink-0 w-6 h-6 rounded-full bg-[#22d3ee]/10 text-[#22d3ee] text-[10px] font-bold flex items-center justify-center', String(idx + 1));
            row.appendChild(num);
            row.appendChild(el('p', 'text-sm leading-relaxed text-white/65 pt-0.5', item));
            body.appendChild(row);
        });
        card.appendChild(body);
        return card;
    }

    function renderQuestionnaire(q) {
        var card = el('div', 'rounded-2xl border border-[#7c3aed]/20 bg-[#7c3aed]/[0.04] overflow-hidden');
        var hdr = el('div', 'flex items-center gap-3 border-b border-[#7c3aed]/15 px-5 py-3');
        hdr.appendChild(el('span', 'text-sm', '\u{1F4DD}'));
        hdr.appendChild(el('span', 'text-sm font-bold tracking-wide text-white/90', q.label));
        card.appendChild(hdr);
        var wrap = el('div', 'px-5 py-4 space-y-4');
        q.items.forEach(function (item) {
            var qa = el('div', 'rounded-xl border border-white/[0.06] bg-black/10 overflow-hidden');
            var qHdr = el('div', 'flex items-start gap-3 px-4 py-3 border-b border-white/[0.05]');
            var codeTag = el('span', 'flex-shrink-0 px-2 py-0.5 rounded-md bg-[#7c3aed]/15 text-[10px] font-bold text-[#7c3aed] tracking-wide', item.code);
            qHdr.appendChild(codeTag);
            if (item.principle) {
                var princTag = el('span', 'px-2 py-0.5 rounded-md bg-white/[0.05] text-[10px] font-medium text-white/40', item.principle);
                qHdr.appendChild(princTag);
            }
            qa.appendChild(qHdr);
            var qBody = el('div', 'px-4 py-3');
            qBody.appendChild(el('p', 'text-xs font-semibold text-white/50 uppercase tracking-wide mb-1.5', 'Domanda'));
            qBody.appendChild(el('p', 'text-sm leading-relaxed text-white/80 mb-3', item.question));
            qBody.appendChild(el('p', 'text-xs font-semibold text-white/50 uppercase tracking-wide mb-1.5', 'Risposta'));
            qBody.appendChild(el('p', 'text-sm leading-relaxed text-white/65', item.answer));
            qa.appendChild(qBody);
            wrap.appendChild(qa);
        });
        card.appendChild(wrap);
        return card;
    }

    function renderProfile(profile) {
        var card = el('div', 'rounded-2xl border border-white/[0.06] bg-white/[0.02] overflow-hidden');
        var hdr = el('div', 'flex items-center gap-3 border-b border-white/[0.05] px-5 py-3');
        hdr.appendChild(el('span', 'text-sm', '\u{2699}'));
        hdr.appendChild(el('span', 'text-sm font-bold tracking-wide text-white/90', 'Profilo Questionario'));
        card.appendChild(hdr);
        var body = el('div', 'px-5 py-4 flex flex-wrap gap-3');
        if (profile.plan_label) {
            var pill = el('span', 'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-[#22d3ee]/20 bg-[#22d3ee]/10 text-[11px] font-semibold text-[#22d3ee]', profile.plan_label);
            body.appendChild(pill);
        }
        if (profile.answer_depth) {
            var pill2 = el('span', 'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-white/10 bg-white/[0.04] text-[11px] font-medium text-white/50', 'Profondit\u00e0: ' + profile.answer_depth);
            body.appendChild(pill2);
        }
        card.appendChild(body);
        return card;
    }

    function renderDna(data) {
        dnaView.innerHTML = '';
        data.text_fields.forEach(function (f) { dnaView.appendChild(renderTextSection(f)); });
        data.sections.forEach(function (s) { dnaView.appendChild(renderListSection(s)); });
        if (data.questionnaire) { dnaView.appendChild(renderQuestionnaire(data.questionnaire)); }
        if (data.profile) { dnaView.appendChild(renderProfile(data.profile)); }
    }

    function openContent(url, trigger) {
        badge.textContent = 'Anteprima';
        badge.className = 'text-[11px] font-semibold uppercase tracking-[0.12em] text-[#22d3ee]';
        title.textContent = 'Caricamento...';
        meta.textContent = 'Caricamento contenuto';
        body.removeAttribute('role');
        dnaView.innerHTML = '';
        dnaView.classList.add('hidden');
        body.classList.add('hidden');
        body.textContent = '';
        showModal(trigger);

        fetch(url, { headers: { Accept: 'application/json' } })
            .then(function (r) { if (!r.ok) throw new Error('Contenuto non disponibile'); return r.json(); })
            .then(function (data) {
                title.textContent = data.title || 'Contenuto';
                meta.textContent = data.meta || '';
                if (data.type === 'dna') {
                    badge.textContent = 'DNA';
                    badge.className = 'text-[11px] font-semibold uppercase tracking-[0.12em] text-[#a3e635]';
                    renderDna(data);
                    dnaView.classList.remove('hidden');
                } else {
                    badge.textContent = 'Allegato';
                    badge.className = 'text-[11px] font-semibold uppercase tracking-[0.12em] text-[#22d3ee]';
                    body.textContent = data.content || 'Nessun contenuto disponibile.';
                    body.classList.remove('hidden');
                }
            })
            .catch(function (err) {
                badge.textContent = 'Errore';
                badge.className = 'text-[11px] font-semibold uppercase tracking-[0.12em] text-[#fb7185]';
                title.textContent = 'Errore apertura';
                meta.textContent = '';
                body.textContent = err.message || 'Impossibile aprire questo contenuto.';
                body.classList.remove('hidden');
            });
    }

    document.querySelectorAll('[data-open-content]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            openContent(btn.dataset.contentUrl, btn);
        });
    });
    document.querySelectorAll('[data-close-content]').forEach(function (btn) {
        btn.addEventListener('click', hideModal);
    });
    document.addEventListener('keydown', function (event) {
        if (modal.classList.contains('hidden')) return;
        if (event.key === 'Escape') {
            event.preventDefault();
            hideModal();
        } else if (event.key === 'Tab') {
            trapFocus(event);
        }
    });
})();
