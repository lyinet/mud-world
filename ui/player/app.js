/* 玩家门面：默认反向代理真 API（docs/player-api.md, profile_version=1）；--readonly 时提案不落地。 */
(function () {
  'use strict';

  /* 读接口一律带令牌：服务模式下不带令牌只会拿到公共摘要（没有 wallet/背包）。 */
  const API = {
    health: () => getJSON('/api/health'),
    enter: (body) => postJSON('/api/player/enter', body),
    state: (account) => getJSON('/api/player/state?account_id=' + encodeURIComponent(account || ''), true),
    action: (account, verb, args, clientTick) => postJSON('/api/player/action', {
      account_id: account || '',
      verb: verb,
      args: args || {},
      client_tick: clientTick == null ? null : clientTick
    }),
    messages: (account, since) => getJSON(
      '/api/player/messages?account_id=' + encodeURIComponent(account || '') +
      '&since_tick=' + encodeURIComponent(String(since == null ? 0 : since)),
      true
    ),
    intents: (account, id) => getJSON(
      '/api/player/intents/' + encodeURIComponent(id) +
      '?account_id=' + encodeURIComponent(account || ''),
      true
    ),
    quests: (account) => getJSON(
      '/api/player/quests?account_id=' + encodeURIComponent(account || ''),
      true
    )
  };

  /* 原因码映射：前端只认 reject_reason，中文文案由这里兜底。
     未知码仍会显示服务端 reason_text；两者都没有时走通用兜底。 */
  const REASON_TEXT = {
    auth_required: '需要登录后才能执行写操作；请先登录/注册。',
    auth_bad_username: '用户名需为 3-32 位小写字母、数字、下划线或连字符。',
    auth_bad_password: '口令长度需为 6-128 位。',
    auth_username_taken: '该用户名已被注册；换一个用户名再试。',
    auth_invalid_credentials: '用户名或口令不正确；请检查后重试。',
    auth_missing_token: '缺少登录令牌；请重新登录。',
    auth_invalid_token: '登录令牌无效或已注销；请重新登录。',
    auth_expired_token: '登录已过期；请重新登录。',
    auth_account_mismatch: '请求账号与令牌账号不一致；请用令牌对应的账号操作。',
    auth_disabled: '当前为 legacy 匿名模式，鉴权接口不可用；可匿名游玩。',
    invalid_token: '登录凭证无效；请重新登录。',
    token_expired: '登录已过期；请重新登录。',
    bad_credentials: '用户名或口令不正确；请检查后重试。',
    username_taken: '这个用户名已被占用；换一个用户名再注册。',
    weak_password: '口令太短或格式不合要求；请换一个更稳妥的口令。',
    bad_username: '用户名格式不合法；请用 3-32 位字母、数字、下划线或连字符。',
    auth_unavailable: '登录服务暂时不可用；可先匿名浏览，稍后再试。',
    bad_json: '请求体不是合法 JSON；请刷新页面后重试。',
    missing_account: '缺少账号；请先创建账号再进入世界。',
    bad_account: '账号格式不合法（3-32 位小写字母、数字、下划线或连字符）。',
    unknown_action: '未知操作；请刷新页面后重试。',
    forbidden_verb: '该操作在本阶段不开放（伤害/抢掠/强迫婚配等不可逆动作被禁用）。',
    player_not_entered: '你还没有进入世界；请先进入世界。',
    player_offline: '你已离线；请重新进入世界后再操作。',
    already_offline: '你已经是离线状态。',
    already_online: '你已经在线，无需重复进入。',
    identity_locked: '身份进入后不可更改；请用原身份重新登录。',
    bad_archetype: '身份必须是游侠、商人或捕快。',
    bad_display_name: '角色名需 2-12 字，且不能与现有角色重名。',
    bad_location: '地点不存在。',
    unknown_location: '地点不存在；请从当前地点可用的出口中选择。',
    not_adjacent: '目标地点与当前位置不相邻；请沿道路逐段移动。',
    insufficient_stamina: '体力不足；请先休息或进食。',
    unknown_target: '目标人物不存在。',
    not_together: '对方不在你当前所在的地点；先移动到对方所在处。',
    target_offline: '对方不在线，不能进行该操作。',
    player_consent_required: '该操作需要对方本人同意；本阶段关闭 NPC 对玩家的婚配与招揽。',
    bad_message: '消息不能为空且不超过 200 字。',
    heartbeat_too_soon: '心跳过于频繁；请稍后再试。',
    stale_tick: '你的请求使用了过期时间点；请刷新状态后重试。',
    unknown_verb: '动作未注册；请刷新页面后重试。',
    quest_not_found: '这条委托已不在任务板上；请刷新任务板换一条。',
    quest_not_open: '任务已被接走或已结束；请换一条开放中的委托。',
    giver_missing: '任务发布人已不在世界；这条委托无法继续。',
    not_at_giver: '你不在委托人所在的地点；先走到委托人那里。',
    busy_on_escort: '你正在押镖，不能同时接单；先完成押镖。',
    too_many_quests: '你手上已有 2 个进行中任务；先交付其中一个，再接新单。',
    quest_not_accepted_by_actor: '这个任务不是由你接下的，不能由你交付。',
    quest_expired: '这条委托已超过截止时间；去任务板换一条未过期的。',
    reputation_too_low: '声望还不够接这一单；先做小委托或经营人情，达到门槛再来。',
    not_at_target: '还没到任务目标地点或委托人处；先移动过去再交付。',
    quest_item_missing: '采购类委托需要你持有目标物品；先取得物品再交付。',
    item_not_held: '你身上没有这条委托要求的物品；先取得物品再交付。',
    trade_unfair: '交易价值明显不对等；对方拒绝了这笔买卖。',
    service_item: '服务类物品不能用于玩家交易。',
    offer_not_held: '你给出的物品数量不足。',
    want_not_held: '对方没有你想要的物品。',
    insufficient_money: '你的钱不够。',
    target_insufficient_money: '对方的钱不够支付这笔差额。',
    hostile: '对方对你敌对，拒绝交易。',
    role_not_allowed: '你的身份不允许该操作。',
    player_cap_reached: '本区域玩家席位已满，暂时无法创建新账号。',
    endowment_already_granted: '该账号已经领取过初始资产，不能重复领取。',
    endowment_cap_reached: '本区域初始资产发放总额已达上限；请联系运维。',
    account_missing: '玩家钱包账户不存在，已被回滚。',
    effect_invalid: '操作违反世界规则，已被回滚。',
    bad_args: '参数不完整或格式错误。',
    'bad_arg:money_delta': '金额参数必须是整数。',
    intent_not_found: '没有找到这个请求；请刷新后重试。',
    readonly_mode: '只读模式不支持该动作；请用可写模式启动。'
  };

  const AUTH_KEY = 'ws_auth_v1';
  const GUIDE_KEY = 'ws_player_guide_v1';
  const CHECK_MODE = /(?:^|[?&])ui_check=1(?:&|$)/.test(window.location.search);
  /* W1 实际契约：鉴权端点统一在 /api/auth/*；refresh/logout 用 Bearer，不用 refresh_token。 */
  const AUTH_API = {
    register: '/api/auth/register',
    login: '/api/auth/login',
    refresh: '/api/auth/refresh',
    logout: '/api/auth/logout'
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
    seenMessages: {},
    auth: {
      mode: 'unknown',   // unknown | auth | legacy
      access: '',
      expiresAt: 0,
      username: '',
      sessionId: '',
      required: false,
      probeDone: false,
      refreshing: null   // 单飞刷新：同一时刻只允许一个 refresh in flight
    },
    guide: {
      active: false,
      step: 1,
      status: 'idle',    // idle | active | skipped | completed
      accountId: ''
    },
    ui: {
      errors: [],
      foldsReady: false,
      toastTimer: null
    }
  };

  const $ = (sel) => document.querySelector(sel);
  const el = (id) => document.getElementById(id);

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function authHeader() {
    return state.auth.access ? { Authorization: 'Bearer ' + state.auth.access } : {};
  }

  /* 跨账号读被拒时，令牌账号才是事实源：把 account_id 换成令牌身份重取一次。 */
  function withIdentityAccount(url) {
    const identity = state.auth.username;
    if (!identity) return url;
    const q = url.indexOf('?');
    const base = q < 0 ? url : url.slice(0, q);
    const params = new URLSearchParams(q < 0 ? '' : url.slice(q + 1));
    params.set('account_id', identity);
    const qs = params.toString();
    return qs ? base + '?' + qs : base;
  }

  async function getJSON(url, withAuth, opts) {
    opts = opts || {};
    const sentToken = withAuth ? state.auth.access : '';
    const headers = Object.assign(
      { Accept: 'application/json' },
      sentToken ? { Authorization: 'Bearer ' + sentToken } : {}
    );
    const res = await fetch(url, { headers: headers, cache: 'no-store' });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401 && withAuth) {
      if (!opts.retried) {
        // legacy 服务端不认令牌：退成匿名读，不弹登录。
        if (state.auth.mode === 'legacy') return getJSON(url, false, Object.assign({}, opts, { retried: true }));
        // 与写操作共用同一个单飞刷新；并发 401 只 refresh 一次，每个请求各重放一次。
        if (await ensureFreshToken(sentToken)) return getJSON(url, true, Object.assign({}, opts, { retried: true }));
      }
      // 刷新确实失败：只清令牌、提示一次；最近一次世界状态保留在 state.st。
      if (state.auth.access === sentToken) {
        clearAuth('expired');
        notifyAuthRequired('登录已失效，需要重新登录；世界状态保持不变。');
      }
      const authErr = new Error((data && (data.reason_text || data.message)) || '需要重新登录');
      authErr.authRequired = true;
      authErr.status = 401;
      authErr.code = data && data.code;
      throw authErr;
    }
    if (res.status === 403 && data && data.code === 'auth_account_mismatch'
        && !opts.identityRetried && state.auth.access) {
      // 用令牌身份重取一次；这不是玩家可感知的错误，不写错误历史、不弹红字。
      return getJSON(withIdentityAccount(url), withAuth, Object.assign({}, opts, { identityRetried: true }));
    }
    if (!res.ok) {
      const err = new Error((data && (data.message || data.error || data.reason_text)) || ('HTTP ' + res.status));
      err.status = res.status;
      err.code = data && data.code;
      throw err;
    }
    return data;
  }

  function isAuthEndpoint(url) {
    return /^\/api\/auth\//.test(String(url || ''));
  }

  /* 单飞刷新：同一时刻只允许一个 refresh in flight。
     令牌已被别的请求换新时，直接用新令牌重放，不再发第二次 refresh。 */
  function ensureFreshToken(failedToken) {
    if (state.auth.access && failedToken && state.auth.access !== failedToken) {
      return Promise.resolve(true);
    }
    if (!state.auth.access) return Promise.resolve(false);
    if (state.auth.refreshing) return state.auth.refreshing;
    const p = refreshAuth().then(function (ok) {
      // 我们的 refresh 失败、但并发的另一次（如启动探针）已换出新令牌时，
      // 同样视为“可重放”，否则会把刚换好的令牌误当成失效。
      return !!ok || !!(state.auth.access && failedToken && state.auth.access !== failedToken);
    }, function () { return false; });
    state.auth.refreshing = p;
    p.then(function () {
      if (state.auth.refreshing === p) state.auth.refreshing = null;
    });
    return p;
  }

  async function postJSON(url, body, opts) {
    opts = opts || {};
    const authEndpoint = isAuthEndpoint(url);
    // refresh/logout 需要 Bearer；register/login 不带令牌；业务写操作带令牌。
    const withToken = authEndpoint
      ? (url === AUTH_API.refresh || url === AUTH_API.logout)
      : true;
    const sentToken = state.auth.access;
    const headers = Object.assign(
      { 'Content-Type': 'application/json', Accept: 'application/json' },
      withToken && sentToken ? { Authorization: 'Bearer ' + sentToken } : {}
    );
    const res = await fetch(url, {
      method: 'POST',
      headers: headers,
      body: JSON.stringify(body == null ? {} : body),
      cache: 'no-store'
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401 && !authEndpoint) {
      if (!opts.retried) {
        const renewed = await ensureFreshToken(sentToken);
        if (renewed) return postJSON(url, body, Object.assign({}, opts, { retried: true }));
      }
      // 服务端已判定为 legacy（refresh 返回 503 auth_disabled）：不按登录失效处理。
      if (state.auth.mode === 'legacy') {
        return { httpOk: res.ok, status: res.status, data: data, authRequired: false, authDisabled: true };
      }
      // 并发 401 只让第一个请求负责清令牌/提示，避免重复弹窗。
      if (state.auth.access === sentToken) {
        clearAuth('expired');
        notifyAuthRequired('登录已失效，需要重新登录；世界状态保持不变。');
      }
    }
    if (res.status === 403 && data && data.code === 'auth_account_mismatch'
        && !authEndpoint && !opts.identityRetried && state.auth.username) {
      // 令牌账号是唯一事实源：用令牌身份重写请求账号并重试一次，不当作玩家错误。
      const next = Object.assign({}, body || {}, { account_id: state.auth.username });
      return postJSON(url, next, Object.assign({}, opts, { identityRetried: true }));
    }
    return {
      httpOk: res.ok,
      status: res.status,
      data: data,
      authRequired: res.status === 401,
      authDisabled: !!(data && data.code === 'auth_disabled')
    };
  }

  function sleep(ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); }

  function toast(message, bad) {
    const node = el('toast');
    node.textContent = message;
    node.classList.remove('hidden', 'bad');
    node.setAttribute('aria-live', bad ? 'assertive' : 'polite');
    if (bad) node.classList.add('bad');
    clearTimeout(state.ui.toastTimer);
    state.ui.toastTimer = setTimeout(function () { node.classList.add('hidden'); }, bad ? 5200 : 4200);
  }

  function reasonText(code, serverText) {
    const key = String(code == null ? '' : code).trim();
    if (key && REASON_TEXT[key]) return REASON_TEXT[key];
    const human = String(serverText == null ? '' : serverText).trim();
    if (human) return human;
    if (key) return '操作没有完成：' + key + '（服务端没有给出中文说明，请刷新后再试）。';
    return '操作没有完成，但服务端没有给出原因；请刷新状态后重试。';
  }

  function reasonOf(data, status) {
    const d = data || {};
    const code = String(d.code || d.reject_reason || d.reason || d.error || '').trim();
    const serverText = d.reason_text || d.message || '';
    const authCodes = ['auth_required', 'auth_missing_token', 'auth_invalid_token',
      'auth_expired_token', 'invalid_token', 'token_expired'];
    return {
      code: code,
      text: reasonText(code, serverText),
      isAuth: status === 401 || authCodes.indexOf(code) >= 0
    };
  }

  function clockText(at) {
    const d = new Date(at || Date.now());
    const p = function (n) { return n < 10 ? '0' + n : String(n); };
    return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }

  function renderErrorDock() {
    const rows = state.ui.errors.slice(0, 3);
    const toggle = el('errorToggle');
    const list = el('errorList');
    el('errorCount').textContent = String(rows.length);
    toggle.classList.toggle('hidden', !rows.length);
    if (!rows.length) {
      list.classList.add('hidden');
      list.innerHTML = '';
      return;
    }
    list.innerHTML = rows.map(function (r) {
      return '<div class="error-item"><div class="error-head"><b>' + escapeHtml(r.context || '操作') +
        '</b><span>' + escapeHtml(clockText(r.at)) + '</span></div>' +
        '<div>' + escapeHtml(r.text || '') + '</div>' +
        (r.code ? '<div class="error-code">原因码：<code>' + escapeHtml(r.code) + '</code></div>' : '') +
        '</div>';
    }).join('');
  }

  function recordError(context, code, text) {
    state.ui.errors.unshift({ at: Date.now(), context: context, code: code || '', text: text || '' });
    if (state.ui.errors.length > 3) state.ui.errors.length = 3;
    renderErrorDock();
  }

  function notifyError(context, data, status, fallback) {
    const r = reasonOf(data, status);
    const text = r.text || fallback || '操作没有完成。';
    toast(text, true);
    recordError(context, r.code, text);
    pushLog('rejected', context + '：' + text, r.code || '');
    return r;
  }

  /* ------------------------------------------------------------- 鉴权 / 令牌 */
  function loadStoredAuth() {
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(AUTH_KEY) || 'null'); } catch (e) { saved = null; }
    if (!saved || typeof saved !== 'object') return false;
    state.auth.access = String(saved.access_token || saved.token || '');
    state.auth.expiresAt = normalizeExpires(saved.expires_at, 0);
    state.auth.username = String(saved.username || '');
    state.auth.sessionId = String(saved.session_id || '');
    return !!state.auth.access;
  }

  function persistAuth() {
    try {
      if (!state.auth.access) {
        localStorage.removeItem(AUTH_KEY);
        return;
      }
      localStorage.setItem(AUTH_KEY, JSON.stringify({
        access_token: state.auth.access,
        expires_at: state.auth.expiresAt,
        username: state.auth.username,
        session_id: state.auth.sessionId
        // 口令不落盘：只在登录请求体里出现。
      }));
    } catch (e) { /* localStorage 不可用时保留内存态 */ }
  }

  function clearAuth(reason) {
    state.auth.access = '';
    state.auth.expiresAt = 0;
    state.auth.sessionId = '';
    state.auth.required = reason === 'expired';
    try { localStorage.removeItem(AUTH_KEY); } catch (e) { /* ignore */ }
    renderAuthUI();
  }

  function markLegacy() {
    state.auth.mode = 'legacy';
    state.auth.access = '';
    state.auth.expiresAt = 0;
    state.auth.username = '';
    state.auth.sessionId = '';
    state.auth.required = false;
    state.auth.probeDone = true;
    try { localStorage.removeItem(AUTH_KEY); } catch (e) { /* ignore */ }
    renderAuthUI();
  }

  function tokenFields(payload) {
    const d = payload && typeof payload === 'object' ? payload : {};
    const inner = d.data && typeof d.data === 'object' ? d.data : {};
    const merged = Object.assign({}, d, inner);
    return {
      access: String(merged.token || merged.access_token || merged.accessToken || ''),
      expiresAt: merged.expires_at || merged.expiresAt || merged.expires || '',
      expiresIn: merged.expires_in || merged.expiresIn || 0,
      username: String((merged.user && (merged.user.username || merged.user.name)) || merged.username || ''),
      sessionId: String(merged.session_id || '')
    };
  }

  function normalizeExpires(value, expiresIn) {
    if (value != null && value !== '') {
      const n = Number(value);
      if (Number.isFinite(n) && n > 0) return n > 1e12 ? Math.round(n) : Math.round(n * 1000);
      const t = Date.parse(String(value));
      if (Number.isFinite(t) && t > 0) return t;
    }
    if (Number(expiresIn) > 0) return Date.now() + Number(expiresIn) * 1000;
    return 0;
  }

  function applyAuthPayload(payload, username) {
    const t = tokenFields(payload);
    if (!t.access) return false;
    // 轮换语义：新令牌立即替换旧令牌，绝不在刷新后再用旧值。
    state.auth.access = t.access;
    state.auth.expiresAt = normalizeExpires(t.expiresAt, t.expiresIn);
    state.auth.username = t.username || username || state.auth.username || '';
    state.auth.sessionId = t.sessionId || state.auth.sessionId || '';
    state.auth.mode = 'auth';
    state.auth.required = false;
    persistAuth();
    renderAuthUI();
    return true;
  }

  /* refresh 需要 Bearer；一次成功后旧令牌立刻失效。
     失败时区分 legacy（503 auth_disabled / 404 / 405 / 501）与真失效（401）。 */
  async function refreshAuth() {
    const token = state.auth.access;
    if (!token) return false;
    let res = null;
    try {
      res = await fetch(AUTH_API.refresh, {
        method: 'POST',
        headers: { Accept: 'application/json', Authorization: 'Bearer ' + token },
        cache: 'no-store'
      });
    } catch (e) {
      return false;
    }
    if (res.status === 404 || res.status === 405 || res.status === 501) {
      markLegacy();
      return false;
    }
    const body = await res.json().catch(function () { return {}; });
    if (!res.ok) {
      if (res.status === 503 || (body && body.code === 'auth_disabled')) {
        markLegacy();
        return false;
      }
      if (res.status === 401) {
        // 只在“这个令牌仍是当前令牌”时清；并发的另一次 refresh 可能已经换新。
        if (state.auth.access === token) {
          clearAuth('expired');
          notifyAuthRequired('登录已失效，需要重新登录；世界状态保持不变。');
        }
      }
      return false;
    }
    return applyAuthPayload(body, state.auth.username);
  }

  async function probeAuth() {
    const hadToken = loadStoredAuth();
    if (hadToken) {
      // 读端点匿名可用；这里只确认带令牌的诊断结果，令牌真伪由 refresh 探针判定。
      const savedAccount = (function () {
        try {
          const raw = JSON.parse(localStorage.getItem('ws_player_account') || 'null');
          return raw && raw.account_id ? raw.account_id : 'u_demo';
        } catch (e) { return 'u_demo'; }
      })();
      try {
        await fetch('/api/player/state?account_id=' + encodeURIComponent(savedAccount), {
          headers: Object.assign({ Accept: 'application/json' }, authHeader()),
          cache: 'no-store'
        });
      } catch (e) { /* 上游暂时不可达时保留令牌，交给写操作再判 */ }
    }

    let probe = null;
    const probeToken = state.auth.access;
    try {
      probe = await fetch(AUTH_API.refresh, {
        method: 'POST',
        headers: Object.assign(
          { Accept: 'application/json' },
          probeToken ? { Authorization: 'Bearer ' + probeToken } : {}
        ),
        cache: 'no-store'
      });
    } catch (e) {
      // 网络不可达时保守放行：已有令牌按鉴权模式，没令牌按匿名模式。
      state.auth.mode = state.auth.access ? 'auth' : 'legacy';
      state.auth.probeDone = true;
      renderAuthUI();
      return;
    }
    if (probe.status === 404 || probe.status === 405 || probe.status === 501) {
      // 服务端没有鉴权端点：legacy 匿名模式，不强行要求登录。
      markLegacy();
      return;
    }
    const body = await probe.json().catch(function () { return {}; });
    if (!probe.ok) {
      if (probe.status === 503 || (body && body.code === 'auth_disabled')) {
        markLegacy();
        return;
      }
      state.auth.mode = 'auth';
      state.auth.probeDone = true;
      // 探针用的旧令牌已被并发的 refresh 换新时，不要误清有效令牌。
      if (hadToken && state.auth.access === probeToken) clearAuth('expired');
      renderAuthUI();
      return;
    }
    // 200：鉴权模式；探针本身完成一次轮换，立即原子替换本地令牌。
    state.auth.mode = 'auth';
    state.auth.probeDone = true;
    applyAuthPayload(body, state.auth.username);
    renderAuthUI();
  }

  function renderAuthNotice() {
    const notice = el('authNotice');
    const show = state.auth.mode === 'auth' && state.auth.required;
    notice.classList.toggle('hidden', !show);
    if (!show) return;
    el('authNoticeText').textContent = state.auth.access
      ? '登录凭证已过期，写操作会被拒绝；请重新登录后继续。'
      : '登录已失效，写操作需要重新登录；世界状态保持不变。';
    el('authNoticeBtn').textContent = state.auth.access ? '重新登录' : '登录/注册';
  }

  function renderAuthUI() {
    const btn = el('authBtn');
    if (!btn) return;
    btn.classList.remove('hidden');
    const line = el('entryAuthLine');
    if (state.auth.mode === 'legacy') {
      btn.textContent = '匿名模式';
      btn.disabled = true;
      btn.title = '服务端未启用鉴权：匿名也能正常游玩。';
      state.auth.required = false;
      if (line) line.classList.add('hidden');
    } else {
      btn.disabled = false;
      btn.title = state.auth.access ? '已登录，点击可退出登录' : '登录或注册';
      btn.textContent = state.auth.access
        ? ('已登录 · ' + (state.auth.username || '账号'))
        : (state.auth.required ? '需要重新登录' : '登录/注册');
      if (line) {
        line.classList.remove('hidden');
        el('entryAuthText').textContent = state.auth.access
          ? ('已登录 · ' + (state.auth.username || '账号') + '；令牌已保存在本机。')
          : (state.auth.required
            ? '登录已失效，需要重新登录；世界状态不会丢。'
            : '写操作需要登录；读世界和看推荐匿名可用。');
        el('entryAuthBtn').textContent = state.auth.access ? '账号 / 退出' : '登录 / 注册';
      }
    }
    renderAuthNotice();
  }

  function notifyAuthRequired(text) {
    state.auth.required = true;
    renderAuthUI();
    toast(text || '需要重新登录后才能继续。', true);
    recordError('鉴权', 'auth_required', text || '登录已失效，需要重新登录。');
  }

  function setAuthMode(mode) {
    const isRegister = mode === 'register';
    state.auth.uiMode = isRegister ? 'register' : 'login';
    el('authTabLogin').classList.toggle('active', !isRegister);
    el('authTabRegister').classList.toggle('active', isRegister);
    el('authTabLogin').setAttribute('aria-selected', String(!isRegister));
    el('authTabRegister').setAttribute('aria-selected', String(isRegister));
    el('authSubmit').textContent = isRegister ? '注册并登录' : '登录';
    el('authTitle').textContent = isRegister ? '注册账号' : '登录';
    el('authSub').textContent = isRegister
      ? '注册后令牌自动保存；口令不会写入本地存储。'
      : '写操作需要令牌；读世界和看推荐匿名可用。';
    el('authPassword').setAttribute('autocomplete', isRegister ? 'new-password' : 'current-password');
  }

  function openAuthModal(mode) {
    setAuthMode(mode || state.auth.uiMode || 'login');
    el('authError').classList.add('hidden');
    el('authUsername').value = state.auth.username || el('authUsername').value || '';
    el('authPassword').value = '';
    el('authLogout').classList.toggle('hidden', !state.auth.access);
    el('authModal').classList.remove('hidden');
    setTimeout(function () { el('authUsername').focus(); }, 0);
  }

  function closeAuthModal() {
    el('authModal').classList.add('hidden');
  }

  function showAuthError(text) {
    const error = el('authError');
    error.textContent = text;
    error.classList.remove('hidden');
  }

  async function submitAuth(ev) {
    if (ev) ev.preventDefault();
    const mode = state.auth.uiMode === 'register' ? 'register' : 'login';
    const username = el('authUsername').value.trim();
    const password = el('authPassword').value;
    el('authError').classList.add('hidden');
    if (!username) { showAuthError('请填写用户名。'); return; }
    if (!password) { showAuthError('请填写口令。'); return; }
    if (mode === 'register' && (password.length < 6 || password.length > 128)) {
      showAuthError('口令长度需为 6-128 位。');
      return;
    }
    const btn = el('authSubmit');
    btn.disabled = true;
    const old = btn.textContent;
    btn.textContent = '提交中…';
    try {
      let res = await postJSON(mode === 'register' ? AUTH_API.register : AUTH_API.login,
        { username: username, password: password });
      if (res.authDisabled || res.status === 404 || res.status === 405 || res.status === 501) {
        markLegacy();
        closeAuthModal();
        toast('服务端未启用鉴权：可直接匿名游玩。');
        return;
      }
      if (!res.httpOk || (res.data && res.data.ok === false)) {
        showAuthError(reasonOf(res.data, res.status).text);
        return;
      }
      let payload = res.data;
      if (mode === 'register') {
        // 真实契约：register 只返回 account_id/username/created，不返回令牌；
        // 注册成功后立刻用同一组凭据登录换令牌。
        const loginRes = await postJSON(AUTH_API.login, { username: username, password: password });
        if (!loginRes.httpOk || (loginRes.data && loginRes.data.ok === false)) {
          showAuthError('注册成功但登录失败：' + reasonOf(loginRes.data, loginRes.status).text);
          return;
        }
        payload = loginRes.data;
      }
      if (!applyAuthPayload(payload, username)) {
        showAuthError('登录响应缺少令牌；请重试。');
        return;
      }
      state.auth.username = state.auth.username || username;
      closeAuthModal();
      toast('登录成功：' + (state.auth.username || username));
      if (state.st) render(state.st);
    } catch (e) {
      showAuthError('登录请求失败：' + (e.message || e));
    } finally {
      btn.disabled = false;
      btn.textContent = old;
      setAuthMode(mode);
    }
  }

  async function logoutAuth() {
    const token = state.auth.access;
    if (token) {
      try {
        // 注销失败不影响本地退出；令牌到期后自然失效。
        await fetch(AUTH_API.logout, {
          method: 'POST',
          headers: { Accept: 'application/json', Authorization: 'Bearer ' + token },
          cache: 'no-store'
        });
      } catch (e) { /* ignore */ }
    }
    clearAuth('logout');
    state.auth.required = false;
    closeAuthModal();
    toast('已退出登录；匿名仍可浏览世界。');
  }


  function requireAuthForWrite() {
    if (state.auth.mode === 'auth' && !state.auth.access) {
      notifyAuthRequired('这项操作需要登录后才能执行。');
      openAuthModal('login');
      return false;
    }
    return true;
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

  /* ------------------------------------------------------------- 新手引导 */
  function guideMap() {
    try {
      const raw = JSON.parse(localStorage.getItem(GUIDE_KEY) || '{}');
      return raw && typeof raw === 'object' ? raw : {};
    } catch (e) {
      return {};
    }
  }

  function guideRecord(account) {
    const map = guideMap();
    return (account && map[account]) || null;
  }

  function writeGuideRecord(account, status, step) {
    if (!account) return;
    const map = guideMap();
    map[account] = { status: status, step: step == null ? 1 : step, updated: Date.now() };
    try { localStorage.setItem(GUIDE_KEY, JSON.stringify(map)); } catch (e) { /* ignore */ }
  }

  function activeQuestForGuide() {
    const board = (state.st && state.st.quest_board) || {};
    const rows = (board.active || []).concat(board.rows || []);
    return rows.find(function (q) {
      return q && (q.accepted_by_me || q.status === 'accepted');
    }) || null;
  }

  function locationLabel(id) {
    if (!id) return '目标地点';
    const loc = (state.st && state.st.location) || {};
    const exits = loc.exits || [];
    const hit = exits.find(function (x) { return x.id === id; });
    return (hit && hit.name) || id;
  }

  function guideTarget(step) {
    if (step === 1) {
      return { node: el('todayNext'), title: '① 先看「先做这个」',
        why: '它会指出今天最值得做的一件事，省得你在 12 行信息里自己挑。' };
    }
    if (step === 2) {
      const node = document.querySelector('.quest button[data-quest][data-can-accept-here="1"]') ||
        document.querySelector('.quest button[data-quest]');
      return { node: node, title: '② 接一条委托',
        why: '委托是前期最稳的收入；接单后任务会留在任务板，随时可以交付。' };
    }
    const quest = activeQuestForGuide();
    if (step === 3) {
      if (!quest) {
        return { node: null, title: '③ 移动到委托人处',
          why: '先接一条委托，这一步会指向委托人所在的出口。' };
      }
      const locId = ((state.st && state.st.location) || {}).id || '';
      const need = quest.target_location || quest.giver_location || '';
      if (need && need !== locId) {
        const node = document.querySelector('#exits button[data-exit-id="' + need + '"]') ||
          document.querySelector('#exits button[data-exit-id]');
        return { node: node, title: '③ 移动到委托人处',
          why: '「' + (quest.name || '这条委托') + '」在' + locationLabel(need) +
            '；点出口逐段走过去，到了才能交付。' };
      }
      return { node: document.querySelector('.quest button[data-deliver]'), title: '③ 已到委托地点',
        why: '委托人就在当前地点，不用再移动；下一步直接在任务卡上交付。' };
    }
    return {
      node: document.querySelector('.quest button[data-deliver]'),
      title: '④ 交付并看到资金变化',
      why: '交付后报酬会从委托人钱包转到你身上；留意顶部「钱」的数值变化。'
    };
  }

  function ensureBlockOpen(node) {
    if (!node) return;
    const block = node.closest ? node.closest('.block.foldable.collapsed') : null;
    if (block) {
      const btn = block.querySelector('.fold-btn');
      toggleFold(block, btn, false);
    }
  }

  function positionGuide(node) {
    const layer = el('guideLayer');
    const card = el('guideCard');
    const spot = el('guideSpotlight');
    if (layer.classList.contains('hidden')) return;
    if (!node) {
      spot.classList.add('hidden');
      const cw = card.offsetWidth || 300;
      card.style.left = Math.max(12, (window.innerWidth - cw) / 2) + 'px';
      card.style.top = Math.max(12, (window.innerHeight - card.offsetHeight) / 2) + 'px';
      return;
    }
    const r = node.getBoundingClientRect();
    if (!r.width || !r.height || r.bottom < 0 || r.top > window.innerHeight) {
      spot.classList.add('hidden');
      return;
    }
    spot.classList.remove('hidden');
    spot.style.left = Math.round(r.left - 4) + 'px';
    spot.style.top = Math.round(r.top - 4) + 'px';
    spot.style.width = Math.round(r.width + 8) + 'px';
    spot.style.height = Math.round(r.height + 8) + 'px';
    const cw = Math.min(320, window.innerWidth - 24);
    card.style.width = cw + 'px';
    const ch = card.offsetHeight || 180;
    const gap = 12;
    let top = r.bottom + gap;
    if (top + ch > window.innerHeight - 10) top = r.top - ch - gap;
    top = Math.max(10, Math.min(window.innerHeight - ch - 10, top));
    let left = r.left + r.width / 2 - cw / 2;
    left = Math.max(10, Math.min(window.innerWidth - cw - 10, left));
    card.style.left = Math.round(left) + 'px';
    card.style.top = Math.round(top) + 'px';
  }

  function guideNextLabel(step) {
    if (step >= 4) return '完成引导';
    if (step === 2) return '我已接单，下一步';
    if (step === 3) return '我已到达，下一步';
    return '下一步';
  }

  function renderGuide() {
    if (!state.guide.active) { hideGuide(); return; }
    const ctx = guideTarget(state.guide.step);
    ensureBlockOpen(ctx.node);
    if (ctx.node && ctx.node.scrollIntoView) {
      ctx.node.scrollIntoView({ block: 'center', inline: 'nearest' });
    }
    el('guideStep').textContent = String(state.guide.step) + ' / 4';
    el('guideTitle').textContent = ctx.title;
    el('guideWhy').textContent = ctx.why;
    el('guideNext').textContent = guideNextLabel(state.guide.step);
    el('guideLayer').classList.remove('hidden');
    requestAnimationFrame(function () { positionGuide(ctx.node); });
  }

  function hideGuide() {
    const layer = el('guideLayer');
    if (layer) layer.classList.add('hidden');
    const spot = el('guideSpotlight');
    if (spot) spot.classList.add('hidden');
  }

  function repositionGuide() {
    if (!state.guide.active) return;
    const ctx = guideTarget(state.guide.step);
    ensureBlockOpen(ctx.node);
    el('guideStep').textContent = String(state.guide.step) + ' / 4';
    el('guideTitle').textContent = ctx.title;
    el('guideWhy').textContent = ctx.why;
    el('guideNext').textContent = guideNextLabel(state.guide.step);
    requestAnimationFrame(function () { positionGuide(ctx.node); });
  }

  function setGuideStep(step) {
    state.guide.step = Math.max(1, Math.min(4, step));
    state.guide.active = true;
    state.guide.status = 'active';
    if (state.guide.accountId) writeGuideRecord(state.guide.accountId, 'active', state.guide.step);
    renderGuide();
  }

  function maybeStartGuide() {
    const account = state.session && state.session.accountId;
    if (!account || !state.st) return;
    const rec = guideRecord(account);
    if (rec && (rec.status === 'skipped' || rec.status === 'completed')) return;
    state.guide.accountId = account;
    state.guide.active = true;
    state.guide.status = 'active';
    state.guide.step = rec && rec.status === 'active' && rec.step ? rec.step : 1;
    writeGuideRecord(account, 'active', state.guide.step);
    renderGuide();
  }

  function guideAdvanceOn(action) {
    if (!state.guide.active) return;
    if (action === 'deliver') { completeGuide(); return; }
    if (action === 'accept' && state.guide.step <= 2) { setGuideStep(3); return; }
    if (action === 'move' && state.guide.step <= 3) {
      const quest = activeQuestForGuide();
      const locId = ((state.st && state.st.location) || {}).id || '';
      const need = quest && (quest.target_location || quest.giver_location);
      if (!need || need === locId) setGuideStep(4);
      else renderGuide(); // 还没到，继续指向下一个出口
    }
  }

  function completeGuide() {
    if (state.guide.accountId) writeGuideRecord(state.guide.accountId, 'completed', 4);
    state.guide.active = false;
    state.guide.status = 'completed';
    hideGuide();
    toast('引导完成：你已经走完「看推荐 → 接单 → 移动 → 交付」。');
  }

  function skipGuide() {
    if (state.guide.accountId) writeGuideRecord(state.guide.accountId, 'skipped', state.guide.step);
    state.guide.active = false;
    state.guide.status = 'skipped';
    hideGuide();
    toast('已跳过新手引导；下次进入不会再自动出现。');
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
      if (res.authRequired) {
        const box = el('entryError');
        box.textContent = '进入世界是写操作，需要登录后才能执行；请先在右上角登录/注册，再点「进入世界」。';
        box.classList.remove('hidden');
        btn.textContent = '进入世界';
        openAuthModal('login');
        return;
      }
      if (res.status === 409 || result.ok === false || res.status === 0) {
        const r = reasonOf(result, res.status);
        const box = el('entryError');
        box.textContent = r.text;
        box.classList.remove('hidden');
        recordError('进入世界', r.code, r.text);
        btn.textContent = '进入世界';
        return;
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
    maybeStartGuide();
  }

  /* ------------------------------------------------------------- 场景渲染 */
  function render(st) {
    if (!st || !st.player) return;
    renderStatus(st);
    renderNotice(st);
    renderPlace(st);
    renderNpcs(st);
    renderQuests(st);
    renderToday(st);
    renderGrowth(st);
    renderTalkTarget();
    renderLog();
    if (state.guide.active) repositionGuide();
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
      btn.setAttribute('data-exit-id', exit.id || '');
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
    if (!requireAuthForWrite()) return;
    const beforeTick = state.st ? Number(state.st.tick) : 0;
    state.busy = true;
    el('exitHint').style.color = '';
    try {
      const res = await API.action(state.session.accountId, 'move', { to: exit.id }, beforeTick);
      const data = res.data || {};
      if (res.authRequired || res.status === 401) return;
      if (res.status === 409 || data.ok === false || res.status === 0) {
        const r = notifyError('移动 ' + exit.name, data, res.status);
        el('exitHint').style.color = '#9b3b2f';
        el('exitHint').textContent = r.text;
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
      guideAdvanceOn('move');
    } catch (err) {
      toast('移动请求失败：' + (err.message || err), true);
      recordError('移动 ' + exit.name, '', '网络请求失败：' + (err.message || err));
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
    if (!requireAuthForWrite()) return;
    const beforeTick = state.st ? Number(state.st.tick) : 0;
    const input = el('talkInput');
    const send = el('talkSend');
    send.disabled = true;
    try {
      const res = await API.action(account, 'talk', { target: npc.id, message: message }, beforeTick);
      const data = res.data || {};
      const box = el('talkResult');
      box.classList.remove('hidden', 'ok', 'bad');
      if (res.authRequired || res.status === 401) return;
      if (res.status === 409 || data.ok === false || res.status === 0) {
        const r = notifyError('对 ' + npc.name + ' 说话', data, res.status);
        box.classList.add('bad');
        box.innerHTML = '<b>被拒：</b>' + escapeHtml(r.text) +
          (r.code ? '<br><span class="tiny">原因码：<code>' + escapeHtml(r.code) + '</code></span>' : '');
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
  function renderToday(st) {
    const t = st.today || {};
    el('todayTick').textContent = t.tick != null
      ? ('tick ' + t.tick + ' · ' + clockOf(t.tick).label)
      : '—';
    el('todayNext').innerHTML = '<span>先做这个</span>' + escapeHtml(t.next_action || '—');
    const list = el('todayList');
    list.innerHTML = '';
    const quests = t.quests || [];
    const skills = t.skills || [];
    const npcs = t.npcs || [];
    const biz = t.business || [];
    const groups = [
      { label: '可接委托', rows: quests.map(function (q) {
        return q.name + ' · ' + q.reward_money + ' 文 · ' + q.giver_name;
      }) },
      { label: '可学技能', rows: skills.map(function (s) {
        if (!s.next_at) return s.skill + ' 已到顶';
        return s.skill + ' ' + s.current + ' → ' + s.next_at + '（约 ' + s.hours_to_next + ' 小时）';
      }) },
      { label: '可拜访 NPC', rows: npcs.map(function (n) { return n.name + '（' + n.role + '）'; }) },
      { label: '可买产业', rows: biz.map(function (b) {
        return b.shop_type + ' ' + b.cost + ' 文' + (b.affordable ? ' · 买得起' : ' · 还差本钱') +
          (b.blocked ? ' · 商会护盘期间暂不可开' : '');
      }) },
    ];
    groups.forEach(function (g) {
      if (!g.rows.length) return;
      const box = document.createElement('div');
      box.className = 'today-group';
      box.innerHTML = '<div class="today-label">' + escapeHtml(g.label) + '</div>' +
        g.rows.slice(0, 3).map(function (line) {
          return '<div class="today-row">' + escapeHtml(line) + '</div>';
        }).join('') +
        (g.rows.length > 3 ? '<div class="today-more">还有 ' + (g.rows.length - 3) + ' 项</div>' : '');
      if (g.label === '可学技能' && skills[0] && skills[0].hours_to_next) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'primary today-act';
        btn.textContent = '练 2 小时';
        btn.addEventListener('click', function () {
          doGrowthAction('cultivate', { skill: skills[0].skill, hours: 2 }, '练手艺');
        });
        box.appendChild(btn);
      }
      if (g.label === '可买产业') {
        biz.forEach(function (b) {
          if (!b.affordable || b.blocked) return;
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'ghost today-act';
          btn.textContent = '开' + (b.shop_type === 'stall' ? '摊' : b.shop_type === 'storefront' ? '铺' : '分号') +
            '（' + b.cost + ' 文）';
          btn.addEventListener('click', function () {
            doGrowthAction('open_shop', { shop_type: b.shop_type }, '置产');
          });
          box.appendChild(btn);
        });
      }
      list.appendChild(box);
    });
    if (!list.children.length) {
      list.innerHTML = '<div class="log-empty">此刻没有可做的事：先休息或换地点。</div>';
    }
  }

  function renderGrowth(st) {
    const g = st.growth || {};
    const rows = [
      { key: '声望', data: g.reputation },
      { key: '技能', data: g.skill },
      { key: '资产', data: g.assets },
    ];
    const list = el('growthList');
    list.innerHTML = rows.filter(function (r) { return r.data; }).map(function (r) {
      const d = r.data;
      const pct = Math.max(0, Math.min(100, Math.round(Number(d.progress || 0) * 100)));
      const next = d.next_at == null ? '已到顶' :
        ('下一级 ' + d.next_name + ' · 还需 ' + (Number(d.next_at) - Number(d.value)).toFixed(3).replace(/\.?0+$/, ''));
      return '<div class="growth-row">' +
        '<div class="growth-top"><b>' + escapeHtml(r.key) + '</b>' +
        '<span>Lv' + escapeHtml(d.level) + ' ' + escapeHtml(d.name) + '</span></div>' +
        '<div class="growth-bar"><i style="width:' + pct + '%"></i></div>' +
        '<div class="growth-hint">' + escapeHtml(next) + (d.next_unlock ? ' · ' + escapeHtml(d.next_unlock) : '') + '</div>' +
        '</div>';
    }).join('') || '<div class="log-empty">进入世界后显示成长线。</div>';
  }

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
      card.setAttribute('data-quest-card', q.id || '');
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
          ? '<button type="button" class="primary" data-deliver="' + escapeHtml(q.id) + '">交付</button>'
          : '<button type="button" class="' + (q.can_accept_here ? 'primary' : 'ghost') +
            '" data-quest="' + escapeHtml(q.id) + '" data-can-accept-here="' +
            (q.can_accept_here ? '1' : '0') + '">去接</button>') + '</div>';
      const btn = card.querySelector('button[data-quest]');
      if (btn) btn.addEventListener('click', function () { doAccept(q); });
      const deliver = card.querySelector('button[data-deliver]');
      if (deliver) deliver.addEventListener('click', function () { doDeliver(q); });
      list.appendChild(card);
    });
  }

  async function doAccept(quest) {
    const account = state.session && state.session.accountId;
    if (!account) { toast('先进入世界，才能接任务。', true); showEntry(true); return; }
    if (!requireAuthForWrite()) return;
    const beforeTick = state.st ? Number(state.st.tick) : 0;
    const res = await API.action(account, 'quest_accept', { quest: quest.id }, beforeTick)
      .catch(function (err) {
        return { status: 0, data: { ok: false, message: '接任务请求失败：' + (err.message || err) } };
      });
    const data = res.data || {};
    if (res.authRequired || res.status === 401) return;
    if (res.status === 409 || data.ok === false || res.status === 0) {
      notifyError('接任务「' + quest.name + '」', data, res.status);
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
    guideAdvanceOn('accept');
  }

  async function doDeliver(quest) {
    const account = state.session && state.session.accountId;
    if (!account) { toast('先进入世界，才能交付。', true); showEntry(true); return; }
    if (!requireAuthForWrite()) return;
    const beforeTick = state.st ? Number(state.st.tick) : 0;
    const beforeMoney = Number(((state.st || {}).player || {}).wallet ? state.st.player.wallet.money : 0) || 0;
    const res = await API.action(account, 'quest_deliver', { quest: quest.id }, beforeTick)
      .catch(function (err) {
        return { status: 0, data: { ok: false, message: '交付请求失败：' + (err.message || err) } };
      });
    const data = res.data || {};
    if (res.authRequired || res.status === 401) return;
    if (res.status === 409 || data.ok === false || res.status === 0) {
      notifyError('交付「' + quest.name + '」', data, res.status);
      return;
    }
    if (data.status === 'proposed' || data.readonly) {
      toast('只读模式：已提交交付提案（不落地）。');
      pushLog('proposal', '交付提案：' + quest.name, data.intent_id || '');
      return;
    }
    const st = await loadState(account);
    const afterMoney = Number(((st || {}).player || {}).wallet ? st.player.wallet.money : 0) || 0;
    const delta = afterMoney - beforeMoney;
    const moneyText = delta === 0
      ? ('钱包 ' + afterMoney + ' 文（本次无现金变化）')
      : ('钱包 ' + beforeMoney + ' → ' + afterMoney + ' 文（' + (delta > 0 ? '+' : '') + delta + '）');
    toast('交付成功：' + quest.name + ' · ' + moneyText);
    pushLog('ok', '交付「' + quest.name + '」' + (delta !== 0 ? ' · ' + (delta > 0 ? '+' : '') + delta + ' 文' : ''),
      'event_seq ' + (data.event_seq == null ? '?' : data.event_seq));
    guideAdvanceOn('deliver');
  }

  async function doGrowthAction(action, payload, label) {
    const account = state.session && state.session.accountId;
    if (!account) { toast('先进入世界。', true); showEntry(true); return; }
    if (!requireAuthForWrite()) return;
    const beforeTick = state.st ? Number(state.st.tick) : 0;
    const res = await API.action(account, action, payload, beforeTick)
      .catch(function (err) {
        return { status: 0, data: { ok: false, message: '请求失败：' + (err.message || err) } };
      });
    const data = res.data || {};
    if (res.authRequired || res.status === 401) return;
    if (res.status === 409 || data.ok === false || res.status === 0) {
      notifyError(label, data, res.status);
      return;
    }
    if (data.status === 'proposed' || data.readonly) {
      toast('只读模式：已提交' + label + '提案（不落地）。');
      pushLog('proposal', label + '提案', data.intent_id || '');
      return;
    }
    const st = await loadState(account);
    const g = (st && st.growth) || {};
    toast('已' + label + ' · ' + (g.skill ? ('技能 ' + g.skill.level + ' 级 ') : '') +
      (g.assets ? ('资产 ' + g.assets.level + ' 级') : ''));
    pushLog('ok', label, 'event_seq ' + (data.event_seq == null ? '?' : data.event_seq));
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
      // 上游暂时不可达：回进入页，不伪造位置；若是鉴权问题，保留已记住的账号。
      if (err && err.authRequired) return false;
    }
    try { localStorage.removeItem('ws_player_account'); } catch (e) { /* ignore */ }
    return false;
  }

  /* ------------------------------------------------------------- 折叠分组 */
  const mobileMq = window.matchMedia('(max-width: 768px)');

  function toggleFold(block, btn, collapsed) {
    if (!block) return;
    const next = collapsed == null ? !block.classList.contains('collapsed') : !!collapsed;
    block.classList.toggle('collapsed', next);
    if (btn) {
      btn.setAttribute('aria-expanded', String(!next));
      btn.title = next ? '展开这一组' : '收起这一组';
      btn.setAttribute('aria-label', (next ? '展开' : '收起') + '分组');
      btn.querySelector('span').textContent = next ? '▸' : '▾';
    }
  }

  function initFolds() {
    if (state.ui.foldsReady) return;
    state.ui.foldsReady = true;
    const blocks = Array.prototype.slice.call(document.querySelectorAll('.panel.side > .block'));
    blocks.forEach(function (block, index) {
      const head = block.querySelector(':scope > .block-head');
      if (!head) return;
      const body = document.createElement('div');
      body.className = 'block-body';
      while (head.nextSibling) body.appendChild(head.nextSibling);
      block.appendChild(body);
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'fold-btn';
      btn.setAttribute('aria-expanded', 'true');
      btn.setAttribute('aria-label', '收起分组');
      btn.title = '收起这一组';
      btn.innerHTML = '<span aria-hidden="true">▾</span>';
      btn.addEventListener('click', function (ev) {
        ev.stopPropagation();
        toggleFold(block, btn);
        repositionGuide();
      });
      head.appendChild(btn);
      block.classList.add('foldable');
      block.setAttribute('data-fold-index', String(index));
    });
    const right = Array.prototype.slice.call(document.querySelectorAll('.panel.right > .block'));
    state.ui.growthBlock = right[1] || null;
    if (state.ui.growthBlock) state.ui.growthBlock.classList.add('fold-growth');
    syncFoldDefaults();
  }

  function syncFoldDefaults() {
    if (!state.ui.foldsReady) return;
    const mobile = mobileMq.matches;
    if (state.ui.foldMode === mobile) return;
    state.ui.foldMode = mobile;
    if (mobile) {
      if (state.ui.growthBlock) {
        const btn = state.ui.growthBlock.querySelector('.fold-btn');
        toggleFold(state.ui.growthBlock, btn, true);
      }
    } else {
      document.querySelectorAll('.block.foldable').forEach(function (block) {
        toggleFold(block, block.querySelector('.fold-btn'), false);
      });
    }
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
      state.guide.active = false;
      hideGuide();
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

    el('authBtn').addEventListener('click', function () {
      if (state.auth.mode === 'legacy') return;
      openAuthModal(state.auth.access ? 'login' : 'login');
      el('authLogout').classList.toggle('hidden', !state.auth.access);
    });
    el('entryAuthBtn').addEventListener('click', function () { openAuthModal('login'); });
    el('authClose').addEventListener('click', closeAuthModal);
    el('authNoticeBtn').addEventListener('click', function () { openAuthModal('login'); });
    el('authTabLogin').addEventListener('click', function () { setAuthMode('login'); });
    el('authTabRegister').addEventListener('click', function () { setAuthMode('register'); });
    el('authForm').addEventListener('submit', submitAuth);
    el('authLogout').addEventListener('click', logoutAuth);
    el('authModal').addEventListener('click', function (ev) {
      if (ev.target === el('authModal')) closeAuthModal();
    });

    el('guideNext').addEventListener('click', function () {
      if (state.guide.step >= 4) { completeGuide(); return; }
      setGuideStep(state.guide.step + 1);
    });
    el('guideSkip').addEventListener('click', skipGuide);

    el('errorToggle').addEventListener('click', function () {
      const list = el('errorList');
      const open = list.classList.contains('hidden');
      list.classList.toggle('hidden', !open);
      el('errorToggle').setAttribute('aria-expanded', String(open));
    });

    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape') {
        closeNpcModal();
        closeAuthModal();
        el('searchResults').classList.add('hidden');
      }
    });
    window.addEventListener('resize', function () {
      syncFoldDefaults();
      repositionGuide();
    });
    window.addEventListener('scroll', function () {
      if (state.guide.active) repositionGuide();
    }, true);
    mobileMq.addEventListener ? mobileMq.addEventListener('change', syncFoldDefaults) : mobileMq.addListener(syncFoldDefaults);
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
    initFolds();
    initEntryForm();
    renderAuthUI();
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
      loadStoredAuth();  // 先载入本地令牌：restore 的首次读必须带 Authorization，否则服务模式只拿到公共摘要
      const results = await Promise.all([restoreAccount(), probeAuth()]);
      const restored = results[0];
      renderNotice(state.st);
      if (restored) {
        showEntry(false);
      } else {
        showEntry(true);
      }
      if (state.st) render(state.st);
      if (restored) maybeStartGuide();
      clearInterval(state.pollTimer);
      state.pollTimer = setInterval(async function () {
        await refreshHealth();
        if (el('entryOverlay').classList.contains('hidden') && state.session) {
          try {
            await loadState(state.session.accountId);
          } catch (err) {
            if (!err.authRequired) markDegraded(err);
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

  if (CHECK_MODE) {
    // 只给 tools/ui_check.py 用的只读/断言入口；正常页面不暴露。
    window.__XKX = {
      reasonText: reasonText,
      reasonOf: reasonOf,
      reasons: Object.keys(REASON_TEXT),
      showError: function (code, serverText) {
        return notifyError('测试错误', { reason: code, reason_text: serverText || '' }, 409);
      },
      apiAction: function (verb, args) {
        const acct = state.session && state.session.accountId;
        return API.action(acct, verb, args || {}, state.st ? state.st.tick : null);
      },
      readState: function (account) {
        const acct = account == null ? (state.session && state.session.accountId) : account;
        return API.state(acct);
      },
      authState: function () {
        return {
          mode: state.auth.mode,
          hasAccess: !!state.auth.access,
          required: state.auth.required,
          username: state.auth.username,
          expiresAt: state.auth.expiresAt
        };
      },
      guideState: function () {
        return {
          active: state.guide.active,
          step: state.guide.step,
          status: state.guide.status,
          accountId: state.guide.accountId
        };
      },
      guideTargetInfo: function () {
        const ctx = guideTarget(state.guide.step);
        if (!ctx.node) return null;
        const r = ctx.node.getBoundingClientRect();
        return {
          title: ctx.title,
          text: ctx.node.textContent.trim().slice(0, 80),
          rect: { left: r.left, top: r.top, width: r.width, height: r.height,
                  right: r.right, bottom: r.bottom },
          tag: ctx.node.tagName,
          id: ctx.node.id || '',
          quest: ctx.node.getAttribute('data-quest') || '',
          deliver: ctx.node.getAttribute('data-deliver') || '',
          exitId: ctx.node.getAttribute('data-exit-id') || '',
          disabled: !!ctx.node.disabled
        };
      }
    };
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
