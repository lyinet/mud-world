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
    /* 停留保活：服务端只刷新活跃时钟、不写事件；离线时会返回 player_offline 触发自愈。 */
    heartbeat: (account) => postJSON('/api/player/heartbeat', {
      account_id: account || '',
      keepalive: true
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
    ),
    /* 全城图是公共只读数据：节点 + 无向边，匿名可读，不依赖玩家令牌。 */
    worldMap: () => getJSON('/api/world/map', false),
    /* 世界动态是公共事件：匿名可读，故意不带令牌，避免与登录/刷新链路互相牵制。 */
    feed: (limit) => getJSON('/api/world/feed?limit=' + encodeURIComponent(String(limit || 20)), false)
  };

  const FEED_LIMIT = 20;
  const FEED_INTERVAL_MS = 8000;
  /* 服务端活跃阈值 30s、离线阈值 60s：20s 一次保活足够，页面前台停留不会再被判离线。 */
  const HEARTBEAT_INTERVAL_MS = 20000;
  /* 商店价格/库存随世界 tick 变化：主状态 8 秒刷新一次，商业面板随之更新。 */
  const STATE_INTERVAL_MS = 8000;
  /* 移动口径：对齐 content/economy 的 stamina_move 默认值与会话推进步长。 */
  const MOVE_STAMINA_COST = 5;
  const SIM_TICKS_PER_HOUR = 6;

  /* 后端 feed 的 verb 是内部动词（move / quest_accept / open_shop …）。
     玩家侧只走这张表：集中映射成中文动作；表里没有的一律「有事发生」，
     绝不把原始英文/下划线动词漏到界面上。 */
  const FEED_VERB_TEXT = {
    move: '赶路', work: '上工', cultivate: '修炼', buy: '采买', sell: '卖货',
    trade: '买卖', talk: '交谈', marry: '成婚', open_shop: '开店', shop_open: '开店',
    join_sect: '入门', escort: '押镖', quest_accept: '接单', quest_deliver: '交单',
    consume: '歇脚', rest: '歇息', eat: '进食', patrol: '巡街'
  };
  const FEED_VERB_FALLBACK = '有事发生';

  function feedVerbLabel(verb) {
    return FEED_VERB_TEXT[String(verb == null ? '' : verb).trim().toLowerCase()] || FEED_VERB_FALLBACK;
  }

  /* 摘要缺省时用中文动作兜底；摘要里若混入内部动词，逐词替换成中文动作，
     未知词落到「有事发生」，保证玩家看到的正文里没有英文内部动词。 */
  function feedBody(row) {
    const raw = String((row && row.summary) || '').trim();
    if (!raw) return feedVerbLabel(row && row.verb);
    return raw.replace(/[a-z][a-z_]*/g, function (word) {
      return FEED_VERB_TEXT[word] || FEED_VERB_FALLBACK;
    });
  }

  /* 商业与经营口径：全部对齐 content/economy.yaml。
     `stock` 是开店时的初始进货成本，报价必须 rent+stock，服务端按总额校验现金。 */
  const COMMERCE_TIERS = [
    { kind: 'stall', label: '摊', rent: 300, stock: 45 },
    { kind: 'storefront', label: '店面', rent: 1200, stock: 272 },
    { kind: 'branch', label: '分号', rent: 3000, stock: 450 }
  ];
  const POSITION_WAGES = {
    '铁匠': 32, '绣娘': 24, '脚夫': 18, '掌柜': 36, '游医': 28, '镖师': 30,
    '酒保': 20, '捕快': 26, '说书人': 22, '师爷': 30, '学究': 24, '药童': 15,
    '屠户': 30, '船夫': 20, '更夫': 16, '媒婆': 24, '石匠': 28, '香客': 14,
    '货郎': 26, '算命先生': 22, '渔娘': 20, '菜农': 20, '抄经人': 14,
    '庙祝': 18, '茶博士': 22
  };
  const ESCORT_RULES = {
    durationTicks: 144,   // 24h × 6 tick
    agencyCut: 0.30,
    feeMin: 120,
    feeMax: 260,
    cargoMin: 100,
    qtyMax: 3
  };
  const SHOP_HIRE = { minAffection: 6.0, minSkill: 0.15, wageMinRatio: 0.8, wageMaxRatio: 1.5, capacity: { stall: 1, storefront: 2, branch: 3 } };
  /* 店铺经营：定价只能在基准价 ±20% 内；账本固定展示最近 7 个有日结的世界日。
     低于 3 件视为缺货线；补货建议量按档位（摊 10 / 店面 15 / 分号 20），
     对齐 docs/shop-profitability.md §7.2，补货仍由玩家点「进货」确认。 */
  const SHOP_OPS = {
    priceMinRatio: 0.8,
    priceMaxRatio: 1.2,
    reportDays: 7,
    lowStock: 3,
    restockTarget: { stall: 10, storefront: 15, branch: 20 }
  };
  /* 报告只给 item id；常规 SKU 的中文名在这里兜底，避免售罄后只显示英文 id。 */
  const SHOP_ITEM_NAMES = {
    flatbread: '炊饼', noodle: '阳春面', rice: '糙米', coarse_cloth: '粗布',
    cleaver: '菜刀', needle: '绣针', wound_powder: '金疮药', mugwort: '艾草',
    iron_ingot: '铁锭', raw_silk: '生丝', charcoal: '木炭', salted_meat: '腊肉'
  };
  /* 各档位开张时的常规 SKU；库存卖空后后端会删掉 0 件键，
     前端用这份清单兜底，保证售罄 SKU 仍出现在库存视图里。 */
  const SHOP_CANONICAL_SKUS = {
    stall: ['flatbread', 'noodle', 'rice'],
    storefront: ['flatbread', 'noodle', 'rice', 'coarse_cloth', 'cleaver', 'needle'],
    branch: ['flatbread', 'noodle', 'rice', 'coarse_cloth', 'cleaver', 'needle',
             'wound_powder', 'mugwort']
  };
  /* 婚姻裁决口径对齐 src/wsim/actions/marriage.py：
     接受 = 好感 ≥60 且 信任 ≥30；再处处看 = 好感 ≥45 或 信任 ≥20；其余为拒绝。 */
  const MARRIAGE_RULES = {
    minAffection: 60.0,
    minTrust: 30.0,
    courtshipAffection: 45.0,
    courtshipTrust: 20.0,
    giftQtyMax: 20,
    reasonChars: 40
  };
  /* 门派门槛：与 docs/player-api.md §13.2 对齐，仅用于门槛提示与预填；
     最终裁决仍在后端，前端不替代校验。 */
  const SECT_RULES = {
    foundCost: 800,
    registrationFee: 200,
    startupCapital: 600,
    minReputation: 20,
    minAffection: 30,
    minTrust: 30,
    minMasterSkill: 0.4,
    discipleCap: 3,
    stipendDaily: 10,
    duesDaily: 5,
    arrearsExpel: 30,
    withdrawCap: 500,
    treasuryReserve: 100
  };

  function tierOf(kind) {
    return COMMERCE_TIERS.filter(function (t) { return t.kind === kind; })[0] || null;
  }

  function tierLabel(kind) {
    const t = tierOf(kind);
    return t ? t.label : (kind || '店铺');
  }

  function positionWage(position) {
    return POSITION_WAGES[position] == null ? 0 : POSITION_WAGES[position];
  }

  /* 与服务端结算一致：share = fee - int(fee × cut)；不能先乘 (1-cut) 再取整，
     否则 180 文会因浮点误差算成 125（服务端结算为 126）。 */
  function escortNet(fee, cut) {
    const base = Math.floor(Number(fee) || 0);
    const ratio = Number(cut == null ? ESCORT_RULES.agencyCut : cut) || 0;
    return Math.max(1, base - Math.floor(base * ratio));
  }

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
    identity_locked: '该账号已有进入身份，身份进入后不可更改；请选原来的身份。',
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
    quest_step_incomplete: '你还没有真正完成这单的目标动作；请先到指定地点上工/交易/买入或把镖单送到目标地点，再回来交付。',
    item_not_held: '你身上没有这件物品或数量不足。',
    /* 装备与使用（actions/equipment.py）。 */
    item_not_equippable: '这件物品不能装备。',
    equipment_slot_empty: '该装备槽位没有已装备物品。',
    unknown_equipment_slot: '未知的装备槽位。',
    item_not_usable: '这件物品不能直接使用；只有食物、酒水和药品可用。',
    bad_quantity: '数量不合法。',
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
    too_many_attempts: '尝试次数过多，请稍后再试。',
    'bad_arg:money_delta': '金额参数必须是整数。',
    /* 玩家消费类动作（buy / sell / consume / escort）专属原因码。 */
    not_enough_money: '你的钱不够（买入价含交易税）。',
    item_not_available: '这件物品不在当前内容包里，买不到。',
    item_missing: '你背包里没有足够的这件物品。',
    not_here: '你不在这个交易地点。',
    shop_closed: '这里没有店铺，不能买卖。',
    not_a_merchant: '这里的店铺不做这件货的买卖。',
    unknown_item: '这件物品在世界里不存在。',
    no_buyer_cash: '本地店铺现金不够，暂时收不下这批货。',
    wrong_location: '你不在这家店铺所在的地点。',
    'bad_arg:qty': '数量必须是 1-20 的整数。',
    'bad_arg:item': '物品参数不合法。',
    'bad_arg:from': '交易地点参数不合法。',
    'bad_arg:to': '交易地点参数不合法。',
    /* 押镖。 */
    not_at_origin: '你不在镖单的起点。',
    route_not_adjacent: '押镖路线不连续；只能沿相邻地点逐段走。',
    already_on_escort: '你正在押镖，不能同时接第二单。',
    cargo_too_low: '货值低于这条线路的最低承运要求。',
    cargo_too_high: '货值超过这条线路的最高承运额度。',
    fee_too_low: '镖银低于这条线路的最低标准。',
    fee_too_high: '镖银超过这条线路的最高标准。',
    no_escort_agency: '起点没有镖局，不能接镖。',
    agency_insufficient_cash: '镖局现金不足以支付这单镖银。',
    'bad_arg:route': '路线格式错误；请从当前地点沿相邻出口选择终点。',
    'bad_arg:cargo_qty': '货物数量必须是 1-100 的整数。',
    'bad_arg:cargo_value': '货值必须是整数。',
    'bad_arg:fee': '镖银必须是整数。',
    /* 店铺与雇工。 */
    treasury_guard: '商会护盘期间暂不可开店。',
    treasury_buffer: '区域金库保底线不足，开店被暂缓；过一会儿再试。',
    bad_shop_type: '店铺档位不合法；只能选摊、店面或分号。',
    already_owns_shop: '你已经有店铺了，不能再开第二家。',
    no_landlord: '这个地点没有可签约的铺面。',
    no_shop: '你还没有店铺；先开一家再来雇人。',
    not_shop_owner: '这家店铺不是你的，不能经营。',
    not_player_shop_hire: '只有玩家店铺可以雇工。',
    target_not_found: '要雇的人不在这个世界。',
    self_recruit: '不能雇自己。',
    target_not_at_shop: '对方不在你的店铺所在地点。',
    already_hired: '对方已经在你的店里做工。',
    target_owns_shop: '对方自己开着店，不会来做雇工。',
    shop_full: '店铺雇工已满；升级铺面或先解雇再雇。',
    /* 店铺经营动作（restock / set_price / dismiss / withdraw / shop_report）。 */
    price_out_of_range: '定价只能在基准价的 ±20% 以内；超出范围没人敢应价。',
    insufficient_shop_cash: '店铺现金不足；先等日结回款，或把数量调小。',
    target_not_employed: '这个人不在你的店里做工，无法辞退。',
    employee_not_found: '店里没有这个雇工；可能已经离开，刷新名单后再试。',
    'bad_arg:price': '价格必须是整数。',
    'bad_arg:amount': '提现金额必须是正整数。',
    unknown_position: '这个岗位不在市价表里；请从可选岗位中选择。',
    skill_too_low: '对方手艺还不够（技能低于 0.15）。',
    relation_too_low: '好感不足 6.0；先多交谈、经营人情再来雇。',
    wage_too_low: '日薪低于市价的 0.8 倍，对方不会接受。',
    wage_too_high: '日薪高于市价的 1.5 倍，对方觉得不合理。',
    /* 婚配相关（服务端已登记，入口下一步开放）：先把文案备齐，避免原始码外露。 */
    already_married: '你已经成婚了，不能再结一次。',
    bond_too_low: '感情还没到谈婚论嫁的地步；先多相处、经营人情。',
    gift_missing: '提亲需要带一件像样的礼物。',
    not_married: '你还没有婚约，无法进行这个操作。',
    self_propose: '不能向自己提亲。',
    spouse_not_alive: '你的配偶已不在人世，这段婚约无法继续。',
    spouse_not_here: '你的配偶不在当前地点；先走到对方那里。',
    spouse_offline: '你的配偶不在线，暂时不能互动。',
    target_in_couple: '对方已有婚约或配偶，不能再谈。',
    target_not_npc: '对方不是可婚配的人物。',
    target_not_player: '该操作只对玩家角色有效。',
    trust_too_low: '信任不足；先建立更深的交情。',
    unknown_actor: '操作发起人不在世界里；请刷新后重试。',
    /* 押镖付款方与休息：服务端已登记原因码，UI 入口按上线节奏开放。 */
    no_payer_funds: '付款方资金不足，这单暂时接不了。',
    cannot_rest_while_escorting: '押镖途中不能休息；先完成这趟镖。',
    cannot_rest_now: '现在不能休息；请稍后再试。',
    'bad_arg:hours': '休息时长必须是 1-8 小时的整数。',
    actor_not_alive: '操作发起人已不在人世。',
    target_not_alive: '对方已不在人世。',
    'bad_arg:gift_money': '聘礼必须是整数。',
    'bad_arg:gift_qty': '信物数量必须是 1-20 的整数。',
    /* 玩家尚未开放的玩法：给明确文案，绝不落到「未知操作」。 */
    not_implemented_for_player: '该玩法尚未开放。',
    sect_exists: '这里已经有门派了。',
    already_in_faction: '你已经入了门派，不能另立门户。',
    low_reputation: '声望不足，暂时不能立派。',
    /* 门派动作契约码（docs/player-api.md §13）：13 个拒绝码全部给中文，
       其余参数/名册类码也一并覆盖，避免落到「未知错误」。 */
    no_sect: '你还没有加入门派。',
    not_sect_leader: '只有门主能执行这个操作。',
    sect_full: '门派弟子已满，暂时不能再收徒。',
    insufficient_funds: '你的钱不够支付这笔费用。',
    insufficient_rep: '声望不足；先攒够声望再来。',
    already_in_sect: '你已经在门派中，不能重复加入或另立门户。',
    already_disciple: '对方已经是门派弟子了。',
    disciple_not_found: '门派名册里没有这名弟子。',
    favor_too_low: '对方对你好感不足，还不愿拜入门下。',
    trust_too_low: '对方对你的信任不足，尚不能收为弟子。',
    withdraw_over_cap: '提取金额超过可提上限。',
    treasury_reserve: '金库需保留底金，不能全部取走。',
    insufficient_treasury: '门派金库余额不足。',
    already_settled_today: '今日俸禄已经发放，明日再来。',
    arrears_too_high: '欠饷过高，对方已经离开门派。',
    'bad_arg:day': '天数参数不合法。',
    'bad_arg:name': '门派名称不合法。',
    'bad_arg:sect_id': '门派编号参数不合法。',
    invalid_sect_name: '门派名称不合法；请换一个可用的名称。',
    sect_name_taken: '这个门派名称已被使用。',
    sect_not_found: '找不到这个门派。',
    no_disciples: '门派里还没有弟子。',
    target_in_sect: '对方已经另有门派。',
    target_is_player: '不能对玩家角色执行该门派操作。',
    target_not_disciple: '对方不是本门弟子。',
    intent_not_found: '没有找到这个请求；请刷新后重试。',
    readonly_mode: '只读模式不支持该动作；请用可写模式启动。',
    world_important: '你所在或相邻的地点出现了重要动静；留意「世界动态」。'
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
    cityMap: {
      data: null,      // GET /api/world/map 的完整邻接响应
      loading: false,
      error: '',       // 读取失败 / 布局校验失败的玩家可读原因
      retryAt: 0       // 失败后 30 秒内不重复请求，期间不画假图
    },
    selected: null,
    busy: false,
    pollTimer: null,
    feedTimer: null,
    feed: {
      rows: [],
      failed: false,
      loaded: false,
      inflight: false,
      announced: {}   // tick|summary -> true，重要动静只提示一次
    },
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
    commerce: {
      account: '',
      tab: 'shop',
      qty: {},          // buy:item / sell:item -> 用户正在输入的数量（刷新时保留）
      pos: {},          // 雇工岗位：target id -> position
      wageSet: false,   // 用户是否亲手改过日薪（没改时跟随岗位市价）
      consume: '',      // 「买一份现吃」的体力变化文案
      hire: '',
      escort: '',
      shopops: {
        qty: {},        // 进货：item -> 用户输入数量
        price: {},      // 改价：item -> 用户输入价格
        busy: {},       // 动作键 -> 是否在途（防重复点击）
        report: null,   // 最近一次 shop_report 的 days
        reportDetails: null, // shop_report 顶层 stock / stockouts / price_overrides
        reportLoaded: false,
        reportUnavailable: false
      },
      dismissTarget: '',
      deferred: null    // 正在输入表单时暂存的 state，失焦后补渲染
    },
    marriage: {
      account: '',
      target: '',      // 当前选中的求婚对象 id
      result: '',      // 求婚/和离结果文案（跨轮询保留）
      resultBad: false
    },
    sect: {
      account: '',
      name: '',          // 立派表单：门派名
      doctrine: '',      // 立派表单：宗旨
      joinTarget: '',    // 加入已有门派：门派 id 或驻地 id
      deposit: '',       // 金库存入金额
      withdraw: '',      // 金库取出金额
      result: '',
      resultBad: false,
      busy: {},          // 写动作在途：key -> true
      confirmTarget: ''  // 驱逐二次确认对象
    },
    ui: {
      errors: [],
      foldsReady: false,
      toastTimer: null,
      recovering: null   // 单飞离线自愈：同一时刻只允许一次 enter 恢复
    },
    hb: {
      timer: null,
      inflight: false
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

  /* 离线自愈：调用 enter 恢复在线（服务端保留实体当前位置，不瞬移），成功后再重试原请求一次。
     并发多请求共用同一次恢复，避免同时打多个 enter。 */
  function recoverOnline(account) {
    if (state.ui.recovering) return state.ui.recovering;
    const acct = String(
      (state.auth.access && state.auth.username) || account || ''
    ).trim().toLowerCase();
    if (!acct) return Promise.resolve(false);
    const p = (async function () {
      const player = (state.st && state.st.player) || {};
      const body = { account_id: acct };
      // 带上已有身份/名字：避免 identity_locked 或重名拒绝。
      if (player.archetype) body.archetype = player.archetype;
      if (player.name) body.display_name = player.name;
      let res;
      try { res = await API.enter(body); } catch (e) { return false; }
      const d = (res && res.data) || {};
      const code = String(d.reject_reason || d.code || '');
      if (!(d.ok === true || code === 'already_online')) return false;
      try {
        const st = await API.state(acct);
        if (st && st.ok && st.player) applyState(st);
      } catch (e) { /* 恢复已成功；状态刷新失败不阻塞动作重试 */ }
      startHeartbeat();
      return true;
    })();
    state.ui.recovering = p;
    p.then(function () {
      if (state.ui.recovering === p) state.ui.recovering = null;
    }, function () {
      if (state.ui.recovering === p) state.ui.recovering = null;
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
    // 离线自愈：任何写动作收到 player_offline，先 enter 恢复，再重试该动作一次（最多一次）。
    const offlineCode = String((data && (data.reject_reason || data.code)) || '');
    if (offlineCode === 'player_offline' && !authEndpoint && !opts.offlineRetried
        && url !== '/api/player/enter') {
      const account = (state.auth.access && state.auth.username) ||
        (body && body.account_id) || '';
      const recovered = await recoverOnline(account);
      if (recovered) {
        return postJSON(url, body, Object.assign({}, opts, { offlineRetried: true }));
      }
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
    // 兜底也不把原始 code 露给玩家：前端原因码表 + 服务端 reason_text 之外一律通用文案。
    if (key) return '操作没有完成；服务端没有给出中文说明，请刷新后再试。';
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
    stopHeartbeat();
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
    syncEntryAccountLock();
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
        const node = document.querySelector('#textMap button[data-city-kind="near"][data-city-id="' + need + '"]') ||
          document.querySelector('#textMap button[data-city-kind="near"]');
        return { node: node, title: '③ 移动到委托人处',
          why: '「' + (quest.name || '这条委托') + '」在' + locationLabel(need) +
            '；点地图上绿色地名逐段走过去，到了才能交付。' };
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
    syncEntryAccountLock();
  }

  /* 已登录时账号是令牌的唯一事实源：自动填入并锁成只读，避免手填成别人后 403/身份错位。 */
  function syncEntryAccountLock() {
    const input = el('accountId');
    const gen = el('genId');
    const hint = el('accountHint');
    if (!input) return;
    const logged = !!(state.auth.access && state.auth.username) && state.auth.mode !== 'legacy';
    if (logged) {
      input.value = state.auth.username;
      input.readOnly = true;
      input.setAttribute('aria-readonly', 'true');
      if (gen) { gen.disabled = true; gen.classList.add('hidden'); }
      if (hint) {
        hint.innerHTML = '已锁定为当前账号 <code>' + escapeHtml(state.auth.username) +
          '</code>；要换账号请先右上角退出登录。';
      }
    } else {
      input.readOnly = false;
      input.removeAttribute('aria-readonly');
      if (gen) { gen.disabled = false; gen.classList.remove('hidden'); }
      if (hint) {
        hint.innerHTML = '服务端会映射为实体 <code>player:&lt;账号&gt;</code>；客户端不自带实体 id。';
      }
    }
  }

  function selectArchetype(value) {
    const nodes = Array.prototype.slice.call(el('archetypes').querySelectorAll('.archetype'));
    nodes.forEach(function (node) {
      const hit = node.getAttribute('data-value') === value;
      node.classList.toggle('selected', hit);
      const input = node.querySelector('input');
      if (input) input.checked = hit;
    });
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

  /* 弹层出口：已进入世界就直接回到主界面，没有可关的也给出明确去处，避免落在分支里死锁。 */
  function closeEntry() {
    showEntry(false);
    if (state.st) {
      render(state.st);
      maybeStartGuide();
      loadFeed();
      return;
    }
    toast('你还没进入世界；点右上角「换人」可重新打开进入面板。', true);
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
    const acct = String(st.account_id || p.account_id || '');
    if (state.commerce.account && state.commerce.account !== acct) {
      // 换账号时清掉上一位玩家留下的数量、雇工与押镖文案。
      state.commerce = { account: acct, tab: 'shop', qty: {}, pos: {}, wageSet: false, consume: '', hire: '', escort: '',
        shopops: { qty: {}, price: {}, busy: {}, report: null, reportDetails: null, reportLoaded: false, reportUnavailable: false },
        dismissTarget: '', deferred: null };
    } else {
      state.commerce.account = acct;
    }
    if (state.marriage.account && state.marriage.account !== acct) {
      // 换账号时清掉上一段婚约的选择与结果，避免把别人的配偶带进界面。
      state.marriage = { account: acct, target: '', result: '', resultBad: false };
    } else {
      state.marriage.account = acct;
    }
    if (state.sect.account && state.sect.account !== acct) {
      // 换账号时清掉门派名册/表单/在途状态，绝不把上一位玩家的门派带进界面。
      state.sect = { account: acct, name: '', doctrine: '', joinTarget: '', deposit: '',
        withdraw: '', result: '', resultBad: false, busy: {}, confirmTarget: '' };
    } else {
      state.sect.account = acct;
    }
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
    startHeartbeat();
    render(st);
    return true;
  }

  /* 前台停留保活：20s 一次、只读时钟刷新；切后台/断网时暂停，回到前台或恢复网络立刻补一次。 */
  function startHeartbeat() {
    if (state.hb.timer) return;
    state.hb.timer = setInterval(heartbeatTick, HEARTBEAT_INTERVAL_MS);
  }

  function stopHeartbeat() {
    if (state.hb.timer) {
      clearInterval(state.hb.timer);
      state.hb.timer = null;
    }
  }

  async function heartbeatTick() {
    if (!state.session || !state.session.accountId) return;
    if (!state.auth.access) return;          // legacy 匿名模式不保活
    if (document.hidden) return;             // 后台/休眠不保活，恢复可见时立即补
    if (state.hb.inflight) return;
    state.hb.inflight = true;
    try {
      await API.heartbeat(state.session.accountId);
    } catch (e) { /* 断网：下一次心跳或在线事件再补；离线超时会由动作的 player_offline 自愈 */ }
    finally {
      state.hb.inflight = false;
    }
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
        const effAccount = (state.auth.access && state.auth.username) ? state.auth.username : accountId;
        if (r.code === 'already_online' || r.code === 'already_entered') {
          // “已在世界内”是成功，不是错误：关弹窗、进主界面、拉状态，并回显位置/钱/tick。
          const st = await loadState(effAccount);
          const p = (st && st.player) || {};
          const wallet = (p.wallet && p.wallet.money != null) ? p.wallet.money : 0;
          showEntry(false);
          render(st);
          maybeStartGuide();
          loadFeed();
          toast('你已经在这个世界里：' + (p.location_name || p.location || '—') +
            ' · ' + wallet + ' 文 · tick ' + (st.tick != null ? st.tick : '—'));
          btn.textContent = '进入画面';
          btn.setAttribute('data-open', '1');
          return;
        }
        if (r.code === 'identity_locked') {
          // 回显该账号已有身份，并给一键“用原身份进入”，不让玩家猜。
          let existing = '';
          try {
            const st = await API.state(effAccount);
            if (st && st.ok && st.player) existing = String(st.player.archetype || '');
          } catch (err) { existing = ''; }
          const box = el('entryError');
          const msg = existing
            ? ('该账号已以『' + existing + '』进入过世界，请选「' + existing + '」。身份进入后不可更改。')
            : (r.text || '身份进入后不可更改；请用原身份重新进入。');
          box.innerHTML = '<div>' + escapeHtml(msg) + '</div>';
          if (existing) {
            const pick = document.createElement('button');
            pick.type = 'button';
            pick.className = 'ghost';
            pick.id = 'useOriginalArchetype';
            pick.textContent = '用原身份进入（' + existing + '）';
            pick.addEventListener('click', function () {
              selectArchetype(existing);
              doEnter();
            });
            box.appendChild(pick);
          }
          box.classList.remove('hidden');
          recordError('进入世界', r.code, msg);
          btn.textContent = '进入世界';
          return;
        }
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
    // 进入世界后立刻对齐一次世界动态，让「附近重要动静」不必等到下个轮询。
    loadFeed();
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
    renderSpouseBlock(st);
    renderCommerce(st);
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
    el('stClock').textContent = clockOf(st.clock_tick != null ? st.clock_tick : st.tick).label;
    const online = (st.online_players || []).length;
    el('stOnline').textContent = String(online);
    document.title = clockOf(st.clock_tick != null ? st.clock_tick : st.tick).label + ' · 入世 · 世界门面';
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

  /* 字符地图按显示格计算宽度：中文占 2 格，线条/ASCII 占 1 格。 */
  function mapCharWide(ch) {
    const c = ch.codePointAt(0);
    return (c >= 0x1100 && c <= 0x115F) ||
      (c >= 0x2E80 && c <= 0xA4CF) ||
      (c >= 0xAC00 && c <= 0xD7A3) ||
      (c >= 0xF900 && c <= 0xFAFF) ||
      (c >= 0xFE30 && c <= 0xFE4F) ||
      (c >= 0xFF00 && c <= 0xFF60) ||
      (c >= 0xFFE0 && c <= 0xFFE6);
  }

  function mapWidth(text) {
    let width = 0;
    Array.from(String(text == null ? '' : text)).forEach(function (ch) {
      width += mapCharWide(ch) ? 2 : 1;
    });
    return width;
  }

  /* 全城固定布局：行列都按“显示格”计（中文占 2 格）。
     坐标是人工排版，不来自后端；渲染前必须用 /api/world/map 校验节点与边集合，
     对不上就报错，不按猜测补画。 */
  const CITY_LAYOUT = {
    rows: 9,
    cols: 30,
    nodes: [
      { id: 'teahouse',      name: '茶棚',   row: 0, col: 14, mark: [1, 16] },
      { id: 'market',        name: '集市',   row: 2, col: 14, mark: [2, 18] },
      { id: 'smithy',        name: '铁匠铺', row: 4, col: 4,  mark: [3, 4] },
      { id: 'pharmacy',      name: '药铺',   row: 4, col: 20, mark: [3, 20] },
      { id: 'academy',       name: '书院',   row: 4, col: 26, mark: [3, 28] },
      { id: 'escort_agency', name: '镖局',   row: 6, col: 4,  mark: [7, 4] },
      { id: 'inn',           name: '客栈',   row: 6, col: 12, mark: [5, 12] },
      { id: 'temple',        name: '城隍庙', row: 6, col: 22, mark: [5, 22] },
      { id: 'ferry',         name: '河渡',   row: 8, col: 12, mark: [7, 12] }
    ]
  };

  /* 11 条无向边的字符路径；路径坐标与 CITY_LAYOUT 一起构成固定排版。 */
  const CITY_EDGES = [
    { from: 'teahouse', to: 'market', path: [['│', 1, 15]] },
    { from: 'market', to: 'inn', path: [['│', 3, 15], ['│', 4, 15], ['│', 5, 15]] },
    { from: 'market', to: 'academy', path: [
      ['─', 2, 19], ['─', 2, 20], ['─', 2, 21], ['─', 2, 22],
      ['─', 2, 23], ['─', 2, 24], ['─', 2, 25], ['┐', 2, 26], ['│', 3, 26]
    ] },
    { from: 'academy', to: 'temple', path: [['│', 5, 27]] },
    { from: 'pharmacy', to: 'temple', path: [['│', 5, 23]] },
    { from: 'market', to: 'pharmacy', path: [['│', 3, 18], ['└', 4, 18], ['─', 4, 19]] },
    { from: 'market', to: 'smithy', path: [
      ['┌', 2, 10], ['─', 2, 11], ['─', 2, 12], ['─', 2, 13], ['│', 3, 10], ['┘', 4, 10]
    ] },
    { from: 'smithy', to: 'escort_agency', path: [['│', 5, 5]] },
    { from: 'escort_agency', to: 'ferry', path: [
      ['│', 7, 5], ['└', 8, 5], ['─', 8, 6], ['─', 8, 7],
      ['─', 8, 8], ['─', 8, 9], ['─', 8, 10], ['─', 8, 11]
    ] },
    { from: 'ferry', to: 'inn', path: [['│', 7, 13]] },
    { from: 'inn', to: 'temple', path: [
      ['─', 6, 16], ['─', 6, 17], ['─', 6, 18], ['─', 6, 19], ['─', 6, 20], ['─', 6, 21]
    ] }
  ];

  function cityEdgeKey(a, b) {
    return [String(a), String(b)].sort().join('|');
  }

  function cityNodeById(map, id) {
    const nodes = (map && map.nodes) || [];
    for (let i = 0; i < nodes.length; i++) {
      if (String(nodes[i].id) === String(id)) return nodes[i];
    }
    return null;
  }

  /* 校验：地点/边集合与固定布局逐项一致；任何一项对不上都不画图。 */
  function validateCityMap(map) {
    const problems = [];
    if (!map || map.ok === false) problems.push('接口返回不可用');
    const nodes = (map && map.nodes) || [];
    const edges = (map && map.edges) || [];
    const layout = CITY_LAYOUT.nodes;
    if (nodes.length !== layout.length) {
      problems.push('地点数：接口 ' + nodes.length + '，布局 ' + layout.length);
    }
    if (edges.length !== CITY_EDGES.length) {
      problems.push('边数：接口 ' + edges.length + '，布局 ' + CITY_EDGES.length);
    }
    const counts = (map && map.counts) || {};
    if (counts.locations != null && counts.locations !== nodes.length) {
      problems.push('counts.locations=' + counts.locations + ' 与 nodes=' + nodes.length + ' 不一致');
    }
    if (counts.edges != null && counts.edges !== edges.length) {
      problems.push('counts.edges=' + counts.edges + ' 与 edges=' + edges.length + ' 不一致');
    }
    layout.forEach(function (slot) {
      const node = cityNodeById(map, slot.id);
      if (!node) {
        problems.push('缺少地点 ' + slot.id);
        return;
      }
      const name = String(node.name || '');
      if (!name) {
        problems.push('地点 ' + slot.id + ' 没有名称');
      } else if (mapWidth(name) !== mapWidth(slot.name)) {
        problems.push('地点 ' + slot.id + ' 名称宽度 ' + mapWidth(name) +
          '，布局预留 ' + mapWidth(slot.name));
      }
      const exits = (node.exits || node.neighbors || []).length;
      const degree = edges.filter(function (edge) {
        return String(edge.from) === slot.id || String(edge.to) === slot.id;
      }).length;
      if (exits !== degree) {
        problems.push('地点 ' + slot.id + ' 出口 ' + exits + ' 与边 ' + degree + ' 不一致');
      }
    });
    const actualEdges = {};
    edges.forEach(function (edge) {
      actualEdges[cityEdgeKey(edge.from, edge.to)] = true;
    });
    const layoutEdges = {};
    CITY_EDGES.forEach(function (edge) {
      layoutEdges[cityEdgeKey(edge.from, edge.to)] = true;
    });
    Object.keys(layoutEdges).forEach(function (key) {
      if (!actualEdges[key]) problems.push('缺少边 ' + key);
    });
    Object.keys(actualEdges).forEach(function (key) {
      if (!layoutEdges[key]) problems.push('多出边 ' + key);
    });
    return { ok: problems.length === 0, problems: problems };
  }

  function cityPlaceText(grid, meta, row, col, text, value) {
    let c = col;
    Array.from(text).forEach(function (ch) {
      if (grid[row][c] !== ' ') {
        throw new Error('布局重叠：第 ' + row + ' 行第 ' + c + ' 列已有内容');
      }
      grid[row][c] = ch;
      meta[row][c] = value;
      if (mapCharWide(ch)) {
        if (grid[row][c + 1] !== ' ') {
          throw new Error('布局重叠：第 ' + row + ' 行第 ' + (c + 1) + ' 列已有内容');
        }
        grid[row][c + 1] = '';
        c += 2;
      } else {
        c += 1;
      }
    });
  }

  function cityPlaceCell(grid, meta, row, col, ch, value) {
    if (grid[row][col] !== ' ') {
      throw new Error('布局重叠：第 ' + row + ' 行第 ' + col + ' 列已有内容');
    }
    grid[row][col] = ch;
    meta[row][col] = value;
  }

  function buildCityView(map, currentId) {
    const grid = [];
    const meta = [];
    for (let r = 0; r < CITY_LAYOUT.rows; r++) {
      grid.push(new Array(CITY_LAYOUT.cols).fill(' '));
      meta.push(new Array(CITY_LAYOUT.cols).fill(null));
    }
    const current = cityNodeById(map, currentId);
    if (!current) throw new Error('当前地点 ' + currentId + ' 不在全城图里');
    const near = {};
    (current.neighbors || current.exits || []).forEach(function (id) {
      near[String(id)] = true;
    });

    CITY_LAYOUT.nodes.forEach(function (slot) {
      const node = cityNodeById(map, slot.id);
      const kind = slot.id === currentId ? 'self' : (near[slot.id] ? 'near' : 'far');
      const label = String(node.name || slot.name);
      cityPlaceText(grid, meta, slot.row, slot.col, label,
        { node: kind, id: slot.id, name: label });
      if (slot.id === currentId) {
        cityPlaceCell(grid, meta, slot.mark[0], slot.mark[1], '◆', { node: 'mark', id: slot.id });
      }
    });

    CITY_EDGES.forEach(function (edge) {
      const on = edge.from === currentId || edge.to === currentId;
      edge.path.forEach(function (cell) {
        cityPlaceCell(grid, meta, cell[1], cell[2], cell[0],
          { linkOn: on, edge: edge.from + '|' + edge.to });
      });
    });

    return { grid: grid, meta: meta, current: current, near: near };
  }

  /* 逐格渲染：每格固定宽度，中文整字占 2 格；不依赖字体对 CJK/线条的等宽假设。
     地名整体渲染成一个原生 button：可 Tab 聚焦、Enter/Space 触发，
     真实一步可达（near）才发移动；self / far 只给中文原因，不发请求。 */
  function renderCityGrid(view) {
    return view.grid.map(function (row, r) {
      let html = '';
      let c = 0;
      while (c < row.length) {
        const m = view.meta[r][c] || {};
        if (m.id && (m.node === 'self' || m.node === 'near' || m.node === 'far')) {
          const width = mapWidth(m.name || '');
          const kind = m.node;
          const tip = kind === 'self'
            ? '你在这里'
            : (kind === 'near' ? '一步可走：每步 ' + MOVE_STAMINA_COST + ' 力 · 1 小时' : '不在一步之内');
          let inner = '';
          for (let k = 0; k < width; k++) {
            const ch = row[c + k];
            if (ch === '') continue;
            inner += '<span class="map-cell' + (mapCharWide(ch) ? ' wide' : '') + '">' +
              escapeHtml(ch) + '</span>';
          }
          html += '<button type="button" class="map-node node-' + kind +
            '" data-node-id="' + escapeHtml(m.id) + '" data-city-id="' + escapeHtml(m.id) +
            '" data-city-kind="' + kind + '" data-city-name="' + escapeHtml(m.name || '') +
            '" aria-label="' + escapeHtml((m.name || '') + '，' + tip) +
            '" title="' + escapeHtml(tip) + '"' +
            (kind === 'near' ? ' data-exit-id="' + escapeHtml(m.id) + '"' : '') +
            '>' + inner + '</button>';
          c += width;
          continue;
        }
        const ch = row[c];
        if (ch !== '') {
          const cls = ['map-cell'];
          if (mapCharWide(ch)) cls.push('wide');
          if (m.node === 'mark') cls.push('node-mark');
          if (m.linkOn) cls.push('link-on');
          let attrs = '';
          if (m.id) attrs += ' data-node-id="' + escapeHtml(m.id) + '"';
          if (m.edge) attrs += ' data-edge="' + escapeHtml(m.edge) + '"';
          html += '<span class="' + cls.join(' ') + '"' + attrs + '>' +
            escapeHtml(ch) + '</span>';
        }
        c += 1;
      }
      return '<div class="map-row">' + html + '</div>';
    }).join('');
  }

  function renderCityError(text) {
    el('mapHint').textContent = '全城图不可用';
    el('mapStats').textContent = '未按猜测作图';
    el('textMap').innerHTML = '<div class="map-error">' + escapeHtml(text) + '</div>';
  }

  /* 全城图是静态公共数据：进世界后取一次；失败 30 秒后重试，期间不做假图。 */
  function ensureCityMap() {
    const cm = state.cityMap;
    if (cm.loading || cm.data) return;
    if (cm.error && Date.now() < cm.retryAt) return;
    cm.loading = true;
    cm.error = '';
    API.worldMap().then(function (map) {
      cm.loading = false;
      const check = validateCityMap(map || {});
      if (!check.ok) {
        cm.error = '全城图数据与固定布局不一致：' + check.problems.join('；') + '。未画图。';
        cm.retryAt = Date.now() + 30000;
      } else {
        cm.data = map;
      }
      if (state.st) renderPlace(state.st);
    }, function (err) {
      cm.loading = false;
      cm.error = '全城图读取失败：' + ((err && (err.message || err.code)) || '请求失败') + '。未按猜测作图。';
      cm.retryAt = Date.now() + 30000;
      if (state.st) renderPlace(state.st);
    });
  }

  function renderPlace(st) {
    const p = st.player || {};
    const loc = st.location || {};
    el('locId').textContent = loc.id || '—';
    el('placeName').textContent = loc.name || '未知地点';
    el('placeMe').textContent = '你在这里：' + (p.name || '') +
      (p.role ? '（' + p.role + '）' : '') + ' · tick ' + st.tick;

    /* 全城图：固定人工布局 + 真实 /api/world/map 邻接校验；对不上就报错，不按猜测作图。 */
    const cm = state.cityMap;
    if (!cm.data && !cm.loading && (!cm.error || Date.now() >= cm.retryAt)) ensureCityMap();
    if (cm.data) {
      try {
        const view = buildCityView(cm.data, loc.id || '');
        const nearCount = Object.keys(view.near).length;
        const counts = cm.data.counts || {};
        const nLoc = counts.locations == null ? cm.data.nodes.length : counts.locations;
        const nEdge = counts.edges == null ? cm.data.edges.length : counts.edges;
        el('mapHint').textContent = '一步可去 ' + nearCount + ' 处';
        el('mapStats').textContent = '地点 ' + nLoc + ' 处 · 无向边 ' + nEdge + ' 条' +
          (counts.directed_edges == null ? '' : ' · 有向方向 ' + counts.directed_edges + ' 个') +
          ' · ◆ 你在这里：' + (view.current.name || loc.name || '—') +
          ' · 一步可走 ' + nearCount + ' 处 · 每步 ' + MOVE_STAMINA_COST + ' 力 · 1 小时';
        el('textMap').innerHTML = '<div class="map-grid">' + renderCityGrid(view) + '</div>';
        el('mapNote').textContent = '◆ 为当前所在地；绿色地名可点即走，灰字地名不在一步之内。' +
          '寻路与两跳路线暂不可用；区域名后端为 null 时不显示区域层。';
      } catch (err) {
        renderCityError('全城图布局失败：' + ((err && err.message) || '未知错误') + '。未画图。');
      }
    } else if (cm.error) {
      renderCityError(cm.error);
    } else {
      el('mapHint').textContent = '读取中…';
      el('mapStats').textContent = '—';
      el('textMap').innerHTML = '<div class="map-loading">正在读取 /api/world/map…</div>';
    }
  }

  /* 地图点击：near = 真实一步可达，直接发移动；self / far 只给中文原因，不发请求。 */
  function onCityNodeClick(ev) {
    const btn = ev.target && ev.target.closest ? ev.target.closest('.map-node') : null;
    if (!btn) return;
    const id = btn.getAttribute('data-city-id') || '';
    const kind = btn.getAttribute('data-city-kind') || '';
    const name = btn.getAttribute('data-city-name') || id;
    if (kind === 'self') {
      setMoveHint('你已经在这里：' + name + '。');
      return;
    }
    if (kind === 'near') {
      doMove({ id: id, name: name });
      return;
    }
    setMoveHint('不在一步之内：' + name + '，需途经他处。');
  }

  function setMoveHint(text, tone) {
    const hint = el('moveHint');
    if (!hint) return;
    hint.textContent = text;
    hint.style.color = tone === 'bad' ? '#9b3b2f' : (tone === 'ok' ? '#3f6b57' : '');
  }

  async function doMove(exit) {
    if (state.busy) {
      setMoveHint('正在移动，请稍候…');
      return;
    }
    if (!state.session || !state.session.accountId) {
      toast('先进入世界，才能移动。', true);
      showEntry(true);
      return;
    }
    if (!requireAuthForWrite()) return;
    const beforeTick = state.st ? Number(state.st.tick) : 0;
    state.busy = true;
    setMoveHint('正在移动到 ' + exit.name + '…');
    try {
      const res = await API.action(state.session.accountId, 'move', { to: exit.id }, beforeTick);
      const data = res.data || {};
      if (res.authRequired || res.status === 401) return;
      if (res.status === 409 || data.ok === false || res.status === 0) {
        const r = notifyError('移动 ' + exit.name, data, res.status);
        setMoveHint(r.text, 'bad');
        return;
      }
      if (data.status === 'proposed' || data.readonly) {
        setMoveHint('只读模式：已提交移动提案 ' + (data.intent_id || '') + '（未落地）');
        pushLog('proposal', '移动提案：' + exit.name, data.intent_id || '');
        return;
      }
      setMoveHint('移动完成：' + exit.name +
        ' · event_seq ' + (data.event_seq == null ? '?' : data.event_seq) +
        ' · tick ' + (data.tick == null ? '?' : data.tick) + ' → ' + (data.next_tick == null ? '?' : data.next_tick), 'ok');
      pushLog('ok', '移动到 ' + exit.name + ' · event_seq ' + (data.event_seq == null ? '?' : data.event_seq),
        'tick ' + (data.tick == null ? '?' : data.tick));
      await loadState(state.session.accountId);
      guideAdvanceOn('move');
    } catch (err) {
      setMoveHint('移动请求失败：' + (err.message || err), 'bad');
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
      const spouse = isSpouseNpc(npc, st);
      btn.className = 'npc' + (state.selected && state.selected.id === npc.id ? ' selected' : '') +
        (spouse ? ' spouse' : '');
      btn.innerHTML =
        '<span class="avatar role-' + escapeHtml(npc.role) + '">' + escapeHtml(firstChar(npc.name)) + '</span>' +
        '<span class="npc-main"><span class="npc-name">' + escapeHtml(npc.name) +
        '<span class="npc-role">' + escapeHtml(npc.role) + '</span>' +
        (spouse ? '<span class="spouse-badge">配偶</span>' : '') + '</span>' +
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
    const spouse = isSpouseNpc(npc, state.st);
    box.classList.toggle('spouse', spouse);
    box.innerHTML =
      '<span class="avatar role-' + escapeHtml(npc.role || '') + '">' + escapeHtml(firstChar(npc.name)) + '</span>' +
      '<div><div class="who">' + escapeHtml(npc.name) +
      (spouse ? '（配偶）<span class="spouse-badge">配偶</span>' : '') + '</div>' +
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
      const spouse = isSpouseName(msg.from);
      const fromLabel = spouse
        ? spouseNameOf(state.st)
        : (npcDisplayName(msg.from) || String(msg.from || 'NPC'));
      node.className = 'reply-item' + (spouse ? ' spouse' : '');
      node.innerHTML =
        '<div class="reply-head"><b>' + escapeHtml(fromLabel) +
        (spouse ? '（配偶）' : '') + '</b>' +
        '<span class="pill">' + (spouse ? '配偶回应' : '世界回应') + '</span><small>tick ' +
        escapeHtml(msg.tick == null ? '?' : msg.tick) +
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
      const withSpouse = isSpouseNpc(npc, state.st);
      box.classList.add('ok');
      box.classList.toggle('spouse', withSpouse);
      box.innerHTML = '<b>对话已执行' + (withSpouse ? '（配偶）' : '') + '</b> <code>' +
        escapeHtml(data.intent_id || '') + '</code>' +
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
        : row.kind === 'rejected' ? '被拒'
          : row.kind === 'world' ? '世界动态' : '你';
      node.innerHTML = '<div class="log-meta"><span>' +
        escapeHtml(row.meta || '') + '</span><span class="tag">' + escapeHtml(tag) + '</span></div>' +
        '<div>' + escapeHtml(row.text || '') + '</div>';
      box.appendChild(node);
    });
    box.scrollTop = 0;
  }

  /* ------------------------------------------------------------- 世界动态 */
  /* 后端契约（GET /api/world/feed?limit=20）：最近世界事件数组，字段
     tick/actor/verb/place/summary/important。这里对包装形状做兼容解析：
     裸数组或 {events|feed|items|rows} 都接受。 */
  function parseFeed(payload) {
    const raw = Array.isArray(payload) ? payload
      : (payload && (payload.events || payload.feed || payload.items || payload.rows)) || [];
    if (!Array.isArray(raw)) return [];
    return raw.filter(function (x) { return x && typeof x === 'object'; })
      .map(function (x) {
        return {
          tick: Number(x.tick) || 0,
          actor: String(x.actor || ''),
          verb: String(x.verb || ''),
          place: String(x.place || ''),
          summary: String(x.summary || ''),
          important: !!x.important
        };
      })
      .filter(function (x) { return x.summary || (x.actor && x.verb); })
      .sort(function (a, b) { return b.tick - a.tick; })
      .slice(0, FEED_LIMIT);
  }

  function renderFeed() {
    const list = el('feedList');
    if (!list) return;
    const rows = state.feed.rows || [];
    const atTop = list.scrollTop <= 1;
    const prevHeight = list.scrollHeight;
    if (rows.length) {
      list.innerHTML = rows.map(function (r) {
        const line = '· [' + r.tick + '] ' + (r.actor || '某处') +
          (r.place ? ' 在 ' + r.place : '') + '：' + feedBody(r);
        return '<div class="feed-row' + (r.important ? ' important' : '') +
          '" data-feed-tick="' + escapeHtml(r.tick) + '" data-feed-important="' +
          (r.important ? '1' : '0') + '">' + escapeHtml(line) + '</div>';
      }).join('');
    } else {
      list.innerHTML = '<div class="feed-empty">' +
        (state.feed.loaded ? '世界暂时安静' : '正在听世界的动静…') + '</div>';
    }
    // 固定高度容器：用户停在顶部时跟随最新一条；滚动到下方时不因新条目插入而跳动。
    if (atTop) list.scrollTop = 0;
    else list.scrollTop += list.scrollHeight - prevHeight;
    el('feedCount').textContent = String(rows.length);
    const status = el('feedStatus');
    status.textContent = '世界暂时安静';
    status.classList.toggle('hidden', !state.feed.failed);
  }

  /* 重要动静只用现有提示体系（toast + 本地操作记录），不新造弹层/原因码通道。 */
  function announceFeed(rows) {
    const loc = (state.st || {}).location || {};
    if (!loc.name) return;
    const near = {};
    near[loc.name] = true;
    (loc.exits || []).forEach(function (x) { if (x && x.name) near[x.name] = true; });
    rows.forEach(function (r) {
      if (!r.important || !near[r.place]) return;
      const key = String(r.tick) + '|' + r.summary;
      if (state.feed.announced[key]) return;
      state.feed.announced[key] = true;
      const text = '附近动静：' + (r.summary || (r.actor + '在' + r.place));
      toast(text, false);
      pushLog('world', text, '原因码 world_important · tick ' + r.tick);
    });
  }

  async function loadFeed() {
    if (state.feed.inflight) return;
    state.feed.inflight = true;
    try {
      const data = await API.feed(FEED_LIMIT);
      state.feed.rows = parseFeed(data);
      state.feed.failed = false;
      state.feed.loaded = true;
      renderFeed();
      announceFeed(state.feed.rows);
    } catch (err) {
      // 降级：feed 失败不弹红字、不写错误历史、不动主状态，只显示「世界暂时安静」。
      state.feed.failed = true;
      state.feed.loaded = true;
      renderFeed();
    } finally {
      state.feed.inflight = false;
    }
  }

  /* ------------------------------------------------------------- 任务板 */
  function renderToday(st) {
    const t = st.today || {};
    el('todayTick').textContent = t.tick != null
      ? ('tick ' + t.tick + ' · ' + clockOf(t.clock_tick != null ? t.clock_tick : t.tick).label)
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
        const tier = tierOf(b.shop_type);
        const total = tier ? tier.rent + tier.stock : Number(b.cost || 0);
        return tierLabel(b.shop_type) + ' 共需 ' + total + ' 文' +
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
        const money = Number((((st.player || {}).wallet) || {}).money || 0);
        biz.forEach(function (b) {
          const tier = tierOf(b.shop_type);
          const total = tier ? tier.rent + tier.stock : Number(b.cost || 0);
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'ghost today-act';
          btn.textContent = '开' + tierLabel(b.shop_type) + '（' + total + ' 文）';
          const reason = b.blocked ? '商会护盘期间暂不可开'
            : (money < total ? '还差 ' + (total - money) + ' 文' : '');
          if (reason) {
            btn.disabled = true;
            btn.title = reason;
          } else {
            btn.addEventListener('click', function () { doOpenShop(b.shop_type); });
          }
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

  /* ------------------------------------------------------------- 商业与经营 */
  function commerceEditing() {
    const active = document.activeElement;
    const block = el('commerceBlock');
    if (!active || !block || !block.contains(active)) return false;
    const tag = active.tagName;
    return tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA';
  }

  function setCommerceTab(tab) {
    const tabs = el('commerceTabs');
    if (!tabs) return;
    const valid = ['shop', 'business', 'hire', 'escort', 'sect', 'marriage', 'soon'];
    const cur = valid.indexOf(tab) >= 0 ? tab : 'shop';
    state.commerce.tab = cur;
    Array.prototype.forEach.call(tabs.querySelectorAll('[data-tab]'), function (btn) {
      const on = btn.getAttribute('data-tab') === cur;
      btn.classList.toggle('active', on);
      btn.setAttribute('aria-selected', on ? 'true' : 'false');
      btn.setAttribute('tabindex', on ? '0' : '-1');
    });
    valid.forEach(function (key) {
      const pane = el('pane-' + key);
      if (pane) pane.classList.toggle('hidden', key !== cur);
    });
  }

  function ownShopOf(st) {
    const account = String((state.session && state.session.accountId) ||
      (st && st.account_id) || '');
    const shop = (st && st.shop) || {};
    const id = 'shop:player:' + account;
    return (shop.businesses || []).filter(function (b) { return b.id === id; })[0] || null;
  }

  function buyLimitOf(row, money) {
    const stock = Math.max(0, Number(row.stock || 0));
    const cap = row.buy_limit == null ? stock : Number(row.buy_limit);
    const total = Math.max(1, Number(row.total || row.price || 1));
    return Math.max(0, Math.min(stock, cap, Math.floor(money / total)));
  }

  function qtyValue(key, max) {
    const raw = Number(state.commerce.qty[key]);
    const qty = Number.isFinite(raw) && raw > 0 ? Math.floor(raw) : 1;
    return Math.max(1, Math.min(Math.max(1, max), qty));
  }

  function shopRowMeta(row) {
    const name = escapeHtml(row.name || row.item || '物品');
    const tax = Number(row.tax || 0);
    const total = Number(row.total == null ? row.price : row.total);
    const limit = row.buy_limit == null ? '不限' : String(row.buy_limit);
    return '<div class="row-main"><span class="row-name">' + name + '</span>' +
      '<span class="row-meta">单价 ' + escapeHtml(row.price) + ' 文 · 税 ' + escapeHtml(tax) +
      ' 文 · 含税 ' + escapeHtml(total) + ' 文/份 · 库存 ' + escapeHtml(row.stock == null ? 0 : row.stock) +
      ' · 限购 ' + escapeHtml(limit) + (row.business_name ? ' · ' + escapeHtml(row.business_name) : '') +
      '</span></div>';
  }

  function foodRowsOf(st) {
    const rows = ((st && st.shop) || {}).buy || [];
    return rows.filter(function (r) {
      const cat = String(r.category || '');
      return (cat === 'food' || cat === 'drink') && Number(r.stock || 0) > 0;
    }).sort(function (a, b) {
      return Number(a.total || a.price || 0) - Number(b.total || b.price || 0);
    });
  }

  function renderBuyList(st, money) {
    const list = el('buyList');
    const rows = ((st.shop || {}).buy || []);
    const foods = foodRowsOf(st);
    el('buyHint').textContent = rows.length ? (rows.length + ' 种') : '这个地点没有铺子卖货';
    el('consumeBtn').disabled = !foods.length;
    el('consumeBtn').textContent = foods.length ? ('买一份' + (foods[0].name || foods[0].item) + '现吃') : '没有现成吃食';
    if (!state.commerce.consume) {
      el('consumeResult').textContent = foods.length
        ? ('现吃 ' + (foods[0].name || foods[0].item) + '：' + (foods[0].total || foods[0].price) + ' 文/份，体力 +25。')
        : '这里没有现成的吃食。';
    }
    if (!rows.length) {
      list.innerHTML = '<div class="commerce-empty">这里没有可买的货。</div>';
      return;
    }
    list.innerHTML = rows.map(function (row) {
      const key = 'buy:' + row.item;
      const max = buyLimitOf(row, money);
      const qty = qtyValue(key, max);
      const reason = max <= 0 ? '库存或现金不足' : '';
      return '<div class="commerce-row" data-buy="' + escapeHtml(row.item) + '">' +
        shopRowMeta(row) +
        '<div class="row-actions">' +
        '<input type="number" min="1" max="' + max + '" step="1" inputmode="numeric" ' +
        'data-qty="' + escapeHtml(key) + '" value="' + (max > 0 ? qty : 0) + '" aria-label="买几份' +
        escapeHtml(row.name || row.item) + '"' + (max <= 0 ? ' disabled' : '') + ' />' +
        '<button type="button" class="ghost commerce-btn" data-buy-go="' + escapeHtml(row.item) + '"' +
        (max <= 0 ? ' disabled title="' + escapeHtml(reason) + '"' : '') + '>买</button>' +
        (reason ? '<span class="commerce-reason">' + escapeHtml(reason) + '</span>' : '') +
        '</div></div>';
    }).join('');
  }

  function renderSellList(st) {
    const list = el('sellList');
    const rows = ((st.shop || {}).sell || []);
    el('sellHint').textContent = rows.length ? (rows.length + ' 种可卖') : '这里不收你的货';
    if (!rows.length) {
      list.innerHTML = '<div class="commerce-empty">没有可卖的货。</div>';
      return;
    }
    list.innerHTML = rows.map(function (row) {
      const key = 'sell:' + row.item;
      const held = Math.max(0, Number(row.qty == null ? 0 : row.qty));
      const cap = row.sell_limit == null ? held : Number(row.sell_limit);
      const max = Math.max(0, Math.min(held, cap));
      const qty = qtyValue(key, max);
      return '<div class="commerce-row" data-sell="' + escapeHtml(row.item) + '">' +
        '<div class="row-main"><span class="row-name">' + escapeHtml(row.name || row.item) + '</span>' +
        '<span class="row-meta">售价 ' + escapeHtml(row.sell_price == null ? row.price : row.sell_price) +
        ' 文/件 · 持有 ' + escapeHtml(held) + ' · 上限 ' + escapeHtml(cap) + '</span></div>' +
        '<div class="row-actions">' +
        '<input type="number" min="1" max="' + max + '" step="1" inputmode="numeric" ' +
        'data-qty="' + escapeHtml(key) + '" value="' + qty + '" aria-label="卖几件' +
        escapeHtml(row.name || row.item) + '"' + (max <= 0 ? ' disabled' : '') + ' />' +
        '<button type="button" class="ghost commerce-btn" data-sell-go="' + escapeHtml(row.item) + '"' +
        (max <= 0 ? ' disabled title="持有数量不足"' : '') + '>卖</button>' +
        '</div></div>';
    }).join('');
  }

  function blockedTier(st, kind) {
    const rows = ((st.today || {}).business) || [];
    return rows.filter(function (b) { return b.shop_type === kind && b.blocked; })[0] || null;
  }

  function renderOwnedShop(st, own) {
    const info = el('ownShopInfo');
    if (!own) {
      info.textContent = '你还没有店铺。当前位置可以开摊、店面或分号。';
      return;
    }
    let stock = 0;
    let stockKnown = false;
    ((st.shop || {}).buy || []).forEach(function (row) {
      if (row.business === own.id && row.stock != null) {
        stock += Number(row.stock || 0);
        stockKnown = true;
      }
    });
    info.innerHTML = '<b>' + escapeHtml(own.name || '你的店铺') + '</b>' +
      ' · ' + escapeHtml(tierLabel(own.kind)) +
      ' · 店内现金 <b>' + escapeHtml(shopCashOf(st, own)) + '</b> 文' +
      ' · 库存 ' + (stockKnown ? ('<b>' + stock + '</b> 件') : '<span class="commerce-gap">件数未由接口提供</span>') +
      ' · 店址 ' + escapeHtml(own.location || ((st.location || {}).name || ''));
  }

  function renderTierList(st, own, money) {
    const list = el('tierList');
    list.innerHTML = COMMERCE_TIERS.map(function (tier) {
      const total = tier.rent + tier.stock;
      let reason = '';
      if (own) reason = '你已经有店铺了';
      else if (money < total) reason = '还差 ' + (total - money) + ' 文';
      else if (blockedTier(st, tier.kind)) reason = '商会护盘期间暂不可开';
      return '<div class="commerce-row" data-tier-row="' + tier.kind + '">' +
        '<div class="row-main"><span class="row-name">' + escapeHtml(tier.label) + '</span>' +
        '<span class="row-meta">铺面 ' + tier.rent + ' 文 + 初始进货 ' + tier.stock +
        ' 文 = 共需 ' + total + ' 文</span></div>' +
        '<div class="row-actions">' +
        '<button type="button" class="primary commerce-btn" data-tier="' + tier.kind + '"' +
        (reason ? ' disabled title="' + escapeHtml(reason) + '"' : '') + '>开' + escapeHtml(tier.label) + '</button>' +
        (reason ? '<span class="commerce-reason">' + escapeHtml(reason) + '</span>' : '') +
        '</div></div>';
    }).join('');
  }

  function renderEmployeeList(st, own) {
    const list = el('employeeList');
    if (!own) {
      el('employeeHint').textContent = '开店后显示';
      list.innerHTML = '<div class="commerce-empty">先开一家店，才能看到店内雇工。</div>';
      return;
    }
    const cap = SHOP_HIRE.capacity[own.kind] || 0;
    const rows = shopEmployeesOf(st, own);
    el('employeeHint').textContent = rows ? (rows.length + '/' + cap + ' 人') : (cap + ' 人上限');
    if (!rows) {
      list.innerHTML = '<div class="commerce-empty">服务端尚未返回雇工名单（岗位 / 日薪 / 是否到店）。' +
        '当前只能从「雇人」页确认新雇契约。</div>';
      return;
    }
    if (!rows.length) {
      list.innerHTML = '<div class="commerce-empty">店里还没有雇工。</div>';
      return;
    }
    list.innerHTML = rows.map(function (emp) {
      const present = emp.present == null ? (emp.at_shop == null ? null : !!emp.at_shop) : !!emp.present;
      const presentText = present == null ? '到店情况未提供' : (present ? '到店' : '未到店');
      const target = employeeTargetOf(st, emp);
      const owed = Number(emp.unpaid_wage || 0);
      const busy = !!state.commerce.shopops.busy['dismiss:' + target];
      return '<div class="commerce-row shopops-row" data-employee="' + escapeHtml(target) + '">' +
        '<div class="row-main"><span class="row-name">' + escapeHtml(emp.name || target || '雇工') + '</span>' +
        '<span class="row-meta">岗位 ' + escapeHtml(emp.position || '—') +
        ' · 日薪 ' + escapeHtml(emp.wage == null ? '—' : emp.wage) + ' 文' +
        ' · ' + escapeHtml(presentText) +
        (owed > 0 ? ' · 欠薪 <b>' + owed + '</b> 文' : '') + '</span></div>' +
        '<div class="row-actions">' +
        '<button type="button" class="ghost commerce-btn" data-dismiss="' + escapeHtml(target) + '"' +
        (busy ? ' disabled' : '') + '>' + (busy ? '处理中…' : '辞退') + '</button>' +
        '</div></div>';
    }).join('');
  }

  /* ------------------------------------------------------------- 店铺经营 */
  function employeeTargetOf(st, emp) {
    if (!emp) return '';
    if (emp.id || emp.target) return String(emp.id || emp.target);
    // 真服务 shop.employees 只回 name/position/wage；用同地点 nearby 反查实体 id，
    // 否则 dismiss 会因 target 为空而无法点名。
    const name = shopNameText(emp.name, '');
    const near = (st && st.nearby) || (st && st.player && st.player.nearby) || [];
    const hit = near.filter(function (n) { return n && n.name === name; })[0];
    return hit ? String(hit.id) : name;
  }

  function shopCashOf(st, own) {
    const shop = (st && st.shop) || {};
    if (shop.cash != null && Number.isFinite(Number(shop.cash))) return Number(shop.cash);
    if (own && own.cash != null && Number.isFinite(Number(own.cash))) return Number(own.cash);
    return 0;
  }

  function shopEmployeesOf(st, own) {
    const shop = (st && st.shop) || {};
    if (Array.isArray(shop.employees)) return shop.employees;
    if (own && Array.isArray(own.employees)) return own.employees;
    return null;
  }

  function shopNameText(raw, item) {
    if (raw && typeof raw === 'object') {
      if (raw.name != null) return shopNameText(raw.name, item);
      if (raw.item != null) return String(raw.item);
      return String(item);
    }
    return raw == null ? '' : String(raw);
  }

  function shopItemNameOf(item, names) {
    const raw = (names && names[item] != null) ? names[item] : SHOP_ITEM_NAMES[item];
    return shopNameText(raw, item) || String(item);
  }

  function shopReportDetailsOf(st) {
    const cached = state.commerce.shopops.reportDetails;
    if (cached && typeof cached === 'object') return cached;
    const shop = (st && st.shop) || {};
    const report = shop.report;
    if (report && typeof report === 'object' && !Array.isArray(report)) return report;
    if (shop.shop_report && typeof shop.shop_report === 'object') return shop.shop_report;
    return {};
  }

  function shopReportStockOf(st) {
    const stock = shopReportDetailsOf(st).stock;
    if (stock && typeof stock === 'object' && !Array.isArray(stock)) return stock;
    return {};
  }

  /* 库存行优先用店铺视图；真实服务的 stock 行不带基准价，
     当前地点的 buy 行才带零售基准价，按 item 合并后定价才不会把批发价当基准价。
     售罄 SKU 不会出现在 stock 行里，用 shop_report 顶层 stock 补 0 件行。 */
  function shopStockRowsOf(st, own) {
    if (!own) return [];
    const shop = (st && st.shop) || {};
    const overrides = shopReportDetailsOf(st).price_overrides || {};
    const buyInfo = {};
    (shop.buy || []).forEach(function (row) {
      if (!row || !row.item) return;
      const hit = buyInfo[row.item] || {};
      if (!hit.name && row.name) hit.name = row.name;
      // 自家店的行优先；否则用同地点任意铺子的零售价作为该货基准价
      // （stock 行只有 sell_price，不能拿 60% 的批发价当基准价）。
      if (row.business === own.id && Number(row.price) > 0) {
        hit.price = Number(row.price);
      } else if (!hit.price && Number(row.price) > 0) {
        hit.price = Number(row.price);
      }
      buyInfo[row.item] = hit;
    });
    let raw = Array.isArray(shop.stock) ? shop.stock : null;
    if (!raw) {
      raw = (shop.buy || []).filter(function (row) {
        return row && row.business === own.id;
      }).map(function (row) {
        return { item: row.item, name: row.name, qty: row.stock, price: row.price };
      });
    }
    const rows = raw.filter(function (row) {
      return row && row.item;
    }).map(function (row) {
      const info = buyInfo[row.item] || {};
      const ownBase = Number(info.price || 0);
      let base = Number(row.base_price != null ? row.base_price : 0);
      if (!base && ownBase) base = ownBase;
      if (!base && row.price != null) base = Number(row.price);
      if (!base && row.sell_price != null) base = Number(row.sell_price);
      let price = Number(row.price != null ? row.price : 0);
      if (!price) price = ownBase || base;
      const override = Number(overrides[row.item] || 0);
      if (override > 0) price = override;
      let restock = Number(row.restock_price != null ? row.restock_price : 0);
      if (!restock && row.cost != null) restock = Number(row.cost);
      if (!restock && row.sell_price != null) restock = Number(row.sell_price);
      if (!restock) restock = base;
      return {
        item: String(row.item),
        name: shopNameText(row.name, row.item) || shopNameText(info.name, row.item) ||
          shopItemNameOf(row.item, buyInfo),
        qty: Math.max(0, Number(row.qty != null ? row.qty : (row.stock || 0))),
        price: price,
        base_price: base || price,
        restock_price: restock,
        priced: base > 0 || price > 0,
        zero_stock: false
      };
    });
    const seen = {};
    rows.forEach(function (row) { seen[row.item] = true; });
    const reportStock = shopReportStockOf(st);
    Object.keys(reportStock).forEach(function (item) {
      if (seen[item] || String(item).indexOf('escrow:') === 0) return;
      const info = buyInfo[item] || {};
      const qty = Math.max(0, Number(reportStock[item]) || 0);
      if (qty > 0) return;
      const base = Number(info.price || 0);
      rows.push({
        item: String(item),
        name: shopItemNameOf(item, buyInfo),
        qty: 0,
        price: base,
        base_price: base,
        restock_price: base > 0 ? Math.max(1, Math.round(base * 0.6)) : 0,
        priced: base > 0,
        zero_stock: true
      });
      seen[item] = true;
    });
    const canonical = SHOP_CANONICAL_SKUS[String((own && own.kind) || 'stall')] || [];
    canonical.forEach(function (item) {
      if (seen[item]) return;
      const info = buyInfo[item] || {};
      const base = Number(info.price || 0);
      rows.push({
        item: item,
        name: shopItemNameOf(item, buyInfo),
        qty: 0,
        price: base,
        base_price: base,
        restock_price: base > 0 ? Math.max(1, Math.round(base * 0.6)) : 0,
        priced: base > 0,
        zero_stock: true
      });
    });
    return rows;
  }

  function priceBoundsOf(base) {
    const b = Math.max(0, Math.floor(Number(base) || 0));
    if (b <= 0) return { min: 0, max: 0, base: 0 };
    return {
      min: Math.max(1, Math.floor(b * SHOP_OPS.priceMinRatio)),
      max: Math.max(1, Math.ceil(b * SHOP_OPS.priceMaxRatio)),
      base: b
    };
  }

  function restockQtyOf(item, max) {
    const raw = Number(state.commerce.shopops.qty[item]);
    const qty = Number.isFinite(raw) && raw > 0 ? Math.floor(raw) : 1;
    return Math.max(1, Math.min(Math.max(1, max == null ? 20 : max), qty));
  }

  function shopRestockTargetOf(own) {
    const kind = String((own && own.kind) || 'stall');
    return SHOP_OPS.restockTarget[kind] || SHOP_OPS.restockTarget.stall;
  }

  function restockFillQtyOf(row, own) {
    const target = shopRestockTargetOf(own);
    const need = Math.max(1, target - Math.max(0, Number(row.qty) || 0));
    return Math.min(20, need);
  }

  function shopReportDaysOf(st) {
    const cached = state.commerce.shopops.report;
    if (Array.isArray(cached) && cached.length) return cached.slice(-SHOP_OPS.reportDays);
    const shop = (st && st.shop) || {};
    let report = shop.report;
    if (Array.isArray(report)) return report.slice(-SHOP_OPS.reportDays);
    if (report && Array.isArray(report.days)) return report.days.slice(-SHOP_OPS.reportDays);
    if (shop.shop_report && Array.isArray(shop.shop_report.days)) {
      return shop.shop_report.days.slice(-SHOP_OPS.reportDays);
    }
    return [];
  }

  function ledgerTradesOf(row) {
    if (row.trades != null) return Number(row.trades || 0);
    if (row.tx != null) return Number(row.tx || 0);
    return 0;
  }

  function ledgerTotalsOf(days) {
    const total = { revenue: 0, restock: 0, wage: 0, tax: 0, net_profit: 0, trades: 0 };
    (days || []).forEach(function (row) {
      total.revenue += Number(row.revenue || 0);
      total.restock += Number(row.restock || 0);
      total.wage += Number(row.wage || 0);
      total.tax += Number(row.tax || 0);
      total.net_profit += Number(row.net_profit || 0);
      total.trades += ledgerTradesOf(row);
    });
    return total;
  }

  function stockoutLabelOf(row) {
    const items = Array.isArray(row.stockout_items) ? row.stockout_items : [];
    const names = items.filter(Boolean).map(function (item) {
      return SHOP_ITEM_NAMES[item] || item;
    });
    return names.length ? '（' + names.join('/') + '）' : '';
  }

  /* 负净利原因只由该行数值推导，不写死文案，也不臆测接口没给的库存字段。
     后端用 `tx` 记成交笔数；缺货日优先用中文直说，而不是只显示一个布尔值。 */
  function ledgerReasonOf(row) {
    const revenue = Number(row.revenue || 0);
    const wage = Number(row.wage || 0);
    const restock = Number(row.restock || 0);
    const net = Number(row.net_profit || 0);
    const trades = ledgerTradesOf(row);
    if (row.stockout) return '缺货导致零成交' + stockoutLabelOf(row);
    if (net >= 0) return '';
    if (revenue <= 0 || trades <= 0) return '当日无客流';
    if (wage > revenue) return '工资高于营收';
    if (restock > 0 && restock >= revenue) return '进货占去营收';
    return '入不敷出';
  }

  function shopOpsDisabledReason(st, own) {
    if (!own) return '你还没有店铺；先开一家再经营';
    const shop = (st && st.shop) || {};
    const me = 'player:' + String((state.session && state.session.accountId) || '');
    if (shop.owner && String(shop.owner) !== me && String(shop.owner) !== String(own.owner || '')) {
      return '这家店铺不是你的，不能经营';
    }
    const loc = String((st.location || {}).id || '');
    if (own.location && loc && String(own.location) !== loc) return '你不在店铺所在地';
    return '';
  }

  function renderRestockSection(st, own, reason) {
    const list = el('restockList');
    const hint = el('restockHint');
    const rows = shopStockRowsOf(st, own);
    if (!own) {
      hint.textContent = '无店';
      list.innerHTML = '<div class="commerce-empty">' + escapeHtml(reason || '你还没有店铺') + '。</div>';
      el('restockTotal').textContent = '合计 0 文';
      return;
    }
    if (!rows.length) {
      hint.textContent = '无库存';
      list.innerHTML = '<div class="commerce-empty">店里没有可进的货。</div>';
      el('restockTotal').textContent = '合计 0 文';
      return;
    }
    const low = rows.filter(function (row) { return row.qty < SHOP_OPS.lowStock; });
    hint.textContent = low.length
      ? low.length + ' 种低于 ' + SHOP_OPS.lowStock + ' 件，先补货'
      : rows.length + ' 种可进货';
    const target = shopRestockTargetOf(own);
    list.innerHTML = rows.map(function (row) {
      const qty = restockQtyOf(row.item, 20);
      const busy = !!state.commerce.shopops.busy['restock:' + row.item];
      const total = row.restock_price > 0 ? row.restock_price * qty : 0;
      const isLow = row.qty < SHOP_OPS.lowStock;
      const alert = row.qty <= 0
        ? '<span class="stock-alert">已售罄 · 优先补货</span>'
        : (isLow ? '<span class="stock-alert">库存不足（低于 ' + SHOP_OPS.lowStock + ' 件）</span>' : '');
      const fill = restockFillQtyOf(row, own);
      return '<div class="commerce-row shopops-row' + (isLow ? ' stock-low' : '') +
        '" data-restock-row="' + escapeHtml(row.item) + '">' +
        '<div class="row-main"><span class="row-name">' + escapeHtml(row.name) + '</span>' +
        '<span class="row-meta">现有 <b class="stock-now">' + escapeHtml(row.qty) + '</b> 件 · 进货单价 ' +
        (row.priced ? (escapeHtml(row.restock_price) + ' 文/件') : '<span class="commerce-gap">接口未提供</span>') +
        (isLow ? ' · ' + alert : '') + '</span></div>' +
        '<div class="row-actions">' +
        (isLow ? '<button type="button" class="ghost commerce-btn" data-restock-fill="' + escapeHtml(row.item) +
          '" data-fill-qty="' + fill + '" title="按档位建议量 ' + target + ' 件预填，仍需点「进货」确认"' +
          ((busy || reason) ? ' disabled' : '') + '>补到 ' + target + ' 件</button>' : '') +
        '<input type="number" min="1" max="20" step="1" inputmode="numeric" ' +
        'data-restock-qty="' + escapeHtml(row.item) + '" value="' + qty + '" aria-label="进几件' +
        escapeHtml(row.name) + '"' + ((busy || reason) ? ' disabled' : '') + ' />' +
        '<button type="button" class="primary commerce-btn" data-restock-go="' + escapeHtml(row.item) + '"' +
        ((busy || reason) ? ' disabled' : '') + '>' + (busy ? '处理中…' : '进货') + '</button>' +
        '<span class="commerce-reason" data-restock-cost="' + escapeHtml(row.item) + '">' +
        (row.priced ? ('合计 ' + total + ' 文') : '总价以服务端结算为准') + '</span>' +
        '</div></div>';
    }).join('');
    updateRestockTotal(st);
  }

  function updateRestockTotal(st) {
    const own = ownShopOf(st);
    const rows = shopStockRowsOf(st, own);
    let total = 0;
    let known = true;
    rows.forEach(function (row) {
      const input = document.querySelector('input[data-restock-qty="' + row.item + '"]');
      const qty = Math.max(1, Math.min(20, Math.floor(Number((input && input.value) || restockQtyOf(row.item)) || 1)));
      if (row.restock_price > 0) total += row.restock_price * qty;
      else known = false;
      const line = document.querySelector('[data-restock-cost="' + row.item + '"]');
      if (line) line.textContent = row.priced ? ('合计 ' + (row.restock_price * qty) + ' 文') : '总价以服务端结算为准';
    });
    el('restockTotal').textContent = known ? ('合计 ' + total + ' 文') : '合计以服务端结算为准';
  }

  function priceNoteText(base, price) {
    const b = Math.max(0, Math.floor(Number(base) || 0));
    const p = Math.max(0, Math.floor(Number(price) || 0));
    const delta = b > 0 ? Math.round(((p - b) / b) * 100) : 0;
    const dir = p > b ? '高' : (p < b ? '低' : '持平');
    const tail = p > b ? ' · 过高可能没人买' : (p < b ? ' · 过低每件赚得少' : ' · 与基准持平');
    const head = '当前基准价 ' + b + ' 文，你的价 ' + p + ' 文，比基准' +
      (dir === '持平' ? '持平 0%' : (dir + ' ' + Math.abs(delta) + '%'));
    return { text: head + tail, delta: delta, bad: b > 0 && (p < Math.floor(b * SHOP_OPS.priceMinRatio)
      || p > Math.ceil(b * SHOP_OPS.priceMaxRatio)) };
  }

  function renderPriceSection(st, own, reason) {
    const list = el('priceList');
    const hint = el('priceHint');
    const rows = shopStockRowsOf(st, own);
    if (!own) {
      hint.textContent = '无店';
      list.innerHTML = '<div class="commerce-empty">' + escapeHtml(reason || '你还没有店铺') + '。</div>';
      return;
    }
    if (!rows.length) {
      hint.textContent = '无库存';
      list.innerHTML = '<div class="commerce-empty">店里没有可定价的货。</div>';
      return;
    }
    hint.textContent = '只能在基准价 ±20% 内调整';
    list.innerHTML = rows.map(function (row) {
      const bounds = priceBoundsOf(row.base_price);
      const input = Number(state.commerce.shopops.price[row.item]);
      const price = Number.isFinite(input) && input > 0 ? Math.floor(input) : (row.price || bounds.base);
      const note = priceNoteText(bounds.base, price);
      const busy = !!state.commerce.shopops.busy['price:' + row.item];
      const disabled = !!(reason || busy || bounds.base <= 0);
      return '<div class="commerce-row shopops-row shopops-price" data-price-row="' + escapeHtml(row.item) + '">' +
        '<div class="row-main"><span class="row-name">' + escapeHtml(row.name) + '</span>' +
        '<span class="row-meta">当前价 <b>' + escapeHtml(row.price) + '</b> 文 · 基准价 ' +
        (bounds.base > 0 ? (escapeHtml(bounds.base) + ' 文') : '<span class="commerce-gap">接口未提供</span>') +
        ' · 库存 ' + escapeHtml(row.qty) + ' 件</span></div>' +
        '<div class="row-actions shopops-price-actions">' +
        '<input type="number" min="' + bounds.min + '" max="' + bounds.max + '" step="1" ' +
        'inputmode="numeric" data-price-input="' + escapeHtml(row.item) + '" value="' + price + '" ' +
        'aria-label="' + escapeHtml(row.name) + '定价"' + (disabled ? ' disabled' : '') + ' />' +
        '<input type="range" class="price-range" min="' + bounds.min + '" max="' + bounds.max + '" ' +
        'step="1" data-price-range="' + escapeHtml(row.item) + '" value="' + price + '" ' +
        'aria-label="' + escapeHtml(row.name) + '定价滑块"' + (disabled ? ' disabled' : '') + ' />' +
        '<span class="price-badge' + (note.bad ? ' bad' : '') + '" data-price-delta="' + escapeHtml(row.item) + '">' +
        (note.bad ? '超出 ±20%' : ((note.delta > 0 ? '+' : '') + note.delta + '%')) + '</span>' +
        '<button type="button" class="primary commerce-btn" data-price-go="' + escapeHtml(row.item) + '"' +
        (disabled ? ' disabled' : '') + '>' + (busy ? '处理中…' : '改价') + '</button>' +
        '</div>' +
        '<div class="price-note' + (note.bad ? ' bad' : '') + '" data-price-note="' + escapeHtml(row.item) + '">' +
        escapeHtml(note.text) + '</div></div>';
    }).join('');
  }

  function updatePriceRow(item) {
    const row = document.querySelector('[data-price-row="' + item + '"]');
    if (!row) return;
    const input = row.querySelector('[data-price-input]');
    const range = row.querySelector('[data-price-range]');
    const priceRows = shopStockRowsOf(state.st || {}, ownShopOf(state.st || {}));
    const stock = priceRows.filter(function (r) { return r.item === item; })[0];
    if (!stock) return;
    const value = Math.max(0, Math.floor(Number((input && input.value) || (range && range.value)) || 0));
    state.commerce.shopops.price[item] = value;
    if (input) input.value = String(value);
    if (range) range.value = String(value);
    const note = priceNoteText(stock.base_price, value);
    const badge = row.querySelector('[data-price-delta]');
    if (badge) {
      badge.textContent = note.bad ? '超出 ±20%' : ((note.delta > 0 ? '+' : '') + note.delta + '%');
      badge.classList.toggle('bad', note.bad);
    }
    const noteEl = row.querySelector('[data-price-note]');
    if (noteEl) {
      noteEl.textContent = note.text;
      noteEl.classList.toggle('bad', note.bad);
    }
  }

  function renderWithdrawSection(st, own, reason) {
    const cash = shopCashOf(st, own);
    el('shopCashNow').textContent = '当前店现 ' + cash + ' 文';
    const input = el('withdrawAmount');
    const btn = el('withdrawGo');
    const busy = !!state.commerce.shopops.busy.withdraw;
    if (!own) {
      input.disabled = true;
      btn.disabled = true;
      return;
    }
    input.disabled = !!busy;
    btn.disabled = !!(busy || reason);
    btn.textContent = busy ? '处理中…' : '提现';
    input.max = String(Math.max(0, cash));
  }

  function ledgerCell(value) {
    return escapeHtml(value == null ? 0 : value);
  }

  function renderLedger(st, own) {
    const days = own ? shopReportDaysOf(st) : [];
    const reason = shopOpsDisabledReason(st, own);
    const empty = el('ledgerEmpty');
    const wrap = el('ledgerWrap');
    const body = el('ledgerBody');
    const foot = el('ledgerFoot');
    const notice = el('ledgerStockout');
    if (!days.length) {
      // 空账本 / 未开店 / 非店主三种状态各自说清楚，不放任何示例行或假数字。
      if (!own) {
        empty.textContent = '你还没有店铺；开张后才有日结账本';
      } else if (reason.indexOf('不是你的') >= 0) {
        empty.textContent = '你不是店主，看不到这家店的账本';
      } else {
        empty.textContent = '尚无日结记录，世界日结束后来看';
      }
      empty.classList.remove('hidden');
      wrap.classList.add('hidden');
      body.innerHTML = '';
      foot.innerHTML = '';
      if (notice) {
        notice.hidden = true;
        notice.textContent = '';
      }
      return;
    }
    empty.classList.add('hidden');
    wrap.classList.remove('hidden');
    body.innerHTML = days.map(function (row) {
      const net = Number(row.net_profit || 0);
      const cls = net > 0 ? 'profit-pos' : (net < 0 ? 'profit-neg' : '');
      const note = ledgerReasonOf(row);
      return '<tr data-ledger-day="' + escapeHtml(row.day) + '"' +
        (row.stockout ? ' data-stockout="1" class="stockout-row"' : '') + '>' +
        '<td>' + escapeHtml('第 ' + (row.day == null ? '?' : row.day) + ' 日') + '</td>' +
        '<td>' + ledgerCell(row.revenue) + '</td>' +
        '<td>' + ledgerCell(row.restock) + '</td>' +
        '<td>' + ledgerCell(row.wage) + '</td>' +
        '<td>' + ledgerCell(row.tax) + '</td>' +
        '<td class="' + cls + '">' + (net > 0 ? '+' : '') + net + '</td>' +
        '<td>' + ledgerTradesOf(row) + '</td>' +
        '<td class="ledger-reason">' + escapeHtml(note) + '</td></tr>';
    }).join('');
    const total = ledgerTotalsOf(days);
    const netCls = total.net_profit > 0 ? 'profit-pos' : (total.net_profit < 0 ? 'profit-neg' : '');
    foot.innerHTML = '<tr class="ledger-total-row">' +
      '<td>' + escapeHtml(days.length + ' 日合计') + '</td>' +
      '<td>' + total.revenue + '</td><td>' + total.restock + '</td><td>' + total.wage + '</td>' +
      '<td>' + total.tax + '</td><td class="' + netCls + '">' +
      (total.net_profit > 0 ? '+' : '') + total.net_profit + '</td>' +
      '<td>' + total.trades + '</td><td></td></tr>';
    if (notice) {
      const details = shopReportDetailsOf(st);
      const count = details.stockouts != null
        ? Number(details.stockouts || 0)
        : days.filter(function (row) { return !!row.stockout; }).length;
      notice.hidden = count <= 0;
      notice.textContent = count > 0
        ? '最近 ' + days.length + ' 个日结里，有 ' + count + ' 天缺货导致零成交；低于 ' +
          SHOP_OPS.lowStock + ' 件的 SKU 请优先补货。'
        : '';
    }
  }

  function ensureShopReport(st) {
    if (shopReportDaysOf(st).length) return;
    const ops = state.commerce.shopops;
    if (!state.commerce.account || ops.reportLoaded || ops.reportUnavailable || ops.reportBusy) return;
    loadShopReport();
  }
  async function loadShopReport() {
    const account = state.session && state.session.accountId;
    if (!account) return;
    const ops = state.commerce.shopops;
    if (ops.reportBusy) return;
    ops.reportBusy = true;
    const before = state.st || {};
    try {
      const res = await API.action(account, 'shop_report', {}, before.tick == null ? null : before.tick);
      const data = res.data || {};
      if (res.authRequired || res.status === 401) return;
      if (res.status === 409 || data.ok === false || res.status === 0) {
        const code = String(data.reject_reason || data.reason || '');
        ops.reportUnavailable = code === 'unknown_action' || code === 'unknown_verb'
          || code === 'not_implemented_for_player' || res.status === 404;
        ops.reportLoaded = true;
        renderLedger(state.st || {}, ownShopOf(state.st || {}));
        return;
      }
      const details = data.details || data;
      const report = details.report && typeof details.report === 'object' ? details.report : {};
      const days = Array.isArray(details.days) ? details.days
        : (Array.isArray(data.days) ? data.days
          : (Array.isArray(report.days) ? report.days : []));
      ops.report = days;
      ops.reportDetails = {
        stock: report.stock || details.stock || {},
        stockouts: report.stockouts != null ? report.stockouts : details.stockouts,
        price_overrides: report.price_overrides || {}
      };
      ops.reportLoaded = true;
      renderLedger(state.st || {}, ownShopOf(state.st || {}));
    } catch (err) {
      ops.reportUnavailable = true;
      ops.reportLoaded = true;
    } finally {
      ops.reportBusy = false;
    }
  }


  function relationMap(st) {
    const map = {};
    ((st.player || {}).relations || []).forEach(function (r) {
      if (r && r.target) map[r.target] = r;
    });
    return map;
  }

  function renderHireList(st, own) {
    const list = el('hireList');
    const hint = el('hireHint');
    const npcs = (st.present_npcs || []).filter(function (n) { return n && n.id; });
    if (!npcs.length) {
      el('hireHint').textContent = '这里没有可雇的人。';
      list.innerHTML = '<div class="commerce-empty">当前地点没有人可以雇。</div>';
      return;
    }
    const rel = relationMap(st);
    const cap = own ? (SHOP_HIRE.capacity[own.kind] || 0) : 0;
    const employees = own && Array.isArray(own.employees) ? own.employees : [];
    const full = own && employees.length >= cap;
    hint.textContent = own
      ? (full ? '店铺雇工已满（' + employees.length + '/' + cap + '）。' : '门槛：好感 ≥6.0、技能 ≥0.15；日薪为市价的 0.8-1.5 倍。')
      : '先开一家店铺，再来雇人。';
    list.innerHTML = npcs.map(function (npc) {
      const r = rel[npc.id];
      const aff = r ? Number(r.affection || 0) : 0;
      const pos = state.commerce.pos[npc.id] ||
        (POSITION_WAGES[npc.role] ? npc.role : '脚夫');
      const market = positionWage(pos);
      const low = Math.max(1, Math.round(market * SHOP_HIRE.wageMinRatio));
      const high = Math.round(market * SHOP_HIRE.wageMaxRatio);
      const localReason = !own ? '先开店' : (full ? '已满员' : '');
      const opts = Object.keys(POSITION_WAGES).sort(function (a, b) { return POSITION_WAGES[a] - POSITION_WAGES[b]; })
        .map(function (p) {
          return '<option value="' + escapeHtml(p) + '"' + (p === pos ? ' selected' : '') + '>' +
            escapeHtml(p) + '（' + POSITION_WAGES[p] + ' 文）</option>';
        }).join('');
      return '<div class="commerce-row" data-hire-row="' + escapeHtml(npc.id) + '">' +
        '<div class="row-main"><span class="row-name">' + escapeHtml(npc.name) +
        '<span class="pill">' + escapeHtml(npc.role || '') + '</span></span>' +
        '<span class="row-meta">好感 ' + escapeHtml(aff.toFixed(1)) +
        (aff < SHOP_HIRE.minAffection ? '（不足 6.0）' : '') +
        ' · 技能 <span class="commerce-gap">接口未提供</span>' +
        ' · 受雇 <span class="commerce-gap">接口未提供</span>' +
        ' · 市价 ' + market + ' 文（可 ' + low + '-' + high + '）</span></div>' +
        '<div class="row-actions">' +
        '<select data-pos-for="' + escapeHtml(npc.id) + '" aria-label="' + escapeHtml(npc.name) + '的岗位">' +
        opts + '</select>' +
        '<button type="button" class="primary commerce-btn" data-hire="' + escapeHtml(npc.id) + '"' +
        (localReason ? ' disabled title="' + escapeHtml(localReason) + '"' : '') + '>雇</button>' +
        (localReason ? '<span class="commerce-reason">' + escapeHtml(localReason) + '</span>' : '') +
        '</div></div>';
    }).join('');
    if (!state.commerce.wageSet && npcs.length) {
      const first = npcs[0];
      const pos = state.commerce.pos[first.id] ||
        (POSITION_WAGES[first.role] ? first.role : '脚夫');
      el('hireWage').value = String(Math.max(1, positionWage(pos)));
    }
  }

  function escortContext(st) {
    const loc = (st && st.location) || {};
    const biz = ((st && st.shop) || {}).businesses || [];
    const agencies = biz.filter(function (b) {
      return String(b.kind || '').indexOf('escort') >= 0;
    });
    const items = (((st || {}).player || {}).wallet || {}).items || [];
    return {
      loc: loc,
      agencies: agencies,
      hasAgency: agencies.length > 0,
      dests: (loc.exits || []).filter(function (x) { return x && x.id && x.exists !== false; }),
      items: items.filter(function (it) { return it.item && Number(it.qty || 0) > 0; }),
      active: ((st || {}).escort_orders || []).filter(function (o) { return o.status === 'active' || o.status === 'open'; })
    };
  }

  function fillSelect(select, rows, placeholder, valueKey, labelFn) {
    if (!select) return;
    const prev = select.value;
    select.innerHTML = rows.length ? rows.map(function (row) {
      return '<option value="' + escapeHtml(row[valueKey]) + '">' + escapeHtml(labelFn(row)) + '</option>';
    }).join('') : ('<option value="">' + escapeHtml(placeholder) + '</option>');
    if (prev && rows.some(function (row) { return String(row[valueKey]) === prev; })) select.value = prev;
    else if (rows.length) select.value = String(rows[0][valueKey]);
  }

  function clampEscortQty() {
    const st = state.st;
    if (!st) return;
    const ctx = escortContext(st);
    const row = ctx.items.filter(function (it) { return it.item === el('escortCargo').value; })[0] || null;
    const max = Math.max(1, Math.min(ESCORT_RULES.qtyMax, Number((row || {}).qty || 1)));
    el('escortQty').max = String(max);
    if (Number(el('escortQty').value) > max) el('escortQty').value = String(max);
  }

  function updateEscortEstimate() {
    const st = state.st;
    if (!st) return;
    const ctx = escortContext(st);
    const dest = el('escortDest').value;
    const cargo = el('escortCargo').value;
    const qty = Math.max(1, Math.min(ESCORT_RULES.qtyMax, Math.floor(Number(el('escortQty').value) || 1)));
    const fee = Math.floor(Number(el('escortFee').value) || 0);
    const cargoRow = ctx.items.filter(function (it) { return it.item === cargo; })[0] || null;
    const est = el('escortEstimate');
    const go = el('escortGo');
    if (!ctx.hasAgency) {
      est.textContent = '这里没有镖局；起点必须有镖局才能接镖。先走到镖局所在地。';
      go.disabled = true;
      return;
    }
    if (!ctx.dests.length) {
      est.textContent = '当前地点没有相邻出口，无法组一条押镖路线。';
      go.disabled = true;
      return;
    }
    if (!dest || !cargoRow) {
      est.textContent = '选一个相邻终点和一件随身货物，再定镖费。';
      go.disabled = true;
      return;
    }
    if (fee < ESCORT_RULES.feeMin || fee > ESCORT_RULES.feeMax) {
      est.textContent = '镖费需在 ' + ESCORT_RULES.feeMin + '-' + ESCORT_RULES.feeMax + ' 文之间。';
      go.disabled = true;
      return;
    }
    const destName = (ctx.dests.filter(function (x) { return x.id === dest; })[0] || {}).name || dest;
    const held = Math.max(0, Number(cargoRow.qty || 0));
    const net = escortNet(fee, ESCORT_RULES.agencyCut);
    const arrive = Number(st.tick || 0) + ESCORT_RULES.durationTicks;
    est.textContent = '路线 ' + (ctx.loc.name || ctx.loc.id) + ' → ' + destName + '（相邻）· ' +
      (cargoRow.name || cargoRow.item) + '×' + Math.min(qty, held) + '（持有 ' + held + '）· ' +
      '预计到手 ' + net + ' 文（镖费 ' + fee + ' 文，镖局抽 ' + Math.round(ESCORT_RULES.agencyCut * 100) + '%）· ' +
      '到货 tick ' + arrive;
    go.disabled = held <= 0;
  }

  function renderEscortForm(st) {
    const ctx = escortContext(st);
    fillSelect(el('escortDest'), ctx.dests, '（没有相邻出口）', 'id', function (row) {
      return (row.name || row.id) + '（相邻）';
    });
    fillSelect(el('escortCargo'), ctx.items, '（背包里没有可押的货）', 'item', function (row) {
      return (row.name || row.item) + '（持有 ' + row.qty + '）';
    });
    const cargoKey = el('escortCargo').value;
    const cargoRow = ctx.items.filter(function (it) { return it.item === cargoKey; })[0] || null;
    const maxHeld = Math.max(0, Number((cargoRow || {}).qty || 0));
    el('escortQty').max = String(Math.max(1, Math.min(ESCORT_RULES.qtyMax, maxHeld || 1)));
    if (Number(el('escortQty').value) > Math.max(1, maxHeld)) {
      el('escortQty').value = String(Math.max(1, Math.min(ESCORT_RULES.qtyMax, maxHeld)));
    }
    updateEscortEstimate();
  }

  function renderSoonList() {
    // 立派与收徒已移入「门派」页签；这里只保留尚未对玩家开放的拜师。
    const rows = [
      { verb: 'apprentice', name: '拜师', note: '该玩法尚未开放；门派招募请用「门派」页签。' }
    ];
    el('soonList').innerHTML = rows.map(function (row) {
      return '<div class="commerce-row" data-soon="' + row.verb + '">' +
        '<div class="row-main"><span class="row-name">' + escapeHtml(row.name) + '</span>' +
        '<span class="row-meta">' + escapeHtml(row.note) + '</span></div>' +
        '<div class="row-actions"><button type="button" class="ghost commerce-btn" disabled ' +
        'title="该玩法尚未开放">尚未开放</button></div></div>';
    }).join('');
  }

  /* ------------------------------------------------------------- 婚姻 */
  function marriageOf(st) {
    return (st && st.marriage) || {};
  }

  function spouseIdOf(st) {
    return String(marriageOf(st).spouse_id || '');
  }

  function spouseNameOf(st) {
    const m = marriageOf(st);
    return String(m.spouse_name || m.spouse_id || '');
  }

  function isSpouseNpc(npc, st) {
    if (!npc || String(marriageOf(st).status || '') !== 'married') return false;
    const sid = spouseIdOf(st);
    if (!sid) return false;
    if (String(npc.id || '') === sid) return true;
    const name = spouseNameOf(st);
    return !!(name && String(npc.name || '') === name);
  }

  function isSpouseName(name) {
    if (String(marriageOf(state.st).status || '') !== 'married') return false;
    const spouse = spouseNameOf(state.st);
    const sid = spouseIdOf(state.st);
    const raw = String(name || '');
    /* 服务端 /messages 的 from 是 NPC 实体 id；本地 mock/未来接口也可能给姓名。 */
    return !!(raw && (raw === spouse || (sid && raw === sid)));
  }

  function npcDisplayName(id) {
    const rows = (state.st && state.st.present_npcs) || [];
    for (let i = 0; i < rows.length; i += 1) {
      const row = rows[i] || {};
      if (String(row.id) === String(id)) return String(row.name || '');
    }
    return '';
  }

  function marriageCandidates(st) {
    const rel = relationMap(st);
    const m = marriageOf(st);
    const sid = spouseIdOf(st);
    const p = (st && st.player) || {};
    const selfIds = [String(p.entity_id || ''),
      state.session && state.session.entityId ? String(state.session.entityId) : ''];
    return ((st && st.present_npcs) || []).filter(function (npc) {
      return npc && npc.id;
    }).map(function (npc) {
      const r = rel[npc.id] || {};
      const aff = Number(r.affection || 0);
      const trust = Number(r.trust || 0);
      const isSelf = selfIds.indexOf(String(npc.id)) >= 0;
      const isSpouse = !!sid && String(npc.id) === sid;
      const marriedText = isSpouse ? '已婚（你的配偶）' :
        (isSelf ? '你自己' : '已婚状态：接口未提供');
      return { npc: npc, aff: aff, trust: trust, isSelf: isSelf,
        isSpouse: isSpouse, marriedText: marriedText,
        couple: String(m.status || '') === 'married' };
    });
  }

  function marriageOfCandidate(st, id) {
    return marriageCandidates(st).filter(function (row) {
      return String(row.npc.id) === String(id || '');
    })[0] || null;
  }

  function giftRowsOf(st) {
    const items = (((st && st.player) || {}).wallet || {}).items || [];
    return items.filter(function (it) {
      return it && it.item && Number(it.qty || 0) > 0;
    }).map(function (it) {
      return { item: it.item, name: it.name || it.item, qty: Number(it.qty || 0) };
    });
  }

  function decisionHint(aff, trust) {
    if (aff >= MARRIAGE_RULES.minAffection && trust >= MARRIAGE_RULES.minTrust) {
      return { tone: 'ok', text: '预计接受：好感 ≥60 且信任 ≥30；服务端仍会复核聘礼与信物。' };
    }
    if (aff >= MARRIAGE_RULES.courtshipAffection || trust >= MARRIAGE_RULES.courtshipTrust) {
      return { tone: 'warn', text: '预计婉拒：会进入「再处处看」，不扣聘礼与信物。' };
    }
    return { tone: 'bad', text: '预计拒绝：好感与信任都不足，不扣聘礼与信物。' };
  }

  function renderSpouseBlock(st) {
    const block = el('marriageBlock');
    if (!block) return;
    const m = marriageOf(st);
    const married = String(m.status || '') === 'married' && (m.spouse_id || m.spouse_name);
    block.classList.toggle('hidden', !married);
    if (!married) return;
    const since = Number(m.since_tick || 0);
    el('spouseInfo').textContent = '配偶：' + (m.spouse_name || m.spouse_id || '—') +
      ' · 成婚 tick ' + since;
    const note = String(m.daily_note || '').trim();
    const last = Number(m.last_daily_tick || 0);
    let text = '';
    if (note || (last && last >= since)) {
      text = '今日已陪伴' + (last ? '（tick ' + last + '）' : '') +
        (note ? '：' + note : '。');
    } else {
      const near = ((st && st.nearby) || []).concat((st && st.present_npcs) || []);
      const together = near.some(function (npc) {
        return npc && String(npc.id || '') === spouseIdOf(st);
      });
      text = together
        ? '今日与你同地点（接口未返回 spouse_time，按 nearby 同地点近似）。'
        : '今日陪伴记录未由接口提供（也不在同地点）。';
    }
    el('spouseNote').textContent = text;
    el('spouseCaliber').textContent = '伴侣日常';
    el('divorceAvatar').textContent = firstChar(m.spouse_name || '离');
  }

  function renderMarriagePane(st) {
    if (!state.st) return;
    const m = marriageOf(st);
    const married = String(m.status || '') === 'married';
    const candidates = marriageCandidates(st);
    const money = Number(((st.player || {}).wallet || {}).money || 0);
    el('marriageHint').textContent = married
      ? '你已成婚；和离后才能再求婚。'
      : (candidates.length
        ? ('当前地点 ' + candidates.length + ' 人；好感 / 信任取自关系记录')
        : '当前地点没有可求婚的人');
    const list = el('proposeList');
    if (!candidates.length) {
      list.innerHTML = '<div class="commerce-empty">当前地点没有可求婚的人。</div>';
    } else {
      list.innerHTML = candidates.map(function (row) {
        const selected = String(state.marriage.target) === String(row.npc.id);
        return '<div class="commerce-row propose-row' + (selected ? ' selected' : '') +
          '" data-propose-row="' + escapeHtml(row.npc.id) + '">' +
          '<div class="row-main"><span class="row-name">' + escapeHtml(row.npc.name || row.npc.id) +
          '<span class="pill">' + escapeHtml(row.npc.role || '') + '</span>' +
          (row.isSpouse ? '<span class="spouse-badge">配偶</span>' : '') + '</span>' +
          '<span class="row-meta">好感 ' + escapeHtml(row.aff.toFixed(1)) +
          ' · 信任 ' + escapeHtml(row.trust.toFixed(1)) +
          ' · 同地点 · ' + escapeHtml(row.marriedText) + '</span></div>' +
          '<div class="row-actions"><button type="button" class="ghost commerce-btn" ' +
          'data-propose-choose="' + escapeHtml(row.npc.id) + '"' +
          ((married || row.isSelf) ? ' disabled title="' +
            escapeHtml(married ? '你已成婚' : '不能向自己求婚') + '"' : '') + '>' +
          (selected ? '已选' : '选此人') + '</button></div></div>';
      }).join('');
    }
    if (state.marriage.target &&
        !candidates.some(function (row) { return String(row.npc.id) === String(state.marriage.target); })) {
      state.marriage.target = '';
    }
    if (!state.marriage.target && !married) {
      const first = candidates.filter(function (row) { return !row.isSelf; })[0] || candidates[0];
      if (first) state.marriage.target = String(first.npc.id);
    }
    const gifts = giftRowsOf(st);
    const select = el('proposeItem');
    const prev = select.value;
    select.innerHTML = '<option value="">不带信物</option>' + gifts.map(function (row) {
      return '<option value="' + escapeHtml(row.item) + '">' + escapeHtml(row.name) +
        '（持有 ' + escapeHtml(row.qty) + '）</option>';
    }).join('');
    select.value = gifts.some(function (row) { return row.item === prev; }) ? prev : '';
    el('proposeMoney').max = String(Math.max(0, money));
    const result = el('proposeResult');
    result.textContent = state.marriage.result || '';
    result.classList.toggle('bad', !!state.marriage.resultBad);
    result.classList.toggle('ok', !!state.marriage.result && !state.marriage.resultBad);
    updateProposeEstimate();
  }

  function updateProposeEstimate() {
    const st = state.st;
    if (!st) return;
    const m = marriageOf(st);
    const est = el('proposeEstimate');
    const go = el('proposeGo');
    const line = el('proposeTargetLine');
    est.className = 'hint';
    go.disabled = false;
    go.textContent = '求婚';
    if (String(m.status || '') === 'married') {
      est.textContent = '你已成婚；先和离，才能再向别人求婚。';
      go.disabled = true;
      line.textContent = '配偶：' + spouseNameOf(st);
      return;
    }
    const row = marriageOfCandidate(st, state.marriage.target);
    if (!row) {
      est.textContent = '先选一个在场的人，再定聘礼与信物。';
      go.disabled = true;
      line.textContent = '先选一个人，再定聘礼与信物。';
      return;
    }
    const money = Math.floor(Number(el('proposeMoney').value) || 0);
    const wallet = Number(((st.player || {}).wallet || {}).money || 0);
    const item = el('proposeItem').value;
    const gift = giftRowsOf(st).filter(function (g) { return g.item === item; })[0] || null;
    const held = gift ? gift.qty : 0;
    const qty = Math.max(1, Math.min(MARRIAGE_RULES.giftQtyMax,
      Math.floor(Number(el('proposeQty').value) || 1)));
    el('proposeQty').max = String(gift ? Math.max(1, Math.min(MARRIAGE_RULES.giftQtyMax, held)) :
      MARRIAGE_RULES.giftQtyMax);
    line.textContent = '求婚对象：' + (row.npc.name || row.npc.id) +
      ' · 好感 ' + row.aff.toFixed(1) + ' · 信任 ' + row.trust.toFixed(1) +
      ' · ' + row.marriedText;
    const hint = decisionHint(row.aff, row.trust);
    if (row.isSelf) {
      est.textContent = reasonText('self_propose');
      go.disabled = true;
      return;
    }
    if (money < 0 || money > wallet) {
      est.textContent = '聘礼 ' + money + ' 文超过钱包余额（' + wallet + ' 文）；先改小聘礼。';
      go.disabled = true;
      return;
    }
    if (item && qty > held) {
      est.textContent = '信物数量超过持有（' + held + ' 件）；先改小数量。';
      go.disabled = true;
      return;
    }
    est.textContent = hint.text + ' · 聘礼 ' + money + ' 文' +
      (gift ? (' · 信物 ' + (gift.name || gift.item) + '×' + qty) : ' · 不带信物');
    est.className = 'hint ' + hint.tone;
  }

  function showMarriageResult(text, bad) {
    state.marriage.result = text || '';
    state.marriage.resultBad = !!bad;
    const box = el('proposeResult');
    if (box) {
      box.textContent = state.marriage.result;
      box.classList.toggle('bad', !!bad);
      box.classList.toggle('ok', !!text && !bad);
    }
  }

  async function doPropose() {
    const account = state.session && state.session.accountId;
    if (!account) { toast('先进入世界，才能求婚。', true); showEntry(true); return; }
    if (!requireAuthForWrite()) return;
    const st = state.st;
    const m = marriageOf(st);
    if (String(m.status || '') === 'married') {
      showMarriageResult('你已成婚；先和离，才能再向别人求婚。', true);
      return;
    }
    const row = marriageOfCandidate(st, state.marriage.target);
    if (!row) { showMarriageResult('先选一个在场的人，再求婚。', true); return; }
    if (row.isSelf) { showMarriageResult(reasonText('self_propose'), true); return; }
    const money = Math.floor(Number(el('proposeMoney').value) || 0);
    const wallet = Number(((st.player || {}).wallet || {}).money || 0);
    if (money < 0 || money > wallet) {
      showMarriageResult('聘礼 ' + money + ' 文超过钱包余额（' + wallet + ' 文）；先改小聘礼。', true);
      return;
    }
    const item = el('proposeItem').value;
    const gift = giftRowsOf(st).filter(function (g) { return g.item === item; })[0] || null;
    const qty = Math.max(1, Math.min(MARRIAGE_RULES.giftQtyMax,
      Math.floor(Number(el('proposeQty').value) || 1)));
    if (item && (!gift || qty > gift.qty)) {
      showMarriageResult('你背包里没有足够的这件信物。', true);
      return;
    }
    const name = row.npc.name || row.npc.id;
    const btn = el('proposeGo');
    btn.disabled = true;
    btn.textContent = '求婚中…';
    showMarriageResult('', false);
    try {
      const res = await API.action(account, 'marriage_propose', {
        target: row.npc.id,
        gift_money: money,
        gift_item: item || '',
        gift_qty: item ? qty : 1
      }, Number(st.tick || 0));
      const data = res.data || {};
      if (res.authRequired || res.status === 401) return;
      if (res.status === 409 || data.ok === false || res.status === 0) {
        const r = notifyError('向 ' + name + ' 求婚', data, res.status);
        showMarriageResult('求婚没有完成：' + r.text, true);
        return;
      }
      if (data.status === 'proposed' || data.readonly) {
        showMarriageResult('只读模式：求婚提案已提交，世界不会落地。', true);
        pushLog('proposal', '求婚提案：' + name, data.intent_id || '');
        return;
      }
      const details = data.details || {};
      const decision = String(details.decision || '');
      const mar = details.marriage || {};
      if (decision === 'accept') {
        const spouse = mar.spouse_name || name;
        const since = mar.since_tick == null ? data.tick : mar.since_tick;
        showMarriageResult('与 ' + spouse + ' 成婚，tick ' + since, false);
        toast('成婚：' + spouse);
        pushLog('ok', '求婚成功', '配偶 ' + spouse + ' · tick ' + since);
      } else if (decision === 'courtship') {
        showMarriageResult(name + ' 说：再处处看（好感 ' + row.aff.toFixed(1) +
          ' / 信任 ' + row.trust.toFixed(1) + '；不扣聘礼与信物）。', false);
        toast(name + '：再处处看。');
        pushLog('ok', '求婚：' + name + ' 再处处看', 'courtship');
      } else {
        const why = row.trust < MARRIAGE_RULES.minTrust
          ? '信任还不足' : (row.aff < MARRIAGE_RULES.minAffection
            ? '感情还没到谈婚论嫁的地步' : '对方拒绝了这次提亲');
        const text = '对方婉拒了这次提亲：' + why + '（好感 ' + row.aff.toFixed(1) +
          ' / 信任 ' + row.trust.toFixed(1) + '）；聘礼与信物未扣除。';
        showMarriageResult(text, true);
        toast(text, true);
        pushLog('rejected', '求婚：' + name, why);
      }
      await loadState(account);
    } catch (err) {
      showMarriageResult('求婚请求失败：' + (err.message || err), true);
    } finally {
      btn.disabled = false;
      btn.textContent = '求婚';
      if (state.st) updateProposeEstimate();
    }
  }

  function openDivorce() {
    const m = marriageOf(state.st);
    if (String(m.status || '') !== 'married') return;
    const name = m.spouse_name || m.spouse_id || '配偶';
    el('divorceText').textContent = '与 ' + name + ' 和离后恢复单身。聘礼不退还，共同财产不分割。';
    el('divorceAvatar').textContent = firstChar(name);
    el('divorceError').classList.add('hidden');
    el('divorceError').textContent = '';
    el('divorceModal').classList.remove('hidden');
    setTimeout(function () { el('divorceCancel').focus(); }, 0);
  }

  function closeDivorce() {
    const modal = el('divorceModal');
    if (!modal || modal.classList.contains('hidden')) return;
    modal.classList.add('hidden');
    const back = el('marriageEndBtn');
    if (back) back.focus();
  }

  async function doMarriageEnd() {
    const account = state.session && state.session.accountId;
    if (!account) { toast('先进入世界。', true); return; }
    if (!requireAuthForWrite()) return;
    const m = marriageOf(state.st);
    const name = m.spouse_name || m.spouse_id || '配偶';
    const btn = el('divorceConfirm');
    btn.disabled = true;
    btn.textContent = '和离中…';
    el('divorceError').classList.add('hidden');
    try {
      const res = await API.action(account, 'marriage_end', {},
        Number((state.st || {}).tick || 0));
      const data = res.data || {};
      if (res.authRequired || res.status === 401) return;
      if (res.status === 409 || data.ok === false || res.status === 0) {
        const r = notifyError('与 ' + name + ' 和离', data, res.status);
        const box = el('divorceError');
        box.textContent = r.text;
        box.classList.remove('hidden');
        return;
      }
      if (data.status === 'proposed' || data.readonly) {
        el('divorceError').textContent = '只读模式：和离提案已提交，世界不会落地。';
        el('divorceError').classList.remove('hidden');
        return;
      }
      const tick = data.tick == null ? (state.st || {}).tick : data.tick;
      showMarriageResult('已与 ' + name + ' 和离，tick ' + tick, false);
      toast('已与 ' + name + ' 和离。');
      pushLog('ok', '和离：' + name, 'tick ' + tick);
      closeDivorce();
      await loadState(account);
    } catch (err) {
      const box = el('divorceError');
      box.textContent = '和离请求失败：' + (err.message || err);
      box.classList.remove('hidden');
    } finally {
      btn.disabled = false;
      btn.textContent = '确认和离';
    }
  }

  function doRestPlaceholder() {
    const text = '体力恢复中（休息接口尚未接入；这里只做占位提示，不发请求）。';
    el('restResult').textContent = text;
    toast(text);
    pushLog('proposal', '休息占位', 'rest 动作待后端上线');
  }

  /* ------------------------------------------------------------- 门派面板 */
  /* 字段缺口一律显示「暂不可用」，不按 0 冒充，也不 mock。 */
  const SECT_GAP_BOND = '暂不可用（缺 disciples[].affection/trust）';
  const SECT_GAP_SETTLE = '暂不可用（缺 disciples[].settled_today）';

  function sectDataOf(st) {
    const top = st && st.sect;
    const pv = st && st.player && st.player.sect;
    if (top === undefined && pv === undefined) {
      return { missing: true, inSect: false, sect: null };
    }
    const sect = (top && top.sect) || (pv && pv.sect) || null;
    return { missing: false, inSect: !!sect, sect: sect };
  }

  function sectReputation(st) {
    const rep = ((st && st.growth) || {}).reputation;
    if (!rep || rep.value == null || !isFinite(Number(rep.value))) return null;
    return Number(rep.value);
  }

  function sectMoney(st) {
    const money = (((st || {}).player || {}).wallet || {}).money;
    if (money == null || !isFinite(Number(money))) return null;
    return Number(money);
  }

  function sectMaxSkill(st) {
    const skills = (((st || {}).growth || {}).skill || {}).skills ||
      ((st || {}).player || {}).skills || {};
    const vals = Object.keys(skills).map(function (k) { return Number(skills[k]); })
      .filter(function (v) { return isFinite(v); });
    return vals.length ? Math.max.apply(null, vals) : null;
  }

  function sectBondOf(st, id) {
    let aff = null;
    let trust = null;
    (((st || {}).player || {}).relations || []).forEach(function (rel) {
      if (!rel || String(rel.target) !== String(id)) return;
      const a = Number(rel.affection);
      const t = Number(rel.trust);
      if (isFinite(a) && (aff == null || a > aff)) aff = a;
      if (isFinite(t) && (trust == null || t > trust)) trust = t;
    });
    return { aff: aff, trust: trust };
  }

  function sectNum(value, digits) {
    const n = Number(value);
    if (!isFinite(n)) return '—';
    const d = digits == null ? 0 : digits;
    return d ? n.toFixed(d).replace(/\.0+$/, '') : String(Math.round(n));
  }

  function sectGate(label, cur, need, unit) {
    const suffix = unit || '';
    let text;
    let tone = '';
    if (cur == null || !isFinite(Number(cur))) {
      text = '当前 暂不可用 / 需要 ' + need + suffix + ' / 差 暂不可用';
    } else {
      const c = Number(cur);
      const gap = Math.max(0, Number(need) - c);
      tone = gap > 0 ? ' bad' : ' ok';
      text = '当前 ' + sectNum(c, Math.abs(c - Math.round(c)) < 1e-9 ? 0 : 1) + suffix +
        ' / 需要 ' + need + suffix + ' / ' + (gap > 0 ? ('差 ' + sectNum(gap, 1) + suffix) : '已达标');
    }
    return '<div class="hint sect-gate' + tone + '">' + escapeHtml(label + '：' + text) + '</div>';
  }

  function sectCapGate(label, cur, need) {
    if (cur == null || !isFinite(Number(cur))) {
      return '<div class="hint sect-gate">' + escapeHtml(label + '：当前 暂不可用 / 上限 ' + need + ' / 差 暂不可用') + '</div>';
    }
    const gap = Number(need) - Number(cur);
    return '<div class="hint sect-gate' + (gap > 0 ? ' ok' : ' bad') + '">' +
      escapeHtml(label + '：当前 ' + sectNum(cur) + ' / 上限 ' + need + ' / ' + (gap > 0 ? ('还差 ' + gap + ' 人') : '已满')) +
      '</div>';
  }

  function sectLimitsOf(sect) {
    const l = (sect && sect.limits) || {};
    return {
      disciple_cap: l.disciple_cap == null ? SECT_RULES.discipleCap : Number(l.disciple_cap),
      dues_daily: l.dues_daily == null ? SECT_RULES.duesDaily : Number(l.dues_daily),
      stipend_daily: l.stipend_daily == null ? SECT_RULES.stipendDaily : Number(l.stipend_daily),
      arrears_expel: l.arrears_expel == null ? SECT_RULES.arrearsExpel : Number(l.arrears_expel)
    };
  }

  function sectBusy(key) { return !!state.sect.busy[key]; }

  function setSectResult(text, bad) {
    state.sect.result = text || '';
    state.sect.resultBad = !!bad;
    const box = el('sectResult');
    if (!box) return;
    box.textContent = state.sect.result;
    box.classList.toggle('bad', !!bad);
    box.classList.toggle('ok', !!text && !bad);
  }

  function sectSnapOf(data, st) {
    const details = (data && data.details) || {};
    // 真实服务返回 details.sect 为 {in_sect, sect} 包装层；mock/旧结构可能是快照本身。
    const raw = details.sect;
    const snap = (raw && raw.sect) ? raw.sect : (raw || sectDataOf(st).sect);
    return snap || null;
  }

  function sectSuccessText(prefix, snap) {
    if (!snap) return prefix + '（服务端未返回门派快照，请刷新后查看真实数字）';
    const treasury = snap.treasury && snap.treasury.balance != null
      ? (Number(snap.treasury.balance) + ' 文') : '暂不可用（缺 treasury.balance）';
    const roster = snap.member_count != null
      ? (Number(snap.member_count) + ' 人') : '暂不可用（缺 member_count）';
    return prefix + ' · 金库 ' + treasury + ' · 名册 ' + roster;
  }

  function renderSectHint(st) {
    const hint = el('sectHint');
    if (!hint) return;
    const data = sectDataOf(st);
    if (!state.session || !state.session.accountId) {
      hint.textContent = '登录并进入世界后查看门派。';
      return;
    }
    if (data.missing) {
      hint.textContent = '门派数据暂不可用：GET /api/player/state 未返回顶层 sect 字段。';
      return;
    }
    if (!data.inSect) {
      hint.textContent = '尚未入派 · 可立派（' + SECT_RULES.foundCost + ' 文 / 声望 ' +
        SECT_RULES.minReputation + '）或加入已有门派。';
      return;
    }
    const sect = data.sect;
    const role = sect.role === 'leader' ? '门主' : '弟子';
    hint.textContent = (sect.name || sect.id || '门派') + ' · ' + role +
      ' · ' + (sect.location_name || sect.location || '驻地未知') +
      ' · tick ' + (st.tick == null ? '—' : st.tick);
  }

  function renderSectRoster(st, sect, isLeader) {
    const rows = (sect && sect.disciples) || [];
    if (!rows.length) {
      return '<div class="commerce-empty">名册暂无弟子。</div>';
    }
    const entityId = String((state.session && state.session.entityId) ||
      ((st.player || {}).entity_id) || '');
    const limits = sectLimitsOf(sect);
    return '<div class="sect-roster">' + rows.map(function (d) {
      const self = entityId && String(d.id) === entityId;
      const bond = sectBondOf(st, d.id);
      const arrears = Number(d.arrears || 0);
      const days = Math.ceil(arrears / (limits.stipend_daily || SECT_RULES.stipendDaily));
      const bondText = (bond.aff == null || bond.trust == null)
        ? ('好感/信任：' + SECT_GAP_BOND)
        : ('好感 ' + sectNum(bond.aff, 1) + ' · 信任 ' + sectNum(bond.trust, 1));
      const settleText = '逐人结算：' + SECT_GAP_SETTLE;
      const statusText = self
        ? (arrears > 0
          ? ('俸禄：欠饷 ' + sectNum(arrears) + ' 文（约 ' + days + ' 天，日俸 ' +
            sectNum(limits.stipend_daily) + ' 文，满 ' + sectNum(limits.arrears_expel) + ' 文离派）')
          : '俸禄：已结清（欠饷 0 文）')
        : ('欠饷 ' + sectNum(arrears) + ' 文' + (arrears > 0 ? ('（约 ' + days + ' 天）') : ''));
      return '<div class="sect-row' + (self ? ' self' : '') + '" data-sect-disciple="' + escapeHtml(d.id) + '">' +
        '<div class="sect-row-head"><b>' + escapeHtml(d.name || d.id) + '</b>' +
        (self ? '<span class="pill">你</span>' : '') +
        '<span class="pill">' + escapeHtml(d.at_sect ? '在驻地' : '外出') + '</span>' +
        (d.presence ? '<span class="hint tiny">' + escapeHtml(d.presence) + '</span>' : '') +
        '</div>' +
        '<div class="sect-meta">技能 ' +
        (d.skill == null ? '暂不可用（缺 disciples[].skill）' : sectNum(d.skill, 2)) +
        ' · ' + bondText + '</div>' +
        '<div class="sect-meta">' + settleText + ' · ' + statusText + '</div>' +
        (isLeader ? '<div class="sect-actions"><button type="button" class="ghost small commerce-btn"' +
          ' data-sect-expel="' + escapeHtml(d.id) + '"' +
          (sectBusy('expel:' + d.id) ? ' disabled' : '') + '>' +
          (sectBusy('expel:' + d.id) ? '处理中…' : '驱逐') + '</button></div>' : '') +
        '</div>';
    }).join('') + '</div>';
  }

  function renderSectRecruit(st, sect, isLeader) {
    if (!isLeader) {
      return '<div class="hint">只有门主可以收徒、派俸、驱逐与金库操作。</div>';
    }
    const limits = sectLimitsOf(sect);
    const cap = limits.disciple_cap;
    const member = sect.member_count == null ? null : Number(sect.member_count);
    const masterSkill = sectMaxSkill(st);
    const npcs = ((st.present_npcs || st.nearby || []).filter(function (n) {
      return n && !String(n.id).startsWith('player:');
    }));
    let html = '<div class="commerce-sub"><b>收徒</b><span class="hint">好感 ' +
      SECT_RULES.minAffection + ' / 信任 ' + SECT_RULES.minTrust + ' / 掌门技能 ' +
      SECT_RULES.minMasterSkill + '</span></div>';
    html += sectCapGate('名册人数', member, cap);
    html += sectGate('掌门技能', masterSkill, SECT_RULES.minMasterSkill, '');
    if (!npcs.length) {
      return html + '<div class="commerce-empty">当前地点没有可收的 NPC。</div>';
    }
    html += '<div class="commerce-list">' + npcs.map(function (npc) {
      // present_npcs.bond 走 affection_between，不含 sworn 关系；收徒裁决用 _bond
      // （所有 active 关系取最大）。这里用 player.relations 里同一 NPC 的真实关系补齐，
      // 让页面门槛与后端裁决一致；两边都没有才显示「暂不可用」。
      const relBond = sectBondOf(st, npc.id);
      const npcBond = npc.bond == null ? null : Number(npc.bond);
      const npcTrust = npc.trust == null ? null : Number(npc.trust);
      const bond = (npcBond == null && relBond.aff == null) ? null
        : Math.max(npcBond == null ? 0 : npcBond, relBond.aff == null ? 0 : relBond.aff);
      const trust = (npcTrust == null && relBond.trust == null) ? null
        : Math.max(npcTrust == null ? 0 : npcTrust, relBond.trust == null ? 0 : relBond.trust);
      const targetSkill = npc.skills && npc.skills.level != null ? Number(npc.skills.level) : null;
      const affOk = bond != null && bond >= SECT_RULES.minAffection;
      const trustOk = trust != null && trust >= SECT_RULES.minTrust;
      const skillOk = masterSkill != null && masterSkill >= SECT_RULES.minMasterSkill &&
        (targetSkill == null || targetSkill >= 0.2);
      const capOk = member == null || member < cap;
      const busy = sectBusy('recruit:' + npc.id);
      const blocked = !affOk || !trustOk || !skillOk || !capOk;
      return '<div class="commerce-row sect-row" data-recruit-row="' + escapeHtml(npc.id) + '">' +
        '<div class="row-main"><span class="row-name">' + escapeHtml(npc.name || npc.id) +
        '<span class="pill">' + escapeHtml(npc.role || '') + '</span></span>' +
        '<span class="row-meta">' + escapeHtml(sectGateText('好感', bond, SECT_RULES.minAffection)) +
        ' · ' + escapeHtml(sectGateText('信任', trust, SECT_RULES.minTrust)) +
        ' · 技能 ' + (targetSkill == null ? '暂不可用' : sectNum(targetSkill, 2)) + '/0.2' +
        '</span></div>' +
        '<div class="row-actions"><button type="button" class="ghost small commerce-btn"' +
        ' data-sect-recruit="' + escapeHtml(npc.id) + '"' +
        ((blocked || busy) ? ' disabled' : '') + '>' +
        (busy ? '处理中…' : (capOk ? '收徒' : '名额已满')) + '</button></div></div>';
    }).join('') + '</div>';
    return html;
  }

  function sectGateText(label, cur, need) {
    if (cur == null || !isFinite(Number(cur))) return label + ' 暂不可用/需要 ' + need;
    const gap = Math.max(0, need - Number(cur));
    return label + ' ' + sectNum(cur, 1) + '/' + need + (gap > 0 ? ('（差 ' + sectNum(gap, 1) + '）') : '（达标）');
  }

  function renderSectLeader(st, sect) {
    const limits = sectLimitsOf(sect);
    const treasury = sect.treasury && sect.treasury.balance != null
      ? Number(sect.treasury.balance) : null;
    const settled = sect.settled_today == null
      ? '暂不可用（缺 settled_today）'
      : (sect.settled_today ? '今日已结算' : '今日未结算');
    const money = sectMoney(st);
    const withdrawMax = treasury == null ? null : Math.max(0,
      Math.min(SECT_RULES.withdrawCap, treasury - SECT_RULES.treasuryReserve));
    const payBusy = sectBusy('pay');
    const canPay = !sect.settled_today && Number(sect.member_count || 0) > 1;
    const depositRaw = Number(state.sect.deposit);
    const depositVal = state.sect.deposit === '' ? '' : (isFinite(depositRaw) ? depositRaw : '');
    const withdrawRaw = Number(state.sect.withdraw);
    const withdrawVal = state.sect.withdraw === '' ? '' : (isFinite(withdrawRaw) ? withdrawRaw : '');
    let html = '<div class="sect-summary">' +
      '<div>门派：<b>' + escapeHtml(sect.name || sect.id) + '</b> · 门主：' +
      escapeHtml(sect.leader_name || sect.leader || '—') + '</div>' +
      '<div class="sect-meta">' + escapeHtml(sectCapLine(sect)) + '</div>' +
      '<div class="sect-meta">金库：' + (treasury == null ? '暂不可用（缺 treasury.balance）' : (sectNum(treasury) + ' 文')) +
      ' · 门级结算：' + escapeHtml(settled) + '</div></div>';

    html += sectRosterBlock(st, sect, true);

    html += '<div class="commerce-sub"><b>派俸禄</b><span class="hint">每日一次；会费 ' +
      sectNum(limits.dues_daily) + ' 文/人，俸禄 ' + sectNum(limits.stipend_daily) + ' 文/人</span></div>';
    html += '<div class="hint' + (settled.indexOf('已结算') >= 0 ? ' bad' : '') + '">' +
      escapeHtml(settled) + ' · 弟子 ' + sectNum(Number(sect.member_count || 0) - 1) + ' 人</div>';
    html += '<button type="button" class="primary small commerce-btn" id="sectPayGo" data-sect-pay="1"' +
      ((!canPay || payBusy) ? ' disabled' : '') + '>' +
      (payBusy ? '处理中…' : (sect.settled_today ? '今日已结算' : (canPay ? '派俸禄' : '门中暂无弟子'))) + '</button>';

    html += renderSectRecruit(st, sect, true);

    html += '<div class="commerce-sub"><b>金库</b><span class="hint">存入 / 取出（单次上限 ' +
      SECT_RULES.withdrawCap + ' 文，须留 ' + SECT_RULES.treasuryReserve + ' 文）</span></div>';
    html += '<div class="sect-meta">当前余额 ' +
      (treasury == null ? '暂不可用' : (sectNum(treasury) + ' 文')) + ' / 保留金 ' +
      SECT_RULES.treasuryReserve + ' 文 / 单次上限 ' + SECT_RULES.withdrawCap + ' 文 / 最多可提 ' +
      (withdrawMax == null ? '暂不可用' : (sectNum(withdrawMax) + ' 文')) + '</div>';
    html += '<div class="commerce-inline">' +
      '<label for="sectDepositAmount">存入</label>' +
      '<input id="sectDepositAmount" type="number" min="1" step="1" inputmode="numeric" value="' +
      escapeHtml(depositVal) + '" />' +
      '<span class="hint">钱包 ' + (money == null ? '暂不可用' : (sectNum(money) + ' 文')) + '</span>' +
      '<button type="button" class="ghost small commerce-btn" id="sectDepositGo" data-sect-deposit="1"' +
      ((!isFinite(depositVal) || depositVal < 1 || (money != null && depositVal > money) || sectBusy('deposit')) ? ' disabled' : '') + '>' +
      (sectBusy('deposit') ? '处理中…' : '存入') + '</button></div>';
    html += '<div class="commerce-inline">' +
      '<label for="sectWithdrawAmount">取出</label>' +
      '<input id="sectWithdrawAmount" type="number" min="1" step="1" inputmode="numeric" value="' +
      escapeHtml(withdrawVal) + '" />' +
      '<span class="hint">可提上限 ' + (withdrawMax == null ? '暂不可用' : (sectNum(withdrawMax) + ' 文')) + '</span>' +
      '<button type="button" class="ghost small commerce-btn" id="sectWithdrawGo" data-sect-withdraw="1"' +
      ((!isFinite(withdrawVal) || withdrawVal < 1 || withdrawVal > (withdrawMax == null ? 0 : withdrawMax) || sectBusy('withdraw')) ? ' disabled' : '') + '>' +
      (sectBusy('withdraw') ? '处理中…' : '取出') + '</button></div>';
    if (withdrawMax != null && withdrawMax <= 0) {
      html += '<div class="hint bad">' + escapeHtml(reasonText(treasury != null && treasury <= SECT_RULES.treasuryReserve
        ? 'treasury_reserve' : 'insufficient_treasury')) + '</div>';
    }
    return html;
  }

  function sectCapLine(sect) {
    const cap = sectLimitsOf(sect).disciple_cap;
    const member = sect.member_count == null ? null : Number(sect.member_count);
    if (member == null) return '名册 暂不可用（缺 member_count）/ 上限 ' + cap;
    return '名册 ' + member + ' / 上限 ' + cap + ' / ' + (member >= cap ? '已满' : ('还差 ' + (cap - member) + ' 人'));
  }

  function sectRosterBlock(st, sect, isLeader) {
    let html = '<div class="commerce-sub"><b>名册</b><span class="hint">' +
      escapeHtml(sectCapLine(sect)) + '</span></div>';
    html += '<div class="sect-meta">门级结算：' + escapeHtml(
      sect.settled_today == null ? '暂不可用（缺 settled_today）' : (sect.settled_today ? '今日已结算' : '今日未结算')
    ) + ' · 逐人结算：' + SECT_GAP_SETTLE + '</div>';
    html += renderSectRoster(st, sect, isLeader);
    return html;
  }

  function renderSectDisciple(st, sect) {
    const limits = sectLimitsOf(sect);
    const body = '<div class="sect-summary">' +
      '<div>门派：<b>' + escapeHtml(sect.name || sect.id) + '</b> · 门主：' +
      escapeHtml(sect.leader_name || sect.leader || '—') + '</div>' +
      '<div class="sect-meta">' + escapeHtml(sectCapLine(sect)) + ' · 金库：' +
      (sect.treasury && sect.treasury.balance != null
        ? (sectNum(sect.treasury.balance) + ' 文') : '暂不可用（缺 treasury.balance）') + '</div></div>' +
      sectRosterBlock(st, sect, false) +
      '<div class="hint">只有门主可以收徒、派俸、驱逐与金库操作。</div>' +
      '<div class="hint tiny">你的俸禄：日俸 ' + sectNum(limits.stipend_daily) +
      ' 文；欠饷满 ' + sectNum(limits.arrears_expel) + ' 文当日离派（债务保留）。</div>';
    return body;
  }

  function renderSectFound(st) {
    const money = sectMoney(st);
    const rep = sectReputation(st);
    const name = String(state.sect.name || '');
    const nameOk = name.trim().length >= 2 && name.trim().length <= 12;
    const moneyOk = money != null && money >= SECT_RULES.foundCost;
    const repOk = rep != null && rep >= SECT_RULES.minReputation;
    return '<div class="commerce-sub"><b>立派</b><span class="hint">' + SECT_RULES.foundCost +
      ' 文（注册费 ' + SECT_RULES.registrationFee + ' + 启动金 ' + SECT_RULES.startupCapital + '） · 声望 ' +
      SECT_RULES.minReputation + '</span></div>' +
      '<div class="commerce-inline"><label for="sectName">门派名</label>' +
      '<input id="sectName" type="text" maxlength="12" placeholder="2-12 字" value="' +
      escapeHtml(name) + '" /></div>' +
      '<div class="commerce-inline"><label for="sectDoctrine">宗旨</label>' +
      '<input id="sectDoctrine" type="text" maxlength="60" placeholder="可留空，默认「守望相济，不欺乡里」" value="' +
      escapeHtml(state.sect.doctrine || '') + '" /></div>' +
      sectGate('立派费用', money, SECT_RULES.foundCost, ' 文') +
      sectGate('声望', rep, SECT_RULES.minReputation, '') +
      '<div class="hint bad' + (nameOk ? ' hidden' : '') + '" id="sectNameHint">门派名需 2-12 字；当前 ' +
      escapeHtml(name.trim().length) + ' 字。</div>' +
      '<button type="button" class="primary small commerce-btn" id="sectFoundGo" data-sect-found="1"' +
      ((!nameOk || !moneyOk || !repOk || sectBusy('found')) ? ' disabled' : '') + '>' +
      (sectBusy('found') ? '处理中…' : '立派') + '</button>';
  }

  function renderSectJoin(st) {
    const target = String(state.sect.joinTarget || ((st.location || {}).id || ''));
    return '<div class="commerce-sub"><b>加入已有门派</b><span class="hint">join_sect 已开放</span></div>' +
      '<div class="commerce-inline"><label for="sectJoinTarget">门派 id / 驻地</label>' +
      '<input id="sectJoinTarget" type="text" maxlength="32" placeholder="如 sect:u_demo 或 market" value="' +
      escapeHtml(target) + '" /></div>' +
      '<div class="hint">暂不可用：后端未提供门派列表接口（无 factions[]），需填写门派 id 或驻地 id。</div>' +
      '<button type="button" class="primary small commerce-btn" id="sectJoinGo" data-sect-join="1"' +
      ((!target.trim() || sectBusy('join')) ? ' disabled' : '') + '>' +
      (sectBusy('join') ? '处理中…' : '加入已有门派') + '</button>';
  }

  function renderSectPane(st) {
    const body = el('sectBody');
    if (!body) return;
    renderSectHint(st);
    if (!state.session || !state.session.accountId) {
      body.innerHTML = '';
      setSectResult('', false);
      return;
    }
    const data = sectDataOf(st);
    if (data.missing) {
      body.innerHTML = '<div class="commerce-empty">门派数据暂不可用：GET /api/player/state 未返回顶层 sect 字段。</div>';
      return;
    }
    if (!data.inSect) {
      body.innerHTML = renderSectFound(st) + '<div class="sect-divider"></div>' + renderSectJoin(st);
      return;
    }
    body.innerHTML = data.sect.role === 'leader'
      ? renderSectLeader(st, data.sect)
      : renderSectDisciple(st, data.sect);
  }

  async function sectAction(verb, args, label, busyKey) {
    const account = state.session && state.session.accountId;
    if (!account) {
      toast('先进入世界，才能操作门派。', true);
      showEntry(true);
      return null;
    }
    if (!requireAuthForWrite()) return null;
    if (busyKey && sectBusy(busyKey)) return null;
    if (busyKey) {
      state.sect.busy[busyKey] = true;
      renderSectPane(state.st || {});
    }
    try {
      const before = state.st || {};
      const res = await API.action(account, verb, args || {}, before.tick == null ? null : before.tick)
        .catch(function (err) {
          return { status: 0, data: { ok: false, message: '请求失败：' + (err.message || err) } };
        });
      const data = res.data || {};
      if (res.authRequired || res.status === 401) return null;
      if (res.status === 409 || data.ok === false || res.status === 0) {
        const reason = notifyError(label, data, res.status);
        setSectResult(reason.text, true);
        return null;
      }
      if (data.status === 'proposed' || data.readonly) {
        setSectResult('只读模式：' + label + '提案已提交，世界不会落地。', true);
        pushLog('proposal', label + '提案', data.intent_id || '');
        return null;
      }
      const next = await loadState(account);
      return { res: res, data: data, st: next };
    } finally {
      if (busyKey) {
        delete state.sect.busy[busyKey];
        renderSectPane(state.st || {});
      }
    }
  }

  async function doSectFound() {
    const st = state.st;
    if (!st) return;
    const name = String(state.sect.name || '').trim();
    if (name.length < 2 || name.length > 12) {
      setSectResult('门派名需 2-12 字；当前 ' + name.length + ' 字。', true);
      return;
    }
    const money = sectMoney(st);
    const rep = sectReputation(st);
    if (money != null && money < SECT_RULES.foundCost) {
      setSectResult('立派还差 ' + (SECT_RULES.foundCost - money) + ' 文（现有 ' + money + ' 文）。', true);
      return;
    }
    if (rep != null && rep < SECT_RULES.minReputation) {
      setSectResult('声望还差 ' + sectNum(SECT_RULES.minReputation - rep, 1) +
        '（现有 ' + sectNum(rep, 1) + ' / 需要 ' + SECT_RULES.minReputation + '）。', true);
      return;
    }
    const out = await sectAction('found_sect', {
      name: name,
      doctrine: String(state.sect.doctrine || '').trim().slice(0, 60)
    }, '立派', 'found');
    if (!out) return;
    const snap = sectSnapOf(out.data, out.st);
    setSectResult(sectSuccessText('立派成功：' + name, snap), false);
    toast('立派成功：' + name);
    pushLog('ok', '立派：' + name, 'event_seq ' + (out.data.event_seq == null ? '?' : out.data.event_seq));
  }

  async function doSectJoin() {
    const st = state.st;
    if (!st) return;
    const target = String(state.sect.joinTarget || ((st.location || {}).id || '')).trim();
    if (!target) {
      setSectResult('请填写门派 id 或驻地 id。', true);
      return;
    }
    const out = await sectAction('join_sect', { target: target, dues: 0 }, '加入门派', 'join');
    if (!out) return;
    const snap = sectSnapOf(out.data, out.st);
    setSectResult(sectSuccessText('已加入门派', snap), false);
    toast('已加入门派。');
    pushLog('ok', '加入门派：' + target, 'event_seq ' + (out.data.event_seq == null ? '?' : out.data.event_seq));
  }

  async function doSectRecruit(targetId) {
    const st = state.st;
    const npc = ((st.present_npcs || st.nearby || []).filter(function (n) { return n.id === targetId; })[0]);
    if (!npc) {
      setSectResult(reasonText('target_not_found'), true);
      return;
    }
    const out = await sectAction('recruit_disciple', { target: targetId }, '收徒 ' + (npc.name || targetId),
      'recruit:' + targetId);
    if (!out) return;
    const snap = sectSnapOf(out.data, out.st);
    setSectResult(sectSuccessText('已收 ' + (npc.name || targetId) + ' 为徒', snap), false);
    toast('已收 ' + (npc.name || targetId) + ' 为徒。');
    pushLog('ok', '收徒：' + (npc.name || targetId), 'event_seq ' + (out.data.event_seq == null ? '?' : out.data.event_seq));
  }

  async function doSectPay() {
    const out = await sectAction('pay_disciples', {}, '派俸禄', 'pay');
    if (!out) return;
    const snap = sectSnapOf(out.data, out.st);
    const settlement = (out.data.details || {}).settlement || {};
    const dues = settlement.dues == null ? null : Number(settlement.dues);
    const stipend = settlement.stipend == null ? null : Number(settlement.stipend);
    const leavers = (settlement.leavers || []).length;
    let text = '日结完成：收会费 ' + (dues == null ? '暂不可用' : (dues + ' 文')) +
      '，发俸禄 ' + (stipend == null ? '暂不可用' : (stipend + ' 文'));
    if (snap && snap.treasury && snap.treasury.balance != null) {
      text += '，金库 ' + Number(snap.treasury.balance) + ' 文';
    } else {
      text += '，金库 暂不可用（缺 treasury.balance）';
    }
    if (snap && snap.member_count != null) text += '，名册 ' + Number(snap.member_count) + ' 人';
    else text += '，名册 暂不可用（缺 member_count）';
    if (leavers) text += '；' + leavers + ' 名弟子欠饷离派';
    setSectResult(text, false);
    toast('日结完成。');
    pushLog('ok', '派俸禄', text);
  }

  function openSectExpel(targetId) {
    const data = sectDataOf(state.st || {});
    const sect = data.sect;
    if (!sect) return;
    const d = (sect.disciples || []).filter(function (x) { return String(x.id) === String(targetId); })[0];
    if (!d) return;
    state.sect.confirmTarget = String(targetId);
    el('sectModalText').textContent = '把 ' + (d.name || d.id) + ' 逐出 ' + (sect.name || sect.id) + '？';
    el('sectModalAvatar').textContent = firstChar(d.name || '逐');
    el('sectModalError').textContent = '';
    el('sectModalError').classList.add('hidden');
    el('sectModal').classList.remove('hidden');
    setTimeout(function () { el('sectModalCancel').focus(); }, 0);
  }

  function closeSectExpel() {
    const modal = el('sectModal');
    if (!modal || modal.classList.contains('hidden')) return;
    modal.classList.add('hidden');
    state.sect.confirmTarget = '';
  }

  async function doSectExpel() {
    const targetId = state.sect.confirmTarget;
    if (!targetId) return;
    const data = sectDataOf(state.st || {});
    const d = ((data.sect || {}).disciples || []).filter(function (x) { return String(x.id) === String(targetId); })[0];
    const name = (d && d.name) || targetId;
    const btn = el('sectModalConfirm');
    btn.disabled = true;
    btn.textContent = '处理中…';
    try {
      const out = await sectAction('expel_disciple', { target: targetId }, '驱逐 ' + name, 'expel:' + targetId);
      if (!out) {
        el('sectModalError').textContent = state.sect.result || '操作没有完成。';
        el('sectModalError').classList.remove('hidden');
        return;
      }
      closeSectExpel();
      const snap = sectSnapOf(out.data, out.st);
      setSectResult(sectSuccessText('已驱逐 ' + name, snap), false);
      toast('已驱逐 ' + name + '（欠薪债务仍保留）。');
      pushLog('ok', '驱逐弟子：' + name, 'event_seq ' + (out.data.event_seq == null ? '?' : out.data.event_seq));
    } finally {
      btn.disabled = false;
      btn.textContent = '确认驱逐';
    }
  }

  async function doSectDeposit() {
    const amount = Math.floor(Number(state.sect.deposit != null ? state.sect.deposit : el('sectDepositAmount').value));
    if (!isFinite(amount) || amount < 1) {
      setSectResult('存入金额必须是 1 文以上的整数。', true);
      return;
    }
    const out = await sectAction('sect_deposit', { amount: amount }, '金库存入', 'deposit');
    if (!out) return;
    const snap = sectSnapOf(out.data, out.st);
    const balance = snap && snap.treasury && snap.treasury.balance != null
      ? (Number(snap.treasury.balance) + ' 文') : '暂不可用（缺 treasury.balance）';
    setSectResult('已存入 ' + amount + ' 文 · 金库 ' + balance, false);
    toast('已存入 ' + amount + ' 文。');
    pushLog('ok', '金库存入 ' + amount + ' 文', '金库 ' + balance);
  }

  async function doSectWithdraw() {
    const amount = Math.floor(Number(state.sect.withdraw != null ? state.sect.withdraw : el('sectWithdrawAmount').value));
    if (!isFinite(amount) || amount < 1) {
      setSectResult('取出金额必须是 1 文以上的整数。', true);
      return;
    }
    const sect = sectDataOf(state.st || {}).sect || {};
    const treasury = sect.treasury && sect.treasury.balance != null ? Number(sect.treasury.balance) : null;
    if (amount > SECT_RULES.withdrawCap) {
      setSectResult(reasonText('withdraw_over_cap'), true);
      return;
    }
    if (treasury != null && treasury - amount < SECT_RULES.treasuryReserve) {
      setSectResult(reasonText('treasury_reserve'), true);
      return;
    }
    const out = await sectAction('sect_withdraw', { amount: amount }, '金库取出', 'withdraw');
    if (!out) return;
    const snap = sectSnapOf(out.data, out.st);
    const balance = snap && snap.treasury && snap.treasury.balance != null
      ? (Number(snap.treasury.balance) + ' 文') : '暂不可用（缺 treasury.balance）';
    setSectResult('已取出 ' + amount + ' 文 · 金库 ' + balance, false);
    toast('已取出 ' + amount + ' 文。');
    pushLog('ok', '金库取出 ' + amount + ' 文', '金库 ' + balance);
  }

  function renderCommerce(st) {
    if (!st) return;
    const block = el('commerceBlock');
    if (!block) return;
    // 正在输入数量/日薪/终点时不重建列表：8 秒轮询照旧更新 state，
    // 等表单失焦后再补一次渲染，绝不打断用户输入。
    if (commerceEditing()) {
      state.commerce.deferred = st;
      return;
    }
    state.commerce.deferred = null;
    const p = st.player || {};
    const loc = st.location || {};
    const money = Number((p.wallet || {}).money || 0);
    const own = ownShopOf(st);
    el('commerceHint').textContent = (loc.name || '这里') +
      ' · tick ' + (st.tick == null ? '—' : st.tick) +
      (own ? (' · 自有店铺 ' + tierLabel(own.kind)) : '');
    setCommerceTab(state.commerce.tab);
    renderBuyList(st, money);
    renderSellList(st);
    renderOwnedShop(st, own);
    renderTierList(st, own, money);
    renderEmployeeList(st, own);
    renderHireList(st, own);
    renderEscortForm(st);
    renderSectPane(st);
    renderMarriagePane(st);
    const opsReason = shopOpsDisabledReason(st, own);
    renderRestockSection(st, own, opsReason);
    renderPriceSection(st, own, opsReason);
    renderWithdrawSection(st, own, opsReason);
    renderLedger(st, own);
    if (state.commerce.tab === 'business' && own) ensureShopReport(st);
    renderSoonList();
  }

  function showReasonToast(context, code) {
    const text = reasonText(code, '');
    toast(text, true);
    recordError(context, code, text);
    pushLog('rejected', context + '：' + text, code);
    return text;
  }

  async function commerceAction(verb, args, label) {
    const account = state.session && state.session.accountId;
    if (!account) { toast('先进入世界。', true); showEntry(true); return null; }
    if (!requireAuthForWrite()) return null;
    const before = state.st || {};
    const res = await API.action(account, verb, args || {}, before.tick == null ? null : before.tick)
      .catch(function (err) {
        return { status: 0, data: { ok: false, message: '请求失败：' + (err.message || err) } };
      });
    const data = res.data || {};
    if (res.authRequired || res.status === 401) return null;
    if (res.status === 409 || data.ok === false || res.status === 0) {
      notifyError(label, data, res.status);
      return null;
    }
    if (data.status === 'proposed' || data.readonly) {
      toast('只读模式：已提交' + label + '提案（不落地）。');
      pushLog('proposal', label + '提案', data.intent_id || '');
      return null;
    }
    const st = await loadState(account);
    return { res: res, data: data, st: st, before: before };
  }

  async function doBuy(itemKey) {
    const st = state.st;
    const rows = ((st || {}).shop || {}).buy || [];
    const row = rows.filter(function (r) { return r.item === itemKey; })[0];
    if (!row) return showReasonToast('买货', 'item_not_available');
    const money = Number(((state.st || {}).player || {}).wallet ? state.st.player.wallet.money : 0) || 0;
    const max = buyLimitOf(row, money);
    if (max <= 0) return showReasonToast('买' + (row.name || itemKey), 'not_enough_money');
    const key = 'buy:' + itemKey;
    const input = document.querySelector('input[data-qty="' + key + '"]');
    const qty = Math.max(1, Math.min(max, Math.floor(Number((input && input.value) || state.commerce.qty[key]) || 1)));
    state.commerce.qty[key] = qty;
    const out = await commerceAction('buy', { item: itemKey, qty: qty }, '买' + (row.name || itemKey));
    if (!out) return;
    const beforeMoney = Number((((out.before || {}).player || {}).wallet || {}).money || 0);
    const afterMoney = Number((((out.st || {}).player || {}).wallet || {}).money || 0);
    const delta = afterMoney - beforeMoney;
    toast('买下 ' + (row.name || itemKey) + '×' + qty + ' · 钱包 ' + beforeMoney + ' → ' + afterMoney +
      ' 文（' + (delta > 0 ? '+' : '') + delta + '）');
    pushLog('ok', '买 ' + (row.name || itemKey) + '×' + qty + ' · ' + (delta > 0 ? '+' : '') + delta + ' 文',
      'event_seq ' + (out.data.event_seq == null ? '?' : out.data.event_seq));
  }

  async function doSell(itemKey) {
    const st = state.st;
    const rows = ((st || {}).shop || {}).sell || [];
    const row = rows.filter(function (r) { return r.item === itemKey; })[0];
    if (!row) return showReasonToast('卖货', 'item_missing');
    const max = Math.max(0, Math.min(Number(row.qty || 0), row.sell_limit == null ? Number(row.qty || 0) : Number(row.sell_limit)));
    if (max <= 0) return showReasonToast('卖' + (row.name || itemKey), 'item_missing');
    const key = 'sell:' + itemKey;
    const input = document.querySelector('input[data-qty="' + key + '"]');
    const qty = Math.max(1, Math.min(max, Math.floor(Number((input && input.value) || state.commerce.qty[key]) || 1)));
    state.commerce.qty[key] = qty;
    const out = await commerceAction('sell', { item: itemKey, qty: qty }, '卖' + (row.name || itemKey));
    if (!out) return;
    const beforeMoney = Number((((out.before || {}).player || {}).wallet || {}).money || 0);
    const afterMoney = Number((((out.st || {}).player || {}).wallet || {}).money || 0);
    const delta = afterMoney - beforeMoney;
    toast('卖出 ' + (row.name || itemKey) + '×' + qty + ' · 钱包 ' + beforeMoney + ' → ' + afterMoney +
      ' 文（' + (delta > 0 ? '+' : '') + delta + '）');
    pushLog('ok', '卖 ' + (row.name || itemKey) + '×' + qty + ' · ' + (delta > 0 ? '+' : '') + delta + ' 文',
      'event_seq ' + (out.data.event_seq == null ? '?' : out.data.event_seq));
  }

  async function doConsume() {
    const st = state.st;
    const foods = foodRowsOf(st);
    if (!foods.length) return showReasonToast('现吃', 'item_not_available');
    const row = foods[0];
    const beforeStamina = Math.round(Number(((state.st || {}).player || {}).stamina || 0));
    const out = await commerceAction('consume', { item: row.item, qty: 1 }, '现吃' + (row.name || row.item));
    if (!out) return;
    const afterStamina = Math.round(Number(((out.st || {}).player || {}).stamina || 0));
    const delta = afterStamina - beforeStamina;
    const beforeMoney = Number((((out.before || {}).player || {}).wallet || {}).money || 0);
    const afterMoney = Number((((out.st || {}).player || {}).wallet || {}).money || 0);
    state.commerce.consume = '现吃 ' + (row.name || row.item) + '×1：体力 ' + beforeStamina + ' → ' +
      afterStamina + '（' + (delta > 0 ? '+' : '') + delta + '）· 钱包 ' + beforeMoney + ' → ' +
      afterMoney + ' 文（' + (afterMoney - beforeMoney > 0 ? '+' : '') + (afterMoney - beforeMoney) + '）';
    el('consumeResult').textContent = state.commerce.consume;
    toast('现吃 ' + (row.name || row.item) + '：体力 +' + Math.max(0, delta));
    pushLog('ok', '现吃 ' + (row.name || row.item) + ' · 体力 +' + Math.max(0, delta),
      'event_seq ' + (out.data.event_seq == null ? '?' : out.data.event_seq));
  }

  async function doOpenShop(kind) {
    const tier = tierOf(kind);
    if (!tier) return showReasonToast('开店', 'bad_shop_type');
    const out = await commerceAction('open_shop', { shop_type: kind }, '开' + tier.label);
    if (!out) return;
    state.commerce.tab = 'business';
    renderCommerce(out.st);
    const own = ownShopOf(out.st);
    toast('已开' + tier.label + ' · 店内现金 ' + (own && own.cash != null ? own.cash : 0) + ' 文');
    pushLog('ok', '开' + tier.label + '（' + tier.rent + ' + ' + tier.stock + ' 文）',
      'event_seq ' + (out.data.event_seq == null ? '?' : out.data.event_seq));
  }

  async function doHire(targetId) {
    const st = state.st;
    const own = ownShopOf(st);
    if (!own) return showReasonToast('雇人', 'no_shop');
    const row = (st.present_npcs || []).filter(function (n) { return n.id === targetId; })[0];
    if (!row) return showReasonToast('雇人', 'target_not_found');
    const select = document.querySelector('select[data-pos-for="' + targetId + '"]');
    const position = (select && select.value) || state.commerce.pos[targetId] || '脚夫';
    state.commerce.pos[targetId] = position;
    const market = positionWage(position);
    const wage = Math.floor(Number(el('hireWage').value) || 0);
    if (market <= 0) return showReasonToast('雇' + row.name, 'unknown_position');
    if (wage < Math.round(market * SHOP_HIRE.wageMinRatio)) return showReasonToast('雇' + row.name, 'wage_too_low');
    if (wage > Math.round(market * SHOP_HIRE.wageMaxRatio)) return showReasonToast('雇' + row.name, 'wage_too_high');
    const out = await commerceAction('recruit',
      { target: targetId, position: position, wage: wage }, '雇' + row.name);
    if (!out) return;
    state.commerce.hire = '已雇佣 ' + row.name + ' 做 ' + position + '，日薪 ' + wage + ' 文。';
    el('hireHint').textContent = state.commerce.hire;
    toast(state.commerce.hire);
    pushLog('ok', '雇佣 ' + row.name + ' · ' + position + ' · ' + wage + ' 文/日',
      'event_seq ' + (out.data.event_seq == null ? '?' : out.data.event_seq));
  }

  function actionDetail(data, key, fallback) {
    const details = (data && data.details) || {};
    if (details[key] != null) return details[key];
    if (data && data[key] != null) return data[key];
    return fallback;
  }

  function shopBusy(key) {
    return !!state.commerce.shopops.busy[key];
  }

  function startShopBusy(key) {
    if (shopBusy(key)) return false;
    state.commerce.shopops.busy[key] = true;
    return true;
  }

  function endShopBusy(key) {
    delete state.commerce.shopops.busy[key];
    if (state.st) renderCommerce(state.st);
  }

  async function doRestock(itemKey) {
    const st = state.st;
    const own = ownShopOf(st);
    const reason = shopOpsDisabledReason(st, own);
    if (reason) return showReasonToast('进货', 'not_shop_owner');
    const row = shopStockRowsOf(st, own).filter(function (r) { return r.item === itemKey; })[0];
    if (!row) return showReasonToast('进货', 'item_not_available');
    const input = document.querySelector('input[data-restock-qty="' + itemKey + '"]');
    const rawQty = input && input.value !== '' ? input.value : state.commerce.shopops.qty[itemKey];
    const qty = Math.floor(Number(rawQty));
    if (!Number.isFinite(qty) || qty < 1 || qty > 20) {
      return showReasonToast('进货' + (row.name || itemKey), 'bad_arg:qty');
    }
    const key = 'restock:' + itemKey;
    if (!startShopBusy(key)) return;
    state.commerce.shopops.qty[itemKey] = qty;
    renderCommerce(st);
    try {
      const out = await commerceAction('restock', { item: itemKey, qty: qty }, '进货' + (row.name || itemKey));
      if (!out) return;
      const data = out.data || {};
      const before = actionDetail(data, 'stock_before', row.qty);
      const after = actionDetail(data, 'stock_after', row.qty + qty);
      const total = actionDetail(data, 'total', row.restock_price * qty);
      toast('进货 ' + (row.name || itemKey) + '×' + qty + '，花费 ' + total + ' 文，库存 ' +
        before + ' → ' + after);
      pushLog('ok', '进货 ' + (row.name || itemKey) + '×' + qty + '，花费 ' + total + ' 文，库存 ' +
        before + ' → ' + after, 'event_seq ' + (data.event_seq == null ? '?' : data.event_seq));
    } finally {
      endShopBusy(key);
    }
  }

  async function doSetPrice(itemKey) {
    const st = state.st;
    const own = ownShopOf(st);
    const reason = shopOpsDisabledReason(st, own);
    if (reason) return showReasonToast('改价', 'not_shop_owner');
    const row = shopStockRowsOf(st, own).filter(function (r) { return r.item === itemKey; })[0];
    if (!row) return showReasonToast('改价', 'item_not_available');
    const input = document.querySelector('input[data-price-input="' + itemKey + '"]');
    const rawPrice = input && input.value !== '' ? input.value : state.commerce.shopops.price[itemKey];
    const price = Math.floor(Number(rawPrice));
    const bounds = priceBoundsOf(row.base_price);
    if (!Number.isFinite(price) || price <= 0) {
      return showReasonToast('改价' + (row.name || itemKey), 'bad_arg:price');
    }
    if (bounds.base > 0 && (price < bounds.min || price > bounds.max)) {
      return showReasonToast('改价' + (row.name || itemKey), 'price_out_of_range');
    }
    const key = 'price:' + itemKey;
    if (!startShopBusy(key)) return;
    state.commerce.shopops.price[itemKey] = price;
    renderCommerce(st);
    try {
      const out = await commerceAction('set_price', { item: itemKey, price: price }, '改价' + (row.name || itemKey));
      if (!out) return;
      const data = out.data || {};
      const next = Number(actionDetail(data, 'price', price));
      const base = Number(actionDetail(data, 'base_price', row.base_price));
      const delta = Number(actionDetail(data, 'delta_pct',
        base > 0 ? Math.round(((next - base) / base) * 100) : 0));
      toast('改价 ' + (row.name || itemKey) + ' ' + row.price + ' → ' + next + ' 文（' +
        (delta > 0 ? '+' : '') + delta + '%）');
      pushLog('ok', '改价 ' + (row.name || itemKey) + ' → ' + next + ' 文（' +
        (delta > 0 ? '+' : '') + delta + '%）', 'event_seq ' + (data.event_seq == null ? '?' : data.event_seq));
    } finally {
      endShopBusy(key);
    }
  }

  async function doWithdraw() {
    const st = state.st;
    const own = ownShopOf(st);
    const reason = shopOpsDisabledReason(st, own);
    if (reason) return showReasonToast('提现', 'not_shop_owner');
    const amount = Math.floor(Number(el('withdrawAmount').value) || 0);
    if (!Number.isFinite(amount) || amount <= 0) {
      return showReasonToast('提现', 'bad_arg:amount');
    }
    const cash = shopCashOf(st, own);
    if (amount > cash) return showReasonToast('提现', 'insufficient_shop_cash');
    const key = 'withdraw';
    if (!startShopBusy(key)) return;
    renderCommerce(st);
    try {
      // 服务端 WithdrawAction 的参数名是 qty（不是 amount）。
      const out = await commerceAction('withdraw', { qty: amount }, '提现' + amount + ' 文');
      if (!out) return;
      const data = out.data || {};
      const beforeCash = shopCashOf(out.before || {}, ownShopOf(out.before || {}));
      const afterCash = Number(actionDetail(data, 'shop_cash_after',
        shopCashOf(out.st || {}, ownShopOf(out.st || {}))));
      toast('提现 ' + amount + ' 文，店现 ' + beforeCash + ' → ' + afterCash);
      pushLog('ok', '提现 ' + amount + ' 文 · 店现 ' + beforeCash + ' → ' + afterCash,
        'event_seq ' + (data.event_seq == null ? '?' : data.event_seq));
    } finally {
      endShopBusy(key);
    }
  }

  function openDismiss(targetId) {
    const st = state.st;
    const own = ownShopOf(st);
    const reason = shopOpsDisabledReason(st, own);
    if (reason) return showReasonToast('辞退', 'not_shop_owner');
    const emp = (shopEmployeesOf(st, own) || []).filter(function (e) {
      return employeeTargetOf(st, e) === String(targetId);
    })[0];
    if (!emp) return showReasonToast('辞退', 'target_not_employed');
    state.commerce.dismissTarget = String(targetId);
    el('dismissText').textContent = '确定辞退 ' + (emp.name || targetId) + '？岗位 ' +
      (emp.position || '—') + '，日薪 ' + (emp.wage == null ? '—' : emp.wage) + ' 文。';
    el('dismissError').classList.add('hidden');
    el('dismissConfirm').disabled = false;
    el('dismissConfirm').textContent = '确认辞退';
    el('dismissModal').classList.remove('hidden');
  }

  function closeDismiss() {
    el('dismissModal').classList.add('hidden');
    state.commerce.dismissTarget = '';
  }

  async function doDismiss() {
    const target = String(state.commerce.dismissTarget || '');
    if (!target) return;
    const st = state.st;
    const own = ownShopOf(st);
    const reason = shopOpsDisabledReason(st, own);
    if (reason) return showReasonToast('辞退', 'not_shop_owner');
    const emp = (shopEmployeesOf(st, own) || []).filter(function (e) {
      return employeeTargetOf(st, e) === target;
    })[0] || {};
    const key = 'dismiss:' + target;
    if (!startShopBusy(key)) return;
    const btn = el('dismissConfirm');
    btn.disabled = true;
    btn.textContent = '处理中…';
    try {
      const out = await commerceAction('dismiss', { target: target }, '辞退' + (emp.name || target));
      if (!out) {
        const box = el('dismissError');
        box.textContent = '辞退没有完成；原因见右上提示。';
        box.classList.remove('hidden');
        return;
      }
      const data = out.data || {};
      const owed = Number(actionDetail(data, 'wage_owed', Number(emp.unpaid_wage || 0)));
      closeDismiss();
      toast('已辞退 ' + (emp.name || target) + (owed > 0 ? ('，欠薪 ' + owed + ' 文仍计') : '，无欠薪'));
      pushLog('ok', '辞退 ' + (emp.name || target) + (owed > 0 ? (' · 欠薪 ' + owed + ' 文仍计') : ' · 无欠薪'),
        'event_seq ' + (data.event_seq == null ? '?' : data.event_seq));
    } finally {
      delete state.commerce.shopops.busy[key];
      if (state.st) renderCommerce(state.st);
      if (!el('dismissModal').classList.contains('hidden')) {
        btn.disabled = false;
        btn.textContent = '确认辞退';
      }
    }
  }

  async function doEscort() {
    const st = state.st;
    const ctx = escortContext(st);
    if (!ctx.hasAgency) return showReasonToast('押镖', 'no_escort_agency');
    const dest = el('escortDest').value;
    if (!dest || !ctx.dests.some(function (x) { return x.id === dest; })) {
      return showReasonToast('押镖', 'route_not_adjacent');
    }
    const cargoItem = el('escortCargo').value;
    const cargoRow = ctx.items.filter(function (it) { return it.item === cargoItem; })[0];
    if (!cargoRow) return showReasonToast('押镖', 'item_missing');
    const held = Math.max(0, Number(cargoRow.qty || 0));
    const qty = Math.max(1, Math.min(ESCORT_RULES.qtyMax, held, Math.floor(Number(el('escortQty').value) || 1)));
    if (held <= 0) return showReasonToast('押镖', 'item_missing');
    const fee = Math.floor(Number(el('escortFee').value) || 0);
    if (fee < ESCORT_RULES.feeMin) return showReasonToast('押镖', 'fee_too_low');
    if (fee > ESCORT_RULES.feeMax) return showReasonToast('押镖', 'fee_too_high');
    const unit = Number(cargoRow.price || 0);
    const cargoValue = Math.max(ESCORT_RULES.cargoMin, Math.round(unit * qty));
    const route = [ctx.loc.id, dest];
    // 服务端 /api/player/action 的 escort 参数只认 `cargo: {item, qty}`（见 session._args），
    // 顶层 cargo_item/cargo_qty 会被忽略并触发 item_missing。
    const args = { route: route, cargo: { item: cargoItem, qty: qty }, cargo_value: cargoValue, fee: fee };
    const out = await commerceAction('escort', args, '押镖 ' + (ctx.loc.name || ctx.loc.id) + '→' + dest);
    if (!out) return;
    const orders = ((out.st || {}).escort_orders || []).concat(((out.data || {}).escort_orders || []));
    const mine = orders.filter(function (o) {
      return o && String(o.carrier || '').indexOf(state.session.accountId) >= 0;
    }).sort(function (a, b) { return Number(b.created_tick || 0) - Number(a.created_tick || 0); })[0] || null;
    const arrive = mine && mine.arrive_tick ? mine.arrive_tick : (Number(out.st.tick || 0) + ESCORT_RULES.durationTicks);
    const net = mine && mine.fee ? escortNet(mine.fee, mine.agency_cut):
      escortNet(fee, ESCORT_RULES.agencyCut);
    const destName = (ctx.dests.filter(function (x) { return x.id === dest; })[0] || {}).name || dest;
    state.commerce.escort = '镖单已接：' + (ctx.loc.name || ctx.loc.id) + ' → ' + destName + ' · ' +
      (cargoRow.name || cargoItem) + '×' + qty + ' · 镖费 ' + (mine && mine.fee ? mine.fee : fee) +
      ' 文 · 预计到手 ' + net + ' 文 · 到货 tick ' + arrive;
    el('escortResult').textContent = state.commerce.escort;
    toast('镖单已接 · 到货 tick ' + arrive);
    pushLog('ok', '押镖 ' + (ctx.loc.name || ctx.loc.id) + '→' + destName + ' · 到货 ' + arrive,
      'event_seq ' + (out.data.event_seq == null ? '?' : out.data.event_seq));
    updateEscortEstimate();
  }

  /* ------------------------------------------------------------- init */
  async function restoreAccount() {
    // 旧版本可能留下 ws_player_session；它不参与恢复，直接清掉，避免读旧快照。
    try { localStorage.removeItem('ws_player_session'); } catch (e) { /* ignore */ }
    let raw = null;
    try { raw = localStorage.getItem('ws_player_account'); } catch (e) { raw = null; }
    let saved = null;
    if (raw) {
      try { saved = JSON.parse(raw); } catch (e) { saved = null; }
      if (!saved || !saved.account_id) {
        try { localStorage.removeItem('ws_player_account'); } catch (e) { /* ignore */ }
        saved = null;
      }
    }
    // 令牌账号优先：刷新时只要令牌有效且该角色已在世界内，就直接落主界面，不再弹进入框。
    const authAccount = (state.auth.access && state.auth.username) ? state.auth.username : '';
    const candidates = [];
    if (authAccount) candidates.push(authAccount);
    if (saved && saved.account_id && saved.account_id !== authAccount) candidates.push(saved.account_id);
    if (!candidates.length) return false;
    for (let i = 0; i < candidates.length; i++) {
      try {
        const st = await API.state(candidates[i]);
        if (st && st.ok && st.player) {
          applyState(st);
          return true;
        }
      } catch (err) {
        // 上游暂时不可达：回进入页，不伪造位置；若是鉴权问题，保留已记住的账号。
        if (err && err.authRequired) return false;
      }
    }
    if (saved) {
      try { localStorage.removeItem('ws_player_account'); } catch (e) { /* ignore */ }
    }
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
    el('entryClose').addEventListener('click', closeEntry);
    el('entryCloseBtn').addEventListener('click', closeEntry);
    el('switchBtn').addEventListener('click', function () {
      state.session = null;
      state.selected = null;
      state.st = null;
      state.log = [];
      state.guide.active = false;
      hideGuide();
      el('log').innerHTML = '';
      try { localStorage.removeItem('ws_player_account'); } catch (e) { /* ignore */ }
      // 登录态下账号跟着令牌走，不能随机成别人的 id；未登录才给随机账号。
      el('accountId').value = (state.auth.access && state.auth.username)
        ? state.auth.username
        : randomAccount();
      el('displayName').value = '无名客';
      showEntry(true);
      syncEntryAccountLock();
    });
    el('talkSend').addEventListener('click', doTalk);
    el('talkInput').addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter') { ev.preventDefault(); doTalk(); }
    });
    el('textMap').addEventListener('click', onCityNodeClick);
    el('npcClose').addEventListener('click', closeNpcModal);
    el('npcModal').addEventListener('click', function (ev) {
      if (ev.target === el('npcModal')) closeNpcModal();
    });

    /* 商业与经营：所有控件都是原生 button/input/select，Tab 可达、Enter/Space 可触发。 */
    el('commerceJump').addEventListener('click', function () {
      state.commerce.tab = state.commerce.tab || 'shop';
      setCommerceTab(state.commerce.tab);
      const block = el('commerceBlock');
      if (block) block.scrollIntoView({ behavior: 'smooth', block: 'start' });
      const active = el('commerceTabs') &&
        el('commerceTabs').querySelector('[data-tab="' + state.commerce.tab + '"]');
      if (active) setTimeout(function () { active.focus(); }, 0);
    });
    el('commerceTabs').addEventListener('click', function (ev) {
      const btn = ev.target.closest('[data-tab]');
      if (!btn) return;
      setCommerceTab(btn.getAttribute('data-tab'));
      if (btn.getAttribute('data-tab') === 'escort') updateEscortEstimate();
      if (btn.getAttribute('data-tab') === 'sect') renderSectPane(state.st || {});
    });
    el('commerceTabs').addEventListener('keydown', function (ev) {
      const tabs = Array.prototype.slice.call(el('commerceTabs').querySelectorAll('[data-tab]'));
      const idx = tabs.indexOf(document.activeElement);
      if (idx < 0) return;
      let next = null;
      if (ev.key === 'ArrowRight') next = tabs[(idx + 1) % tabs.length];
      else if (ev.key === 'ArrowLeft') next = tabs[(idx + tabs.length - 1) % tabs.length];
      else if (ev.key === 'Home') next = tabs[0];
      else if (ev.key === 'End') next = tabs[tabs.length - 1];
      if (!next) return;
      ev.preventDefault();
      next.focus();
      setCommerceTab(next.getAttribute('data-tab'));
    });
    el('commerceBlock').addEventListener('input', function (ev) {
      const target = ev.target;
      if (target && target.getAttribute && target.getAttribute('data-qty')) {
        state.commerce.qty[target.getAttribute('data-qty')] = target.value;
      }
      if (target && target.id === 'hireWage') state.commerce.wageSet = true;
    });
    el('commerceBlock').addEventListener('change', function (ev) {
      const target = ev.target;
      if (target && target.getAttribute && target.getAttribute('data-pos-for')) {
        const id = target.getAttribute('data-pos-for');
        state.commerce.pos[id] = target.value;
        state.commerce.wageSet = true;
        const market = positionWage(target.value);
        if (market > 0) el('hireWage').value = String(market);
      }
      if (target && target.id === 'escortCargo') {
        clampEscortQty();
        updateEscortEstimate();
      }
    });
    el('commerceBlock').addEventListener('focusout', function () {
      // 输入结束后补一次被暂缓的渲染：价格/库存不会因为“正在输入”而永久滞后。
      setTimeout(function () {
        if (state.commerce.deferred && !commerceEditing()) {
          const st = state.commerce.deferred;
          state.commerce.deferred = null;
          renderCommerce(st);
        }
      }, 0);
    });
    el('buyList').addEventListener('click', function (ev) {
      const btn = ev.target.closest('[data-buy-go]');
      if (btn) doBuy(btn.getAttribute('data-buy-go'));
    });
    el('sellList').addEventListener('click', function (ev) {
      const btn = ev.target.closest('[data-sell-go]');
      if (btn) doSell(btn.getAttribute('data-sell-go'));
    });
    el('consumeBtn').addEventListener('click', doConsume);
    el('tierList').addEventListener('click', function (ev) {
      const btn = ev.target.closest('[data-tier]');
      if (btn) doOpenShop(btn.getAttribute('data-tier'));
    });
    el('hireList').addEventListener('click', function (ev) {
      const btn = ev.target.closest('[data-hire]');
      if (btn) doHire(btn.getAttribute('data-hire'));
    });
    el('employeeList').addEventListener('click', function (ev) {
      const btn = ev.target.closest('[data-dismiss]');
      if (btn && !btn.disabled) openDismiss(btn.getAttribute('data-dismiss'));
    });
    el('restockList').addEventListener('click', function (ev) {
      const fill = ev.target.closest('[data-restock-fill]');
      if (fill && !fill.disabled) {
        // 只预填建议量，不代替玩家下单：仍要再点一次「进货」确认。
        const item = fill.getAttribute('data-restock-fill');
        const qty = Math.max(1, Math.min(20, Math.floor(Number(fill.getAttribute('data-fill-qty')) || 1)));
        state.commerce.shopops.qty[item] = qty;
        const input = el('restockList').querySelector('[data-restock-qty="' + item + '"]');
        if (input) {
          input.value = String(qty);
          input.focus();
        }
        updateRestockTotal(state.st || {});
        toast('已按建议量填好 ' + qty + ' 件，点「进货」确认。');
        return;
      }
      const btn = ev.target.closest('[data-restock-go]');
      if (btn && !btn.disabled) doRestock(btn.getAttribute('data-restock-go'));
    });
    el('restockList').addEventListener('input', function (ev) {
      const input = ev.target.closest('[data-restock-qty]');
      if (!input) return;
      const item = input.getAttribute('data-restock-qty');
      state.commerce.shopops.qty[item] = Math.floor(Number(input.value) || 1);
      updateRestockTotal(state.st || {});
    });
    el('priceList').addEventListener('input', function (ev) {
      const input = ev.target.closest('[data-price-input]');
      const range = ev.target.closest('[data-price-range]');
      const item = (input || range) && (input || range).getAttribute(input ? 'data-price-input' : 'data-price-range');
      if (item) updatePriceRow(item);
    });
    el('priceList').addEventListener('click', function (ev) {
      const btn = ev.target.closest('[data-price-go]');
      if (btn && !btn.disabled) doSetPrice(btn.getAttribute('data-price-go'));
    });
    el('withdrawGo').addEventListener('click', doWithdraw);
    el('dismissClose').addEventListener('click', closeDismiss);
    el('dismissCancel').addEventListener('click', closeDismiss);
    el('dismissConfirm').addEventListener('click', doDismiss);
    el('dismissModal').addEventListener('click', function (ev) {
      if (ev.target === el('dismissModal')) closeDismiss();
    });
    el('escortDest').addEventListener('change', updateEscortEstimate);
    el('escortQty').addEventListener('input', updateEscortEstimate);
    el('escortFee').addEventListener('input', updateEscortEstimate);
    el('escortGo').addEventListener('click', doEscort);

    /* 门派：表单输入只更新状态，避免 8 秒轮询打断输入；按钮禁用态即时同步。 */
    function syncSectInput(input) {
      if (!input || !input.id) return;
      const id = input.id;
      const value = input.value;
      if (id === 'sectName') {
        state.sect.name = value;
        /* 只更新姓名提示与按钮禁用态，不整体重绘，避免打断输入。 */
        const ok = value.trim().length >= 2 && value.trim().length <= 12;
        const hint = el('sectNameHint');
        if (hint) {
          hint.textContent = '门派名需 2-12 字；当前 ' + value.trim().length + ' 字。';
          hint.classList.toggle('hidden', ok);
        }
        const found = el('sectFoundGo');
        if (found) {
          const money = sectMoney(state.st || {});
          const rep = sectReputation(state.st || {});
          found.disabled = !ok || (money != null && money < SECT_RULES.foundCost) ||
            (rep != null && rep < SECT_RULES.minReputation) || sectBusy('found');
        }
        return;
      }
      if (id === 'sectDoctrine') { state.sect.doctrine = value; return; }
      if (id === 'sectJoinTarget') {
        state.sect.joinTarget = value;
        const join = el('sectJoinGo');
        if (join) join.disabled = !value.trim() || sectBusy('join');
        return;
      }
      if (id === 'sectDepositAmount') {
        state.sect.deposit = value;
        const v = Math.floor(Number(value));
        const money = sectMoney(state.st || {});
        const btn = el('sectDepositGo');
        if (btn) btn.disabled = !isFinite(v) || v < 1 || (money != null && v > money) || sectBusy('deposit');
        return;
      }
      if (id === 'sectWithdrawAmount') {
        state.sect.withdraw = value;
        const v = Math.floor(Number(value));
        const sect = sectDataOf(state.st || {}).sect || {};
        const bal = sect.treasury && sect.treasury.balance != null ? Number(sect.treasury.balance) : null;
        const max = bal == null ? null : Math.max(0, Math.min(SECT_RULES.withdrawCap, bal - SECT_RULES.treasuryReserve));
        const btn = el('sectWithdrawGo');
        if (btn) btn.disabled = !isFinite(v) || v < 1 || (max != null && v > max) || sectBusy('withdraw');
      }
    }
    el('sectBody').addEventListener('input', function (ev) { syncSectInput(ev.target); });
    el('sectBody').addEventListener('click', function (ev) {
      const btn = ev.target.closest('button[data-sect-found], button[data-sect-join], ' +
        'button[data-sect-recruit], button[data-sect-pay], button[data-sect-expel], ' +
        'button[data-sect-deposit], button[data-sect-withdraw]');
      if (!btn || btn.disabled) return;
      if (btn.hasAttribute('data-sect-found')) return doSectFound();
      if (btn.hasAttribute('data-sect-join')) return doSectJoin();
      if (btn.hasAttribute('data-sect-recruit')) return doSectRecruit(btn.getAttribute('data-sect-recruit'));
      if (btn.hasAttribute('data-sect-pay')) return doSectPay();
      if (btn.hasAttribute('data-sect-expel')) return openSectExpel(btn.getAttribute('data-sect-expel'));
      if (btn.hasAttribute('data-sect-deposit')) return doSectDeposit();
      if (btn.hasAttribute('data-sect-withdraw')) return doSectWithdraw();
    });
    el('sectModalClose').addEventListener('click', closeSectExpel);
    el('sectModalCancel').addEventListener('click', closeSectExpel);
    el('sectModalConfirm').addEventListener('click', doSectExpel);
    el('sectModal').addEventListener('click', function (ev) {
      if (ev.target === el('sectModal')) closeSectExpel();
    });

    el('proposeList').addEventListener('click', function (ev) {
      const btn = ev.target.closest('[data-propose-choose]');
      if (!btn || btn.disabled) return;
      state.marriage.target = btn.getAttribute('data-propose-choose');
      renderMarriagePane(state.st || {});
    });
    el('proposeMoney').addEventListener('input', updateProposeEstimate);
    el('proposeQty').addEventListener('input', updateProposeEstimate);
    el('proposeItem').addEventListener('change', updateProposeEstimate);
    el('proposeGo').addEventListener('click', doPropose);
    el('restBtn').addEventListener('click', doRestPlaceholder);
    el('marriageEndBtn').addEventListener('click', openDivorce);
    el('divorceCancel').addEventListener('click', closeDivorce);
    el('divorceClose').addEventListener('click', closeDivorce);
    el('divorceConfirm').addEventListener('click', doMarriageEnd);
    el('divorceModal').addEventListener('click', function (ev) {
      if (ev.target === el('divorceModal')) closeDivorce();
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
        closeDivorce();
        closeDismiss();
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
    // 回到前台 / 网络恢复时立即补一次保活：离线超时的玩家不必等下一个 20s 周期。
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) heartbeatTick();
    });
    window.addEventListener('online', function () { heartbeatTick(); });
    try {
      state.health = await API.health();
      renderRunTag();
      if (!state.health.ok) {
        showEntry(true);
        const box = el('entryError');
        box.textContent = '世界不可用：' + (state.health.message || state.health.error || '未知错误');
        box.classList.remove('hidden');
        el('enterBtn').disabled = true;
        return;
      }
      loadStoredAuth();  // 先载入本地令牌：restore 的首次读必须带 Authorization，否则服务模式只拿到公共摘要
      // 先跑鉴权探针（它会轮换令牌），再按最新令牌恢复角色，避免并发轮换把首读打成 401。
      await probeAuth();
      const restored = await restoreAccount();
      renderNotice(state.st);
      if (restored) {
        showEntry(false);
      } else {
        showEntry(true);
      }
      if (state.st) render(state.st);
      if (restored) maybeStartGuide();
      // 世界动态与登录态无关：匿名、只读降级都能读；独立节流，不参与主状态刷新。
      loadFeed();
      clearInterval(state.feedTimer);
      state.feedTimer = setInterval(loadFeed, FEED_INTERVAL_MS);
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
      }, STATE_INTERVAL_MS);
    } catch (err) {
      showEntry(true);
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
      feedState: function () {
        return {
          rows: state.feed.rows.length,
          failed: !!state.feed.failed,
          announced: Object.keys(state.feed.announced).length,
          important: state.feed.rows.filter(function (r) { return r.important; }).length,
          topTick: state.feed.rows.length ? state.feed.rows[0].tick : null
        };
      },
      refreshFeed: function () { return loadFeed(); },
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
      commerceState: function () {
        const st = state.st || {};
        const own = ownShopOf(st);
        const shop = st.shop || {};
        const money = Number(((st.player || {}).wallet || {}).money || 0);
        return {
          tab: state.commerce.tab,
          money: money,
          buyRows: (shop.buy || []).length,
          sellRows: (shop.sell || []).length,
          servers: ((shop.businesses || []).length),
          ownShop: own ? { id: own.id, kind: own.kind, cash: own.cash, employees: Array.isArray(own.employees) ? own.employees.length : null } : null,
          items: (((st.player || {}).wallet || {}).items || [])
            .filter(function (it) { return it && it.item; })
            .map(function (it) { return { item: it.item, qty: Number(it.qty || 0) }; }),
          hireRows: (st.present_npcs || []).length,
          hasEscortAgency: ((shop.businesses || []).some(function (b) { return String(b.kind || '').indexOf('escort') >= 0; })),
          escortDests: ((st.location || {}).exits || []).map(function (x) { return x.id; }),
          activeEscorts: ((st.escort_orders || []).filter(function (o) { return o.status === 'active' || o.status === 'open'; }).length),
          deferred: !!state.commerce.deferred,
          editing: commerceEditing()
        };
      },
      shopOpsState: function () {
        const st = state.st || {};
        const own = ownShopOf(st);
        const days = own ? shopReportDaysOf(st) : [];
        return {
          reason: shopOpsDisabledReason(st, own),
          cash: shopCashOf(st, own),
          stock: shopStockRowsOf(st, own),
          employees: (shopEmployeesOf(st, own) || []).length,
          days: days,
          total: ledgerTotalsOf(days),
          busy: Object.keys(state.commerce.shopops.busy),
          reportLoaded: !!state.commerce.shopops.reportLoaded,
          reportUnavailable: !!state.commerce.shopops.reportUnavailable,
          stockouts: own ? Number(shopReportDetailsOf(st).stockouts || 0) : 0,
          lowStock: own ? shopStockRowsOf(st, own).filter(function (row) {
            return row.qty < SHOP_OPS.lowStock;
          }).length : 0,
          dismissTarget: state.commerce.dismissTarget
        };
      },
      refreshShopReport: function () {
        const ops = state.commerce.shopops;
        ops.report = null;
        ops.reportDetails = null;
        ops.reportLoaded = false;
        ops.reportUnavailable = false;
        ops.reportBusy = false;
        renderLedger(state.st || {}, ownShopOf(state.st || {}));
        return loadShopReport();
      },
      refreshState: function (account) {
        const acct = account == null ? (state.session && state.session.accountId) : account;
        return loadState(acct);
      },
      marriageState: function () {
        const st = state.st || {};
        const m = marriageOf(st);
        return {
          status: String(m.status || 'single'),
          spouseId: spouseIdOf(st),
          spouseName: spouseNameOf(st),
          sinceTick: Number(m.since_tick || 0),
          lastDailyTick: Number(m.last_daily_tick || 0),
          dailyNote: String(m.daily_note || ''),
          spouseBlockVisible: !el('marriageBlock').classList.contains('hidden'),
          spouseInfo: el('spouseInfo').textContent.trim(),
          spouseNote: el('spouseNote').textContent.trim(),
          target: state.marriage.target,
          candidates: marriageCandidates(st).map(function (row) {
            return { id: row.npc.id, name: row.npc.name, role: row.npc.role,
              affection: row.aff, trust: row.trust, married: row.marriedText,
              isSpouse: row.isSpouse, sameLocation: true };
          }),
          giftRows: giftRowsOf(st).map(function (row) { return row.item; }),
          money: Number(((st.player || {}).wallet || {}).money || 0),
          result: state.marriage.result,
          resultBad: !!state.marriage.resultBad,
          estimate: el('proposeEstimate').textContent.trim(),
          proposeDisabled: !!el('proposeGo').disabled,
          confirmOpen: !el('divorceModal').classList.contains('hidden'),
          talkTargetSpouse: !!el('talkTarget').classList.contains('spouse'),
          restResult: el('restResult').textContent.trim()
        };
      },
      sectState: function () {
        const st = state.st || {};
        const data = sectDataOf(st);
        const sect = data.sect;
        const limits = sect ? sectLimitsOf(sect) : null;
        const npcs = ((st.present_npcs || st.nearby || []).filter(function (n) {
          return n && !String(n.id).startsWith('player:');
        }));
        return {
          missing: data.missing,
          inSect: data.inSect,
          role: sect ? String(sect.role || '') : '',
          name: sect ? String(sect.name || '') : '',
          leader: sect ? String(sect.leader_name || sect.leader || '') : '',
          memberCount: sect && sect.member_count != null ? Number(sect.member_count) : null,
          cap: limits ? limits.disciple_cap : SECT_RULES.discipleCap,
          treasury: sect && sect.treasury && sect.treasury.balance != null ? Number(sect.treasury.balance) : null,
          settledToday: sect && sect.settled_today != null ? !!sect.settled_today : null,
          disciples: sect ? (sect.disciples || []).map(function (d) {
            const bond = sectBondOf(st, d.id);
            return {
              id: d.id, name: d.name, skill: d.skill == null ? null : Number(d.skill),
              atSect: !!d.at_sect, arrears: Number(d.arrears || 0),
              affection: bond.aff, trust: bond.trust,
              settledToday: d.settled_today == null ? null : !!d.settled_today
            };
          }) : [],
          candidates: npcs.map(function (n) {
            // 与渲染同一口径：present_npcs.bond 不含 sworn，用 relations 补齐。
            const rel = sectBondOf(st, n.id);
            const nb = n.bond == null ? null : Number(n.bond);
            const nt = n.trust == null ? null : Number(n.trust);
            const bond = (nb == null && rel.aff == null) ? null
              : Math.max(nb == null ? 0 : nb, rel.aff == null ? 0 : rel.aff);
            const trust = (nt == null && rel.trust == null) ? null
              : Math.max(nt == null ? 0 : nt, rel.trust == null ? 0 : rel.trust);
            return { id: n.id, name: n.name, bond: bond, trust: trust,
              skill: n.skills && n.skills.level != null ? Number(n.skills.level) : null };
          }),
          reputation: sectReputation(st),
          money: sectMoney(st),
          maxSkill: sectMaxSkill(st),
          busy: Object.keys(state.sect.busy),
          result: state.sect.result,
          resultBad: !!state.sect.resultBad,
          confirmTarget: state.sect.confirmTarget,
          confirmOpen: !el('sectModal').classList.contains('hidden'),
          foundDisabled: !!(el('sectFoundGo') && el('sectFoundGo').disabled),
          bodyText: (el('sectBody') ? el('sectBody').textContent : '').trim().slice(0, 600),
          hint: (el('sectHint') ? el('sectHint').textContent : '').trim()
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
          exitId: ctx.node.getAttribute('data-city-id') || '',
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
