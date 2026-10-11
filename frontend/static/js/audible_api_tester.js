document.addEventListener('DOMContentLoaded', () => {
    const callTypeSelect = document.getElementById('callType');
    const pathInput = document.getElementById('callPath');
    const asinInput = document.getElementById('callAsin');
    const titleSearchInput = document.getElementById('titleSearch');
    const titleResults = document.getElementById('titleResults');
    const extraGroupsInput = document.getElementById('extraGroups');
    const extraParamsInput = document.getElementById('extraParams');
    const sendBtn = document.getElementById('sendBtn');
    const copyBtn = document.getElementById('copyBtn');
    const resultMeta = document.getElementById('resultMeta');
    const resultBody = document.getElementById('resultBody');
    const resultStatus = document.getElementById('resultStatus');
    const groupCheckboxes = Array.from(document.querySelectorAll('.response-group-cb'));

    function applyPreset() {
        const opt = callTypeSelect.options[callTypeSelect.selectedIndex];
        const isCustom = callTypeSelect.value === 'custom';
        const presetPath = opt.dataset.path || '';
        const presetGroups = (opt.dataset.groups || '').split(',').map(g => g.trim()).filter(Boolean);

        pathInput.value = presetPath;
        pathInput.readOnly = !isCustom;
        pathInput.classList.toggle('opacity-60', !isCustom);

        groupCheckboxes.forEach(cb => {
            cb.checked = presetGroups.includes(cb.value);
        });
        extraGroupsInput.value = '';

        asinInput.placeholder = opt.dataset.needsAsin === 'true' ? 'B0XXXXXXXX (required)' : 'B0XXXXXXXX (not required)';
    }

    callTypeSelect.addEventListener('change', applyPreset);
    applyPreset();

    const runTitleSearch = AA.debounce(async (query) => {
        if (!query || query.length < 2) {
            titleResults.classList.add('hidden');
            titleResults.innerHTML = '';
            return;
        }
        try {
            const res = await fetch(`/tests/audible_api/search-titles?q=${encodeURIComponent(query)}`);
            const data = await res.json();
            const rows = (data.results || []);
            if (!rows.length) {
                titleResults.innerHTML = '<div class="px-3 py-2 text-xs text-base-content/40">No matches in your library</div>';
                titleResults.classList.remove('hidden');
                return;
            }
            titleResults.innerHTML = rows.map(r => `
                <button type="button" class="title-result-row w-full text-left px-3 py-2 text-xs hover:bg-base-200 border-b border-base-content/5 last:border-0"
                        data-asin="${AA.escapeHtml(r.asin || '')}">
                    <div class="font-medium truncate">${AA.escapeHtml(r.title || 'Unknown title')}</div>
                    <div class="text-base-content/50 truncate">${AA.escapeHtml(r.author || '')} &middot; ${AA.escapeHtml(r.asin || '')}</div>
                </button>
            `).join('');
            titleResults.classList.remove('hidden');
        } catch (err) {
            titleResults.classList.add('hidden');
        }
    }, 250);

    titleSearchInput.addEventListener('input', (e) => runTitleSearch(e.target.value.trim()));

    titleResults.addEventListener('click', (e) => {
        const row = e.target.closest('.title-result-row');
        if (!row) return;
        asinInput.value = row.dataset.asin || '';
        titleSearchInput.value = row.querySelector('.font-medium')?.textContent || '';
        titleResults.classList.add('hidden');
        titleResults.innerHTML = '';
    });

    document.addEventListener('click', (e) => {
        if (!titleResults.contains(e.target) && e.target !== titleSearchInput) {
            titleResults.classList.add('hidden');
        }
    });

    sendBtn.addEventListener('click', async () => {
        const selectedGroups = groupCheckboxes.filter(cb => cb.checked).map(cb => cb.value);
        const extraGroups = extraGroupsInput.value.split(',').map(g => g.trim()).filter(Boolean);
        const responseGroups = [...selectedGroups, ...extraGroups].join(', ');

        const payload = {
            call_type: callTypeSelect.value,
            path: pathInput.value.trim(),
            asin: asinInput.value.trim(),
            response_groups: responseGroups,
            extra_params: extraParamsInput.value,
        };

        sendBtn.disabled = true;
        sendBtn.classList.add('loading');
        resultMeta.textContent = '';
        resultBody.textContent = 'Sending request…';
        copyBtn.classList.add('hidden');
        resultStatus.classList.add('hidden');
        resultStatus.classList.remove('success', 'error');

        try {
            const res = await fetch('/tests/audible_api/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await res.json();

            const metaParts = [];
            if (data.path) metaParts.push(`GET ${data.path}`);
            if (data.params_sent && Object.keys(data.params_sent).length) {
                metaParts.push(`params: ${JSON.stringify(data.params_sent)}`);
            }
            resultMeta.textContent = metaParts.join('\n');

            resultStatus.classList.remove('hidden');
            if (data.success) {
                resultStatus.textContent = 'Success';
                resultStatus.classList.add('success');
                resultBody.textContent = JSON.stringify(data.result, null, 2);
                copyBtn.classList.remove('hidden');
            } else {
                resultStatus.textContent = 'Error';
                resultStatus.classList.add('error');
                resultBody.textContent = `Error: ${data.error || 'Unknown error'}`;
            }
        } catch (err) {
            resultStatus.classList.remove('hidden');
            resultStatus.textContent = 'Error';
            resultStatus.classList.add('error');
            resultMeta.textContent = '';
            resultBody.textContent = `Request failed: ${err.message}`;
        } finally {
            sendBtn.disabled = false;
            sendBtn.classList.remove('loading');
        }
    });

    copyBtn.addEventListener('click', () => {
        navigator.clipboard.writeText(resultBody.textContent || '').then(() => {
            const original = copyBtn.innerHTML;
            copyBtn.innerHTML = '<i class="fas fa-check"></i> Copied';
            setTimeout(() => { copyBtn.innerHTML = original; }, 1200);
        });
    });
});
