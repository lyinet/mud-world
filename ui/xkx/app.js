(() => {
  "use strict";

  const TICKS_PER_DAY = 144;
  const TICKS_PER_HOUR = 6;

  const logEl = document.getElementById("log");
  const formEl = document.getElementById("cmd-form");
  const inputEl = document.getElementById("cmd");
  const mapEl = document.getElementById("map");
  const blockedEl = document.getElementById("blocked");
  const npcsEl = document.getElementById("npcs");
  const goodsEl = document.getElementById("goods");
  const invEl = document.getElementById("inventory");
  const quickEl = document.getElementById("quick");
  const pillName = document.getElementById("pill-name");
  const pillPlace = document.getElementById("pill-place");
  const pillStamina = document.getElementById("pill-stamina");
  const pillMoney = document.getElementById("pill-money");
  const pillTime = document.getElementById("pill-time");

  const MAP_SLOTS = [
    { direction: "northwest", label: "西北", empty: true },
    { direction: "north", label: "北", empty: true },
    { direction: "northeast", label: "东北", empty: true },
    { direction: "west", label: "西", empty: true },
    { direction: "center", label: "此间", empty: false },
    { direction: "east", label: "东", empty: true },
    { direction: "southwest", label: "西南", empty: true },
    { direction: "south", label: "南", empty: true },
    { direction: "southeast", label: "东南", empty: true },
  ];

  const EXTRA_DIRECTIONS = ["up", "down", ""];
  const DIRECTION_LABELS = {
    north: "北", northeast: "东北", east: "东", southeast: "东南",
    south: "南", southwest: "西南", west: "西", northwest: "西北",
    up: "上", down: "下",
  };

  let history = [];
  let historyAt = 0;
  let busy = false;
  let requestSeq = 0;

  function accountId() {
    const key = "xkx.account";
    let value = "";
    try {
      value = localStorage.getItem(key) || "";
    } catch (_) {
      value = "";
    }
    if (!value) {
      const rand = Math.random().toString(36).slice(2, 8);
      value = `xkx-${rand}`;
      try {
        localStorage.setItem(key, value);
      } catch (_) {
        // 本地存储不可用时，至少保证本次页面能玩。
      }
    }
    return value;
  }

  const ACCOUNT = accountId();

  function scrollBottom() {
    logEl.scrollTop = logEl.scrollHeight;
  }

  function addLine(kind, text) {
    const div = document.createElement("div");
    div.className = `line ${kind || "info"}`;
    div.textContent = text;
    logEl.appendChild(div);
    scrollBottom();
  }

  function setStatus(view) {
    if (!view) return;
    const p = view.player || {};
    pillName.textContent = `侠名：${p.name || "—"}`;
    pillPlace.textContent = `所在：${p.location_name || "—"}`;
    const stamina = p.stamina == null ? "—" : `${Math.round(Number(p.stamina))}/${Math.round(Number(p.max_stamina || 0))}`;
    pillStamina.textContent = `体力：${stamina}`;
    pillMoney.textContent = `银两：${p.money == null ? "—" : p.money}文`;
    const tick = Number(view.tick || 0);
    const day = Math.floor(tick / TICKS_PER_DAY) + 1;
    const hour = Math.floor((tick % TICKS_PER_DAY) / TICKS_PER_HOUR);
    const minute = (tick % TICKS_PER_HOUR) * 10;
    pillTime.textContent = `世界：第${day}天 ${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
  }

  function makeButton(className, label, onClick, disabled, title) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = className;
    btn.textContent = label;
    if (disabled) btn.disabled = true;
    if (title) btn.title = title;
    if (!disabled && onClick) btn.addEventListener("click", onClick);
    return btn;
  }

  function renderMap(view) {
    mapEl.innerHTML = "";
    blockedEl.textContent = "";
    if (!view) return;
    const exits = Array.isArray(view.exits) ? view.exits : [];
    const byDirection = new Map();
    const extras = [];
    exits.forEach((exit) => {
      if (exit.direction && MAP_SLOTS.some((s) => s.direction === exit.direction)) {
        byDirection.set(exit.direction, exit);
      } else {
        extras.push(exit);
      }
    });

    MAP_SLOTS.forEach((slot) => {
      const cell = document.createElement("div");
      cell.className = "cell";
      const dir = document.createElement("span");
      dir.className = "dir";
      dir.textContent = slot.label;
      cell.appendChild(dir);
      if (slot.direction === "center") {
        cell.classList.add("center");
        const place = document.createElement("span");
        place.className = "place";
        place.textContent = (view.room && view.room.name) || "此间";
        cell.appendChild(place);
        mapEl.appendChild(cell);
        return;
      }
      const exit = byDirection.get(slot.direction);
      if (!exit) {
        cell.classList.add("empty");
        mapEl.appendChild(cell);
        return;
      }
      const btn = makeButton(
        "cell",
        "",
        () => sendCommand(`go ${exit.direction || exit.name}`),
        !exit.available,
        exit.reason || exit.name
      );
      const place = document.createElement("span");
      place.className = "place";
      place.textContent = exit.name;
      btn.appendChild(place);
      if (!exit.available && exit.reason) {
        const why = document.createElement("span");
        why.className = "why";
        why.textContent = exit.reason;
        btn.appendChild(why);
      }
      mapEl.appendChild(btn);
    });

    if (extras.length) {
      extras.forEach((exit) => {
        const row = makeButton(
          "row",
          "",
          () => sendCommand(`go ${exit.direction || exit.name}`),
          !exit.available,
          exit.reason || exit.name
        );
        const name = document.createElement("span");
        name.className = "name";
        name.textContent = `${DIRECTION_LABELS[exit.direction] || exit.direction_label || "路"} · ${exit.name}`;
        const meta = document.createElement("span");
        meta.className = "meta";
        meta.textContent = exit.available ? "可走" : "未开放";
        row.appendChild(name);
        row.appendChild(meta);
        blockedEl.appendChild(row);
      });
    }

    const blocked = exits.filter((e) => !e.available);
    if (blocked.length) {
      const title = document.createElement("div");
      title.textContent = "未开放出口：";
      blockedEl.appendChild(title);
      blocked.forEach((e) => {
        const row = document.createElement("div");
        row.textContent = `${e.direction_label || "路"}→${e.name}：${e.reason}`;
        blockedEl.appendChild(row);
      });
    }
  }

  function renderList(container, rows, build, emptyText) {
    container.innerHTML = "";
    container.classList.toggle("empty", !rows.length);
    if (!rows.length) {
      container.textContent = emptyText;
      return;
    }
    rows.forEach((row) => {
      const el = build(row);
      if (el) container.appendChild(el);
    });
  }

  function renderSide(view) {
    if (!view) return;
    const npcs = Array.isArray(view.npcs) ? view.npcs : [];
    renderList(npcsEl, npcs, (npc) => {
      const row = makeButton("row", "", () => {
        inputEl.value = `ask ${npc.name} `;
        inputEl.focus();
      });
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = npc.name;
      const meta = document.createElement("span");
      meta.className = "meta";
      meta.textContent = npc.role || "人";
      row.appendChild(name);
      row.appendChild(meta);
      return row;
    }, "这里没有人。");

    const goods = Array.isArray(view.goods) ? view.goods : [];
    renderList(goodsEl, goods, (item) => {
      const row = makeButton("row", "", () => {
        inputEl.value = `buy ${item.name} `;
        inputEl.focus();
      }, false, item.available ? "" : "售罄");
      row.classList.add("row");
      if (!item.available) row.classList.add("sold");
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = item.name;
      const meta = document.createElement("span");
      meta.className = "meta";
      meta.textContent = item.available
        ? `${item.total}文 · 存${item.stock}`
        : "售罄";
      row.appendChild(name);
      row.appendChild(meta);
      return row;
    }, "此处没有货架。");

    const inv = Array.isArray(view.inventory) ? view.inventory : [];
    renderList(invEl, inv, (item) => {
      const row = makeButton("row", "", () => {
        inputEl.value = `sell ${item.name} `;
        inputEl.focus();
      });
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = item.name;
      const meta = document.createElement("span");
      meta.className = "meta";
      meta.textContent = `×${item.qty}`;
      row.appendChild(name);
      row.appendChild(meta);
      return row;
    }, "行囊空空。");
  }

  function renderQuick(view) {
    quickEl.innerHTML = "";
    const buttons = [
      ["看", "look"],
      ["状态", "score"],
      ["行囊", "inventory"],
      ["帮助", "help"],
      ["打", "fight"],
      ["捡", "loot"],
    ];
    buttons.forEach(([label, command]) => {
      quickEl.appendChild(makeButton("", label, () => sendCommand(command)));
    });
  }

  function renderView(view) {
    if (!view) return;
    setStatus(view);
    renderMap(view);
    renderSide(view);
    renderQuick(view);
  }

  async function postJson(path, body) {
    const resp = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    let payload = null;
    try {
      payload = await resp.json();
    } catch (_) {
      payload = null;
    }
    if (!resp.ok) {
      return {
        ok: false,
        lines: [{
          kind: "error",
          text: (payload && payload.reason_text) || `服务返回 ${resp.status}。`,
        }],
      };
    }
    return payload || {
      ok: false,
      lines: [{ kind: "error", text: "服务没有返回内容。" }],
    };
  }

  function nextRequestId() {
    requestSeq += 1;
    return `${Date.now().toString(36)}-${requestSeq}`;
  }

  async function sendCommand(rawCommand, options = {}) {
    if (busy) return;
    const command = String(rawCommand || "").trim();
    if (!command) {
      inputEl.focus();
      return;
    }
    busy = true;
    if (!options.silentEcho) addLine("cmd", `› ${command}`);
    history.push(command);
    if (history.length > 80) history.shift();
    historyAt = history.length;
    inputEl.value = "";

    let result;
    try {
      result = await postJson("/api/xkx/command", {
        account_id: ACCOUNT,
        command,
        request_id: nextRequestId(),
      });
    } catch (err) {
      result = {
        ok: false,
        lines: [{
          kind: "system",
          text: `连不上玩家服务；确认服务已启动。${err && err.message ? `（${err.message}）` : ""}`,
        }],
      };
    }

    const lines = Array.isArray(result.lines) ? result.lines : [];
    if (lines.length) {
      lines.forEach((line) => addLine(line.kind || "info", line.text || ""));
    } else if (result.ok) {
      addLine("info", "完成了。");
    } else {
      addLine("error", result.reason_text || "命令没有成功。");
    }
    renderView(result.view);
    busy = false;
    inputEl.focus();
  }

  formEl.addEventListener("submit", (event) => {
    event.preventDefault();
    const command = inputEl.value.trim();
    if (!command) {
      inputEl.focus();
      return;
    }
    sendCommand(command);
  });

  inputEl.addEventListener("keydown", (event) => {
    if (event.key === "ArrowUp") {
      if (history.length) {
        event.preventDefault();
        historyAt = Math.max(0, historyAt - 1);
        inputEl.value = history[historyAt] || "";
        inputEl.setSelectionRange(inputEl.value.length, inputEl.value.length);
      }
    } else if (event.key === "ArrowDown") {
      if (history.length) {
        event.preventDefault();
        historyAt = Math.min(history.length, historyAt + 1);
        inputEl.value = historyAt >= history.length ? "" : history[historyAt];
      }
    }
  });

  // 不在输入框里时，直接敲字母就把焦点送回命令行，保持文字 MUD 手感。
  document.addEventListener("keydown", (event) => {
    if (document.activeElement === inputEl) return;
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key.length === 1) {
      inputEl.focus();
    }
  });

  // 页面停留时刷新活跃时钟；断线由下一次命令自动重新进入。
  window.setInterval(() => {
    fetch("/api/player/heartbeat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_id: ACCOUNT, keepalive: true }),
    }).catch(() => {});
  }, 20000);

  addLine("system", "你推门走进江湖。输入 help 看命令；方向可直接打 n/s/e/w，也可点右侧舆图。");
  sendCommand("look", { silentEcho: true });
})();
