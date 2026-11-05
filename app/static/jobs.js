// Jobs list: follow a running assessment round without reloading the page.
// Pending-form handling lives in ui.js.
(function () {
  const more = document.querySelector(".filters-more");
  if (more) {
    more.hidden = false;
    more.addEventListener("click", function () {
      more.closest("form").classList.remove("filters-fold");
      more.hidden = true;
    });
  }
})();

// Reach slider: counts for every stop come with the page, so sliding answers at once;
// letting go applies the new reach to the list.
(function () {
  const slider = document.getElementById("reach-minutes");
  if (!slider) return;
  const counts = JSON.parse(slider.dataset.reachCounts || "{}");
  const count = document.querySelector("[data-reach-count]");
  const output = document.querySelector("[data-reach-minutes]");
  const hint = document.querySelector("[data-reach-hint]");
  const bars = document.querySelectorAll(".reach-bars span");
  const number = new Intl.NumberFormat("nl-NL");

  function show() {
    const minutes = Number(slider.value);
    output.textContent = minutes + " min";
    slider.setAttribute("aria-valuetext", minutes + " minuten");
    if (counts[minutes] !== undefined) count.textContent = number.format(counts[minutes]);
    bars.forEach((bar) => bar.classList.toggle("is-in", Number(bar.dataset.minutes) <= minutes));
    const next = minutes + 15;
    if (counts[next] === undefined) {
      hint.textContent = "Dit is de ruimste stand.";
      return;
    }
    const gain = counts[next] - counts[minutes];
    hint.textContent = gain <= 0
      ? "Met 15 minuten meer komen er geen vacatures bij."
      : "Met 15 minuten meer " + (gain === 1 ? "komt er 1 vacature" : "komen er " + number.format(gain) + " vacatures") + " bij.";
  }

  slider.addEventListener("input", show);
  slider.addEventListener("change", () => slider.form.requestSubmit());
})();

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
