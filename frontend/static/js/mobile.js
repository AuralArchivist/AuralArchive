// --- Phone sheets -------------------------------------------------------------
// A [data-sheet-open="id"] control opens that dialog; a tap outside the sheet
// box (on the backdrop) or on [data-sheet-close] closes it. A [data-click="id"]
// row closes its sheet and clicks that control.

(function () {
    'use strict';

    document.addEventListener('click', function (event) {
        // Sheet rows that stand in for a control kept elsewhere on the page
        const proxy = event.target.closest('[data-click]');
        if (proxy) {
            proxy.closest('dialog')?.close();
            document.getElementById(proxy.dataset.click).click();
            return;
        }
        const opener = event.target.closest('[data-sheet-open]');
        if (opener) {
            const sheet = document.getElementById(opener.dataset.sheetOpen);
            if (sheet && !sheet.open) sheet.showModal();
            return;
        }
        const sheet = event.target.closest('dialog.aa-sheet');
        if (sheet && (event.target === sheet || event.target.closest('[data-sheet-close]'))) {
            sheet.close();
        }
    });

    // --- Panels: page sections shown as a bottom sheet on phones -------------
    // [data-panel-open="id"] adds .is-open to that element. A tap outside it is
    // swallowed and closes it, so the page underneath does not react.

    let panel = null;

    function closePanel() {
        panel.classList.remove('is-open');
        document.documentElement.classList.remove('aa-panel-open');
        panel = null;
    }

    document.addEventListener('click', function (event) {
        if (panel) {
            if (!panel.contains(event.target)) {
                event.preventDefault();
                event.stopPropagation();
                closePanel();
            } else if (event.target.closest('[data-panel-close]')) {
                closePanel();
            }
            return;
        }
        const opener = event.target.closest('[data-panel-open]');
        if (opener) {
            panel = document.getElementById(opener.dataset.panelOpen);
            panel.classList.add('is-open');
            document.documentElement.classList.add('aa-panel-open');
        }
    }, true);

    document.addEventListener('keydown', function (event) {
        if (panel && event.key === 'Escape') closePanel();
    });
})();
