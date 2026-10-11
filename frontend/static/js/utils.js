// Loaded before page scripts; everything lives on window.AA to keep globals clean.

'use strict';

window.AA = window.AA || {};


/**
 * Safely escape HTML to prevent XSS when inserting user-supplied text.
 * @param {*} text
 * @returns {string}
 */
AA.escapeHtml = function (text) {
    if (text === null || text === undefined) return '';
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
};


AA.formatSize = function (bytes) {
    if (!bytes || bytes === 0) return 'Unknown';
    const gb = bytes / (1024 * 1024 * 1024);
    const mb = bytes / (1024 * 1024);
    if (gb >= 1) return `${gb.toFixed(2)} GB`;
    if (mb >= 1) return `${mb.toFixed(0)} MB`;
    return `${bytes} B`;
};

/**
 * Relative time for an ISO timestamp: "5 min ago", "in 2 h", or "soon".
 * @param {string|null} iso
 * @param {boolean} future
 * @returns {string|null}
 */
AA.relativeTime = function (iso, future) {
    if (!iso) return null;
    const diffMs = new Date(iso).getTime() - Date.now();
    const mins = Math.round(Math.abs(diffMs) / 60000);
    if (future && diffMs <= 60000) return 'soon';
    let text;
    if (mins < 60) {
        text = `${Math.max(1, mins)} min`;
    } else if (mins < 60 * 24) {
        text = `${Math.round(mins / 60)} h`;
    } else {
        const days = Math.round(mins / 1440);
        text = `${days} day${days === 1 ? '' : 's'}`;
    }
    return future ? `in ${text}` : `${text} ago`;
};

// Date part of a stored date or timestamp ("2024-01-30T00:00:00" -> "2024-01-30")
AA.formatDate = function (value) {
    const text = value == null ? '' : String(value);
    const match = text.match(/^(\d{4}-\d{2}-\d{2})[T ]/);
    return match ? match[1] : text;
};

AA.formatDuration = function (seconds) {
    const value = Number(seconds);
    if (!Number.isFinite(value) || value < 0) return '0s';
    const h = Math.floor(value / 3600);
    const m = Math.floor((value % 3600) / 60);
    const s = Math.floor(value % 60);
    if (h > 0) return m > 0 ? `${h}h ${m}m` : `${h}h`;
    if (m > 0) return s > 0 ? `${m}m ${s}s` : `${m}m`;
    return `${s}s`;
};


AA.debounce = function (fn, ms) {
    let timer;
    return function (...args) {
        clearTimeout(timer);
        timer = setTimeout(() => fn.apply(this, args), ms);
    };
};


/**
 * Fetch wrapper with JSON parsing and normalised error handling.
 * Always resolves (never rejects on HTTP errors); throws only on network failure.
 * @param {string} url
 * @param {RequestInit} [options]
 * @returns {Promise<{ok: boolean, status: number, data: any}>}
 */
AA.apiFetch = async function (url, options = {}) {
    const defaults = {
        headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
    };
    const merged = {
        ...defaults,
        ...options,
        headers: { ...defaults.headers, ...(options.headers || {}) },
    };
    const response = await fetch(url, merged);
    let data = null;
    try {
        data = await response.json();
    } catch (_) { /* non-JSON body */ }
    return { ok: response.ok, status: response.status, data };
};


AA.bookCoverImg = function (src, alt, imgClass = '', divClass = '') {
    if (src) {
        return `<img src="${AA.escapeHtml(src)}" alt="${AA.escapeHtml(alt)}" loading="lazy"${imgClass ? ` class="${imgClass}"` : ''}>`;
    }
    return `<div class="book-modal__cover book-modal__cover--placeholder${divClass ? ' ' + divClass : ''}">AUDIO<br>BOOK</div>`;
};

/**
 * Show a DaisyUI modal confirmation dialog.
 * Returns a Promise<boolean> that resolves true on confirm, false on cancel.
 *
 * Falls back to window.confirm() if the #aaConfirmDialog element is absent.
 *
 * @param {string} message        - Body text
 * @param {string} [title]        - Dialog heading  (default: 'Confirm')
 * @param {string} [confirmLabel] - Confirm button label  (default: 'Confirm')
 * @param {string} [confirmClass] - DaisyUI btn class for confirm  (default: 'btn-error')
 * @returns {Promise<boolean>}
 */
AA.showConfirmDialog = function (message, title = 'Confirm', confirmLabel = 'Confirm', confirmClass = 'btn-error') {
    return new Promise((resolve) => {
        const dialog = document.getElementById('aaConfirmDialog');
        if (!dialog) {
            resolve(window.confirm(message));
            return;
        }

        document.getElementById('aaConfirmTitle').textContent = title;
        document.getElementById('aaConfirmMessage').textContent = message;

        const confirmBtn = document.getElementById('aaConfirmOk');
        confirmBtn.textContent = confirmLabel;
        confirmBtn.className = `btn ${confirmClass}`;

        const cancelBtn = document.getElementById('aaConfirmCancel');

        function cleanup(result) {
            dialog.close();
            confirmBtn.removeEventListener('click', onConfirm);
            cancelBtn.removeEventListener('click', onCancel);
            dialog.removeEventListener('cancel', onCancel);
            resolve(result);
        }
        function onConfirm() { cleanup(true); }
        function onCancel() { cleanup(false); }

        confirmBtn.addEventListener('click', onConfirm);
        cancelBtn.addEventListener('click', onCancel);
        dialog.addEventListener('cancel', onCancel, { once: true });

        dialog.showModal();
        cancelBtn.focus();
    });
};
