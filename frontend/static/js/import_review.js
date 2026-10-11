(function () {
    "use strict";

    const section = document.getElementById("review");
    const listEl = document.getElementById("importReviewList");
    if (!section || !listEl) {
        return;
    }

    const API = "/api/import/review";
    const esc = (value) => AA.escapeHtml(value == null ? "" : String(value));

    function suggestion(item) {
        if (item.state === "needs_review" && item.suggested_title) {
            const who = item.suggested_author ? ` by ${esc(item.suggested_author)}` : "";
            const score = item.confidence != null ? ` · ${Math.round(Number(item.confidence) * 100)}% match` : "";
            return `Suggested: <b>${esc(item.suggested_title)}</b>${who}${score}`;
        }
        return esc(item.message || "No confident match found.");
    }

    function render(items) {
        section.classList.toggle("hidden", items.length === 0);
        listEl.innerHTML = items.map((item) => `
            <li class="import-review-item" data-id="${esc(item.id)}">
                <div class="min-w-0">
                    <div class="import-review-path">${item.library_name ? `<span class="badge badge-ghost badge-xs mr-1">${esc(item.library_name)}</span>` : ""}${esc(item.rel_path || item.name)}</div>
                    <div class="import-review-sugg">${suggestion(item)}</div>
                </div>
                <div class="import-review-actions">
                    ${item.state === "needs_review" && item.suggested_asin
                        ? `<button type="button" class="btn btn-primary btn-xs" data-action="accept">Accept</button>` : ""}
                    <button type="button" class="btn btn-ghost btn-xs" data-action="ignore">Ignore</button>
                </div>
            </li>`).join("");
    }

    async function load() {
        const { ok, data } = await AA.apiFetch(API, { method: "GET" });
        if (ok && data && data.success !== false) {
            render(data.items || []);
        }
    }

    listEl.addEventListener("click", async (event) => {
        const button = event.target.closest("[data-action]");
        const row = event.target.closest("[data-id]");
        if (!button || !row) {
            return;
        }
        button.disabled = true;
        const action = button.dataset.action;
        const { ok, data } = await AA.apiFetch(`${API}/${row.dataset.id}/${action}`, { method: "POST" });
        if (ok && data && data.success !== false) {
            if (typeof window.showNotification === "function") {
                window.showNotification(action === "accept"
                    ? `Imported ${data.title || "book"} in place.` : "Folder ignored.", "success");
            }
            load();
        } else {
            button.disabled = false;
            if (typeof window.showNotification === "function") {
                window.showNotification((data && (data.message || data.error)) || "Action failed", "error");
            }
        }
    });

    load();
})();
