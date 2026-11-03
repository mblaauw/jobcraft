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

  // Under a path prefix the proxy rewrites links in pages, but not addresses in JSON.
  const appRoot = new URL(document.querySelector('link[href*="static/app.css"]').href).pathname.split("static/")[0];

  // Parts that improve once the AI is done (a tidy vacancy text, an assessment) carry
  // data-swap and a hidden link to their own address; swap the ready version in place.
  // The job drawer calls this again after it renders a vacancy.
  function watchSwaps() {
    document.querySelectorAll("[data-swap]:not([data-swap-watched])").forEach((part) => {
      part.setAttribute("data-swap-watched", "");
      let tries = 0;
      const check = async () => {
        if (!part.isConnected || tries++ > 12) return;
        try {
          const response = await fetch(part.querySelector("[data-swap-link]").href, { cache: "no-store" });
          if (response.ok) {
            const page = new DOMParser().parseFromString(await response.text(), "text/html");
            const next = page.getElementById(part.id);
            if (next && !next.hasAttribute("data-swap")) { part.replaceWith(next); return; }
          }
        } catch (error) { /* What is on screen stays readable. */ }
        setTimeout(check, 5000);
      };
      setTimeout(check, 5000);
    });
  }
  window.jobcraftWatchSwaps = watchSwaps;
  watchSwaps();

  // Work in the background: visible on every page, announced when it is done.
  const strips = document.querySelectorAll("[data-work-strip]");
  if (strips.length && document.querySelector("[data-work-running]")) {
    const poll = async () => {
      try {
        const response = await fetch(appRoot + "api/werk");
        if (response.ok) {
          const work = await response.json();
          strips.forEach((strip) => {
            const items = [...work.running.map((item) => [item, true]), ...work.done.map((item) => [item, false])];
            strip.replaceChildren(...items.map(([item, running]) => {
              const link = document.createElement("a");
              link.className = "work-item " + (running ? "is-running" : "is-done");
              link.href = appRoot + item.href.slice(1);
              const text = document.createElement("span");
              const strong = document.createElement("strong");
              strong.textContent = item.what;
              const title = document.createElement("span");
              title.className = "work-for";
              title.textContent = item.title;
              text.append(strong, " " + item.verb, title);
              if (running) {
                const dot = document.createElement("span");
                dot.className = "work-dot";
                link.append(dot);
              }
              link.append(text);
              return link;
            }));
          });
          if (!work.running.length) return;
        }
      } catch (error) { /* Try again at the next tick. */ }
      setTimeout(poll, 8000);
    };
    setTimeout(poll, 8000);
  }

  // Document generation: one click, clear feedback.
  document.addEventListener("submit", (event) => {
    const form = event.target.closest("form[data-doc-form]");
    if (!form || form.closest("dialog")) return;
    document.querySelectorAll("form[data-doc-form] button").forEach((button) => { button.disabled = true; });
    const status = document.getElementById("doc-status");
    if (status) status.textContent = "Document wordt gemaakt. Dit duurt meestal 10 tot 20 seconden.";
  });

  // Old links into the profile point at parts that now live on another tab.
  if (document.querySelector("[data-profile-tabs]") && location.hash.length > 1 && !document.getElementById(location.hash.slice(1))) {
    const tab = { "#positie": "positie", "#reisbereik": "zoek", "#voorkeuren": "zoek", "#overzicht": "cv" }[location.hash] || "notities";
    if (new URLSearchParams(location.search).get("tab") !== tab) location.replace("?tab=" + tab + location.hash);
  }

  // Text boxes that grow with what is in them, up to most of the screen.
  const grow = (box) => {
    box.style.height = "auto";
    box.style.height = Math.min(box.scrollHeight + 2, window.innerHeight * 0.75) + "px";
  };
  document.querySelectorAll("textarea[data-autogrow]").forEach((box) => {
    box.addEventListener("input", () => grow(box));
    const details = box.closest("details");
    if (details) details.addEventListener("toggle", () => { if (details.open) grow(box); });
    if (box.offsetParent) grow(box);
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
  // we poll the current page once and reload when nothing on it is running any more.
  const waiting = document.querySelector("[data-auto-refresh]");
  if (waiting) {
    const interval = parseInt(waiting.dataset.autoRefresh, 10) || 4000;
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
  }

  // How long running work has been going, so a wait never looks like a hang.
  // Also started by the job drawer, whose waits arrive after the page has loaded.
  let clockTimer = null;
  function startClocks() {
    if (clockTimer || !document.querySelector("[data-elapsed]")) return;
    const opened = Date.now();
    const tick = () => {
      document.querySelectorAll("[data-elapsed]").forEach((clock) => {
        const since = Date.parse(clock.dataset.elapsed);
        const seconds = Math.max(0, Math.round((Date.now() - (Number.isNaN(since) ? opened : since)) / 1000));
        const minutes = Math.floor(seconds / 60);
        clock.textContent = "Bezig: " + (minutes ? minutes + " min " : "") + (seconds % 60) + " s.";
      });
    };
    tick();
    clockTimer = window.setInterval(tick, 1000);
  }
  window.jobcraftStartClocks = startClocks;
  startClocks();

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
