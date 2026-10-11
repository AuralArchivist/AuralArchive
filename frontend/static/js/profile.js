(function () {
    "use strict";


    function formatDate(isoString) {
        if (!isoString) return "—";
        try {
            return new Date(isoString).toLocaleDateString(undefined, {
                year: "numeric", month: "long", day: "numeric",
            });
        } catch (e) {
            return isoString;
        }
    }
    function notifyError(message, err) {
        console.error(message, err);
        if (typeof showNotification === "function") {
            showNotification(message, "error");
        }
    }

    function showStatus(el, msg, isError) {
        if (!el) return;
        el.textContent = msg;
        el.classList.remove("hidden", "text-success", "text-error");
        el.classList.add(isError ? "text-error" : "text-success");
        setTimeout(function () {
            el.textContent = "";
            el.classList.add("hidden");
        }, 4000);
    }


    async function loadProfile() {
        try {
            const resp = await AA.apiFetch("/api/profile/me");
            if (!resp.ok) return;
            const data = resp.data || {};

            const usernameEl    = document.getElementById("profileUsername");
            const displayNameEl = document.getElementById("profileDisplayName");
            const createdAtEl   = document.getElementById("profileCreatedAt");
            if (usernameEl)    usernameEl.value = data.username || "";
            if (displayNameEl) displayNameEl.value = data.display_name || "";
            if (createdAtEl)   createdAtEl.textContent = formatDate(data.created_at);
        } catch (err) {
            notifyError("Failed to load profile", err);
        }
    }


    const saveDisplayNameBtn  = document.getElementById("saveDisplayNameBtn");
    const displayNameStatus   = document.getElementById("displayNameStatus");

    if (saveDisplayNameBtn) {
        saveDisplayNameBtn.addEventListener("click", async function () {
            const name = (document.getElementById("profileDisplayName").value || "").trim();
            if (!name) return;
            saveDisplayNameBtn.disabled = true;
            try {
                const resp = await AA.apiFetch("/api/profile/update", {
                    method: "POST",
                    body: JSON.stringify({ display_name: name }),
                });
                const d = resp.data || {};
                if (!resp.ok || d.success === false) {
                    showStatus(displayNameStatus, d.error || "Save failed", true);
                } else {
                    showStatus(displayNameStatus, "Saved", false);
                }
            } catch (err) {
                showStatus(displayNameStatus, "Save failed", true);
            } finally {
                saveDisplayNameBtn.disabled = false;
            }
        });
    }


    const changePasswordBtn = document.getElementById("changePasswordBtn");
    const passwordStatus    = document.getElementById("passwordStatus");

    if (changePasswordBtn) {
        changePasswordBtn.addEventListener("click", async function () {
            const current = document.getElementById("pwCurrent").value;
            const newPw   = document.getElementById("pwNew").value;
            const confirm = document.getElementById("pwConfirm").value;

            if (!current || !newPw || !confirm) {
                showStatus(passwordStatus, "All fields are required", true);
                return;
            }

            changePasswordBtn.disabled = true;
            try {
                const resp = await AA.apiFetch("/api/profile/change-password", {
                    method: "POST",
                    body: JSON.stringify({
                        current_password: current,
                        new_password: newPw,
                        confirm_password: confirm,
                    }),
                });
                const d = resp.data || {};
                if (!resp.ok || d.success === false) {
                    showStatus(passwordStatus, d.error || "Failed to change password", true);
                } else {
                    showStatus(passwordStatus, "Password changed successfully", false);
                    document.getElementById("pwCurrent").value = "";
                    document.getElementById("pwNew").value     = "";
                    document.getElementById("pwConfirm").value = "";
                }
            } catch (err) {
                showStatus(passwordStatus, "Failed to change password", true);
            } finally {
                changePasswordBtn.disabled = false;
            }
        });
    }


    loadProfile();
})();
