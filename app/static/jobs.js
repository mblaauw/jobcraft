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
  const number = new Intl.NumberFormat("nl-NL");

  function show() {
    const minutes = Number(slider.value);
    output.textContent = minutes + " min";
    slider.setAttribute("aria-valuetext", minutes + " minuten");
    if (counts[minutes] !== undefined) count.textContent = number.format(counts[minutes]);
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

// Keys for going through many vacancies without the mouse: j and k move, Enter opens,
// s saves, x sets aside, / searches. Never while typing, and never under the open drawer.
(function () {
  const rows = () => Array.from(document.querySelectorAll(".vacancy-list .vacancy-row"));
  if (!rows().length) return;
  let current = -1;

  function select(index) {
    const all = rows();
    if (!all.length) return;
    current = Math.max(0, Math.min(all.length - 1, index));
    all.forEach((row, at) => row.classList.toggle("is-current", at === current));
    const row = all[current];
    row.scrollIntoView({ block: "nearest" });
    // Move focus with the highlight, so Tab continues from this vacancy.
    row.querySelector("[data-job-link]:not(.vacancy-score)")?.focus({ preventScroll: true });
  }

  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
    if (document.querySelector("dialog[open]")) return;
    const target = event.target;
    if (target.closest("input, textarea, select, [contenteditable]")) return;
    const key = event.key;
    if (key === "/") {
      const search = document.getElementById("job-search");
      if (search && search.offsetParent) {
        event.preventDefault();
        search.focus();
        search.select();
      }
      return;
    }
    if (key === "j" || key === "k") {
      event.preventDefault();
      // Start from the row that has focus, when the person tabbed into the list.
      const focused = rows().indexOf(target.closest(".vacancy-row"));
      if (focused >= 0) current = focused;
      select(current + (key === "j" ? 1 : -1));
      return;
    }
    const row = rows()[current];
    if (!row) return;
    if (key === "s" || key === "x") {
      const button = row.querySelectorAll(".vacancy-save button")[key === "s" ? 0 : 1];
      if (button && !button.disabled) {
        event.preventDefault();
        button.click();
      }
    }
  });
})();
