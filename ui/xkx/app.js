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
  const pillHp = document.getElementById("pill-hp");
  const pillStamina = document.getElementById("pill-stamina");
  const pillMoney = document.getElementById("pill-money");
  const pillTime = document.getElementById("pill-time");
  const worldStatsEl = document.getElementById("world-stats");
  const characterEl = document.getElementById("character");
  const skillsEl = document.getElementById("skills");
  const equipmentEl = document.getElementById("equipment");
  const equipNoteEl = document.getElementById("equip-note");
  const questsEl = document.getElementById("quests");
  const groundEl = document.getElementById("ground");
  const mapMetaEl = document.getElementById("map-meta");
  const completeEl = document.getElementById("complete");

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
    northup: "北上行", southdown: "南下行", northdown: "北下行", southup: "南上行",
    eastup: "东上行", westup: "西上行", eastdown: "东下行", westdown: "西下行",
    up: "上", down: "下", out: "外", enter: "内",
  };

  let history = [];
  let historyAt = 0;
  let busy = false;
  let requestSeq = 0;
  let helpCatalog = null;
  let completeRows = [];
  let completeAt = -1;

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
    const vitals = p.vitals;
    pillHp.textContent = vitals && vitals.hp != null
      ? `气血：${vitals.hp}/${vitals.max_hp}`
      : "气血：未开放";
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
      } else if (exit.available) {
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
          false,
          exit.name
        );
        const name = document.createElement("span");
        name.className = "name";
        name.textContent = `${DIRECTION_LABELS[exit.direction] || exit.direction_label || "路"} · ${exit.name}`;
        const meta = document.createElement("span");
        meta.className = "meta";
        meta.textContent = "可走";
        row.appendChild(name);
        row.appendChild(meta);
        blockedEl.appendChild(row);
      });
    }

    // 不可走的出口不消失：九宫格放不下的方向（上下/斜上行等）也在这里写明原因。
    const blocked = exits.filter((e) => !e.available);
    if (blocked.length) {
      const title = document.createElement("div");
      title.className = "blocked-title";
      title.textContent = "未开放出口：";
      blockedEl.appendChild(title);
      blocked.forEach((e) => {
        const row = document.createElement("div");
        row.className = "blocked-row";
        row.textContent = `${DIRECTION_LABELS[e.direction] || e.direction_label || "路"}→${e.name}：${e.reason || "未开放"}`;
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
      meta.textContent = npc.role_label || npc.role || "人";
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

  }

  function renderQuick(view) {
    quickEl.innerHTML = "";
    const ready = (view && view.combat_ready) || {};
    const reasons = (view && view.combat_reason) || {};
    const npcs = (view && view.npcs) || [];
    // 场上只有一个人时，“打”直接带上名字；多个人则让玩家自己指定。
    const fightCommand = ready.fight && npcs.length === 1 ? `fight ${npcs[0].name}` : "fight";
    const buttons = [
      ["看", "look"],
      ["状态", "score"],
      ["行囊", "inventory"],
      ["帮助", "help"],
      [ready.fight ? "打" : "打（暂不可用）", fightCommand, ready.fight, reasons.fight],
      [ready.loot ? "捡" : "捡（暂不可用）", "loot", ready.loot, reasons.loot],
    ];
    buttons.forEach(([label, command, enabled, reason]) => {
      const btn = makeButton(
        enabled === false ? "quick-blocked" : "",
        label,
        () => sendCommand(command),
        false,
        reason || "",
      );
      quickEl.appendChild(btn);
    });
  }

  function renderWorld(view) {
    if (!worldStatsEl) return;
    const w = (view && view.world) || {};
    if (w.rooms_in_slice == null) {
      worldStatsEl.textContent = "";
      return;
    }
    const boundary = w.rooms_boundary ? `（另有 ${w.rooms_boundary} 间边界房未开放）` : "";
    worldStatsEl.textContent =
      `世界：${w.rooms_in_slice} 间房 · ${w.npcs_in_slice} 位 NPC · ` +
      `${w.exits_walkable} 条可走方向（通往 ${w.exits_walkable_targets} 间房） · ` +
      `${w.exits_blocked} 条未开放出口${boundary}`;
  }

  function renderNote(container, text, warn) {
    container.innerHTML = "";
    const div = document.createElement("div");
    div.className = `note${warn ? " warn" : ""}`;
    div.textContent = text;
    container.appendChild(div);
  }

  function statRow(label, value, reason) {
    const row = document.createElement("div");
    row.className = "stat";
    const key = document.createElement("span");
    key.className = "label";
    key.textContent = label;
    const val = document.createElement("span");
    val.className = "value";
    val.textContent = value;
    row.appendChild(key);
    row.appendChild(val);
    if (reason) {
      const why = document.createElement("div");
      why.className = "reason";
      why.textContent = reason;
      row.appendChild(why);
    }
    return row;
  }

  function renderCharacter(player) {
    characterEl.innerHTML = "";
    const p = player || {};
    const v = p.vitals || {};
    if (v.hp != null && v.max_hp != null) {
      const row = statRow("气血", `${v.hp} / ${v.max_hp}`);
      const bar = document.createElement("div");
      bar.className = "bar";
      const fill = document.createElement("div");
      fill.className = "bar-fill";
      // 百分比由后端 vitals.hp_pct 提供，前端只渲染，不计算。
      fill.style.width = `${Number(v.hp_pct == null ? 0 : v.hp_pct)}%`;
      bar.appendChild(fill);
      row.appendChild(bar);
      characterEl.appendChild(row);
    } else {
      characterEl.appendChild(statRow("气血", "未开放", v.hp_reason || "后端未提供气血字段。"));
    }
    characterEl.appendChild(
      statRow("内力", "未开放", v.neili_reason || "后端未提供内力字段。")
    );
    characterEl.appendChild(statRow("经验", p.combat_exp == null ? "—" : String(p.combat_exp)));

    const attrs = Array.isArray(p.attrs_detail) ? p.attrs_detail : [];
    if (attrs.length) {
      const grid = document.createElement("div");
      grid.className = "attrs";
      attrs.forEach((item) => {
        const cell = document.createElement("div");
        cell.className = "attr";
        const label = document.createElement("span");
        label.textContent = item.label || item.key || "—";
        const value = document.createElement("b");
        value.textContent = item.value == null ? "—" : String(item.value);
        cell.appendChild(label);
        cell.appendChild(value);
        grid.appendChild(cell);
      });
      characterEl.appendChild(grid);
      if (p.attrs_reason) {
        const why = document.createElement("div");
        why.className = "reason";
        why.textContent = p.attrs_reason;
        characterEl.appendChild(why);
      }
    } else {
      characterEl.appendChild(
        statRow("属性", "未开放", p.attrs_reason || "后端未提供属性字段。")
      );
    }

    const money = p.wallet && p.wallet.money != null ? p.wallet.money : null;
    characterEl.appendChild(statRow("金钱", money == null ? "—" : `${money} 文`));
    const weight = p.weight || {};
    const weightValue = weight.current == null
      ? "—"
      : weight.max == null
        ? `${weight.current}（上限未开放）`
        : `${weight.current} / ${weight.max}`;
    characterEl.appendChild(statRow("负重", weightValue, weight.reason || ""));
  }

  function renderSkills(player) {
    const p = player || {};
    const engine = Array.isArray(p.skills_engine) ? p.skills_engine : [];
    const legacy = Array.isArray(p.skills_detail) ? p.skills_detail : [];
    if (engine.length) {
      renderList(skillsEl, engine, (row) => {
        const el = document.createElement("div");
        el.className = "row";
        const name = document.createElement("span");
        name.className = "name";
        name.textContent = row.name;
        const meta = document.createElement("span");
        meta.className = "meta";
        meta.textContent = `${row.level} 级${row.xp ? ` · ${row.xp} 经验` : ""}`;
        el.appendChild(name);
        el.appendChild(meta);
        return el;
      }, "暂无可显示的技能。");
      return;
    }
    if (legacy.length) {
      renderList(skillsEl, legacy, (row) => {
        const el = document.createElement("div");
        el.className = "row";
        const name = document.createElement("span");
        name.className = "name";
        name.textContent = row.name;
        const meta = document.createElement("span");
        meta.className = "meta";
        meta.textContent = row.level == null ? "—" : `${row.level} 级`;
        if (row.description) el.title = row.description;
        el.appendChild(name);
        el.appendChild(meta);
        return el;
      }, "暂无可显示的技能。");
      return;
    }
    const status = p.skills_status || {};
    renderNote(
      skillsEl,
      `暂不可用：${status.reason || "后端未提供 skills/practice 字段。"}`,
      true
    );
  }

  function renderEquipment(player) {
    equipmentEl.innerHTML = "";
    const eq = (player && player.equipment) || {};
    const slots = Array.isArray(eq.slots) ? eq.slots : [];
    if (slots.length) {
      slots.forEach((slot) => {
        const cell = document.createElement("div");
        cell.className = `slot${slot.empty ? " empty" : ""}`;
        const name = document.createElement("span");
        name.className = "slot-name";
        name.textContent = slot.slot_name || slot.slot || "—";
        const item = document.createElement("span");
        item.className = "slot-item";
        item.textContent = slot.empty ? "空" : (slot.name || slot.item || "—");
        cell.appendChild(name);
        cell.appendChild(item);
        if (!slot.empty && slot.category) {
          const meta = document.createElement("span");
          meta.className = "slot-meta";
          meta.textContent = slot.category;
          cell.appendChild(meta);
        }
        equipmentEl.appendChild(cell);
      });
    } else {
      renderNote(equipmentEl, "暂不可用：后端未提供装备槽（equip_slot）。", true);
    }
    const xkxSlots = Array.isArray(eq.xkx_slots) ? eq.xkx_slots : [];
    if (xkxSlots.length) {
      const title = document.createElement("div");
      title.className = "subhead";
      title.textContent = "内容包装备槽（equip_slot，引擎未接）";
      equipmentEl.appendChild(title);
      const grid = document.createElement("div");
      grid.className = "slots";
      xkxSlots.forEach((slot) => {
        const cell = document.createElement("div");
        cell.className = "slot empty";
        const name = document.createElement("span");
        name.className = "slot-name";
        name.textContent = slot.label || slot.slot;
        const item = document.createElement("span");
        item.className = "slot-item";
        item.textContent = "未接";
        cell.appendChild(name);
        cell.appendChild(item);
        grid.appendChild(cell);
      });
      equipmentEl.appendChild(grid);
    }
    const actions = eq.actions || {};
    const reasons = [
      actions.reason || "后端未提供穿脱可用性。",
      eq.xkx_slots_reason || "",
    ].filter(Boolean);
    equipNoteEl.textContent = `穿脱：暂不可用（${reasons.join("；")}）`;
  }

  function renderQuests(state) {
    const detail = state && Array.isArray(state.active_quests_detail)
      ? state.active_quests_detail
      : null;
    if (detail === null) {
      renderNote(questsEl, "暂不可用：后端未提供任务字段。", true);
      return;
    }
    if (!detail.length) {
      renderNote(questsEl, "当前没有进行中的任务。");
      return;
    }
    renderList(questsEl, detail, (quest) => {
      const el = document.createElement("div");
      el.className = "row";
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = quest.name || quest.template || "任务";
      const meta = document.createElement("span");
      meta.className = "meta";
      const progress = quest.progress || {};
      const step = progress.done
        ? "已达成"
        : `${progress.units == null ? "—" : progress.units}/${progress.required == null ? "—" : progress.required}`;
      meta.textContent = `${step} · ${quest.status || "进行中"}`;
      el.title = quest.giver_name ? `委托人：${quest.giver_name}` : "";
      el.appendChild(name);
      el.appendChild(meta);
      return el;
    }, "当前没有进行中的任务。");
  }

  function renderInventory(player) {
    const items = player && player.wallet && Array.isArray(player.wallet.items)
      ? player.wallet.items
      : null;
    if (items === null) {
      renderNote(invEl, "暂不可用：后端未提供行囊数据。", true);
      return;
    }
    renderList(invEl, items, (item) => {
      const row = makeButton("row", "", () => {
        inputEl.value = `sell ${item.name} `;
        inputEl.focus();
      });
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = item.name;
      const meta = document.createElement("span");
      meta.className = "meta";
      const slot = item.equip_slot_label ? ` · ${item.equip_slot_label}` : "";
      meta.textContent = `×${item.qty}${slot}`;
      row.appendChild(name);
      row.appendChild(meta);
      return row;
    }, "行囊空空。");
  }

  function renderGround(state) {
    const rows = state && Array.isArray(state.ground) ? state.ground : null;
    if (rows === null) {
      renderNote(groundEl, "暂不可用：后端未提供地面物品。", true);
      return;
    }
    renderList(groundEl, rows, (item) => {
      const row = makeButton("row", "", () => {
        inputEl.value = `get ${item.name || item.item} `;
        inputEl.focus();
      });
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = item.name || item.item;
      const meta = document.createElement("span");
      meta.className = "meta";
      meta.textContent = `×${item.qty} · 点击捡起`;
      row.appendChild(name);
      row.appendChild(meta);
      return row;
    }, "地上没有东西。");
  }

  function renderMapMeta(exploration) {
    const exp = exploration || null;
    if (!exp) {
      mapMetaEl.textContent = "区域与探索计数暂不可用（后端未提供 exploration）。";
      return;
    }
    const rooms = `全图已探索 ${exp.rooms_explored == null ? "—" : exp.rooms_explored}/${exp.rooms_total == null ? "—" : exp.rooms_total}`;
    if (exp.area && exp.area.name) {
      mapMetaEl.textContent =
        `${exp.area.name} · 本区已探索 ${exp.area_explored == null ? "—" : exp.area_explored}/${exp.area_total == null ? "—" : exp.area_total} · ${rooms}`;
    } else {
      // 区域名只读引擎 region；没有就写原因，不按房间 id 猜。
      mapMetaEl.textContent = `${exp.area_reason || "区域暂不可用。"} · ${rooms}`;
    }
  }

  function renderState(state) {
    if (!state || !state.ok || !state.player) {
      renderStateError("后端未返回玩家状态。");
      return;
    }
    const player = state.player;
    renderCharacter(player);
    renderSkills(player);
    renderEquipment(player);
    renderQuests(state);
    renderInventory(player);
    renderGround(state);
    renderMapMeta(player.exploration);
    const v = player.vitals || {};
    pillHp.textContent = v.hp != null && v.max_hp != null
      ? `气血：${v.hp}/${v.max_hp}`
      : "气血：未开放";
  }

  function renderStateError(reason) {
    const text = `状态暂不可用（${reason}）`;
    [characterEl, skillsEl, equipmentEl, questsEl, invEl, groundEl].forEach((el) => {
      renderNote(el, text, true);
    });
    equipNoteEl.textContent = `穿脱：暂不可用（${reason}）`;
    mapMetaEl.textContent = text;
  }

  async function refreshState() {
    try {
      const resp = await fetch(
        `/api/player/state?account_id=${encodeURIComponent(ACCOUNT)}`
      );
      const state = await resp.json();
      if (!resp.ok || !state.ok) {
        throw new Error((state && state.reason_text) || `HTTP ${resp.status}`);
      }
      renderState(state);
    } catch (err) {
      renderStateError(err && err.message ? err.message : "连接失败");
    }
  }

  function renderView(view) {
    if (!view) return;
    setStatus(view);
    renderMap(view);
    renderSide(view);
    renderQuick(view);
    renderWorld(view);
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
    hideComplete();
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
    await refreshState();
    busy = false;
    inputEl.focus();
  }

  async function loadHelpCatalog() {
    try {
      const resp = await fetch("/api/xkx/help");
      const data = await resp.json();
      if (data && data.ok !== false) helpCatalog = data;
    } catch (_) {
      helpCatalog = null;
    }
  }

  function completeCandidates() {
    const rows = new Map();
    const add = (value, info) => {
      const key = String(value || "").trim();
      if (!key || rows.has(key)) return;
      rows.set(key, { value: key, ...info });
    };
    const aliases = helpCatalog && Array.isArray(helpCatalog.shell_aliases)
      ? helpCatalog.shell_aliases
      : [];
    aliases.forEach((alias) => {
      add(alias, {
        status: "可用",
        summary: "本文字壳已接入口",
        blocker: "",
        shell: true,
      });
    });
    const commands = helpCatalog && Array.isArray(helpCatalog.commands)
      ? helpCatalog.commands
      : [];
    commands.forEach((cmd) => {
      const info = {
        status: cmd.shell ? "可用" : (cmd.status || "未注明"),
        summary: cmd.summary || "",
        blocker: cmd.blocker || "",
        shell: !!cmd.shell,
      };
      add(cmd.name, info);
      (cmd.aliases || []).forEach((alias) => add(alias, info));
    });
    return Array.from(rows.values());
  }

  function hideComplete() {
    if (!completeEl) return;
    completeEl.classList.remove("show");
    completeEl.innerHTML = "";
    completeRows = [];
    completeAt = -1;
  }

  function updateComplete() {
    if (!completeEl) return;
    const raw = inputEl.value;
    if (!raw || raw.indexOf(" ") >= 0) {
      hideComplete();
      return;
    }
    const token = raw.trim().toLowerCase();
    if (!token) {
      hideComplete();
      return;
    }
    const matches = completeCandidates()
      .filter((row) => row.value.toLowerCase().startsWith(token))
      .sort((a, b) => (b.shell ? 1 : 0) - (a.shell ? 1 : 0))
      .slice(0, 8);
    if (!matches.length) {
      hideComplete();
      return;
    }
    completeRows = matches;
    completeAt = -1;
    completeEl.innerHTML = "";
    matches.forEach((row, index) => {
      const item = document.createElement("div");
      item.className = `complete-row${row.shell ? "" : " blocked"}`;
      item.setAttribute("role", "option");
      const value = document.createElement("span");
      value.className = "value";
      value.textContent = row.value;
      const status = document.createElement("span");
      status.className = "status";
      status.textContent = row.status;
      item.appendChild(value);
      item.appendChild(status);
      if (row.blocker || row.summary) {
        item.title = row.blocker || row.summary;
      }
      item.addEventListener("mousedown", (event) => {
        event.preventDefault();
        chooseComplete(index);
      });
      completeEl.appendChild(item);
    });
    completeEl.classList.add("show");
  }

  function chooseComplete(index) {
    const row = completeRows[index];
    if (!row) return;
    inputEl.value = `${row.value} `;
    inputEl.setSelectionRange(inputEl.value.length, inputEl.value.length);
    hideComplete();
    inputEl.focus();
  }

  function longestPrefix(values) {
    if (!values.length) return "";
    let prefix = values[0];
    values.forEach((value) => {
      while (prefix && !value.startsWith(prefix)) prefix = prefix.slice(0, -1);
    });
    return prefix;
  }

  function moveComplete(delta) {
    if (!completeRows.length) return;
    completeAt = Math.max(0, Math.min(completeRows.length - 1, completeAt + delta));
    Array.from(completeEl.children).forEach((child, index) => {
      child.classList.toggle("active", index === completeAt);
    });
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

  inputEl.addEventListener("input", updateComplete);

  inputEl.addEventListener("keydown", (event) => {
    if (event.key === "Tab" && completeRows.length) {
      event.preventDefault();
      if (completeRows.length === 1) {
        chooseComplete(0);
        return;
      }
      const prefix = longestPrefix(completeRows.map((row) => row.value));
      if (prefix && prefix.length > inputEl.value.trim().length) {
        inputEl.value = prefix;
        inputEl.setSelectionRange(prefix.length, prefix.length);
      }
      updateComplete();
      return;
    }
    if (event.key === "Escape") {
      hideComplete();
      return;
    }
    if (event.key === "Enter" && completeAt >= 0) {
      event.preventDefault();
      const value = completeRows[completeAt].value;
      hideComplete();
      sendCommand(value);
      return;
    }
    if (event.key === "ArrowUp" || event.key === "ArrowDown") {
      if (completeRows.length) {
        event.preventDefault();
        moveComplete(event.key === "ArrowDown" ? 1 : -1);
        return;
      }
      if (event.key === "ArrowUp") {
        if (history.length) {
          event.preventDefault();
          historyAt = Math.max(0, historyAt - 1);
          inputEl.value = history[historyAt] || "";
          inputEl.setSelectionRange(inputEl.value.length, inputEl.value.length);
        }
      } else if (history.length) {
        event.preventDefault();
        historyAt = Math.min(history.length, historyAt + 1);
        inputEl.value = historyAt >= history.length ? "" : history[historyAt];
      }
    }
  });

  inputEl.addEventListener("blur", () => {
    window.setTimeout(hideComplete, 150);
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
  loadHelpCatalog();
  sendCommand("look", { silentEcho: true });
})();
