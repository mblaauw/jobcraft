(function () {
  const root = document.querySelector("[data-session-id]");
  if (!root) return;
  const sessionId = root.dataset.sessionId;
  const log = document.getElementById("chat-log");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  const status = document.getElementById("chat-status");

  function render(messages) {
    log.innerHTML = "";
    for (const message of messages) {
      const div = document.createElement("div");
      div.className = "msg " + message.role;
      div.textContent = message.text;
      log.appendChild(div);
    }
    log.scrollTop = log.scrollHeight;
  }

  async function load() {
    try {
      const response = await fetch(`/api/sessions/${sessionId}/messages`);
      if (!response.ok) throw new Error("gesprek niet gevonden");
      const data = await response.json();
      render(data.messages);
    } catch (error) {
      status.textContent = "Kon gesprek niet laden: " + error.message;
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text || form.dataset.busy === "1") return;
    form.dataset.busy = "1";
    input.value = "";
    input.disabled = true;
    status.textContent = "Interviewer denkt…";

    const optimistic = document.createElement("div");
    optimistic.className = "msg user";
    optimistic.textContent = text;
    log.appendChild(optimistic);
    log.scrollTop = log.scrollHeight;

    try {
      const response = await fetch(`/api/sessions/${sessionId}/send`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": document.querySelector('meta[name="csrf-token"]')?.content || "" },
        body: JSON.stringify({ text })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "versturen mislukt");
      render(data.messages);
      status.textContent = "";
    } catch (error) {
      status.textContent = "Fout: " + error.message;
    } finally {
      form.dataset.busy = "0";
      input.disabled = false;
      input.focus();
    }
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  load();
})();
