// Quick choices: keep the job count in step with what is tapped. The form works without this.
(function () {
  const form = document.getElementById("start-choices");
  const count = document.getElementById("start-count");
  const note = document.getElementById("choice-place-note");
  if (!form || !count) return;
  // Under a path prefix the proxy rewrites links in pages, but not addresses in scripts.
  const appRoot = new URL(document.querySelector('link[href*="static/app.css"]').href).pathname.split("static/")[0];
  let timer = null;
  let latest = 0;

  async function refresh() {
    const ticket = ++latest;
    try {
      const response = await fetch(appRoot + "start/keuzes/teller", {
        method: "POST",
        body: new URLSearchParams(new FormData(form)),
      });
      if (!response.ok) return;
      const data = await response.json();
      if (ticket !== latest) return;
      count.textContent = data.count;
      const typed = form.elements.place ? form.elements.place.value.trim() : "";
      if (note) note.textContent = typed && !data.place_known ? "Kies een plaats uit de lijst, dan rekenen we reistijden uit." : "";
    } catch (_) {
      // The count is a convenience; saving the form does not depend on it.
    }
  }

  function later() {
    window.clearTimeout(timer);
    timer = window.setTimeout(refresh, 350);
  }

  form.addEventListener("change", later);
  form.addEventListener("input", later);
})();
