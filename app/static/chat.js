(function () {
  const root = document.querySelector("[data-session-id]");
  if (!root) return;
  const sessionId = root.dataset.sessionId;
  const log = document.getElementById("chat-log");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  const status = document.getElementById("chat-status");

  function bubble(role, text) {
    const div = document.createElement("div");
    div.className = "msg " + role;
    div.textContent = text;
    return div;
  }

  function scrollToEnd() {
    log.scrollTop = log.scrollHeight;
  }

  function render(messages) {
    log.replaceChildren(...messages.map((message) => bubble(message.role, message.text)));
    scrollToEnd();
  }

  function typingIndicator() {
    const div = document.createElement("div");
    div.className = "msg assistant typing";
    div.setAttribute("aria-label", "De interviewer schrijft");
    div.append(document.createElement("span"), document.createElement("span"), document.createElement("span"));
    return div;
  }

  // Grow the composer with its content, up to the CSS max-height.
  function autosize() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 200) + "px";
  }

  async function load() {
    try {
      const response = await fetch(`/api/sessions/${sessionId}/messages`);
      if (!response.ok) throw new Error("gesprek niet gevonden");
      const data = await response.json();
      render(data.messages);
    } catch (error) {
      status.textContent = "Het gesprek kon niet worden geladen: " + error.message;
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text || form.dataset.busy === "1") return;
    form.dataset.busy = "1";
    input.value = "";
    autosize();
    input.disabled = true;
    status.textContent = "";
    const sent = bubble("user", text);
    log.append(sent);
    const typing = typingIndicator();
    log.append(typing);
    scrollToEnd();

    try {
      const response = await fetch(`/api/sessions/${sessionId}/send`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": document.querySelector('meta[name="csrf-token"]')?.content || "" },
        body: JSON.stringify({ text })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "versturen mislukt");
      render(data.messages);
    } catch (error) {
      typing.remove();
      sent.remove();
      status.textContent = "Je antwoord is niet verstuurd: " + error.message + ". Je tekst staat weer in het invoerveld.";
      input.value = text;
      autosize();
    } finally {
      form.dataset.busy = "0";
      input.disabled = false;
      input.focus();
    }
  });

  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  scrollToEnd();
  load();
})();
