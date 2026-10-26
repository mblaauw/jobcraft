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
    div.setAttribute("role", "status");
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

    let assistantBubble = null;
    let fullText = "";
    let streamed = false;
    let doneReceived = false;
    try {
      const response = await fetch(`/api/sessions/${sessionId}/send/stream`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": document.querySelector('meta[name="csrf-token"]')?.content || "" },
        body: JSON.stringify({ text })
      });
      if (!response.ok) {
        let detail = "versturen mislukt";
        try { detail = (await response.json()).detail || detail; } catch (_) {}
        throw new Error(detail);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop();
        for (const chunk of chunks) {
          const dataLine = chunk.split("\n").find((line) => line.startsWith("data:"));
          if (!dataLine) continue;
          let payload = null;
          try { payload = JSON.parse(dataLine.slice(5).trim()); } catch (_) { continue; }
          if (payload.type === "token") {
            if (!assistantBubble) {
              typing.remove();
              assistantBubble = bubble("assistant", "");
              assistantBubble.setAttribute("aria-live", "off");
              log.append(assistantBubble);
            }
            fullText += payload.text;
            assistantBubble.textContent = fullText;
            scrollToEnd();
            streamed = true;
          } else if (payload.type === "error") {
            throw new Error(payload.detail || "versturen mislukt");
          } else if (payload.type === "done") {
            doneReceived = true;
            if (payload.messages && payload.messages[0]) {
              if (!assistantBubble) { typing.remove(); assistantBubble = bubble("assistant", ""); log.append(assistantBubble); }
              assistantBubble.textContent = payload.messages[0].text || fullText;
            } else if (!assistantBubble) {
              typing.remove();
              assistantBubble = bubble("assistant", fullText);
              log.append(assistantBubble);
            }
            if (assistantBubble) assistantBubble.removeAttribute("aria-live");
            streamed = true;
          }
        }
      }
      if (!doneReceived) {
        throw new Error("De verbinding werd onderbroken. Probeer het opnieuw.");
      }
      if (!streamed) {
        typing.remove();
      }
    } catch (error) {
      if (assistantBubble) assistantBubble.remove();
      typing.remove();
      sent.remove();
      status.textContent = "Je antwoord is niet verstuurd: " + error.message + ". Je tekst staat weer in het invoerveld.";
      input.value = text;
      autosize();
      return;
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
