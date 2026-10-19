(function () {
  const listURL = window.location.href;
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
        announcement.textContent = "Wijziging wordt verwerkt…";
        announcement.classList.remove("visually-hidden");
      }
    });
  });
  // Back/forward cache can restore a form with a stale pending state.
  window.addEventListener("pageshow", () => {
    document.querySelectorAll('[aria-busy="true"]').forEach((form) => form.removeAttribute("aria-busy"));
    document.querySelectorAll(".is-saving").forEach((button) => button.classList.remove("is-saving"));
    if (announcement) {
      announcement.textContent = "";
      announcement.classList.add("visually-hidden");
    }
  });

  const progress = document.getElementById("jobs-progress");
  if (!progress || progress.dataset.matchRunning !== "true") return;
  async function checkProgress() {
    try {
      // Same URL retains all filters and also works behind a path-prefix proxy.
      const response = await fetch(listURL, { cache: "no-store" });
      if (!response.ok) throw new Error("status unavailable");
      const next = new DOMParser().parseFromString(await response.text(), "text/html");
      const nextProgress = next.getElementById("jobs-progress");
      if (!nextProgress) throw new Error("status unavailable");
      if (nextProgress.dataset.matchRunning === "true") {
        window.setTimeout(checkProgress, 4000);
        return;
      }
      progress.dataset.matchRunning = "false";
      if (nextProgress.textContent.trim()) {
        progress.replaceChildren(...Array.from(nextProgress.childNodes));
      } else {
        const banner = document.createElement("div");
        banner.className = "banner ok";
        banner.append("De beoordelingsronde is afgerond. ");
        const link = document.createElement("a");
        link.href = listURL;
        link.textContent = "Resultaten vernieuwen";
        banner.append(link);
        progress.replaceChildren(banner);
      }
      const button = document.querySelector('.jobs-matching button');
      if (button) {
        button.disabled = false;
        button.textContent = "Beoordelingen bijwerken";
      }
      const queue = document.querySelector('.jobs-matching > span');
      const nextQueue = next.querySelector('.jobs-matching > span');
      if (queue && nextQueue) queue.textContent = nextQueue.textContent;
    } catch (_) {
      const note = document.createElement("p");
      note.className = "muted small";
      note.textContent = "Automatische statuscontrole is niet beschikbaar. Gebruik ‘Resultaten vernieuwen’ om de voortgang te bekijken.";
      progress.append(note);
    }
  }
  window.setTimeout(checkProgress, 4000);
})();
