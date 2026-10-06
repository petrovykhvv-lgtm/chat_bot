(() => {
  const $messages = document.getElementById("messages");
  const $buttons = document.getElementById("buttons");
  const $form = document.getElementById("form");
  const $input = document.getElementById("input");
  const $send = document.getElementById("send");
  const $nav = document.getElementById("nav");
  const $date = document.getElementById("date");
  const $chip = document.getElementById("ai-chip");

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

  $date.textContent = new Date().toLocaleDateString("ru-RU", {
    weekday: "long", day: "numeric", month: "long",
  });

  // Режим ИИ из /api/health: «модель» или «демо». Ошибка не мешает чату.
  fetch("/api/health")
    .then((r) => r.json())
    .then((h) => {
      $chip.textContent = h.ai === "model" ? "ИИ подключён" : "ИИ: демо-режим";
      $chip.classList.toggle("is-demo", h.ai !== "model");
      $chip.hidden = false;
    })
    .catch(() => { /* индикатор необязателен */ });

  // Подсветка раздела в левой навигации.
  const NAV_ACTIONS = ["menu", "services", "faq", "lead_start", "ai_start", "feedback_start"];
  function setActive(action) {
    const key = action === "start" ? "menu" : action;
    if (!NAV_ACTIONS.includes(key)) return;
    $nav.querySelectorAll(".nav__btn").forEach((b) =>
      b.classList.toggle("is-active", b.dataset.action === key));
    store.set("nav", key);
  }

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
        setActive(b.action);
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
    $nav.querySelectorAll("button").forEach((b) => { b.disabled = busy; });
  }

  async function send(payload) {
    setBusy(true);
    const typing = addMessage("bot", "", false);
    typing.classList.add("msg--typing");
    typing.setAttribute("aria-label", "Печатает");
    const dots = document.createElement("span");
    dots.className = "dots";
    dots.append(document.createElement("i"), document.createElement("i"), document.createElement("i"));
    typing.append(dots);
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

  $nav.addEventListener("click", (e) => {
    const btn = e.target.closest(".nav__btn");
    if (!btn || btn.disabled) return;
    setActive(btn.dataset.action);
    addMessage("user", btn.dataset.title);
    send({ action: btn.dataset.action });
  });

  $form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = $input.value.trim();
    if (!text) return;
    $input.value = "";
    addMessage("user", text);
    send({ text });
  });

  // Старт: восстановить историю или запросить главное меню.
  setActive(store.get("nav") || "menu");
  if (history.length) {
    history.forEach((m) => addMessage(m.role, m.text, false));
    setButtons(lastBot.buttons);
    if (lastBot.placeholder) $input.placeholder = lastBot.placeholder;
  } else {
    send({ action: "start" });
  }
})();
