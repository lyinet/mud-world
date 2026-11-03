/* 玩家门面：默认反向代理真 API（docs/player-api.md, profile_version=1）；--readonly 时提案不落地。 */
(function () {
  'use strict';

  const API = {
    health: () => getJSON('/api/health'),
    enter: (body) => postJSON('/api/player/enter', body),
    state: (account) => getJSON('/api/player/state?account_id=' + encodeURIComponent(account || '')),
    action: (account, verb, args, clientTick) => postJSON('/api/player/action', {
      account_id: account || '',
      verb: verb,
      args: args || {},
      client_tick: clientTick == null ? null : clientTick
    }),
    messages: (account, since) => getJSON(
      '/api/player/messages?account_id=' + encodeURIComponent(account || '') +
      '&since_tick=' + encodeURIComponent(String(since == null ? 0 : since))
    ),
    intent: (account, id) => getJSON(
      '/api/player/intents/' + encodeURIComponent(id) +
      '?account_id=' + encodeURIComponent(account || '')
    )
  };

  const state = {
    health: null,
    session: null,     // {accountId, entityId, displayName, archetype, role, mode}
    st: null,          // 最近一次 GET /api/player/state 的契约响应
    selected: null,
    busy: false,
    pollTimer: null,
    log: [],
    sinceTick: 0,
    seenMessages: {}
  };

  const $ = (sel) => document.querySelector(sel);
  const el = (id) => document.getElementById(id);

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  async function getJSON(url) {
    const res = await fetch(url, { headers: { Accept: 'application/json' }, cache: 'no-store' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error((data && (data.message || data.error || data.reason_text)) || ('HTTP ' + res.status));
    }
    return data;
  }

  async function postJSON(url, body) {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body),
      cache: 'no-store'
    });
    const data = await res.json().catch(() => ({}));
    return { httpOk: res.ok, status: res.status, data: data };
  }

  function sleep(ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); }

  function toast(message, bad) {
    const node = el('toast');
    node.textContent = message;
    node.classList.remove('hidden', 'bad');
    if (bad) node.classList.add('bad');
    clearTimeout(toast._t);
    toast._t = setTimeout(() => node.classList.add('hidden'), 4200);
  }

  /* ------------------------------------------------------------- tick / 钱物 */
  function clockOf(tick) {
    const t = Math.max(0, Number(tick) || 0);
    const day = Math.floor(t / 144) + 1;
    const h = Math.floor((t % 144) / 6);
    const m = (t % 6) * 10;
    const pad = (n) => (n < 10 ? '0' + n : String(n));
    return { day: day, label: '第 ' + day + ' 天 ' + pad(h) + ':' + pad(m) };
  }

  function itemListText(items) {
    if (!Array.isArray(items) || !items.length) return '无';
    return items.map(function (x) {
      return (x.name || x.item || '物品') + '×' + (x.qty == null ? 1 : x.qty);
    }).join('、');
  }

  function firstChar(name) {
    return String(name || '?').slice(0, 1);
  }

  function pushLog(kind, text, meta) {
    state.log.unshift({ kind: kind, text: text, meta: meta || '', at: Date.now() });
    if (state.log.length > 40) state.log.length = 40;
    renderLog();
  }

  /* ------------------------------------------------------------- 进入流程 */
  function randomAccount() {
    const alphabet = 'abcdefghijklmnopqrstuvwxyz0123456789';
    let s = '';
    for (let i = 0; i < 6; i++) s += alphabet[Math.floor(Math.random() * alphabet.length)];
    return 'u_' + s;
  }

  function initEntryForm() {
    // 默认用创世预置账号，首次进入才有契约 §2 的 300 文/炊饼/粗布；点“随机”可换运行中新建账号（钱包为 0）。
    el('accountId').value = el('accountId').value || 'u_demo';
    el('displayName').value = el('displayName').value || '无名客';
  }

  function selectedArchetype() {
    const checked = document.querySelector('input[name="archetype"]:checked');
    return checked ? checked.value : '游侠';
  }

  function showEntry(show) {
    el('entryOverlay').classList.toggle('hidden', !show);
    if (show) {
      el('entryResult').classList.add('hidden');
      el('entryError').classList.add('hidden');
      el('enterBtn').textContent = '进入世界';
      el('enterBtn').removeAttribute('data-open');
    }
  }

  function rememberAccount(accountId, displayName, archetype) {
    try {
      localStorage.setItem('ws_player_account', JSON.stringify({
        account_id: accountId,
        display_name: displayName,
        archetype: archetype
      }));
    } catch (e) { /* ignore */ }
  }

  function renderEntryResult(result, st) {
    const box = el('entryResult');
    const p = (st && st.player) || {};
    if (!p.entity_id) {
      box.textContent = '进入成功，但状态读取失败；请刷新重试。';
      box.classList.remove('hidden');
      box.classList.add('warn');
      return;
    }
    const money = (p.wallet && p.wallet.money != null) ? p.wallet.money : 0;
    const items = (p.wallet && p.wallet.items) || [];
    const details = (result && result.details) || {};
    const lines = [];
    lines.push('<div><b>' + (state.session && state.session.mode === 'readonly'
      ? '只读模式：进入提案已提交（未落地）'
      : (details.created ? '进入世界（首次）' : '进入世界（按 account_id 恢复）')) + '</b></div>');
    lines.push('<div>账号 <code>' + escapeHtml(st.account_id || p.account_id || '—') +
      '</code> · 实体 <code>' + escapeHtml(p.entity_id || '—') + '</code></div>');
    lines.push('<div>位置：<b>' + escapeHtml(p.location_name || p.location || '—') + '</b>' +
      (p.location ? ' <code>' + escapeHtml(p.location) + '</code>' : '') + '</div>');
    lines.push('<div>钱物：<b>' + escapeHtml(money) + ' 文</b> / ' + escapeHtml(itemListText(items)) + '</div>');
    lines.push('<div>身份：' + escapeHtml(p.archetype || '—') + ' → role <code>' +
      escapeHtml(p.role || '—') + '</code> · 体力 ' +
      escapeHtml(Math.round(Number(p.stamina || 0))) + '/' +
      escapeHtml(Math.round(Number(p.max_stamina || 0))) + '</div>');
    if (details.endowment) {
      lines.push('<div class="tiny">' + (details.endowment.granted
        ? '首次进入发放：300 文 + 炊饼×3 + 粗布×1（独立事件 player_endowment）。'
        : '初始资产已发放，本次不重复发放。') + '</div>');
    }
    if (state.session && state.session.mode === 'readonly') {
      lines.push('<div style="color:#8a5a1c"><b>只读模式：</b>写操作只提交提案，不写 events.sqlite。</div>');
    }
    box.innerHTML = lines.join('');
    box.classList.remove('hidden');
    box.classList.add(state.session && state.session.mode === 'readonly' ? 'warn' : 'ok');
  }

  function renderRunTag() {
    const health = state.health || {};
    const readonly = !!health.readonly;
    el('runTag').textContent = readonly
      ? '只读模式 · ' + (health.run || '本地回放')
      : '可写 · 真 API · ' + (health.upstream || '127.0.0.1:8020') +
        (health.upstream_ok === false ? '（不可达）' : '');
  }

  async function refreshHealth() {
    const wasDown = !!(state.health && state.health.upstream_ok === false);
    try {
      state.health = await API.health();
    } catch (err) {
      markDegraded(err);
    }
    const down = state.health.upstream_ok === false && !state.health.readonly;
    if (down !== wasDown) {
      if (down) {
        toast('真 API 已断开：页面显示“上游不可达”，重启 wsim.api 后会自动恢复。', true);
      } else {
        toast('真 API 已恢复。');
      }
    }
    renderRunTag();
    renderNotice(state.st);
  }

  function markDegraded(err) {
    state.health = Object.assign({}, state.health || {}, {
      mode: 'degraded',
      upstream_ok: false,
      message: '无法连接前端服务：' + (err && (err.message || err) || '未知错误')
    });
    renderRunTag();
    renderNotice(state.st);
  }

  async function loadState(accountId) {
    const st = await API.state(accountId);
    if (!st || !st.ok || !st.player) {
      throw new Error((st && (st.message || st.reason_text || st.reason)) || '状态不可用');
    }
    applyState(st);
    return st;
  }

  function applyState(st) {
    state.st = st;
    const p = st.player || {};
    const readonly = !!st.readonly || !!(state.health && state.health.readonly);
    state.session = {
      accountId: st.account_id || p.account_id || '',
      entityId: p.entity_id || ('player:' + (st.account_id || '')),
      displayName: p.name || '',
      archetype: p.archetype || '',
      role: p.role || '',
      mode: readonly ? 'readonly' : 'live'
    };
    rememberAccount(state.session.accountId, state.session.displayName, state.session.archetype);
    render(st);
    return true;
  }

  async function doEnter() {
    const accountId = el('accountId').value.trim().toLowerCase();
    const displayName = el('displayName').value.trim();
    const archetype = selectedArchetype();
    el('entryError').classList.add('hidden');
    el('entryResult').classList.add('hidden');
    const btn = el('enterBtn');
    btn.disabled = true;
    btn.textContent = '提交中…';
    try {
      const res = await API.enter({ account_id: accountId, display_name: displayName, archetype: archetype });
      const result = res.data || {};
      if (res.status === 409 || result.ok === false) {
        throw new Error(result.message || result.reason_text || result.reason || ('HTTP ' + res.status));
      }
      const st = await loadState(accountId);
      renderEntryResult(result, st);
      btn.textContent = '进入画面';
      btn.setAttribute('data-open', '1');
    } catch (err) {
      const box = el('entryError');
      box.textContent = err.message || String(err);
      box.classList.remove('hidden');
      btn.textContent = '进入世界';
    } finally {
      btn.disabled = false;
    }
  }

  function doOpenWorld() {
    const btn = el('enterBtn');
    if (!btn.getAttribute('data-open')) {
      doEnter();
      return;
    }
    showEntry(false);
    if (state.st) render(state.st);
  }

  /* ------------------------------------------------------------- 场景渲染 */
  function render(st) {
    if (!st || !st.player) return;
    renderStatus(st);
    renderNotice(st);
    renderPlace(st);
    renderNpcs(st);
    renderQuests(st);
    renderTalkTarget();
    renderLog();
  }

  function renderStatus(st) {
    const p = st.player || {};
    const wallet = p.wallet || {};
    const active = p.active_quests || [];
    el('stMoney').textContent = (wallet.money == null ? 0 : wallet.money) + ' 文';
    el('stStamina').textContent = Math.round(Number(p.stamina || 0)) + '/' +
      Math.round(Number(p.max_stamina || 0));
    el('stItems').textContent = itemListText(wallet.items);
    el('stGoals').textContent = '任务 ' + active.length + '/2';
    el('stClock').textContent = clockOf(st.tick).label;
    const online = (st.online_players || []).length;
    el('stOnline').textContent = String(online);
    document.title = clockOf(st.tick).label + ' · 入世 · 世界门面';
  }

  function renderNotice(st) {
    const bar = el('noticeBar');
    const items = [];
    const readonly = !!((st && st.readonly) || (state.health && state.health.readonly));
    if (readonly) {
      items.push('<span class="pill warn-pill">只读模式</span><span>' +
        '提案不落地：写操作只提交提案、不写 events.sqlite；要真实移动/说话/接任务请启动真 API 并去掉 <code>--readonly</code>。</span>');
    } else if (state.health && state.health.upstream_ok === false) {
      items.push('<span class="pill warn-pill">上游不可达</span><span>' +
        '真 API 已断开，读请求会失败；请重启 <code>wsim.api</code>。</span>');
    }
    if (items.length) {
      bar.innerHTML = items.join('<br>');
      bar.classList.remove('hidden');
    } else {
      bar.classList.add('hidden');
      bar.innerHTML = '';
    }
  }

  function renderPlace(st) {
    const p = st.player || {};
    const loc = st.location || {};
    el('locId').textContent = loc.id || '—';
    el('placeName').textContent = loc.name || '未知地点';
    el('placeMe').textContent = '你在这里：' + (p.name || '') +
      (p.role ? '（' + p.role + '）' : '') + ' · tick ' + st.tick;
    const exits = el('exits');
    exits.innerHTML = '';
    (loc.exits || []).filter(function (x) { return x.exists !== false; }).forEach(function (exit) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'exit';
      btn.innerHTML = '<span>' + escapeHtml(exit.name) + '<br><span class="id">' +
        escapeHtml(exit.id) + '</span></span><span class="go">走 →</span>';
      btn.title = '移动到' + exit.name;
      btn.addEventListener('click', function () { doMove(exit); });
      exits.appendChild(btn);
    });
    if (!(loc.exits || []).length) {
      exits.innerHTML = '<div class="hint">没有可用出口。</div>';
    }
  }

  async function doMove(exit) {
    if (state.busy) return;
    if (!state.session || !state.session.accountId) {
      toast('先进入世界，才能移动。', true);
      showEntry(true);
      return;
    }
    const beforeTick = state.st ? Number(state.st.tick) : 0;
    state.busy = true;
    el('exitHint').style.color = '';
    try {
      const res = await API.action(state.session.accountId, 'move', { to: exit.id }, beforeTick);
      const data = res.data || {};
      if (res.status === 409 || data.ok === false) {
        const reason = data.message || data.reason_text || '移动被拒';
        const code = data.reason || data.reject_reason;
        el('exitHint').style.color = '#9b3b2f';
        el('exitHint').textContent = reason + (code ? '（' + code + '）' : '');
        toast(reason + (code ? '（' + code + '）' : ''), true);
        pushLog('rejected', '移动 ' + exit.name + ' 被拒：' + reason, code || '');
        return;
      }
      if (data.status === 'proposed' || data.readonly) {
        el('exitHint').style.color = '#8a5a1c';
        el('exitHint').textContent = '只读模式：已提交移动提案 ' + (data.intent_id || '') + '（未落地）';
        pushLog('proposal', '移动提案：' + exit.name, data.intent_id || '');
        return;
      }
      el('exitHint').style.color = '#3f6b57';
      el('exitHint').textContent = '移动完成：' + exit.name +
        ' · event_seq ' + (data.event_seq == null ? '?' : data.event_seq) +
        ' · tick ' + (data.tick == null ? '?' : data.tick) + ' → ' + (data.next_tick == null ? '?' : data.next_tick);
      pushLog('ok', '移动到 ' + exit.name + ' · event_seq ' + (data.event_seq == null ? '?' : data.event_seq),
        'tick ' + (data.tick == null ? '?' : data.tick));
      await loadState(state.session.accountId);
    } catch (err) {
      toast('移动请求失败：' + (err.message || err), true);
    } finally {
      state.busy = false;
    }
  }

  function renderNpcs(st) {
    const list = el('npcList');
    const present = st.present_npcs || [];
    el('npcCount').textContent = String(present.length);
    list.innerHTML = '';
    if (!present.length) {
      list.innerHTML = '<div class="log-empty">这里暂时没有别的 NPC。</div>';
    }
    present.forEach(function (npc) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'npc' + (state.selected && state.selected.id === npc.id ? ' selected' : '');
      btn.innerHTML =
        '<span class="avatar role-' + escapeHtml(npc.role) + '">' + escapeHtml(firstChar(npc.name)) + '</span>' +
        '<span class="npc-main"><span class="npc-name">' + escapeHtml(npc.name) +
        '<span class="npc-role">' + escapeHtml(npc.role) + '</span></span>' +
        '<span class="npc-doing">' + escapeHtml(npc.doing || '无记录') + '</span>' +
        '<span class="npc-goal">' + escapeHtml(npc.presence || '') + '</span>' +
        '</span>';
      btn.addEventListener('click', function () { selectNpc(npc); openNpcDetail(npc); });
      list.appendChild(btn);
    });
  }

  /* ------------------------------------------------------------- NPC 搜索 */
  function initSearch() {
    const input = el('npcSearch');
    input.addEventListener('input', function () {
      const q = input.value.trim().toLowerCase();
      const box = el('searchResults');
      if (!q) { box.classList.add('hidden'); box.innerHTML = ''; return; }
      const present = (state.st && state.st.present_npcs) || [];
      const all = present.filter(function (npc) {
        return (npc.name + npc.id + npc.role).toLowerCase().indexOf(q) >= 0;
      });
      box.innerHTML = '';
      if (!all.length) {
        box.innerHTML = '<div class="search-row"><small>当前地点没有这个人（契约 state 不提供全量 NPC 列表）</small></div>';
        box.classList.remove('hidden');
        return;
      }
      all.slice(0, 10).forEach(function (npc) {
        const row = document.createElement('button');
        row.type = 'button';
        row.className = 'search-row';
        row.innerHTML = '<span>' + escapeHtml(npc.name) + ' <small>' + escapeHtml(npc.role) + '</small></span>' +
          '<small>在此</small>';
        row.addEventListener('click', function () {
          box.classList.add('hidden');
          input.value = '';
          selectNpc(npc);
        });
        box.appendChild(row);
      });
      box.classList.remove('hidden');
    });
  }

  function selectNpc(npc) {
    state.selected = npc;
    renderNpcs(state.st || {});
    renderTalkTarget();
    el('talkResult').classList.add('hidden');
    el('talkReplies').classList.add('hidden');
    el('talkReplies').innerHTML = '';
    state.seenMessages = {};
    el('talkInput').focus();
  }

  function renderTalkTarget() {
    const box = el('talkTarget');
    const npc = state.selected;
    if (!npc) {
      box.innerHTML = '<div class="empty">点左侧在场 NPC，或搜索当前地点的人。</div>';
      return;
    }
    box.innerHTML =
      '<span class="avatar role-' + escapeHtml(npc.role || '') + '">' + escapeHtml(firstChar(npc.name)) + '</span>' +
      '<div><div class="who">' + escapeHtml(npc.name) + '</div>' +
      '<div class="where">' + escapeHtml(npc.role || '') + ' · ' +
      escapeHtml((state.st && state.st.location && state.st.location.name) || '当前地点') +
      ' · ' + escapeHtml(npc.presence || '') + '</div></div>';
  }

  function renderReplies(rows, sinceTick, label) {
    const box = el('talkReplies');
    if (!box) return 0;
    let added = 0;
    (rows || []).forEach(function (msg) {
      const id = String(msg.id || msg.seq || ((msg.from || '') + ':' + (msg.tick || 0) + ':' + (msg.text || '')));
      if (state.seenMessages[id]) return;
      state.seenMessages[id] = true;
      const node = document.createElement('div');
      node.className = 'reply-item';
      node.innerHTML =
        '<div class="reply-head"><b>' + escapeHtml(msg.from || 'NPC') + '</b>' +
        '<span class="pill">世界回应</span><small>tick ' + escapeHtml(msg.tick == null ? '?' : msg.tick) +
        ' · ' + escapeHtml(label || ('/api/player/messages?since_tick=' + sinceTick)) + '</small></div>' +
        '<div class="reply-text">' + escapeHtml(msg.text || '') + '</div>';
      box.appendChild(node);
      added++;
    });
    if (added) box.classList.remove('hidden');
    return added;
  }

  async function pollReplies(account, sinceTick, attempts) {
    for (let i = 0; i < attempts; i++) {
      try {
        const r = await API.messages(account, sinceTick);
        const rows = (r.messages || []).filter(function (m) { return m.kind === 'talk_reply'; });
        if (rows.length) {
          renderReplies(rows, sinceTick);
          state.sinceTick = Math.max(state.sinceTick || 0, Number(r.next_since_tick || 0));
          return rows;
        }
      } catch (err) { /* 下一轮再试 */ }
      if (i < attempts - 1) await sleep(600);
    }
    return [];
  }

  async function doTalk() {
    const npc = state.selected;
    const account = state.session && state.session.accountId;
    if (!account) { toast('先进入世界，才能说话。', true); showEntry(true); return; }
    if (!npc) { toast('先选一个 NPC。', true); return; }
    const message = el('talkInput').value.trim();
    if (!message) { toast('消息不能为空。', true); return; }
    if (message.length > 200) { toast('消息不能超过 200 字。', true); return; }
    const beforeTick = state.st ? Number(state.st.tick) : 0;
    const input = el('talkInput');
    const send = el('talkSend');
    send.disabled = true;
    try {
      const res = await API.action(account, 'talk', { target: npc.id, message: message }, beforeTick);
      const data = res.data || {};
      const box = el('talkResult');
      box.classList.remove('hidden', 'ok', 'bad');
      if (res.status === 409 || data.ok === false) {
        const reason = data.message || data.reason_text || '对话被拒';
        const code = data.reason || data.reject_reason;
        box.classList.add('bad');
        box.innerHTML = '<b>被拒：</b>' + escapeHtml(reason) +
          '<br><span class="tiny">拒绝码：<code>' + escapeHtml(code || '') + '</code></span>';
        toast(reason + (code ? '（' + code + '）' : ''), true);
        pushLog('rejected', '对 ' + npc.name + ' 说话被拒：' + reason, code || '');
        return;
      }
      if (data.status === 'proposed' || data.readonly) {
        box.classList.add('ok');
        box.innerHTML = '<b>已提交对话提案</b> <code>' + escapeHtml(data.intent_id || '') + '</code><br>' +
          '只读模式：提案停在 outbox，世界不会产生回应。';
        pushLog('proposal', '对话提案：' + npc.name, data.intent_id || '');
        return;
      }
      const since = Math.max(0, Number(data.tick == null ? beforeTick : data.tick) - 1);
      box.classList.add('ok');
      box.innerHTML = '<b>对话已执行</b> <code>' + escapeHtml(data.intent_id || '') + '</code>' +
        ' · event_seq ' + escapeHtml(data.event_seq == null ? '?' : data.event_seq) +
        ' · tick ' + escapeHtml(data.tick == null ? '?' : data.tick) +
        '<br><span class="tiny">正在用 <code>/api/player/messages?since_tick=' + since + '</code> 取世界回应…</span>';
      input.value = '';
      pushLog('ok', '对 ' + npc.name + ' 说话', 'event_seq ' + (data.event_seq == null ? '?' : data.event_seq));
      const rows = await pollReplies(account, since, 3);
      if (rows.length) {
        box.innerHTML += '<br><span class="tiny">已在 since_tick 轮询中收到 ' + rows.length + ' 条世界回应。</span>';
        toast('收到 ' + npc.name + ' 的世界回应。');
      } else if (data.details && data.details.reply) {
        renderReplies(
          [{ id: 'details:' + (data.event_seq || data.tick), from: npc.name, tick: data.tick, text: data.details.reply }],
          since,
          'details.reply 兜底（同一事件）'
        );
        box.innerHTML += '<br><span class="tiny">messages 暂未返回，已按 <code>details.reply</code> 兜底显示。</span>';
        toast('收到 ' + npc.name + ' 的回应（details.reply）。');
      } else {
        toast('对话已执行，但世界没有产生回应。');
      }
      await loadState(account);
    } catch (err) {
      toast('对话请求失败：' + (err.message || err), true);
    } finally {
      send.disabled = false;
    }
  }

  /* ------------------------------------------------------------- NPC 详情 */
  function openNpcDetail(npc) {
    el('npcAvatar').textContent = firstChar(npc.name);
    el('npcAvatar').className = 'avatar big role-' + String(npc.role || '');
    el('npcModalName').textContent = npc.name || npc.id;
    el('npcModalMeta').textContent = (npc.role || '无职业') + ' · ' + (npc.location || '') +
      ' · ' + (npc.presence || '');
    el('npcModalBody').innerHTML =
      '<div class="kv"><dt>最近在做</dt><dd>' + escapeHtml(npc.doing || '无记录') + '</dd>' +
      '<dt>所在地点</dt><dd><code>' + escapeHtml(npc.location || '—') + '</code></dd>' +
      '<dt>在线状态</dt><dd>' + escapeHtml(npc.presence || '—') + '</dd></div>' +
      '<div class="hint">契约 <code>/api/player/state</code> 只提供以上字段；目标进度与关系历史不在本接口。</div>';
    el('npcModal').classList.remove('hidden');
  }

  function closeNpcModal() { el('npcModal').classList.add('hidden'); }

  /* ------------------------------------------------------------- 本地操作记录 */
  function renderLog() {
    const box = el('log');
    box.innerHTML = '';
    if (!state.log.length) {
      box.innerHTML = '<div class="log-empty">每次操作的结果会记在这里；世界日志不由本接口提供。</div>';
      return;
    }
    state.log.forEach(function (row) {
      const node = document.createElement('div');
      node.className = 'log-item ' + row.kind;
      const tag = row.kind === 'proposal' ? '提案 · 待执行'
        : row.kind === 'rejected' ? '被拒' : '你';
      node.innerHTML = '<div class="log-meta"><span>' +
        escapeHtml(row.meta || '') + '</span><span class="tag">' + escapeHtml(tag) + '</span></div>' +
        '<div>' + escapeHtml(row.text || '') + '</div>';
      box.appendChild(node);
    });
    box.scrollTop = 0;
  }

  /* ------------------------------------------------------------- 任务板 */
  function renderQuests(st) {
    const board = st.quest_board || {};
    const rows = board.rows || [];
    const active = board.active || [];
    const counts = board.counts || {};
    const cards = active.concat(rows);
    el('questCount').textContent = String(cards.length);
    el('questSummary').innerHTML =
      '<span>开放 ' + escapeHtml(counts.open || 0) + '</span>' +
      '<span>可就地接 ' + escapeHtml(counts.acceptable_here || 0) + '</span>' +
      '<span>我进行中 ' + escapeHtml(counts.accepted_by_me || 0) + '</span>';
    const list = el('questList');
    list.innerHTML = '';
    if (!cards.length) {
      list.innerHTML = '<div class="log-empty">任务板空了，等世界再发任务。</div>';
      return;
    }
    cards.forEach(function (q) {
      const mine = !!q.accepted_by_me || (q.status === 'accepted' && active.some(function (x) { return x.id === q.id; }));
      const hint = mine ? 'status=accepted · accepted_by_me=true'
        : q.expired ? '已过期'
          : q.can_accept_here ? '发布人在此，可直接接单'
            : q.acceptable ? '可接，但需到发布人所在地点'
              : '暂不可接（发布人不在场或任务上限）';
      const card = document.createElement('div');
      card.className = 'quest' + (q.can_accept_here && !mine ? ' acceptable' : '');
      const statusPill = mine
        ? '<span class="pill accepted" title="status=accepted, accepted_by_me=true">accepted_by_me</span>'
        : '<span class="pill open">open</span>';
      card.innerHTML =
        '<div class="quest-top"><div class="quest-name">' + escapeHtml(q.name) + '</div>' +
        '<div class="quest-reward">' + escapeHtml(q.reward_money) + ' 文</div></div>' +
        '<div class="quest-meta">发布人：' + escapeHtml(q.giver_name) + ' · ' +
        escapeHtml(q.giver_location || '') +
        (q.deadline_day ? ' · 截止第 ' + escapeHtml(q.deadline_day) + ' 天' : '') + '</div>' +
        '<div class="quest-hint' + (q.can_accept_here && !mine ? ' ok' : '') + '">' + escapeHtml(hint) + '</div>' +
        '<div class="quest-actions">' + statusPill + (mine
          ? '<button type="button" class="ghost" disabled>进行中</button>'
          : '<button type="button" class="' + (q.can_accept_here ? 'primary' : 'ghost') +
            '" data-quest="' + escapeHtml(q.id) + '">去接</button>') + '</div>';
      const btn = card.querySelector('button[data-quest]');
      if (btn) btn.addEventListener('click', function () { doAccept(q); });
      list.appendChild(card);
    });
  }

  async function doAccept(quest) {
    const account = state.session && state.session.accountId;
    if (!account) { toast('先进入世界，才能接任务。', true); showEntry(true); return; }
    const beforeTick = state.st ? Number(state.st.tick) : 0;
    const res = await API.action(account, 'quest_accept', { quest: quest.id }, beforeTick)
      .catch(function (err) {
        return { status: 0, data: { ok: false, message: '接任务请求失败：' + (err.message || err) } };
      });
    const data = res.data || {};
    if (res.status === 409 || data.ok === false) {
      const reason = data.message || data.reason_text || '接任务被拒';
      const code = data.reason || data.reject_reason;
      toast(reason + (code ? '（' + code + '）' : ''), true);
      pushLog('rejected', '接任务「' + quest.name + '」被拒：' + reason, code || '');
      return;
    }
    if (data.status === 'proposed' || data.readonly) {
      toast('只读模式：已提交接任务提案（不落地）。');
      pushLog('proposal', '接任务提案：' + quest.name, data.intent_id || '');
      return;
    }
    const st = await loadState(account);
    const counts = (st.quest_board && st.quest_board.counts) || {};
    toast('已接任务：' + quest.name +
      (counts.accepted_by_me != null ? '（我进行中 ' + counts.accepted_by_me + '）' : '') +
      ' · event_seq ' + (data.event_seq == null ? '?' : data.event_seq));
    pushLog('ok', '接下任务「' + quest.name + '」', 'event_seq ' + (data.event_seq == null ? '?' : data.event_seq));
  }

  /* ------------------------------------------------------------- init */
  async function restoreAccount() {
    // 旧版本可能留下 ws_player_session；它不参与恢复，直接清掉，避免读旧快照。
    try { localStorage.removeItem('ws_player_session'); } catch (e) { /* ignore */ }
    let raw = null;
    try { raw = localStorage.getItem('ws_player_account'); } catch (e) { raw = null; }
    if (!raw) return false;
    let saved = null;
    try { saved = JSON.parse(raw); } catch (e) { saved = null; }
    if (!saved || !saved.account_id) {
      try { localStorage.removeItem('ws_player_account'); } catch (e) { /* ignore */ }
      return false;
    }
    try {
      const st = await API.state(saved.account_id);
      if (st && st.ok && st.player) {
        applyState(st);
        return true;
      }
    } catch (err) {
      // 上游暂时不可达：回进入页，不伪造位置。
    }
    try { localStorage.removeItem('ws_player_account'); } catch (e) { /* ignore */ }
    return false;
  }

  function initEvents() {
    document.querySelectorAll('.archetype').forEach(function (node) {
      node.addEventListener('click', function () {
        document.querySelectorAll('.archetype').forEach(function (n) { n.classList.remove('selected'); });
        node.classList.add('selected');
        node.querySelector('input').checked = true;
        if (el('displayName').value === '无名客' || el('displayName').value === '') {
          el('displayName').value = '无名' + node.getAttribute('data-value');
        }
      });
    });
    el('genId').addEventListener('click', function () { el('accountId').value = randomAccount(); });
    el('enterBtn').addEventListener('click', doOpenWorld);
    el('switchBtn').addEventListener('click', function () {
      state.session = null;
      state.selected = null;
      state.st = null;
      state.log = [];
      el('log').innerHTML = '';
      try { localStorage.removeItem('ws_player_account'); } catch (e) { /* ignore */ }
      el('accountId').value = randomAccount();
      el('displayName').value = '无名客';
      showEntry(true);
    });
    el('talkSend').addEventListener('click', doTalk);
    el('talkInput').addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter') { ev.preventDefault(); doTalk(); }
    });
    el('npcClose').addEventListener('click', closeNpcModal);
    el('npcModal').addEventListener('click', function (ev) {
      if (ev.target === el('npcModal')) closeNpcModal();
    });
    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape') { closeNpcModal(); el('searchResults').classList.add('hidden'); }
    });
    document.addEventListener('click', function (ev) {
      const box = el('searchResults');
      const input = el('npcSearch');
      if (!box.classList.contains('hidden') && !box.contains(ev.target) && ev.target !== input) {
        box.classList.add('hidden');
      }
    });
  }

  async function init() {
    initEvents();
    initSearch();
    initEntryForm();
    try {
      state.health = await API.health();
      renderRunTag();
      if (!state.health.ok) {
        const box = el('entryError');
        box.textContent = '世界不可用：' + (state.health.message || state.health.error || '未知错误');
        box.classList.remove('hidden');
        el('enterBtn').disabled = true;
        return;
      }
      const restored = await restoreAccount();
      renderNotice(state.st);
      if (restored) {
        showEntry(false);
      } else {
        showEntry(true);
      }
      if (state.st) render(state.st);
      clearInterval(state.pollTimer);
      state.pollTimer = setInterval(async function () {
        await refreshHealth();
        if (el('entryOverlay').classList.contains('hidden') && state.session) {
          try {
            await loadState(state.session.accountId);
          } catch (err) {
            markDegraded(err);
          }
        }
      }, 10000);
    } catch (err) {
      const box = el('entryError');
      box.textContent = '无法连接服务端：' + (err.message || err);
      box.classList.remove('hidden');
      el('enterBtn').disabled = true;
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
