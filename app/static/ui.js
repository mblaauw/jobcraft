// Small progressive enhancements shared by every page. Everything works without
// this file; it replaces the inline scripts and onchange attributes that used to
// live in the templates (which also keeps a strict Content-Security-Policy possible).
(function () {
  document.documentElement.classList.add("js");

  // Filters and selects that apply themselves: <form data-autosubmit>.
  document.querySelectorAll("form[data-autosubmit]").forEach((form) => {
    form.addEventListener("change", (event) => {
      const target = event.target;
      if (target.matches("select, input[type=checkbox], input[type=radio]")) form.requestSubmit();
    });
  });

  // Prevent double posts and show that a change is being saved.
  const announcement = document.getElementById("jobs-action-status");
  document.querySelectorAll("[data-pending-form]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (form.getAttribute("aria-busy") === "true") {
        event.preventDefault();
        return;
      }
      // Don't disable named controls: their values must still reach the server.
      form.setAttribute("aria-busy", "true");
      if (event.submitter) event.submitter.classList.add("is-saving");
      if (announcement) {
        announcement.textContent = "Wijziging wordt opgeslagen…";
        announcement.classList.remove("visually-hidden");
      }
    });
  });
  window.addEventListener("pageshow", () => {
    document.querySelectorAll('[data-pending-form][aria-busy="true"]').forEach((form) => form.removeAttribute("aria-busy"));
    document.querySelectorAll(".is-saving").forEach((button) => button.classList.remove("is-saving"));
    if (announcement) {
      announcement.textContent = "";
      announcement.classList.add("visually-hidden");
    }
  });

  // Document generation: one click, clear feedback.
  document.addEventListener("submit", (event) => {
    const form = event.target.closest("form[data-doc-form]");
    if (!form || form.closest("dialog")) return;
    document.querySelectorAll("form[data-doc-form] button").forEach((button) => { button.disabled = true; });
    const status = document.getElementById("doc-status");
    if (status) status.textContent = "Document wordt gemaakt. Dit duurt meestal 10 tot 20 seconden.";
  });

  // Profile editor: live character and line count.
  const editor = document.getElementById("profile-editor");
  const stats = document.getElementById("editor-stats");
  if (editor && stats) {
    const update = () => {
      const lines = editor.value ? editor.value.split("\n").length : 0;
      stats.textContent = `${editor.value.length} tekens · ${lines} regels`;
    };
    editor.addEventListener("input", update);
    update();
  }

  // Copy buttons: <button data-copy="#target">.
  document.querySelectorAll("[data-copy]").forEach((button) => {
    button.hidden = false;
    button.addEventListener("click", async () => {
      const target = document.querySelector(button.dataset.copy);
      if (!target) return;
      try {
        await navigator.clipboard.writeText(target.textContent.trim());
        button.textContent = "Gekopieerd";
      } catch (_) {
        button.textContent = "Kopiëren lukte niet";
      }
    });
  });

  // Live refresh for long-running tasks: instead of <meta http-equiv="refresh">
  // we poll the current page and reload once the running banner disappears.
  document.querySelectorAll("[data-auto-refresh]").forEach((banner) => {
    const interval = parseInt(banner.dataset.autoRefresh, 10) || 4000;
    const url = window.location.href;
    async function check() {
      try {
        const response = await fetch(url, { cache: "no-store" });
        if (!response.ok) throw new Error("unavailable");
        const next = new DOMParser().parseFromString(await response.text(), "text/html");
        if (!next.querySelector("[data-auto-refresh]")) {
          window.location.reload();
          return;
        }
      } catch (_) {
        // Keep trying; a transient failure should not stop the wait.
      }
      window.setTimeout(check, interval);
    }
    window.setTimeout(check, interval);
  });

  // Theme toggle on Account: persist the choice in a cookie and reload.
  const themeSelect = document.getElementById("theme-select");
  if (themeSelect) {
    themeSelect.addEventListener("change", () => {
      const value = themeSelect.value;
      if (value === "system") {
        document.cookie = "theme=; path=/; max-age=0; SameSite=Lax";
      } else {
        document.cookie = "theme=" + encodeURIComponent(value) + "; path=/; max-age=31536000; SameSite=Lax";
      }
      window.location.reload();
    });
  }

  // Close the mobile menu when tapping elsewhere or pressing Escape.
  const menu = document.querySelector(".topbar-menu");
  if (menu) {
    document.addEventListener("click", (event) => {
      if (menu.open && !menu.contains(event.target)) menu.open = false;
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && menu.open) {
        menu.open = false;
        menu.querySelector("summary").focus();
      }
    });
  }
})();
