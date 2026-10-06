(() => {
  const $messages = document.getElementById("messages");
  const $buttons = document.getElementById("buttons");
  const $form = document.getElementById("form");
  const $input = document.getElementById("input");
  const $send = document.getElementById("send");

  const store = {
    get(k) { try { return sessionStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { sessionStorage.setItem(k, v); } catch { /* storage недоступен */ } },
  };

  let sessionId = store.get("sid");
  if (!sessionId) {
    sessionId = crypto.randomUUID();
    store.set("sid", sessionId);
  }

  // История сообщений хранится в sessionStorage, чтобы пережить перезагрузку страницы.
  let history = [];
  let lastBot = { buttons: [], placeholder: "" };
  try {
    const saved = JSON.parse(store.get("chat") || "null");
    if (saved) { history = saved.history; lastBot = saved.lastBot; }
  } catch { /* повреждённая история игнорируется */ }

  function persist() {
    store.set("chat", JSON.stringify({ history, lastBot }));
  }

  // Безопасный рендер: только текст и **жирный**, без innerHTML.
  function renderText(el, text) {
    text.split(/(\*\*[^*]+\*\*)/g).forEach((part) => {
      if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
        const b = document.createElement("strong");
        b.textContent = part.slice(2, -2);
        el.append(b);
      } else {
        el.append(document.createTextNode(part));
      }
    });
  }

  function addMessage(role, text, save = true) {
    const el = document.createElement("div");
    el.className = `msg msg--${role}`;
    renderText(el, text);
    $messages.append(el);
    $messages.scrollTop = $messages.scrollHeight;
    if (save) { history.push({ role, text }); persist(); }
    return el;
  }

  function setButtons(buttons) {
    $buttons.replaceChildren();
    buttons.forEach((b) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = b.label;
      btn.addEventListener("click", () => {
        addMessage("user", b.label);
        send({ action: b.action });
      });
      $buttons.append(btn);
    });
  }

  function setBusy(busy) {
    $send.disabled = busy;
    $input.disabled = busy;
    $buttons.querySelectorAll("button").forEach((b) => { b.disabled = busy; });
  }

  async function send(payload) {
    setBusy(true);
    const typing = addMessage("bot", "Печатает…", false);
    typing.classList.add("msg--typing");
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, ...payload }),
      });
      if (!res.ok) throw new Error(String(res.status));
      const data = await res.json();
      typing.remove();
      data.messages.forEach((m) => addMessage("bot", m));
      lastBot = { buttons: data.buttons, placeholder: data.placeholder };
      setButtons(data.buttons);
      $input.placeholder = data.placeholder;
      persist();
    } catch {
      typing.remove();
      addMessage("bot", "Не удалось связаться с сервером. Попробуйте ещё раз.", false);
    } finally {
      setBusy(false);
      $input.focus();
    }
  }

  $form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = $input.value.trim();
    if (!text) return;
    $input.value = "";
    addMessage("user", text);
    send({ text });
  });

  // Старт: восстановить историю или запросить главное меню.
  if (history.length) {
    history.forEach((m) => addMessage(m.role, m.text, false));
    setButtons(lastBot.buttons);
    if (lastBot.placeholder) $input.placeholder = lastBot.placeholder;
  } else {
    send({ action: "start" });
  }
})();
