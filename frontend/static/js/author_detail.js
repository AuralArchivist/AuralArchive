document.addEventListener('DOMContentLoaded', () => {
    const importAuthorButton = document.querySelector('.js-import-author');
    if (importAuthorButton) {
        importAuthorButton.addEventListener('click', async () => {
            const authorName = importAuthorButton.dataset.author;
            if (!authorName) {
                showNotification('Author name missing for import.', 'error');
                return;
            }

            const confirmMessage = `Import all catalog titles for ${authorName}?`;
            if (!await AA.showConfirmDialog(confirmMessage, 'Import Author')) {
                return;
            }

            handleImportRequest(
                '/authors/api/import-author',
                { author_name: authorName },
                importAuthorButton
            );
        });
    }

    const seriesButtons = document.querySelectorAll('.js-import-series');
    seriesButtons.forEach((button) => {
        button.addEventListener('click', async () => {
            const authorName = button.dataset.author;
            const seriesName = button.dataset.series;

            if (!authorName || !seriesName) {
                showNotification('Series import metadata is incomplete.', 'error');
                return;
            }

            const confirmMessage = `Import every title from series "${seriesName}"?`;
            if (!await AA.showConfirmDialog(confirmMessage, 'Import Series')) {
                return;
            }

            handleImportRequest(
                '/authors/api/import-series',
                { author_name: authorName, series_name: seriesName },
                button
            );
        });
    });

    const standaloneButton = document.querySelector('.js-import-standalone');
    if (standaloneButton) {
        standaloneButton.addEventListener('click', async () => {
            const authorName = standaloneButton.dataset.author;
            if (!authorName) {
                showNotification('Author name missing for import.', 'error');
                return;
            }

            const confirmMessage = `Import standalone titles for ${authorName}?`;
            if (!await AA.showConfirmDialog(confirmMessage, 'Import Standalone')) {
                return;
            }

            handleImportRequest(
                '/authors/api/import-standalone',
                { author_name: authorName },
                standaloneButton
            );
        });
    }

    const standaloneBookButtons = document.querySelectorAll('.js-import-book');
    standaloneBookButtons.forEach((button) => {
        button.addEventListener('click', async () => {
            const authorName = button.dataset.author;
            const asin = button.dataset.asin;

            if (!authorName || !asin) {
                showNotification('Missing data for book import.', 'error');
                return;
            }

            const confirmMessage = `Import this title (ASIN: ${asin}) into your library?`;
            if (!await AA.showConfirmDialog(confirmMessage, 'Import Book', 'Import', 'btn-primary')) {
                return;
            }

            handleImportRequest(
                '/authors/api/import-book',
                { author_name: authorName, asin },
                button
            );
        });
    });

    initHiddenTitles();
});

// --- Hidden / unmonitored titles --------------------------------------------

function initHiddenTitles() {
    const rows = document.querySelectorAll('.author-book-row');
    const hiddenWrap = document.getElementById('adShowHiddenWrap');
    const hiddenToggle = document.getElementById('adShowHiddenToggle');
    const hiddenLabel = document.getElementById('adShowHiddenLabel');

    const hiddenCount = Array.from(rows).filter(r => r.dataset.monitored === 'false').length;
    if (hiddenCount > 0 && hiddenWrap) {
        hiddenWrap.classList.remove('hidden');
        hiddenLabel.textContent = `Show hidden titles (${hiddenCount})`;
    }

    applyHiddenTitleFilter();

    if (hiddenToggle) {
        hiddenToggle.addEventListener('change', applyHiddenTitleFilter);
    }

    document.querySelectorAll('.js-toggle-monitored').forEach((button) => {
        button.addEventListener('click', async () => {
            const asin = button.dataset.asin;
            const title = button.dataset.title;
            const authorName = button.dataset.author;
            const seriesTitle = button.dataset.seriesTitle || '';
            const wasMonitored = button.dataset.monitored !== 'false';
            const row = button.closest('.author-book-row');

            button.disabled = true;
            try {
                const res = await fetch(`/api/titles/${encodeURIComponent(asin)}/monitored`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        monitored: !wasMonitored,
                        title,
                        author: authorName,
                        series_title: seriesTitle,
                        source_context: 'author',
                    }),
                });
                const data = await res.json();
                if (!data.success) throw new Error(data.error || 'Failed to update');

                const nowMonitored = !wasMonitored;
                button.dataset.monitored = nowMonitored ? 'true' : 'false';
                button.title = nowMonitored ? 'Hide this title' : 'Unhide this title';
                button.querySelector('i').className = `fas ${nowMonitored ? 'fa-eye-slash' : 'fa-eye'}`;
                if (row) {
                    row.dataset.monitored = nowMonitored ? 'true' : 'false';
                    row.classList.toggle('opacity-50', !nowMonitored);
                }
                showNotification(nowMonitored ? 'Title unhidden.' : 'Title hidden.', 'success');

                const currentHiddenCount = document.querySelectorAll('.author-book-row[data-monitored="false"]').length;
                if (hiddenWrap) {
                    hiddenWrap.classList.toggle('hidden', currentHiddenCount === 0);
                    if (hiddenLabel) hiddenLabel.textContent = `Show hidden titles (${currentHiddenCount})`;
                }
                applyHiddenTitleFilter();
            } catch (error) {
                showNotification(error.message || 'Failed to update title.', 'error');
            } finally {
                button.disabled = false;
            }
        });
    });
}

function applyHiddenTitleFilter() {
    const showHidden = document.getElementById('adShowHiddenToggle')?.checked || false;
    document.querySelectorAll('.author-book-row').forEach((row) => {
        const isHidden = row.dataset.monitored === 'false';
        row.classList.toggle('hidden', isHidden && !showHidden);
    });
}

async function handleImportRequest(url, payload, button) {
    setLoadingState(button, true);

    try {
        const data = await sendImportRequest(url, payload);

        if (data.success) {
            showNotification(data.message || 'Import complete.', 'success');
        } else {
            let warningMessage = data.error || 'No titles imported.';

            const skippedMessages = [];
            if (Number(data.already_in_library) > 0) {
                skippedMessages.push(`${data.already_in_library} already in library`);
            }
            if (Number(data.language_skipped) > 0) {
                skippedMessages.push(`${data.language_skipped} filtered by language`);
            }
            if (Number(data.missing_raw) > 0) {
                skippedMessages.push(`${data.missing_raw} skipped due to missing metadata`);
            }

            if (skippedMessages.length) {
                warningMessage += ` (${skippedMessages.join(', ')})`;
            }

            showNotification(warningMessage, 'warning');
        }
    } catch (error) {
        showNotification(error.message || 'Import failed.', 'error');
    } finally {
        setLoadingState(button, false);
    }
}

async function sendImportRequest(url, payload) {
    const response = await fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload)
    });

    let data = {};
    try {
        data = await response.json();
    } catch (error) {
    }

    if (!response.ok) {
        const message = data.error || `Request failed (${response.status})`;
        throw new Error(message);
    }

    return data;
}

function setLoadingState(button, isLoading) {
    if (!button) {
        return;
    }

    if (isLoading) {
        button.disabled = true;
        button.classList.add('loading');
        button.setAttribute('aria-busy', 'true');
    } else {
        button.disabled = false;
        button.classList.remove('loading');
        button.removeAttribute('aria-busy');
    }
}
