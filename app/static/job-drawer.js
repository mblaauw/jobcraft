(function () {
  const dialog = document.getElementById("job-drawer");
  if (!dialog || typeof dialog.showModal !== "function") return;
  const body = document.getElementById("job-drawer-body");
  const title = document.getElementById("job-drawer-title");
  const notice = document.getElementById("job-drawer-notice");
  const fullPage = document.getElementById("job-drawer-full");
  const closeButton = dialog.querySelector("[data-drawer-close]");
  const listURL = window.location.href;
  const listTitle = document.title;
  let context = null;
  let opener = null;
  let controller = null;
  let version = 0;
  let pollTimer = null;
  let posting = false;
  let closing = false;
  const rowVersions = new Map();

  function clearRequest() {
    version += 1;
    if (controller) controller.abort();
    window.clearTimeout(pollTimer);
    posting = false;
  }

  function announce(text, error = false, retry = false) {
    notice.setAttribute("role", error ? "alert" : "status");
    notice.replaceChildren(document.createTextNode(text));
    if (retry) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "button small";
      button.textContent = "Details opnieuw laden";
      button.addEventListener("click", () => loadDetail(true));
      notice.append(" ", button);
    }
  }

  // Mirror a change made in the drawer onto the list row, without reordering the list.
  function syncRow(detail, jobTitle, revision) {
    if (rowVersions.get(detail.dataset.jobId) !== revision) return;
    const row = document.getElementById("job-" + detail.dataset.jobId);
    if (!row) return;
    const vote = Number(detail.dataset.jobVote);
    row.querySelectorAll(".vacancy-votes button").forEach((button, index) => {
      const direction = index === 0 ? 1 : -1;
      const selected = vote === direction;
      button.classList.toggle("selected", selected);
      button.setAttribute("aria-pressed", String(selected));
      button.value = selected ? "clear" : (direction === 1 ? "up" : "down");
      button.setAttribute("aria-label", (selected ? "Wis je stem voor: " : (direction === 1 ? "Interessant: " : "Minder passend: ")) + jobTitle);
    });
    const status = detail.dataset.jobStatus;
    const [save, hide] = row.querySelectorAll(".vacancy-save button");
    if (save && hide) {
      const saved = status === "saved";
      const hidden = status === "ignored";
      save.value = saved ? "new" : "saved";
      save.setAttribute("aria-pressed", String(saved));
      save.title = saved ? "Bewaard" : "Bewaren";
      save.setAttribute("aria-label", (saved ? "Haal uit je bewaarde vacatures: " : "Bewaar: ") + jobTitle);
      const use = save.querySelector("use");
      if (use) use.setAttribute("href", "/static/icons.svg#i-" + (saved ? "bookmark-check" : "bookmark"));
      hide.value = hidden ? "new" : "ignored";
      hide.setAttribute("aria-pressed", String(hidden));
      hide.title = hidden ? "Terugzetten" : "Negeren";
      hide.setAttribute("aria-label", (hidden ? "Zet terug: " : "Negeer: ") + jobTitle);
      save.disabled = hide.disabled = status === "applied";
      row.classList.toggle("is-dimmed", hidden);
    }
    const labels = { saved: "Bewaard", applied: "Gereageerd", ignored: "Genegeerd" };
    const heading = row.querySelector(".vacancy-main h3");
    let flags = row.querySelector(".vacancy-flags");
    if (labels[status]) {
      if (!flags) {
        flags = document.createElement("span");
        flags.className = "vacancy-flags";
        heading.append(flags);
      }
      const chip = document.createElement("span");
      chip.className = "chip" + (status === "ignored" ? "" : " chip-lime");
      chip.textContent = labels[status];
      flags.replaceChildren(chip);
    } else if (flags) {
      flags.remove();
    }
    const score = Number(detail.dataset.jobScore);
    const scoreBox = row.querySelector(".vacancy-score");
    if (scoreBox && detail.dataset.jobScore !== "" && Number.isFinite(score)) {
      scoreBox.classList.remove("high", "mid", "low");
      scoreBox.classList.add(score >= 70 ? "high" : score >= 50 ? "mid" : "low");
      scoreBox.classList.toggle("is-provisional", detail.dataset.jobProvisional === "true");
      const strong = scoreBox.querySelector("strong");
      if (strong && strong.firstChild) strong.firstChild.textContent = String(Math.round(score));
    }
  }

  function render(html, preserveScroll, focusId, revision) {
    const page = new DOMParser().parseFromString(html, "text/html");
    const detail = page.querySelector("[data-job-detail]");
    if (!detail || detail.dataset.jobId !== String(context.jobId)) {
      throw new Error("invalid detail response");
    }
    const heading = detail.querySelector("h1");
    const jobTitle = heading ? heading.textContent.trim() : "Vacaturedetails";
    const scroll = preserveScroll ? body.scrollTop : 0;
    const active = document.activeElement;
    const restoreFocus = body.contains(active) || active === document.body || active === dialog;
    const focusedId = focusId || (body.contains(active) ? active.id : "");
    // Parsed content is inert: never execute embedded scripts or a page refresh.
    detail.querySelectorAll('script, meta[http-equiv="refresh"], .detail-back').forEach((element) => element.remove());
    if (heading) heading.remove();
    title.textContent = jobTitle;
    title.title = jobTitle;
    document.title = page.title || jobTitle;
    syncRow(detail, jobTitle, revision);
    body.replaceChildren(detail);
    dialog.querySelectorAll("[data-drawer-section]").forEach((button) => {
      button.disabled = !detail.querySelector('[data-detail-section="' + button.dataset.drawerSection + '"]');
    });
    body.scrollTop = scroll;
    if (restoreFocus && focusedId) {
      const target = document.getElementById(focusedId);
      if (target && body.contains(target) && !target.disabled) {
        if (active.hasAttribute("tabindex")) target.setAttribute("tabindex", active.getAttribute("tabindex"));
        target.focus({ preventScroll: true });
      }
    }
    if (detail.dataset.documentState === "running") {
      pollTimer = window.setTimeout(() => loadDetail(true), 4000);
    }
  }

  async function loadDetail(preserveScroll = false) {
    if (!dialog.open || !context || posting) return;
    clearRequest();
    const ticket = version;
    rowVersions.set(String(context.jobId), ticket);
    controller = new AbortController();
    body.setAttribute("aria-busy", "true");
    if (!preserveScroll) announce("Vacaturedetails laden…");
    try {
      const response = await fetch(context.url, { signal: controller.signal, cache: "no-store" });
      if (!response.ok) throw new Error("detail unavailable");
      const html = await response.text();
      if (ticket !== version || !dialog.open) return;
      render(html, preserveScroll, "", ticket);
      announce("");
    } catch (error) {
      if (error.name === "AbortError" || ticket !== version || !dialog.open) return;
      announce("De vacature kon niet worden geladen. Probeer opnieuw of open de volledige pagina.", true, true);
    } finally {
      if (ticket === version) body.setAttribute("aria-busy", "false");
    }
  }

  function showDetail(state, origin) {
    clearRequest();
    context = state;
    const links = document.querySelectorAll('#job-' + state.jobId + ' [data-job-link]');
    opener = origin || links[state.openerIndex || 0] || links[0];
    document.querySelectorAll(".vacancy-row.is-open").forEach((row) => row.classList.remove("is-open"));
    const row = document.getElementById("job-" + state.jobId);
    if (row) row.classList.add("is-open");
    title.textContent = "Vacature laden…";
    title.removeAttribute("title");
    dialog.querySelectorAll("[data-drawer-section]").forEach((button) => { button.disabled = true; });
    fullPage.href = state.url;
    body.replaceChildren();
    if (!dialog.open) dialog.showModal();
    document.documentElement.classList.add("job-drawer-open");
    closeButton.focus({ preventScroll: true });
    loadDetail();
  }

  function hideDetail() {
    const previous = context;
    const previousOpener = opener;
    clearRequest();
    context = null;
    closing = false;
    if (dialog.open) dialog.close();
    document.documentElement.classList.remove("job-drawer-open");
    document.querySelectorAll(".vacancy-row.is-open").forEach((row) => row.classList.remove("is-open"));
    document.title = listTitle;
    body.replaceChildren();
    announce("");
    if (previous) {
      window.requestAnimationFrame(() => {
        if (dialog.open) return;
        if (previousOpener && previousOpener.isConnected) previousOpener.focus({ preventScroll: true });
        window.scrollTo(previous.scrollX, previous.scrollY);
      });
    }
  }

  function requestClose() {
    if (closing) return;
    const state = history.state && history.state.jobcraftDrawer;
    if (state && state.listURL === listURL) {
      closing = true;
      history.back();
    } else {
      hideDetail();
    }
  }

  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[data-job-link]");
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || link.target || link.hasAttribute("download")) return;
    const url = new URL(link.href);
    const row = link.closest(".vacancy-row");
    if (!row || url.origin !== window.location.origin) return;
    const state = { url: url.href, listURL, jobId: row.id.replace("job-", ""), openerIndex: Array.from(row.querySelectorAll("[data-job-link]")).indexOf(link), scrollX: window.scrollX, scrollY: window.scrollY };
    event.preventDefault();
    history.pushState({ ...history.state, jobcraftDrawer: state }, "", state.url);
    showDetail(state, link);
  });
  closeButton.addEventListener("click", requestClose);
  dialog.querySelectorAll("[data-drawer-section]").forEach((button) => {
    button.addEventListener("click", () => {
      const section = body.querySelector('[data-detail-section="' + button.dataset.drawerSection + '"]');
      if (!section) return;
      const top = section.getBoundingClientRect().top - body.getBoundingClientRect().top + body.scrollTop - 12;
      body.scrollTo({ top, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
      const heading = section.querySelector("h2");
      if (heading) { heading.tabIndex = -1; heading.focus({ preventScroll: true }); }
    });
  });
  dialog.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") return;
    const controls = Array.from(dialog.querySelectorAll('a[href], button:not(:disabled), select:not(:disabled), input:not(:disabled), textarea:not(:disabled), [tabindex="0"]')).filter((element) => element.getClientRects().length > 0);
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); requestClose(); });
  dialog.addEventListener("click", (event) => {
    const rect = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) requestClose();
  });
  window.addEventListener("popstate", (event) => {
    closing = false;
    const state = event.state && event.state.jobcraftDrawer;
    if (state && state.listURL === listURL) showDetail(state);
    else hideDetail();
  });
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    const state = history.state && history.state.jobcraftDrawer;
    if (state && state.listURL === listURL) showDetail(state);
    else hideDetail();
  });

  dialog.addEventListener("submit", async (event) => {
    const form = event.target;
    if (form.method.toLowerCase() !== "post") return;
    event.preventDefault();
    if (posting || !context) return;
    const data = new FormData(form);
    if (event.submitter && event.submitter.name) data.append(event.submitter.name, event.submitter.value);
    const focusId = document.activeElement ? document.activeElement.id : "";
    const requestJobId = String(context.jobId);
    clearRequest();
    posting = true;
    const ticket = version;
    rowVersions.set(requestJobId, ticket);
    const controls = Array.from(body.querySelectorAll("button, select")).filter((control) => !control.disabled);
    controls.forEach((control) => { control.disabled = true; });
    body.setAttribute("aria-busy", "true");
    announce("Je wijziging wordt verwerkt…");
    try {
      // Closing the panel must not cancel a mutation whose outcome is unknown.
      // Let it complete and reconcile only that row, never reorder the list.
      const response = await fetch(form.action, { method: "POST", body: new URLSearchParams(data) });
      if (!response.ok) throw new Error("save unavailable");
      const html = await response.text();
      if (ticket !== version || !dialog.open) {
        const page = new DOMParser().parseFromString(html, "text/html");
        const detail = page.querySelector("[data-job-detail]");
        if (detail && detail.dataset.jobId === requestJobId) syncRow(detail, page.querySelector("h1")?.textContent || "Vacature", ticket);
        return;
      }
      posting = false;
      render(html, true, focusId, ticket);
      announce("Opgeslagen. Je vacaturelijst blijft op dezelfde plek.");
    } catch (error) {
      if (error.name === "AbortError" || ticket !== version || !dialog.open) return;
      announce("Opslaan kon niet worden bevestigd. Laad de details opnieuw voordat je het nogmaals probeert.", true, true);
    } finally {
      if (ticket === version) {
        posting = false;
        body.setAttribute("aria-busy", "false");
        controls.filter((control) => control.isConnected).forEach((control) => { control.disabled = false; });
      }
    }
  });
})();
