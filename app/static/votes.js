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
        const row = form.closest(".vacancy-row");
        row?.querySelector(".row-alert")?.remove();
        form.querySelectorAll("button").forEach((item) => { item.disabled = true; });
        try {
          // Asked as a script: an expired session then answers 401 instead of a redirect to
          // the login page, which looks exactly like the redirect after a saved vote.
          const response = await fetch(form.action, {
            method: "POST", body, redirect: "manual", headers: { Accept: "application/json" },
          });
          if (response.status === 401) throw new Error("signed out");
          if (!response.ok && response.type !== "opaqueredirect") throw new Error("vote failed");
          refresh(form, button.value);
          if (status) status.textContent = "Je stem is opgeslagen.";
        } catch (error) {
          // Seen, not only announced: a lost vote silently steers the list the wrong way.
          const alert = document.createElement("p");
          alert.className = "row-alert";
          alert.setAttribute("role", "alert");
          if (error.message === "signed out") {
            const link = document.createElement("a");
            // Relative to where the app is served, which may be under a path prefix.
            const appRoot = new URL(document.querySelector('link[href*="static/app.css"]').href).pathname.split("static/")[0];
            link.href = appRoot + "login?next=" + encodeURIComponent(location.pathname + location.search);
            link.textContent = "Log opnieuw in";
            alert.append("Je bent uitgelogd, dus je stem is niet opgeslagen. ", link, ".");
          } else {
            alert.textContent = "Je stem is niet opgeslagen. Probeer het opnieuw.";
          }
          (row?.querySelector(".vacancy-main") || form).append(alert);
          if (status) status.textContent = "";
        } finally {
          form.querySelectorAll("button").forEach((item) => { item.disabled = false; });
        }
      },
      true
    );
  });
})();
