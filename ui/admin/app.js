'use strict';

/* XKX 管理控制台 · 前端
 * 所有数据来自真实管理 API（/api/admin/*）；没有 mock、没有本地伪造的成功态。
 * 不在任何位置渲染 API key 明文：页面只使用后端返回的 key_masked / 元数据。
 */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const SS_API = 'xk_admin_api_base';
const SS_SESSION = 'xk_admin_session';

const WORLD_META = {
  tick_minutes: { label: '世界 tick 间隔', unit: '分钟', kind: 'number', hint: '主服务当前无运行期读取点' },
  ambient_speed: { label: '环境演算速度', unit: '倍', kind: 'number', hint: '玩家 API 启动时读取 XKX_AMBIENT_SPEED' },
  ambient_cpu: { label: '环境 CPU 上限', unit: '%', kind: 'number', hint: '玩家 API 启动时读取 XKX_AMBIENT_CPU' },
  player_cap: { label: '玩家上限', unit: '人', kind: 'number', hint: '主服务当前无运行期读取点' },
  starting_money: { label: '初始资金', unit: '文', kind: 'number', hint: '主服务当前无运行期读取点' },
  quest_floor_enabled: { label: '任务下限开关', unit: '', kind: 'bool', hint: '主服务当前无运行期读取点' },
};

const CAP_META = [
  { id: 'dialogue', label: '对话生成', note: 'talk 动作的真实对话队列' },
  { id: 'strategy', label: '策略决策', note: 'StrategyClient 真实模式' },
  { id: 'self_goal', label: '自我目标', note: '确定性规则台账' },
  { id: 'night_batch', label: '夜间批处理', note: 'decision 槽 / night batch' },
  { id: 'quest_gen', label: '任务生成', note: '规则生成' },
  { id: 'persona_gen', label: '人格生成', note: '静态文本' },
  { id: 'validation', label: '校验', note: '确定性 schema 校验' },
];

const ACTION_META = {
  'provider.put': { label: '中转站写入', cls: 'info' },
  'provider.delete': { label: '中转站删除', cls: 'bad' },
  'secret.put': { label: '保存 key', cls: 'warn' },
  'provider.key_rotate': { label: '轮换 key', cls: 'warn' },
  'settings.update': { label: '参数/路由写入', cls: 'info' },
  'settings.put': { label: '参数写入', cls: 'info' },
  'llm.killswitch': { label: 'LLM 总闸', cls: 'bad' },
};

const ERR_TEXT = {
  unauthorized: '未登录或会话失效',
  session_expired: '会话已过期',
  invalid_admin_token: '管理口令不正确',
  admin_token_missing: '服务端未配置 XKX_ADMIN_TOKEN',
  too_many_attempts: '登录失败次数过多，已锁定 15 分钟',
  admin_ip_not_allowed: '来源 IP 不在 XKX_ADMIN_ALLOW_IPS 白名单',
  master_key_missing: '未配置主密钥 XKX_SECRET_KEY：key 未写入（503 master_key_missing）',
  master_key_invalid_base64: '主密钥不是合法 base64（503）',
  master_key_invalid_length: '主密钥长度不是 32 字节（503）',
  provider_key_missing: '该 provider 没有可用 key，无法测试',
  provider_not_found: 'provider 不存在',
  network: '网络不可达（error=network）',
  llm_killswitch: 'LLM 总闸已关闭（llm_killswitch）',
  llm_not_configured: '未配置可用的 LLM provider',
  not_found: '端点/对象不存在（404）',
  non_json_response: '响应不是 JSON',
  enabled_not_bool: 'enabled 必须是布尔值',
};

const state = {
  apiBase: '',
  sessionToken: '',
  sessionExpires: 0,
  auth: false,
  busy: false,
  backendDown: false,
  backendError: '',
  lastOkAt: null,
  health: null,
  settings: null,
  providers: [],
  capabilities: null,
  routingView: null,
  overview: null,
  audit: [],
  auditLimit: 100,
  endpointErrors: {},
  unimplemented: {},
  paramDraft: new Map(),
  routeDraft: {},
  testing: {},
  testResults: {},
  modalOpen: false,
};

let autoTimer = null;

/* ---------------------------------------------------------------- utils */
function esc(value) {
  return String(value === null || value === undefined ? '' : value).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);
}

const SENSITIVE_KEY = /^(api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|passwd|authorization|credential)$/i;

function redactText(text) {
  return String(text === null || text === undefined ? '' : text)
    .replace(/\b(sk-[A-Za-z0-9._-]{6,})/g, 'sk-****')
    .replace(/\b(Bearer\s+)[^\s"']+/gi, 'Bearer ****');
}

function maskDeep(value, depth = 0) {
  if (depth > 8) return '…';
  if (Array.isArray(value)) return value.map((item) => maskDeep(item, depth + 1));
  if (value && typeof value === 'object') {
    const out = {};
    for (const [key, item] of Object.entries(value)) {
      out[key] = SENSITIVE_KEY.test(key)
        ? (item === null || item === undefined || item === '' ? '' : '****（已掩码）')
        : maskDeep(item, depth + 1);
    }
    return out;
  }
  if (typeof value === 'string') return redactText(value).slice(0, 2000);
  return value;
}

function fmtVal(value) {
  if (value === null || value === undefined) return 'null';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : String(Math.round(value * 100) / 100);
  return String(value);
}

function fmtIso(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return formatDate(date);
}

function fmtEpoch(value) {
  if (value === null || value === undefined || value === '') return '—';
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return '—';
  return formatDate(new Date(seconds * 1000));
}

function formatDate(date) {
  return date.toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  });
}

function nowClock() {
  return formatDate(new Date()).slice(11);
}

function fmtBytes(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number < 0) return '—';
  if (number < 1024) return `${number} B`;
  if (number < 1024 * 1024) return `${(number / 1024).toFixed(1)} KB`;
  if (number < 1024 * 1024 * 1024) return `${(number / 1024 / 1024).toFixed(2)} MB`;
  return `${(number / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function describeError(status, data) {
  const code = String((data && data.code) || '');
  const detail = redactText((data && data.detail) || '');
  const mapped = ERR_TEXT[code] || (code ? `错误码 ${code}` : '请求失败');
  let text = `HTTP ${status} · ${mapped}`;
  if (detail && detail !== code) text += ` · ${detail}`;
  return text;
}

function errorHtml(status, data) {
  return `<b>${esc(describeError(status, data))}</b>`;
}

function normalizeBase(raw) {
  let text = String(raw || '').trim();
  if (!text) return '';
  if (!/^http(s)?:\/\//i.test(text)) text = 'http://' + text;
  return text.replace(/\/+$/, '');
}

function defaultApiBase() {
  const query = new URLSearchParams(location.search).get('api');
  if (query) return normalizeBase(query);
  const saved = sessionStorage.getItem(SS_API);
  if (saved) return normalizeBase(saved);
  if (location.protocol === 'file:') return 'http://127.0.0.1:8031';
  return location.origin;
}

function apiUrl(path) {
  return normalizeBase(state.apiBase) + path;
}

function toast(message, kind = 'info', ttl = 6000) {
  const node = document.createElement('div');
  node.className = `toast ${kind}`;
  node.textContent = message;
  $('#toastRoot').appendChild(node);
  setTimeout(() => node.remove(), ttl);
}

/* ---------------------------------------------------------------- API */
class BackendError extends Error {
  constructor(message, path) {
    super(message);
    this.name = 'BackendError';
    this.path = path;
  }
}

function backendHint() {
  if (location.protocol === 'file:') {
    return `后端不可达：浏览器以 file:// 打开会拦截到 ${state.apiBase} 的跨域请求（管理服务未提供 CORS）。请运行 python3 ui/admin/server.py，然后访问 http://127.0.0.1:8039/ 。`;
  }
  return `后端不可达：${state.apiBase} 没有响应（服务未启动、端口不对或被网络拦截）。`;
}

async function api(path, opts = {}) {
  const {
    method = 'GET', body = null, auth = true, timeout = 20000, ep = '', mark404 = false,
  } = opts;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const headers = {};
  if (body !== null) headers['Content-Type'] = 'application/json';
  if (auth && state.sessionToken) headers['Authorization'] = `Bearer ${state.sessionToken}`;

  let response;
  try {
    response = await fetch(apiUrl(path), {
      method,
      headers,
      body: body === null ? undefined : JSON.stringify(body),
      signal: controller.signal,
      cache: 'no-store',
    });
  } catch (error) {
    if (error && error.name === 'AbortError') {
      throw new BackendError(`请求超时（${Math.round(timeout / 1000)}s）：${path}`, path);
    }
    throw new BackendError(backendHint(), path);
  } finally {
    clearTimeout(timer);
  }

  state.backendDown = false;
  state.backendError = '';

  const raw = await response.text();
  let data = {};
  try {
    data = raw ? JSON.parse(raw) : {};
  } catch (_) {
    data = { ok: false, code: 'non_json_response', detail: raw.slice(0, 300) };
  }

  if (response.status === 401 && auth) onUnauthorized();
  if (mark404 && response.status === 404 && ep) state.unimplemented[ep] = path;
  return { status: response.status, ok: response.ok, data };
}

function onUnauthorized() {
  if (!state.auth && !state.sessionToken) return;
  state.auth = false;
  state.sessionToken = '';
  state.sessionExpires = 0;
  sessionStorage.removeItem(SS_SESSION);
  state.settings = null;
  state.providers = [];
  state.capabilities = null;
  state.overview = null;
  state.audit = [];
  state.paramDraft.clear();
  state.routeDraft = {};
  showGate('会话失效，请重新输入管理口令。', 'bad');
}

function saveSession(token, expiresAt) {
  state.sessionToken = token;
  state.sessionExpires = Number(expiresAt || 0);
  sessionStorage.setItem(SS_SESSION, JSON.stringify({ token, expires_at: state.sessionExpires }));
}

function readSession() {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(SS_SESSION) || 'null');
    if (!parsed || !parsed.token) return null;
    if (Number(parsed.expires_at || 0) * 1000 <= Date.now()) return null;
    return parsed;
  } catch (_) {
    return null;
  }
}

/* ---------------------------------------------------------------- loaders */
function noteFailure(ep, status, data) {
  state.endpointErrors[ep] = { status, code: (data && data.code) || '', at: Date.now() };
}

function noteSuccess(ep) {
  delete state.endpointErrors[ep];
  delete state.unimplemented[ep];
  state.lastOkAt = Date.now();
}

async function guarded(ep, fn) {
  try {
    await fn();
  } catch (error) {
    if (error instanceof BackendError) {
      state.backendDown = true;
      state.backendError = error.message;
      noteFailure(ep, 0, { code: 'backend_unreachable', detail: error.message });
    } else {
      throw error;
    }
  }
}

async function loadHealth() {
  await guarded('health.get', async () => {
    const result = await api('/api/admin/health', { auth: false, ep: 'health.get', mark404: true, timeout: 8000 });
    if (result.status === 200) {
      state.health = result.data;
      noteSuccess('health.get');
    } else {
      noteFailure('health.get', result.status, result.data);
    }
  });
}

async function loadSettings() {
  let ok = false;
  await guarded('settings.get', async () => {
    const result = await api('/api/admin/settings', { ep: 'settings.get', mark404: true, timeout: 15000 });
    if (result.status === 200) {
      state.settings = result.data;
      state.capabilities = result.data.capabilities || null;
      noteSuccess('settings.get');
      ok = true;
    } else {
      noteFailure('settings.get', result.status, result.data);
    }
  });
  return ok;
}

async function loadProviders() {
  await guarded('providers.get', async () => {
    const result = await api('/api/admin/providers', { ep: 'providers.get', mark404: true, timeout: 15000 });
    if (result.status === 200) {
      state.providers = Array.isArray(result.data.providers) ? result.data.providers : [];
      state.routingView = result.data.routing_view || null;
      state.capabilities = result.data.capabilities || state.capabilities;
      noteSuccess('providers.get');
    } else {
      noteFailure('providers.get', result.status, result.data);
    }
  });
}

async function loadOverview() {
  await guarded('overview.get', async () => {
    const result = await api('/api/admin/overview', { ep: 'overview.get', mark404: true, timeout: 15000 });
    if (result.status === 200) {
      state.overview = result.data;
      noteSuccess('overview.get');
    } else {
      noteFailure('overview.get', result.status, result.data);
    }
  });
}

async function loadAudit() {
  await guarded('audit.get', async () => {
    const result = await api(`/api/admin/audit?limit=${encodeURIComponent(state.auditLimit)}`, {
      ep: 'audit.get', mark404: true, timeout: 15000,
    });
    if (result.status === 200) {
      state.audit = Array.isArray(result.data.records) ? result.data.records : [];
      noteSuccess('audit.get');
    } else {
      noteFailure('audit.get', result.status, result.data);
    }
  });
}

async function loadAll() {
  await Promise.all([loadHealth(), loadSettings(), loadProviders(), loadOverview(), loadAudit()]);
  renderAll();
}

/* ---------------------------------------------------------------- gate */
function showGate(message = '', kind = '') {
  $('#loginGate').classList.remove('hidden');
  $('#gateStatus').className = `gate-status ${kind}`;
  $('#gateStatus').textContent = message;
  $('#apiBaseInput').value = state.apiBase || defaultApiBase();
  $('#adminTokenInput').value = '';
  $('#shell').hidden = true;
}

function hideGate() {
  $('#loginGate').classList.add('hidden');
  $('#shell').hidden = false;
}

function setGateStatus(message, kind = '') {
  const node = $('#gateStatus');
  node.className = `gate-status ${kind}`;
  node.textContent = message;
}

function setGateBusy(busy) {
  $('#connectBtn').disabled = busy;
  $('#apiBaseInput').disabled = busy;
  $('#adminTokenInput').disabled = busy;
  $('#connectBtn').textContent = busy ? '连接中…' : '连接';
}

async function connect() {
  const base = normalizeBase($('#apiBaseInput').value) || defaultApiBase();
  const token = $('#adminTokenInput').value;
  $('#adminTokenInput').value = ''; // 立刻清除，不把口令留在 DOM 里
  state.apiBase = base;
  sessionStorage.setItem(SS_API, base);
  setGateBusy(true);
  setGateStatus('正在探测 /api/admin/health…', '');
  try {
    const health = await api('/api/admin/health', { auth: false, timeout: 8000 });
    if (health.status !== 200) {
      setGateStatus(`健康探测失败：${describeError(health.status, health.data)}`, 'bad');
      return;
    }
    state.health = health.data;
    state.backendDown = false;
    const master = health.data.master_key ? '主密钥已配置' : '主密钥缺失（保存 key 会 503）';
    $('#gateProbe').textContent = `admin ${health.data.admin_version || '—'} · providers ${health.data.providers ?? '—'} · ${master}`;

    const login = await api('/api/admin/login', { method: 'POST', auth: false, body: { token }, timeout: 10000 });
    if (login.status !== 200) {
      setGateStatus(`登录失败：${describeError(login.status, login.data)}`, 'bad');
      return;
    }
    saveSession(login.data.token, login.data.expires_at);
    state.auth = true;
    hideGate();
    setGateStatus('', '');
    await loadAll();
    toast('已连接管理服务', 'ok');
  } catch (error) {
    state.backendDown = true;
    state.backendError = error.message || String(error);
    setGateStatus(state.backendError, 'bad');
    $('#gateProbe').textContent = '后端不可达';
  } finally {
    setGateBusy(false);
  }
}

function logout() {
  sessionStorage.removeItem(SS_SESSION);
  state.sessionToken = '';
  state.auth = false;
  state.settings = null;
  state.providers = [];
  state.overview = null;
  state.audit = [];
  state.capabilities = null;
  state.paramDraft.clear();
  state.routeDraft = {};
  state.testResults = {};
  showGate('已断开连接。', '');
}

/* ---------------------------------------------------------------- render */
function renderAll() {
  renderHeader();
  renderBanner();
  renderOverview();
  renderParams();
  renderRelays();
  renderCaps();
  renderAudit();
  renderRail();
}

function renderHeader() {
  const chip = $('#backendChip');
  if (state.backendDown) {
    chip.className = 'chip bad';
    chip.innerHTML = '<i>后端</i><b>不可达</b>';
  } else if (state.auth) {
    chip.className = 'chip ok';
    chip.innerHTML = '<i>后端</i><b>已连接</b>';
  } else {
    chip.className = 'chip';
    chip.innerHTML = '<i>后端</i><b>未连接</b>';
  }

  const version = $('#versionChip');
  const settingsVersion = state.settings && state.settings.version !== undefined ? state.settings.version : null;
  if (state.health) {
    version.className = 'chip';
    version.innerHTML = `<i>版本</i><b>admin ${esc(state.health.admin_version || '—')} · 配置 v${esc(settingsVersion === null ? '—' : settingsVersion)}</b>`;
  } else {
    version.className = 'chip';
    version.innerHTML = '<i>版本</i><b>—</b>';
  }

  const master = $('#masterKeyChip');
  if (state.health) {
    const has = state.health.master_key === true;
    master.className = `chip ${has ? '' : 'bad'}`;
    master.innerHTML = `<i>主密钥</i><b>${has ? '已配置' : '缺失 · key 保存会 503'}</b>`;
  } else {
    master.className = 'chip';
    master.innerHTML = '<i>主密钥</i><b>—</b>';
  }

  const sync = $('#lastSyncChip');
  if (state.lastOkAt) {
    sync.className = 'chip clock';
    sync.innerHTML = `<i>数据时间</i><b>${esc(formatDate(new Date(state.lastOkAt)).slice(11))}${state.backendDown ? ' · 已过期' : ''}</b>`;
  } else {
    sync.className = 'chip clock';
    sync.innerHTML = '<i>数据时间</i><b>—</b>';
  }
  $('#railApiBase').textContent = apiUrl('/api/admin/*');
}

function renderBanner() {
  const node = $('#globalBanner');
  const blocks = [];
  if (state.backendDown) {
    const stale = state.lastOkAt
      ? `当前展示的是 ${formatDate(new Date(state.lastOkAt))} 最后一次成功读取的数据，写操作已禁用。`
      : '尚未读取到任何真实数据。';
    blocks.push(`<div class="banner bad"><b>后端不可达</b>：${esc(state.backendError || state.apiBase)}<br>${esc(stale)}</div>`);
  }
  const unimplemented = Object.entries(state.unimplemented);
  if (unimplemented.length) {
    const items = unimplemented.map(([ep, path]) => `<li><code>${esc(ep)}</code> → <code>${esc(path)}</code>：<span class="badge unimpl">未实现</span> 相关区块已禁用</li>`).join('');
    blocks.push(`<div class="banner"><b>后端未实现以下端点</b><ul>${items}</ul></div>`);
  }
  const failures = Object.entries(state.endpointErrors).filter(([ep]) => !state.unimplemented[ep] && ep !== 'health.get');
  if (failures.length && !state.backendDown) {
    const items = failures.map(([ep, info]) => `<li><code>${esc(ep)}</code>：${esc(info.code || 'error')}（HTTP ${esc(info.status || '—')}）</li>`).join('');
    blocks.push(`<div class="banner bad"><b>部分接口调用失败</b><ul>${items}</ul></div>`);
  }
  if (!blocks.length) {
    node.className = 'banner hidden';
    node.innerHTML = '';
    return;
  }
  node.className = 'banner';
  node.style.margin = '10px 18px 0';
  node.style.border = '0';
  node.style.padding = '0';
  node.innerHTML = blocks.join('');
}

function renderRail() {
  const worldState = (state.settings && state.settings.world_state) || {};
  const pending = Object.values(worldState).filter((item) => item && item.restart_required && item.applied !== true).length;
  const caps = (state.settings && state.settings.capabilities) || state.capabilities || {};
  const degraded = Object.values(caps).filter((item) => item && item.status === 'degraded').length;
  const stubs = Object.values(caps).filter((item) => item && item.status === 'stub').length;
  $('#navOverview').textContent = state.overview
    ? `tick ${(state.overview.world && state.overview.world.tick) ?? '—'}`
    : '—';
  $('#navParams').textContent = state.settings ? (pending ? `${pending} 待生效` : '全部生效') : '—';
  $('#navRelays').textContent = state.providers.length ? `${state.providers.length} 条` : '—';
  $('#navCaps').textContent = degraded ? `${degraded} 异常` : (stubs ? `${stubs} stub` : '—');
  $('#navAudit').textContent = state.audit.length ? `${state.audit.length}` : '—';
}

/* ---------------------------------------------------------------- overview */
function statCard(label, value, sub, nullLike = false) {
  const shown = value === null || value === undefined || value === '' ? '—' : value;
  return `<div class="stat"><span>${esc(label)}</span><b class="${nullLike || shown === '—' ? 'nullv' : ''}">${esc(shown)}</b><em>${esc(sub || '')}</em></div>`;
}

function renderOverview() {
  const health = $('#overviewHealth');
  if (state.overview) {
    const h = state.overview.health || {};
    health.textContent = `admin ${h.admin || '—'} · ambient ${h.ambient_state || 'unknown'}`;
  } else {
    health.textContent = '未读取';
  }

  const grid = $('#statGrid');
  if (!state.overview) {
    grid.innerHTML = state.backendDown
      ? statCard('后端不可达', '—', esc(state.backendError))
      : statCard('概览未读取', '—', '等待 /api/admin/overview');
  } else {
    const ov = state.overview;
    const world = ov.world || {};
    const llm = ov.llm || {};
    const db = ov.db_bytes || {};
    grid.innerHTML = [
      statCard('世界 tick', world.tick, 'events/tick 表最大值'),
      statCard('在线玩家', world.online_players, 'sessions active/connected'),
      statCard('累计事件', world.events_total, 'events.sqlite'),
      statCard('在场 NPC', world.active_npcs, 'ambient_status.json'),
      statCard('LLM 调用', llm.calls, 'ambient 计数'),
      statCard('LLM 花费', llm.cost_usd === null || llm.cost_usd === undefined ? '—' : `$${llm.cost_usd}`, llm.cost_source || 'unavailable'),
      statCard('service.sqlite', fmtBytes(db.service_sqlite), '管理配置库'),
      statCard('events.sqlite', fmtBytes(db.events_sqlite), '世界事件库'),
      statCard('最近备份', ov.backup && ov.backup.last_at ? fmtIso(ov.backup.last_at) : '—', (ov.backup && ov.backup.dir) || ''),
      statCard('管理服务运行', `${ov.uptime_s ?? '—'} s`, `admin ${ov.admin_version || '—'}`),
    ].join('');
  }

  const runtimeBody = $('#runtimeBody');
  if (!state.overview) {
    runtimeBody.innerHTML = '<div class="empty">未读取</div>';
  } else {
    const ov = state.overview;
    const rt = ov.runtime || {};
    const configured = rt.configured || {};
    const rows = [];
    const worlds = rt.world_state || {};
    const speed = worlds.ambient_speed || {};
    const cpu = worlds.ambient_cpu || {};
    const bothNumbers = (a, b) => a !== null && a !== undefined && b !== null && b !== undefined
      && Number.isFinite(Number(a)) && Number.isFinite(Number(b));
    const speedMismatch = bothNumbers(speed.effective_value, configured.ambient_speed)
      && Number(speed.effective_value) !== Number(configured.ambient_speed);
    const cpuMismatch = bothNumbers(cpu.effective_value, configured.ambient_cpu)
      && Number(cpu.effective_value) !== Number(configured.ambient_cpu);
    rows.push(['ambient_speed 配置 / 运行', `${fmtVal(configured.ambient_speed)} / ${fmtVal(speed.effective_value)}`, speedMismatch ? '不一致，重启后生效' : '']);
    rows.push(['ambient_cpu 配置 / 运行', `${fmtVal(configured.ambient_cpu)}% / ${cpu.effective_value === null || cpu.effective_value === undefined ? 'null' : fmtVal(cpu.effective_value) + '%'}`, cpuMismatch ? '不一致，重启后生效' : '']);
    rows.push(['玩家进程 pid', fmtVal(rt.player_pid), '重启判定依据']);
    rows.push(['配置版本', fmtVal(rt.settings_version), 'MAX(admin_settings.version)']);
    rows.push(['ambient 状态', fmtVal((ov.health || {}).ambient_state), ov.health && ov.health.updated_at ? `观测 ${fmtIso(ov.health.updated_at)}` : '']);
    runtimeBody.innerHTML = rows.map(([k, v, sub]) => `
      <div class="kv-row"><span>${esc(k)}</span><b>${esc(v)}${sub ? `<div class="hint tiny">${esc(sub)}</div>` : ''}</b></div>
    `).join('') + `<div class="hint tiny" style="margin-top:6px">${esc(rt.note || '配置面与运行面可能不同。')}</div>`;
  }

  const ks = $('#ksState');
  const ksBtn = $('#ksBtn');
  const enabled = state.settings && state.settings.llm ? state.settings.llm.global_enabled : undefined;
  if (typeof enabled !== 'boolean') {
    ks.textContent = '未知（未读取 llm.global_enabled）';
    ks.className = 'ks-state';
    ksBtn.disabled = true;
    ksBtn.textContent = '无法操作';
    ksBtn.classList.remove('arm');
  } else if (enabled) {
    ks.textContent = 'LLM 调用：允许';
    ks.className = 'ks-state off';
    ksBtn.disabled = writesDisabled() || writeEpUnavailable('killswitch.post');
    ksBtn.textContent = '关闭总闸（阻断新调用）';
    ksBtn.classList.remove('arm');
  } else {
    ks.textContent = 'LLM 调用：已阻断';
    ks.className = 'ks-state on';
    ksBtn.disabled = writesDisabled() || writeEpUnavailable('killswitch.post');
    ksBtn.textContent = '打开总闸（恢复调用）';
    ksBtn.classList.add('arm');
  }
}

/* ---------------------------------------------------------------- params */
function writesDisabled() {
  return !state.auth || state.backendDown;
}

function isUnimplemented(ep) {
  return Boolean(state.unimplemented[ep]);
}

function writeEpUnavailable(ep) {
  return isUnimplemented(ep);
}

function renderParams() {
  const wrap = $('#paramTableWrap');
  if (isUnimplemented('settings.get')) {
    wrap.innerHTML = `<div class="result-box bad"><b>未实现</b>：后端没有返回 <code>/api/admin/settings</code>（404）。参数区块已禁用。</div>`;
    $('#saveAllParams').disabled = true;
    return;
  }
  if (!state.settings) {
    wrap.innerHTML = `<div class="empty">${state.backendDown ? '后端不可达：参数未读取，不使用缓存值' : '未读取到 settings'}</div>`;
    $('#saveAllParams').disabled = true;
    return;
  }

  const settings = state.settings;
  const worldState = settings.world_state || {};
  const worldConfig = settings.world || {};
  $('#envBridgePath').textContent = settings.env_bridge || 'runs/<run>/admin_env.sh';

  const rows = Object.keys(WORLD_META).map((key) => {
    const meta = WORLD_META[key];
    const item = worldState[key] || {};
    const configValue = worldConfig[key];
    const effective = item.effective_value;
    const applied = item.applied === true;
    const restart = item.restart_required === true;
    const source = String(item.source || 'none');
    const draftValue = state.paramDraft.has(key) ? state.paramDraft.get(key) : configValue;

    let inputHtml;
    if (meta.kind === 'bool') {
      inputHtml = `<select data-param="${esc(key)}" ${writesDisabled() ? 'disabled' : ''}>
        <option value="true" ${draftValue === true ? 'selected' : ''}>true</option>
        <option value="false" ${draftValue === false ? 'selected' : ''}>false</option>
      </select>`;
    } else {
      inputHtml = `<div class="param-input-row">
        <input type="number" min="0" step="any" data-param="${esc(key)}" value="${esc(draftValue)}" ${writesDisabled() ? 'disabled' : ''}>
        <span class="unit">${esc(meta.unit)}</span>
      </div>`;
    }

    let effHtml;
    if (effective === null || effective === undefined) {
      effHtml = `<div class="eff-value nullv">null</div>
        <div class="hint">${source === 'none' ? '无运行期读取点' : '未观测到运行值'}</div>`;
    } else {
      effHtml = `<div class="eff-value">${esc(fmtVal(effective))}<span class="unit"> ${esc(meta.unit)}</span></div>
        <div class="hint">${source === 'ambient_status.json' ? '来自 ambient_status.json' : esc(source)}</div>`;
    }

    const statusBadges = [];
    if (restart) statusBadges.push('<span class="badge restart">重启后生效</span>');
    if (applied) {
      statusBadges.push('<span class="badge ok">已生效 · applied=true</span>');
    } else if (effective === null || effective === undefined) {
      statusBadges.push('<span class="badge muted">无法观测生效值</span>');
    } else {
      statusBadges.push('<span class="badge warn">未生效 · applied=false</span>');
    }

    return `<tr data-row="${esc(key)}">
      <td>
        <div class="param-name">${esc(meta.label)}<code>world.${esc(key)}</code></div>
        <div class="hint tiny">${esc(meta.hint)}</div>
      </td>
      <td>
        ${inputHtml}
        <span class="dirty-chip" data-dirty-for="${esc(key)}" hidden>有未保存修改</span>
      </td>
      <td>${effHtml}</td>
      <td><div class="badges">${statusBadges.join('')}</div></td>
      <td>
        <code>${esc(source)}</code>
        <div class="hint">${esc(item.note || meta.hint)}</div>
        <div class="hint tiny">观测时间 ${esc(item.observed_at ? fmtIso(item.observed_at) : 'null')}</div>
      </td>
      <td>
        <button class="ghost small" data-act="save-param" data-key="${esc(key)}" ${writesDisabled() || writeEpUnavailable('settings.put') ? 'disabled' : ''}>保存</button>
        <button class="plain micro" data-act="reset-param" data-key="${esc(key)}">撤销</button>
      </td>
    </tr>`;
  }).join('');

  wrap.innerHTML = `<table class="grid params-table">
    <colgroup>
      <col class="c-param"><col class="c-value"><col class="c-eff"><col class="c-status"><col class="c-source"><col class="c-act">
    </colgroup>
    <thead><tr>
      <th>参数</th><th>期望值（写入配置）</th><th>当前运行值</th><th>生效状态</th><th>来源 / 说明</th><th></th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;

  $('#saveAllParams').disabled = state.paramDraft.size === 0 || writesDisabled() || writeEpUnavailable('settings.put');
  updateParamDirty();
}

function updateParamDirty() {
  $('#saveAllParams').disabled = state.paramDraft.size === 0 || writesDisabled() || writeEpUnavailable('settings.put');
  for (const key of Object.keys(WORLD_META)) {
    const chip = $(`[data-dirty-for="${key}"]`);
    const input = $(`[data-param="${key}"]`);
    if (!chip || !input || !state.settings) continue;
    const original = (state.settings.world || {})[key];
    let changed;
    if (WORLD_META[key].kind === 'bool') {
      changed = (input.value === 'true') !== (original === true);
    } else {
      changed = input.value === '' || Number(input.value) !== Number(original);
    }
    chip.hidden = !changed;
  }
}

function collectParamValue(key) {
  const meta = WORLD_META[key];
  const input = $(`[data-param="${key}"]`);
  if (!input) return { ok: false, reason: '找不到输入框' };
  if (meta.kind === 'bool') return { ok: true, value: input.value === 'true' };
  const raw = input.value.trim();
  if (raw === '') return { ok: false, reason: '值不能为空' };
  const value = Number(raw);
  if (!Number.isFinite(value)) return { ok: false, reason: '不是有效数字' };
  if (value < 0) return { ok: false, reason: '不能为负数' };
  return { ok: true, value };
}

async function saveParams(keys) {
  if (state.busy) { toast('上一个写操作还在进行中', 'warn'); return; }
  if (writesDisabled()) {
    toast('后端不可达或未登录：写操作已禁用', 'bad');
    return;
  }
  const patch = {};
  const invalid = [];
  for (const key of keys) {
    const parsed = collectParamValue(key);
    if (!parsed.ok) {
      invalid.push(`${key}：${parsed.reason}`);
      continue;
    }
    patch[key] = parsed.value;
  }
  if (invalid.length) {
    showParamResult('bad', `<b>未提交</b><ul class="result-list">${invalid.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`);
    return;
  }
  if (!Object.keys(patch).length) {
    toast('没有可保存的修改', 'warn');
    return;
  }

  setBusy(true);
  try {
    const result = await api('/api/admin/settings', {
      method: 'PUT', body: { world: patch }, ep: 'settings.put', timeout: 20000,
    });
    if (result.status !== 200) {
      if ([404, 405, 501].includes(result.status)) {
        state.unimplemented['settings.put'] = '/api/admin/settings';
        renderAll();
        showParamResult('bad', `<b>未实现</b>：后端没有实现 <code>PUT /api/admin/settings</code>（HTTP ${result.status}）。参数写入按钮已禁用。`);
        return;
      }
      noteFailure('settings.put', result.status, result.data);
      showParamResult('bad', `<b>写入失败</b>：${esc(describeError(result.status, result.data))}<br>配置值没有变化；页面不会显示成功。`);
      return;
    }
    const payload = result.data || {};
    if (payload.settings) state.settings = payload.settings;
    state.paramDraft.clear();
    renderAll();
    showParamResult('warn', buildParamSaveSummary(payload, Object.keys(patch)));
    await Promise.all([loadOverview(), loadAudit()]);
    renderAll();
  } catch (error) {
    state.backendDown = true;
    state.backendError = error.message || String(error);
    renderAll();
    showParamResult('bad', `<b>后端不可达</b>：${esc(state.backendError)}。参数没有写入。`);
  } finally {
    setBusy(false);
  }
}

function buildParamSaveSummary(payload, requestedKeys) {
  const worldState = payload.world_state || (payload.settings && payload.settings.world_state) || {};
  const changed = Array.isArray(payload.changed) ? payload.changed : [];
  const requiresRestart = Array.isArray(payload.requires_restart) ? payload.requires_restart : [];
  const keys = changed.length ? changed : (requestedKeys || []);
  const items = keys.map((key) => {
    const item = worldState[key] || {};
    const effective = item.effective_value;
    const applied = item.applied === true;
    let text;
    if (effective === null || effective === undefined) {
      text = item.source === 'none'
        ? '无运行期读取点，无法观测实际生效值'
        : '未观测到运行期实际值';
    } else if (applied) {
      text = '运行值与配置值一致（applied=true）';
    } else {
      text = `未生效：运行期仍是 ${fmtVal(effective)}（applied=false）`;
    }
    return `<li><code>${esc(key)}</code>：期望 ${esc(fmtVal(item.value))} / 运行值 ${esc(effective === null || effective === undefined ? 'null' : fmtVal(effective))} · ${esc(text)}${item.restart_required ? ' · <b>重启后生效</b>' : ''}</li>`;
  });
  const restartNote = requiresRestart.length
    ? `<br><b>需要重启才生效</b>：${esc(requiresRestart.join(', '))}。请先 <code>source ${esc((state.settings && state.settings.env_bridge) || 'runs/<run>/admin_env.sh')}</code> 再重启玩家 API。`
    : '';
  const header = changed.length
    ? `<b>配置已写入（${changed.length} 项）</b>。下面给出每个参数的配置值与当前运行值，未生效的项会保持 <code>applied=false</code>。`
    : '<b>配置未变化（0 项写入）</b>。以下为该参数的当前双值状态；未生效的项仍是 <code>applied=false</code>。';
  return `${header}${restartNote}
    <ul class="result-list">${items.join('')}</ul>`;
}

function showParamResult(kind, html) {
  const node = $('#paramResult');
  node.className = `result-box ${kind}`;
  node.innerHTML = html;
}

/* ---------------------------------------------------------------- providers */
function providerGroupList() {
  const groups = new Map();
  for (const provider of state.providers) {
    const base = String(provider.base_url || '').trim();
    const key = base || '（base_url 为空）';
    if (!groups.has(key)) groups.set(key, { base_url: base, items: [] });
    groups.get(key).items.push(provider);
  }
  return Array.from(groups.values());
}

function relayKeySummary(group) {
  const total = group.items.length;
  const withKey = group.items.filter((item) => item.has_key);
  if (!withKey.length) return { label: '未配置 key', cls: 'bad' };
  if (withKey.length < total) return { label: `部分已配置（${withKey.length}/${total}）`, cls: 'warn' };
  const readable = withKey.filter((item) => item.key_readable && item.key_masked);
  if (!readable.length) return { label: 'key 不可读（主密钥缺失或变更）', cls: 'bad' };
  const masks = new Set(readable.map((item) => item.key_masked));
  if (masks.size === 1) return { label: `key ${[...masks][0]}`, cls: 'ok' };
  return { label: `多份 key（${masks.size} 种掩码）`, cls: 'warn' };
}

function renderRelays() {
  const list = $('#providerList');
  $('#addRelayBtn').disabled = writesDisabled() || isUnimplemented('providers.get') || isUnimplemented('providers.put');
  if (isUnimplemented('providers.get')) {
    list.innerHTML = '<div class="result-box bad"><b>未实现</b>：<code>/api/admin/providers</code> 返回 404，中转站区块已禁用。</div>';
    return;
  }
  if (!state.providers.length) {
    list.innerHTML = state.backendDown
      ? '<div class="empty">后端不可达：未读取到中转站，不使用缓存或 mock 配置。</div>'
      : (state.auth ? '<div class="empty">还没有中转站。点右上角「新增中转站」创建第一条 provider 记录。</div>' : '<div class="empty">未读取</div>');
    return;
  }

  const groups = providerGroupList();
  list.innerHTML = groups.map((group) => {
    const summary = relayKeySummary(group);
    const rows = group.items.map((provider) => {
      const testing = state.testing[provider.id];
      const result = state.testResults[provider.id];
      let testHtml = '<span class="hint">—</span>';
      if (testing) {
        testHtml = '<span class="hint">测试中…</span>';
      } else if (result) {
        const cls = result.ok ? 'ok' : 'bad';
        const parts = [];
        parts.push(`<span class="badge ${cls}">${result.ok ? 'ok=true' : 'ok=false'}</span>`);
        if (result.latency_ms !== null && result.latency_ms !== undefined) parts.push(`<span>${esc(result.latency_ms)} ms</span>`);
        if (result.model) parts.push(`<span class="hint">model=${esc(result.model)}</span>`);
        if (!result.ok && result.error) parts.push(`<span class="hint">error=${esc(result.error)}</span>`);
        testHtml = `<div class="test-badge">${parts.join('')}</div>`;
      }
      const keyText = provider.has_key
        ? (provider.key_readable && provider.key_masked ? provider.key_masked : '不可读')
        : '未配置';
      const keySub = provider.has_key
        ? `长度 ${esc(provider.key_length || '—')} · 最后使用 ${esc(provider.key_last_used_at ? fmtIso(provider.key_last_used_at) : '从未')}`
        : '测试会返回 provider_key_missing';
      return `<tr data-provider="${esc(provider.id)}">
        <td>
          <b class="key-masked">${esc(provider.id)}</b>
          <div class="cell-note">${esc(provider.name || provider.id)}</div>
        </td>
        <td>
          <div>${esc(provider.model || '—')}</div>
          <div class="cell-note">timeout ${esc(provider.timeout_s ?? '—')}s · thinking=${esc(provider.thinking ? 'true' : 'false')}</div>
        </td>
        <td>
          <div class="key-masked">${esc(keyText)}</div>
          <div class="cell-note">${esc(keySub)}</div>
        </td>
        <td>${testHtml}</td>
        <td>
          <div class="relay-actions">
            <button class="ghost micro" data-act="provider-test" data-id="${esc(provider.id)}" ${writesDisabled() ? 'disabled' : ''}>测试连通</button>
            <button class="plain micro" data-act="provider-delete" data-id="${esc(provider.id)}" ${writesDisabled() ? 'disabled' : ''}>删除</button>
          </div>
        </td>
      </tr>`;
    }).join('');

    return `<article class="relay" data-base="${esc(group.base_url)}">
      <header class="relay-head">
        <div class="relay-title">
          <h3>${esc(group.base_url || '（未配置 base_url）')}</h3>
          <p>${group.items.length} 个模型条目 · 鉴权 Bearer（后端固定）</p>
        </div>
        <span class="chip ${summary.cls}"><i>key</i><b>${esc(summary.label)}</b></span>
        <div class="relay-actions">
          <button class="ghost small" data-act="relay-edit" data-base="${esc(group.base_url)}" ${writesDisabled() || writeEpUnavailable('providers.put') ? 'disabled' : ''}>编辑中转站</button>
          <button class="ghost small" data-act="relay-key" data-base="${esc(group.base_url)}" ${writesDisabled() || writeEpUnavailable('providers.put') ? 'disabled' : ''}>设置 / 轮换 key</button>
          <button class="danger small" data-act="relay-delete" data-base="${esc(group.base_url)}" ${writesDisabled() ? 'disabled' : ''}>删除中转站</button>
        </div>
      </header>
      <div class="table-wrap">
        <table class="grid">
          <colgroup><col style="width:19%"><col style="width:22%"><col style="width:23%"><col style="width:18%"><col style="width:18%"></colgroup>
          <thead><tr><th>Provider ID</th><th>模型</th><th>key（仅掩码）</th><th>最近一次测试</th><th>操作</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </article>`;
  }).join('');
}

function findGroup(baseUrl) {
  return providerGroupList().find((group) => group.base_url === baseUrl) || null;
}

function setBusy(busy) {
  state.busy = busy;
  $('#refreshBtn').disabled = busy;
}

async function providerTest(providerId) {
  if (writesDisabled()) return;
  state.testing[providerId] = true;
  renderRelays();
  try {
    const result = await api(`/api/admin/providers/${encodeURIComponent(providerId)}/test`, {
      method: 'POST', ep: 'providers.test', timeout: 45000,
    });
    if (result.status === 200) {
      state.testResults[providerId] = Object.assign({ at: Date.now() }, result.data);
    } else {
      state.testResults[providerId] = {
        ok: false, latency_ms: null, model: '', error: describeError(result.status, result.data), at: Date.now(),
      };
    }
  } catch (error) {
    state.backendDown = true;
    state.backendError = error.message || String(error);
    state.testResults[providerId] = { ok: false, latency_ms: null, model: '', error: '后端不可达', at: Date.now() };
  } finally {
    delete state.testing[providerId];
    renderRelays();
  }
}

function validateModelRows(rows, baseUrl) {
  const problems = [];
  if (!baseUrl) problems.push('base_url 不能为空');
  if (!rows.length) problems.push('至少需要一个模型条目');
  const seen = new Set();
  for (const row of rows) {
    if (!/^[a-z0-9][a-z0-9_-]{0,39}$/.test(row.id)) problems.push(`provider id 不合法：${row.id || '(空)'}（需小写字母/数字开头，最多 40 字符）`);
    if (!String(row.model).trim()) problems.push(`${row.id || '(空 id)'}：模型不能为空`);
    if (seen.has(row.id)) problems.push(`provider id 重复：${row.id}`);
    seen.add(row.id);
  }
  return problems;
}

function openRelayEditor(group) {
  const rows = group
    ? group.items.map((item) => ({
      id: item.id,
      model: item.model || '',
      timeout_s: String(item.timeout_s ?? 30),
      thinking: Boolean(item.thinking),
    }))
    : [{ id: '', model: '', timeout_s: '30', thinking: false }];
  const title = group ? '编辑中转站' : '新增中转站';
  const subtitle = group
    ? `${group.items.length} 个模型条目；同一个 base_url 在列表里聚合显示。`
    : '每个模型条目会成为一条 provider 记录；同 base_url 自动聚合显示。';

  const bodyHtml = `
    <div class="form-grid">
      <label class="field span2"><span>base_url（中转站地址）</span>
        <input id="relayBaseUrl" class="mono" type="text" spellcheck="false" placeholder="https://relay.example.com/v1" value="${esc(group ? group.base_url : '')}">
      </label>
      <div class="span2 modal-readonly">鉴权方式：<b>Bearer</b>（主服务固定使用 <code>Authorization: Bearer &lt;key&gt;</code>；后端 provider 契约没有 <code>auth_mode</code> 字段，不可切换）</div>
      <div class="span2">
        <div class="hint" style="margin-bottom:5px">可用模型列表</div>
        <div class="model-head"><span>provider id</span><span>模型</span><span>timeout_s</span><span>thinking</span><span></span></div>
        <div class="model-rows" id="modelRows"></div>
        <div style="margin-top:7px"><button class="ghost small" type="button" id="addModelRow">+ 添加模型</button></div>
      </div>
      <label class="field span2"><span>API key（可选；本输入框不会回填任何已有值）</span>
        <input id="relayApiKey" type="password" autocomplete="new-password" spellcheck="false" placeholder="${group ? '留空表示不修改 key' : '留空表示暂不配置 key'}">
        <span class="hint tiny">填写后会分别写入本中转站每个模型条目的密文；只保存密文，页面之后只显示掩码。无主密钥时整条写入返回 503 master_key_missing。</span>
      </label>
    </div>
    <div id="relaySaveResult" class="result-box hidden"></div>
    <div class="modal-foot">
      <span class="modal-note">${group ? '保存时会新增/更新列出的条目；被移除的条目会同时删除其密文与路由引用。' : '新增按 provider id 逐条写入。'}</span>
      <button class="ghost" type="button" data-modal-close>取消</button>
      <button class="primary" type="button" id="relaySaveBtn">保存</button>
    </div>`;

  const { wrap, close } = pushModal(bodyHtml, { title, subtitle, width: '760px' });
  const modelRows = $(`#modelRows`, wrap);

  const rowHtml = (row) => `<div class="model-row">
    <input class="mono" data-model-field="id" type="text" spellcheck="false" placeholder="relay-a-model1" value="${esc(row.id)}">
    <input data-model-field="model" type="text" spellcheck="false" placeholder="model-name" value="${esc(row.model)}">
    <input data-model-field="timeout_s" type="number" min="1" step="1" value="${esc(row.timeout_s)}">
    <label style="display:flex;align-items:center;gap:4px;font-size:11px;color:var(--muted)"><input data-model-field="thinking" type="checkbox" ${row.thinking ? 'checked' : ''}> 开启</label>
    <button class="rm" type="button" title="移除该模型">×</button>
  </div>`;

  const bindRemove = (node) => {
    const button = $('.rm', node);
    if (button) button.onclick = () => {
      if (modelRows.children.length <= 1) {
        toast('至少保留一个模型条目', 'warn');
        return;
      }
      node.remove();
    };
  };

  rows.forEach((row) => {
    const holder = document.createElement('div');
    holder.innerHTML = rowHtml(row);
    const node = holder.firstElementChild;
    modelRows.appendChild(node);
    bindRemove(node);
  });

  $(`#addModelRow`, wrap).onclick = () => {
    const holder = document.createElement('div');
    holder.innerHTML = rowHtml({ id: '', model: '', timeout_s: '30', thinking: false });
    const node = holder.firstElementChild;
    modelRows.appendChild(node);
    bindRemove(node);
  };

  const saveBtn = $(`#relaySaveBtn`, wrap);
  const resultNode = $(`#relaySaveResult`, wrap);
  saveBtn.onclick = async () => {
    if (writesDisabled()) { toast('后端不可达或未登录：写操作已禁用', 'bad'); return; }
    const baseUrl = $(`#relayBaseUrl`, wrap).value.trim();
    const modelNodes = Array.from(modelRows.children);
    const collected = modelNodes.map((node) => ({
      id: $(`[data-model-field="id"]`, node).value.trim(),
      model: $(`[data-model-field="model"]`, node).value.trim(),
      timeout_s: Number($(`[data-model-field="timeout_s"]`, node).value || 30),
      thinking: $(`[data-model-field="thinking"]`, node).checked,
    }));
    const problems = validateModelRows(collected, baseUrl);
    if (problems.length) {
      resultNode.className = 'result-box bad';
      resultNode.innerHTML = `<b>未提交</b><ul class="result-list">${problems.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`;
      return;
    }

    const keyInput = $(`#relayApiKey`, wrap);
    const apiKey = keyInput.value;
    keyInput.value = ''; // 读取后立刻清空输入框

    const existingIds = group ? group.items.map((item) => item.id) : [];
    const keepIds = new Set(collected.map((row) => row.id));
    const removed = existingIds.filter((id) => !keepIds.has(id));
    if (removed.length) {
      const ok = await confirmAction({
        title: '删除已移除的模型条目？',
        subtitle: `中转站 ${baseUrl}`,
        bodyHtml: `<p>保存会删除以下 provider 条目及其密文，并清空 routing 中的引用：</p>
          <div class="affected">${removed.map((id) => `<code>${esc(id)}</code>`).join('')}</div>`,
        confirmText: '删除并保存',
        danger: true,
      });
      if (!ok) return;
    }

    saveBtn.disabled = true;
    saveBtn.textContent = '保存中…';
    const failures = [];
    try {
      for (const row of collected) {
        const body = {
          base_url: baseUrl,
          model: row.model,
          timeout_s: Number.isFinite(row.timeout_s) && row.timeout_s > 0 ? row.timeout_s : 30,
          thinking: row.thinking,
        };
        if (apiKey) body.api_key = apiKey;
        const resp = await api(`/api/admin/providers/${encodeURIComponent(row.id)}`, {
          method: 'PUT', body, ep: 'providers.put', timeout: apiKey ? 25000 : 12000,
        });
        if (resp.status !== 200) {
          if ([404, 405, 501].includes(resp.status)) state.unimplemented['providers.put'] = '/api/admin/providers/{id}';
          failures.push({ id: row.id, text: describeError(resp.status, resp.data) });
        }
      }
      for (const id of removed) {
        const resp = await api(`/api/admin/providers/${encodeURIComponent(id)}`, {
          method: 'DELETE', ep: 'providers.delete', timeout: 12000,
        });
        if (resp.status !== 200) failures.push({ id, text: describeError(resp.status, resp.data) });
      }
    } catch (error) {
      state.backendDown = true;
      state.backendError = error.message || String(error);
      failures.push({ id: '(请求中断)', text: state.backendError });
    }

    await Promise.all([loadProviders(), loadSettings(), loadAudit()]);
    renderAll();

    if (failures.length) {
      const attempted = collected.length + removed.length;
      const okCount = attempted - failures.length;
      resultNode.className = 'result-box bad';
      resultNode.innerHTML = `<b>${okCount === 0 ? '全部失败' : '部分失败'}（成功 ${okCount} / 失败 ${failures.length}）</b>；页面已重新读取真实配置，失败的行没有写入。
        <ul class="result-list">${failures.map((item) => `<li><code>${esc(item.id)}</code>：${esc(item.text)}</li>`).join('')}</ul>`;
      saveBtn.disabled = false;
      saveBtn.textContent = '重试保存';
      return;
    }
    close();
    const masks = state.providers.filter((item) => item.has_key && item.key_masked).map((item) => item.key_masked);
    toast(apiKey
      ? `中转站已保存；key 已写入密文${masks.length ? `（掩码 ${masks[0]}）` : ''}`
      : '中转站已保存（未提交 key）', 'ok', 8000);
  };
}

function openKeyModal(group) {
  const ids = group.items.map((item) => item.id);
  const current = group.items.map((item) => `<code>${esc(item.id)}</code>：${item.has_key ? esc(item.key_readable ? item.key_masked : '不可读') : '<span class="hint">未配置</span>'}`).join(' · ');
  const bodyHtml = `
    <p>本次 key 会分别写入以下 ${ids.length} 个模型条目的密文（每个条目独立加密），之后页面只显示掩码。</p>
    <div class="affected">${ids.map((id) => `<code>${esc(id)}</code>`).join('')}</div>
    <p class="hint tiny" style="margin-top:6px">当前：${current}</p>
    <label class="field" style="margin-top:10px"><span>新 API key（不会回填）</span>
      <input id="keyRotateInput" type="password" autocomplete="new-password" spellcheck="false" placeholder="输入新的 key">
    </label>
    <div class="callout subtle">主服务使用新 key 需要设置版本递增；后端会在写入后自动递增 settings 版本。若服务端未配置 <code>XKX_SECRET_KEY</code>，本次写入返回 <b>503 master_key_missing</b>，页面会显示该原因而不是成功。</div>
    <div id="keyRotateResult" class="result-box hidden"></div>
    <div class="modal-foot">
      <span class="modal-note">key 值不会出现在审计或页面其它位置。</span>
      <button class="ghost" type="button" data-modal-close>取消</button>
      <button class="primary" type="button" id="keyRotateBtn">写入 key</button>
    </div>`;
  const { wrap, close } = pushModal(bodyHtml, {
    title: '设置 / 轮换 API key',
    subtitle: `${group.base_url} · ${ids.length} 个模型条目`,
    width: '640px',
  });
  const saveBtn = $(`#keyRotateBtn`, wrap);
  const resultNode = $(`#keyRotateResult`, wrap);
  saveBtn.onclick = async () => {
    if (writesDisabled()) { toast('后端不可达或未登录：写操作已禁用', 'bad'); return; }
    const input = $(`#keyRotateInput`, wrap);
    const value = input.value;
    input.value = ''; // 立即清除，不在 DOM 中保留明文
    if (!value.trim()) {
      resultNode.className = 'result-box bad';
      resultNode.innerHTML = '<b>未提交</b>：key 不能为空。';
      return;
    }
    saveBtn.disabled = true;
    saveBtn.textContent = '写入中…';
    const failures = [];
    for (const id of ids) {
      try {
        const resp = await api(`/api/admin/providers/${encodeURIComponent(id)}`, {
          method: 'PUT', body: { api_key: value }, ep: 'providers.put', timeout: 20000,
        });
        if (resp.status !== 200) {
          if ([404, 405, 501].includes(resp.status)) state.unimplemented['providers.put'] = '/api/admin/providers/{id}';
          failures.push({ id, text: describeError(resp.status, resp.data) });
        }
      } catch (error) {
        state.backendDown = true;
        state.backendError = error.message || String(error);
        failures.push({ id, text: state.backendError });
      }
    }
    await Promise.all([loadProviders(), loadSettings(), loadAudit()]);
    renderAll();

    if (failures.length) {
      resultNode.className = 'result-box bad';
      resultNode.innerHTML = `<b>失败（${failures.length}/${ids.length}）</b>，未显示成功：
        <ul class="result-list">${failures.map((item) => `<li><code>${esc(item.id)}</code>：${esc(item.text)}</li>`).join('')}</ul>`;
      saveBtn.disabled = false;
      saveBtn.textContent = '重试写入';
      return;
    }
    close();
    const masks = state.providers.filter((item) => ids.includes(item.id) && item.key_masked).map((item) => item.key_masked);
    toast(`key 已写入 ${ids.length} 个条目${masks.length ? `；当前掩码 ${masks[0]}` : ''}`, 'ok', 8000);
  };
}

/* ---------------------------------------------------------------- capabilities */
function syncRouteDrafts(force) {
  const routing = (state.settings && state.settings.routing) || {};
  for (const meta of CAP_META) {
    const current = state.routeDraft[meta.id];
    if (current && current.dirty && !force) continue;
    const route = routing[meta.id] || {};
    state.routeDraft[meta.id] = {
      provider: String(route.provider || ''),
      fallback: Array.isArray(route.fallback) ? route.fallback.map((x) => String(x)).filter(Boolean) : [],
      dirty: false,
    };
  }
}

function capStatus(status) {
  const map = {
    active: ['ok', 'active'],
    degraded: ['bad', 'degraded'],
    unverified: ['warn', 'unverified'],
    stub: ['stub', 'stub'],
    unconfigured: ['muted', '未配置'],
  };
  return map[status] || ['muted', status || '未知'];
}

function providerSelectOptions(selected) {
  const options = ['<option value="">（未配置）</option>'];
  const list = state.providers.map((item) => ({ id: item.id, text: `${item.id} · ${item.model || '—'}` }));
  if (selected && !list.some((item) => item.id === selected)) {
    options.push(`<option value="${esc(selected)}" selected>${esc(selected)}（provider 不存在）</option>`);
  }
  for (const item of list) {
    options.push(`<option value="${esc(item.id)}" ${item.id === selected ? 'selected' : ''}>${esc(item.text)}</option>`);
  }
  return options.join('');
}

function availableFallbackOptions(capability, draft) {
  const exclude = new Set([draft.provider, ...draft.fallback].filter(Boolean));
  return state.providers.filter((item) => !exclude.has(item.id));
}

function renderCaps() {
  const list = $('#capList');
  if (isUnimplemented('settings.get') || isUnimplemented('providers.get')) {
    list.innerHTML = '<div class="result-box bad"><b>未实现</b>：能力位依赖 <code>/api/admin/settings</code> 与 <code>/api/admin/providers</code>，当前端点缺失，区块已禁用。</div>';
    $('#capsLastCheck').textContent = '—';
    return;
  }
  if (!state.settings) {
    list.innerHTML = `<div class="empty">${state.backendDown ? '后端不可达：未读取到能力位状态' : '未读取到 settings'}</div>`;
    $('#capsLastCheck').textContent = '—';
    return;
  }
  syncRouteDrafts(false);

  const caps = state.settings.capabilities || state.capabilities || {};
  const counts = { active: 0, degraded: 0, unverified: 0, stub: 0, unconfigured: 0 };
  for (const meta of CAP_META) {
    const item = caps[meta.id] || {};
    const status = item.status || 'unconfigured';
    counts[status] = (counts[status] || 0) + 1;
  }
  $('#capLegend').innerHTML = [
    `<span class="badge ok">active ${counts.active}</span>`,
    `<span class="badge bad">degraded ${counts.degraded}</span>`,
    `<span class="badge warn">unverified ${counts.unverified}</span>`,
    `<span class="badge muted">未配置 ${counts.unconfigured}</span>`,
    `<span class="badge stub">stub ${counts.stub}</span>`,
  ].join('');

  const lastCheck = state.settings.capabilities_last_check_at;
  $('#capsLastCheck').textContent = lastCheck
    ? `最近一次真实调用：${fmtEpoch(lastCheck)}`
    : '尚无真实调用记录（启动不探活）';

  list.innerHTML = CAP_META.map((meta) => {
    const item = caps[meta.id] || {};
    const status = String(item.status || 'unconfigured');
    const [badgeCls, badgeText] = capStatus(status);
    const draft = state.routeDraft[meta.id];
    const isStub = status === 'stub';
    const options = availableFallbackOptions(meta.id, draft);
    const chain = draft.fallback.length
      ? draft.fallback.map((id, index) => `<span class="chain-chip">
          <span class="idx">${index + 1}</span><b>${esc(id)}</b>
          <button type="button" data-act="cap-up" data-cap="${esc(meta.id)}" data-idx="${index}" title="上移">↑</button>
          <button type="button" data-act="cap-down" data-cap="${esc(meta.id)}" data-idx="${index}" title="下移">↓</button>
          <button type="button" data-act="cap-remove" data-cap="${esc(meta.id)}" data-idx="${index}" title="移除">×</button>
        </span>`).join('')
      : '<span class="hint">无降级链</span>';

    const served = item.last_served_by || null;
    const errorLine = (item.error || item.http_status)
      ? `<div class="cap-warn">最近一次真实调用失败：<b>${esc(item.error || 'error')}</b>${item.http_status ? ` · HTTP ${esc(item.http_status)}` : ''} · ${esc(item.last_check_at_iso || (item.last_check_at ? fmtEpoch(item.last_check_at) : '—'))}</div>`
      : '';

    const stubNote = isStub
      ? `<div class="cap-stub-note"><b>stub</b>：本地确定性实现，不会发起模型调用。证据：${esc(item.evidence || '—')}。此能力位保存 provider/降级链不会改变状态，也不会接入真实模型。</div>`
      : '';
    const unconfiguredNote = status === 'unconfigured'
      ? '<div class="cap-note">该能力位没有配置 provider。保存后状态变为 <b>unverified</b>，直到发生真实调用才可能变为 active / degraded。</div>'
      : '';

    return `<article class="cap-row ${isStub ? 'stub' : ''} ${status === 'degraded' ? 'degraded' : ''}" data-cap-row="${esc(meta.id)}">
      <header class="cap-head">
        <div class="cap-name"><b>${esc(meta.id)}</b><span>${esc(meta.label)} · ${esc(meta.note)}</span></div>
        <span class="badge ${badgeCls}">${esc(badgeText)}</span>
        <div class="cap-metrics">
          <span>last_check <b>${esc(item.last_check_at_iso || (item.last_check_at ? fmtEpoch(item.last_check_at) : 'null'))}</b></span>
          <span>延迟 <b>${esc(item.latency_ms === null || item.latency_ms === undefined ? '—' : `${item.latency_ms} ms`)}</b></span>
          <span>承担 <b>${esc(served ? `${served.provider} / ${served.model || '—'}` : '—')}</b></span>
          <span>降级 <b>${esc(served && served.fallback_used ? '命中' : served ? '未命中' : '—')}</b></span>
        </div>
      </header>
      <div class="cap-body">
        <div class="cap-edit">
          <label class="field"><span>主 provider</span>
            <select data-cap-primary="${esc(meta.id)}" ${writesDisabled() ? 'disabled' : ''}>${providerSelectOptions(draft.provider)}</select>
          </label>
          <div class="cap-route">
            <span>降级链（按顺序尝试）</span>
            <div class="chain">${chain}</div>
            <div class="chain-add">
              <select data-cap-add="${esc(meta.id)}" ${writesDisabled() || !options.length ? 'disabled' : ''} aria-label="添加降级 provider">
                <option value="">选择 provider…</option>
                ${options.map((item) => `<option value="${esc(item.id)}">${esc(item.id)} · ${esc(item.model || '—')}</option>`).join('')}
              </select>
              <button class="ghost micro" data-act="cap-add-fallback" data-cap="${esc(meta.id)}" ${writesDisabled() || !options.length ? 'disabled' : ''}>添加</button>
            </div>
          </div>
          <button class="primary small" data-act="cap-save" data-cap="${esc(meta.id)}" ${writesDisabled() || writeEpUnavailable('settings.put') ? 'disabled' : ''}>保存路由</button>
        </div>
        ${errorLine}
        ${stubNote}
        ${unconfiguredNote}
        <div class="cap-note">
          <b>状态来源</b>：<code>${esc(item.source || 'config')}</code> ·
          服务方 <code>${esc(served ? served.provider : (item.provider || '—'))}</code> ·
          模型 <code>${esc(served ? served.model : (item.model || '—'))}</code>
          <details style="margin-top:4px"><summary>调用点证据</summary><div>${esc(item.evidence || '—')}</div></details>
        </div>
      </div>
    </article>`;
  }).join('');
}

async function saveCapability(capability) {
  if (state.busy) { toast('上一个写操作还在进行中', 'warn'); return; }
  if (writesDisabled()) { toast('后端不可达或未登录：写操作已禁用', 'bad'); return; }
  const draft = state.routeDraft[capability];
  if (!draft) return;
  const fallback = draft.fallback.filter((id) => id && id !== draft.provider);
  if (!draft.provider) {
    if (fallback.length) {
      showCapResult('bad', `<b>未提交</b>：能力位 <code>${esc(capability)}</code> 的主 provider 为空，但降级链还有 ${fallback.length} 项。请先选择主 provider，或移除降级项后再清空。`);
      return;
    }
    const clear = await confirmAction({
      title: '清空该能力位的路由配置？',
      subtitle: capability,
      bodyHtml: `<p>将把 <code>${esc(capability)}</code> 的 <code>routing.provider</code> 与 <code>routing.fallback</code> 写为空。状态会回到「未配置」，直到重新配置并发生真实调用。</p>`,
      confirmText: '清空配置',
      danger: true,
    });
    if (!clear) return;
  }
  setBusy(true);
  try {
    const result = await api('/api/admin/settings', {
      method: 'PUT',
      body: { routing: { [capability]: { provider: draft.provider, fallback } } },
      ep: 'settings.put',
      timeout: 20000,
    });
    if (result.status !== 200) {
      if ([404, 405, 501].includes(result.status)) {
        state.unimplemented['settings.put'] = '/api/admin/settings';
        renderAll();
        showCapResult('bad', `<b>未实现</b>：后端没有实现 <code>PUT /api/admin/settings</code>（HTTP ${result.status}）。能力位路由写入已禁用。`);
        return;
      }
      showCapResult('bad', `<b>保存失败</b>：${esc(describeError(result.status, result.data))}`);
      return;
    }
    const payload = result.data || {};
    if (payload.settings) state.settings = payload.settings;
    state.routeDraft[capability].dirty = false;
    syncRouteDrafts(true);
    renderAll();
    showCapResult('warn', `<b>路由已保存</b>（hot，≤5 秒生效）。能力状态仍由真实调用决定：保存不会把状态改成 active。`);
    await Promise.all([loadOverview(), loadAudit()]);
    renderAll();
  } catch (error) {
    state.backendDown = true;
    state.backendError = error.message || String(error);
    renderAll();
    showCapResult('bad', `<b>后端不可达</b>：${esc(state.backendError)}。路由没有写入。`);
  } finally {
    setBusy(false);
  }
}

function showCapResult(kind, html) {
  const node = $('#capResult');
  node.className = `result-box ${kind}`;
  node.innerHTML = html;
}

/* ---------------------------------------------------------------- audit */
function actionMeta(action) {
  return ACTION_META[action] || { label: action || '—', cls: 'muted' };
}

function summarize(value) {
  if (value === null || value === undefined || value === '') return '—';
  try {
    const masked = maskDeep(value);
    const text = JSON.stringify(masked);
    return text.length > 220 ? text.slice(0, 220) + '…' : text;
  } catch (_) {
    return '—';
  }
}

function renderAudit() {
  const wrap = $('#auditWrap');
  if (isUnimplemented('audit.get')) {
    wrap.innerHTML = '<div class="result-box bad"><b>未实现</b>：<code>/api/admin/audit</code> 返回 404，审计区块已禁用。</div>';
    return;
  }
  if (!state.audit.length) {
    wrap.innerHTML = state.endpointErrors['audit.get']
      ? `<div class="result-box bad">读取失败：${esc(describeError(state.endpointErrors['audit.get'].status, { code: state.endpointErrors['audit.get'].code }))}</div>`
      : '<div class="empty">暂无审计记录</div>';
    return;
  }
  const rows = state.audit.map((record) => {
    const meta = actionMeta(record.action);
    const detail = record.detail;
    const before = record.before;
    const after = record.after;
    const summary = detail && Object.keys(maskDeep(detail) || {}).length ? summarize(detail) : (before || after ? '有变更（见详情）' : '—');
    const details = [];
    if (before !== null && before !== undefined) details.push({ label: 'before（掩码）', value: before });
    if (after !== null && after !== undefined) details.push({ label: 'after（掩码）', value: after });
    if (detail !== null && detail !== undefined) details.push({ label: 'detail（掩码）', value: detail });
    const detailsHtml = details.length
      ? `<details><summary>查看</summary>${details.map((item) => `<div class="cell-note">${esc(item.label)}</div><pre class="json">${esc(JSON.stringify(maskDeep(item.value), null, 2))}</pre>`).join('')}</details>`
      : '—';
    return `<tr>
      <td class="time">${esc(fmtEpoch(record.at))}</td>
      <td><span class="badge ${meta.cls}">${esc(meta.label)}</span><div class="cell-note"><code>${esc(record.action)}</code></div></td>
      <td><code>${esc(record.key)}</code><div class="cell-note">${esc(record.who || '—')}</div></td>
      <td><div class="cell-note" style="font-size:11.5px;color:var(--ink-soft)">${esc(summary)}</div></td>
      <td>${detailsHtml}</td>
    </tr>`;
  }).join('');
  wrap.innerHTML = `<table class="grid">
    <colgroup><col style="width:140px"><col style="width:150px"><col style="width:150px"><col><col style="width:80px"></colgroup>
    <thead><tr><th>时间</th><th>操作</th><th>对象 / 操作者</th><th>摘要（已掩码）</th><th>详情</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

/* ---------------------------------------------------------------- modal */
function pushModal(html, opts = {}) {
  const { title = '', subtitle = '', width = '720px', closable = true, closeOnBackdrop = true } = opts;
  const wrap = document.createElement('div');
  wrap.className = 'modal-backdrop';
  wrap.dataset.closable = closable ? '1' : '0';
  wrap.innerHTML = `<div class="modal" style="--modal-w:${width}">
    <div class="modal-head">
      <div><h3>${esc(title)}</h3>${subtitle ? `<p>${esc(subtitle)}</p>` : ''}</div>
      ${closable ? '<button class="modal-close" type="button" data-modal-close aria-label="关闭">×</button>' : ''}
    </div>
    <div class="modal-body">${html}</div>
  </div>`;
  $('#modalRoot').appendChild(wrap);
  state.modalOpen = true;
  const close = () => {
    wrap.remove();
    state.modalOpen = $('#modalRoot').children.length > 0;
  };
  wrap.addEventListener('click', (event) => {
    if (event.target === wrap && closable && closeOnBackdrop) close();
  });
  $$('[data-modal-close]', wrap).forEach((button) => { button.onclick = close; });
  return { wrap, close };
}

function confirmAction(opts) {
  const { title, subtitle = '', bodyHtml = '', confirmText = '确认', danger = true } = opts;
  return new Promise((resolve) => {
    let settled = false;
    const { wrap, close } = pushModal(`
      ${bodyHtml}
      <div class="modal-foot">
        <span class="modal-note">二次确认</span>
        <button class="ghost" type="button" data-confirm-no>取消</button>
        <button class="${danger ? 'danger' : 'primary'}" type="button" data-confirm-yes>${esc(confirmText)}</button>
      </div>`, { title, subtitle, width: '560px', closable: false });
    const finish = (value) => {
      if (settled) return;
      settled = true;
      close();
      resolve(value);
    };
    $('[data-confirm-yes]', wrap).onclick = () => finish(true);
    $('[data-confirm-no]', wrap).onclick = () => finish(false);
    wrap.addEventListener('click', (event) => {
      if (event.target === wrap) finish(false);
    });
  });
}

/* ---------------------------------------------------------------- actions */
async function deleteProvider(providerId) {
  const provider = state.providers.find((item) => item.id === providerId);
  if (!provider) return;
  const ok = await confirmAction({
    title: '删除这个模型条目？',
    subtitle: providerId,
    bodyHtml: `<p>将删除 provider <code>${esc(providerId)}</code>（${esc(provider.model || '—')}）及其密文，并清空 routing 中对该 id 的引用。</p>`,
    confirmText: '删除',
    danger: true,
  });
  if (!ok) return;
  setBusy(true);
  try {
    const result = await api(`/api/admin/providers/${encodeURIComponent(providerId)}`, {
      method: 'DELETE', ep: 'providers.delete', timeout: 15000,
    });
    if (result.status !== 200) {
      showProviderResult('bad', `<b>删除失败</b>：${esc(describeError(result.status, result.data))}`);
      return;
    }
    await Promise.all([loadProviders(), loadSettings(), loadAudit(), loadOverview()]);
    renderAll();
    showProviderResult('warn', `<b>已删除</b> <code>${esc(providerId)}</code>；routing 引用已由后端清空。`);
  } catch (error) {
    state.backendDown = true;
    state.backendError = error.message || String(error);
    renderAll();
    showProviderResult('bad', `<b>后端不可达</b>：${esc(state.backendError)}`);
  } finally {
    setBusy(false);
  }
}

async function deleteRelay(group) {
  const ids = group.items.map((item) => item.id);
  const ok = await confirmAction({
    title: '删除整个中转站？',
    subtitle: group.base_url || '（base_url 为空）',
    bodyHtml: `<p>将删除 ${ids.length} 个模型条目及其密文，并清空 routing 中的引用：</p>
      <div class="affected">${ids.map((id) => `<code>${esc(id)}</code>`).join('')}</div>`,
    confirmText: '删除中转站',
    danger: true,
  });
  if (!ok) return;
  setBusy(true);
  const failures = [];
  for (const id of ids) {
    try {
      const result = await api(`/api/admin/providers/${encodeURIComponent(id)}`, {
        method: 'DELETE', ep: 'providers.delete', timeout: 15000,
      });
      if (result.status !== 200) failures.push(`${id}：${describeError(result.status, result.data)}`);
    } catch (error) {
      state.backendDown = true;
      state.backendError = error.message || String(error);
      failures.push(`${id}：${state.backendError}`);
    }
  }
  await Promise.all([loadProviders(), loadSettings(), loadAudit(), loadOverview()]);
  renderAll();
  if (failures.length) {
    showProviderResult('bad', `<b>部分删除失败</b><ul class="result-list">${failures.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`);
  } else {
    showProviderResult('warn', `<b>已删除中转站</b> ${esc(group.base_url || '（空 base_url）')}，共 ${ids.length} 条。`);
  }
  setBusy(false);
}

function showProviderResult(kind, html) {
  const node = $('#providerResult');
  node.className = `result-box ${kind}`;
  node.innerHTML = html;
}

async function toggleKillswitch() {
  if (state.busy) { toast('上一个写操作还在进行中', 'warn'); return; }
  if (writesDisabled()) { toast('后端不可达或未登录：写操作已禁用', 'bad'); return; }
  const current = state.settings && state.settings.llm ? state.settings.llm.global_enabled : undefined;
  if (typeof current !== 'boolean') {
    toast('未读取到 llm.global_enabled，先刷新', 'warn');
    return;
  }
  const turningOn = current === true;
  const ok = await confirmAction({
    title: turningOn ? '关闭 LLM 总闸？' : '恢复 LLM 调用？',
    subtitle: '二次确认 · 当前状态回显',
    bodyHtml: `
      <div class="kv-row"><span>当前 llm.global_enabled</span><b>${esc(String(current))}</b></div>
      <div class="kv-row"><span>当前效果</span><b>${current ? 'LLM 调用允许' : '新调用直接返回 llm_killswitch'}</b></div>
      <p style="margin:10px 0 0">${turningOn
        ? '确认后立即阻断新的 LLM 调用（≤5 秒生效）；在飞请求按各自超时结束。'
        : '确认后恢复允许 LLM 调用（≤5 秒生效）。'}</p>
      <p class="hint tiny" style="margin-top:6px">提交前会重新读取一次真实状态；若状态已变化，本次操作会取消。</p>`,
    confirmText: turningOn ? '确认阻断' : '确认恢复',
    danger: turningOn,
  });
  if (!ok) return;

  setBusy(true);
  try {
    const freshOk = await loadSettings();
    if (!freshOk) {
      renderAll();
      toast('重新读取当前状态失败，本次操作已取消（未发送总闸请求）', 'bad', 9000);
      return;
    }
    const fresh = state.settings && state.settings.llm ? state.settings.llm.global_enabled : undefined;
    if (typeof fresh !== 'boolean' || fresh !== current) {
      renderAll();
      toast('确认期间状态已变化，本次操作已取消；请按最新状态重试', 'warn');
      return;
    }
    const result = await api('/api/admin/llm/killswitch', {
      method: 'POST', body: { enabled: turningOn }, ep: 'killswitch.post', timeout: 15000,
    });
    if (result.status !== 200) {
      if ([404, 405, 501].includes(result.status)) {
        state.unimplemented['killswitch.post'] = '/api/admin/llm/killswitch';
        renderAll();
        toast(`未实现：后端没有实现 POST /api/admin/llm/killswitch（HTTP ${result.status}）；总闸按钮已禁用`, 'bad', 10000);
        return;
      }
      toast(`总闸操作失败：${describeError(result.status, result.data)}`, 'bad', 10000);
      return;
    }
    await Promise.all([loadSettings(), loadProviders(), loadOverview(), loadAudit()]);
    renderAll();
    const payload = result.data || {};
    toast(`${payload.note || '总闸已切换'} · 当前 llm_global_enabled=${payload.llm_global_enabled}`, turningOn ? 'warn' : 'ok', 9000);
  } catch (error) {
    state.backendDown = true;
    state.backendError = error.message || String(error);
    renderAll();
    toast(state.backendError, 'bad', 10000);
  } finally {
    setBusy(false);
  }
}

/* ---------------------------------------------------------------- wiring */
function bindStatic() {
  $('#connectBtn').onclick = connect;
  $('#adminTokenInput').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') connect();
  });
  $('#apiBaseInput').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') connect();
  });
  $('#refreshBtn').onclick = async () => {
    if (state.paramDraft.size || Object.values(state.routeDraft).some((d) => d.dirty)) {
      toast('有未保存的修改：先保存或撤销，再刷新，避免覆盖输入', 'warn', 7000);
      return;
    }
    setBusy(true);
    try {
      await loadAll();
      toast('已刷新真实数据', 'ok');
    } finally {
      setBusy(false);
    }
  };
  $('#logoutBtn').onclick = logout;
  $('#addRelayBtn').onclick = () => openRelayEditor(null);
  $('#ksBtn').onclick = toggleKillswitch;
  $('#saveAllParams').onclick = () => saveParams(Array.from(state.paramDraft.keys()));
  $('#auditLimit').onchange = async (event) => {
    state.auditLimit = Number(event.target.value) || 100;
    await loadAudit();
    renderAudit();
  };

  $('#shell').addEventListener('click', (event) => {
    const nav = event.target.closest('[data-nav]');
    if (nav) {
      $$('.rail-item').forEach((item) => item.classList.toggle('active', item === nav));
    }
    const button = event.target.closest('[data-act]');
    if (!button || button.disabled) return;
    const act = button.dataset.act;
    if (act === 'save-param') saveParams([button.dataset.key]);
    else if (act === 'reset-param') {
      state.paramDraft.delete(button.dataset.key);
      renderParams();
    } else if (act === 'provider-test') providerTest(button.dataset.id);
    else if (act === 'provider-delete') deleteProvider(button.dataset.id);
    else if (act === 'relay-edit') {
      const group = findGroup(button.dataset.base);
      if (group) openRelayEditor(group);
    } else if (act === 'relay-key') {
      const group = findGroup(button.dataset.base);
      if (group) openKeyModal(group);
    } else if (act === 'relay-delete') {
      const group = findGroup(button.dataset.base);
      if (group) deleteRelay(group);
    } else if (act === 'cap-add-fallback') {
      const capability = button.dataset.cap;
      const select = $(`[data-cap-add="${capability}"]`);
      const value = select ? select.value : '';
      if (!value) { toast('先选择一个 provider', 'warn'); return; }
      const draft = state.routeDraft[capability];
      if (!draft.fallback.includes(value)) draft.fallback.push(value);
      draft.dirty = true;
      renderCaps();
    } else if (act === 'cap-up' || act === 'cap-down' || act === 'cap-remove') {
      const capability = button.dataset.cap;
      const index = Number(button.dataset.idx);
      const draft = state.routeDraft[capability];
      if (!draft || Number.isNaN(index)) return;
      if (act === 'cap-remove') draft.fallback.splice(index, 1);
      else {
        const target = act === 'cap-up' ? index - 1 : index + 1;
        if (target < 0 || target >= draft.fallback.length) return;
        const temp = draft.fallback[index];
        draft.fallback[index] = draft.fallback[target];
        draft.fallback[target] = temp;
      }
      draft.dirty = true;
      renderCaps();
    } else if (act === 'cap-save') saveCapability(button.dataset.cap);
  });

  $('#shell').addEventListener('input', (event) => {
    const input = event.target.closest('[data-param]');
    if (!input) return;
    state.paramDraft.set(input.dataset.param, input.value);
    updateParamDirty();
  });
  $('#shell').addEventListener('change', (event) => {
    const input = event.target.closest('[data-param]');
    if (input) {
      state.paramDraft.set(input.dataset.param, input.value);
      updateParamDirty();
      return;
    }
    const select = event.target.closest('[data-cap-primary]');
    if (select) {
      const capability = select.dataset.capPrimary;
      const draft = state.routeDraft[capability];
      if (draft) {
        draft.provider = select.value;
        draft.fallback = draft.fallback.filter((id) => id !== select.value);
        draft.dirty = true;
        renderCaps();
      }
    }
  });

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    const root = $('#modalRoot');
    const last = root.lastElementChild;
    if (!last || last.dataset.closable !== '1') return;
    last.remove();
    state.modalOpen = root.children.length > 0;
  });
}

function startAutoRefresh() {
  if (autoTimer) clearInterval(autoTimer);
  autoTimer = setInterval(async () => {
    if (!state.auth || state.backendDown || state.modalOpen || state.busy) return;
    if (state.paramDraft.size || Object.values(state.routeDraft).some((d) => d.dirty)) return;
    await Promise.all([loadSettings(), loadOverview()]);
    renderHeader();
    renderBanner();
    renderOverview();
    renderParams();
    renderRelays();
    renderCaps();
    renderRail();
  }, 15000);
}

async function restoreSession() {
  const saved = readSession();
  if (!saved) { showGate(); return; }
  state.apiBase = normalizeBase(sessionStorage.getItem(SS_API) || defaultApiBase());
  state.sessionToken = saved.token;
  state.sessionExpires = Number(saved.expires_at || 0);
  $('#apiBaseInput').value = state.apiBase;
  showGate('正在用已保存的会话连接…', '');
  await loadAll();
  if (state.auth) {
    hideGate();
  } else {
    showGate('会话不可用，请重新输入管理口令。', 'bad');
  }
}

function boot() {
  state.apiBase = defaultApiBase();
  $('#apiBaseInput').value = state.apiBase;
  if (location.protocol === 'file:') {
    $('#fileHint').textContent = '当前以 file:// 打开：浏览器会拦截到管理服务的跨域请求（后端未提供 CORS）。'
      + '请运行 python3 ui/admin/server.py，然后访问 http://127.0.0.1:8039/ 。';
  } else {
    $('#fileHint').textContent = '';
  }
  bindStatic();
  startAutoRefresh();
  restoreSession();
}

document.addEventListener('DOMContentLoaded', boot);
