// Vacancy list: a vote updates its row in place, so the list keeps its position.
// Without this script the forms still work through a normal page load.
(function () {
  function refresh(form, value) {
    const title = form.closest(".vacancy-row")?.querySelector("h3 a")?.textContent.trim() || "";
    const buttons = {
      up: form.querySelector('.vote-choice:not(.is-down)'),
      down: form.querySelector(".vote-choice.is-down"),
    };
    const labels = {
      up: ["Interessant:", "Wis je positieve stem voor"],
      down: ["Minder passend:", "Wis je negatieve stem voor"],
    };
    for (const side of ["up", "down"]) {
      const chosen = value === side;
      buttons[side].classList.toggle("selected", chosen);
      buttons[side].setAttribute("aria-pressed", chosen ? "true" : "false");
      buttons[side].value = chosen ? "clear" : side;
      buttons[side].setAttribute("aria-label", `${labels[side][chosen ? 1 : 0]} ${title}`);
    }
  }

  document.querySelectorAll(".vacancy-votes").forEach((form) => {
    form.addEventListener(
      "submit",
      async (event) => {
        const button = event.submitter;
        if (!button) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        const body = new URLSearchParams(new FormData(form));
        body.set("vote", button.value);
        const status = document.getElementById("jobs-action-status");
        form.querySelectorAll("button").forEach((item) => { item.disabled = true; });
        try {
          const response = await fetch(form.action, { method: "POST", body, redirect: "manual" });
          if (!response.ok && response.type !== "opaqueredirect") throw new Error("vote failed");
          refresh(form, button.value);
          if (status) status.textContent = "Je stem is opgeslagen.";
        } catch (_) {
          if (status) status.textContent = "Je stem is niet opgeslagen. Probeer het opnieuw.";
        } finally {
          form.querySelectorAll("button").forEach((item) => { item.disabled = false; });
        }
      },
      true
    );
  });
})();
