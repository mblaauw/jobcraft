// Jobs list: follow a running assessment round without reloading the page.
// Pending-form handling lives in ui.js.
(function () {
  const listURL = window.location.href;
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
        banner.append("De beoordeling is klaar. ");
        const link = document.createElement("a");
        link.href = listURL;
        link.textContent = "Toon de nieuwe volgorde";
        banner.append(link);
        progress.replaceChildren(banner);
      }
      const current = document.querySelector(".jobs-matching");
      const fresh = next.querySelector(".jobs-matching");
      if (current && fresh) current.replaceWith(fresh);
    } catch (_) {
      const note = document.createElement("p");
      note.className = "muted small";
      note.textContent = "De voortgang kan niet automatisch worden gevolgd. Vernieuw de pagina om het resultaat te zien.";
      progress.append(note);
    }
  }
  window.setTimeout(checkProgress, 4000);
})();
