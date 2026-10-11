document.addEventListener('DOMContentLoaded', () => {
    const page = document.getElementById('hiddenTitlesPage');
    if (!page) return;
    loadHiddenTitles();
});

async function loadHiddenTitles() {
    const loading = document.getElementById('htLoadingState');
    const empty = document.getElementById('htEmptyState');
    const list = document.getElementById('htList');
    loading.classList.remove('hidden');
    empty.classList.add('hidden');
    list.classList.add('hidden');

    try {
        const res = await fetch('/api/titles/hidden');
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'Failed to load hidden titles');

        const titles = Array.isArray(data.titles) ? data.titles : [];
        loading.classList.add('hidden');

        if (titles.length === 0) {
            empty.classList.remove('hidden');
            return;
        }

        document.getElementById('htTableBody').innerHTML = titles.map(htRenderRow).join('');
        list.classList.remove('hidden');
    } catch (err) {
        loading.classList.add('hidden');
        htNotify('Failed to load hidden titles: ' + err.message, 'error');
    }
}

function htRenderRow(entry) {
    const statusBadge = entry.owned
        ? '<span class="badge badge-success badge-soft badge-sm">Owned</span>'
        : '<span class="badge badge-ghost badge-sm">Not Owned</span>';

    return `
    <tr data-asin="${htEsc(entry.asin)}">
        <td class="font-medium">${htEsc(entry.title || 'Unknown Title')}</td>
        <td>${entry.author ? `<a href="/authors/${encodeURIComponent(entry.author)}" class="text-primary hover:underline">${htEsc(entry.author)}</a>` : '-'}</td>
        <td>${htEsc(entry.series || '-')}</td>
        <td>${statusBadge}</td>
        <td class="text-right">
            <button type="button" class="btn btn-xs btn-outline btn-primary rounded gap-1"
                    onclick="htUnhide(this, '${htEsc(entry.asin)}', '${htEsc(entry.title || '')}')">
                Unhide
            </button>
        </td>
    </tr>`;
}

async function htUnhide(btn, asin, title) {
    btn.disabled = true;
    try {
        const res = await fetch(`/api/titles/${encodeURIComponent(asin)}/monitored`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ monitored: true }),
        });
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'Failed to unhide');

        htNotify(`"${title}" unhidden.`, 'success');
        const row = btn.closest('tr');
        if (row) row.remove();

        const remaining = document.querySelectorAll('#htTableBody tr').length;
        if (remaining === 0) {
            document.getElementById('htList').classList.add('hidden');
            document.getElementById('htEmptyState').classList.remove('hidden');
        }
    } catch (err) {
        htNotify('Failed to unhide: ' + err.message, 'error');
    } finally {
        btn.disabled = false;
    }
}

function htEsc(str) {
    if (str == null) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

let _htNotifyTimer = null;
function htNotify(message, type = 'info') {
    let toast = document.getElementById('htToast');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'htToast';
        toast.className = 'toast toast-top toast-end z-50';
        document.body.appendChild(toast);
    }
    const cls = { success: 'alert-success', error: 'alert-error', warning: 'alert-warning', info: 'alert-info' }[type] || 'alert-info';
    toast.innerHTML = `<div class="alert ${cls} shadow-lg"><span>${htEsc(message)}</span></div>`;
    toast.classList.remove('hidden');
    clearTimeout(_htNotifyTimer);
    _htNotifyTimer = setTimeout(() => toast.classList.add('hidden'), 3500);
}
