/* 法律工作台前端（原生 JS，无 CDN，无外网请求） */
const $ = (s, r) => (r || document).querySelector(s);
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
/* 批 47k：长文本单元格统一解法——单行截断 + 鼠标悬停 title 看全文。
   六页审计结论：律师函/首页/客户/台账进展/费用跟踪的表格被「长文本竖排折行」
   撑出超高行（一格 4-10 行），全部换成 clip 后行高回到 1-2 行。 */
const clip = (s, w) => {
  s = String(s == null ? '' : s);
  return `<span class="clip" style="max-width:${w || 240}px" title="${esc(s)}">${esc(s)}</span>`;
};
const num = v => (v == null || v === '') ? '—' : (typeof v === 'number' ? v.toLocaleString('zh-CN') : esc(v));
/* 金额/标的额单元格：把括号里的说明折成第二行小字（列宽立刻变窄、行高也稳） */
function amtCell(v) {
  const s = (v == null || v === '') ? '' : String(v);
  if (!s) return '<span class="dim">—</span>';
  const i = s.search(/[（(]/);
  return (i > 0)
    ? `${esc(s.slice(0, i).trim())}<span class="sub">${esc(s.slice(i))}</span>`
    : esc(s);
}

async function api(path, opts) {
  const r = await fetch(path, Object.assign({headers:{'Content-Type':'application/json'}}, opts||{}));
  const txt = await r.text();
  let body = null;
  try { body = txt ? JSON.parse(txt) : null; } catch (e) { body = null; }
  if (!r.ok) {
    const e = new Error((body && body.error) || txt.slice(0, 300) || ('HTTP ' + r.status));
    e.status = r.status; e.body = body;          // 409 冲突/占用靠 body 判定
    throw e;
  }
  return body;
}
const post = (p, b, opts) => api(p, Object.assign({method:'POST', body: JSON.stringify(b||{})}, opts||{}));
const toast = m => { const el = $('#meta'); el.textContent = m; };

/* ---- 行内操作反馈（批 46，2026-09-19）-------------------------------------
   为什么需要：`toast` 只写顶部 #meta，用户视线在表格中间**根本看不到**；
   且按钮点击后若只弹 alert / 无视觉变化，用户会以为「没反应」而**重复点击**
   （实踩：点一次「建任务」因页面重绘 + 无反馈，误建 2 条并落到错误行）。

   纪律（README 第 22 条）：
   ① 行操作反馈一律**就近显示**（渲到该行/该卡片内），禁只用顶部 toast
   ② 异步操作按钮必须进入**处理中**状态（disabled + 文案变），防重复提交
   ③ 行操作完成后**禁整页 render()**——只重绘受影响区域（patchInner）
------------------------------------------------------------------------- */
/** 在指定容器显示行内反馈；kind: ok|warn|err */
function inlineMsg(cid, msg, kind) {
  const el = document.getElementById(cid);
  if (!el) { toast(msg); return; }          // 容器不在（页面刚重绘）→ 退回顶部，至少不丢消息
  // kind: ok（成功）/ warn、err（告警）/ dim（中性，如「已取消」）
  const cls = (kind === 'err' || kind === 'warn') ? 'warn' : (kind === 'dim' ? 'dim' : 'ok');
  el.innerHTML = `<span class="${cls}">${esc(msg)}</span>`;
}
/** 按钮处理中：禁用 + 换文案；返回 restore 函数 */
function btnBusy(btn, busyText) {
  if (!btn) return () => {};
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = busyText || '处理中…';
  return () => { btn.disabled = false; btn.textContent = old; };
}
/* 挂 window：① onclick 内联调用可直接用 ② smoke_views 按 window[f] 断言 */
window.inlineMsg = inlineMsg;
window.btnBusy = btnBusy;

/* ---- 浮层（modal）——批 46.5 新增 ------------------------------------------
   为什么需要：批 46.x 把各种结果都渲到表格**下方**的 letterOut，用户**不往下滚
   就看不到**（实测用户连点多次仍认为「没反应」——后端日志显示请求全成功了）。
   ⇒ 结论：**「结果渲染位置」和「用户视线位置」必须重合**。
     凡"点一下要出结果"的操作，一律用**居中浮层**呈现，不再用"渲到下方"。
   ESC / 点遮罩 / 点关闭 都能关；浮层内可滚（长内容）。
------------------------------------------------------------------------- */
function showModal(title, bodyHtml, opts) {
  opts = opts || {};
  const id = 'wbModal';
  let el = document.getElementById(id);
  if (!el) {
    el = document.createElement('div');
    el.id = id;
    el.style.cssText = 'position:fixed;inset:0;z-index:9999;display:flex;'
      + 'align-items:center;justify-content:center;background:rgba(0,0,0,.42)';
    el.addEventListener('click', (ev) => { if (ev.target === el) closeModal(); });
    document.body.appendChild(el);
  }
  el.innerHTML = `<div style="background:#fff;border-radius:12px;max-width:min(1180px,94vw);
      max-height:92vh;display:flex;flex-direction:column;box-shadow:0 12px 48px oklch(0.29 0.045 250 / 28%);
      overflow:hidden" onclick="event.stopPropagation()">
    <div style="padding:12px 16px;border-bottom:1px solid var(--rule);display:flex;
        align-items:center;justify-content:space-between;gap:12px;flex:0 0 auto">
      <b style="font-size:15px">${esc(title)}</b>
      <button class="act ghost mini" onclick="closeModal()">关闭 ✕</button>
    </div>
    <div style="padding:14px 16px;overflow:auto;flex:1 1 auto">${bodyHtml}</div>
    ${opts.footer ? `<div style="padding:10px 16px;border-top:1px solid var(--rule);flex:0 0 auto">${opts.footer}</div>` : ''}
  </div>`;
  el.style.display = 'flex';
  if (!showModal._esc) {
    showModal._esc = true;
    document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') closeModal(); });
  }
}
function closeModal() {
  const el = document.getElementById('wbModal');
  if (el) { el.style.display = 'none'; el.innerHTML = ''; }
}
window.showModal = showModal;
window.closeModal = closeModal;

/* 浮层里加载中提示（先弹浮层再填内容，用户立刻看到反应） */
function showModalLoading(title, msg) {
  showModal(title, `<div class="hint">${esc(msg || '加载中…')}</div>`);
}
window.showModalLoading = showModalLoading;

const NAV = [
  ['日常', [['home', '首页'], ['board', '看板'], ['clients', '客户'], ['cases', '案件'], ['tasks', '任务'], ['letters', '律师函'], ['contracts', '合同审查'], ['trace', '文件管理'], ['calc', '计算器']]],
  ['台账', [['ledger', '台账进展'], ['assistant', '工作台助理'], ['fees', '费用跟踪']]],
  ['办理', [['new', '开新案'], ['writing', '写作项目'], ['intake', '收件箱入库'], ['scatter', '散件'], ['sohan', '所函'],
            ['deadlines', '期限与日历'], ['archive', '归档与导出']]]
];
/* 每个页面的标题与一句话说明（渲染时统一加在内容上方，不必各页自己写） */
const PAGE_META = {
  home: ['首页', '今天要办的事、进行中的案子、期限提醒'],
  board: ['看板', '在办案件按「最新状态」排成泳道，一屏看全分布（台账只读，不做拖拽）'],
  clients: ['客户', '按委托人聚合在办案件与补充档案'],
  cases: ['案件', '案卷目录巡览、材料规范、台账对应关系'],
  tasks: ['任务', '待办清单与关联案件'],
  letters: ['律师函', '台账「律师函」表 + 文件系统里的律师函夹'],
  contracts: ['合同审查', '台账「合同审查」表 + 素材夹文件（只读；表结构由人在 WPS 维护）'],
  ledger: ['台账进展', '受控回写「当前进度 · 最新状态」——改前自动备份、旧值留痕到日志列'],
  new: ['开新案', '按 v2 规范建案卷目录，可顺带建台账草稿行与知识库页'],
  writing: ['写作项目', '诉状/代理词等长文的项目化登记与版本入卷（复制不移动，台账零写入）'],
  intake: ['收件箱入库', '把 00-收件箱 里的材料归到规范子夹'],
  fees: ['费用跟踪', '台账 O–W 列派生 + 发票上传归案（本机识别）'],
  scatter: ['散件', '律师函 / 合同 / 差旅发票 / 所函 等横向目录索引（只读，一个文件都不动）'],
  sohan: ['所函', '按律所既有模板生成所函（复制模板 + 定点替换，日期留空）'],
  deadlines: ['期限与日历', '期限清单 + 按月排布的日历视图（红=逾期/当日、黄=3 日内）'],
  archive: ['归档与导出', '结案三步：案卷移档 · 台账移行 · 知识库页标记'],
  trace: ['文件管理', '案卷文件的分区体检、错位报告与归集（只移动不删除）'],
  calc: ['法律计算器', '诉讼费 · 执行/保全申请费 · 迟延履行利息 · 借款利息 · 劳动六件（解除补偿/二倍工资/未休年假/加班工资/竞业限制/工伤补助） · 执行款清偿 · 天数 · 数字大写（纯本地计算，附法条依据）'],
  assistant: ['工作台助理', '问工作台里的事——案件进展、开庭、尾款、待办、期限、任务、案卷目录（分析走本地积分池；案卷文件正文不发送）']
};
function todayLine() {
  const d = new Date();
  const wk = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][d.getDay()];
  return `今天 <b>${d.getMonth() + 1} 月 ${d.getDate()} 日 ${wk}</b>`;
}
let TAB = 'home', OVERVIEW = null, CASES = null, TASKS = null, LEDGER = null;
let BOARD = null, BOARD_INC = false;                 // 看板泳道（批 50：数据快照 + 含已结案开关）
let T_FILTER = {seg: 'open', q: '', prio: 'all'};
let CASE_Q = '';                                     // 案件页搜索词（批 34）
let CASE_ST = '';                                    // 案件页状态下拉（批 47n）
let LET_FILTER = {sign: 'all', follow: 'all', q: ''};// 律师函①筛选（批 47n）
let FEE_FILTER = {gap: 'all'};                       // 费用页缺口筛选（批 47n）
let FEE_DATA = null;                                 // 费用页数据快照（批 47n）
let DL_FILTER = {cs: '', tp: ''};                    // 期限页筛选（批 47n）
let DL_DATA = null;                                  // 期限页数据快照（批 47n）
let SCATTER = null, SCATTER_Q = '', SCATTER_DIR = 'all';   // 散件页（批 36）
let SCATTER_MARK = null;            // 关注/待处理标记（存自建库，不动散件文件；批 38）
let SCATTER_STAR_ONLY = false;      // 「只看关注 / 待处理」开关（批 38）
const SCATTER_TAGS = ['关注', '待处理', '待分发', '参考'];   // 与后端 SCATTER_TAGS 一致
let CURRENT_VIEW = '';              // 当前页名（供 async 回调判断该不该重绘）
let CAL_Y = 0, CAL_M = 0, CAL_DATA = null, SCHED = null;   // 日历页（批 36/37）
let SCHED_EDIT = null;                     // 批 69：日程编辑中的条目 id（null=新建模式）
let QUITTING = false;   // 主动关停后停掉心跳，避免覆盖关停提示

function renderNav() {
  $('#nav').innerHTML = NAV.map(([g, items]) =>
    (g ? `<div class="grp">${g}</div>` : '') +
    items.map(([k, t]) =>
      `<button class="${k === TAB ? 'on' : ''}" onclick="go('${k}')">`
      + `<span class="dot"></span>${t}</button>`).join('')
  ).join('');
  updateBell();
  // 批 77：侧栏跟随——当前页签滚入视野（19 项菜单超视口时，active 高亮不再藏在外面）
  const onBtn = document.querySelector('nav button.on');
  if (onBtn && onBtn.scrollIntoView) onBtn.scrollIntoView({block: 'nearest'});
}
/* 地址栏记住当前页（刷新/回退不跳回首页；也方便直接分享某一页） */
const TAB_ALIAS = {calendar: 'deadlines'};   // 批 40：日历并入「期限与日历」，旧链接/书签不断
function tabFromHash() {
  const raw = (location.hash || '').replace(/^#\/?/, '').split('?')[0];
  const k = TAB_ALIAS[raw] || raw;
  return (k && Object.prototype.hasOwnProperty.call(VIEWS, k)) ? k : 'home';
}

/* hash 里的查询参数（如 #trace?case=xxx）：供页面直达指定对象 */
function hashParam(name) {
  const q = (location.hash || '').split('?')[1] || '';
  const m = new RegExp('(?:^|&)' + name + '=([^&]*)').exec(q);
  return m ? decodeURIComponent(m[1]) : '';
}
async function go(k) {
  TAB = k;
  SCHED_EDIT = null;            // 批 69：切页即退出日程编辑模式（防残留误覆盖）
  if ((location.hash || '').replace(/^#\/?/, '') !== k) location.hash = k;
  renderNav();
  await render();
}

/* ---------------- 首页 ---------------- */
const PRIO_CLS = { '普通': 'p0', '重要': 'p1', '紧急': 'p2' };
const STAT_CLS = { '待办': 's0', '进行中': 's1', '已完成': 's2' };

function taskLi(t) {
  const due = t.due_date ? `<span class="${t.逾期 ? 'red' : (t.剩余天 != null && t.剩余天 <= 1 ? 'warn' : 'dim')}">
      ${esc(t.due_date)}${t.剩余天 == null ? '' : (t.剩余天 < 0 ? `（逾期${-t.剩余天}天）` : `（${t.剩余天}天）`)}</span>`
    : '<span class="dim">未排期</span>';
  return `<div class="row"><span class="badge ${PRIO_CLS[t.priority] || 'p0'}">${esc(t.priority)}</span>
    <b>${esc(t.title)}</b><span class="badge ${STAT_CLS[t.status] || 's0'}">${esc(t.status)}</span>
    ${due}<span class="dim">${esc(t.case_ref || '')}</span>
    ${t.status !== '已完成' ? `<button class="act ghost mini" onclick="taskDone(${t.id},'${jsq(t.title)}')" title="一键标记为已完成（有确认提示）">✓完成</button>` : ''}</div>`;
}

async function viewHome() {
  const o = OVERVIEW || (OVERVIEW = await api('/api/overview'));
  const t = o.tasks || {}, f = o.fees, d = o.deadlines, tl = o.task_lists || {today: [], week: []};
  const ac = o.active_cases || {count: 0, items: []};
  /* 批 56：首页问候区（CaseBoard 手法——caps 日期行 + 大问候 + 副标题分布） */
  const _hr = new Date().getHours();
  const _hi = _hr < 6 ? '凌晨好' : _hr < 12 ? '上午好' : _hr < 14 ? '中午好' : _hr < 18 ? '下午好' : '晚上好';
  const _dt = new Date();
  const _caps = `OVERVIEW · ${_dt.getFullYear()} 年 ${_dt.getMonth() + 1} 月 ${_dt.getDate()} 日 · `
    + ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][_dt.getDay()];
  const _dist = {};
  (o.sheets || []).forEach(s => (s.status || []).forEach(([k, v]) => { _dist[k] = (_dist[k] || 0) + v; }));
  const _top = Object.entries(_dist).filter(([k]) => k !== '已结案')
    .sort((a, b) => b[1] - a[1]).slice(0, 3)
    .map(([k, v]) => `${k === '（空）' ? '状态未填' : k} <b>${v}</b>`).join(' · ');   // 批 77：空状态说人话
  const _greet = `<div class="greet"><div class="caps">${_caps}</div><h1>${_hi}</h1>
    <div class="sub">你正在办 <b>${ac.count}</b> 个案件${_top ? '：' + _top : ''} —— 扫一眼今天的进度。</div></div>`;
  /* KPI 标签一律 ≤6 字：9 格宽度只有约 100px，长标签必折行、把卡片撑高；
     完整口径放 title（悬停可见），不挤版式 */
  const kpi = (val, label, cls, tip) =>
    `<div class="kpi"${tip ? ` title="${esc(tip)}"` : ''}><b class="${cls || ''}">${val}</b><span>${label}</span></div>`;
  let h = _greet + '<div class="grid">' +
    kpi((o.clients || {}).count || 0, '客户', '', '台账「委托人」去重后的客户数') +
    kpi((o.clients || {}).with_ext || 0, '客户档案', '', '已补过补充档案（联系人/电话/微信/备注）的客户数') +
    kpi(o.cases.count, '案件夹', '', `案卷目录下的案件夹总数；其中已建 00-收件箱的 ${o.cases.with_inbox} 个`) +
    kpi(t.open || 0, '未完成', t.open ? '' : 'ok', '未完成任务数（今天要办 + 以后要办的）') +
    kpi(t.overdue || 0, '逾期', t.overdue ? 'red' : 'ok', '已过截止时间还没完成的任务数') +
    kpi(d.due_count, '7 日内期限', d.due_count ? 'red' : 'ok', '7 日内到期或已逾期的期限（含开庭/上诉期/举证/续封/缴费）') +
    kpi(ac.count, '进行中', '', '台账「最新状态」不含结案/归档的案件数') +
    kpi(num(f.已收到), '已收款', '', '台账 O–W 列派生的已收款合计（元）') +
    kpi(f.发票缺口, '费用缺口', f.发票缺口 ? 'warn' : 'ok', '费用信息不全的案件数（发票号空缺 / 已收 > 已开票）') +
    '</div>';

  // 批 C：智能提醒卡（三源合一之 LLM 源；期限/日程见下方既有区块）
  try {
    h += await asRemindCard();
  } catch (e) { /* 提醒卡失败不拖垮首页 */ }

  h += '<div class="card"><h2>今日待办（含逾期）</h2>' +
    (tl.today.length ? tl.today.map(taskLi).join('')
      : '<div class="dim">今天没有到期的待办。去「任务」页排一排。</div>') +
    `<div class="hint">共 ${t.today || 0} 项｜逾期 ${t.overdue || 0} 项｜未排期 ${t.none || 0} 项</div></div>`;

  h += '<div class="card"><h2>7 日内到期</h2>' +
    (tl.week.length ? tl.week.map(taskLi).join('') : '<div class="dim">7 日内暂无到期任务。</div>') + '</div>';

  h += `<div class="card"><h2>进行中的案件（${ac.count}）</h2>` +
    (ac.items.length
      ? '<table><tr><th>案件</th><th>委托人</th><th>状态</th><th>法院/仲裁</th></tr>' +
        ac.items.map(x => `<tr><td class="case">${clip(x['案件'], 250)}</td><td>${esc(x['委托人'])}</td>
          <td>${x['状态'] ? `<span class="pill">${esc(x['状态'])}</span>` : '<span class="dim">未填</span>'}</td>
          <td class="dim">${esc(x['法院/仲裁'] || '')}</td></tr>`).join('') + '</table>'
      : '<div class="dim">暂无。</div>') +
    `<div class="hint">口径：台账「最新状态」不含结案/归档的案件（共 ${ac.count} 件，此处显示前 12）</div></div>`;

  const ec = o.exec_cases || {count: 0, items: []};
  h += `<div class="card"><h2>执行案（${ec.count} 件）</h2>` +
    (ec.items.length
      ? '<table><tr><th>案件</th><th>委托人</th><th>状态</th><th>法院/仲裁</th><th>案卷材料</th></tr>' +
        ec.items.map(x => `<tr><td class="case">${clip(x['案件'], 250)}</td><td>${esc(x['委托人'])}</td>
          <td>${x['状态'] ? `<span class="pill">${esc(x['状态'])}</span>` : '<span class="dim">未填</span>'}</td>
          <td class="dim">${esc(x['法院/仲裁'] || '')}</td>
          <td class="dim">${x.folder
            ? `${x.has_09 ? '<span class="ok">✓ 09-财产查控</span>' : '<span class="dim">无 09-财产查控</span>'}
               ${x.has_10 ? '<span class="ok">✓ 10-处置分配</span>' : '<span class="dim">无 10-处置分配</span>'}`
            : '<span class="dim">案卷夹未认到</span>'}</td></tr>`).join('') + '</table>'
      : '<div class="dim">台账里暂无「案件类别 = 执行」的在办案。</div>') +
    `<div class="hint">口径：台账「案件类别」列 = 执行 且未结案（共 ${ec.count} 件，此处显示前 12）。
      「案卷材料」列直接看案卷目录有没有 <b>09-财产查控</b> / <b>10-处置分配</b>（v2 执行案规范；有产可执行再建 10）
      —— 案卷夹按可解释匹配认，认不到就写「未认到」，不猜。已认到 ${ec.matched_folder || 0} 件，其中含 09 共 ${ec.with_09 || 0} 件。</div></div>`;

  h += '<div class="card"><h2>期限倒计时（前 8）</h2>' +
    (d.due.length ? dlTable(d.due) : '<div class="dim">暂无临期/逾期项。去「期限与日程」页点「从台账抽取」。</div>') +
    `<div class="hint">期限表生成时间：${esc(d.generated_at || '尚未抽取')}｜共 ${d.total} 项未完成</div></div>`;

  h += '<div class="card"><h2>按最新状态分组</h2>';
  o.sheets.forEach(s => {
    h += `<div class="row"><b>${esc(s.sheet)}</b>` +
      s.status.map(([k, v]) => `<span class="pill">${esc(k)} ${v}</span>`).join('') + '</div>';
  });
  h += '</div>';
  return h;
}

/* opt.act = true 时多一列操作：「→建任务」把期限转成工作台任务；「标完成」关掉期限项。
   （批 14：期限与日程页与任务打通。任务只进自建库，台账零改动。） */
function dlTable(items, opt) {
  const act = !!(opt && opt.act);
  return '<table><tr><th>到期日</th><th>剩余</th><th>案件</th><th>类型</th><th>档位</th><th>来源/备注</th>'
    + (act ? '<th>操作</th>' : '') + '</tr>' +
    items.map(i => `<tr><td class="n">${esc(i['到期日']||'')}</td>
      <td class="nw">${i['剩余天']==null?'<span class="dim">—</span>'
        :(i['剩余天']<0?`<span class="red n">逾期 ${-i['剩余天']} 天</span>`
                      :`<span class="n">${i['剩余天']} 天</span>`)}</td>
      <td>${esc(i['案件']||'')}</td><td>${esc(i['期限类型']||'')}</td>
      <td class="${['逾期','当日'].includes(i['档位'])?'red':(i['档位']==='3日内'||i['档位']==='7日内'?'warn':'')}">${esc(i['档位']||'')}</td>
      <td class="dim">${esc(i['来源']||'')} ${esc((i['备注']||'').slice(0,50))}</td>`
      + (act ? `<td>${i['档位']==='已完成' ? '<span class="dim">已标记完成</span>'
          : `<button class="act ghost mini" onclick="dlToTask('${jsq(i['案件']||'')}','${jsq(i['期限类型']||'')}','${esc(i['到期日']||'')}','${esc(i['档位']||'')}')">→建任务</button>
             <button class="act ghost mini" onclick="dlDone('${jsq(i['id']||'')}')">标完成</button>`}</td>` : '')
      + '</tr>').join('') + '</table>';
}

/* ---------------- 看板泳道（批 50；CaseBoard 反向移植） ----------------
   台账在办案件按「最新状态」排成横向泳道（数据＝/api/board，台账只读）。
   不做拖拽——拖卡=写台账状态（三重闸受控回写），风险大；改状态仍走「台账进展」页。
   点卡片 → 到「案件」页 + 用案件名片段本地搜（认不到也落在案件页，行为可解释）。 */
/* 批 56：状态 → pastel 分色 class（11 档 → 7 组；与 index.html 的 .p-* 一一对应） */
function statusPillClass(st) {
  const m = {'待立案': 'p-file', '已立案': 'p-file', '待开庭': 'p-trial', '待判决': 'p-trial',
             '调解中': 'p-trial', '已判决': 'p-judge', '执行立案中': 'p-exefile',
             '待缴费': 'p-exefile', '执行中': 'p-exe', '待续封': 'p-seal', '已结案': 'p-grey'};
  return m[String(st || '').trim()] || 'p-grey';
}
function boardLanesHtml() {
  const lanes = (BOARD && BOARD.lanes) || [];
  if (!lanes.length) return '<div class="dim">台账暂无可读案件行。</div>';
  return '<div class="boardWrap">' + lanes.map(l => {
    const cards = (l.items || []).map(it =>
      `<div class="bcard" onclick="boardGo('${jsq(it['案件'])}')">
        <div class="bname">${clip(it['案件'], 190)}</div>
        <div class="bmeta"><span class="dim">${esc(it['委托人'] || '—')}</span>${it['案件类别'] ? `<span class="pill">${esc(it['案件类别'])}</span>` : ''}</div>
        <div class="bmeta dim">${esc(it['案号'] || '（无案号）')}</div>
        <div class="bprog clip2">${it['当前进度'] ? esc(it['当前进度']) : '<span class="dim">（进度为空）</span>'}</div>
      </div>`).join('');
    return `<div class="boardLane${l.count ? '' : ' boardDim'}" data-lane-status="${esc(l.status)}">
      <div class="boardHead"><span class="pill ${statusPillClass(l.status)}">${esc(l.status)}</span><span class="dim" style="font-size:12.5px">${l.count} 件</span></div>
      ${l.count ? cards : '<div class="dim" style="font-size:13px;padding:2px 2px 6px">（无）</div>'}
    </div>`;
  }).join('') + '</div>';
}
function boardCountText() {
  const lanes = (BOARD && BOARD.lanes) || [];
  const closedShown = (lanes.find(l => l.status === '已结案') || {}).count || 0;
  const open = lanes.reduce((s, l) => s + (l.count || 0), 0) - closedShown;
  const closed = (BOARD && BOARD.closed_count) || 0;
  return `在办 <b>${open}</b> 件 ｜ 已结案 <b>${closed}</b> 件${closedShown ? '' : '（默认隐藏）'}`;
}
async function viewBoard() {
  CURRENT_VIEW = 'board';
  const d = await api('/api/board' + (BOARD_INC ? '?include_closed=true' : ''));
  BOARD = d;
  return `<div class="card"><h2 id="boardHead">看板（${boardCountText()}）</h2>
    <div class="hint">台账在办案件按「最新状态」排成泳道（数据源＝台账，<b>只读</b>）；点卡片到「案件」页搜该案。
    改状态在「台账进展」页（受控回写）。<b>拖拽改状态不做</b>——写台账走三重闸，不从这里开口子。
    泳道<b>按住空白处拖动</b>即可横向移动（或 Shift+滚轮）；滚轮上下＝正常滚页面。</div>
    <div class="row" style="margin:10px 0">
      <label style="display:flex;align-items:center;gap:6px"><input type="checkbox" id="boardInc" style="width:auto"
        ${BOARD_INC ? 'checked' : ''} onchange="boardToggle(this.checked)"> 含已结案</label>
      <button class="act ghost mini" id="boardAiBtn" onclick="boardAiRun()">🤖 AI 分析</button>
      <span class="dim">生成时间 ${esc(d.generated_at || '')}</span>
      <span class="dim">空泳道＝当前没有该状态的案件</span>
    </div>
    <div id="boardAiOut"></div>
    <div id="boardLanes">${boardLanesHtml()}</div></div>`;
}
/* 批 55：看板 AI 分析（本地 8788；处理中态 + 行内反馈，防重复点击） */
window.boardAiRun = async function () {
  const btn = document.getElementById('boardAiBtn');
  const rst = btnBusy(btn, '分析中…（本地 8788，要等一会）');
  patchInner('boardAiOut', '');
  try {
    const r = await post('/api/board/analyze', {});
    if (r.llm === 'down') {
      patchInner('boardAiOut', `<div class="hint" style="margin-bottom:8px">⚠️ ${esc(r.msg || '外部服务/8788 不在线')}——开外部服务后再试。</div>`);
    } else if (!r.ok) {
      patchInner('boardAiOut', `<div class="hint" style="margin-bottom:8px">${failHintHtml(r.error || '分析失败')}</div>`);
    } else {
      patchInner('boardAiOut', `<div class="editbox" style="margin-bottom:8px"><b>🤖 督办分析</b>
        <span class="dim">（${r.open_count} 件在办 · ${r.elapsed}s · 本地 8788，内容未出本机）</span>
        <div style="margin-top:6px;white-space:pre-wrap">${esc(r.analysis || '（AI 没给出内容）')}</div></div>`);
    }
  } catch (e) {
    patchInner('boardAiOut', `<div class="hint" style="margin-bottom:8px">${failHintHtml(e)}</div>`);
  } finally { rst(); }
};
window.boardToggle = async function (on) {
  BOARD_INC = !!on;
  try {
    const d = await api('/api/board' + (BOARD_INC ? '?include_closed=true' : ''));
    BOARD = d;
    patchInner('boardLanes', boardLanesHtml());
    patchInner('boardHead', '看板（' + boardCountText() + '）');
  } catch (e) { toast('看板刷新失败：' + e.message); }
};
window.boardGo = async function (name) {
  await go('cases');
  const frag = String(name || '').trim().slice(0, 10);
  const inp = document.getElementById('cq');
  if (inp) inp.value = frag;
  caseSearch(frag);
};
/* 批 55b：泳道拖拽滚动 + 滚轮横移（用户反馈「底部滚动条拉动时看不到上面内容」）。
   按住空白处拖 = 内容随手走（>6px 才算拖，避免误触卡片点击）；滚轮上下 = 横向左右
   （只在容器可横滚且真的滚动了才拦默认竖滚）。 */
function boardBindDrag() {
  const host = document.getElementById('boardLanes');
  if (!host || host._dragBound) return;
  host._dragBound = true;
  /* 批 56 修：真滚动容器是 .boardWrap（overflow-x:auto），#boardLanes 只是普通外壳
     （55b 绑错元素导致拖拽无反应——scrollLeft 设在无 overflow 元素上恒为 0）。
     事件绑 host（patchInner 不销毁它，绑定持久）；滚动目标 wrap() 每次现取
     （boardToggle 重拉会重建 .boardWrap）。 */
  const wrap = () => host.querySelector('.boardWrap') || host;
  let down = false, startX = 0, startL = 0, moved = 0;
  host.style.cursor = 'grab';
  host.addEventListener('pointerdown', ev => {
    if (ev.button !== 0) return;
    down = true; moved = 0; startX = ev.clientX; startL = wrap().scrollLeft;
  });
  host.addEventListener('pointermove', ev => {
    if (!down) return;
    const dx = ev.clientX - startX;
    if (Math.abs(dx) > 6) { moved = 1; wrap().scrollLeft = startL - dx; host.style.cursor = 'grabbing'; }
  });
  const up = () => { down = false; host.style.cursor = 'grab'; };
  host.addEventListener('pointerup', up);
  host.addEventListener('pointercancel', up);
  host.addEventListener('pointerleave', up);
  /* 拖拽刚结束时吞掉那一次 click（捕获阶段），防拖完误跳卡片详情 */
  host.addEventListener('click', ev => {
    if (moved) { moved = 0; ev.stopPropagation(); ev.preventDefault(); }
  }, true);
  /* 批 56b：wheel 劫持**已删除**（用户反馈「横向条和页面竖向条鼠标滚动打架」）。
     竖滚一律归页面、横滚走：① 按住空白拖（上面）② Shift+滚轮/触控板横滑（浏览器原生）
     ③ 底部滚动条。滚轮不再改向，零抢方向。 */
}
window.boardBindDrag = boardBindDrag;

/* ---------------- 案件 ---------------- */
async function viewCases() {
  const c = CASES || (CASES = await api('/api/cases'));
  let h = `<div class="card"><h2>案件夹（<span id="caseCount">${caseCountText()}</span> 个 · 根目录 ${esc(c.root)}）</h2>`;
  h += '<div class="hint">「台账」列 = 从台账里认出的案件行（最新状态 / 案号 / 承办律师），'
    + '<b>认不出就写「未认到」，不猜</b>。'
    + '搜索在<b>本地</b>完成（案件夹名 / 委托人 / 案号 / 承办律师 / 状态 / 类别），不会打断正在输入的内容。</div>';
  h += '<div class="row" style="margin-top:8px">'
    + `<input id="cq" placeholder="搜索案件夹名/委托人/案号/律师…（边打边筛）" style="width:300px" value="${esc(CASE_Q)}"`
    + ' oninput="caseSearch(this.value)" onkeyup="caseSearch(this.value)"'
    + ' oncompositionend="caseSearch(this.value)" onchange="caseSearch(this.value)"'
    + ` onkeydown="if(event.key==='Enter'){caseSearch(this.value);}"><select onchange="caseSt(this.value)" style="width:170px"><option value="">状态：全部</option>${caseStOpts()}</select></div>`;
  h += '<div class="card" style="margin-bottom:10px">' +
    '<h3 style="margin:0 0 4px">材料说明（_材料怎么放.md）</h3>' +
    '<div class="hint">每案一份、放在案卷根，写给带教/助理看「这个案子的材料怎么放」。' +
    '点开看哪些夹还缺；补齐只新增这一份 md，<b>不改动、不移动任何既有文件</b>。</div>' +
    '<div class="row" style="margin-top:8px"><button class="act ghost" onclick="guidesOpen()">看哪些缺</button></div>' +
    '<div id="guidesOut"></div></div>';
  /* 列宽重排（2026-09-13）：去掉「子目录」长串列（它把行高撑到 3 倍）——
     完整目录树在「详情」里看；这里只给目录数量（鼠标悬停可看全部）。 */
  h += '<table><tr><th>案件夹</th><th class="r">文件</th><th class="r">00-收件箱</th>'
    + '<th class="r">目录</th><th>规范</th><th>台账</th><th class="r">更新</th><th></th></tr>'
    + `<tbody id="caseRows">${caseRowsHtml()}</tbody></table>`;
  h += '<div class="hint">「目录」列＝该夹下的子目录数量（鼠标悬停看全部名称）；'
    + '完整目录树与材料清单在「详情」里。含 <b>00-收件箱</b> = 已按 v2 规范建卷。</div></div>'
    + '<div id="caseDetail"></div>';
  return h;
}

/* 案件页搜索（批 34）：本地过滤 + 只重绘表格 tbody——禁整页重渲染（防打断输入法/丢光标）；
   触发点 5 个 = input / keyup / compositionend / change / Enter。
   注意：禁用 isComposing 护栏（Chromium 组合期只发 input、上屏后只发 compositionend，
   护栏会堵死中文搜索——2026-09-12 实测教训，任务/台账/客户页同款）。 */
window.caseSearch = function (v) {
  CASE_Q = v || '';
  patchInner('caseRows', caseRowsHtml());
  patchInner('caseCount', caseCountText());
};
function caseStOpts() {
  const seen = [];
  for (const x of ((CASES && CASES.cases) || [])) {
    const st = String((x.ledger || {})['状态'] || '').trim();
    if (st && !seen.includes(st)) seen.push(st);
  }
  return seen.map(s => `<option ${CASE_ST === s ? 'selected' : ''}>${esc(s)}</option>`).join('');
}
window.caseSt = function (v) {
  CASE_ST = v || '';
  patchInner('caseRows', caseRowsHtml());
  patchInner('caseCount', caseCountText());
};
function caseFiltered() {
  const all = (CASES && CASES.cases) || [];
  const q = (CASE_Q || '').trim();
  return all.filter(x => {
    if (CASE_ST && String((x.ledger || {})['状态'] || '') !== CASE_ST) return false;
    if (!q) return true;
    const l = x.ledger || {};
    return (String(x.name || '') + (l['案件'] || '') + (l['委托人'] || '') + (l['案号'] || '')
      + (l['承办律师'] || '') + (l['状态'] || '') + (l['案件类别'] || '')).includes(q);
  });
}
function caseCountText() {
  const f = caseFiltered();
  return `${f.length} / ${(CASES && CASES.count) || f.length}`;
}
function caseRowsHtml() {
  const rows = caseFiltered();
  let h = rows.map(x => `<tr>
    <td class="case">${esc(x.name)}${x.series?`<span class="sub">系列案 · ${x.subcase_count} 个子案件夹</span>`:''}</td>
    <td class="r">${x.files}</td>
    <td class="r">${x.inbox_count ? `<span class="warn">${x.inbox_count}</span>` : '<span class="dim">0</span>'}</td>
    <td class="r" title="${esc((x.dirs || []).join(' / '))}">${(x.dirs || []).length}</td>
    <td>${x.has_inbox?'<span class="ok">收件箱</span>':'<span class="dim">—</span>'}
        ${x.has_pipeline?'<span class="ok"> pipeline</span>':''}
        ${x.has_guide?'<span class="ok"> 材料说明</span>':''}</td>
    <td>${x.series
      ? '<span title="系列案总夹不对应单条台账行——各子夹在「详情」里各自匹配">系列案总夹</span>'
      : caseLedgerBriefHtml(x.ledger)}</td>
    <td class="r nw">${esc(String(x.mtime || '').slice(5))}</td>
    <td><button class="act ghost mini" onclick="showCase('${esc(x.name)}')">详情</button></td></tr>`).join('');
  if (!rows.length) h += '<tr><td colspan="8" class="dim">没有匹配的案件夹。换个词试试（支持案号/委托人片段）。</td></tr>';
  return h;
}

/* 台账行摘要（批 27；批 35 细分）：列表/详情共用；只读匹配，认不出就明说，绝不猜。
   批 35：matched=false 时区分「有候选待指认」与「真未认到」；系列案总夹由调用方给徽章 */
function caseLedgerBriefHtml(l) {
  if (!l) return '<span class="dim">—</span>';
  if (l.matched) {
    const st = l['状态']
      ? `<span class="${l['状态合规']?'ok':'warn'}">${esc(l['状态'])}</span>`
      : '<span class="dim">状态空</span>';
    return `<div>${st} <span class="dim">${esc(l['案件类别']||'')}</span></div>` +
      `<div class="dim">${esc(l['案号']||'（无案号）')}${l['承办律师']?' · '+esc(l['承办律师']):''}</div>`;
  }
  const n = Number(l.candidates || 0);
  if (n) return `<span class="warn" title="台账里有 ${n} 行相近，但拿不准是哪行——点「详情」人工确认">候选 ${n} · 待指认</span>`;
  return '<span class="dim">未认到（台账无相近行）</span>';
}

/* 系列案总夹 → 子案件夹（批 27）：纯只读 */
function subcasesHtml(subs) {
  if (!subs || !subs.length) return '';
  return `<h2 style="margin-top:12px">子案件夹（${subs.length}）</h2>
    <div class="hint">系列案总夹下的各个子案（如「A闽兴」这类客户总夹）。</div>
    <table><tr><th>子案件夹</th><th>文件</th><th>00-收件箱</th><th>说明</th><th>台账</th></tr>` +
    subs.map(s => `<tr><td>${esc(s.name)}</td>
      <td>${s.files}</td>
      <td>${s.inbox_count ? `<span class="warn">${s.inbox_count} 件待入库</span>`
        : (s.inbox ? '<span class="dim">0</span>' : '<span class="dim">无</span>')}</td>
      <td>${s.guide ? '<span class="ok">✓</span>' : '<span class="dim">缺</span>'}</td>
      <td>${caseLedgerBriefHtml(s.ledger)}</td></tr>`).join('') + '</table>';
}

/* 差旅发票（批 27）：只列文件名线索认得出本案的；发票本身不动 */
function travelBlockHtml(tv) {
  if (!tv || !tv.total) return '';
  return `<h2 style="margin-top:12px">差旅发票（${tv.total} 张）</h2>
    <div class="hint">按文件名线索挂到本案的发票（发票仍按月存放在「差旅发票」目录，不动、不改名、不移动）。</div>
    <table><tr><th>月份</th><th>文件</th><th>大小</th><th>为什么算本案</th></tr>` +
    tv.items.map(i => `<tr><td class="dim">${esc(i.month)}</td><td>${esc(i.name)}</td>
      <td class="dim">${i.size_kb} KB</td><td class="dim">${esc(i.why)}</td></tr>`).join('') + '</table>';
}
window.guidesOpen = async function() {
  const out = $('#guidesOut');
  out.innerHTML = '<div class="dim">读取中…（要读台账做匹配，稍等一下）</div>';
  try {
    const g = await api('/api/guides');
    let h = `<div class="hint">共 ${g.count} 夹，已有 <b>${g.have}</b> 份｜根：${esc(g.root)}</div>`;
    h += (g.have === g.count)
      ? '<div class="ok">所有案件夹都已有材料说明 ✓</div>'
      : `<div class="warn">还缺 <b>${g.todo.length}</b> 个：${g.todo.map(esc).join('、')}</div>`;
    h += '<table style="margin-top:6px"><tr><th>案件夹</th><th>说明</th><th>类型</th><th>子目录</th><th>台账案件</th></tr>' +
      g.items.map(x => `<tr><td>${esc(x.folder)}</td>
        <td>${x.exists ? '<span class="ok">✓ 已有</span>' : '<span class="warn">缺</span>'}</td>
        <td class="dim">${x.series ? '系列案总夹' : (x.exec ? '执行案' : '单案')}</td>
        <td class="dim">${x.dirs}（规则外 ${x.extra}）</td>
        <td class="dim">${esc(x.ledger_case || '—')}</td></tr>`).join('') + '</table>';
    if (g.have !== g.count) {
      h += '<div class="row" style="margin-top:8px">' +
        '<button class="act" onclick="guidesApply()">补齐缺的（只新增）</button></div>';
    }
    h += '<div class="hint">说明里会写：本夹现状（哪些标准目录已建/还缺、旧目录建议归到哪）＋存放规则＋命名＋三条铁律。</div>';
    patchInner('guidesOut', h);
  } catch (e) { out.innerHTML = `<div class="red">读取失败：${esc(e.message)}</div>`; }
};
window.guidesApply = async function() {
  if (!window.confirm('将在缺说明的案件夹里各新增一份《_材料怎么放.md》。\n\n只新增、不覆盖、不移动任何文件 —— 继续？')) return;
  const out = $('#guidesOut');
  out.innerHTML = '<div class="dim">写入中…</div>';
  try {
    const r = await post('/api/guides', {apply: true, confirm: true});
    let h = `<div class="ok">已新增 ${r.written.length} 份；跳过 ${r.skipped.length} 份。</div>`;
    h += `<div class="hint">${esc(r.note || '')}</div>`;
    if (r.written.length) {
      h += '<details><summary class="dim">看新增了哪些</summary><pre>' +
        esc(r.written.join('\n')) + '</pre></details>';
    }
    out.innerHTML = h;
    CASES = null;                                  // 刷新案件列表的「✓ 材料说明」标记
    setTimeout(() => render(), 600);
  } catch (e) { out.innerHTML = `<div class="red">写入失败：${esc(e.message)}</div>`; }
};
window.showCase = async function(name) {
  const d = await api('/api/case?name=' + encodeURIComponent(name));
  if (d.error) return alert(d.error);
  let h = `<div class="card"><h2>${esc(d.name)}</h2>
    <div class="hint">${esc(d.path)}　${d.guide_exists ? '<span class="ok">✓ _材料怎么放.md</span>'
      : '<span class="warn">缺 _材料怎么放.md（材料规则）</span>'}</div><div class="tree">` +
    d.tree.map(t => `<div><b>${esc(t.dir)}</b> <span class="dim">(${t.count})</span><br>
      <span class="dim">${t.files.map(esc).join('　')}</span></div>`).join('') + '</div>';
  h += subcasesHtml(d.subcases);
  h += caseLedgerHtml(d.ledger);
  PAY_DATA = {case: d.name, items: (d.payments && d.payments.items) || [],
              summary: (d.payments && d.payments.summary) || {count: 0, total: 0, pending: 0}};
  h += paymentBlockHtml(d.payments, d.name);
  h += travelBlockHtml(d.travel);
  DD_DATA = d.deepdive || null;
  h += deepdiveBlockHtml(d.deepdive, d.name);
  if (d.intake_log.length) h += `<h2 style="margin-top:12px">intake-log（近 12 行）</h2><pre>${esc(d.intake_log.join('\n'))}</pre>`;
  h += '</div>';
  $('#caseDetail').innerHTML = h;
  $('#caseDetail').scrollIntoView({behavior:'auto'});   // 批 77：smooth 实测定位不可靠（视口未动，用户以为没反应），改即时定位
};

/* ---------------- 案件研究报告（批 49 立「深挖报告」，批 60 泛化） ----------------
   报告由助手在 WorkBuddy 会话里跑元典生成，按约定落案件夹 08-过程文件\研究报告\
   （被执行人深挖报告-YYYYMMDD.md / 承办法官裁判参考-<法官>-YYYYMMDD.md 等）；
   工作台只读列出，预览走 /api/deepdive/view（本机文本直显，不上行 LLM，零外发）。 */
let DD_DATA = null;   // 当前详情页的研究报告清单快照（ddView 按索引取，避免 onclick 拼长文件名）
function deepdiveBlockHtml(dd, caseName) {
  if (!dd) return '';
  let h = `<h2 style="margin-top:12px">案件研究报告（${dd.count}）</h2>`;
  if (!dd.count) {
    h += `<div class="dim">本夹还没有研究报告。</div>
      <div class="hint">在工作台会话里说「深挖 某公司」→ 元典查被执行人关联公司 / 股权 / 司法 / 执行线索；说「查 某案 承办法官裁判参考」→ 元典查该法官同类案件被撤销 / 发回的理由、按我方立场给攻防线索
      → 报告自动落本夹 <b>08-过程文件\\研究报告\\</b>（被执行人深挖报告 / 承办法官裁判参考-<法官>-日期.md 等），这里就会列出。
      <button class="act ghost mini" onclick="ddOpen('${jsq(caseName)}')">📂 打开存放位置</button></div>`;
    return h;
  }
  h += `<div class="hint">由助手在会话里跑元典生成，落 ${esc(dd.dir)}；工作台只读列出。
      <button class="act ghost mini" onclick="ddOpen('${jsq(caseName)}')">📂 打开文件夹</button></div>
    <table><tr><th>报告</th><th style="width:100px">日期</th><th style="width:88px">大小</th><th style="width:130px">操作</th></tr>`;
  dd.items.forEach((it, i) => {
    h += `<tr><td>${esc(it.name)}</td><td class="dim">${esc(it.mtime)}</td>
      <td class="dim">${it.size_kb} KB</td>
      <td><button class="act ghost mini" onclick="ddView('${jsq(caseName)}',${i})">预览</button></td></tr>`;
  });
  return h + '</table>';
}
window.ddView = async function(caseName, idx) {
  const it = (DD_DATA && DD_DATA.items && DD_DATA.items[idx]) || null;
  if (!it) return;
  showModalLoading('案件研究报告', '读取中…');
  try {
    const r = await api('/api/deepdive/view?case=' + encodeURIComponent(caseName)
      + '&file=' + encodeURIComponent(it.name));
    if (r.error) { showModal('案件研究报告', `<div class="red">${esc(r.error)}</div>`); return; }
    showModal('案件研究报告 · ' + it.name,
      `<pre style="max-height:62vh;overflow:auto;white-space:pre-wrap;font-size:12.5px;line-height:1.65">${esc(r.text || '')}</pre>
       <div class="hint">${esc(it.mtime || '')} · ${it.size_kb} KB · 检索数据由元典核验后整理</div>`);
  } catch (e) {
    showModal('案件研究报告', `<div class="red">读取失败：${esc(e.message)}</div>`);
  }
};
window.ddOpen = async function(caseName) {
  try {
    const r = await post('/api/deepdive/open', {case: caseName});
    if (!r.ok && r.error) { alert(r.error); return; }
    toast('已打开' + (r.opened ? '：' + r.opened : '存放位置'));
  } catch (e) { alert('打开失败：' + e.message); }
};

/* ---------------- 执行款（批 62）：自建库记录 + 台账受控同步 ----------------
   回款主存 workbench.db（payment_ops）；「同步到台账」走 ledger_ops.sync_payments
   受控写入（锁检测 → 备份 → 写后审计），追加镜像「执行款」sheet；台账只增不回头。 */
let PAY_DATA = null;   // 当前详情页的执行款快照（payAdd/payDel/paySync 用）
function paymentBlockHtml(pay, caseName) {
  const s = (pay && pay.summary) || {count: 0, total: 0, pending: 0};
  let h = `<h2 style="margin-top:12px">💰 执行款（${s.count} 笔 ｜ 合计 ${Number(s.total || 0).toLocaleString('zh-CN')} 元${s.pending ? ' ｜ 待同步 ' + s.pending : ''}）</h2>`;
  h += `<div class="row" style="margin:8px 0">
    <input id="pkDate" type="date" style="width:150px" title="回款日期">
    <input id="pkAmt" style="width:130px" inputmode="decimal" placeholder="金额（元）">
    <select id="pkSrc"><option>法院划付</option><option>对方支付</option><option>其他</option></select>
    <input id="pkNote" style="width:190px" placeholder="备注（可选）">
    <button class="act" onclick="payAdd()">记一笔</button>
    <button class="act ghost" onclick="paySync()">同步到台账</button></div>
    <div class="hint">记录存工作台自建库（随时可记，不怕台账被占用）；「同步」把<b>未同步</b>记录追加进台账「执行款」页（受控写入：锁检测 → 备份 → 写后审计）。台账只增不回头（删记录不回删台账行）。</div>`;
  h += '<div id="pkOut"></div>';
  const items = (pay && pay.items) || [];
  if (items.length) {
    h += `<table style="margin-top:6px"><tr><th style="width:110px">日期</th><th style="width:120px">金额（元）</th><th style="width:100px">来源</th><th>备注</th><th style="width:100px">台账</th><th style="width:56px"></th></tr>`;
    for (const it of items) {
      h += `<tr><td>${esc(it.pay_date)}</td><td>${Number(it.amount).toLocaleString('zh-CN')}</td>
        <td>${esc(it.source || '—')}</td><td class="dim">${esc(it.note || '')}</td>
        <td class="dim">${it.synced_at ? '✓ 已同步' : '未同步'}</td>
        <td><button class="act ghost mini" onclick="payDel(${it.id})">删</button></td></tr>`;
    }
    h += '</table>';
  } else {
    h += '<div class="dim">还没有回款记录。记一笔 →「同步到台账」（执行案回款建议同步，带教可见）。</div>';
  }
  return h;
}
window.payAdd = async function() {
  const out = $('#pkOut');
  if (!PAY_DATA) return;
  const pay_date = $('#pkDate').value, amount = $('#pkAmt').value;
  if (!pay_date || !amount) { out.innerHTML = '<div class="warn">填回款日期和金额。</div>'; return; }
  out.innerHTML = '<div class="dim">保存中…</div>';
  try {
    const r = await post('/api/payments/add', {folder: PAY_DATA.case, pay_date, amount,
      source: $('#pkSrc').value, note: $('#pkNote').value, confirm: true});
    if (r.error) { out.innerHTML = `<div class="red">失败：${esc(r.error)}</div>`; return; }
    toast('已记一笔回款');
    await window.showCase(PAY_DATA.case);
  } catch (e) { out.innerHTML = `<div class="red">失败：${esc(e.message)}</div>`; }
};
window.payDel = async function(id) {
  if (!PAY_DATA) return;
  if (!confirm('删除这笔回款记录？（台账已同步行不会被删——需要删行请到 WPS 手工处理）')) return;
  try {
    const r = await post('/api/payments/delete', {id, confirm: true});
    if (r.error) { alert(r.error); return; }
    toast('已删除');
    await window.showCase(PAY_DATA.case);
  } catch (e) { alert('失败：' + e.message); }
};
window.paySync = async function() {
  if (!PAY_DATA) return;
  const out = $('#pkOut');
  out.innerHTML = '<div class="dim">同步中（受控写入：锁检测 → 备份 → 写后审计）…</div>';
  try {
    const r = await post('/api/payments/sync', {folder: PAY_DATA.case, confirm: true});
    if (r.error) { out.innerHTML = `<div class="red">失败：${esc(r.error)}</div>`; return; }
    if (r.action === 'none') { out.innerHTML = `<div class="ok">${esc(r.note || '没有待同步记录')}</div>`; return; }
    out.innerHTML = `<div class="ok">已同步 ${r.count || 0} 行到台账「执行款」（从第 ${r.start_row} 行起）</div>
      <div class="dim">${esc(r.note || '')}${r.backup ? '｜备份 ' + esc(r.backup) : ''}</div>`;
    await window.showCase(PAY_DATA.case);
  } catch (e) { out.innerHTML = `<div class="red">失败：${esc(e.message)}</div>`; }
};

/* ---- 执行款清偿分配（计算器 ⑪）：纯本地；费用 → 一般利息 → 本金 → 加倍部分 ---- */
function parseRepLines(txt) {
  const reps = [];
  for (const raw of String(txt || '').split(/\r?\n/)) {
    const s = raw.trim(); if (!s) continue;
    const m = s.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})[\s,，\t]+([0-9][0-9,，.]*)$/);
    if (!m) return {error: '这行没看懂（要「日期 金额」）：' + s};
    const date = m[1] + '-' + String(m[2]).padStart(2, '0') + '-' + String(m[3]).padStart(2, '0');
    const amount = parseFloat(m[4].replace(/[,，]/g, ''));
    if (!(amount > 0)) return {error: '金额不合法：' + s};
    reps.push({date, amount});
  }
  reps.sort((a, b) => (a.date < b.date ? -1 : 1));
  return {reps};
}
function calcExecAlloc(inp) {
  const P = parseFloat(String(inp.principal || '').replace(/[,，]/g, ''));
  const F = parseFloat(String(inp.fees || '').replace(/[,，]/g, '')) || 0;
  if (!(P > 0)) return {error: '先填本金（正数）'};
  const isum = parseFloat(String(inp.interestSum || '').replace(/[,，]/g, '')) || 0;
  const irate = parseFloat(String(inp.interestRate || '').replace(/[,，]/g, '')) || 0;
  const useRate = !isum && irate > 0 && !!inp.startDate;
  if (!isum && !useRate) return {error: '一般利息：填「总额」或「年利率% + 起算日」二选一'};
  if (inp.delayed && !inp.delayedStart) return {error: '加倍部分需要「迟延起算日」'};
  const reps = (inp.reps || []).slice();
  const dailyR = useRate ? irate / 100 / 365 : 0;
  let remP = P, remFee = F, pool = isum, acc = 0, daPool = 0, daBase = P;
  let last = inp.startDate || '', lastDa = inp.delayedStart || '';
  const steps = [];
  for (const rep of reps) {
    if (useRate && last) {
      const d = Math.max(0, calcDays(last, rep.date) || 0);
      acc += remP * dailyR * d;
    }
    if (inp.delayed && lastDa) {
      const d = Math.max(0, calcDays(lastDa, rep.date) || 0);
      daPool += daBase * 0.000175 * d;
      lastDa = rep.date;
    }
    let rem = rep.amount;
    const dFee = Math.min(remFee, rem); remFee -= dFee; rem -= dFee;
    const avail = useRate ? acc : pool;
    const dInt = Math.min(avail, rem);
    if (useRate) { acc -= dInt; } else { pool -= dInt; }
    rem -= dInt;
    const dPri = Math.min(remP, rem); remP -= dPri; rem -= dPri;
    daBase = remP;
    const dDa = Math.min(daPool, rem); daPool -= dDa; rem -= dDa;
    steps.push({date: rep.date, amount: rep.amount, dFee, dInt, dPri, dDa,
                remP, intLeft: (useRate ? acc : pool), daLeft: daPool});
    last = rep.date;
  }
  let tailInt = 0, tailDa = 0;
  if (inp.endDate) {
    if (useRate && last) {
      tailInt = remP * dailyR * Math.max(0, calcDays(last, inp.endDate) || 0);
      acc += tailInt;
    }
    if (inp.delayed && lastDa) {
      tailDa = daBase * 0.000175 * Math.max(0, calcDays(lastDa, inp.endDate) || 0);
      daPool += tailDa;
    }
  }
  const remInt = useRate ? acc : pool;
  return {steps, summary: {remFee, remInt, remP, daPool, tailInt, tailDa, useRate,
                           total: remFee + remInt + remP + daPool}};
}
window.calcExecRun = function() {
  const out = document.getElementById('exOut');
  if (!out) return;
  const pr = parseRepLines(document.getElementById('exRep').value);
  if (pr.error) { out.innerHTML = `<div class="warn">${esc(pr.error)}</div>`; return; }
  const r = calcExecAlloc({
    principal: document.getElementById('exP').value,
    fees: document.getElementById('exF').value,
    interestSum: document.getElementById('exIS').value,
    interestRate: document.getElementById('exIR').value,
    startDate: document.getElementById('exSD').value,
    endDate: document.getElementById('exED').value,
    delayed: document.getElementById('exDL').checked,
    delayedStart: document.getElementById('exDD').value,
    reps: pr.reps});
  if (r.error) { out.innerHTML = `<div class="dim">${esc(r.error)}</div>`; return; }
  const S = r.summary, dl = document.getElementById('exDL').checked;
  let h = '';
  if (r.steps.length) {
    h += '<table style="margin-top:6px"><tr><th>回款日</th><th>金额</th><th>抵费用</th><th>抵一般利息</th><th>抵本金</th>'
      + (dl ? '<th>抵加倍</th>' : '') + '<th>剩余本金</th></tr>';
    for (const st of r.steps) {
      h += `<tr><td>${esc(st.date)}</td><td>${calcFmt(st.amount)}</td><td>${calcFmt(st.dFee)}</td>
        <td>${calcFmt(st.dInt)}</td><td>${calcFmt(st.dPri)}</td>` + (dl ? `<td>${calcFmt(st.dDa)}</td>` : '')
        + `<td>${calcFmt(st.remP)}</td></tr>`;
    }
    h += '</table>';
  }
  h += `<div class="ok" style="margin-top:6px">未清偿：<b>${calcFmt(S.total)}</b> 元 ＝ 剩余费用 ${calcFmt(S.remFee)} ＋ 剩余一般利息 ${calcFmt(S.remInt)}${S.useRate ? '（算至截止日）' : ''} ＋ 剩余本金 ${calcFmt(S.remP)}`
    + (S.daPool > 0 ? ` ＋ 加倍利息 ${calcFmt(S.daPool)}` : '') + '</div>';
  h += '<div class="hint">抵扣顺序：费用 → 一般利息 → 本金 → 加倍部分（《民法典》第 561 条；法释〔2014〕8号第 4 条）。</div>';
  out.innerHTML = h;
};

/* 案件夹 ↔ 台账行（批 15）：只读展示；匹配靠当事人片段/主体名/委托人命中，
   分数 ≥3 且唯一最高才算认定，否则明说「没认出来」并列出候选，绝不硬套。 */
function caseLedgerHtml(led) {
  if (!led) return '';
  const m = led.matched;
  if (!m) {
    let h = `<h2 style="margin-top:12px">台账行</h2>
      <div class="warn">没在台账里认出这个案件夹 —— 不做猜测。`;
    if (led.candidates && led.candidates.length) {
      h += `<table style="margin-top:6px"><tr><th>候选台账行</th><th>表/行</th><th>为什么像</th></tr>` +
        led.candidates.map(c => `<tr><td>${esc(c['案件'])}</td>
          <td class="dim">${esc(c.sheet)} r${c.row}</td>
          <td class="dim">${esc((c.why || []).join('；'))}</td></tr>`).join('') + '</table>';
    } else {
      h += '<div class="dim">台账里连相近的行都没有（可能还没登记，或写法差得较远）。</div>';
    }
    return h + `<div class="hint">规则：当事人片段各 +2、主体名/委托人/类别各 +1，合计 ≥3 且唯一最高才认定；
      候选只有 1 条或并列时交给你判断。</div>`;
  }
  const rows = [['台账位置', `${m.sheet} 第 ${m.row} 行`], ['案件', m['案件']], ['委托人', m['委托人']],
    ['类别', m['案件类别']], ['最新状态', m['状态'] + (m['状态合规'] ? '' : '（旧写法）')],
    ['案号', m['案号']], ['法院/仲裁', m['法院']], ['开庭时间', m['开庭时间']],
    ['标的额', m['标的额']], ['基础律师费', m['基础律师费']]];
  let h = `<h2 style="margin-top:12px">台账行</h2>
    <div class="row"><span class="hint">匹配依据：${esc((m.match_why || []).join('；'))}（共 ${m.match_score} 分）</span>
      <span style="flex:1"></span>
      <button class="act ghost mini" onclick="tlExport('${jsq(m.sheet)}',${m.row})">导出时间轴 xlsx</button></div>
    <div id="tlOut"></div>
    <table>` + rows.filter(x => x[1]).map(x =>
      `<tr><th style="width:96px">${esc(x[0])}</th><td>${esc(x[1])}</td></tr>`).join('') + '</table>';
  h += `<h2 style="margin-top:12px">当前进度</h2><div>${esc(m['当前进度'] || '（空）')}</div>`;
  const tl = m.timeline || [];
  if (tl.length) {
    h += `<h2 style="margin-top:12px">时间轴（${tl.length} 条，取自台账「进度日志」）</h2>
      <table><tr><th style="width:78px">日期</th><th>事项</th><th style="width:88px">来自</th></tr>` +
      tl.map(x => `<tr><td class="dim">${esc(x['月日'] || '—')}</td>
        <td>${esc(x['事项'])}</td><td class="dim">${esc(x['来源'])}</td></tr>`).join('') + '</table>' +
      `<div class="hint">台账日志只写「MM.DD」，所以这里也只有月日；年份以台账原文为准，工作台不推断。
        导出正式时间轴时再补年份。</div>`;
  }
  return h;
}

/* 时间轴导出（批 15，方案 §5.1）：先 dry-run 看条目与落点，确认才写；只新增不覆盖。
   不重写根目录模板（那份带图表，openpyxl 重写会丢图表），输出独立表格。 */
window.tlExport = async function(sheet, row) {
  const out = $('#tlOut');
  out.innerHTML = '<div class="dim">正在汇总台账条目…</div>';
  try {
    const r = await post('/api/timeline/export', {sheet: sheet, row: row, confirm: false});
    const p = r.plan || {};
    if (!p.count) { out.innerHTML = '<div class="warn">这一行没有可导出的时间轴条目。</div>'; return; }
    const ok = confirm('将导出 ' + p.count + ' 条时间轴到：\n' + p.dst
      + '\n\n（只新增文件、不覆盖；时间列照抄台账原文，年份不推断）\n继续？');
    if (!ok) { out.innerHTML = '<div class="dim">已取消，未落盘。</div>'; return; }
    const r2 = await post('/api/timeline/export', {sheet: sheet, row: row, confirm: true});
    out.innerHTML = '<div class="ok">已导出 ' + (r2.count || 0) + ' 条：' + esc(r2.path || '') + '</div>';
  } catch (e) { out.innerHTML = '<div class="red">导出失败：' + esc(e.message) + '</div>'; }
};

/* ---------------- 立卷 ---------------- */
async function viewNew() {
  const cs = CASES || (CASES = await api('/api/cases'));
  return `<div class="card"><h2>开新案（建案卷 + 材料存放规则 + 可选台账草稿行 / 知识库案件页）</h2>
  <div class="hint">一次做完：按 v2 规范建好整套目录 + 本夹 <b>_材料怎么放.md</b>（材料规则）+ intake-log；
    执行案自动多 <b>09-财产查控 / 10-处置分配</b>。之后新材料只需丢进 <b>00-收件箱</b>，
    回「收件箱入库」点扫描 → 确认归位（移动 + 留痕）。</div>
  <div class="editbox" style="margin-top:10px">
    <b>📄 从材料预填</b>
    <span class="hint" style="margin-left:8px">扫描件/材料 → <b>本机 OCR + 本地 AI 抽取</b>（AI 走本地 8788，材料不出本机）；
    <b>预填只是草稿——核对后照常点下方「确认开案」，本页绝不自动提交。</b></span>
    <div class="row" style="margin:8px 0">
      <input type="file" id="n_pf_file" style="max-width:250px">
      <button class="act ghost" onclick="pfUpload(this)">上传并预填</button>
      <span class="dim">或</span>
      <input id="n_pf_case" list="pfCaseList" placeholder="案件夹名（已建案的）" style="width:200px">
      <datalist id="pfCaseList">${((cs && cs.cases) || []).map(c => `<option value="${esc(c.name)}"></option>`).join('')}</datalist>
      <input id="n_pf_name" placeholder="00-收件箱里的文件名" style="width:170px">
      <button class="act ghost" onclick="pfInbox(this)">识别并预填</button>
    </div>
    <div id="pfOut" class="hint" style="white-space:pre-wrap"></div>
  </div>
  <div class="form-grid" style="margin-top:10px">
    <input id="n_no" placeholder="所内编号 民-A-26NNN（必填）">
    <input id="n_cl" placeholder="委托人（必填）">
    <input id="n_op" placeholder="对方（必填）">
    <input id="n_ca" placeholder="案由（必填，如 买卖合同纠纷）">
    <select id="n_ty"><option>诉讼</option><option>执行</option><option>仲裁</option>
      <option>非诉</option><option>调解</option><option>执行异议</option></select>
    <select id="n_sheet"><option>示例案件</option><option>其他案件</option></select>
  </div>
  <div class="row">
    <input id="n_court" placeholder="法院/仲裁（可空）" style="width:210px">
    <input id="n_cno" placeholder="法院案号（立案后再填也行）" style="width:230px">
    <input id="n_law" placeholder="承办律师（可空）" style="width:160px">
  </div>
  <div class="row">
    <label class="dim"><input type="checkbox" id="n_ledger" checked> 追加台账草稿行（[待确认]）</label>
    <label class="dim"><input type="checkbox" id="n_entity" checked> 建知识库案件页骨架（只新建、不覆盖）</label>
    <label class="dim"><input type="checkbox" id="n_pipe" checked> 跑 pipeline 初始化</label>
  </div>
  <div class="row"><button class="act ghost" onclick="doNew(false)">预览（dry-run）</button>
    <button class="act" onclick="doNew(true)">确认开案</button>
    <span class="hint">编号格式：民-/执-/仲- + 字母 + 数字（如 民-A-26016）；预览不落盘。</span></div>
  <div id="newOut"></div></div>`;
}
function newPreviewHtml(r) {
  const p = r.plan || {}, e = r.entity, v = r.vault || {};
  let h = `<div class="editbox"><div class="row"><b>将建案件夹：</b>${esc(p.folder || '')}</div>
    <div class="hint">${esc(p.path || '')}</div>`;
  if (p.exists) h += `<div class="red">✗ 该案件夹已存在 —— 执行会被拒绝（不会覆盖既有案卷）</div>`;
  h += `<div class="row"><b>目录（${(p.dirs || []).length} 个）：</b>` +
    (p.dirs || []).map(d => `<span class="pill">${esc(d)}</span>`).join('') + '</div>' +
    `<div class="hint">另建：<b>intake-log.md</b>（入库留痕）· <b>_材料怎么放.md</b>（本夹材料规则，放案卷根）</div>`;
  if (p.ledger_draft) h += `<div class="row"><b>台账草稿行：</b>` +
    Object.entries(p.ledger_draft).map(([k, val]) => `<span class="pill">${esc(k)}=${esc(val)}</span>`).join('') + '</div>';
  if (e) {
    h += `<div class="row"><b>知识库案件页：</b>${esc(e.title)} ` +
      (e.exists ? `<span class="warn">已有同名页 → 跳过，不覆盖</span>` : '<span class="ok">将新建</span>') +
      `<div class="hint">${esc(e.dst)}</div></div>`;
    if (v && v.dirty) h += `<div class="warn">知识库现有 ${v.dirty} 处未提交改动（${esc(v.detail || '')}）——工作台只新建、不代提交</div>`;
  }
  h += `<div class="dim">点「确认开案」才真正落盘（建目录只新增；台账草稿行会先备份台账）。</div></div>`;
  return h;
}
function newDoneHtml(r) {
  let h = `<div class="editbox"><div class="ok">✓ 已开案：${esc(r.path || '')}</div>`;
  if (r.guide) h += `<div class="dim">材料规则：${esc(r.guide)}</div>`;
  if (r.ledger) h += `<div class="ok">台账草稿行：${esc(r.ledger.sheet)} 第 ${r.ledger.row} 行（[待确认]）` +
    `｜备份 ${esc(String(r.ledger.backup || '').split('\\').pop())}</div>`;
  if (r.entity) h += `<div class="ok">知识库案件页：${esc(r.entity)}</div>`;
  if (r.entity_skipped) h += `<div class="warn">知识库案件页已跳过：${esc(r.entity_skipped)}</div>`;
  if (r.entity_error) h += `<div class="red">知识库案件页失败（案卷不受影响）：${esc(r.entity_error)}</div>`;
  h += `<div class="row" style="margin-top:8px"><b>下一步：</b>把材料（手机照片/扫描件/对方发来的文件）
    丢进本夹的 <b>00-收件箱</b>，再回工作台「收件箱入库」点「扫描」→ 确认归位。</div>
    <div class="row"><button class="act ghost mini" onclick="go('intake')">去「收件箱入库」</button>
      <button class="act ghost mini" onclick="go('cases')">去「案件」页看目录</button></div></div>`;
  return h;
}
window.doNew = async function (confirm) {
  const g = id => (($('#' + id) || {}).value || '').trim();
  const form = {case_no: g('n_no'), client: g('n_cl'), opponent: g('n_op'), cause: g('n_ca'),
                type: $('#n_ty').value, court: g('n_court'), case_no_court: g('n_cno'),
                lawyer: g('n_law')};
  const body = {form: form, confirm: confirm, append_ledger: $('#n_ledger').checked,
                sheet: $('#n_sheet').value, write_entity: $('#n_entity').checked,
                with_pipeline: $('#n_pipe').checked};
  const out = $('#newOut');
  out.innerHTML = '<div class="dim">处理中…</div>';
  try {
    const r = await post('/api/new_case', body);
    CASES = null;
    out.innerHTML = r.dry_run ? newPreviewHtml(r) : newDoneHtml(r);
  } catch (e) { out.innerHTML = `<div class="red">失败：${esc(e.message)}</div>`; }
};
/* ---- 批 53：从材料预填（本机 OCR + 本地 8788 抽取；只填草稿、绝不自动提交）---- */
window.pfUpload = async function (btn) {
  const f = ((document.getElementById('n_pf_file') || {}).files || [])[0];
  const out = document.getElementById('pfOut');
  if (!f) { if (out) out.innerHTML = '<span class="warn">先选一个文件（PDF/图片）。</span>'; return; }
  const rst = btnBusy(btn, '识别中…（大文件要等）');
  if (out) out.innerHTML = '<span class="dim">本机 OCR + 本地 AI 抽取中…（只发本地 8788）</span>';
  try {
    const data = await wrFileB64(f);
    const r = await post('/api/newcase/prefill', {filename: f.name, b64: data});
    pfShowResult(r, out);
  } catch (e) { if (out) out.innerHTML = failHintHtml(e); }
  finally { rst(); }
};
window.pfInbox = async function (btn) {
  const out = document.getElementById('pfOut');
  const g = id => ((document.getElementById(id) || {}).value || '').trim();
  const caseName = g('n_pf_case'), name = g('n_pf_name');
  if (!caseName || !name) {
    if (out) out.innerHTML = '<span class="warn">案件夹与收件箱文件名都要填。</span>';
    return;
  }
  const rst = btnBusy(btn, '识别中…（大文件要等）');
  if (out) out.innerHTML = '<span class="dim">本机 OCR + 本地 AI 抽取中…（只发本地 8788）</span>';
  try {
    const r = await post('/api/newcase/prefill', {case: caseName, name: name});
    pfShowResult(r, out);
  } catch (e) { if (out) out.innerHTML = failHintHtml(e); }
  finally { rst(); }
};
function pfShowResult(r, out) {
  if (!out) return;
  if (!r) { out.innerHTML = '<span class="warn">没有拿到结果。</span>'; return; }
  if (r.llm === 'down') {
    out.innerHTML = `<span class="warn">${esc(r.msg || '外部服务/8788 不在线')}——等外部服务在跑时再试，或直接手工填写下表。</span>`;
    return;
  }
  if (!r.ok) { out.innerHTML = failHintHtml(r.error || '预填失败'); return; }
  const f = r.fields || {};
  const map = [['n_cl', '委托人', f['委托人']], ['n_op', '对方', f['对方']],
               ['n_ca', '案由', f['案由']], ['n_court', '法院', f['法院']],
               ['n_cno', '案号', f['案号']]];
  const filled = [], skipped = [];
  for (const [id, lab, v] of map) {
    const el = document.getElementById(id);
    if (!v) { skipped.push(lab); continue; }               // 空字段不碰输入框
    if (el) { el.value = v; el.title = '（AI 预填·请核对）'; el.style.background = 'var(--accent-wash)'; }
    filled.push([lab, v]);
  }
  let h = `<div class="ok">预填完成（OCR ${r.ocr_chars || 0} 字${r.truncated ? '，超长已截断' : ''}，用时 ${r.elapsed || 0} 秒）</div>`;
  h += filled.length
    ? '<div class="row" style="margin:4px 0">' + filled.map(pair =>
        `<span class="pill">${esc(pair[0])}=${esc(pair[1])}（AI 预填·请核对）</span>`).join('') + '</div>'
    : '<div class="warn">这次没抽出任何字段（材料可能不是开案材料）——直接手工填写。</div>';
  if (skipped.length) h += `<div class="dim">未抽出（保持空）：${skipped.join('、')}。</div>`;
  h += '<div class="warn"><b>预填只是草稿，核对后照常点下方「确认开案」</b>——本页不会自动提交。</div>';
  out.innerHTML = h;
}

/* ---------------- 写作项目（批 51；CaseBoard 反向移植） ----------------
   一任务一个项目：诉状/代理词等长文 → 项目化登记 + 版本入卷留痕。
   版本文件**复制入卷**：<案件夹>\02-法律文书\<项目标题>\vNN-YYYYMMDD-原名（只新增不覆盖）。
   台账零写入；新建/传版本都走「预览 → 确认」；打开版本按类型分发（md/txt 文本窗、
   PDF/图片走 file_view 直出、docx 走系统程序）。 */
let WRITING = null, WR_FILTER = {st: 'all', q: ''};
let WR_DETAIL = null, WR_PENDING = null;   // 详情浮层快照 / 待确认的版本上传
const WR_NEXT = {'起草': '修订', '修订': '送审', '送审': '定稿', '定稿': '修订'};
const WR_STATUS_CLS = {'起草': 's0', '修订': 's1', '送审': 'p1', '定稿': 's2'};
async function viewWriting() {
  CURRENT_VIEW = 'writing';
  const cs = CASES || (CASES = await api('/api/cases'));
  const d = await api('/api/writing/projects');
  WRITING = d;
  return `<div class="card"><h2>写作项目（${(d.items || []).length} 个 · 版本文件入案卷 <code>${esc(d.doc_dir || '02-法律文书')}</code>）</h2>
    <div class="hint">一次任务一个项目：登记项目 → 传版本（<b>复制入卷</b>留痕，文件名 vNN-日期-原名）→ 状态机 起草→修订→送审→定稿（定稿可回退修订）。
    <b>台账零写入</b>；版本文件只新增、不覆盖、不删除。点「详情·传版本」看版本表 / 传新版本 / 打开版本文件。</div>
    <div class="row" style="margin:10px 0">
      <span class="seg" id="wrSeg">${wrSegHtml()}</span>
      <input id="wrQ" placeholder="搜项目名/案件名/类型…（边打边筛）" style="width:260px" value="${esc(WR_FILTER.q)}"
        oninput="wrSearch(this.value)" onkeyup="wrSearch(this.value)"
        oncompositionend="wrSearch(this.value)" onchange="wrSearch(this.value)">
      <span id="wrCount" class="dim" style="font-size:12px">${wrCountText()}</span></div>
    <div class="row" style="flex-wrap:wrap;gap:8px;border:1px dashed var(--rule);border-radius:8px;padding:10px">
      <b>新建项目</b>
      <input id="wrCase" list="wrCaseList" placeholder="案件夹名（从案件列表选）" style="width:220px">
      <datalist id="wrCaseList">${((cs && cs.cases) || []).map(c => `<option value="${esc(c.name)}"></option>`).join('')}</datalist>
      <select id="wrType">${(d.doc_types || []).map(t => `<option>${esc(t)}</option>`).join('')}</select>
      <input id="wrTitle" placeholder="项目标题（如：民事起诉状）" style="width:240px">
      <button class="act" onclick="wrCreate(this)">新建</button>
      <span class="hint" style="margin:0">新建只在自建库登记 + 建目标文件夹（随首个版本落卷）</span></div>
    <table><tr><th>项目</th><th>案件</th><th>类型</th><th>状态</th><th class="r">版本</th><th>最近更新</th><th style="width:230px"></th></tr>
      <tbody id="wrRows">${wrRowsHtml()}</tbody></table>
    <div id="wrOut"></div></div>`;
}
function wrFiltered() {
  const all = (WRITING && WRITING.items) || [];
  const q = (WR_FILTER.q || '').trim();
  return all.filter(x => {
    if (WR_FILTER.st !== 'all' && x.status !== WR_FILTER.st) return false;
    if (!q) return true;
    return (String(x.title || '') + String(x.case_name || '') + String(x.doc_type || '')).includes(q);
  });
}
function wrSegHtml() {
  const counts = (WRITING && WRITING.counts) || {};
  const segs = [['all', '全部'], ['起草', '起草'], ['修订', '修订'], ['送审', '送审'], ['定稿', '定稿']];
  return segs.map(([k, t]) =>
    `<button class="${WR_FILTER.st === k ? 'on' : ''}" onclick="wrSeg('${k}')">${t}${k === 'all' ? '' : ' ' + (counts[k] || 0)}</button>`).join('');
}
function wrCountText() {
  return '显示 ' + wrFiltered().length + ' / ' + ((WRITING && WRITING.items) || []).length + ' 个';
}
function wrRowsHtml() {
  const rows = wrFiltered();
  let h = rows.map(x => {
    const nxt = WR_NEXT[x.status];
    const btn = nxt
      ? ` <button class="act ghost mini" onclick="wrSetStatus(${x.id}, '${nxt}', this)">${x.status === '定稿' ? '↩ 回退修订' : '→ ' + nxt}</button>`
      : '';
    return `<tr>
      <td><b>${clip(x.title, 240)}</b></td>
      <td>${clip(x.case_name, 170)}</td>
      <td class="dim">${esc(x.doc_type)}</td>
      <td><span class="badge ${WR_STATUS_CLS[x.status] || 's0'}">${esc(x.status)}</span></td>
      <td class="r n">${x.version_count || 0}</td>
      <td class="dim nw">${esc(String(x.last_update || '').slice(0, 16))}</td>
      <td class="nw"><button class="act ghost mini" onclick="wrDetail(${x.id})">详情·传版本</button>${btn}</td></tr>`;
  }).join('');
  if (!rows.length) h = '<tr><td colspan="7" class="dim">没有符合条件的项目（清一下筛选，或在上面新建）。</td></tr>';
  return h;
}
/* 批 47n：筛选（chips + 文本搜，本地过滤 + patchInner；IME 五触发点同任务/台账页） */
window.wrSeg = function (k) {
  WR_FILTER.st = k;
  patchInner('wrSeg', wrSegHtml());
  patchInner('wrRows', wrRowsHtml());
  patchInner('wrCount', wrCountText());
};
window.wrSearch = function (v) {
  WR_FILTER.q = v || '';
  patchInner('wrRows', wrRowsHtml());
  patchInner('wrCount', wrCountText());
};
async function wrReload() {
  WRITING = await api('/api/writing/projects');
  patchInner('wrSeg', wrSegHtml());
  patchInner('wrRows', wrRowsHtml());
  patchInner('wrCount', wrCountText());
}
window.wrCreate = async function (btn) {
  const g = id => (document.getElementById(id) || {}).value || '';
  const case_name = g('wrCase').trim(), doc_type = g('wrType').trim(), title = g('wrTitle').trim();
  if (!case_name || !title) { alert('案件夹名与项目标题都要填。'); return; }
  const rst = btnBusy(btn, '新建中…');
  try {
    const r = await post('/api/writing/project', {case: case_name, doc_type: doc_type,
                                                  title: title, confirm: true});
    toast('已建项目「' + r.project.title + '」（' + r.project.status + '）');
    const t = document.getElementById('wrTitle'); if (t) t.value = '';
    await wrReload();
  } catch (e) { alert('新建失败：' + e.message); }
  finally { rst(); }
};
window.wrSetStatus = async function (id, to, btn) {
  const rst = btnBusy(btn, '处理中…');
  try {
    const r = await post('/api/writing/status', {id: id, to: to, confirm: true});
    toast('状态已改为「' + r.project.status + '」');
    await wrReload();
  } catch (e) { alert('改状态失败：' + e.message); }
  finally { rst(); }
};
window.wrDetail = async function (id) {
  showModalLoading('写作项目', '加载版本清单…');
  let d;
  try { d = await api('/api/writing/versions?project_id=' + id); }
  catch (e) { showModal('写作项目', `<div class="red">读取失败：${esc(e.message)}</div>`); return; }
  if (d.error) { showModal('写作项目', `<div class="red">${esc(d.error)}</div>`); return; }
  WR_DETAIL = d;
  showModal('写作项目：' + d.project.title, wrDetailHtml(),
    {footer: `<button class="act ghost mini" onclick="closeModal()">关闭</button>`});
};
function wrDetailHtml() {
  const d = WR_DETAIL || {};
  const p = d.project || {};
  return `<div class="hint">案卷落点：<code>${esc(d.case || '')}\\${esc(d.doc_dir || '')}\\${esc(p.title || '')}\\</code>
    （目录不存在则新建＝只新增；同名自动避让，绝不覆盖）</div>
  <table style="margin-top:8px"><tr><th style="width:56px">版本</th><th style="width:132px">时间</th><th>文件名</th><th>备注</th><th style="width:80px"></th></tr>
    <tbody>${wrVerRowsHtml(d)}</tbody></table>
  <div class="editbox" style="margin-top:12px">
    <b>传新版本</b><span class="hint" style="margin-left:8px">（文件复制入卷：vNN-日期-原名）</span>
    <div class="row" style="margin:8px 0">
      <input type="file" id="wrFile" style="max-width:300px">
      <input id="wrNote" placeholder="备注（可空）" style="width:180px">
      <button class="act ghost" onclick="wrUpload(false)">传为新版本（先预览）</button>
    </div>
    <div id="wrUpOut" class="hint" style="white-space:pre-wrap"></div>
  </div>`;
}
function wrVerRowsHtml(d) {
  if (!d.items || !d.items.length) {
    return '<tr><td colspan="5" class="dim">还没有版本——用下面「传新版本」复制入卷第一版。</td></tr>';
  }
  return d.items.map(v => `<tr>
    <td class="n">v${String(v.version_no).padStart(2, '0')}</td>
    <td class="dim nw">${esc(String(v.created_at || '').slice(0, 16))}</td>
    <td>${esc(v.filename)}</td>
    <td class="dim">${esc(v.note || '')}</td>
    <td class="nw"><button class="act ghost mini" onclick="wrOpenVer(${v.id}, this)">打开</button></td></tr>`).join('');
}
function wrFileB64(file) {
  return new Promise((res, rej) => {
    const rd = new FileReader();
    rd.onload = () => res(String(rd.result).split(',', 2)[1] || '');
    rd.onerror = () => rej(new Error('读取文件失败'));
    rd.readAsDataURL(file);
  });
}
window.wrUpload = async function (doConfirm) {
  const pid = (WR_DETAIL && WR_DETAIL.project && WR_DETAIL.project.id) || 0;
  const note = ((document.getElementById('wrNote') || {}).value || '').trim();
  const out = document.getElementById('wrUpOut');
  if (!pid) return;
  if (!doConfirm) {
    const f = ((document.getElementById('wrFile') || {}).files || [])[0];
    if (!f) { if (out) out.innerHTML = '<span class="warn">先在左侧选一个文件（.docx/.doc/.pdf/.md/.txt）。</span>'; return; }
    try {
      const data = await wrFileB64(f);
      const r = await post('/api/writing/version', {project_id: pid, filename: f.name,
                                                    note: note, data: data, confirm: false});
      WR_PENDING = {filename: f.name, note: note, data: data};
      out.innerHTML = `<span class="ok">预览 OK：将落 <code>${esc(r.plan.rel_path)}</code>（${r.size_kb} KB ｜ v${r.plan.version_no}）</span>\n`
        + `<button class="act" onclick="wrUpload(true)">确认写入（复制入卷）</button>
           <button class="act ghost" onclick="wrUploadCancel()">取消</button>`;
    } catch (e) { if (out) out.innerHTML = `<span class="warn">预览未通过：${esc(e.message)}</span>`; }
    return;
  }
  if (!WR_PENDING) { if (out) out.innerHTML = '<span class="warn">先点「传为新版本（先预览）」再确认。</span>'; return; }
  try {
    const r = await post('/api/writing/version', {project_id: pid, filename: WR_PENDING.filename,
                                                  note: WR_PENDING.note, data: WR_PENDING.data, confirm: true});
    WR_PENDING = null;
    toast('已入卷：' + r.version.rel_path);
    await wrDetail(pid);
    await wrReload();
  } catch (e) { if (out) out.innerHTML = `<span class="warn">写入失败：${esc(e.message)}</span>`; }
};
window.wrUploadCancel = function () {
  WR_PENDING = null;
  const out = document.getElementById('wrUpOut');
  if (out) out.innerHTML = '<span class="dim">已取消（未写任何文件）。</span>';
};
window.wrOpenVer = async function (vid, btn) {
  const d = WR_DETAIL || {};
  const v = (d.items || []).find(x => x.id === vid);
  if (!v) return;
  const ext = String(v.ext || '').toLowerCase();
  if (ext === '.md' || ext === '.txt') {
    showModalLoading('打开版本', '读取中…');
    try {
      const r = await api('/api/writing/view?case=' + encodeURIComponent(d.case)
        + '&rel=' + encodeURIComponent(v.rel_path));
      if (r.error) { showModal('打开版本', `<div class="red">${esc(r.error)}</div>`); return; }
      showModal('版本 · ' + v.filename,
        `<pre style="max-height:62vh;overflow:auto;white-space:pre-wrap;font-size:12.5px;line-height:1.65">${esc(r.text || '')}</pre>
         <div class="hint">${esc(v.rel_path)} · ${r.size_kb} KB</div>`);
    } catch (e) { showModal('打开版本', `<div class="red">读取失败：${esc(e.message)}</div>`); }
    return;
  }
  if (['.pdf', '.jpg', '.jpeg', '.png', '.bmp', '.webp'].includes(ext)) {
    const url = '/api/letter/file_view?path=' + encodeURIComponent(v.abs_path) + '&_=' + Date.now();
    const body = ext === '.pdf'
      ? `<iframe src="${url}" title="${esc(v.filename)}" style="width:100%;height:74vh;border:1px solid var(--rule);border-radius:8px;background:#fff"></iframe>`
      : `<div style="text-align:center;background:var(--rule-soft);padding:10px;border-radius:8px"><img src="${url}" alt="${esc(v.filename)}" style="max-width:100%;max-height:74vh;border-radius:6px"></div>`;
    showModal('预览：' + v.filename, body,
      {footer: `<button class="act ghost mini" onclick="closeModal()">关闭</button>`});
    return;
  }
  // docx/doc 等页内看不了 → 用系统默认程序打开（走既有 open_file 安全闸）
  const rst = btnBusy(btn, '打开中…');
  try {
    const r = await post('/api/letter/open_file', {path: v.abs_path});
    if (r.error) alert('打开失败：' + r.error); else toast('已用系统程序打开：' + v.filename);
  } catch (e) { alert('打开失败：' + e.message); }
  finally { rst(); }
};

/* ---------------- 入库 ---------------- */
async function viewIntake() {
  const c = CASES || (CASES = await api('/api/cases'));
  const opts = c.cases.map(x => `<option>${esc(x.name)}</option>`).join('')
    + c.cases.map(x => (x.subcases || []).map(sn =>
        `<option value="${esc(x.name)}/${esc(sn)}">↳ ${esc(sn)}（${esc(x.name)}）</option>`
      ).join('')).join('');   // 批 71：系列案子案件夹也可选（下载/入库直达）
  return `<div class="card"><h2>收件箱入库（扫 00-收件箱 → 归类建议 → 移动 + 留痕）</h2>
  <div class="row"><select id="i_case" style="min-width:340px">${opts}</select>
    <button class="act ghost" onclick="doIntake(false)">扫描（dry-run）</button>
    <button class="act" onclick="doIntake(true)">确认归位</button></div>
  <div class="hint">同名不同内容 → 冲突跳过；同内容 → 判重复不覆盖；需人工项原地保留。改名/移动全部记入 intake-log.md。</div>
  <div class="hint">📷 <b>认不出的扫描件/照片</b>：在扫描结果里点「本地识别」——<b>只在本机跑（零外发）</b>，
    读正文后给归位建议；识别文本存案卷 <code>08-过程文件\_ocr\</code>，<b>原文件不改名、不移动</b>，第二次直接读缓存。</div>
  <div id="intakeOut"></div><div id="ocrOut"></div></div>
  <div class="card"><h2>法院短信 → 送达文书入卷（一张网直连）</h2>
  <div class="hint">粘贴「人民法院在线服务（一张网）」<b>送达短信全文</b> → 解析 → 文书直接下载并<b>按类型直接归位</b>（判决书→04-法院文件 等；认不准的落 <code>00-收件箱</code> 再走上面的扫描）。全程只从法院官方平台<b>拉取</b>，不上传任何本地数据；<b>省级平台短信（不带 zxfw.court.gov.cn 链接）不支持</b>。</div>
  <div class="row"><select id="cs_case" style="min-width:340px">${opts}<option value="__stash__">📥 短信文书暂存（案件还没建时）</option></select></div>
  <textarea id="cs_sms" rows="3" style="width:100%;margin:6px 0;resize:vertical" placeholder="粘贴法院短信全文，例：【XX人民法院】…向您发送了(2026)闽0000民初0000号案件相关文书…点击链接查阅：https://zxfw.court.gov.cn/zxfw/…?qdbh=…&sdbh=…&sdsin=…"></textarea>
  <div class="row">
    <button class="act ghost" onclick="csPreview()">① 解析短信并拉文书</button>
    <button class="act" onclick="csDownload(true)">② 下载到收件箱</button>
  </div>
  <div id="csOut"></div></div>`;
}
/* 批 61：法院短信送达文书（解析 → 拉列表 → 下载入收件箱；只拉取不上传）
   接口形态参照 CaseBoard 0.4.18 court_sms（一张网纯 API 路径）。
   渲染拆三个纯函数（csPreviewHtml / csPlanHtml / csDoneHtml）供 smoke 直接断言。 */
let CS_STATE = null;   // 本页最近一次解析结果（parse.link 供下载）
function csPreviewHtml(r) {
  const p = r.parse || {};
  let h = `<div style="margin-top:6px"><div>法院：<b>${esc(p.court || '—')}</b> ｜ 案号：<code>${esc(p.case_no || '—')}</code></div>`;
  h += p.link ? '<div class="dim">送达链接参数 qdbh / sdbh / sdsin 三齐 ✓</div>'
              : '<div class="warn">没有识别到一张网送达链接</div>';
  if (r.match) {
    h += r.match.suggest
      ? `<div class="ok">自动匹配案件：<b>${esc(r.match.suggest)}</b>（案号命中台账）</div>`
      : `<div class="warn">${esc(r.match.note || '未自动匹配——请手动选案件')}</div>`;
  }
  if (r.docs && r.docs.length) {
    h += `<div style="margin-top:4px">送达文书（${r.docs.length} 份）：</div><table><tr><th>文书</th><th style="width:80px">格式</th><th style="width:120px">拟归入</th></tr>` +
      r.docs.map(d => `<tr><td>${esc(d.name)}</td><td class="dim">${esc(d.ext)}</td>` +
        `<td class="${d.dir ? 'ok' : 'dim'}">${esc(d.dir || '收件箱待核对')}</td></tr>`).join('') + '</table>';
  } else {
    h += `<div class="warn">${esc(r.fetch_note || '没拉到文书（链接可能已失效）')}</div>`;
  }
  return h + '</div>';
}
function csPlanHtml(r) {
  if (r.stash) {
    return `<div>将下载 <b>${r.count || 0}</b> 份文书 → <b>短信文书暂存</b>（<code>接收到的文件\短信文书\</code>${r.inbox_exists ? '' : '（将新建）'}）</div>
    <div class="dim">重名自动避让 -2/-3；仅从法院平台拉取，不上传数据。案件建好后可在「散件」页入库到案件。</div>`;
  }
  // 批 72：下载即归位——方案里直接列出每份的拟归目录；批 74：分组夹目标提醒（防误落）
  return (r.warn ? `<div class="warn">⚠️ ${esc(r.warn)}</div>` : '') +
    `<div>将下载 <b>${r.count || 0}</b> 份文书 → 所选案件（<b>下载后按类型直接归位</b>；认不准的落 <code>00-收件箱</code> 待扫描核对）</div>` +
    ((r.docs || []).length
      ? '<table><tr><th>文书</th><th style="width:140px">拟归入</th></tr>' +
        r.docs.map(d => `<tr><td>${esc(d.name)}</td><td class="${d.dir ? 'ok' : 'dim'}">${esc(d.dir || '收件箱待核对')}</td></tr>`).join('') + '</table>'
      : '') +
    '<div class="dim">重名自动避让；仅从法院平台拉取，不上传数据。</div>';
}
function csDoneHtml(r) {
  if (r.stash) {
    let h = `<div class="ok">已下载 ${(r.downloaded || []).length} 份 → <b>短信文书暂存</b>（<code>接收到的文件\短信文书\</code>）</div>`;
    h += (r.downloaded || []).map(d => `<div class="dim">· ${esc(d.name)}（${d.size_kb} KB）</div>`).join('');
    if ((r.failed || []).length) {
      h += '<div class="warn">跳过：' + r.failed.map(f => esc(f.name + '（' + (f.error || '') + '）')).join('；') + '</div>';
    }
    h += `<div class="hint">案件建好后到「散件」页可一键入库到案件；不归咱们办的，也可以直接拷给带教——
      <button class="act ghost mini" onclick="csOpenStash()">📂 打开暂存夹</button></div>`;
    return h;
  }
  // 批 72：下载即归位——已归位 / 留在收件箱 分开报；批 74：分组夹目标提醒
  let h = `<div class="ok">已下载 ${(r.downloaded || []).length} 份</div>`;
  if (r.warn) h += `<div class="warn">⚠️ ${esc(r.warn)}</div>`;
  h += (r.filed || []).map(x => `<div class="dim">· ${esc(x.name)} → 已直接归位 <b>${esc(x.dir)}</b>` +
    (x.newname && x.newname !== x.name ? `（改名 ${esc(x.newname)}）` : '') + '</div>').join('');
  h += (r.left || []).map(x => `<div class="warn">· ${esc(x.name)}：${esc(x.reason)}（留在 <code>00-收件箱</code>）</div>`).join('');
  if ((r.failed || []).length) {
    h += '<div class="warn">跳过：' + r.failed.map(f => esc(f.name + '（' + (f.error || '') + '）')).join('；') + '</div>';
  }
  h += '<div class="hint">直接归位＝与「扫描→确认归位」同一套分类规则（判决书→04-法院文件 等）；留痕已记入本夹 intake-log.md。</div>';
  if ((r.opened || []).length) {
    h += `<div class="hint">📂 已自动打开：${r.opened.map(esc).join('、')}</div>`;   // 批 75
  }
  return h;
}
window.csOpenStash = async function() {
  try {
    const r = await post('/api/court_sms/open_stash', {});
    if (r.ok) toast('已打开：' + (r.opened || '短信文书暂存'));
    else alert('打开失败：' + (r.error || '未知'));
  } catch (e) { alert('打开失败：' + failHint(e).why); }
};
window.csPreview = async function() {
  const out = $('#csOut');
  const el = $('#cs_sms');
  const sms = ((el && el.value) || '').trim();
  if (!sms) { out.innerHTML = '<div class="warn">先粘贴法院短信全文。</div>'; return; }
  out.innerHTML = '<div class="dim">解析中（拉文书列表可能几秒）…</div>';
  try {
    const r = await post('/api/court_sms/preview', {sms});
    CS_STATE = r;
    out.innerHTML = csPreviewHtml(r);
    if (r.match && r.match.suggest) {
      const sel = $('#cs_case');
      const s = r.match.suggest;
      const hit = sel && Array.from(sel.options || []).find(
        o => o.value === s || o.value.endsWith('/' + s));   // 批 71：嵌套「总夹/子夹」也预选
      if (hit) sel.value = hit.value;
    }
  } catch (e) { out.innerHTML = `<div class="red">失败：${esc(e.message)}</div>`; }
};
window.csDownload = async function(confirm) {
  const out = $('#csOut');
  if (!CS_STATE || !CS_STATE.parse || !CS_STATE.parse.link) {
    out.innerHTML = '<div class="warn">先点「① 解析短信并拉文书」。</div>'; return;
  }
  out.innerHTML = `<div class="dim">${confirm ? '下载中…' : '拉取清单…'}</div>`;
  try {
    const sel = $('#cs_case').value;
    const body = {link: CS_STATE.parse.link, confirm: !!confirm};
    if (sel === '__stash__') body.stash = true;           // 批 67：案件没建 → 落短信文书暂存
    else body.case = sel;
    const r = await post('/api/court_sms/download', body);
    if (r.error) { out.innerHTML = `<div class="red">失败：${esc(r.error)}</div>`; return; }
    out.innerHTML = r.dry_run ? csPlanHtml(r) : csDoneHtml(r);
  } catch (e) { out.innerHTML = `<div class="red">失败：${esc(e.message)}</div>`; }
};
window.doIntake = async function(confirm) {
  const cs = $('#i_case').value, out = $('#intakeOut');
  out.innerHTML = '<div class="dim">处理中…</div>';
  try {
    const r = await post('/api/intake', {case: cs, confirm});
    if (r.dry_run) {
      if (!r.items.length) return out.innerHTML = '<div class="dim">收件箱为空或该案无 00-收件箱。</div>';
      out.innerHTML = '<table><tr><th></th><th>文件</th><th>归档目录</th><th>建议文件名</th><th>状态</th><th>理由</th><th>识别</th></tr>' +
        r.items.map(i => `<tr><td>${i.status==='建议'?'':'❗'}</td><td>${esc(i.name)}</td>
          <td>${esc(i.dir||'（原位）')}</td><td>${esc(i.newname||'')}</td>
          <td>${esc(i.status)}</td><td class="dim">${esc(i.reason)}</td>
          <td>${i.ocr ? `<button class="act ghost mini" onclick="ocrRun('${esc(i.name)}')">本地识别</button>`
                      : '<span class="dim">Word 文档无需 OCR</span>'}</td></tr>`).join('') + '</table>';
    } else {
      out.innerHTML = `<div class="ok">已归位 ${r.moved} 个</div>` +
        (r.skipped.length ? `<div class="warn">跳过：${r.skipped.map(esc).join('；')}</div>` : '');
    }
  } catch (e) { out.innerHTML = `<div class="red">失败：${esc(e.message)}</div>`; }
};

/* 批 66：失败说人话——把错误翻成「一句话原因 + 怎么办」 */
function failHint(err) {
  const msg = String((err && (err.message || err)) || '未知错误').trim();
  const m = msg.toLowerCase();
  if (/外部服务|8788|econnrefused|failed to fetch|无法连接|network|connection|通道/.test(m))
    return {why: '本地 AI 服务（外部服务 8788）没在运行，或连接被挡', todo: '双击桌面「外部服务」图标启动（WB 代理需开着）后重试；仍不行就刷新本页'};
  if (/timeout|超时|abort/.test(m))
    return {why: '等太久了——服务可能正忙，或这次任务太重', todo: '稍等十几秒再点一次；连续超时先看外部服务与工作台是否在跑'};
  if (/500|internal|内部/.test(m))
    return {why: '工作台服务内部出错了', todo: '看 app.out.log 尾部日志；或重启工作台（双击图标）后重试'};
  if (/404|不存在|未找到|not found/.test(m))
    return {why: '找不到对应的文件或接口', todo: '刷新页面重试；文件类的先确认文件还在原位置（没被改名/移走）'};
  return {why: msg.slice(0, 120) || '未知错误', todo: '先重试一次；连续失败就把这句话截图发给助手看看'};
}
window.failHint = failHint;

/* 批 66：失败说人话的 HTML 组装（一行原因 + 一行怎么办） */
function failHintHtml(err) {
  const f = failHint(err);
  return `<div class="warn">⚠️ ${esc(f.why)}<br><span class="dim">怎么办：${esc(f.todo)}</span></div>`;
}

/* 批 66：归位建议渲染（独立块——修字保存后局部刷新用） */
function ocrSuggestHtml(s) {
  s = s || {};
  let h = s.dir
    ? `<div class="ok">建议归入：<b>${esc(s.dir)}</b>（依据：${esc(s.from)}）—— ${esc(s.reason)}</div>`
    : `<div class="warn">还是认不出：${esc(s.reason || '正文没有可用关键词')}（保持原位待人工判定）</div>`;
  if (s.scores && s.scores.length) {
    h += '<table style="margin-top:6px"><tr><th>候选目录</th><th>分</th><th>命中词</th></tr>' +
      s.scores.map(x => `<tr><td>${esc(x[0])}</td><td>${x[1]}</td><td class="dim">${esc((x[2]||[]).join('、'))}</td></tr>`).join('') + '</table>';
  }
  return h;
}
window.ocrSuggestHtml = ocrSuggestHtml;

/* 批 66：对照原图修字（识别稿人工补正；原图走 file_view 直出，本地渲染） */
function ocrEditHtml(r, name) {
  if (!r.src) return '';
  const ext = String(r.src).split('.').pop().toLowerCase();
  const viewUrl = '/api/letter/file_view?path=' + encodeURIComponent(r.src);
  const preview = (ext === 'pdf')
    ? `<iframe src="${viewUrl}" style="width:100%;height:430px;border:1px solid var(--rule);border-radius:6px"></iframe>`
    : `<img src="${viewUrl}" alt="原图" style="max-width:100%;max-height:430px;border:1px solid var(--rule);border-radius:6px">`;
  const nm = encodeURIComponent(name);
  return `<div id="ocrEditBox" style="display:none;margin-top:8px">
    <div class="hint">对照原图直接改识别文本——保存后「建议归入」会按修正文本重新判断（旧版存 <code>.md.bak</code>，人工劳动不丢）。</div>
    <div class="row" style="gap:10px;align-items:flex-start;margin-top:6px">
      <div style="flex:1;min-width:300px">${preview}</div>
      <div style="flex:1;min-width:300px">
        <textarea id="ocrEditTa" class="editbox" style="width:100%;height:430px;white-space:pre-wrap" placeholder="点上方按钮载入识别全文…"></textarea>
        <div class="row" style="margin-top:6px">
          <button class="act" onclick="ocrSaveText(decodeURIComponent('${nm}'))">保存修正</button>
          <button class="act ghost" onclick="document.getElementById('ocrEditBox').style.display='none'">收起</button>
        </div>
      </div>
    </div></div>`;
}

window.ocrEditToggle = async function(name) {
  const box = document.getElementById('ocrEditBox');
  if (!box) return;
  if (box.style.display !== 'none') { box.style.display = 'none'; return; }   // 已显示 → 收起
  box.style.display = '';
  const ta = document.getElementById('ocrEditTa');
  if (ta && ta.value.trim()) return;                    // 已有内容不重复载入
  if (ta) ta.value = '（载入中…）';
  try {
    const cs = $('#i_case').value;
    const d = await api('/api/ocr/text?case=' + encodeURIComponent(cs) + '&name=' + encodeURIComponent(name));
    if (ta) ta.value = d.text || '';
  } catch (e) {
    if (ta) ta.value = '';
    alert('载入识别全文失败：' + e.message);
  }
};

window.ocrSaveText = async function(name) {
  const ta = document.getElementById('ocrEditTa');
  const cs = $('#i_case').value;
  if (!ta || !ta.value.trim()) { alert('修正文本不能为空'); return; }
  try {
    const r = await post('/api/ocr/save_text', {case: cs, name: name, text: ta.value, confirm: true});
    toast('已保存修正（' + r.chars + ' 字）');
    const sb = document.getElementById('ocrSuggestBox');
    if (sb && r.suggest) sb.innerHTML = ocrSuggestHtml(r.suggest);
  } catch (e) { alert('保存失败：' + e.message); }
};

/* 批 65：识别结果渲染（单次 / 分段共用） */
function ocrResultHtml(r, name) {
  let h = `<div class="card" style="margin-top:10px"><h3 style="margin:0 0 4px">识别结果：${esc(name)}</h3>
    <div class="hint">${r.cached ? '读的缓存（没重复跑）' : '新跑'}｜识别 ${r.chars} 字` +
    (r.seconds ? `｜耗时 ${r.seconds}s` : '') +
    `｜文本存案卷 <code>08-过程文件\_ocr\</code>（只新增，原文件未动）</div>`;
  if (r.note) h += `<div class="hint">${esc(r.note)}</div>`;
  h += `<div id="ocrSuggestBox">${ocrSuggestHtml(r.suggest)}</div>`;
  h += `<details style="margin-top:6px"><summary class="dim">看识别正文前 600 字</summary><pre>${esc(r.head || '')}</pre></details>`;
  h += `<div class="row" style="margin-top:6px">` +
    (r.src ? `<button class="act ghost mini" onclick="ocrEditToggle(decodeURIComponent('${encodeURIComponent(name)}'))">🔍 对照原图修字</button>` : '') +
    `<button class="act ghost mini" onclick="ocrRun(decodeURIComponent('${encodeURIComponent(name)}'), true)">强制重新识别</button></div>`;
  h += ocrEditHtml(r, name);
  h += '</div>';
  return h;
}
window.ocrResultHtml = ocrResultHtml;

window.ocrRun = async function(name, force) {
  if (window.OCR_RUNNING) { alert('已有识别在进行中，请等它结束（或刷新页面）'); return; }
  const cs = $('#i_case').value, out = $('#ocrOut');
  out.innerHTML = `<div class="dim">识别中…「${esc(name)}」（本机 OCR；超长 PDF 会自动分段跑）</div>`;
  window.OCR_RUNNING = true;
  try {
    // 批 65：先探页数 —— 超长 PDF 自动走分段（防卡死/超时）；超单次大小上限也走分段
    let pl = null;
    try { pl = await api('/api/ocr/plan?case=' + encodeURIComponent(cs) + '&name=' + encodeURIComponent(name)); } catch (e) { /* 探测失败就按单次走 */ }
    if (pl && ((pl.ok && pl.split) || pl.split_hint)) { window.OCR_RUNNING = false; return await window.ocrRunSplit(name, force); }
    const r = await post('/api/ocr/run', {case: cs, name, confirm: true, force: !!force});
    out.innerHTML = ocrResultHtml(r, name);
  } catch (e) { out.innerHTML = failHintHtml(e); }
  finally { window.OCR_RUNNING = false; }
};

/* 批 65：大 PDF 分段识别 —— 前端循环调用，每段分钟级；段级断点续跑 */
window.ocrRunSplit = async function(name, force) {
  if (window.OCR_RUNNING) { alert('已有识别在进行中，请等它结束（或刷新页面）'); return; }
  const cs = $('#i_case').value, out = $('#ocrOut');
  window.OCR_RUNNING = true;
  try {
    let seg = null, forceOnce = !!force, last = null;
    while (true) {
      out.innerHTML = `<div class="dim">分段识别中…「${esc(name)}」` +
        (last ? `（已完成第 ${last.seg}/${last.segs_total} 段 · 已识 ${last.done_pages}/${last.total_pages} 页）` : '（准备中）') +
        `<br>超长卷宗按段识别，每段可能几分钟；<b>中途关闭页面也没关系</b>——回来再点一下会从断点继续</div>`;
      const r = await post('/api/ocr/split-run', {case: cs, name, seg, confirm: true, force: forceOnce});
      forceOnce = false;
      if (!r.ok) {
        out.innerHTML = `<div class="red">分段识别第 ${r.seg || '?'} 段失败：${esc(r.error || '未知错误')}</div>` +
          `<div class="hint">已完成的页不会重跑（断点续跑）。稍后可再试，或检查文件是否被占用。</div>` +
          `<div class="row" style="margin-top:6px"><button class="act"
            onclick="ocrRunSplit(decodeURIComponent('${encodeURIComponent(name)}'), false)">继续识别（从断点续跑）</button></div>`;
        return;
      }
      if (r.done) { out.innerHTML = ocrResultHtml(r, name); return; }
      last = r;
      seg = (r.done_pages >= (r.seg_range ? r.seg_range[1] : 0)) ? r.seg + 1 : r.seg;
      if (r.seg >= r.segs_total) seg = null;   // 兜底（正常情况下上一行已推进到尾段）
    }
  } catch (e) { out.innerHTML = failHintHtml(e); }
  finally { window.OCR_RUNNING = false; }
};

/* ---------------- 费用 ---------------- */
async function viewFees() {
  const f = await api('/api/fees');
  FEE_DATA = f;
  INV_CTX = await api('/api/invoice/overview');        // 批 30：发票页上下文（只读）
  const s = f.summary;
  let h = '<div class="grid">' + [
      ['应收基础', s.应收基础, '', '台账派生的应收基础律师费合计（元）'],
      ['已收到', s.已收到, '', '已收款合计（元）'],
      ['已开票', s.已开票, '', '已开票金额合计（元）'],
      ['剩余尾款', s.剩余尾款, s.剩余尾款 > 0 ? 'red' : 'ok', '还没收到的尾款合计（元）；红色＝有待收'],
      ['费用缺口', s.发票缺口, s.发票缺口 > 0 ? 'warn' : 'ok', '费用信息不全的案件数（发票号空缺 / 已收 > 已开票）'],
      ['案件数', s.案件数, '', '参与统计的案件数']].map(([k, v, c, tip]) =>
      `<div class="kpi" title="${esc(tip)}"><b class="${c}">${num(v)}</b><span>${k}</span></div>`).join('') + '</div>';
  h += `<div class="card"><h2>上传发票 → 归案（本机识别，零外发）</h2>
    <div class="row"><input type="file" id="invFiles" multiple
        accept=".pdf,.png,.jpg,.jpeg,.bmp,.tif,.tiff,.webp,.ofd">
      <button class="act" onclick="invUpload()">上传并识别</button>
      <button class="act ghost" onclick="invLib()">看公共发票库</button></div>
    <div class="hint">一次可多张（PDF / 图片 / OFD，单次上限 ${INV_CTX.max_mb || 40} MB）；识别<b>全部在本机做</b>
      （与「收件箱入库」的「本地识别」同一个引擎）。识别完给你「建议案件 + 建议文件名」，<b>你点确认才真的挪文件</b>。</div>
    <div class="hint">落点＝<b>案卷一份</b>（<code>05-费用票据</code>，正本，跟案走、归档时随夹迁移）＋
      <b>公共区一份</b>（<code>${esc(INV_CTX.lib_root || '')}\\年月</code>，副本，便于按月盘点）。
      <b>不写台账</b>（发票号/金额仍由你手填）；原件内容不改、重名自动加 <code>-2</code>。</div>
    <div id="invOut"></div>
    <div id="invLibOut"></div></div>`;
  h += `<div class="card"><h2>逐案费用（台账 O–W 列派生，按剩余尾款降序）</h2>
    <div class="hint">缺口 = 发票号空缺 / 已收 &gt; 已开票。
      <span class="red">红字</span>＝已经出问题（欠款没到 / 发票号空缺）；
      <span class="warn">黄字</span>＝需要跟进（费用信息还没补全）。台账仍为唯一事实源（本页只读）。</div>
    <div class="row" style="margin:10px 0"><span class="seg" id="feeSeg">${feeSegHtml()}</span>
      <span class="dim" style="font-size:12px" id="feeCount">${feeCountText()}</span></div>
    <table><thead><tr><th>案件</th><th>状态</th><th>标的额</th><th class="r">基础律师费</th>
      <th class="r">已收到</th><th class="r">开票金额</th><th>发票号</th><th class="r">剩余尾款</th><th>缺口</th></tr></thead>
      <tbody id="feeRows">${feeRowsHtml()}</tbody></table></div>`;
  /* ⚠️ 差旅发票卡片必须排在 </table> 之后：之前误写在 <table> 内部，
     属非法嵌套 —— 浏览器 foster-parent 把它挪到表格外面，顺序会莫名跑到表格前
     （2026-09-14 目检发现）。 */
  h += `<div class="card" style="margin-top:10px"><h3 style="margin:0 0 4px">差旅发票（按文件名线索挂到案件）</h3>
    <div class="hint">方案 §5.1：发票<b>维持按月存放不动</b>，这里只做<b>索引视图</b> —— 从文件名猜对应案，认不出就明说（纯发票号/平台名认不出是正常的）。</div>
    <div class="row" style="margin-top:8px"><button class="act ghost" onclick="travelOpen()">看看 / 刷新</button></div>
    <div id="travelOut"></div></div>`;
  return h;
}

function feeRowsHtml() {
  const rows = (FEE_DATA && FEE_DATA.rows) || [];
  const g = FEE_FILTER.gap;
  const frows = rows.filter(r => g === 'gap' ? (r['缺口'] || []).length > 0
    : g === 'due' ? (Number(r['剩余尾款']) || 0) > 0 : true);
  let h = frows.map(r => `<tr>
    <td class="case" title="委托人：${esc(r['委托人'] || '—')}">${clip(r['案件']||'', 230)}
      <span class="sub">${esc(r.sheet)} r${r.row}</span></td>
    <td>${r['状态'] ? `<span class="pill">${esc(r['状态'])}</span>` : '<span class="dim">—</span>'}</td>
    <td>${amtCell(r['标的额'])}</td><td class="r">${num(r['基础律师费'])}</td>
    <td class="r">${num(r['已收到'])}</td><td class="r">${num(r['开票金额'])}</td>
    <td class="${r['发票号']?'':'red'}">${esc(r['发票号']||'空缺')}</td>
    <td class="r ${r['剩余尾款']>0?'red':''}">${num(r['剩余尾款'])}</td>
    <td class="warn nw">${(r['缺口']||[]).map(esc).join('、')||'—'}</td></tr>`).join('');
  if (!frows.length) h = '<tr><td colspan="9" class="dim">没有符合筛选的案件。</td></tr>';
  return h;
}
function feeSegHtml() {
  return [['all', '全部'], ['gap', '有缺口'], ['due', '有尾款']].map(([k, t]) =>
    `<button class="${FEE_FILTER.gap === k ? 'on' : ''}" onclick="feeFilter('${k}')">${t}</button>`).join('');
}
function feeCountText() {
  const rows = (FEE_DATA && FEE_DATA.rows) || [];
  const g = FEE_FILTER.gap;
  const n = rows.filter(r => g === 'gap' ? (r['缺口'] || []).length > 0
    : g === 'due' ? (Number(r['剩余尾款']) || 0) > 0 : true).length;
  return `显示 ${n} / ${rows.length} 案`;
}
window.feeFilter = function (k) {
  FEE_FILTER.gap = k;
  patchInner('feeSeg', feeSegHtml());
  patchInner('feeRows', feeRowsHtml());
  patchInner('feeCount', feeCountText());
};

/* ---- 批 30：发票上传 → 归案（本机识别；案卷正本 + 公共库副本；不写台账） ---- */
let INV_CTX = null, INV_FILES = [];

window.invUpload = async function() {
  const out = $('#invOut'), inp = $('#invFiles');
  if (!inp || !inp.files || !inp.files.length) {
    out.innerHTML = '<div class="warn">先选发票文件（可多选），再点「上传并识别」。</div>'; return;
  }
  const fd = new FormData();
  for (const fl of inp.files) fd.append('files', fl, fl.name);
  out.innerHTML = `<div class="dim">上传并识别中…（本机识别 ${inp.files.length} 张，可能要几秒到几十秒，别关页面）</div>`;
  try {
    const r = await fetch('/api/invoice/upload', {method: 'POST', body: fd});
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
    INV_FILES = d.items || [];
    patchInner('invOut', invItemsHtml(d));
    inp.value = '';
  } catch (e) { out.innerHTML = `<div class="red">上传失败：${esc(e.message)}</div>`; }
};

function invItemsHtml(d) {
  const cases = (INV_CTX && INV_CTX.cases) || [];
  const items = d.items || [];
  let h = `<div class="hint">本批 ${items.length} 张，识别成功 ${d.ok_count || 0} 张｜暂存目录：<code>${esc(d.dir || '')}</code></div>`;
  h += '<table><tr><th>原文件名</th><th>识别结果</th><th>归到哪一案</th><th>归位后文件名</th><th>月份</th><th></th></tr>';
  h += items.map((it, i) => {
    const f = it.fields || {};
    const info = it.ok
      ? `<div>${esc(f['类型'] || '')}${f['金额'] ? ` <b>${esc(f['金额'])} 元</b>` : ''}</div>
         <div class="dim">${esc(f['发票号码'] || '（无号码）')} ${esc(f['开票日期'] || '')} ${esc(f['项目'] || '')}</div>
         <div class="dim">${esc(f['疑似我方'] || '')}${(f['去向线索'] || []).length ? ' ｜去向 ' + esc(f['去向线索'].join('、')) : ''}</div>
         ${(f['提示'] || []).map(x => `<div class="warn">${esc(x)}</div>`).join('')}`
      : `<div class="red">${esc(it.error || '识别失败')}</div>`;
    const opts = ['<option value="">（选一个案子）</option>'].concat(cases.map(c =>
      `<option value="${esc(c)}"${c === it.suggest_case ? ' selected' : ''}>${esc(c)}</option>`)).join('');
    return `<tr><td>${esc(it.orig_name)}<div class="dim">${it.kb || ''} KB</div></td>
      <td>${info}</td>
      <td><select id="invCase${i}" style="min-width:230px">${opts}</select>
        ${it.suggest_why ? `<div class="dim">建议依据：${esc(it.suggest_why)}</div>`
                         : '<div class="dim">发票正文认不出案子 → 请你选</div>'}</td>
      <td><input id="invName${i}" value="${esc(it.suggest_name || it.orig_name)}" style="min-width:250px"></td>
      <td><input id="invMonth${i}" value="${esc(it.month || '')}" style="width:88px"></td>
      <td>${it.ok ? `<button class="act" onclick="invFile(${i})">确认归位</button>`
                  : '<span class="dim">跳过</span>'}<div id="invR${i}"></div></td></tr>`;
  }).join('');
  return h + '</table><div class="hint">确认后：正本移进案卷 <code>05-费用票据</code>，并复制一份到公共区当月目录；同时记 intake-log 与发票索引。</div>';
}

window.invFile = async function(i) {
  const it = (INV_FILES || [])[i] || {};
  const box = $('#invR' + i);
  const caseName = ($('#invCase' + i) || {}).value || '';
  const newname = ($('#invName' + i) || {}).value || '';
  const month = ($('#invMonth' + i) || {}).value || '';
  if (!caseName) { box.innerHTML = '<div class="warn">先选案件</div>'; return; }
  box.innerHTML = '<div class="dim">归位中…</div>';
  try {
    const r = await post('/api/invoice/file', {staged: it.staged, case: caseName,
                                               newname, month, fields: it.fields, confirm: true});
    const d = r.done || {};
    box.innerHTML = `<div class="ok">已归位：${esc(d.name || '')}</div>
      <div class="dim">正本：${esc(d.main || '')}</div>
      <div class="dim">副本：${esc(d.copy || '')}</div>`;
  } catch (e) { box.innerHTML = `<div class="red">失败：${esc(e.message)}</div>`; }
};

window.invLib = async function() {
  const out = $('#invLibOut'); out.innerHTML = '<div class="dim">读公共发票库…</div>';
  try {
    INV_CTX = await api('/api/invoice/overview');
    const lib = INV_CTX.library || {};
    let h = `<div class="hint">公共发票库：<code>${esc(lib.root || '')}</code>　共 <b>${lib.total || 0}</b> 份副本</div>`;
    if (!lib.exists || !(lib.months || []).length) {
      h += '<div class="dim">还没有归档的发票（上传后点「确认归位」才会进这里）。</div>';
    } else {
      h += lib.months.map(m => `<div class="row"><b>${esc(m.month)}</b>
        <span class="pill">${m.count} 张</span>
        <span class="dim">${m.files.map(x => esc(x.name)).join('　')}</span></div>`).join('');
    }
    if ((lib.index_tail || []).length) {
      h += '<details><summary class="dim">索引尾部（正本↔副本对照）</summary><pre>' +
        esc(lib.index_tail.join('\n')) + '</pre></details>';
    }
    patchInner('invLibOut', h);
  } catch (e) { out.innerHTML = `<div class="red">失败：${esc(e.message)}</div>`; }
};

/* ---------------- 律师函（§4.4 业务线：台账表 + 文件系统夹） ---------------- */
let LETTER_ITEMS = null;                    // 当前页的台账「律师函」行（供一键建任务取数）
/* ============================ 散件页（批 36） ============================
   用户 2026-09-17 定调：`D:\案件\你的案卷根` 下「律师函 / 合同 / 差旅发票 / 所函 /
   介绍信 / 判决书 / 接收到的文件」这些横向目录是**故意这么放的**（方便找），
   不归拢、不搬家；其中一部分在个案里没有（非直接经手）。本页只做**索引视图**：
   汇总 + 搜索 + 疑似归属，**一个文件都不动**。
   搜索遵守既有纪律：本地过滤、只重绘 tbody 与计数、5 触发点（含中文输入法护栏）。 */
function scatterFiltered() {
  const d = SCATTER || {items: []};
  const q = SCATTER_Q.trim().toLowerCase();
  return (d.items || []).filter(x => {
    if (SCATTER_DIR !== 'all' && x.dir !== SCATTER_DIR) return false;
    if (SCATTER_STAR_ONLY && !x.starred) return false;
    if (!q) return true;
    const hay = [x.name, x.rel, x.dir, x.sub,
                 (x.cases || []).map(c => c['案件']).join(' ')].join(' ').toLowerCase();
    return hay.indexOf(q) >= 0;
  });
}
function scatterCountText() {
  const all = (SCATTER || {items: []}).items || [];
  return scatterFiltered().length + ' / ' + all.length;
}
function scatterRowsHtml() {
  const rows = scatterFiltered();
  if (!rows.length) {
    return '<tr><td colspan="8" class="dim">没有匹配的散件（换个词试试，或点「全部」）</td></tr>';
  }
  return rows.map(x => {
    let match;
    if (x.matched) {
      const amb = x.ambiguous ? ' <span class="warn">（多案并存，请人工确认）</span>' : '';
      match = `<span class="ok">${esc((x.cases[0] || {})['案件'] || '')}</span>${amb}`
        + `<div class="dim">${esc((x.cases[0] || {})['为什么'] || '')}</div>`;
    } else if (x.offhand) {
      match = `<span class="dim">非经手 / 已迁空</span>`;
    } else {
      match = `<span class="dim">${esc((x.why || '认不出').slice(0, 44))}</span>`;
    }
    /* 批 38：标记列 = 当前标记 + 一键选择（下拉 onchange 只传值，避免引号嵌套）
       标记只写工作台自建库；散件文件一个字节都不动 */
    const cur = (SCATTER_MARK || {})[x.rel] || null;
    const opts = ['—'].concat(SCATTER_TAGS);
    const curTag = cur ? (cur.tag || '关注') : '—';
    const markSel = `<select class="n" style="width:84px" data-rel="${esc(x.rel)}"`
      + ` onchange="scatterMarkSel(this)">`
      + opts.map(o => `<option value="${esc(o)}"${o === curTag ? ' selected' : ''}>${esc(o)}</option>`).join('')
      + `</select>`;
    const starTag = (cur ? `<span class="warn">★ ${esc(cur.tag || '关注')}</span>` : '<span class="dim">未标</span>')
      + `<div>${markSel}</div>`;
    // 批 39c：多案并存（ambiguous）→ 直接给「指定案件」按钮（点了必然被拦，别让用户白点）
    let actBtn;
    if (x.matched && x.ambiguous) {
      actBtn = `<button class="act mini" onclick="scatterIntake('${esc(x.rel)}')">指定案件</button>`;
    } else if (x.matched) {
      actBtn = `<button class="act ghost mini" onclick="scatterIntake('${esc(x.rel)}')">入库</button>`;
    } else {
      // 未自动认出的也允许人工指定（批 39c）
      actBtn = `<button class="act ghost mini" onclick="scatterIntake('${esc(x.rel)}')">指定案件</button>`;
    }
    return `<tr><td class="dim nw">${esc(x.dir)}</td>`
      + `<td>${esc(x.name)}${x.sub ? `<div class="dim">${esc(x.sub)}</div>` : ''}</td>`
      + `<td class="r n nw">${x.size_kb >= 1024 ? (x.size_kb / 1024).toFixed(1) + ' MB' : Math.round(x.size_kb) + ' KB'}</td>`
      + `<td class="dim n nw">${esc(x.mtime)}</td>`
      + `<td>${match}</td>`
      + `<td>${starTag}</td>`
      + `<td>${actBtn}</td></tr>`;
  }).join('');
}

/* ---- 批 38：关注/待处理标记（只写自建库，散件文件一个字都不动） ---- */
function scatterToggleStar() {
  SCATTER_STAR_ONLY = !SCATTER_STAR_ONLY;
  const b = document.getElementById('scOnlyStar');
  if (b) b.textContent = (SCATTER_STAR_ONLY ? '✓ ' : '') + '只看关注 / 待处理';
  patchScatter();
}
window.scatterToggleStar = scatterToggleStar;

/* 下拉选择器回调：value='—' = 取消标记 */
function scatterMarkSel(el) {
  const rel = el.getAttribute('data-rel') || '';
  const v = el.value || '';
  if (!rel) return;
  if (v === '—') { scatterToggleMark(rel, '', ''); return; }
  scatterToggleMark(rel, v, '');
}
window.scatterMarkSel = scatterMarkSel;

async function scatterToggleMark(rel, tag, note) {
  const cur = (SCATTER_MARK || {})[rel];
  const body = {rel: rel, note: note || ''};
  if (!tag || (cur && cur.tag === tag)) { body.clear = true; } else { body.tag = tag; }
  try {
    await post('/api/scatter/mark', body);
    await scatterReload();
    toast(body.clear ? '已取消标记' : ('已标为「' + tag + '」'));
  } catch (e) { toast('标记失败：' + (e.message || e)); }
}
window.scatterToggleMark = scatterToggleMark;

async function scatterReload() {
  SCATTER = await api('/api/scatter');
  SCATTER_MARK = (SCATTER && SCATTER.marks) || {};
  patchScatter();
}
window.scatterReload = scatterReload;

function scatterClose() {
  scatterOut('');
}
window.scatterClose = scatterClose;

function scatterOut(html) {
  const box = document.getElementById('scatterOut');
  if (box) box.innerHTML = html;
}
window.scatterOut = scatterOut;

/* ---- 批 38：入库（只移动不复制不删除；台账零写入；你点确认才动） ---- */
async function scatterIntake(rel) {
  if (!rel) return;
  try {
    const p = await api('/api/scatter/intake?rel=' + encodeURIComponent(rel));
    const plan = (p && p.plan) || {};
    let h = '<div class="card"><h2>入库预览（还没动文件）</h2>'
      + `<div class="hint">来源：<code>${esc(plan.src || '')}</code><br>`
      + `去向：<code>${esc(plan.dst || '')}</code><br>`
      + `认到案件：<b>${esc(plan.case || '')}</b>`
      + (plan.why ? `　<span class="dim">（依据：${esc(plan.why)}）</span>` : '') + '</div>';
    if (p.blocked) {
      h += `<div class="red">${esc(p.blocked)}</div>`;
      // 批 39c：自动认不出/多案并存时，给「人工指定案件」入口
      const cands = (plan.candidates || []);
      if (cands.length > 1) {
        h += `<div class="hint">工具认到 <b>${cands.length}</b> 个候选案件：<b>${cands.map(esc).join('、')}</b>`
          + `　<span class="dim">（分不清是哪个，需你来定）</span></div>`;
      } else if (cands.length === 1) {
        h += `<div class="hint">工具只认到：<b>${esc(cands[0])}</b>，但案卷里没找到对应夹</div>`;
      }
      h += '<div class="hint">你可以<b>自己指定归到哪个案件</b>：</div>'
        + '<input type="hidden" id="scRel2" value="' + esc(rel) + '">'
        + '<select id="scCase" class="n" style="width:100%;max-width:560px">'
        + '<option value="">— 选择案件 —</option></select>'
        + '<div class="row" style="margin-top:8px">'
        + '<button class="act" onclick="scatterIntakeManual()">按指定案件入库</button> '
        + '<button class="act ghost" onclick="scatterClose()">关闭</button></div></div>';
      scatterOut(h);
      scatterFillCases(rel);
      return;
    }
    h += '<div class="hint">这是<b>移动</b>（不是复制）：移动后散件目录里就不再有这份文件，'
      + '它会出现在该案卷的 <code>00-收件箱</code>，并写一行 `_散件入库.md` 留痕。<b>台账不动。</b></div>'
      + '<input type="hidden" id="scRel" value="' + esc(rel) + '">'
      + '<div class="row"><button class="act" onclick="scatterIntakeDo()">确认入库</button> '
      + '<button class="act ghost" onclick="scatterClose()">取消</button></div></div>';
    scatterOut(h);
  } catch (e) { toast('预览失败：' + (e.message || e)); }
}
window.scatterIntake = scatterIntake;

/* 批 39c：填「人工指定案件」下拉（候选来自 /api/scatter/cases） */
async function scatterFillCases(rel) {
  const sel = document.getElementById('scCase');
  if (!sel) return;
  try {
    const d = await api('/api/scatter/cases');
    const opts = (d && d.options) || [];
    sel.innerHTML = '<option value="">— 选择案件 —</option>'
      + opts.map(o => `<option value="${esc(o.case)}">${esc(o.case)}`
          + (o.src === 'ledger' ? '' : '（仅案卷）') + '</option>').join('');
  } catch (e) { toast('取候选案件失败：' + (e.message || e)); }
}

async function scatterIntakeManual() {
  const relEl = document.getElementById('scRel2');
  const sel = document.getElementById('scCase');
  const rel = (relEl && relEl.value) || '';
  const caseName = (sel && sel.value) || '';
  if (!rel) { toast('缺文件路径'); return; }
  if (!caseName) { toast('请先选择案件'); return; }
  try {
    // 先预览（dry-run）
    const p = await api('/api/scatter/intake?rel=' + encodeURIComponent(rel)
                        + '&case_name=' + encodeURIComponent(caseName));
    const plan = (p && p.plan) || {};
    if (p.blocked) { toast('不能入库：' + p.blocked); return; }
    if (!confirm('将把这份文件【移动】到：\n\n' + (plan.dst || '') +
                 '\n\n认到案件：' + (plan.case || '') +
                 '\n\n（只移动不删除；写 _散件入库.md 留痕；台账不动）\n\n确认？')) return;
    const r = await post('/api/scatter/intake', {rel: rel, case_name: caseName, confirm: true});
    scatterOut('<div class="card"><h2>已入库</h2><div class="hint">已移到：<code>'
      + esc((r && r.dst) || '') + '</code><br>案件：<b>' + esc((r && r.case) || '') + '</b></div>'
      + '<button class="act ghost" onclick="scatterClose()">关闭</button></div>');
    await scatterReload();
    toast('已按指定案件入库');
  } catch (e) { toast('入库失败：' + (e.message || e)); }
}
window.scatterIntakeManual = scatterIntakeManual;

async function scatterIntakeDo() {
  const el = document.getElementById('scRel');
  const rel = (el && el.value) || '';
  if (!rel) { toast('缺文件路径'); return; }
  try {
    const r = await post('/api/scatter/intake', {rel: rel, confirm: true});
    scatterOut('<div class="card"><h2>已入库</h2><div class="hint">已移到：<code>'
      + esc((r && r.dst) || '') + '</code></div>'
      + '<button class="act ghost" onclick="scatterClose()">关闭</button></div>');
    await scatterReload();
    toast('已入库到案卷收件箱');
  } catch (e) { toast('入库失败：' + (e.message || e)); }
}
window.scatterIntakeDo = scatterIntakeDo;

async function scatterBatchIntake() {
  try {
    const p = await api('/api/scatter/intake?batch=1');
    const rows = (p && p.plan) || [];
    if (!rows.length) { toast('没有可入库的散件（要先认到台账案件）'); return; }
    const okN = rows.filter(r => !r.blocked).length;
    let h = '<div class="card"><h2>批量入库预览（还没动任何文件）</h2>'
      + '<div class="hint">只列<b>已认到台账案件</b>的散件。确认后逐件<b>移动</b>到对应案卷的 '
      + '<code>00-收件箱</code>。<b>是移动不是复制；台账不动；认不出的一律不动。</b></div>'
      + '<table><tr><th>文件</th><th>认到案件</th><th>去向</th><th>状态</th></tr>'
      + rows.map(r => '<tr><td>' + esc(r.name) + '<div class="dim">' + esc(r.rel) + '</div></td>'
        + '<td>' + esc(r.case || '') + '</td>'
        + '<td class="dim">' + esc(r.dst || '') + '</td>'
        + '<td>' + (r.blocked ? '<span class="red">' + esc(r.blocked) + '</span>'
                              : '<span class="ok">可入库</span>') + '</td></tr>').join('')
      + '</table><div class="row" style="margin-top:8px">'
      + '<button class="act" onclick="scatterBatchIntakeDo()"' + (okN ? '' : ' disabled') + '>'
      + '确认全部入库（' + okN + ' 件）</button> '
      + '<button class="act ghost" onclick="scatterClose()">取消</button></div></div>';
    scatterOut(h);
  } catch (e) { toast('预览失败：' + (e.message || e)); }
}
window.scatterBatchIntake = scatterBatchIntake;

async function scatterBatchIntakeDo() {
  try {
    const r = await post('/api/scatter/intake', {batch: 1, confirm: true});
    const ok = (r && r.done) || [];
    const bad = (r && r.failed) || [];
    scatterOut('<div class="card"><h2>批量入库完成</h2>'
      + '<div class="hint">成功 <b>' + ok.length + '</b> 件，失败 ' + bad.length + ' 件。</div>'
      + (bad.length ? '<table><tr><th>文件</th><th>原因</th></tr>'
        + bad.map(x => '<tr><td>' + esc(x.name || x.rel) + '</td><td class="red">'
                       + esc(x.error || '') + '</td></tr>').join('') + '</table>' : '')
      + '<button class="act ghost" onclick="scatterClose()">关闭</button></div>');
    await scatterReload();
    toast('批量入库完成：' + ok.length + ' 件');
  } catch (e) { toast('批量入库失败：' + (e.message || e)); }
}
window.scatterBatchIntakeDo = scatterBatchIntakeDo;
function patchScatter() {
  const box = document.getElementById('scatterRows');
  if (box) box.innerHTML = scatterRowsHtml();
  const cnt = document.getElementById('scatterCount');
  if (cnt) cnt.textContent = scatterCountText();
  const sc = document.getElementById('scatterStarCount');
  if (sc) {
    const its = ((SCATTER || {}).items) || [];
    sc.textContent = '关注 ' + its.filter(x => x.starred).length + ' 项';
  }
}
function scatterSearch(v) {
  SCATTER_Q = v || '';
  patchScatter();
}
function scatterDir(k) {
  SCATTER_DIR = k || 'all';
  const box = document.getElementById('scatterRows');
  if (box) box.innerHTML = scatterRowsHtml();
  const cnt = document.getElementById('scatterCount');
  if (cnt) cnt.textContent = scatterCountText();
  const tabs = document.querySelectorAll('[data-scatter]');
  for (let i = 0; i < tabs.length; i++) {
    tabs[i].className = tabs[i].getAttribute('data-scatter') === SCATTER_DIR ? 'act' : 'act ghost';
  }
}
window.scatterSearch = scatterSearch;
window.scatterDir = scatterDir;
window.patchScatter = patchScatter;

async function viewScatter() {
  CURRENT_VIEW = 'scatter';
  const d = await api('/api/scatter');
  /* 批 39h：归档区导入卡片需要 zones（失败不阻塞整页） */
  try {
    const zz = await api('/api/archive/zones');
    ARCHIVE_CTX.zones = (zz && zz.zones) || [];
  } catch (e) { ARCHIVE_CTX.zones = []; }
  SCATTER = d;
  SCATTER_MARK = d.marks || {};
  /* 标记的唯一来源 = 自建库 marks；逐行补 starred（后端已并，这里做前端兜底，
     保证「只看关注」在任何数据形态下都按 marks 判定） */
  (d.items || []).forEach(x => {
    const mk = SCATTER_MARK[x.rel];
    x.starred = !!mk;
    x.star_tag = mk ? (mk.tag || '关注') : '';
    x.star_note = mk ? (mk.note || '') : '';
  });
  const dirs = d.dirs || [];
  let h = `<div class="card"><h2>散件索引（${d.total} 个文件 ｜ 根目录 ${esc(d.root)}）</h2>`;
  h += '<div class="hint">这些是<b>按业务类型横铺</b>的目录（律师函 / 合同 / 差旅发票 / 所函…），'
    + '按你的要求<b>维持原样不动</b>——本页只把它索引出来方便找。<br>'
    + '「疑似归属」= 文件名+子夹名里的当事人线索对上了台账行（<b>只提示，不硬套</b>；认不出就明说）。'
    + '标 <span class="dim">非经手 / 已迁空</span> 的是台账里没有、或原件已归位的。<br>'
    + '<b>关注 / 待处理</b>标记只存工作台自己的库里、<b>一个字都不动散件文件</b>；'
    + '认到案件的散件可以<b>入库到案卷 00-收件箱</b>（是移动；你点确认才动；台账不写）。</div>';

  h += '<table style="margin-bottom:12px"><tr><th>目录</th><th>业务类型</th><th class="r">文件</th>'
    + '<th class="r">子夹</th><th class="r">体积</th><th>状态 / 说明</th></tr>';
  h += dirs.map(x => {
    const st = x.offhand ? `<span class="warn">${esc(x.offhand)}</span>`
      : (x.exists ? '<span class="ok">在用</span>' : '<span class="dim">无此目录</span>');
    const note = x.notes ? esc(x.notes.note) : esc(x.desc);
    return `<tr><td><b>${esc(x.name)}</b></td><td class="dim">${esc(x.kind)}</td>`
      + `<td class="r n">${x.files}</td><td class="r n">${x.subdirs}</td>`
      + `<td class="r n">${x.size_mb >= 1 ? x.size_mb.toFixed(1) + ' MB' : Math.round(x.size_mb * 1024) + ' KB'}</td>`
      + `<td>${st}<div class="dim">${note}</div></td></tr>`;
  }).join('') + '</table>';

  const dirKeys = ['all'].concat(dirs.map(x => x.name));
  h += '<div class="row" style="flex-wrap:wrap;gap:6px;margin-bottom:8px">'
    + dirKeys.map(k => `<button class="act ghost" data-scatter="${esc(k)}" `
      + `onclick="scatterDir('${esc(k)}')">${k === 'all' ? '全部' : esc(k)}</button>`).join('')
    + `<input id="sq" placeholder="搜文件名 / 归属案件（边打边筛）" style="width:280px;margin-left:auto"`
    + ` value="${esc(SCATTER_Q)}" oninput="scatterSearch(this.value)" `
    + `onkeyup="scatterSearch(this.value)" oncompositionend="scatterSearch(this.value)" `
    + `onchange="scatterSearch(this.value)" onkeydown="if(event.key==='Enter'){scatterSearch(this.value)}">`
    + `<span id="scatterCount" class="dim n">${d.total} / ${d.total}</span></div>`;

  const its0 = d.items || [];
  const recN = its0.filter(x => x.matched && (x.cases || []).length).length;
  h += '<div class="row" style="flex-wrap:wrap;gap:8px;margin-bottom:8px;align-items:center">'
    + `<button class="act ghost" id="scOnlyStar" onclick="scatterToggleStar()">`
    + `${SCATTER_STAR_ONLY ? '✓ ' : ''}只看关注 / 待处理</button>`
    + `<span class="dim" id="scatterStarCount">关注 ${its0.filter(x => x.starred).length} 项</span>`
    + `<span class="dim" style="margin-left:auto">已认到台账案件的散件 <b>${recN}</b> 个`
    + `（可入库到对应案卷 00-收件箱）</span>`
    + `<button class="act ghost" onclick="scatterBatchIntake()"${recN ? '' : ' disabled'}>`
    + `批量入库（预览）</button>`
    + `<button class="act ghost" onclick="scatterClose()">收起预览</button></div>`;

  h += '<table><tr><th>目录</th><th>文件</th><th class="r">大小</th><th>修改</th>'
    + '<th>疑似归属（或为何认不出）</th><th>标记</th><th>操作</th></tr>'
    + `<tbody id="scatterRows">${scatterRowsHtml()}</tbody></table>`;
  h += '<div id="scatterOut"></div>';
  const rec = d.recognized || 0;
  h += `<div class="hint">共 ${d.total} 个文件，其中 <b>${rec}</b> 个挂上了台账案件；`
    + `其余认不出很正常（散件多是非经手、客户/资料类，台账里本就没有）。</div>`;
    h += archiveImportCardHtml();
return h + '</div>';
}

/* ============================ 所函页（批 39a） ============================
   用户 2026-09-17 拍板：① 落点＝选定案卷 01-委托手续 ② 出具日期**留空**。
   铁律：**正式法律文书一律复制既有模板改**，禁自拟版式——本页只做「填格 → 预览 → 生成」。 */
let SOHAN_META = null;
let COMMISSION_META = null;   // 批 64：委托手续套件 meta 缓存

async function viewSohan() {
  CURRENT_VIEW = 'sohan';
  const m = SOHAN_META || (SOHAN_META = await api('/api/sohan/meta'));
  let h = '<div class="card"><h2>所函生成（复制律所既有模板 + 定点替换）</h2>';
  h += '<div class="hint">按你的口径：<b>生成件落到选定案卷的 <code>01-委托手续&#92;</code></b>；'
    + '<b>出具日期留空</b>（生成后在 Word 里手填）。<br>'
    + '本页<b>只做模板替换，不改版式、不动抬头落款</b>；模板真源一行不改。</div>';

  if (!m.template_ok) {
    h += `<div class="red">模板不可用：${esc(m.template_error || '')}</div>`;
    h += `<div class="hint">模板路径：<code>${esc(m.template || '')}</code></div></div>`;
    return h;
  }
  h += `<div class="hint">模板：<code>${esc(m.template)}</code>'
    + '　<span class="dim">（真源 <code>${esc(m.template_src)}</code> ${m.src_exists ? '在位' : '<span class="red">缺失</span>'}）</span></div>`;

  /* ---- 案件选择（选中后自动带出委托人/对方/案由） ---- */
  h += '<h3 style="margin-top:12px">1 · 选案件（可留空＝只生成不归档到案卷）</h3>';
  h += '<div class="row" style="flex-wrap:wrap;gap:8px;align-items:center">'
    + `<select id="sh_case" style="min-width:420px" onchange="sohanCasePick(this.value)">`
    + '<option value="">（不指定案卷）</option>'
    + (m.folders || []).map(f => `<option value="${esc(f)}">${esc(f)}</option>`).join('')
    + '</select>'
    + '<button class="act ghost" onclick="sohanAutoFill()">从台账带出委托人 / 对方 / 案由</button>'
    + '<span id="sh_casehint" class="dim"></span></div>';

  /* ---- 填空格 ---- */
  h += '<h3 style="margin-top:12px">2 · 填格（模板里那几个空）</h3>';
  h += '<div class="form-grid" style="display:grid;grid-template-columns:130px 1fr;gap:8px;align-items:center">'
    + '<label>致送法院 <span class="red">*</span></label>'
    + '<input id="sh_court" placeholder="如：厦门市思明区人民法院">'
    + '<label>委托人 <span class="red">*</span></label>'
    + '<input id="sh_client" placeholder="如：福建闽兴饲料有限公司">'
    + '<label>承办律师</label>'
    + `<input id="sh_lawyer" value="${esc(m.default_lawyer || '')}">`
    + '<label>诉讼阶段</label>'
    + `<select id="sh_stage">${(m.stages || []).map(s => `<option value="${esc(s)}">${esc(s)}</option>`).join('')}</select>`
    + '<label>对方当事人</label>'
    + '<input id="sh_opponent" placeholder="如：当事人丙、陈某某">'
    + '<label>案由</label>'
    + '<input id="sh_cause" placeholder="如：买卖合同纠纷">'
    + '</div>';
  h += '<div class="hint">出具日期 <b>留空</b>（按你的口径，不在生成时填）。</div>';
  h += '<div class="row" style="margin-top:8px"><button class="act" onclick="sohanPreview()">'
    + '预览改动（不落盘）</button> <button class="act ghost" onclick="sohanClear()">清空</button></div>';
  h += '<div id="shOut"></div></div>';
  /* 批 64：委托手续套件（同页第二卡；民事六件套 → 案件夹 01-委托手续） */
  const cm = COMMISSION_META || (COMMISSION_META = await api('/api/commission/meta'));
  h += commissionCardHtml(cm);
  return h;
}

function sohanCasePick(v) {
  const el = document.getElementById('sh_casehint');
  if (el) el.textContent = v ? ('将落到 ' + v + '/01-委托手续') : '不指定案卷（落到所函目录下 _生成/日期/）';
}
window.sohanCasePick = sohanCasePick;

async function sohanAutoFill() {
  const sel = document.getElementById('sh_case');
  const folder = (sel && sel.value) || '';
  try {
    const d = await api('/api/sohan/meta');
    const cases = await api('/api/cases');
    const hit = ((cases && cases.cases) || []).find(x => x.name === folder);
    const led = (hit && hit.ledger) || null;
    if (!led || !led.matched) { toast('这个案卷没在台账里认到行，请手动填'); return; }
    const set = (id, v) => { const e = document.getElementById(id); if (e && v) e.value = v; };
    set('sh_client', led['委托人'] || '');
    set('sh_cause', led['案件类别'] || '');
    toast('已从台账带出；对方当事人请手动填（台账行名需人工拆）');
  } catch (e) { toast('带出失败：' + (e.message || e)); }
}
window.sohanAutoFill = sohanAutoFill;

function sohanClear() {
  ['sh_court', 'sh_client', 'sh_opponent', 'sh_cause'].forEach(id => {
    const e = document.getElementById(id); if (e) e.value = '';
  });
  const o = document.getElementById('shOut'); if (o) o.innerHTML = '';
}
window.sohanClear = sohanClear;

function sohanBody() {
  const g = id => ((document.getElementById(id) || {}).value || '').trim();
  return {court: g('sh_court'), client: g('sh_client'), lawyer: g('sh_lawyer'),
          stage: g('sh_stage'), opponent: g('sh_opponent'), cause: g('sh_cause'),
          case_folder: g('sh_case')};
}

function sohanPlanHtml(p) {
  let h = '<div class="card" style="margin-top:10px"><h2>预览（还没生成文件）</h2>';
  if (p.blocked) {
    return h + `<div class="red">不能生成：${esc(p.blocked)}</div></div>`;
  }
  h += '<table><tr><th>填空位</th><th>改前（模板原文）</th><th>改后（本次）</th><th>命中</th></tr>'
    + (p.steps || []).map(s => `<tr><td>${esc(s['字段'])}</td>`
      + `<td class="dim">${esc(s['改前'] || '')}</td>`
      + `<td><b>${esc(s['改后'] || '')}</b></td>`
      + `<td>${s['命中'] ? '<span class="ok">✓</span>' : '<span class="red">未命中</span>'}</td></tr>`).join('')
    + '</table>';
  h += '<div class="hint">出具日期：<b>留空</b>（不在生成时填）。</div>';
  h += `<div class="hint">将生成到：<code>${esc(p.dst || '')}</code></div>`;
  if (p.leftover && p.leftover.length) {
    h += `<div class="warn">注意：模板里还有未被本次覆盖的样例内容：${esc(p.leftover.join('；'))}</div>`;
  }
  h += '<div class="row" style="margin-top:8px">'
    + '<button class="act" onclick="sohanMake()">确认生成</button> '
    + '<button class="act ghost" onclick="document.getElementById(\'shOut\').innerHTML=\'\'">取消</button></div></div>';
  return h;
}

async function sohanPreview() {
  try {
    const p = await api('/api/sohan/plan?' + new URLSearchParams(sohanBody()).toString());
    document.getElementById('shOut').innerHTML = sohanPlanHtml(p);
  } catch (e) { toast('预览失败：' + (e.message || e)); }
}
window.sohanPreview = sohanPreview;

async function sohanMake() {
  try {
    const r = await post('/api/sohan/make', Object.assign(sohanBody(), {confirm: true}));
    if (r.error) { toast('生成失败：' + r.error); return; }
    document.getElementById('shOut').innerHTML =
      '<div class="card" style="margin-top:10px"><h2>已生成</h2>'
      + `<div class="hint">文件：<code>${esc(r.dst || '')}</code>（${r.bytes || 0} 字节）</div>`
      + '<div class="hint">请打开核对，并在 Word 里手填出具日期。</div>'
      + '<button class="act ghost" onclick="document.getElementById(\'shOut\').innerHTML=\'\'">关闭</button></div>';
    toast('所函已生成');
  } catch (e) { toast('生成失败：' + (e.message || e)); }
}
window.sohanMake = sohanMake;

/* ============================ 批 64：委托手续套件（民事六件套 → 01-委托手续） ============================ */
const COMMISSION_STAGE_LABEL = {'一': '一审', '二': '二审', '再': '再审'};

function commissionCardHtml(cm) {
  const d = cm.defaults || {};
  const nw = new Date();
  const today = nw.getFullYear() + '-' + String(nw.getMonth() + 1).padStart(2, '0') + '-' + String(nw.getDate()).padStart(2, '0');
  let h = '<div class="card" style="margin-top:12px"><h2>委托手续套件（民事六件套 · 复制模板 + 定点替换）</h2>';
  h += '<div class="hint">接谈确认委托后，一键生成六件到案件夹 <code>01-委托手续</code>——'
    + '① 委托代理合同 ② 授权委托书 ③ 办案反馈表 ④ 聘请律师提示 ⑤ 送达地址确认书（集美格式） ⑥ 账户确认表（集美格式）。'
    + '<b>日期与签名一律留空</b>（生成后在 Word 里手填/手签）；只替换模板填空，不改版式、不动抬头落款。</div>';
  const miss = (cm.templates || []).filter(x => !(x.exists && x.readable));
  if (miss.length) {
    h += '<div class="red">模板缺失或不可读：' + miss.map(x => esc(x.file)).join('、') + '</div>';
  }
  h += '<h3 style="margin-top:12px">1 · 选案件（按案件名自动解析预填）</h3>';
  h += '<div class="row" style="flex-wrap:wrap;gap:8px;align-items:center">'
    + '<select id="cm_case" style="min-width:420px" onchange="commissionAutoFill()">'
    + '<option value="">（选案卷）</option>'
    + (cm.folders || []).map(f => '<option value="' + esc(f) + '">' + esc(f) + '</option>').join('')
    + '</select>'
    + '<button class="act ghost" onclick="commissionAutoFill()">重新解析案件名</button>'
    + '<span id="cm_casehint" class="dim"></span></div>';
  h += '<h3 style="margin-top:12px">2 · 套件字段（自动解析后可改；留白＝生成后手补）</h3>';
  h += '<div class="form-grid" style="display:grid;grid-template-columns:150px 1fr 130px 1fr;gap:8px;align-items:center">'
    + '<label>委托人（当事人）<span class="red">*</span></label><input id="cm_client" placeholder="如：张某某 / 公司全称">'
    + '<label>证件号</label><input id="cm_client_id" placeholder="身份证/信用代码">'
    + '<label>住址</label><input id="cm_address">'
    + '<label>电话</label><input id="cm_phone">'
    + '<label>对方当事人</label><input id="cm_opponent" placeholder="多人用、分隔">'
    + '<label>案由</label><input id="cm_cause" placeholder="如：买卖合同纠纷">'
    + '<label>审级</label><select id="cm_stage"><option value="">（未选）</option>'
    + (cm.stages || []).map(s => '<option value="' + esc(s) + '">' + esc(COMMISSION_STAGE_LABEL[s] || s) + '</option>').join('')
    + '</select>'
    + '<label>授权方式</label><select id="cm_auth">'
    + (cm.auth_types || []).map(s => '<option value="' + esc(s) + '">' + esc(s) + '授权</option>').join('')
    + '</select>'
    + '<label>案件类型</label><select id="cm_case_type">'
    + (cm.case_types || []).map(s => '<option value="' + esc(s) + '">' + esc(s) + '</option>').join('')
    + '</select>'
    + '<label>案号（可留空）</label><input id="cm_case_no" placeholder="如：（2026）闽0000民初1号">'
    + '<label>受理法院</label><input id="cm_court" value="' + esc(d.court || '') + '">'
    + '<label>委托时间</label><input id="cm_commission_date" type="date" value="' + today + '">'
    + '<label>律师费（元）</label><input id="cm_fee" placeholder="如 6000">'
    + '<label>收款人（留空＝委托人）</label><input id="cm_payee">'
    + '<label>开户行</label><input id="cm_bank">'
    + '<label>账号</label><input id="cm_account">'
    + '<label>承办律师</label><input id="cm_lawyer" value="' + esc(d.lawyer || '') + '">'
    + '<label>律师电话</label><input id="cm_lawyer_phone" value="' + esc(d.lawyer_phone || '') + '">'
    + '<label>律师证号</label><input id="cm_lawyer_license" value="' + esc(d.lawyer_license || '') + '">'
    + '<label>律师所地址</label><input id="cm_lawyer_addr" value="' + esc(d.lawyer_addr || '') + '">'
    + '</div>';
  h += '<div class="hint">⑤⑥ 是<b>某法院格式</b>（去别院需换模板）；⑤ 中的电子送达选项、⑥ 的是否同意等，生成后手动勾选。</div>';
  h += '<div class="row" style="margin-top:8px"><button class="act" onclick="commissionPreview()">预览（不落盘）</button></div>';
  h += '<div id="cmOut"></div>';
  return h + '</div>';
}

/* 从案件名解析预填：当事人=VS 左侧（去前缀编号）、对方=VS 右侧（去尾段）、案由=_ 末段、类型=前缀 */
function commissionParseCase(name) {
  const s = String(name || '');
  const parts = s.split('_');
  const cause = parts.length > 1 ? parts[parts.length - 1].trim() : '';
  const vsSeg = parts.find(x => /[Vv][Ss]/.test(x)) || '';
  let client = '', opponent = '';
  if (vsSeg) {
    const m = vsSeg.split(/[Vv][Ss]/);
    client = (m[0] || '').trim();
    opponent = (m[1] || '').trim();
    if (client.indexOf('-') >= 0) client = client.split('-').pop().trim();
    if (opponent.indexOf('-') >= 0) opponent = opponent.split('-')[0].trim();
  }
  const caseType = /^执-/.test(s) ? '执行' : '民事';
  return {client: client, opponent: opponent, cause: cause, caseType: caseType};
}
window.commissionParseCase = commissionParseCase;

function commissionAutoFill() {
  const sel = document.getElementById('cm_case');
  const folder = (sel && sel.value) || '';
  const hint = document.getElementById('cm_casehint');
  if (hint) hint.textContent = folder ? ('将落到 ' + folder + '/01-委托手续') : '';
  if (!folder) return;
  const r = commissionParseCase(folder);
  const set = (id, v) => { const e = document.getElementById(id); if (e) e.value = v || ''; };
  set('cm_client', r.client); set('cm_opponent', r.opponent); set('cm_cause', r.cause);
  const ct = document.getElementById('cm_case_type'); if (ct) ct.value = r.caseType;
  toast('已按案件名解析预填（可手动修正）');
}
window.commissionAutoFill = commissionAutoFill;

function commissionBody() {
  const g = id => (((document.getElementById(id) || {}).value) || '').trim();
  return {case: g('cm_case'), fields: {
    client: g('cm_client'), client_id: g('cm_client_id'), address: g('cm_address'), phone: g('cm_phone'),
    opponent: g('cm_opponent'), cause: g('cm_cause'), stage: g('cm_stage'), auth_type: g('cm_auth'),
    case_type: g('cm_case_type'), case_no: g('cm_case_no'), court: g('cm_court'),
    commission_date: g('cm_commission_date'), fee: g('cm_fee'),
    payee: g('cm_payee'), bank: g('cm_bank'), account: g('cm_account'),
    lawyer: g('cm_lawyer'), lawyer_phone: g('cm_lawyer_phone'),
    lawyer_license: g('cm_lawyer_license'), lawyer_addr: g('cm_lawyer_addr')}};
}

function commissionPlanHtml(p) {
  let h = '<div class="card" style="margin-top:10px"><h2>预览（还没生成文件）</h2>';
  if (p.blocked) {
    return h + '<div class="red">不能生成：' + esc(p.blocked) + '</div></div>';
  }
  h += '<table><tr><th>件</th><th>将填入的内容</th></tr>'
    + (p.docs || []).map(d2 => '<tr><td>' + esc(d2.file) + '</td><td class="dim">'
      + (d2.keys.length ? d2.keys.map(k => esc(k + '＝' + (d2.values[k] || '（空）'))).join('；') : '（固定件，无填空）')
      + '</td></tr>').join('')
    + '</table>';
  if (p.warnings && p.warnings.length) {
    h += '<div class="warn">注意：' + p.warnings.map(x => esc(x)).join('；') + '</div>';
  }
  h += '<div class="hint">生成到：<code>' + esc(p.case_folder || '') + '\\01-委托手续</code>；日期与签名留空。六件一次生成。</div>';
  h += '<div class="row" style="margin-top:8px">'
    + '<button class="act" onclick="commissionBuild()">确认生成六件套</button> '
    + '<button class="act ghost" onclick="document.getElementById(\'cmOut\').innerHTML=\'\'">取消</button></div></div>';
  return h;
}

async function commissionPreview() {
  try {
    const b = commissionBody();
    if (!b.case) { toast('先在「1 · 选案件」里选案卷'); return; }
    const p = await post('/api/commission/plan', b);
    document.getElementById('cmOut').innerHTML = commissionPlanHtml(p);
  } catch (e) { toast('预览失败：' + (e.message || e)); }
}
window.commissionPreview = commissionPreview;

async function commissionBuild() {
  try {
    const b = commissionBody();
    b.confirm = true;
    const r = await post('/api/commission/build', b);
    if (r.error) { toast('生成失败：' + r.error); return; }
    const list = (r.items || []).map(x => '<li>' + esc(x) + '</li>').join('');
    document.getElementById('cmOut').innerHTML =
      '<div class="card" style="margin-top:10px"><h2>已生成 ' + (r.items || []).length + ' 件</h2>'
      + '<div class="hint">目录：<code>' + esc(r.outdir || '') + '</code></div><ul>' + list + '</ul>'
      + '<div class="hint">请逐件打开核对，手填日期/签名，补齐留白处（对方、费用等）。</div>'
      + ((r.errors && r.errors.length) ? '<div class="red">部分失败：' + r.errors.map(x => esc(x)).join('；') + '</div>' : '')
      + '<button class="act ghost" onclick="document.getElementById(\'cmOut\').innerHTML=\'\'">关闭</button></div>';
    toast('委托手续六件套已生成');
  } catch (e) { toast('生成失败：' + (e.message || e)); }
}
window.commissionBuild = commissionBuild;

/* ============================ 期限与日历（批 40：双视图合一） ============================
   清单 = 原「期限与日程」；月视图·日程 = 原「日历」页（批 36）整体并入。
   切换状态 DL_SEG 存内存，切页不丢。viewCalendar 函数保留，仅由本页 seg 调用。 */
let DL_SEG = 'list';                       // 'list' 清单 / 'month' 月视图·日程
function dlSegHtml() {
  return `<div class="seg">${['list', 'month'].map(k =>
    `<button class="${DL_SEG === k ? 'on' : ''}" onclick="dlSeg('${k}')">`
    + (k === 'list' ? '清单' : '月视图 · 日程') + `</button>`).join('')}</div>`;
}
window.dlSeg = async function(k) { DL_SEG = k; await render(); };

/* ============================ 日历视图（批 36 建，批 40 并入期限页） ============================
   期限按月排布：逾期红 / 3日内黄 / 未来墨。数据源 = /api/deadlines（与期限页同源）。 */
async function viewCalendar() {
  const d = CAL_DATA || (CAL_DATA = await api('/api/deadlines'));
  const sc = SCHED || (SCHED = await api('/api/schedule'));
  const schedItems = (sc.items || []);
  const allOpen = (d.items || []).filter(x => x['状态'] !== 'done');
  if (!CAL_Y) {
    // 批 77：默认停本月——本月有未完成期限（含逾期）或日程即停；本月真空才跳「最近未来期限月」。
    // （旧逻辑只看 future>=今天，逾期项全被排除 → 本月 6 项逾期却开局跳到 2027-04，与「防空月错觉」意图相反）
    const today = new Date();
    const todayStr = today.getFullYear() + '-' + String(today.getMonth() + 1).padStart(2, '0')
      + '-' + String(today.getDate()).padStart(2, '0');
    const thisMonth = todayStr.slice(0, 7);   // 'YYYY-MM'
    const hasThisMonth = allOpen.some(x => String(x['到期日'] || '').indexOf(thisMonth) === 0)
      || schedItems.some(x => String(x['日期'] || '').indexOf(thisMonth) === 0);
    const future = allOpen.map(x => String(x['到期日'] || '')).filter(x => x && x >= todayStr).sort();
    if (hasThisMonth) {
      CAL_Y = today.getFullYear(); CAL_M = today.getMonth() + 1;
    } else if (future.length) {
      const t = future[0].split('-');
      CAL_Y = parseInt(t[0], 10); CAL_M = parseInt(t[1], 10);
    } else if (schedItems.length) {
      const t = String(schedItems[0]['日期']).split('-');
      CAL_Y = parseInt(t[0], 10); CAL_M = parseInt(t[1], 10);
    } else {
      CAL_Y = today.getFullYear(); CAL_M = today.getMonth() + 1;
    }
  }
  const items = allOpen;
  const first = new Date(CAL_Y, CAL_M - 1, 1);
  const startWk = first.getDay();
  const daysInMonth = new Date(CAL_Y, CAL_M, 0).getDate();
  const today = new Date();
  const todayStr = today.getFullYear() + '-' + String(today.getMonth() + 1).padStart(2, '0')
    + '-' + String(today.getDate()).padStart(2, '0');

  const byDay = {};
  items.forEach(x => {
    const due = String(x['到期日'] || '');
    if (!byDay[due]) byDay[due] = [];
    byDay[due].push({'kind': '期限', 'label': String(x['案件'] || '').slice(0, 8),
                     'type': x['期限类型']});
  });
  /* 手工日程并入同一天（标注「日程」以示区分，来源不同但视图统一） */
  schedItems.forEach(x => {
    const due = String(x['日期'] || '');
    if (!due) return;
    if (!byDay[due]) byDay[due] = [];
    byDay[due].push({'kind': '日程', 'label': String(x['标题'] || '').slice(0, 8),
                     'type': x['类型'], 'id': x['id']});
  });
  const pad = n => String(n).padStart(2, '0');
  const cells = [];
  for (let i = 0; i < startWk; i++) cells.push(null);
  for (let dd = 1; dd <= daysInMonth; dd++) cells.push(dd);
  while (cells.length % 7) cells.push(null);

  let h = '<div class="card"><div class="row" style="margin:0 0 10px">' + dlSegHtml() + '</div><h2>日历 · 期限排布</h2>';
  h += '<div class="hint">数据 = 期限表（开庭 / 上诉期 / 续封 / 缴费…），与「期限与日程」页同源。'
    + '<span class="red">红＝逾期或当日</span>、<span class="warn">黄＝3 日内</span>、墨＝更远。</div>';
  h += '<div class="row" style="align-items:center;margin:10px 0">'
    + `<button class="act ghost" onclick="calMove(-1)">← 上月</button>`
    + `<b style="margin:0 12px">${CAL_Y} 年 ${CAL_M} 月</b>`
    + `<button class="act ghost" onclick="calMove(1)">下月 →</button>`
    + `<button class="act ghost" style="margin-left:12px" onclick="calToday()">回到本月</button>`
    + `<span class="dim" style="margin-left:auto">本月 ${Object.keys(byDay).filter(k => k.indexOf(CAL_Y + '-' + pad(CAL_M)) === 0).length} 天有期限</span></div>`;

  const WK = ['日', '一', '二', '三', '四', '五', '六'];
  h += '<table style="table-layout:fixed"><tr>'
    + WK.map(w => `<th style="text-align:center">${w}</th>`).join('') + '</tr>';
  for (let i = 0; i < cells.length; i += 7) {
    h += '<tr>';
    for (let j = 0; j < 7; j++) {
      const dd = cells[i + j];
      if (!dd) { h += '<td class="dim" style="height:74px;vertical-align:top"></td>'; continue; }
      const key = `${CAL_Y}-${pad(CAL_M)}-${pad(dd)}`;
      const list = byDay[key] || [];
      const isToday = key === todayStr;
      let cls = '';
      if (list.length) {
        const hasOverdue = list.some(x => key < todayStr);
        const hasNear = list.some(x => key >= todayStr && key <= addDays(todayStr, 3));
        cls = hasOverdue ? 'red' : (hasNear ? 'warn' : '');
      }
      const badge = list.length ? `<div class="${cls} n" style="font-size:12px;margin-top:2px">`
        + list.slice(0, 2).map(x => (x.kind === '日程' ? '▸' : '') + esc(x.label)).join('<br>')
        + (list.length > 2 ? `<br>…共${list.length}` : '') + '</div>' : '';
      h += `<td style="height:74px;vertical-align:top${isToday ? ';border:1.5px solid var(--accent)' : ''}">`
        + `<div class="n ${isToday ? '' : 'dim'}">${dd}${isToday ? ' 今天' : ''}</div>${badge}</td>`;
    }
    h += '</tr>';
  }
  h += '</table>';

  /* ---- 手工日程（批 37）：与台账派生期限分开的那一套，这里可写 ---- */
  h += '<h3 style="margin-top:16px">手工日程（会谈 / 阅卷 / 出差 / 内部提醒）</h3>';
  h += '<div class="hint">这一块是<b>你自己录的</b>，存在独立的日程文件里——'
    + '台账派生的期限不会覆盖它，它也不写台账。<b>提前 N 天</b>填了就会在到期前推外部服务气泡。'
    + '<span class="dim">日历格子里带 ▸ 的就是日程。</span></div>';

  const kindOpts = (sc.kinds || []).map(k => `<option value="${esc(k)}">${esc(k)}</option>`).join('');
  h += '<div class="row" style="flex-wrap:wrap;gap:8px;margin:8px 0">'
    + `<input id="sc_title" placeholder="事项（必填）" style="width:220px">`
    + `<input id="sc_date" type="date" style="width:150px">`
    + `<input id="sc_time" placeholder="时间 如 10:00" style="width:120px">`
    + `<select id="sc_kind" style="width:120px">${kindOpts}</select>`
    + `<input id="sc_case" placeholder="关联案件（可空）" style="width:180px">`
    + `<input id="sc_lead" type="number" min="0" max="30" value="1" style="width:80px" title="提前几天提醒">`
    + `<label class="dim" style="font-size:12px">提前天数</label>`
    + `<button class="act" onclick="schedAdd()">＋ 加日程</button></div>`;
  h += '<div id="schedOut"></div>';

  h += '<table><tr><th>日期</th><th>时间</th><th>事项</th><th>类型</th>'
    + '<th>关联案件</th><th>提前提醒</th><th></th></tr>'
    + (schedItems.length ? schedItems.map(x => `<tr>`
      + `<td class="n nw">${esc(x['日期'])}</td><td class="n dim nw">${esc(x['时间'] || '—')}</td>`
      + `<td>${esc(x['标题'])}${x['备注'] ? `<div class="dim">${esc(x['备注'])}</div>` : ''}</td>`
      + `<td class="dim">${esc(x['类型'] || '')}</td>`
      + `<td class="dim">${esc(x['案件'] || '—')}</td>`
      + `<td class="n">${Number(x['提前提醒'] || 0)} 天</td>`
      + `<td><button class="act ghost mini" onclick="schedEdit('${esc(x['id'])}')">编辑</button>`
      + ` <button class="act ghost mini" onclick="schedCancel('${esc(x['id'])}')">删除</button></td>`
      + `</tr>`).join('') : '<tr><td colspan="7" class="dim">还没有手工日程</td></tr>')
    + '</table>';
  if (sc.due && sc.due.length) {
    h += `<div class="warn">今日待提醒 <b>${sc.due.length}</b> 项：`
      + sc.due.map(x => esc(x['标题'])).join('、') + '（已进外部服务气泡推送队列）</div>';
  }
  const cancelled = sc.cancelled || 0;
  if (cancelled) h += `<div class="hint">另有 ${cancelled} 条已删除日程（软删留痕，未从文件移除）。</div>`;

  const upcoming = items.filter(x => String(x['到期日'] || '') >= todayStr).slice(0, 12);
  h += '<h3 style="margin-top:14px">最近期限</h3><table><tr><th>到期日</th><th>剩余</th>'
    + '<th>案件</th><th>类型</th><th>档位</th></tr>'
    + (upcoming.length ? upcoming.map(x => {
      const rem = daysBetween(todayStr, x['到期日']);
      const dr = rem < 0 ? 'red' : (rem <= 3 ? 'warn' : '');
      return `<tr><td class="n nw">${esc(x['到期日'])}</td>`
        + `<td class="${dr} n nw">${rem < 0 ? '逾期' + (-rem) + ' 天' : (rem === 0 ? '今天' : rem + ' 天')}</td>`
        + `<td>${esc(x['案件'])}</td><td class="dim">${esc(x['期限类型'])}</td>`
        + `<td class="${dr}">${esc(x['档位'] || '')}</td></tr>`;
    }).join('') : '<tr><td colspan="5" class="dim">没有未来期限</td></tr>') + '</table>';

  return h + '</div>';
}
function schedAdd() {
  const g = id => (document.getElementById(id) || {}).value || '';
  const title = g('sc_title').trim();
  if (!title) { toast('请填事项名称'); return; }
  if (!g('sc_date')) { toast('请选日期'); return; }
  if (SCHED_EDIT) {
    // 批 69：编辑模式（后端 update 只更新非空字段，既定边界不动）
    const ubody = {id: SCHED_EDIT, '标题': title, '日期': g('sc_date'), '时间': g('sc_time'),
                   '类型': g('sc_kind') || '其他', '案件': g('sc_case'),
                   '提前提醒': parseInt(g('sc_lead') || '0', 10) || 0, confirm: true};
    post('/api/schedule/update', ubody).then(() => {
      SCHED_EDIT = null; SCHED = null;
      toast('已保存修改：' + title);
      render();
    }).catch(e => toast('保存失败：' + (e.message || e)));
    return;
  }
  const body = {title: title, date: g('sc_date'), time: g('sc_time'),
                kind: g('sc_kind') || '其他', case: g('sc_case'),
                lead: parseInt(g('sc_lead') || '0', 10) || 0, confirm: true};
  post('/api/schedule/add', body).then(() => {
    SCHED = null;             // 让下次渲染重新取
    toast('已加日程：' + title);
    render();
  }).catch(e => toast('加日程失败：' + (e.message || e)));
}
function schedCancel(id) {
  if (!id) return;
  post('/api/schedule/cancel', {id: id, confirm: true}).then(() => {
    SCHED = null;
    toast('已删除该日程（软删留痕）');
    render();
  }).catch(e => toast('删除失败：' + (e.message || e)));
}
/* 批 69：日程编辑——回填表单进入编辑模式（再点上方按钮=保存修改） */
function schedEdit(id) {
  const x = ((SCHED && SCHED.items) || []).find(v => String(v.id) === String(id));
  if (!x) { toast('没找到这条日程（可能刚被改动，刷新一下）'); return; }
  const s = (i, v) => { const el = document.getElementById(i); if (el) el.value = v; };
  s('sc_title', x['标题'] || '');
  s('sc_date', x['日期'] || '');
  s('sc_time', x['时间'] || '');
  s('sc_kind', x['类型'] || '其他');
  s('sc_case', x['案件'] || '');
  s('sc_lead', Number(x['提前提醒'] || 0));
  SCHED_EDIT = x.id;
  const btn = document.querySelector('button[onclick="schedAdd()"]');
  if (btn) btn.textContent = '💾 保存修改（' + (x['标题'] || '') + '）';
  const t = document.getElementById('sc_title');
  if (t) t.scrollIntoView({block: 'center'});
  toast('编辑模式：改完点上方「保存修改」；切页即放弃编辑');
}
window.schedAdd = schedAdd;
window.schedCancel = schedCancel;
window.schedEdit = schedEdit;
function addDays(iso, n) {
  const d = new Date(iso + 'T00:00:00');
  d.setDate(d.getDate() + n);
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0')
    + '-' + String(d.getDate()).padStart(2, '0');
}
function daysBetween(a, b) {
  return Math.round((new Date(b + 'T00:00:00') - new Date(a + 'T00:00:00')) / 86400000);
}
function calMove(n) {
  let m = CAL_M + n, y = CAL_Y;
  if (m < 1) { m = 12; y -= 1; }
  if (m > 12) { m = 1; y += 1; }
  CAL_M = m; CAL_Y = y;
  render();
}
function calToday() {
  const t = new Date();
  CAL_Y = t.getFullYear(); CAL_M = t.getMonth() + 1;
  render();
}
window.calMove = calMove;
window.calToday = calToday;

/* ========================= 律师函台（批 39b，三段联动） =========================
   用户 2026-09-17 拍板 ③：签收状态 / 后续跟进**开受控回写**（走台账三重闸）。
   三段：① 台账行（可回写两列）② 债务人夹文件（只读）③ 发函进展。
   认不出对应夹的行 → 明说「未认到夹」，不硬套。 */
/* ---- 批 41：新增发函对象（台账律师函 sheet 受控加行 + 批量催收件建材料夹）---- */
function letterAddHtml() {
  const inp = (id, ph, type) => `<input id="${id}" placeholder="${ph}"${type ? ` type="${type}"` : ''}>`;
  return `<div class="hint" style="margin-top:10px"><b>新增发函对象</b>——一次做完：台账「律师函」表受控加行
    （自动备份 + 写后审计，占用返 409 不硬写）+ 批量催收件建材料夹。签收状态/后续跟进留空，寄出后在本表回写。</div>
  <div class="row" style="flex-wrap:wrap;gap:8px;margin:8px 0">
    ${inp('la_cl', '委托人')}
    ${inp('la_obj', '收函对象（必填，兼作夹名）')}
    ${inp('la_amt', '欠款金额（本金）')}
    ${inp('la_cause', '案由')}
    ${inp('la_date', '发函日期', 'date')}
    ${inp('la_law', '承办律师')}
    ${inp('la_note', '备注（可空）')}
    <button class="act ghost" onclick="letterAddDo(false)">预览</button>
    <button class="act" onclick="letterAddDo(true)">确认新增</button></div>
  <div id="laOut"></div>`;
}
window.letterAddDo = async function(confirm) {
  const out = document.getElementById('laOut');
  const gv = id => (document.getElementById(id) || {}).value || '';
  const body = {'委托人': gv('la_cl'), '收函对象': gv('la_obj'), '欠款金额（本金）': gv('la_amt'),
                '案由': gv('la_cause'), '发函日期': gv('la_date'),
                '承办律师': gv('la_law'), '备注': gv('la_note'), confirm};
  if (confirm && !body['收函对象'].trim()) { out.innerHTML = '<div class="red">收函对象必填（台账行与材料夹都靠它命名）。</div>'; return; }
  try {
    const r = await post('/api/letter/add_row', body);
    if (!confirm) {
      const p = r.plan || {}, f = r.folder || {};
      const cols = Object.keys(p.values || {}).map(k => `${esc(k)}=${esc(p.values[k])}`).join('，');
      out.innerHTML = `<div class="editbox"><b>预览（dry-run，未落盘）</b><br>
        台账「${esc(p.sheet)}」将新增第 <b class="n">${p.row}</b> 行：${cols || '（空行）'}<br>
        材料夹：${f.path ? `${esc(f.path)}（${f.exists ? '<span class="dim">已存在，跳过</span>' : '<span class="ok">将新建</span>'}）` : '<span class="red">收函对象为空，不建夹</span>'}
        ${(p.normalized || []).map(w => `<div class="dim">↳ 自动对齐台账写法：${esc(w)}</div>`).join('')}
        ${(p.warnings || []).map(w => `<div class="warn">${esc(w)}</div>`).join('')}</div>`;
      return;
    }
    if (r.error) { out.innerHTML = `<div class="red">失败：${esc(r.error)}</div>`; return; }
    const fr = r.folder || {};
    toast(`已新增台账第 ${r.plan.row} 行` + (fr.created ? ' + 材料夹已建' : ''));
    await render();
  } catch (e) { out.innerHTML = `<div class="red">失败：${esc(e.message)}</div>`; }
};

async function viewLetters() {
  CURRENT_VIEW = 'letters';
  const d = await api('/api/letters');
  LETTER_ITEMS = d.items || [];
  const fs = d.folders || {folders: []};
  schedLetterOpen();
  let h = `<div class="card"><h2>律师函台（台账 ${d.count} 条 ｜ 债务人夹 ${fs.folders.length} 个）</h2>
    <div class="hint">三段联动：<b>台账行</b>（签收状态 / 后续跟进）↔ <b>债务人夹文件</b> ↔ <b>发函进展</b>。<br>
    台账仍是唯一事实源：本页<b>只能改「签收状态 / 后续跟进」两列</b>，走受控回写
    （写前自动备份、行快照冲突返 409 不覆盖、写后审计）；其他列一律只读，台账里改。</div>
    <div class="row" style="flex-wrap:wrap;gap:8px;margin:10px 0">
      <button class="act ghost" onclick="letterOpenRoot(this)">📁 打开批量催收件根目录</button>
      <button class="act ghost" onclick="letterRescan(this)">🔄 重新扫描</button>
      <button class="act ghost" onclick="emsPanel()">📷 导入 EMS 签收截图</button>
    </div>
    <div id="emsOut"></div>
    <div id="lettersTop"></div>
  ${letterAddHtml()}`;

  if (!d.count) {
    h += '<div class="dim">台账「律师函」表暂无可读记录。</div></div>';
    return h;
  }

  /* ---- 段①：台账行 ---- */
  h += `<h3 style="margin-top:12px">① 台账行（可改：签收状态 / 后续跟进）<span id="letCount" class="dim" style="font-size:12px;font-weight:400;margin-left:10px">${letCountHtml()}</span></h3>`;
  h += `<div class="row" style="flex-wrap:wrap;gap:8px;margin:6px 0">
    <span class="seg" id="letSegSign">${letSegHtml('sign')}</span>
    <span class="seg" id="letSegFollow">${letSegHtml('follow')}</span>
    <input id="letQ" placeholder="搜收函对象/委托人/案由…（边打边筛）" style="width:220px" value="${esc(LET_FILTER.q)}"
      oninput="letSearch(this.value)" onkeyup="letSearch(this.value)"
      oncompositionend="letSearch(this.value)" onchange="letSearch(this.value)"></div>`;
  h += '<div style="overflow-x:auto"><table class="tight" style="min-width:1240px"><colgroup><col style="width:3.5%"><col style="width:9%"><col style="width:7.5%"><col style="width:14%"><col style="width:10.5%"><col style="width:7.5%"><col style="width:10%"><col style="width:8%"><col style="width:6%"><col style="width:24%"></colgroup>'
    + '<tr><th>#</th><th>委托人</th><th>发函</th><th>收函对象</th><th class="r">欠款本金</th>'
    + '<th>案由</th><th>签收状态</th><th>后续跟进</th><th>对应夹</th><th>操作</th></tr></thead>'
    + '<tbody id="letRows">' + letRowsHtml() + '</tbody></table></div>';

  const noF = d.items.filter(x => !x['后续跟进']).length;
  const noM = d.items.filter(x => !x.matched_folder).length;
  if (noF) h += `<div class="warn">「后续跟进」为空 <b>${noF}</b> 条 —— 可直接在本行「改进度」填写（受控回写台账）。</div>`;
  if (noM) h += `<div class="dim">未认到对应债务人夹 <b>${noM}</b> 条（夹名与「收函对象」对不上，需人工核对；不硬套）。</div>`;
  h += '<div id="letterOut"></div>';

  /* ---- 段②：债务人夹 ---- */
  if (fs.exists) {
    h += `<h3 style="margin-top:16px">② 债务人夹（只读）：<code>${esc(fs.root)}</code></h3>`;
    h += '<table class="tight"><colgroup><col style="width:14%"><col style="width:6%"><col style="width:6%"><col style="width:5%"><col style="width:27%"><col style="width:42%"></colgroup>'
      + '<tr><th>债务人夹</th><th class="r">文件</th><th class="r">子夹</th>'
      + '<th class="r">OCR</th><th>最近改动</th><th>样例</th></tr>'
      + fs.folders.map(f => {
        return `<tr><td><b>${esc(f.name)}</b></td><td class="r n">${f.files}</td>`
          + `<td class="r n dim">${Array.isArray(f.subdirs) ? f.subdirs.length : (f.subdirs || 0)}</td><td class="r n dim">—</td>`
          + `<td class="dim n">${esc(f.latest_time)} ${esc(f.latest.slice(0, 26))}</td>`
          + `<td class="dim">${clip(f.samples.join('；'), 360)}</td></tr>`;
      }).join('') + '</table>';
  } else {
    h += `<div class="warn">没找到目录 ${esc(fs.root || '')}</div>`;
  }
  return h + '</div>';
}

function letRowsHtml() {
  const rows = letFilteredRows();
  let h = rows.map(x => {
    const mf = x.matched_folder
      ? `<span class="ok">${esc(x.matched_folder)}</span> `
        + `<button class="act ghost mini" onclick="letterOpenRow(${x.row}, this)">📁</button>`
      : '<span class="dim">未认到夹</span>';
    return `<tr><td class="n">${esc(x['序号'])}</td><td>${clip(x['委托人'], 82)}</td>`
      + `<td class="dim n nw">${esc(x['发函日期'])}</td><td>${clip(x['收函对象'], 135)}</td>`
      + `<td class="r n">${esc(x['欠款金额（本金）'])}</td><td class="dim">${clip(x['案由'], 115)}</td>`
      + `<td>${x['签收状态'] ? clip(x['签收状态'], 110) : '<span class="warn">（空）</span>'}</td>`
      + `<td class="dim">${x['后续跟进'] ? clip(x['后续跟进'], 112) : '<span class="warn">（空）</span>'}</td>`
      + `<td class="nw">${mf}</td>`
      + '<td class="nw">'
      + `<button class="act ghost mini" onclick="letterEdit(${x.row}, this)">改进度</button> `
      + (x['后续跟进'] ? '' : `<button class="act ghost mini" onclick="letterToTask(${x.row}, this)">建任务</button>`)
      + (x.matched_folder ? ` <button class="act ghost mini" onclick="letterDocs(${x.row}, this)">函件</button>` : '')
      + (x.matched_folder ? ` <button class="act ghost mini" onclick="letterShowFiles(${x.row}, this)">看文件</button>` : '')
      + (x.matched_folder ? ` <button class="act ghost mini" onclick="letterSignRecs(${x.row}, this)">签收</button>` : '')
      + `<div id="lt-${x.row}" class="hint" style="margin-top:4px;white-space:normal"></div>`
      + '</td></tr>';
  }).join('');
  if (!rows.length) h = '<tr><td colspan="10" class="dim">没有符合条件的台账行（清一下筛选）。</td></tr>';
  return h;
}
/* 批 47n：① 筛选（chips + 文本搜，本地过滤 + patchInner；IME 五触发点同任务/台账页） */
function letSegHtml(kind) {
  const cur = LET_FILTER[kind];
  const opts = kind === 'sign'
    ? [['all', '全部'], ['empty', '签收未登记'], ['has', '已登记']]
    : [['all', '全部'], ['empty', '待跟进'], ['has', '已跟进']];
  return opts.map(([k, t]) =>
    `<button class="${cur === k ? 'on' : ''}" onclick="letFilter('${kind}','${k}')">${t}</button>`).join('');
}
function letFilteredRows() {
  const q = (LET_FILTER.q || '').trim();
  return (LETTER_ITEMS || []).filter(x => {
    if (LET_FILTER.sign === 'empty' && x['签收状态']) return false;
    if (LET_FILTER.sign === 'has' && !x['签收状态']) return false;
    if (LET_FILTER.follow === 'empty' && x['后续跟进']) return false;
    if (LET_FILTER.follow === 'has' && !x['后续跟进']) return false;
    if (q && !(String(x['收函对象'] || '') + (x['委托人'] || '') + (x['案由'] || '')).includes(q)) return false;
    return true;
  });
}
function letCountHtml() {
  return `显示 ${letFilteredRows().length} / ${(LETTER_ITEMS || []).length}`;
}
window.letFilter = function (kind, k) {
  LET_FILTER[kind] = k;
  patchInner('letSegSign', letSegHtml('sign'));
  patchInner('letSegFollow', letSegHtml('follow'));
  patchInner('letRows', letRowsHtml());
  patchInner('letCount', letCountHtml());
};
window.letSearch = function (v) {
  LET_FILTER.q = v || '';
  patchInner('letRows', letRowsHtml());
  patchInner('letCount', letCountHtml());
};

let LETTER_OPEN = null;
function schedLetterOpen() { LETTER_OPEN = null; }

function letterEdit(row) {
  const x = (LETTER_ITEMS || []).find(v => v.row === row);
  if (!x) { toast('页面数据已过期，请刷新'); return; }
  LETTER_OPEN = row;
  const h = '<div class="card" style="margin-top:10px"><h2>改发函进度（受控回写台账）</h2>'
    + `<div class="hint">台账第 <b>r${row}</b> 行 ｜ 委托人 ${esc(x['委托人'])} ｜ 收函对象 ${esc(x['收函对象'])}<br>`
    + '只能改这两列；<b>写前自动备份</b>，若该行已被外部改动会拒绝覆盖（409）。空着 = 该列不改。</div>'
    + '<div class="form-grid" style="display:grid;grid-template-columns:110px 1fr;gap:8px;align-items:center">'
    + '<label>签收状态</label>'
    + `<input id="le_sign" value="${esc(x['签收状态'] || '')}" placeholder="如：7封均已签收（EMS，均本人签收）">`
    + '<label>后续跟进</label>'
    + `<input id="le_follow" value="${esc(x['后续跟进'] || '')}" placeholder="如：已电话联系，承诺本周还款">`
    + '</div>'
    + '<div class="row" style="margin-top:8px">'
    + `<button class="act" onclick="letterEditDo(${row})">预览并写入</button> `
    + '<button class="act ghost" onclick="document.getElementById(\'letterOut\').innerHTML=\'\'">取消</button></div>'
    + '<div id="leOut"></div></div>';
  patchInner('letterOut', h);
}
window.letterEdit = letterEdit;

async function letterEditDo(row) {
  const x = (LETTER_ITEMS || []).find(v => v.row === row) || {};
  const g = id => ((document.getElementById(id) || {}).value || '').trim();
  const sign = g('le_sign'), follow = g('le_follow');
  if (!sign && !follow) { toast('两列都空着，没有要改的内容'); return; }
  const expect = {'签收状态': x['签收状态'] || '', '后续跟进': x['后续跟进'] || ''};
  try {
    const p = await post('/api/letter/update', {row: row, sign: sign, follow: follow});
    let h = '<div class="hint" style="margin-top:8px">将改动：</div><table>'
      + '<tr><th>列</th><th>改前</th><th>改后</th></tr>'
      + (p.plan.changes || []).map(c => `<tr><td>${esc(c['字段'])}</td>`
        + `<td class="dim">${esc(c['改前']) || '<span class="dim">（空）</span>'}</td>`
        + `<td><b>${esc(c['改后'])}</b></td></tr>`).join('')
      + '</table>'
      + '<div class="hint">写前会自动备份台账（该表没有进度日志列，回退靠备份）。</div>'
      + `<button class="act" onclick="letterEditApply(${row}, this)">确认写入台账</button>`;
    patchInner('leOut', h);
  } catch (e) { toast('预览失败：' + (e.message || e)); }
}
window.letterEditDo = letterEditDo;

async function letterEditApply(row, btn) {
  const x = (LETTER_ITEMS || []).find(v => v.row === row) || {};
  const g = id => ((document.getElementById(id) || {}).value || '').trim();
  const expect = {'签收状态': x['签收状态'] || '', '后续跟进': x['后续跟进'] || ''};
  const restore = btnBusy(btn, '写入中…');
  try {
    const r = await post('/api/letter/update', {row: row, sign: g('le_sign'),
                                                follow: g('le_follow'), expect: expect, confirm: true});
    if (r.busy) {
      patchInner('leOut', '<div class="warn" style="margin-top:8px">台账被占用（WPS / 云盘同步中），未写入。稍后再试。</div>');
      return;
    }
    if (r.conflict) {
      patchInner('leOut', '<div class="warn" style="margin-top:8px">该行已被外部改动，<b>未覆盖</b>。<br>'
        + '当前值：<code>' + esc(r.now || '') + '</code>　你看到的：<code>' + esc(r.expect || '')
        + '</code><br>请刷新页面后重新核对。</div>');
      return;
    }
    const cid = 'lt-' + row;
    patchInner('leOut', '<div class="hint" style="margin-top:8px">'
      + `<span class="ok">✓ 已写入台账</span>，备份：<code>${esc(r.backup || '')}</code>`
      + (r.audit && r.audit.length ? `<div class="warn">写后审计有 ${r.audit.length} 处意外变化，请核对</div>` : '')
      + '</div>');
    /* 就地更新该行的两列显示（不整页重绘 → 不跳位） */
    inlineMsg(cid, `✓ 已写入（r${row}）`, 'ok');
    LETTER_ITEMS = null;                        // 使缓存失效，下次进页重新取
    const tr = btn ? btn.closest('tr') : null;
    if (tr) {
      const tds = tr.querySelectorAll('td');
      if (tds.length >= 8) {
        tds[6].innerHTML = esc(g('le_sign')) || '<span class="warn">（空）</span>';
        tds[7].innerHTML = esc(g('le_follow').slice(0, 50)) || '<span class="warn">（空）</span>';
      }
    }
    toast('已写入台账');
  } catch (e) {
    patchInner('leOut', `<div class="warn" style="margin-top:8px">写入失败：${esc(e.message || String(e))}</div>`);
  } finally { restore(); }
}
window.letterEditApply = letterEditApply;

async function letterShowFiles(row, btn) {
  const cid = 'lt-' + row;
  const x = (LETTER_ITEMS || []).find(v => v.row === row);
  if (!x || !x.matched_folder) { inlineMsg(cid, '这一行没认到债务人夹', 'warn'); return; }
  const restore = btnBusy(btn, '读取中…');
  try {
  /* 批 48：改**点按实时扫夹**——原先用进页时的 LETTER_ITEMS 缓存，之后新放进
     夹子的文件（如盖章扫描件）不显示（用户 09-21 实踩）。接口毫秒级，勿改回缓存。 */
  const ff = await api('/api/letter/folder_files?folder=' + encodeURIComponent(x.matched_folder));
  if (ff.error) { inlineMsg(cid, ff.error, 'err'); return; }
  const byKind = {};
  (ff.files || []).forEach(f => { (byKind[f.kind] = byKind[f.kind] || []).push(f); });
  let h = '<div class="card" style="margin-top:10px"><h2>债务人夹：' + esc(x.matched_folder) + '</h2>'
    + `<div class="hint">共 ${(ff.files || []).length} 份材料`
    + (ff.ocr ? ` ｜ OCR 文本 ${ff.ocr} 份（在 <code>_ocr</code> 子夹，缓存可删）` : '')
    + (ff.subdirs && ff.subdirs.length ? ` ｜ 子夹：${ff.subdirs.map(esc).join('、')}` : '')
    + '</div>';
  h += '<table><tr><th>类别</th><th>文件</th><th class="r">大小</th><th>修改</th><th>操作</th></tr>';
  Object.keys(byKind).sort().forEach(k => {
    byKind[k].forEach((f, i) => {
      h += `<tr><td class="dim nw">${i === 0 ? esc(k) : ''}</td>`
        + `<td>${esc(f.name)}${f.sub ? `<div class="dim">${esc(f.sub)}</div>` : ''}</td>`
        + `<td class="r n dim">${f.size_kb >= 1024 ? (f.size_kb / 1024).toFixed(1) + ' MB' : Math.round(f.size_kb) + ' KB'}</td>`
        + `<td class="dim n nw">${esc(f.mtime)}</td>`
        + `<td class="nw">`
        + (/^(主体资料|image|pdf)$/.test(f.kind) || /\.(jpg|jpeg|png|bmp|webp|pdf)$/i.test(f.name)
            ? `<button class="act ghost mini" onclick="letterPreviewImg(${row}, ${esc(JSON.stringify(f.sub ? (f.sub + '\\' + f.name) : f.name))}, this)">👁 预览</button> ` : '')
        + `<button class="act ghost mini" onclick="letterOpenDir(${row}, ${esc(JSON.stringify(f.sub ? (f.sub + '\\' + f.name) : f.name))}, this)">📂 所在文件夹</button>`
        + `</td></tr>`;
    });
  });
  h += '</table><button class="act ghost" onclick="document.getElementById(\'letterOut\').innerHTML=\'\'">关闭</button></div>';
  patchInner('letterOut', h);
  /* ⚠️ 就近告知 + 自动滚到结果（否则用户视线还在表格中间，以为「没反应」） */
  inlineMsg(cid, `✓ 已展开文件清单（见下方）`, 'ok');
  const out = document.getElementById('letterOut');
  if (out && out.scrollIntoView) out.scrollIntoView({behavior: 'smooth', block: 'nearest'});
  } finally { restore(); }
}
window.letterShowFiles = letterShowFiles;

/* 批 48：📄 函件——台账行专用入口，一键只看「盖章函扫描件」（kind=律师函，
   自动排除 _备份 夹），与「签收记录」并列；实时扫夹（folder_files 接口），
   预览走 file_view 直出。用户 09-21：盖章扫描件要能像签收记录一样直接看。 */
async function letterDocs(row, btn) {
  const cid = 'lt-' + row;
  const x = (LETTER_ITEMS || []).find(v => v.row === row);
  if (!x || !x.matched_folder) { inlineMsg(cid, '这一行没认到债务人夹', 'warn'); return; }
  const restore = btnBusy(btn, '读取中…');
  try {
    const ff = await api('/api/letter/folder_files?folder=' + encodeURIComponent(x.matched_folder));
    if (ff.error) { inlineMsg(cid, ff.error, 'err'); return; }
    const docs = (ff.files || []).filter(f => f.kind === '律师函' && !String(f.sub || '').includes('_备份'));
    const rows = docs.map(f => {
      const rel = f.sub ? (f.sub + '\\' + f.name) : f.name;
      return `<tr>
      <td>${esc(f.name)}${f.sub ? `<div class="dim" style="font-size:11px">${esc(f.sub)}</div>` : ''}</td>
      <td class="r n dim">${f.size_kb >= 1024 ? (f.size_kb / 1024).toFixed(1) + ' MB' : Math.round(f.size_kb) + ' KB'}</td>
      <td class="dim n nw">${esc(f.mtime)}</td>
      <td class="nw">
        ${/\.(jpg|jpeg|png|bmp|webp|pdf)$/i.test(f.name) ? `<button class="act ghost mini" onclick="letterPreviewImg(${row}, ${esc(JSON.stringify(rel))}, this)">👁 预览</button> ` : ''}
        <button class="act ghost mini" onclick="letterOpenDir(${row}, ${esc(JSON.stringify(rel))}, this)">📂 所在文件夹</button>
      </td></tr>`;
    }).join('');
    const body = docs.length
      ? `<table><tr><th>文件</th><th class="r">大小</th><th>修改</th><th>操作</th></tr>${rows}</table>`
      : `<div class="hint">该夹暂无盖章函扫描件。放法：存进该夹的「律师函」子夹（或夹根），
          文件名含「律师函」即自动认出（实时扫，无需建索引）。<br>
          EMS 签收图走「签收记录」或页首「📷 导入 EMS 签收截图」。</div>`;
    showModal('函件（盖章扫描件）：' + x.matched_folder + '（' + docs.length + ' 份）', body,
      {footer: `<button class="act ghost mini" onclick="letterShowFiles(${row}, null)">📁 看该夹全部文件</button>
        <button class="act ghost mini" onclick="letterSignRecs(${row}, null)">✍ 签收记录</button>
        <button class="act ghost mini" onclick="closeModal()">关闭</button>`});
  } catch (e) { inlineMsg(cid, '读取失败：' + (e.message || e), 'err'); }
  finally { restore(); }
}
window.letterDocs = letterDocs;

/** 签收记录：列出某债务人夹下所有「签收」相关材料（批 46.2 建；46.6 操作改页内）。
    ⚠️ 2026-09-19 实踩：批 46.6 用正则删死代码时**误删了本函数**
    （它夹在要删的两个函数之间），导致按钮点了报错。
    教训：正则批量删代码必须**逐个核对删除边界**，删后跑 smoke 验证。 */
async function letterSignRecs(row, btn) {
  const cid = 'lt-' + row;
  const x = (LETTER_ITEMS || []).find(v => v.row === row);
  if (!x || !x.matched_folder) { inlineMsg(cid, '这一行没认到债务人夹', 'warn'); return; }
  const restore = btnBusy(btn, '查找中…');
  try {
    const d = await post('/api/letter/sign_records', {folder: x.matched_folder});
    if (d.error) { inlineMsg(cid, '查询失败：' + d.error, 'err'); return; }
    if (!d.count) {
      showModal('签收记录：' + x.matched_folder,
        `<div class="warn">该夹下未找到「签收」相关目录或文件。</div>
         <div class="hint">约定位置：<code>&lt;债务人夹&gt;\律师函及签收记录\</code>；
           可用「📷 导入 EMS 签收截图」把截图存进去。</div>`,
        {footer: `<button class="act ghost mini" onclick="letterShowFiles(${row}, null)">📁 看该夹文件</button>
          <button class="act ghost mini" onclick="closeModal()">关闭</button>`});
      inlineMsg(cid, '未找到签收记录（见浮层）', 'warn');
      return;
    }
    /* 批 47e：**按材料归组**——同一份签收材料的 png 原始截图与后导入 PDF（名字同源，
       如「徐俊帆律师函（签收）.pdf」+「徐俊帆签收.jpg」）成对挨着，组头标人名；
       纯展示排序，**不动案卷文件**。键 = 去扩展名 + 去「律师函（签收）/律师函/签收」修饰词。 */
    const _signKey = (name) => String(name || '').replace(/\.[a-z0-9]+$/i, '')
      .replace(/律师函（签收）|律师函\(签收\)|律师函|（签收）|\(签收\)|签收/g, '').trim() || String(name || '');
    const groups = [];
    (d.items || []).forEach(f => {
      const k = _signKey(f.name);
      const g = groups[groups.length - 1];
      if (g && g.k === k) g.items.push(f); else groups.push({k, items: [f]});
    });
    const rows = groups.map(g => g.items.map((f, i) => `<tr>
      ${i === 0 ? `<td class="dim nw">${esc(f.kind)}</td>` : '<td class="dim nw" style="color:transparent">·</td>'}
      <td>${esc(f.name)}${f.dir ? `<div class="dim" style="font-size:11px">${esc(f.dir)}</div>` : ''}</td>
      <td class="r n dim">${f.size_kb >= 1024 ? (f.size_kb / 1024).toFixed(1) + ' MB' : Math.round(f.size_kb) + ' KB'}</td>
      <td class="dim n nw">${esc(f.mtime)}</td>
      <td class="nw">
        ${(f.kind === '图片' || (f.name || '').toLowerCase().endsWith('.pdf')) ? `<button class="act ghost mini" onclick="letterPreviewImg(${row}, ${esc(JSON.stringify(f.rel))}, this)">👁 预览</button> ` : ''}
        <button class="act ghost mini" onclick="letterOpenDir(${row}, ${esc(JSON.stringify(f.rel))}, this)">📂 所在文件夹</button>
      </td></tr>`).join('')
      + (g.items.length > 1 || groups.length > 1
        ? `<tr><td colspan="5" class="dim" style="font-size:11px;border-top:1px dashed var(--rule)">▸ ${esc(g.k)}（${g.items.length} 份）</td></tr>`
        : '')).join('');
    /* 批 46.6：结果用**居中浮层**；操作**全在页内**（预览+定位所在文件夹），不产生下载副本。 */
    showModal('签收记录：' + x.matched_folder + '（' + d.count + ' 份）',
      `<div class="hint" style="margin:0 0 8px">按目录名含「签收」跨层级找出；同一材料的 png 截图 / PDF 已归组（▸ 组头），文件仍留在原位不动。</div>
       <table><tr><th>类型</th><th>文件</th><th class="r">大小</th><th>修改</th><th>操作</th></tr>${rows}</table>`,
      {footer: `<button class="act ghost mini" onclick="letterShowFiles(${row}, null)">📁 看该夹全部文件</button>
        <button class="act ghost mini" onclick="closeModal()">关闭</button>`});
    inlineMsg(cid, `✓ 已打开签收记录浮层（${d.count} 份）`, 'ok');
  } catch (e) { inlineMsg(cid, '查询失败：' + (e.message || e), 'err'); }
  finally { restore(); }
}
window.letterSignRecs = letterSignRecs;

/* ---- 批 46：打开本地文件夹 / 重扫 / EMS 导入（用户要求「一键直达」）-------------
   设计要点：浏览器**不能直接读本地路径**（安全沙箱），所以一律走后端接口，
   后端做**白名单校验**（只允许案件/备份体系内的路径）后再调 explorer 打开。
   ——这既满足「点一下就打开」，又不会变成「任意目录浏览器」。
------------------------------------------------------------------------- */
/* 批 46.6（用户拍板「全在页面内」）：**不再弹资源管理器**。
   实测外部窗口层级/焦点不可控——窗口确实开出来了且未最小化，但用户看不到，
   反复误判「点了没反应」。改为**页内列出所有债务人夹**，点某一夹即看其文件。 */
async function letterOpenRoot(btn) {
  const restore = btnBusy(btn, '扫描中…');
  try {
    const r = await post('/api/letter/rescan', {});
    const fl = (r.folders && r.folders.folders) || [];
    const rows = fl.map(f => `<tr>
      <td><b>${esc(f.name || '')}</b></td>
      <td class="r n">${f.files || 0}</td>
      <td class="dim n">${(f.subdirs || []).join('、') || '—'}</td>
      <td class="nw"><button class="act ghost mini"
        onclick="letterShowFilesByFolder(${esc(JSON.stringify(f.name || ''))}, this)">看文件</button></td>
    </tr>`).join('');
    showModal('批量催收件 · 债务人夹（' + fl.length + ' 个）',
      `<div class="hint" style="margin:0 0 8px">根目录：<code>${esc((r.folders || {}).root || '')}</code><br>
        点「看文件」在页内列出该夹材料（含签收记录），可直接预览/下载。</div>
       <table><tr><th>债务人夹</th><th class="r">文件数</th><th>子目录</th><th>操作</th></tr>${rows}</table>`,
      {footer: `<button class="act ghost mini" onclick="closeModal()">关闭</button>`});
  } catch (e) { inlineMsg('lettersTop', '扫描失败：' + (e.message || e), 'err'); }
  finally { restore(); }
}
window.letterOpenRoot = letterOpenRoot;

/* 按**夹名**在页内看文件（批 46.6，根目录列表点「看文件」用）。
   与 letterShowFiles（按台账 row）的区别：这个不依赖台账行，纯按文件夹看。 */
window.letterShowFilesByFolder = async function(folderName, btn) {
  const restore = btn ? btnBusy(btn, '…') : function(){};
  try {
    const d = await post('/api/letter/rescan', {});
    const fl = ((d.folders || {}).folders || []).find(f => f.name === folderName);
    if (!fl) { toast('没找到该夹'); return; }
    const files = fl.files_list || [];
    const rows = files.map(f => `<tr>
      <td class="dim nw">${esc(f.kind || '')}</td>
      <td>${esc(f.name)}${f.sub ? `<div class="dim" style="font-size:11px">${esc(f.sub)}</div>` : ''}</td>
      <td class="r n dim">${f.size_kb >= 1024 ? (f.size_kb / 1024).toFixed(1) + ' MB' : Math.round(f.size_kb) + ' KB'}</td>
      <td class="dim n nw">${esc(f.mtime || '')}</td>
      <td class="nw">
        ${(/\.(jpg|jpeg|png|bmp|webp|pdf)$/i.test(f.name)) ? `<button class="act ghost mini"
          onclick="letterPreviewByPath(${esc(JSON.stringify(folderName))}, ${esc(JSON.stringify(f.sub ? (f.sub + '\\' + f.name) : f.name))}, this)">👁 预览</button> ` : ''}
        <button class="act ghost mini"
          onclick="letterOpenDirByPath(${esc(JSON.stringify(folderName))}, ${esc(JSON.stringify(f.sub ? (f.sub + '\\' + f.name) : f.name))}, this)">📂 所在文件夹</button>
      </td></tr>`).join('');
    showModal(folderName + '（' + files.length + ' 个文件）',
      `<table><tr><th>类型</th><th>文件</th><th class="r">大小</th><th>修改</th><th>操作</th></tr>
        ${rows || '<tr><td colspan="5" class="dim">（无文件）</td></tr>'}</table>`,
      {footer: `<button class="act ghost mini" onclick="closeModal()">关闭</button>`});
  } catch (e) { toast('读取失败：' + (e.message || e)); }
  finally { restore(); }
};

/* ⚠️ 批 46.5：打开类操作**必须给浮层回执**——外包程序弹窗可能在浏览器后面，
   用户看不到 → 误判「没反应」。浮层明确说"已在资源管理器打开"，用户就放心了。 */
async function _openFeedback(title, body, call, btn) {
  const restore = btnBusy(btn, '…');
  showModalLoading(title, '正在打开…');
  try {
    const r = await call();
    if (r && r.error) {
      showModal(title, `<div class="warn">打开失败：${esc(r.error)}</div>`);
      return;
    }
    showModal(title, body);
  } catch (e) {
    showModal(title, `<div class="warn">打开失败：${esc(e.message || String(e))}</div>`);
  } finally { restore(); }
}

/* 批 46.6：改为**页内看文件清单**（原来弹资源管理器，用户看不到） */
async function letterOpenRow(row, btn) {
  const x = (LETTER_ITEMS || []).find(v => v.row === row) || {};
  if (!x.matched_folder) { toast('该行没认到债务人夹'); return; }
  await letterShowFiles(row, btn);
}
window.letterOpenRow = letterOpenRow;



/* 页面内预览（批 46.4 起；批 47 改**直出 URL 渲染**）
   ⚠️ 批 47 定案（勿改回）：预览不再走 preview_file 的 base64 dataURL——
   dataURL 对 PDF 在 Edge 里大概率**白屏**（Chromium 对 iframe 内嵌
   data:application/pdf 支持不可靠 + base64 膨胀 33%）。改为 <img>/<iframe>
   的 src 直接指向 /api/letter/file_view（后端直出二进制流），走浏览器
   **原生**图片/PDF 渲染器——零转码、无 URL 长度限制。 */
async function _letterShowPreview(folderName, rel, btn, openDirOnclick) {
  const restore = btn ? btnBusy(btn, '打开中…') : null;
  try {
    const isPdf = String(rel).toLowerCase().endsWith('.pdf');
    const name = String(rel).split('\\').pop();
    const url = '/api/letter/file_view?folder=' + encodeURIComponent(folderName)
              + '&path=' + encodeURIComponent(rel) + '&_=' + Date.now();
    const body = isPdf
      ? `<div class="hint" style="margin:0 0 8px">页面内预览 PDF · ${esc(name)} ｜ 长文档可在框内滚动</div>
         <iframe src="${url}" title="${esc(name)}"
           style="width:100%;height:74vh;border:1px solid var(--rule);border-radius:8px;background:#fff"></iframe>`
      : `<div class="hint" style="margin:0 0 8px">页面内预览 · ${esc(name)}</div>
         <div style="text-align:center;background:var(--rule-soft);padding:10px;border-radius:8px">
           <img src="${url}" alt="${esc(name)}"
             style="max-width:100%;max-height:74vh;border-radius:6px;box-shadow:0 2px 10px oklch(0.29 0.045 250 / 14%)">
         </div>`;
    /* ⚠️ 批 46.5：结果改**居中浮层**——渲到表格下方用户看不到（实踩） */
    showModal('预览：' + name, body,
      {footer: `<button class="act ghost mini" onclick="${esc(openDirOnclick)}">📂 所在文件夹</button>
       <button class="act ghost mini" onclick="closeModal()">关闭</button>`});
  } catch (e) { toast('预览失败：' + (e.message || e)); }
  finally { if (restore) restore(); }
}
window._letterShowPreview = _letterShowPreview;

async function letterPreviewImg(row, rel, btn) {
  const x = (LETTER_ITEMS || []).find(v => v.row === row);
  if (!x || !x.matched_folder) { toast('未认到夹'); return; }
  await _letterShowPreview(x.matched_folder, rel, btn,
    `letterOpenDir(${row}, ${JSON.stringify(rel)}, null)`);
}
window.letterPreviewImg = letterPreviewImg;

/** 下载文件到本地（批 46.6，用户拍板「全在页面内」）。
    为什么不用 os.startfile：外部窗口层级/焦点不可控——实测窗口确实开出来了
    且未最小化，但用户看不到 → 反复「点了没反应」。浏览器原生下载**零依赖**。
    后端返回 base64，这里拼 Blob + <a download> 触发（避免超长 dataURL 限制）。 */
/** 打开文件所在文件夹（批 47e，用户拍板 A：文件本来就在本地，浏览器下载多余）。
    取代原 letterDownload（浏览器下载会在「下载」目录再复制一份副本）。
    走 /api/letter/open_folder + select=文件 → 后端 os.startfile 打开**文件所在
    的最深目录**（实测唯一可靠方式）；浮层回执提示窗口可能在浏览器后面。 */
window.letterOpenDir = async function(row, rel, btn) {
  const x = (LETTER_ITEMS || []).find(v => v.row === row);
  if (!x) { toast('页面数据已过期，请刷新'); return; }
  const restore = btn ? btnBusy(btn, '定位…') : function(){};
  try {
    const d = await post('/api/letter/open_folder', {folder: x.matched_folder, select: rel});
    if (d.error) { showModal('未能打开', `<div class="warn">${esc(d.error)}</div>`,
      {footer: `<button class="act" onclick="closeModal()">知道了</button>`}); return; }
    showModal('已打开所在文件夹',
      `<div class="hint">资源管理器已打开文件所在目录：<b>${esc(d.opened || x.matched_folder)}</b><br>
       窗口可能在浏览器后面，请检查任务栏；文件原件位置不动。</div>`,
      {footer: `<button class="act" onclick="closeModal()">知道了</button>`});
  } catch (e) { inlineMsg('letterOut', '打开失败：' + (e.message || e), 'err'); }
  finally { restore(); }
};

/** 按**夹名+相对路径**预览（批 46.6）——根目录/夹内清单用，不依赖台账 row。
    与 letterPreviewImg 的区别：后者需要台账行（取 matched_folder），这里直接给夹名。
    批 47：渲染改 _letterShowPreview（直出 URL），不再走 dataURL。 */
window.letterPreviewByPath = async function(folderName, rel, btn) {
  await _letterShowPreview(folderName, rel, btn,
    `letterOpenDirByPath(${JSON.stringify(folderName)}, ${JSON.stringify(rel)}, null)`);
};

/** 按**夹名+相对路径**打开所在文件夹（批 47e，同 letterOpenDir 语义） */
window.letterOpenDirByPath = async function(folderName, rel, btn) {
  const restore = btn ? btnBusy(btn, '定位…') : function(){};
  try {
    const d = await post('/api/letter/open_folder', {folder: folderName, select: rel});
    if (d.error) { toast('打开失败：' + d.error); return; }
    showModal('已打开所在文件夹',
      `<div class="hint">资源管理器已打开文件所在目录：<b>${esc(d.opened || folderName)}</b><br>
       窗口可能在浏览器后面，请检查任务栏；文件原件位置不动。</div>`,
      {footer: `<button class="act" onclick="closeModal()">知道了</button>`});
  } catch (e) { toast('打开失败：' + (e.message || e)); }
  finally { restore(); }
};

async function letterRescan(btn) {
  const restore = btnBusy(btn, '扫描中…');
  try {
    await post('/api/letter/rescan', {});
    /* 批 48：**真刷新**——原版只调接口报数、页面数据纹丝不动（假动作，
       用户实踩「放新文件点重扫也看不到」→ 误以为要建索引）。render() 会重拉
       /api/letters（台账行+夹清单全部新鲜）。此处是全页刷新语义，非行操作。 */
    toast('重扫完成，页面已刷新');
    await render();
  } catch (e) { toast('重扫失败：' + (e.message || e)); }
  finally { restore(); }
}
window.letterRescan = letterRescan;

/* ---- EMS 签收截图导入面板 ---- */
function emsPanel() {
  const h = `<div class="card" style="margin-top:10px;border-left:3px solid var(--alert)">
    <h2>📷 导入 EMS 签收截图</h2>
    <div class="hint">上传截图 → <b>本机 OCR 识别</b>（零外发）→ 提炼出单号/时间/签收方式 →
      <b>你自己确认后填台账</b>（本页不会自动改任何数据）。<br>
      图片会存到该债务人夹的 <code>签收凭证\\</code> 子目录，原图不动。</div>
    <div class="hint" style="margin:6px 0">存放位置：<code>&lt;债务人夹&gt;\律师函及签收记录\</code>
      （沿用你现有习惯；导入时**复制**不移动，重名加时间前缀）。</div>
    <div class="row" style="flex-wrap:wrap;gap:8px;margin:8px 0;align-items:center">
      <input type="file" id="emsFile" accept="image/*,.pdf" style="flex:1;min-width:220px">
      <label class="dim" style="font-size:12px">存入哪个债务人夹：</label>
      <select id="emsFolder" style="min-width:200px">
        <option value="">（不存入，仅识别）</option>
        ${(LETTER_ITEMS || []).filter(v => v.matched_folder)
          .map(v => `<option value="${esc(v.matched_folder)}">${esc(v.matched_folder)}</option>`).join('')}
      </select>
      <button class="act" onclick="emsGo(this)">上传并识别</button>
      <button class="act ghost" onclick="document.getElementById('emsOut').innerHTML=''">关闭</button>
    </div>
    <div id="emsResult"></div></div>`;
  patchInner('emsOut', h);
}
window.emsPanel = emsPanel;

async function emsGo(btn) {
  const fi = document.getElementById('emsFile');
  const folder = (document.getElementById('emsFolder') || {}).value || '';
  const box = document.getElementById('emsResult');
  if (!fi || !fi.files || !fi.files[0]) {
    if (box) box.innerHTML = '<div class="warn">请先选一张截图</div>';
    return;
  }
  const file = fi.files[0];
  if (file.size > 12 * 1024 * 1024) {
    if (box) box.innerHTML = '<div class="warn">图片过大（>' + Math.round(file.size / 1048576) + 'MB），请压缩后重试</div>';
    return;
  }
  const restore = btnBusy(btn, '识别中…');
  if (box) box.innerHTML = '<div class="hint">读取并上传中…（本机 OCR 约需数秒）</div>';
  try {
    const b64 = await new Promise((res, rej) => {
      const fr = new FileReader();
      fr.onload = () => res(String(fr.result || ''));
      fr.onerror = () => rej(new Error('读文件失败'));
      fr.readAsDataURL(file);
    });
    const up = await post('/api/ems/upload', {name: file.name, data: b64});
    if (up.error) { box.innerHTML = `<div class="warn">上传失败：${esc(up.error)}</div>`; return; }
    const rec = up.recognize || {};
    const sug = up.suggest || {};
    let h = `<div class="hint">✓ 已上传（${up.size_kb} KB）→ <code>${esc(up.path)}</code></div>`;
    /* 自动匹配建议（批 46.2）：按「文件名 ↔ 台账收函对象 ↔ 夹」桥接，
       匹配到就自动选中下拉，并说明依据；匹配不到就明说、让用户手选。 */
    if (sug.folder) {
      const sel = document.getElementById('emsFolder');
      if (sel) {
        let hit = false;
        for (const op of sel.options) { if (op.value === sug.folder) { sel.value = sug.folder; hit = true; break; } }
        h += `<div class="hint"><span class="ok">✓ 已自动匹配债务人夹：<b>${esc(sug.folder)}</b></span>`
          + `（依据：${esc(sug.why)}）${hit ? '' : ' <span class="warn">但下拉里没有这个夹，请手动确认</span>'}</div>`;
      }
    } else if (sug.why) {
      h += `<div class="hint"><span class="warn">未自动匹配到债务人夹</span>（${esc(sug.why)}）——请自己在下拉里选，别存错。</div>`;
    }
    if (!rec.ok) {
      h += `<div class="warn">识别未成功：${esc(rec.error || '未知')}（图片已存好，可稍后重试识别）</div>`;
    } else {
      const f = rec.fields || {};
      h += `<div class="hint" style="margin-top:6px">识别字符 ${rec.chars} 个
        ${rec.cached ? '（命中缓存）' : `（耗时 ${Number(rec.seconds).toFixed(1)}s）`}</div>`;
      h += '<table><tr><th>字段</th><th>识别结果</th></tr>'
        + `<tr><td>EMS 单号</td><td>${f.ems_no ? '<b>' + esc(f.ems_no) + '</b>' : '<span class="dim">未识别到</span>'}</td></tr>`
        + `<tr><td>时间</td><td>${f.sign_time ? esc(f.sign_time) : '<span class="dim">未识别到</span>'}</td></tr>`
        + `<tr><td>签收方式</td><td>${esc(f.signer || '') || '<span class="dim">未识别到</span>'}</td></tr>`
        + `<tr><td>签收人</td><td>${esc(f.signer_name || '') || '<span class="dim">未识别到</span>'}</td></tr>`
        + `<tr><td>状态</td><td>${esc(f.status || '') || '<span class="dim">未识别到</span>'}</td></tr>`
        + '</table>';
      if (rec.summary) {
        h += `<div class="row" style="margin-top:6px;align-items:center">
          <span class="hint">可直接参考填入台账：</span>
          <code id="emsLine">${esc(rec.summary)}</code>
          <button class="act ghost mini" onclick="emsCopy()">复制</button></div>`;
      }
      h += '<div class="hint">⚠️ OCR 可能识别不全，请<b>对照原图核对</b>后再填台账（本页不会自动写入）。</div>';
      h += `<div class="dim" style="margin-top:4px;font-size:12px">OCR 原文（前 400 字）：<br>`
        + `${esc(String(rec.text || '').slice(0, 400))}</div>`;
    }
    // 有选债务人夹 → 询问是否存入「签收凭证」
    if (folder) {
      h += `<div class="row" style="margin-top:8px">
        <button class="act" onclick="emsDoImport(${esc(JSON.stringify(up.path))}, ${esc(JSON.stringify(folder))}, this)">
        存入「${esc(folder)}\\签收凭证\\」</button></div>`;
    }
    box.innerHTML = h;
  } catch (e) {
    box.innerHTML = `<div class="warn">失败：${esc(e.message || String(e))}</div>`;
  } finally { restore(); }
}
window.emsGo = emsGo;

async function emsDoImport(path, folder, btn) {
  const restore = btnBusy(btn, '存入中…');
  try {
    const p = await post('/api/ems/import', {path: path, folder: folder});
    if (p.error) { toast('存入失败：' + p.error); return; }
    const r = await post('/api/ems/import', {path: path, folder: folder, confirm: true});
    if (r.error) { toast('存入失败：' + r.error); return; }
    inlineMsg('lettersTop', `✓ 已存入：${r.dst}`, 'ok');
    toast('已存入签收凭证目录');
    if (btn) { btn.textContent = '✓ 已存入'; btn.disabled = true; }
  } catch (e) { toast('存入失败：' + (e.message || e)); }
  finally { restore(); if (btn && btn.textContent === '存入中…') restore(); }
}
window.emsDoImport = emsDoImport;

function emsCopy() {
  const el = document.getElementById('emsLine');
  if (!el) return;
  const t = el.textContent;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(t).then(() => toast('已复制'), () => toast('复制失败，请手动选'));
  } else { toast('浏览器不支持自动复制，请手动选中'); }
}
window.emsCopy = emsCopy;

/* 律师函 → 工作任务（批 22）：只拼台账里已有的确定性字段，绝不猜关联案件/到期日/优先级 */
function letterTaskPayload(x) {
  const who = String(x['收函对象'] || '').trim() || '（台账未填收函对象）';
  const cli = String(x['委托人'] || '').trim();
  const title = '【律师函跟进】' + who.slice(0, 40) + (cli ? '（' + cli + '）' : '');
  const bits = ['来源：台账「律师函」表 r' + x.row + '（序号 ' + (x['序号'] || '') + '）'];
  if (cli) bits.push('委托人 ' + cli);
  if (x['发函日期']) bits.push('发函 ' + String(x['发函日期']).trim());
  if (x['欠款金额（本金）']) bits.push('欠款本金 ' + String(x['欠款金额（本金）']).trim());
  const sign = String(x['签收状态'] || '').trim();
  if (sign) bits.push('签收：' + sign.slice(0, 60));
  bits.push('台账「后续跟进」列为空，由工作台一键建任务（台账该列不回写，仍由人工补）');
  return {title: title, case_ref: '', priority: '普通', due_date: '',
          note: bits.join('｜'), letter_row: x.row};
}
/* 建任务 —— **两段式：先预览，再确认**（批 46.1 用户要求加二次确认）。
   ⚠️ 历史教训（三条，都在本轮实踩过）：
   ① 原 `await render()` 整页重绘 → 页面高度突变 → 按钮移位 → 点到错误行
   ② 原 alert / 顶部 toast 反馈，用户视线在表格中间**看不见**
   ③ 原**点一下即落库、无二次确认** → 用户「不小心点了一下」就建了 3 条错任务
      （#221/#222/#223，均误建，已删）
   → 现改为：点击 → 行内展开预览卡（标题/关联案件/优先级/截止/来源）→「确认建」才落库。
   这与全站受控回写惯例（预览 → 确认）一致；且**不整页重绘**，预览只占该行下方。 */
window.letterToTask = async function(row, btn) {
  const cid = 'lt-' + row;
  const x = (LETTER_ITEMS || []).find(v => v.row === row);
  if (!x) { inlineMsg(cid, '页面数据已过期，请刷新后再点（未建任何任务）', 'warn'); return; }
  const p = letterTaskPayload(x);
  /* 行内只留一行短提示（宽表里放卡片会撑破布局） */
  inlineMsg(cid, '↓ 预览见下方', 'warn');
  /* ---- 第一段：预览（不碰数据库）——渲到表格**下方**的共享区，全宽 ---- */
  const rows = [
    ['标题', esc(p.title)],
    p.case_ref ? ['关联案件', esc(p.case_ref)] : null,
    p.priority ? ['优先级', esc(p.priority)] : null,
    p.due_date ? ['截止', esc(p.due_date)] : null,
    ['备注', '<span class="dim">' + esc(String(p.note || '').slice(0, 200)) + '</span>'],
  ].filter(Boolean);
  const h = `<div class="card" style="margin-top:10px;border-left:3px solid var(--alert)">
    <h2>确认建这条任务？</h2>
    <div class="hint">确认后写入工作台「任务」页；可随时在任务页删除。</div>
    <table style="table-layout:fixed"><colgroup><col style="width:110px"><col></colgroup>
      ${rows.map(r => `<tr><th style="text-align:left">${r[0]}</th><td style="word-break:break-word">${r[1]}</td></tr>`).join('')}
    </table>
    <div class="row" style="margin-top:8px">
      <button class="act" onclick="letterToTaskDo(${row}, this)">确认建</button>
      <button class="act ghost" onclick="letterTaskCancel(${row})">取消</button>
    </div></div>`;
  patchInner('letterOut', h);
  const out = document.getElementById('letterOut');
  if (out && out.scrollIntoView) out.scrollIntoView({behavior: 'smooth', block: 'nearest'});
};
/** 取消建任务预览（清掉下方卡片 + 行内提示） */
window.letterTaskCancel = function(row) {
  patchInner('letterOut', '');
  inlineMsg('lt-' + row, '已取消，未建任何任务', 'dim');
};
window.letterToTaskDo = async function(row, btn) {
  const cid = 'lt-' + row;
  const x = (LETTER_ITEMS || []).find(v => v.row === row);
  if (!x) { inlineMsg(cid, '页面数据已过期，请刷新（未建任何任务）', 'warn'); return; }
  const restore = btnBusy(btn, '建任务中…');
  const p = letterTaskPayload(x);
  try {
    const t = await api('/api/tasks?q=' + encodeURIComponent(p.title));
    const dup = ((t && t.tasks) || []).find(v => v.title === p.title && v.status !== '已完成');
    if (dup) {
      inlineMsg(cid, `已有同标题未完成任务（#${dup.id}），未重复创建`, 'warn');
      return;
    }
    const r = await post('/api/tasks', {title: p.title, case_ref: p.case_ref, priority: p.priority,
      due_date: p.due_date, note: p.note, confirm: true});
    const tid = (r && r.task) ? r.task.id : '';
    /* 反馈：说清「建到哪了」+ 给跳转入口（用户反馈「建了不知道去哪」）。
       行内 + 按钮变完成态，不整页重绘（防跳位）。 */
    const el = document.getElementById(cid);
    if (el) {
      el.innerHTML = `<span class="ok">✓ 已建任务 #${tid}</span>　`
        + `<button class="act ghost mini" onclick="go('tasks')">去任务页查看</button>`;
    }
    TASKS = null; OVERVIEW = null;
    toast(`已建任务 #${tid}`);
    if (btn) { btn.textContent = '已建任务'; btn.disabled = true; }
  } catch (e) {
    inlineMsg(cid, '建任务失败：' + (e.message || e), 'err');
  } finally {
    if (btn && btn.textContent === '建任务中…') restore();
  }
};
window.travelOpen = async function() {
  const out = $('#travelOut');
  out.innerHTML = '<div class="dim">读取中…（要读台账做匹配）</div>';
  try {
    const d = await api('/api/travel');
    if (!d.exists) { out.innerHTML = `<div class="warn">没找到 ${esc(d.root)}</div>`; return; }
    let h = `<div class="hint">共 <b>${d.items.length}</b> 张；能从文件名认出对应案的 <b>${d.recognized}</b> 张，其余认不出属正常。</div>`;
    h += '<table><tr><th>月份</th><th>文件</th><th>大小</th><th>疑似对应案</th><th>依据</th></tr>' +
      d.items.map(x => `<tr><td class="dim">${esc(x.month)}</td><td>${esc(x.name)}</td>
        <td class="dim">${x.size_kb} KB</td>
        <td>${x.matched ? x.cases.map(c => esc(c['案件'])).join('<br>') : '<span class="dim">—</span>'}</td>
        <td class="dim">${esc(x.why || (x.cases[0] ? x.cases[0]['为什么'] : ''))}</td></tr>`).join('') + '</table>';
    patchInner('travelOut', h);
  } catch (e) { out.innerHTML = `<div class="red">读取失败：${esc(e.message)}</div>`; }
};

/* ---------------- 合同审查（批 52；§4.4 业务线照律师函先例） ----------------
   双源只读：① 台账「合同审查」sheet（批 54 受控建表通道建好；三行头结构：1 标题 / 3 表头 / 4+ 数据，
   数据请在 WPS 里从第 4 行起填——代码绝不代建第二张/绝不写入）
   ② 素材夹扫描（夹不存在给建夹指引，不自动建）。筛选/搜索走本地过滤 + patchInner（批 47n）。 */
let CONTRACTS = null, CT_FILTER = {st: 'all', q: ''};
async function viewContracts() {
  CURRENT_VIEW = 'contracts';
  const d = await api('/api/contracts');
  CONTRACTS = d;
  const fee = (d.fee_total || 0).toLocaleString('zh-CN');
  let h = `<div class="card"><h2>合同审查（台账 ${d.count} 条 ｜ <b>收费合计 ¥${fee}</b>${d.fee_count ? '（' + d.fee_count + ' 笔可计，非数字跳过）' : ''}）</h2>
    <div class="hint">只读页：台账仍是唯一事实源（在 WPS 里改）。本页只浏览 + 素材夹直达；<b>台账零写入、素材夹零改动</b>。</div>`;
  if (!d.count) {
    h += `<div class="warn">台账「<b>合同审查</b>」sheet 还没有数据（表已由工作台受控建好：标题行 + A–J 表头在第 3 行）。<br>
      <b>填数据请在 WPS 里从第 4 行开始填</b>（全簿统一结构：第 1 行标题 / 第 3 行表头）；A–J 列 ——<br>
      <code>序号 ｜ 送审日期 ｜ 委托人 ｜ 合同名称 ｜ 相对方 ｜ 合同类型 ｜ 审查状态 ｜ 收费 ｜ 交付日期 ｜ 备注</code><br>
      审查状态建议取值：待审 / 审查中 / 已反馈 / 已定稿。填好后刷新本页即可看到。</div>`;
  } else {
    h += `<div class="row" style="margin:10px 0"><span class="seg" id="ctSeg">${ctSegHtml()}</span>
      <input id="ctQ" placeholder="搜合同名称/委托人/相对方…（边打边筛）" style="width:260px" value="${esc(CT_FILTER.q)}"
        oninput="ctSearch(this.value)" onkeyup="ctSearch(this.value)"
        oncompositionend="ctSearch(this.value)" onchange="ctSearch(this.value)">
      <span id="ctCount" class="dim" style="font-size:12px">${ctCountText()}</span></div>`;
    h += '<table class="tight"><colgroup><col style="width:4%"><col style="width:9%"><col style="width:12%"><col style="width:20%"><col style="width:15%"><col style="width:7%"><col style="width:8%"><col style="width:8%"><col style="width:8%"><col style="width:9%"></colgroup>'
      + '<tr><th>#</th><th>送审日期</th><th>委托人</th><th>合同名称</th><th>相对方</th><th>类型</th>'
      + '<th>审查状态</th><th class="r">收费</th><th>交付</th><th>备注</th></tr>'
      + '<tbody id="ctRows">' + ctRowsHtml() + '</tbody></table>';
  }
  /* 素材夹（只读扫描；夹不存在给建夹指引——不自动建） */
  const f = d.folder || {};
  h += `<h3 style="margin-top:16px">素材夹（只读）：<code>${esc(f.root || '')}</code></h3>`;
  if (!f.exists) {
    h += `<div class="warn">素材夹不存在。<b>不自动建</b>——需要时在资源管理器里手工新建这个文件夹，
      把送审的合同放进去，刷新本页即可看到清单。</div>`;
  } else if (!(f.files || []).length) {
    h += '<div class="dim">素材夹现在没有文件（放进去刷新即见）。</div>';
  } else {
    h += `<div class="hint">共 ${f.files.length} 个文件（只列元数据，不动文件）。</div>`;
    h += '<table class="tight"><tr><th>文件</th><th>子夹</th><th>类型</th><th class="r">大小</th><th>改动</th><th style="width:130px"></th></tr>'
      + f.files.map(x => `<tr>
        <td>${clip(x.name, 260)}</td><td class="dim">${esc(x.sub || '—')}</td>
        <td class="dim">${esc(x.kind || '')}</td><td class="r n">${x.size_kb} KB</td>
        <td class="dim nw">${esc(x.mtime)}</td><td class="nw">${ctFileBtnsHtml(x)}</td></tr>`).join('')
      + '</table>';
  }
  return h + '</div>';
}
function ctFiltered() {
  const all = (CONTRACTS && CONTRACTS.items) || [];
  const q = (CT_FILTER.q || '').trim();
  return all.filter(x => {
    const st = x['审查状态'] || '（未填）';
    if (CT_FILTER.st !== 'all' && st !== CT_FILTER.st) return false;
    if (!q) return true;
    return (String(x['合同名称'] || '') + String(x['委托人'] || '') + String(x['相对方'] || '')).includes(q);
  });
}
function ctSegHtml() {
  const sc = (CONTRACTS && CONTRACTS.status_counts) || {};
  const segs = [['all', '全部 ' + ((CONTRACTS && CONTRACTS.count) || 0)]]
    .concat(Object.keys(sc).map(k => [k, k + ' ' + sc[k]]));
  return segs.map(([k, t]) =>
    `<button class="${CT_FILTER.st === k ? 'on' : ''}" onclick="ctSeg('${jsq(k)}')">${esc(t)}</button>`).join('');
}
function ctCountText() {
  return '显示 ' + ctFiltered().length + ' / ' + ((CONTRACTS && CONTRACTS.items) || []).length + ' 条';
}
function ctRowsHtml() {
  const rows = ctFiltered();
  let h = rows.map(x => `<tr>
    <td class="n">${esc(x['序号'])}</td>
    <td class="dim nw">${esc(x['送审日期'])}</td>
    <td>${clip(x['委托人'], 110)}</td>
    <td>${clip(x['合同名称'], 190)}</td>
    <td>${clip(x['相对方'], 130)}</td>
    <td class="dim">${clip(x['合同类型'], 80)}</td>
    <td>${x['审查状态'] ? `<span class="badge s0">${esc(x['审查状态'])}</span>` : '<span class="warn">（未填）</span>'}</td>
    <td class="r n">${esc(x['收费'])}</td>
    <td class="dim nw">${esc(x['交付日期'])}</td>
    <td class="dim">${clip(x['备注'], 120)}</td></tr>`).join('');
  if (!rows.length) h = '<tr><td colspan="10" class="dim">没有符合条件的行（清一下筛选）。</td></tr>';
  return h;
}
window.ctSeg = function (k) {
  CT_FILTER.st = k;
  patchInner('ctSeg', ctSegHtml());
  patchInner('ctRows', ctRowsHtml());
  patchInner('ctCount', ctCountText());
};
window.ctSearch = function (v) {
  CT_FILTER.q = v || '';
  patchInner('ctRows', ctRowsHtml());
  patchInner('ctCount', ctCountText());
};
function ctFileBtnsHtml(x) {
  const m = /\.([a-z0-9]+)$/i.exec(String(x.name));
  const ext = m ? '.' + m[1].toLowerCase() : '';
  const prev = ['.pdf', '.jpg', '.jpeg', '.png', '.bmp', '.webp'].includes(ext);
  return (prev ? `<button class="act ghost mini" onclick="ctPreview('${jsq(x.sub || '')}','${jsq(x.name)}')">预览</button> ` : '')
    + `<button class="act ghost mini" onclick="ctOpen('${jsq(x.sub || '')}','${jsq(x.name)}')">打开</button>`;
}
window.ctOpen = async function (sub, name) {
  const root = (CONTRACTS && CONTRACTS.folder && CONTRACTS.folder.root) || '';
  const p = sub ? root + '\\' + sub + '\\' + name : root + '\\' + name;
  try {
    const r = await post('/api/letter/open_file', {path: p});
    if (r.error) alert('打开失败：' + r.error); else toast('已打开：' + name);
  } catch (e) { alert('打开失败：' + e.message); }
};
window.ctPreview = function (sub, name) {
  const root = (CONTRACTS && CONTRACTS.folder && CONTRACTS.folder.root) || '';
  const p = sub ? root + '\\' + sub + '\\' + name : root + '\\' + name;
  const m = /\.([a-z0-9]+)$/i.exec(String(name));
  const ext = m ? '.' + m[1].toLowerCase() : '';
  const url = '/api/letter/file_view?path=' + encodeURIComponent(p) + '&_=' + Date.now();
  const body = ext === '.pdf'
    ? `<iframe src="${url}" title="${esc(name)}" style="width:100%;height:74vh;border:1px solid var(--rule);border-radius:8px;background:#fff"></iframe>`
    : `<div style="text-align:center;background:var(--rule-soft);padding:10px;border-radius:8px"><img src="${url}" alt="${esc(name)}" style="max-width:100%;max-height:74vh;border-radius:6px"></div>`;
  showModal('预览：' + name, body,
    {footer: `<button class="act ghost mini" onclick="closeModal()">关闭</button>`});
};

/* ---------------- 期限 ---------------- */
function dlFilteredItems(d) {
  d = d || DL_DATA; if (!d) return [];
  return d.items.filter(i => i['状态'] !== 'done'
    && (!DL_FILTER.cs || String(i['案件'] || '').trim() === DL_FILTER.cs)
    && (!DL_FILTER.tp || String(i['期限类型'] || '') === DL_FILTER.tp));
}
window.dlFilterSel = function (k, v) {
  DL_FILTER[k] = v;
  patchInner('dlRows', dlTable(dlFilteredItems(DL_DATA), {act: true}));
  const total = ((DL_DATA && DL_DATA.items) || []).filter(i => i['状态'] !== 'done').length;
  patchInner('dlCount', '显示 ' + dlFilteredItems(DL_DATA).length + ' / ' + total + ' 项');
};
async function viewDeadlines() {
  if (DL_SEG === 'month') return await viewCalendar();
  const d = await api('/api/deadlines');
  DL_DATA = d;
  let h = '<div class="card"><div class="row" style="margin:0 0 10px">' + dlSegHtml() + '</div><h2>期限与日程（本地派生表，只读台账不回写）</h2>' +
    `<div class="row"><button class="act ghost" onclick="dlExtract(false)">从台账抽取（dry-run）</button>
      <button class="act" onclick="dlExtract(true)">抽取并写入</button></div>
     <div class="row">
       <input id="d_case" placeholder="案件（台账原名或简称）" style="width:260px">
       <select id="d_type">${d.types.map(t=>`<option>${esc(t)}</option>`).join('')}</select>
       <input id="d_date" type="date">
       <input id="d_note" placeholder="备注" style="width:200px">
       <button class="act" onclick="dlAdd()">人工补录</button></div>
     <div class="hint">生成时间 ${esc(d.generated_at||'尚未抽取')}｜未完成 ${d.items.filter(i=>i['状态']!=='done').length} 项｜7 日内 ${d.due_count} 项</div>
     <div class="hint">「→建任务」= 把这条期限转成工作台任务（进自建库，不动台账；同标题未完成任务不会重复建）；
       「标完成」= 只改本地期限表 <b>data\\deadlines.json</b>，台账不回写。</div>
     <div id="dlOut"></div>`;
  const dlCasesAll = [...new Set(d.items.filter(i => i['状态'] !== 'done')
    .map(i => String(i['案件'] || '').trim()))].filter(Boolean).sort();
  h += `<div class="row" style="margin:10px 0;flex-wrap:wrap;gap:6px">
    <span class="dim" style="font-size:12px">案件：</span>
    <select onchange="dlFilterSel('cs',this.value)" style="width:210px"><option value="">全部</option>${dlCasesAll.map(c => `<option ${DL_FILTER.cs === c ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select>
    <span class="dim" style="font-size:12px">类型：</span>
    <select onchange="dlFilterSel('tp',this.value)" style="width:170px"><option value="">全部</option>${(d.types || []).map(t => `<option ${DL_FILTER.tp === t ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select>
    <span class="dim" style="font-size:12px" id="dlCount">显示 ${dlFilteredItems(d).length} / ${d.items.filter(i => i['状态'] !== 'done').length} 项</span></div>`;
  h += `<div id="dlRows">${dlTable(dlFilteredItems(d), {act: true})}</div>`;
  if (d.items.some(i => i['状态'] === 'done')) {
    h += '<h2 style="margin-top:14px">已完成</h2><table><tr><th>到期日</th><th>案件</th><th>类型</th><th>完成于</th></tr>' +
      d.items.filter(i => i['状态'] === 'done').map(i => `<tr><td class="n">${esc(i['到期日'])}</td>
        <td>${esc(i['案件'])}</td><td>${esc(i['期限类型'])}</td><td class="dim n">${esc(i['done_at']||'')}</td></tr>`).join('') + '</table>';
  }
  if (d.manual_hints.length) {
    h += '<h2 style="margin-top:14px" class="warn">需人工补录（台账里是相对期限，未猜日期）</h2><table><tr><th>案件</th><th>台账行</th><th>原文</th></tr>' +
      d.manual_hints.map(x => `<tr><td>${esc(x['案件'])}</td><td class="dim">${esc(x['sheet'])} r${x['row']}</td>
        <td class="dim">${esc(x['line'])}</td></tr>`).join('') + '</table>';
  }

  return h + '</div>';
}
window.dlExtract = async function(confirm) {
  const out = $('#dlOut'); out.innerHTML = '<div class="dim">处理中…</div>';
  try {
    const r = await post('/api/deadlines/extract', {confirm});
    out.innerHTML = `<pre>${esc(JSON.stringify(r, null, 1))}</pre>`;
    if (confirm) { await render(); }
  } catch (e) { out.innerHTML = `<div class="red">失败：${esc(e.message)}</div>`; }
};
window.dlAdd = async function() {
  const out = $('#dlOut');
  try {
    const r = await post('/api/deadlines/add', {case:$('#d_case').value.trim(),
      type:$('#d_type').value, date:$('#d_date').value, note:$('#d_note').value, confirm:true});
    out.innerHTML = `<pre>${esc(JSON.stringify(r, null, 1))}</pre>`; await render();
  } catch (e) { out.innerHTML = `<div class="red">失败：${esc(e.message)}</div>`; }
};
/* 期限 → 任务（批 14）：点击后动作，允许整页渲染 */
window.dlToTask = async function(caseName, type, due, level) {
  const title = '【期限】' + (caseName || '未指案件') + ' ' + (type || '期限') + (due ? '（' + due + '）' : '');
  const prio = ['逾期', '当日', '3日内'].includes(level) ? '紧急'
    : (level === '7日内' ? '重要' : '普通');
  try {
    const t = await api('/api/tasks?q=' + encodeURIComponent(title));
    const dup = ((t && t.tasks) || []).find(x => x.title === title && x.status !== '已完成');
    if (dup) return alert('已有同标题未完成任务（#' + dup.id + '），未重复创建。');
    const r = await post('/api/tasks', {title: title, case_ref: caseName || '', priority: prio,
      due_date: due || '', note: '由「期限与日程」页一键生成（来源：' + (type || '') + '）', confirm: true});
    TASKS = null; OVERVIEW = null;
    toast('已建任务 #' + ((r && r.task) ? r.task.id : ''));
    await render();
  } catch (e) { alert('建任务失败：' + e.message); }
};
window.dlDone = async function(id) {
  if (!confirm('把这条期限标记为「已完成」？\n（只改工作台本地期限表 data\\deadlines.json，不动台账）')) return;
  try { await post('/api/deadlines/done', {id: id, confirm: true}); await render(); }
  catch (e) { alert('标记失败：' + e.message); }
};

/* ---------------- 归档与导出 ---------------- */
async function viewArchive() {
  const c = CASES || (CASES = await api('/api/cases'));
  const y = new Date().getFullYear();
  const opts = c.cases.map(x => `<option>${esc(x.name)}</option>`).join('');
  return `<div class="card"><h2>结案归档（整夹移 _已结案\\年份\\，纯移动不删除）</h2>
  <div class="row"><select id="a_case" style="min-width:360px">${opts}</select>
    <input id="a_year" value="${y}" style="width:80px">
    <button class="act ghost" onclick="doArchive(false)">预览（dry-run）</button>
    <button class="act" onclick="doArchive(true)">确认归档</button></div>
  <div class="hint">确认归档时三步一起做完：① 案卷整夹移到 <code>_已结案\年份\</code> ② 台账里这一案的行<b>整行移入「已结案」页</b>（移前自动备份、原行内容清空、信息全量落已结案页）③ 知识库案件页<b>标「已结案」</b>。<b>认不出的步骤会在结果里说明、不猜</b>。仍需人工：过程性文件打印状态核对、在办页序号重排。</div>
  <div id="archOut"></div></div>
  <div class="card"><h2>卷宗装订包（原件只读；产物落 <code>卷宗装订\\</code>）</h2>
  <div class="row"><select id="bp_case" style="min-width:360px">${opts}</select>
    <button class="act ghost" onclick="bindingDo(false)">预览清单（dry-run）</button>
    <button class="act" onclick="bindingDo(true)">生成装订包</button>
    <button class="act ghost" onclick="bindingOpen()">打开产出目录</button></div>
  <div class="hint">把案卷 7 个规范子夹（01-委托手续 → 07-律师函）的 PDF / 图片合成一本<b>完整卷宗 PDF</b>：封面 + 登记表 + 卷内目录 + 办案小结 + 正文（连续页码 + 书签）；docx 等非 PDF 材料只列清单提示、<b>不自动并入</b>。原件只读合成，不改动任何材料。</div>
  <div id="bpOut"></div></div>
  <div class="card"><h2>知识库案件页 → 标「已结案」（单页，等价于归档时自动做的第三步）</h2>
  <div class="row"><select id="wk_sel" style="min-width:400px"><option value="">（先点「读取案件页」）</option></select>
    <button class="act ghost" onclick="wkLoad()">读取案件页</button>
    <button class="act ghost" onclick="wkDo(false)">预览标记</button>
    <button class="act" onclick="wkDo(true)">确认标记</button></div>
  <div class="hint">只做<b>锚点增量改</b>：frontmatter 的 <code>status / tags / updated</code> + 标题下加一行「已结案」+ 文末追加「## 结案归档」小节；<b>不重写整页</b>、已标过就跳过；写前备份到 <code>D:\\备份\\03-vault备份</code>，写后复读校验，不过就自动回滚。</div>
  <div id="wkOut"></div></div>
  <div class="card"><h2>台账结案移行（单行，等价于归档时自动做的第二步）</h2>
  <div class="row"><select id="cl_sel" style="min-width:360px"><option value="">（先在下面点「读取在办行」）</option></select>
    <button class="act ghost" onclick="clLoad()">读取在办行</button>
    <button class="act ghost" onclick="clDo(false)">预览移行</button>
    <button class="act" onclick="clDo(true)">确认移行</button></div>
  <div class="hint">只动台账：源行<b>清空内容、行保留</b>（在办页序号会出现空档，重编号需人工）；目标行「最新状态」写 <code>已结案</code>，备注记明来源行。</div>
  <div id="clOut"></div></div>
  <div class="card"><h2>台账 → 批量文书数据表派生导出</h2>
  <div class="row"><button class="act ghost" onclick="doExport(false)">预览派生结果</button>
    <button class="act" onclick="doExport(true)">生成派生表（写 数据\\生成\\，不动正式表）</button></div>
  <div class="hint">只派生台账能提供的列（案由/本金/律师费/诉请总额/entity_key）；<b>违约金参数、事实理由、被告、证据、财产线索必须人工补</b>——不猜。</div>
  <div id="expOut"></div></div>`;
}
/* 批 59：卷宗装订包（dry-run 分拣清单 / 生成 / 打开产出目录） */
window.bindingDo = async function(confirm) {
  const out = $('#bpOut');
  out.innerHTML = '<div class="dim">' + (confirm ? '生成中（大卷宗可能要几十秒）…' : '扫描材料…') + '</div>';
  try {
    const r = await post('/api/binding/build', {case: $('#bp_case').value, confirm: !!confirm});
    out.innerHTML = confirm ? bindingDoneHtml(r) : bindingPlanHtml(r);
  } catch (e) { out.innerHTML = `<div class="red">失败：${esc(e.message)}</div>`; }
};
window.bindingOpen = async function() {
  const out = $('#bpOut');
  out.innerHTML = '<div class="dim">打开中…</div>';
  try {
    const r = await post('/api/binding/open', {case: $('#bp_case').value});
    out.innerHTML = (r && r.ok)
      ? `<div class="ok">已打开：<code>${esc(r.opened || '')}</code></div>`
      : `<div class="red">打不开：${esc((r && r.error) || '未知原因')}</div>`;
  } catch (e) { out.innerHTML = `<div class="red">失败：${esc(e.message)}</div>`; }
};
function bindingPlanHtml(r) {
  const p = r.plan || {}, sc = p.scan || {}, m = r.meta || {};
  let h = `<div><b>${esc(m['案件名'] || r.case || '')}</b>`
        + (m['案号'] ? ` <span class="dim">${esc(m['案号'])}</span>` : '')
        + (m.matched === false ? ' <span class="warn">（台账未匹配到行——封面仅有案件名，案号等为空）</span>' : '')
        + '</div>';
  const secs = (sc.sections || []).filter(s => s.files.length);
  h += '<div style="margin-top:8px"><b>入卷材料（按卷序）：</b></div>';
  if (!secs.length) h += '<div class="warn">规范子夹（01-07）里没有材料——先把材料归位再装订。</div>';
  for (const s of secs) {
    h += `<div style="margin:6px 0 2px"><span class="pill">${esc(s.dir)}</span></div>`;
    for (const f of s.files) {
      const tag = f.kind === 'pdf' ? 'PDF·并入' : (f.kind === 'image' ? '图片·并入' : '非 PDF·仅列目录');
      h += `<div class="dim" style="margin-left:14px">· ${esc(f.name)}（${tag}，${f.size_kb}KB，${f.mtime}）</div>`;
    }
  }
  const ex = (sc.excluded || []).filter(e => e.count);
  if (ex.length) {
    h += '<div style="margin-top:8px"><b>未入卷目录：</b></div>';
    for (const e of ex) h += `<div class="dim" style="margin-left:14px">· ${esc(e.dir)}：${e.count} 个文件 — ${esc(e.note)}</div>`;
  }
  const w = p.warnings || [];
  if (w.length) h += '<div style="margin-top:8px">' + w.map(x => `<div class="warn">⚠️ ${esc(x)}</div>`).join('') + '</div>';
  h += '<div class="hint">以上为预览，未落盘。确认后点「生成装订包」——产物新建在 <code>卷宗装订\\</code>，不覆盖已有产物。</div>';
  return h;
}
function bindingDoneHtml(r) {
  let h = `<div class="ok">已生成：<code>${esc(r.pdf || '')}</code>（${r.pages} 页 ｜ 正文 ${r.body_pages} 页 ｜ 并入 ${r.merged} 份）</div>`;
  h += `<div>卷内目录（文字版）：<code>${esc(r.md || '')}</code></div>`;
  h += `<div class="dim">产物位置：案件夹 ${esc(r.out_dir || '卷宗装订')}\\ —— 含封面 / 登记表 / 卷内目录 / 办案小结 + 连续页码 + 书签。可点「打开产出目录」查看。</div>`;
  const w = r.warnings || [];
  if (w.length) h += w.map(x => `<div class="warn">⚠️ ${esc(x)}</div>`).join('');
  return h;
}
window.doArchive = async function(confirm) {
  const out = $('#archOut'); out.innerHTML = '<div class="dim">处理中…</div>';
  try {
    const r = await post('/api/archive', {case:$('#a_case').value, year:$('#a_year').value, confirm});
    if (r.dry_run) {
      const p = r.plan || {};
      let h = `<div>案卷 → <code>${esc(p.dst || '')}</code></div>`;
      const L = p.ledger;
      h += L ? `<div>台账移行：<span class="pill">${esc(L.src_sheet)} r${L.src_row}</span> → <span class="pill">${esc(L.dst_sheet)} r${L.dst_row}</span>（案件 ${esc(L.src_案件 || '')}）</div>`
             : `<div class="warn">台账：${esc(p.ledger_note || '未预判')}</div>`;
      const W = p.wiki || {};
      h += W.suggest ? `<div>知识库页：<b>${esc(W.suggest)}</b> 将标「已结案」</div>`
                     : `<div class="warn">知识库页：${esc(W.note || '未认到')}</div>`;
      if (W.vault) h += `<div class="dim">知识库 git：${esc(W.vault.detail || '')}</div>`;
      out.innerHTML = h + '<div class="hint">以上为预览，未落盘。确认后点「确认归档」。</div>';
      return;
    }
    let h = `<div class="ok">已归档 → <code>${esc(r.moved_to || '')}</code></div>`;
    const L = r.ledger_moved || {};
    h += L.error ? `<div class="warn">台账移行：${esc(L.error)}</div>`
                 : `<div>台账：已移入「${esc(L.dst_sheet || '')}」第 ${L.dst_row} 行（源行已清空）</div>`;
    const W = r.wiki_marked || {};
    h += W.done ? `<div>知识库：已给「${esc(W.page)}」标「已结案」<span class="dim">（备份 ${esc(W.backup || '')}）</span></div>`
                : `<div class="warn">知识库未标：${esc(W.reason || '未处理')}</div>`;
    h += `<div class="hint">${esc((r.followup || []).join('；') || '')}</div>`;
    out.innerHTML = h;
    /* 批 68 审计补：归档三步含台账移行（结案移行）→ LEDGER/OVERVIEW 一并失效 */
    if (confirm) { CASES = null; LEDGER = null; OVERVIEW = null; }
  } catch (e) { out.innerHTML = `<div class="red">失败：${esc(e.message)}</div>`; }
};
/* 批 28：知识库案件页 → 标「已结案」（单页受控） */
window.wkLoad = async function() {
  const out = $('#wkOut'); out.innerHTML = '<div class="dim">读知识库…</div>';
  try {
    const d = await api('/api/wiki/case_pages');
    const items = d.items || [];
    if (!items.length) { out.innerHTML = '<div class="warn">知识库里没读到案件页。</div>'; return; }
    $('#wk_sel').innerHTML = '<option value="">（选一页要标「已结案」的）</option>' + items.map(p =>
      `<option value="${esc(p.title)}"${p.closed ? ' disabled' : ''}>${p.closed ? '【已结案】' : ''}${esc(p.title)}（${esc(p.mtime)}）</option>`).join('');
    out.innerHTML = `<div class="dim">共 ${d.count} 页，其中已标「已结案」${d.closed} 页（已标的不可再选）。</div>` +
      (d.vault ? `<div class="dim">知识库 git：${esc(d.vault.detail || '')}</div>` : '');
  } catch (e) { out.innerHTML = `<div class="red">失败：${esc(e.message)}</div>`; }
};
window.wkDo = async function(confirm) {
  const out = $('#wkOut');
  const page = $('#wk_sel').value;
  if (!page) { out.innerHTML = '<div class="warn">先选一页（没有就点「读取案件页」）。</div>'; return; }
  out.innerHTML = '<div class="dim">处理中…</div>';
  try {
    const r = await post('/api/wiki/close', {page, confirm});
    if (r.dry_run) {
      const p = r.plan || {};
      if (!p.found) { out.innerHTML = `<div class="warn">没找到这一页：${esc(p.title || '')}</div>`; return; }
      if (p.already) { out.innerHTML = `<div class="ok">该页已标过「已结案」，无需再改。</div>`; return; }
      out.innerHTML = `<div>页：<code>${esc(p.path || '')}</code></div>` +
        '<ul>' + (p.changes || []).map(c => `<li>${esc(c)}</li>`).join('') + '</ul>' +
        (p.warnings || []).map(w => `<div class="warn">⚠️ ${esc(w)}</div>`).join('') +
        '<div class="hint">确认无误后点「确认标记」。写前会先备份原文件到 <code>D:\\备份\\03-vault备份</code>。</div>';
      return;
    }
    out.innerHTML = r.changed
      ? `<div class="ok">已标记：<code>${esc(r.path || '')}</code></div><div class="dim">备份：${esc(r.backup || '')}</div>`
      : `<div class="ok">${esc(r.note || '未改动')}</div>`;
    if (typeof CASES !== 'undefined') CASES = null;
  } catch (e) { out.innerHTML = `<div class="red">失败：${esc(e.message)}</div>`; }
};
/* 批 26：台账结案移行（单行）——在办页 → 「已结案」页 */
window.clLoad = async function() {
  const out = $('#clOut'); out.innerHTML = '<div class="dim">读取台账…</div>';
  try {
    const d = await api('/api/ledger_rows');
    const rows = d.rows || d.items || [];
    if (!rows.length) { out.innerHTML = '<div class="warn">没读到台账案件行。</div>'; return; }
    $('#cl_sel').innerHTML = '<option value="">（选一行要结案的）</option>' + rows.map(r =>
      `<option value="${esc(r.sheet)}|${r.row}">${esc(r.sheet)} r${r.row} ｜ ${esc(r['序号'] || '')} ｜ ${esc(r['案件'] || '')}</option>`).join('');
    out.innerHTML = `<div class="dim">已载入 ${rows.length} 行，请选择后点「预览移行」。</div>`;
  } catch (e) { out.innerHTML = `<div class="red">失败：${esc(e.message)}</div>`; }
};
window.clDo = async function(confirm) {
  const out = $('#clOut');
  const v = $('#cl_sel').value;
  if (!v) { out.innerHTML = '<div class="warn">先选一行（没有就点「读取在办行」）。</div>'; return; }
  const parts = v.split('|');
  out.innerHTML = '<div class="dim">处理中…</div>';
  try {
    const r = await post('/api/ledger/close', {sheet: parts[0], row: Number(parts[1]), confirm});
    if (r.dry_run) {
      const p = r.plan;
      out.innerHTML = `<div><span class="pill">${esc(p.src_sheet)} r${p.src_row}</span> → <span class="pill">${esc(p.dst_sheet)} r${p.dst_row}</span></div>` +
        `<div>案件：<b>${esc(p.src_案件 || '')}</b>（原序号 ${esc(p.src_序号 || '')}）</div>` +
        (p.warnings || []).map(w => `<div class="warn">⚠️ ${esc(w)}</div>`).join('') +
        '<div class="hint">确认无误后点「确认移行」。</div>';
    } else {
      const d = r.done;
      out.innerHTML = `<div class="ok">已移入「${esc(d.dst_sheet)}」第 ${d.dst_row} 行；源行清空 ${d.cleared.length} 列。</div>` +
        `<div class="dim">备份：${esc(d.backup || '')}</div>` +
        ((d.audit || []).length ? '<div class="red">写后审计有意外改动，请查看备份</div>'
                                : '<div class="dim">写后审计：零意外 ✓</div>');
      /* 批 68 审计补：结案移行改动了台账在办/已结案分布——OVERVIEW（首页计数）
         与 CASES（案件列表台账摘要）快照必须同步失效，否则页面口径与台账不一致 */
      if (typeof LEDGER !== 'undefined') LEDGER = null;
      if (typeof OVERVIEW !== 'undefined') OVERVIEW = null;
      if (typeof CASES !== 'undefined') CASES = null;
    }
  } catch (e) { out.innerHTML = `<div class="red">失败：${esc(e.message)}</div>`; }
};
window.doExport = async function(confirm) {
  const out = $('#expOut'); out.innerHTML = '<div class="dim">处理中…</div>';
  try {
    const r = await post('/api/export_batch', {confirm});
    const d = r.preview || r;
    out.innerHTML = `<div class="row"><span class="pill">台账案件 ${d.rows ? d.rows.length : d.cases} 条</span>
      <span class="pill">原告主体匹配 ${d.matched_entity ?? '—'}</span>
      <span class="pill">数据表现有案件行 ${d.existing_rows ?? 0}</span></div>` +
      (r.path ? `<div class="ok">已生成：${esc(r.path)}</div>` : '') +
      `<pre>${esc(JSON.stringify(d.rows ? d.rows.slice(0,14) : d, null, 1))}</pre>`;
  } catch (e) { out.innerHTML = `<div class="red">失败：${esc(e.message)}</div>`; }
};

/* ---------------- 台账进展（批 13：受控回写台账） ---------------- */
/* 纪律：台账是唯一事实源；本页改「当前进度」列，旧值自动挪到「进度日志」列；
   占用（WPS 打开/云盘同步）→ 入待同步队列可重试；台账被外部改动 → 报差异不覆盖。 */
let LED_EDIT = null, LED_PREVIEW = null, LED_FILTER = {q: '', st: 'all'};

const jsq = s => String(s == null ? '' : s).replace(/\\/g, '\\\\').replace(/'/g, "\\'");

/* 局部重绘：只换指定容器的内容，**不重建输入框**
   —— 2026-09-12 用户报「打拼音还没打完就卡住」：原做法「每按键 → 整页 render()」会把输入框
   元素销毁重建 → 输入法组合状态（未上屏的拼音）+ 光标一起丢，且每次还发一次请求。
   纪律：**搜索/筛选一律局部重绘 + 前端过滤；绝不在输入过程中整页重渲染。** */
function patchInner(id, html) {
  if (typeof document === 'undefined') return;
  const el = document.getElementById(id);
  if (el) el.innerHTML = html;
}

async function loadLedger(force) {
  if (!LEDGER || force) LEDGER = await api('/api/ledger_rows');
  return LEDGER;
}
window.ledgerReload = async function () { LEDGER = null; OVERVIEW = null; LED_CASE_LIST = null; await render(); };
window.ledOpen = function (sheet, row) {
  LED_EDIT = {sheet: sheet, row: row}; LED_PREVIEW = null;
  patchInner('ledRows', ledRowsHtml());
  const t = document.getElementById('led_text');
  if (t) t.focus();
};
window.ledClose = function () {
  LED_EDIT = null; LED_PREVIEW = null;
  patchInner('ledRows', ledRowsHtml());
};
window.ledSearch = function (v) {
  LED_FILTER.q = v || '';
  patchInner('ledRows', ledRowsHtml());
  patchInner('ledCount', ledCountText());
};

function ledStOpts() {
  const seen = [];
  for (const r of ((LEDGER && LEDGER.rows) || [])) {
    const st = String(r['状态'] || '').trim();
    if (st && !seen.includes(st)) seen.push(st);
  }
  return seen.map(s => `<option ${LED_FILTER.st === s ? 'selected' : ''}>${esc(s)}</option>`).join('');
}
function ledFiltered() {
  const all = (LEDGER && LEDGER.rows) || [];
  const q = (LED_FILTER.q || '').trim();
  const st = LED_FILTER.st || 'all';
  return all.filter(r => {
    if (st !== 'all' && r['状态'] !== st) return false;
    if (!q) return true;
    return (r['案件'] + r['委托人'] + r['当前进度'] + r['案号'] + r['序号']).includes(q);
  });
}
window.ledSt = function (v) {
  LED_FILTER.st = v || 'all';
  patchInner('ledRows', ledRowsHtml());
};
function ledCountText() {
  return `${ledFiltered().length} / ${(LEDGER && LEDGER.count) || 0}`;
}
function ledRowsHtml() {
  const rows = ledFiltered();
  let h = '';
  for (const r of rows) {
    /* 案号逐个分行：台账 C 列多案号空格连排（如「…民初4194号 （2026）…执保525号」），
       按「（20xx年）」边界拆开各占一行，避免与行标签挤在一起断行难看（用户截图反馈） */
    const cnos = (r['案号'] || '').trim().split(/(?=（20\d{2}）)/).map(s => s.trim()).filter(Boolean);
    h += `<tr><td><b>${esc(r['案件'])}</b>
        <div class="hint">${esc(r.sheet)} r${r.row}</div>
        ${cnos.map(c => `<div class="hint">${esc(c)}</div>`).join('')}</td>
      <td>${esc(r['委托人'])}</td>
      <td>${r['状态']
          ? `<span class="pill${r['状态合规'] ? '' : ' pill-warn'}"
              ${r['状态合规'] ? '' : 'title="这句写法不在台账「维护说明」页规定的取值内（历史写法）"'}>${esc(r['状态'])}</span>`
            + (r['状态合规'] ? '' : '<div class="hint dim">旧写法</div>')
          : '<span class="dim">（空）</span>'}</td>
      <td>${r['当前进度'] ? clip(r['当前进度'], 340) : '<span class="dim">（空）</span>'}
        ${r['进度日志'] ? `<div class="hint">日志 ${esc(String(r['进度日志']).split('\n').length)} 条</div>` : ''}</td>
      <td><button class="act mini" onclick="ledOpen('${jsq(r.sheet)}',${r.row})">改进度/状态</button></td></tr>`;
    if (LED_EDIT && LED_EDIT.sheet === r.sheet && LED_EDIT.row === r.row) h += ledEditorRow(r);
  }
  if (!rows.length) h += '<tr><td colspan="5" class="dim">没有匹配的台账行。</td></tr>';
  return h;
}

function ledEditorRow(r) {
  const pool = (LEDGER && LEDGER.status_pool) || [];
  const cur = r['状态'] || '';
  const offOpt = (cur && pool.indexOf(cur) < 0)
    ? `<option value="${esc(cur)}">${esc(cur)}（现状，非规定取值）</option>` : '';
  const stOpts = pool.map(v =>
    `<option value="${esc(v)}"${v === cur ? ' selected' : ''}>${esc(v)}</option>`).join('');
  return `<tr><td colspan="5"><div class="editbox">
    <div class="row"><b>改进度 / 状态：${esc(r['案件'])}</b>
      <span class="dim">（${esc(r.sheet)} 第 ${r.row} 行）</span>
      <span style="flex:1"></span>
      <button class="act ghost mini" onclick="ledPreview()">预览（dry-run）</button>
      <button class="act mini" onclick="ledApply()">确认写入</button>
      <button class="act ghost mini" onclick="ledClose()">取消</button></div>
    <textarea id="led_text" rows="2" style="width:100%"
      placeholder="例：已寄出要素式起诉状，等法院受理（只想改状态可留空）"></textarea>
    <div class="row">
      <label class="dim">最新状态</label>
      <select id="led_status"><option value="">（不改）</option>${offOpt}${stOpts}</select>
      <input id="led_date" placeholder="回迁日期 MM.DD（默认今天）" style="width:200px">
      <span class="hint">旧进度会自动挪到「进度日志」列；改状态会另留一行「【状态】旧→新」。
        状态下拉里只有台账「维护说明」页规定的取值。</span></div>
    <div id="ledOut"></div></div></td></tr>`;
}

window.ledPreview = async function () {
  const out = $('#ledOut');
  const t = ($('#led_text').value || '').trim();
  const st = ($('#led_status') && $('#led_status').value || '').trim();
  if (!t && !st) {
    out.innerHTML = '<div class="red">请写新进度，或选一个新状态（两样至少填一样）。</div>';
    return;
  }
  out.innerHTML = '<div class="dim">校验中…</div>';
  try {
    const r = await post('/api/ledger/progress', {
      sheet: LED_EDIT.sheet, row: LED_EDIT.row, text: t, status: st,
      date: ($('#led_date').value || '').trim() || null, confirm: false});
    r._text = t; r._status = st;       // 写入前核对内容是否改过
    LED_PREVIEW = r;
    const p = r.plan || {};
    const jNew = [p.J_entry_added, p.J_status_added].filter(Boolean);
    out.innerHTML =
      `<div class="ok">当前进度：${esc(p.I_old || '（空）')} → ${esc(p.I_new || '（本次不改）')}</div>`
      + (p.K_new ? `<div class="ok">最新状态：${esc(p.K_old || '（空）')} → ${esc(p.K_new)}</div>` : '')
      + `<div class="dim">进度日志将新增：${esc(jNew.join(' ｜ ') || '（无）')}</div>
      <div class="dim">台账状态：${r.probe && r.probe.ok ? '✓ 可写' : '⛔ 不可写'}
        ｜点「确认写入」才会真正落盘（先自动备份台账）</div>` +
      (p.warnings || []).map(w => `<div class="warn">⚠️ ${esc(w)}</div>`).join('');
  } catch (e) { out.innerHTML = `<div class="red">预览失败：${esc(e.message)}</div>`; }
};

window.ledApply = async function () {
  const out = $('#ledOut');
  const t = ($('#led_text').value || '').trim();
  const st = ($('#led_status') && $('#led_status').value || '').trim();
  if (!LED_PREVIEW) { out.innerHTML = '<div class="red">请先点「预览」核对一遍。</div>'; return; }
  if (t !== LED_PREVIEW._text || st !== LED_PREVIEW._status) {
    out.innerHTML = '<div class="red">内容改过了，请重新点「预览」再写入（防写错）。</div>';
    return;
  }
  out.innerHTML = '<div class="dim">写入中（先备份台账）…</div>';
  try {
    const r = await post('/api/ledger/progress', {
      sheet: LED_EDIT.sheet, row: LED_EDIT.row, text: t, status: st,
      date: ($('#led_date').value || '').trim() || null,
      confirm: true, expect: LED_PREVIEW.expect});
    LED_EDIT = null; LED_PREVIEW = null; LEDGER = null; OVERVIEW = null;
    CASES = null;   // 批 68 审计补：案件列表页带台账摘要，回写后快照必须失效
    await render();
    toast('已写入台账（旧进度已挪到日志列）｜备份 ' + String(r.backup || '').split('\\').pop());
    if (r.audit && r.audit.length) {
      alert('注意：这次写入发现目标格之外还有 ' + r.audit.length + ' 处变化，请核对台账：\n'
        + r.audit.slice(0, 5).map(x => `${x[0]} r${x[1]} c${x[2]}：${x[3]} → ${x[4]}`).join('\n'));
    }
  } catch (e) {
    const b = e.body || {};
    if (b.conflict) {
      out.innerHTML = `<div class="red"><b>没有写入：台账已经被别处改动过</b>
          （不再是你刚看到的样子）</div>
        <div class="hint">为防覆盖，本次不动台账。核对下面的差异后再重新预览。</div>
        <table><tr><th>字段</th><th>你看到的</th><th>台账现有</th></tr>` +
        ((b.check && b.check.hard) || []).map(x =>
          `<tr><td>${esc(x['字段'])}</td><td class="dim">${esc(x['页面看到'])}</td>
           <td>${esc(x['台账现有'])}</td></tr>`).join('') +
        `</table><div class="row">
          <button class="act ghost mini" onclick="ledPreview()">重新预览（以台账现有为基准）</button></div>`;
    } else if (b.busy) {
      out.innerHTML = `<div class="red"><b>没有写入：台账正被占用</b></div>
        <div class="hint">${esc(e.message)}</div>
        <div class="ok">已排进「待同步队列」${b.queued ? '（' + esc(b.queued.id) + '）' : ''}
          —— 等 WPS 里关掉台账后，到本页下方点「重试」即可补写。</div>
        <div class="row"><button class="act ghost mini" onclick="ledgerReload()">刷新队列</button></div>`;
    } else {
      out.innerHTML = `<div class="red">写入失败：${esc(e.message)}</div>`;
    }
  }
};

window.pendRetry = async function (id) {
  try {
    const r = await post('/api/ledger/pending/retry', {id: id || null, confirm: true});
    LEDGER = null; OVERVIEW = null; CASES = null;   // 批 68 审计补：同上，补 CASES 失效
    await render();
    const s = r.result || {};
    toast(`重试：成功 ${(s.ok || []).length}｜冲突 ${(s.conflict || []).length}｜仍占用 ${(s.busy || []).length}`);
  } catch (e) { alert('重试失败：' + e.message); }
};
window.pendDrop = async function (id) {
  if (!confirm('从待同步队列移除这条？\n（只移除排队记录，不动台账）')) return;
  try {
    await post('/api/ledger/pending/drop', {id: id, confirm: true});
    LEDGER = null; await render();
  } catch (e) { alert('移除失败：' + e.message); }
};

async function viewLedger() {
  const d = await loadLedger(!LEDGER);
  const probe = d.probe || {}, pend = d.pending || [];
  let h = `<div class="card"><h2>台账进展（受控回写 · 改前自动备份 · 旧值挪到日志列）</h2>
    <div class="row">
      <span class="pill ${probe.ok ? 'pill-ok' : 'pill-red'}">
        ${probe.ok ? '✓ 台账可写' : '⛔ 台账当前不可写'}</span>
      <span class="dim">检测：${esc(probe.checked_at || '—')}</span>
      <button class="act ghost mini" onclick="ledgerReload()">重新检测</button></div>
    ${(probe.reasons || []).length
      ? '<div class="warn">' + probe.reasons.map(esc).join('；') + '</div>' : ''}
    <div class="hint">台账（WPS 云文档，与带教共享）是唯一事实源；这里能改「当前进度」列
      （旧内容自动挪到「进度日志」列）与「最新状态」列（只能从台账「维护说明」页规定的取值里选，
      改完在日志留一行「【状态】旧→新」）。若台账被别处改过，只报差异、不覆盖。</div></div>`;

  if (pend.length) {
    h += `<div class="card"><h2 class="warn">待同步队列（${pend.length} 条 · 台账被占用时自动排队）</h2>
      <table><tr><th>时间</th><th>案件</th><th>要写入的进度</th><th>尝试</th><th>原因</th><th></th></tr>` +
      pend.map(i => `<tr><td class="dim">${esc(i.created_at || '')}</td>
        <td>${esc(i['案件'] || '')}<div class="hint">${esc(i.sheet || '')} r${esc(i.row)}</div></td>
        <td>${esc(i.text || '')}</td><td>${i.tries || 0}</td>
        <td class="dim">${esc((i.last_error || '').slice(0, 60))}</td>
        <td><button class="act mini" onclick="pendRetry('${jsq(i.id)}')">重试</button>
          <button class="act ghost mini" onclick="pendDrop('${jsq(i.id)}')">移除</button></td></tr>`).join('') +
      `</table><div class="row"><button class="act" onclick="pendRetry('')">全部重试</button>
        <span class="hint">队列文件：${esc(d.pending_path || '')}</span></div></div>`;
  }

  const off = d.status_off_pool || [];
  const all = (d.rows || []).length;
  h += `<div class="card"><h2>案件进度（<span id="ledCount">${ledCountText()}</span> 行 · 台账只读浏览）</h2>
    <div class="row"><input id="lq" placeholder="搜索案件 / 委托人 / 进度…（边打边筛）" style="width:300px"
      value="${esc(LED_FILTER.q)}"
      oninput="ledSearch(this.value)" onkeyup="ledSearch(this.value)"
      oncompositionend="ledSearch(this.value)" onchange="ledSearch(this.value)"
      onkeydown="if(event.key==='Enter'){ledSearch(this.value);}"><select onchange="ledSt(this.value)" style="width:170px"><option value="all">状态：全部</option>${ledStOpts()}</select></div>
    <table><tr><th style="width:26%">案件</th><th>委托人</th><th>最新状态</th>
      <th style="width:30%">当前进度</th><th></th></tr>
      <tbody id="ledRows">${ledRowsHtml()}</tbody></table>
    <div class="hint">共 ${all} 行。搜索在本地过滤、只重绘表格（不打字时重建输入框，也不重新加载页面）。</div>
    ${off.length ? `<details class="hint" style="margin-top:8px">
      <summary style="cursor:pointer">另有 ${off.length} 行「最新状态」是台账规定外的写法（历史写法，工作台不会自动改）</summary>
      <table style="margin-top:6px"><tr><th>表/行</th><th>案件</th><th>现在的写法</th></tr>` +
      off.map(x => `<tr><td class="dim">${esc(x.sheet)} r${x.row}</td>
        <td>${esc(x['案件'])}</td><td class="warn">${esc(x['状态'])}</td></tr>`).join('') +
      `</table><div class="hint">要改成规定取值，点上面那行的「改进度/状态」，在下拉里选一个。</div>
      </details>` : ''}</div>`;
  return h;
}

/* ---------------- 客户（聚合视图 + 补充档案，批 12/13） ---------------- */
let CL_EDIT = null, CL_FILTER = {q: ''};
window.clEdit = function (name) { CL_EDIT = name; patchInner('clRows', clRowsHtml()); };
window.clCancel = function () { CL_EDIT = null; patchInner('clRows', clRowsHtml()); };
window.clSearch = function (v) {
  CL_FILTER.q = v || '';
  patchInner('clRows', clRowsHtml());
  patchInner('clCount', clCountText());
};
window.clSave = async function (name) {
  const g = id => ($('#' + id).value || '').trim();
  try {
    await post('/api/clients_ext', {client_key: name, confirm: true,
      patch: {contact: g('ce_contact'), phone: g('ce_phone'),
              wechat: g('ce_wechat'), note: g('ce_note')}});
    CL_EDIT = null; OVERVIEW = null; await render();
    toast('已保存客户档案：' + name);
  } catch (e) { alert('保存失败：' + e.message); }
};

function clFiltered() {
  const cls = (OVERVIEW && OVERVIEW.clients) || {items: []};
  const q = (CL_FILTER.q || '').trim();
  return (cls.items || []).filter(x => !q || x.name.includes(q));
}
function clCountText() {
  const cls = (OVERVIEW && OVERVIEW.clients) || {count: 0, with_ext: 0};
  return `${clFiltered().length} / ${cls.count || 0} 家`;
}
function clRowsHtml() {
  let h = '';
  for (const c of clFiltered()) {
    const e = c.ext || {};
    const hasExt = !!(e.contact || e.phone || e.wechat || e.note);
    const tasks = (c.tasks || []).length
      ? (c.tasks || []).map(t => `<div class="row"><span class="badge ${PRIO_CLS[t.priority] || 'p0'}">${esc(t.priority)}</span>
          ${esc(t.title)}<span class="badge ${STAT_CLS[t.status] || 's0'}">${esc(t.status)}</span>
          <span class="${t.逾期 ? 'red' : 'dim'}">${esc(t.due_date || '未排期')}</span></div>`).join('')
      : '<span class="dim">—</span>';
    const cases = (c.cases || []).map(x =>
      `<div class="dim">${clip(x['案件'], 330)} <span class="pill">${esc(x['状态'] || '未填')}</span></div>`).join('');   // 批 77：「—」看不懂，改「未填」
    h += `<tr><td><b>${esc(c.name)}</b><div class="hint">匹配词：${esc(c.core)}</div></td>
      <td>${hasExt
          ? `${[e.contact, e.phone].filter(Boolean).map(esc).join(' ')}
             ${e.wechat ? '<div class="dim">微信 ' + esc(e.wechat) + '</div>' : ''}
             ${e.note ? '<div class="dim">' + esc(e.note) + '</div>' : ''}`
          : '<span class="dim">—</span>'}
        <div><button class="act ghost mini" onclick="clEdit('${jsq(c.name)}')">
          ${hasExt ? '编辑' : '添加'}</button></div></td>
      <td>${c.case_count}</td><td class="${c.open_tasks ? 'warn' : 'dim'}">${c.open_tasks}</td>
      <td>${cases || '<span class="dim">—</span>'}${c.open_tasks ? '<div style="margin-top:4px">' + tasks + '</div>' : ''}</td></tr>`;
    if (CL_EDIT === c.name) h += clEditorRow(c);
  }
  if (!h) h = '<tr><td colspan="5" class="dim">没有匹配的客户。</td></tr>';
  return h;
}

function clEditorRow(c) {
  const e = c.ext || {};
  return `<tr><td colspan="5"><div class="editbox">
    <div class="row"><b>补充档案：${esc(c.name)}</b>
      <input id="ce_contact" placeholder="联系人" value="${esc(e.contact || '')}" style="width:120px">
      <input id="ce_phone" placeholder="电话" value="${esc(e.phone || '')}" style="width:150px">
      <input id="ce_wechat" placeholder="微信" value="${esc(e.wechat || '')}" style="width:150px">
      <input id="ce_note" placeholder="备注" value="${esc(e.note || '')}" style="width:200px">
      <button class="act mini" onclick="clSave('${jsq(c.name)}')">保存</button>
      <button class="act ghost mini" onclick="clCancel()">取消</button></div>
    <div class="hint">台账里没有的客户信息（联系人/电话/微信/备注）存工作台自建库；
      案件与费用仍以台账为事实源。字段清空即隐藏。</div></div></td></tr>`;
}

async function viewClients() {
  const o = OVERVIEW || (OVERVIEW = await api('/api/overview'));
  const cls = o.clients || {count: 0, items: [], with_ext: 0};
  let h = `<div class="card"><h2>客户（<span id="clCount">${clCountText()}</span> · 已补档案 ${cls.with_ext || 0} 家）</h2>
    <div class="hint">口径：以台账「委托人」列去重聚合（只读派生），关联案件来自台账行；
      联系方式等台账没有的信息存工作台自建库。</div>
    <div class="row"><input id="cq" placeholder="搜索客户名…（边打边筛）" style="width:260px"
      value="${esc(CL_FILTER.q)}"
      oninput="clSearch(this.value)" onkeyup="clSearch(this.value)"
      oncompositionend="clSearch(this.value)" onchange="clSearch(this.value)"></div>
    <table><tr><th>客户</th><th>联系方式（工作台自建）</th><th>关联案件</th>
      <th>未完成任务</th><th>案件明细</th></tr>
      <tbody id="clRows">${clRowsHtml()}</tbody></table></div>`;
  return h;
}

/* ---------------- 任务（批 12，工作台自建库） ---------------- */
async function loadTasks(force) {
  if (!TASKS || force) TASKS = await api('/api/tasks');
  return TASKS;
}
/* 批 14：任务「关联案件」不再用文件系统案件夹下拉，改**取自台账行**（台账 = 唯一事实源，
   这样任务、台账行、客户聚合三处口径一致）。取不到台账时降级为空列表，不阻塞任务页。 */
let LED_CASE_LIST = null;
async function ledgerCaseList(force) {
  if (LED_CASE_LIST && !force) return LED_CASE_LIST;
  let list = [];
  try {
    const L = await loadLedger();
    const seen = {};
    for (const r of ((L && L.rows) || [])) {
      const nm = String(r['案件'] || '').trim();
      if (!nm || seen[nm]) continue;
      seen[nm] = 1;
      list.push({name: nm, client: String(r['委托人'] || '').trim(),
                 sheet: r['sheet'], row: r['row']});
    }
  } catch (e) { list = []; }
  LED_CASE_LIST = list;
  return list;
}
function caseOptsHtml(cur) {
  const list = LED_CASE_LIST || [];
  let h = '<option value="">（不关联案件）</option>', hit = false;
  for (const it of list) {
    const sel = (it.name === cur) ? ' selected' : '';
    if (sel) hit = true;
    h += `<option value="${esc(it.name)}"${sel}>${esc(it.name)}${it.client ? '｜' + esc(it.client) : ''}</option>`;
  }
  if (cur && !hit) {
    h += `<option value="${esc(cur)}" selected>${esc(cur)}（台账现无此行）</option>`;
  }
  return h;
}
async function afterTaskChange(msg) {
  TASKS = null; OVERVIEW = null;
  toast(msg || '已更新');
  await render();
}
window.taskSet = async function (id, patch) {
  if (patch && patch.status === '已完成') {              // 完成动作统一二次确认（按钮/下拉全覆盖）
    const t = ((TASKS && TASKS.tasks) || []).find(x => x.id === id);
    if (!confirm('确认完成任务「' + ((t && t.title) || '#' + id) + '」？\n（可在任务页「已完成」筛选段随时回看；选回「待办」即撤销）')) return;
  }
  try { await post('/api/tasks/update', {id: id, patch: patch, confirm: true}); await afterTaskChange('已更新任务'); }
  catch (e) { alert('更新失败：' + e.message); }
};
window.taskDone = async function (id, title) { await taskSet(id, {status: '已完成'}); };
window.taskRevert = async function (id, title) {
  try { await post('/api/tasks/update', {id: id, patch: {status: '待办'}, confirm: true});
    await afterTaskChange('已恢复为待办'); }
  catch (e) { alert('恢复失败：' + e.message); }
};
window.taskDel = async function (id, title) {
  if (!confirm('确定删除任务「' + title + '」？\n（只删工作台自己的任务记录，不动案件材料）')) return;
  try { await post('/api/tasks/delete', {id: id, confirm: true}); await afterTaskChange('已删除任务'); }
  catch (e) { alert('删除失败：' + e.message); }
};
window.taskAdd = async function () {
  const g = id => $('#' + id).value;
  const title = g('t_title').trim();
  if (!title) return alert('请填任务标题');
  try {
    await post('/api/tasks', {title: title, case_ref: g('t_case'), priority: g('t_prio'),
      due_date: g('t_due'), note: g('t_note'), confirm: true});
    await afterTaskChange('已新增任务');
  } catch (e) { alert('新增失败：' + e.message); }
};
const T_SEGS = [['open', '未完成'], ['today', '今日到期'], ['week', '7 日内'], ['overdue', '逾期'],
                ['none', '未排期'], ['done', '已完成'], ['all', '全部']];

window.taskFilter = function (seg) {
  T_FILTER.seg = seg;
  patchInner('taskSegs', taskSegHtml());
  patchInner('taskRows', taskRowsHtml());
  patchInner('taskCount', taskCountText());
};
window.taskSearch = function (v) {
  T_FILTER.q = v || '';
  patchInner('taskRows', taskRowsHtml());
  patchInner('taskCount', taskCountText());
};
window.taskPrio = function (v) {
  T_FILTER.prio = v || 'all';
  patchInner('taskRows', taskRowsHtml());
  patchInner('taskCount', taskCountText());
};

function taskSegHtml() {
  return T_SEGS.map(([k, t]) =>
    `<button class="${T_FILTER.seg === k ? 'on' : ''}" onclick="taskFilter('${k}')">${t}</button>`).join('');
}
function taskFiltered() {
  const all = (TASKS && TASKS.tasks) || [];
  const today = new Date().toISOString().slice(0, 10);
  const in7 = new Date(Date.now() + 7 * 864e5).toISOString().slice(0, 10);
  const q = (T_FILTER.q || '').trim();
  return all.filter(t => {
    if (q) {
      const s = (t.title + ' ' + (t.note || '') + ' ' + (t.case_ref || ''));
      if (!s.includes(q)) return false;
    }
    if (T_FILTER.prio !== 'all' && (t.priority || '普通') !== T_FILTER.prio) return false;
    const s = T_FILTER.seg;
    if (s === 'open') return t.status !== '已完成';
    if (s === 'done') return t.status === '已完成';
    if (s === 'all') return true;
    if (t.status === '已完成') return false;
    const dd = t.due_date || '';
    if (s === 'today') return !!dd && dd <= today;
    if (s === 'week') return !!dd && dd > today && dd <= in7;
    if (s === 'overdue') return !!dd && dd < today;
    if (s === 'none') return !dd;
    return true;
  });
}
function taskCountText() {
  const st = (TASKS && TASKS.stats) || {};
  return `未完成 ${st.open || 0}／逾期 ${st.overdue || 0}／共 ${st.total || 0}`
    + `（当前显示 ${taskFiltered().length}）`;
}
function taskRowsHtml() {
  const rows = taskFiltered();
  let h = '';
  for (const t of rows) {
    const stOpts = ['待办', '进行中', '已完成'].map(x =>
      `<option ${x === t.status ? 'selected' : ''}>${x}</option>`).join('');
    const prOpts = ['普通', '重要', '紧急'].map(x =>
      `<option ${x === t.priority ? 'selected' : ''}>${x}</option>`).join('');
    h += `<tr>
      <td><b>${esc(t.title)}</b>${t.status === '已完成' && t.done_at ? ` <span class="dim">（完成于 ${esc(t.done_at.slice(5, 16))}）</span>` : ''}</td>
      <td><select onchange="taskSet(${t.id},{case_ref:this.value})" style="max-width:240px">${caseOptsHtml(t.case_ref || '')}</select></td>
      <td><select class="mini" onchange="taskSet(${t.id},{status:this.value})">${stOpts}</select></td>
      <td><select onchange="taskSet(${t.id},{priority:this.value})">${prOpts}</select></td>
      <td><input type="date" value="${esc(t.due_date || '')}"
          onchange="taskSet(${t.id},{due_date:this.value})" style="width:140px">
        ${t.逾期 ? '<span class="red">逾期</span>' : ''}</td>
      <td class="dim">${esc((t.note || '').slice(0, 40))}</td>
      <td>${t.status !== '已完成' ? `<button class="act ghost mini" onclick="taskDone(${t.id},'${jsq(t.title)}')" title="一键标记为已完成（有确认提示）">✓完成</button> ` : `<button class="act ghost mini" onclick="taskRevert(${t.id},'${jsq(t.title)}')" title="恢复为待办（done_at 自动清除）">↩恢复</button> `}<button class="act ghost mini" onclick="taskDel(${t.id},'${jsq(t.title)}')">删除</button></td></tr>`;
  }
  if (!rows.length) h = '<tr><td colspan="7" class="dim">没有符合条件的任务。</td></tr>';
  return h;
}

async function viewTasks() {
  const d = await loadTasks();
  await ledgerCaseList();
  let h = `<div class="card"><h2>任务（<span id="taskCount">${taskCountText()}</span>）</h2>
    <div class="hint">任务记录存工作台自建库（不写台账、不动案件材料）；删除只删任务记录。
      <b>关联案件取自台账行</b>（台账为唯一事实源，改这里只是给任务挂个案名，不动台账）。
      搜索与筛选在本地完成，不会打断正在输入的内容。</div>
    <div class="form-grid" style="margin-top:10px">
      <input id="t_title" placeholder="任务标题（如：阅卷并整理时间线）">
      <select id="t_case">${caseOptsHtml('')}</select>
      <select id="t_prio"><option>普通</option><option>重要</option><option>紧急</option></select>
      <input id="t_due" type="date">
      <input id="t_note" placeholder="备注">
      <button class="act" onclick="taskAdd()">＋ 新增</button>
    </div>
    <div class="row" style="margin-top:12px">
      <div class="seg" id="taskSegs">${taskSegHtml()}</div>
      <input id="tq" placeholder="搜索标题/备注/案件…（边打边筛）" style="width:240px" value="${esc(T_FILTER.q)}"
        oninput="taskSearch(this.value)" onkeyup="taskSearch(this.value)"
        oncompositionend="taskSearch(this.value)" onchange="taskSearch(this.value)">
      <select onchange="taskPrio(this.value)" style="width:130px"><option value="all">优先级：全部</option><option>普通</option><option>重要</option><option>紧急</option></select></div></div>
    <table><tr><th style="width:30%">任务</th><th>关联案件</th><th>状态</th>
      <th>优先级</th><th>截止</th><th>备注</th><th></th></tr>
      <tbody id="taskRows">${taskRowsHtml()}</tbody></table></div>`;
  return h;
}

/* ============================================================ 工作台助理（批 C 迁移自外部服务；批 45 升级）
   对话 + 智能提醒。数据源 = **六源**（台账全字段 + 任务/期限/日程/案卷索引/客户备注，
   见后端 assistant_ops._build_workbench_blocks）；LLM 走本地积分池 8788。
   ⚠️ **只读纪律（批 45 用户拍板 A 方案）**：助理**不能改任何数据**——只能读 + 回答 +
   引导去对应页面操作。前端不得给它任何写接口。
   ⚠️ **轮数以 AS_TURNS 为准**（与后端 MAX_HISTORY_TURNS 对齐）：**禁在文案里手写轮数**
   （批 45 实踩：标题改了 12、页脚还写 6，同页自相矛盾）。
   ⚠️ IME 纪律：聊天是**显式提交**场景 —— Enter 发送须带 isComposing 护栏
   （组合期间的 Enter 是拼音选词，不发送）。这与搜索框「拆护栏、5 触发点」的
   教训**不冲突**：搜索是被动过滤（input 驱动），聊天是主动提交（Enter 驱动）。
   ⚠️ 追加消息一律 DOM append，**严禁整页 render / patchInner 重建消息流**
   （否则输入框失焦 + 滚动位置丢）。 */
const AS_TURNS = 12;         // 与后端 MAX_HISTORY_TURNS 对齐；文案一律引用本常量
let AS_CHAT = [];            // 当前会话消息 [{role, content}]
let AS_BUSY = false;
let AS_REMIND = null;        // /api/assistant/reminders 快照

async function viewAssistant() {
  CURRENT_VIEW = 'assistant';
  let hist = [];
  try { hist = (await api('/api/assistant/history')).items || []; } catch (e) { /* 首次无历史 */ }
  AS_CHAT = hist.slice();
  const h = `<div class="card asCard" style="flex:1;display:flex;flex-direction:column;min-height:0">
    <h2>工作台助理 <span class="dim" style="font-weight:400;font-size:12px">最近 ${AS_TURNS} 轮会随提问一起发给模型</span></h2>
    <div class="hint">问工作台里的事：<b>案件进展 / 开庭 / 尾款 / 合同费用</b>（来自台账）、
      <b>任务 / 期限 / 日程 / 案卷目录</b>（来自工作台本地数据）。
      数据里没有的会说「工作台数据中没有记录」，不编造。<br>
      分析经<b>本地积分池</b>；案卷文件<b>正文</b>（证据/文书）与客户<b>联系方式</b>不参与发送。<br>
      <b>助理只读</b>——能查能答，<b>但不能替你改数据</b>；要登记/修改请到对应页面操作（我会告诉你去哪一页）。</div>
    <div id="asFlow" style="margin-top:10px;display:flex;
        flex-direction:column;gap:10px;padding:4px 2px;overflow-y:auto"></div>
    <div class="row" style="margin-top:10px;align-items:flex-end">
      <textarea id="asInput" rows="2" placeholder="问工作台里的事…（Enter 发送，Shift+Enter 换行）"
        style="flex:1;min-height:52px;max-height:160px;resize:vertical;padding:10px 12px;
        font:13.5px/1.6 inherit;border-radius:8px"></textarea>
      <button class="act" id="asSend" onclick="asAsk()" style="padding:12px 22px">发送</button>
      <button class="act ghost mini" onclick="asClear()">清空历史</button>
    </div>
    <div class="hint" id="asTip"></div></div>`;
  setTimeout(() => {
    asRenderFlow();
    const ta = $('#asInput');
    if (ta) {
      ta.addEventListener('keydown', (ev) => {
        // ⚠️ 必须带 isComposing 护栏（组合期的 Enter 是选词，不是发送）
        if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) {
          ev.preventDefault();
          asAsk();
        }
      });
    }
  }, 0);
  return h;
}

function asRenderFlow() {
  const el = $('#asFlow');
  if (!el) return;
  el.innerHTML = '';
  if (!AS_CHAT.length) {
    asAppend('ai', '喵，工作台数据已加载。可以问：案件进展、开庭、尾款、合同费用、任务、期限、日程、案卷目录。\n（案卷文件正文与客户联系方式我看不到；我只能查和答，不能改数据——要登记/修改我告诉你去哪一页）');
    return;
  }
  AS_CHAT.forEach(m => asAppend(m.role === 'user' ? 'me' : 'ai', m.content));
}

function asAppend(role, text) {
  const el = $('#asFlow');
  if (!el) return null;
  const div = document.createElement('div');
  const me = role === 'me';
  div.className = 'asMsg' + (me ? ' me' : '');
  div.style.cssText = 'max-width:min(760px,86%);padding:9px 13px;border-radius:10px;white-space:pre-wrap;'
    + 'word-break:break-word;font-size:13.5px;line-height:1.7;'
    + (me ? 'align-self:flex-end;background:var(--accent);color:#fff;border-bottom-right-radius:3px'
          : 'align-self:flex-start;background:var(--rule-soft);color:var(--ink);border-bottom-left-radius:3px');
  div.textContent = text;
  el.appendChild(div);
  el.scrollTop = el.scrollHeight;
  return div;
}

async function asAsk() {
  if (AS_BUSY) return;
  const ta = $('#asInput');
  const q = (ta.value || '').trim();
  if (!q) return;
  ta.value = '';
  AS_BUSY = true;
  const btn = $('#asSend'); if (btn) btn.disabled = true;
  asAppend('me', q);
  const tip = asAppend('ai', '分析中…（全量台账，约 10-30 秒）');
  if (tip) tip.style.opacity = '.65';
  const t2 = $('#asTip'); if (t2) t2.textContent = '';
  try {
    // 与后端 CHAT_TIMEOUT(180s) 对齐，多留 10s 余量（审计问题 #5 超时半）
    const r = await post('/api/assistant/chat', {q: q}, {signal: AbortSignal.timeout(190000)});
    if (tip) tip.remove();
    if (r && r.ok) {
      asAppend('ai', r.reply);
      AS_CHAT.push({role: 'user', content: q}, {role: 'assistant', content: r.reply});
    } else if (r && r.busy) {
      asAppend('ai', r.reply || '上一条还在思考，稍等一下喵');
    } else {
      const f = failHint((r && (r.error || r.reply)) || '请求失败');
      asAppend('ai', '⚠️ ' + f.why + '\n（怎么办：' + f.todo + '）');
    }
  } catch (e) {
    if (tip) tip.remove();
    const f = failHint(e);
    asAppend('ai', '⚠️ ' + f.why + '\n（怎么办：' + f.todo + '）');
  } finally {
    AS_BUSY = false;
    if (btn) btn.disabled = false;
    const t3 = $('#asTip');
    if (t3) t3.textContent = '共 ' + AS_CHAT.length + ' 条消息（只保留最近 ' + AS_TURNS + ' 轮）';
    ta.focus();
  }
}

async function asClear() {
  if (!confirm('清空工作台助理的对话历史？\n（只删本机保存的记录，不删任何案件材料）')) return;
  try { await post('/api/assistant/history_clear', {confirm: true}); } catch (e) { /* 忽略 */ }
  AS_CHAT = [];
  asRenderFlow();
  const t = $('#asTip'); if (t) t.textContent = '对话历史已清空';
}

// onclick 内联调用需挂 window（本文件惯例，见 sohanPreview / dlSeg / taskDone）
window.asAsk = asAsk;
window.asClear = asClear;

/* ---------------- 首页「今日提醒」卡：期限 + 日程 + 智能提醒 三源合一 ----------------
   设计意图：审计（09-18）指出期限/日历/任务/日程四件套概念已过载 —— 故不新建
   第五个「AI 提醒页」，而是把 LLM 提醒与已有两套规则提醒收敛到首页一处。 */
async function asRemindCard() {
  let d = AS_REMIND;
  if (!d) {
    try { d = AS_REMIND = await api('/api/assistant/reminders'); } catch (e) { d = null; }
  }
  if (!d) return '<div class="card"><h2>今日提醒</h2><div class="dim">读取失败。</div></div>';
  const st = d.state || {};
  let h = '<div class="card"><h2>今日提醒（智能分析）';
  if (d.unread) h += ` <span class="badge p2">${d.unread} 条未读</span>`;
  h += '</h2>';
  if (st.busy) {
    h += '<div class="dim">🤔 助手正在分析台账…（约 10-30 秒，页面会自动刷新）</div>';
  } else if (!d.count) {
    h += '<div class="dim">'
      + (st.last_run ? '上次分析没有产出提醒。' : '还没分析过台账。')
      + '</div>';
  } else {
    h += '<table><tr><th>级别</th><th>提醒</th><th>状态</th><th></th></tr>' + d.items.map(x => {
      const pcls = x.priority === 'high' ? 'p2' : (x.priority === 'low' ? 'p0' : 'p1');
      const plab = x.priority === 'high' ? '紧急' : (x.priority === 'low' ? '低' : '普通');
      const chg = x.change === 'new' ? '<span class="badge p2">新</span>'
        : (x.change === 'updated' ? '<span class="badge p1">变</span>' : '');
      return `<tr${x.read ? ' style="opacity:.55"' : ''}>
        <td><span class="badge ${pcls}">${plab}</span></td>
        <td>${esc(x.text)} ${chg}</td>
        <td>${x.dismissed ? '<span class="dim">已忽略</span>'
                          : (x.read ? '<span class="dim">已读</span>' : '<span class="ok">未读</span>')}</td>
        <td>${x.read ? '' : `<button class="act ghost mini" onclick="asMark('${x.id}',false)">知道了</button>`}
            ${x.dismissed ? '' : `<button class="act ghost mini"
              onclick="asMark('${x.id}',true)">不再提醒</button>`}</td></tr>`;
    }).join('') + '</table>';
  }
  h += `<div class="hint">${st.last_run ? '上次分析 ' + esc(st.last_run.slice(5, 16))
        + (st.last_count != null ? ' · ' + st.last_count + ' 条' : '') : '尚未分析'}
      ${st.last_error ? ' · <span class="red">上次失败：' + esc(String(st.last_error).slice(0, 60)) + '</span>' : ''}
      ${(d.llm && !d.llm.ok) ? '<br><span class="red">' + esc(d.llm.msg) + '</span>' : ''}
      <br>提醒由 <b>LLM 分析台账</b>生成，<b>仅供参考，以台账为准</b>。</div>
    <div class="row" style="margin-top:6px">
      <button class="act ghost mini" onclick="asRun()">🔍 立即分析</button>
      <button class="act ghost mini" onclick="asIgnorePanel()">忽略名单（${(d.ignore || []).length}）</button>
    </div><div id="asIgnoreBox"></div></div>`;
  return h;
}

async function asRun() {
  try {
    const r = await post('/api/assistant/remind_run', {});
    if (r && r.started) {
      toast('已开始分析台账…（约 10-30 秒）');
      asPollUntilDone();
    } else {
      toast((r && r.msg) || '上一轮还在跑');
    }
  } catch (e) { toast('启动失败：' + (e.message || e)); }
}

let AS_POLL = null;
function asPollUntilDone() {
  if (AS_POLL) clearInterval(AS_POLL);
  let n = 0;
  AS_POLL = setInterval(async () => {
    n += 1;
    try {
      // 超时兜底：后端对话/分析 180s 上限，前端请求比它多留 10s 余量——
      // 防「后端已释放锁但前端 fetch 永久挂起」时轮询卡死（审计问题 #5 超时半）。
      // 轮询本身有 40 轮 × 3s ≈ 120s 上限，此处是**单次请求**的超时保护。
      AS_REMIND = await api('/api/assistant/reminders',
                            {signal: AbortSignal.timeout(190000)});
      if (TAB === 'home') await render();
      if (!AS_REMIND.state.busy || n > 40) {
        clearInterval(AS_POLL); AS_POLL = null;
        if (AS_REMIND.state.last_status === 'error') {
          toast('分析失败：' + (AS_REMIND.state.last_error || ''));
        } else {
          toast('分析完成：' + AS_REMIND.count + ' 条提醒');
        }
        if (TAB === 'home') await render();
      }
    } catch (e) { /* 轮询失败静默，下轮再试 */ }
  }, 3000);
}

async function asMark(id, dismissed) {
  if (dismissed && !confirm('「不再提醒」后，这条提醒不再出现在首页（同文案跨轮也不重复推）。\n继续？')) return;
  try {
    await post('/api/assistant/mark', {id: id, dismissed: !!dismissed});
    AS_REMIND = await api('/api/assistant/reminders');
    if (TAB === 'home') await render();
    updateBell();
  } catch (e) { toast('操作失败：' + (e.message || e)); }
}

async function asIgnorePanel() {
  const box = $('#asIgnoreBox');
  if (!box) return;
  if (box.innerHTML) { box.innerHTML = ''; return; }        // 再点收起
  const d = AS_REMIND || (AS_REMIND = await api('/api/assistant/reminders'));
  const items = d.ignore || [];
  box.innerHTML = '<div style="margin-top:8px;padding:8px 10px;background:var(--rule-soft);border-radius:8px">'
    + '<div class="hint" style="margin:0 0 6px">这些词命中的提醒不再出现（按当事人姓名/关键词）。'
    + '只影响提醒，不影响台账。</div>'
    + (items.length ? items.map(k => `<span class="pill" style="margin-right:6px">${esc(k)}
        <a href="javascript:;" onclick="asIgnore(${jsq(k)},true)" style="margin-left:6px">×</a></span>`).join('')
      : '<span class="dim">（空）</span>')
    + '<div class="row" style="margin-top:8px"><input id="asIgNew" placeholder="加一个词…" style="width:200px">'
    + '<button class="act ghost mini" onclick="asIgnore($(\'#asIgNew\').value,false)">添加</button></div></div>';
}

async function asIgnore(word, remove) {
  if (!word) return;
  try {
    await post('/api/assistant/ignore', remove ? {remove: word} : {add: word});
    AS_REMIND = await api('/api/assistant/reminders');
    await asIgnorePanel();                                  // 收起后重开以刷新
    await asIgnorePanel();
    toast(remove ? '已移出忽略名单' : '已加入忽略名单');
  } catch (e) { toast('操作失败：' + (e.message || e)); }
}

/* 导航铃铛：未读智能提醒 + 临期期限（挂在既有心跳上，不新增轮询请求） */
let BELL_N = 0;
async function updateBell() {
  try {
    const d = await api('/api/assistant/reminders');
    AS_REMIND = d;
    const dl = (OVERVIEW && OVERVIEW.deadlines) ? OVERVIEW.deadlines.due_count : 0;
    BELL_N = (d.unread || 0) + dl;
  } catch (e) { /* 静默 */ }
  const el = document.getElementById('bell');
  if (el) {
    el.textContent = BELL_N ? String(BELL_N) : '';
    el.style.display = BELL_N ? 'inline-flex' : 'none';
  }
}

/* ---------------- 法律计算器（批 49） ----------------
   全部纯本地计算：零 API 调用、零外发、无框架无 CDN（同全站铁律）。
   法条依据（均为现行有效，核验日 2026-10-09 · 元典法规库）：
   ① 案件受理费分段：《诉讼费用交纳办法》（国务院令第481号，2007-04-01 施行）第十三条第（一）项
   ② 执行/保全申请费分段：同办法第十四条第（一）（二）项
   ③ 迟延履行加倍利息：法释〔2014〕8号第一条（日万分之一点七五）
   借款利息为通用单利公式（口径自选 ÷360/÷365）；天数、大写为纯数学工具。 */

const FEE_SEG_CASE = [[1e4, null], [1e5, 0.025], [2e5, 0.02], [5e5, 0.015], [1e6, 0.01],
  [2e6, 0.009], [5e6, 0.008], [1e7, 0.007], [2e7, 0.006], [Infinity, 0.005]];
const FEE_SEG_EXEC = [[1e4, null], [5e5, 0.015], [5e6, 0.01], [1e7, 0.005], [Infinity, 0.001]];
const FEE_SEG_PRESERVE = [[1e3, null], [1e5, 0.01], [Infinity, 0.005]];
const PRESERVE_CAP = 5000;

/* 解析金额：支持「10万」「100000」「1,234.56」等写法 */
function calcParseAmt(s) {
  s = String(s || '').trim().replace(/[，,¥￥\s]/g, '');
  if (!s) return 0;
  const m = s.match(/^([\d.]+)万$/);
  if (m) return Number(m[1]) * 10000;
  const n = Number(s);
  return isNaN(n) || n < 0 ? 0 : n;
}
function calcDays(d1, d2) {
  if (!d1 || !d2) return null;
  const a = new Date(d1 + 'T00:00:00'), b = new Date(d2 + 'T00:00:00');
  if (isNaN(a) || isNaN(b)) return null;
  return Math.round((b - a) / 86400000);
}
function calcFmt(n) {
  if (n == null || isNaN(n)) return '—';
  return n.toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
}
/* 分段累计：segs=[[上限,费率],…] 首段费率 null = 固定 base 元 */
function feeSegCalc(amt, segs, base) {
  if (!(amt > 0)) return null;
  let lower = 0, total = 0, rows = [];
  for (const seg of segs) {
    const cap = seg[0], rate = seg[1];
    if (amt <= lower) break;
    const span = Math.min(amt, cap) - lower;
    const sum = rate == null ? base : span * rate;
    total += sum;
    rows.push({from: lower, to: Math.min(amt, cap), span, rate, sum});
    lower = cap;
  }
  return {total: Math.round(total * 100) / 100, rows};
}
function segLabel(r) {
  const wan = x => x >= 1e8 ? (x / 1e8) + ' 亿' : (x / 1e4) + ' 万';
  const to = r.to === Infinity ? Infinity : r.to;
  if (r.from === 0 && r.rate == null) return wan(to) + '元以下：每件固定';
  const hi = to === Infinity ? '以上' : '–' + wan(to) + '的部分';
  return wan(r.from) + (r.from === 0 ? '元' : '') + hi;
}
/* 迟延履行加倍利息（法释〔2014〕8号第一条）：本金×日万分之1.75×天数 */
function delayInterestCalc(amt, days) {
  if (!(amt > 0) || !(days > 0)) return null;
  return Math.round(amt * 0.000175 * days * 100) / 100;
}
/* 通用单利（口径自选）：本金×年利率÷basis×天数 */
function loanInterestCalc(amt, ratePct, days, basis) {
  if (!(amt > 0) || !(ratePct > 0) || !(days > 0)) return null;
  return Math.round(amt * (ratePct / 100) / basis * days * 100) / 100;
}
/* 人民币大写（会计口径：中间连续零只写一个；角后不带整字；角零分非零加「零」） */
const RMB_D = ['零', '壹', '贰', '叁', '肆', '伍', '陆', '柒', '捌', '玖'];
const RMB_U = ['', '拾', '佰', '仟'];
function rmbCap(x) {
  x = Number(x);
  if (isNaN(x) || x < 0) return '';
  const fen = Math.round(x * 100);
  if (fen === 0) return '零元整';
  const yuan = Math.floor(fen / 100);
  const jiao = Math.floor((fen % 100) / 10), fen2 = fen % 10;
  let s = '';
  if (yuan > 0) {
    const groups = [];
    let y = yuan;
    while (y > 0) { groups.unshift(y % 10000); y = Math.floor(y / 10000); }
    const bigs = ['', '万', '亿', '万亿'];
    groups.forEach((g, gi) => {
      const big = bigs[groups.length - 1 - gi];
      if (g === 0) {
        const restNZ = groups.slice(gi + 1).some(v => v > 0);
        if (restNZ && s && s.slice(-1) !== '零') s += '零';
        return;
      }
      const ds = String(g).padStart(4, '0');
      let gStr = '', zeroPending = false;
      for (let i = 0; i < 4; i++) {
        const d = Number(ds[i]);
        if (d === 0) { zeroPending = true; continue; }
        if (zeroPending && (s || gStr)) gStr += '零';
        gStr += RMB_D[d] + RMB_U[3 - i];
        zeroPending = false;
      }
      s += gStr + big;
    });
    s += '元';
  }
  if (jiao > 0 && fen2 > 0) s += RMB_D[jiao] + '角' + RMB_D[fen2] + '分';
  else if (jiao > 0) s += RMB_D[jiao] + '角';
  else if (fen2 > 0) s += (yuan > 0 ? '零' : '') + RMB_D[fen2] + '分';
  else s += '整';
  return s;
}
/* 结果块（金额卡共用）：合计 + 大写 + 分段明细表 */
function feeResultHtml(r, cap) {
  if (!r) return '<div class="dim">输入金额后即时计算。</div>';
  const t = cap ? Math.min(r.total, cap) : r.total;
  const capped = cap && r.total > cap;
  let h = `<div class="ok" style="font-size:18px"><b>¥ ${calcFmt(t)}</b>
    <span style="font-size:13px;color:var(--muted)">（${rmbCap(t)}）</span></div>`;
  h += '<table style="margin-top:6px"><tr><th>分段</th><th style="width:96px">部分金额</th><th style="width:64px">费率</th><th style="width:96px">段合计</th></tr>' +
    r.rows.map(x => `<tr><td class="dim">${esc(segLabel(x))}</td>
      <td>${calcFmt(x.span)}</td>
      <td class="dim">${x.rate == null ? '固定' : (x.rate * 100).toFixed(x.rate * 100 % 1 ? 1 : 0) + '%'}</td>
      <td>${calcFmt(x.sum)}</td></tr>`).join('') + '</table>';
  if (capped) h += `<div class="warn">按办法第十四条第（二）项：保全申请费最多不超过 5000 元（已按上限计）。</div>`;
  return h;
}
/* 律师费参考（批 55）：《厦门市律师服务收费指引》附件一 · 民诉一个审级 · **区间标准**。
   分段 [上限, 低费率, 高费率]；首段 10 万以下 = 固定 6000~10000 元。 */
const LAWYER_SEGS = [[1e5, null, null], [1e6, .06, .09], [5e6, .04, .06], [1e7, .02, .04],
                     [5e7, .01, .03], [1e8, .005, .015], [Infinity, .0025, .01]];
function lawyerFeeCalc(amt) {
  if (!(amt > 0)) return null;
  let lower = 0, lo = 0, hi = 0, rows = [];
  for (const seg of LAWYER_SEGS) {
    const cap = seg[0], rLo = seg[1], rHi = seg[2];
    if (amt <= lower) break;
    const span = Math.min(amt, cap) - lower;
    const sumLo = (rLo == null) ? 6000 : span * rLo;
    const sumHi = (rHi == null) ? 10000 : span * rHi;
    lo += sumLo; hi += sumHi;
    rows.push({from: lower, to: Math.min(amt, cap), span, rLo, rHi, sumLo, sumHi});
    lower = cap;
  }
  return {lo: Math.round(lo), hi: Math.round(hi), rows};
}
function lawyerFeeHtml(r) {
  if (!r) return '<div class="dim">输入争议标的额后即时计算（输出下限~上限区间）。</div>';
  const pct = x => (x * 100).toFixed(x * 100 % 1 ? 1 : 0) + '%';
  let h = `<div class="ok" style="font-size:18px"><b>¥ ${calcFmt(r.lo)} ~ ${calcFmt(r.hi)}</b>
    <span style="font-size:13px;color:var(--muted)">（${rmbCap(r.lo)} ~ ${rmbCap(r.hi)}）</span></div>`;
  h += '<table style="margin-top:6px"><tr><th>分段</th><th style="width:96px">部分金额</th>'
    + '<th style="width:110px">费率区间</th><th style="width:150px">段合计区间</th></tr>'
    + r.rows.map(x => `<tr><td class="dim">${esc(segLabel(x))}</td><td>${calcFmt(x.span)}</td>
      <td class="dim">${x.rLo == null ? '固定' : pct(x.rLo) + ' ~ ' + pct(x.rHi)}</td>
      <td>${calcFmt(x.sumLo)} ~ ${calcFmt(x.sumHi)}</td></tr>`).join('') + '</table>';
  return h;
}
window.calcFeeLawyerRun = function () {
  const r = lawyerFeeCalc(calcParseAmt(document.getElementById('cL').value));
  patchInner('cLOut', lawyerFeeHtml(r));
};
window.calcFeeCaseRun = function() {
  const r = feeSegCalc(calcParseAmt(document.getElementById('cA').value), FEE_SEG_CASE, 50);
  patchInner('cAOut', feeResultHtml(r));
};
window.calcFeeExecRun = function() {
  const r = feeSegCalc(calcParseAmt(document.getElementById('cE').value), FEE_SEG_EXEC, 50);
  patchInner('cEOut', feeResultHtml(r));
};
window.calcFeePreserveRun = function() {
  const r = feeSegCalc(calcParseAmt(document.getElementById('cP').value), FEE_SEG_PRESERVE, 30);
  patchInner('cPOut', feeResultHtml(r, PRESERVE_CAP));
};
window.calcDelayRun = function() {
  const amt = calcParseAmt(document.getElementById('dD').value);
  const days = calcDays(document.getElementById('dS').value, document.getElementById('dT').value);
  const out = document.getElementById('dOut');
  if (!(amt > 0) || days == null) { out.innerHTML = '<div class="dim">填「未清偿金额」和起止日期后即时计算。</div>'; return; }
  if (days < 0) { out.innerHTML = '<div class="red">止日早于起日，请检查日期。</div>'; return; }
  const it = delayInterestCalc(amt, days);
  if (it == null) { out.innerHTML = '<div class="red">天数须为正整数（起算规则见下方依据说明）。</div>'; return; }
  out.innerHTML = `<div class="ok" style="font-size:18px"><b>¥ ${calcFmt(it)}</b>
    <span style="font-size:13px;color:var(--muted)">（${rmbCap(it)}）</span></div>
    <div class="hint">未清偿金钱债务 ${calcFmt(amt)} × 日万分之 1.75 × ${days} 天。
    期间天数这里按「止日 − 起日」直减；<b>起算日与截止日规则</b>（生效法律文书确定的履行期间届满之日起算；
    分批履行的分别起算）见法释〔2014〕8号第二、三条，请按个案确认后取数。
    一般债务利息（文书确定的）另按文书方法算，本工具只算<b>加倍部分</b>。</div>`;
};
window.calcLoanRun = function() {
  const p = calcParseAmt(document.getElementById('lP').value);
  const r = Number(document.getElementById('lR').value) || 0;
  const basis = Number(document.getElementById('lB').value) === 360 ? 360 : 365;
  const days = calcDays(document.getElementById('lS').value, document.getElementById('lT').value);
  const out = document.getElementById('lOut');
  if (!(p > 0) || !(r > 0) || days == null || days < 0) {
    out.innerHTML = '<div class="dim">填本金、年利率（%）、起止日期后即时计算。</div>'; return; }
  const it = loanInterestCalc(p, r, days, basis);
  out.innerHTML = `<div class="ok" style="font-size:18px"><b>¥ ${calcFmt(it)}</b>
    <span style="font-size:13px;color:var(--muted)">（${rmbCap(it)}）</span></div>
    <div class="hint">本金 ${calcFmt(p)} × ${r}% ÷ ${basis} × ${days} 天（单利）。
    本息合计：<b>¥ ${calcFmt(p + it)}</b>（${rmbCap(Math.round((p + it) * 100) / 100)}）。
    日利率分母口径自选：÷360 为金融机构惯例，÷365 为按自然年。</div>`;
};
window.calcDaysRun = function() {
  const days = calcDays(document.getElementById('wS').value, document.getElementById('wT').value);
  const out = document.getElementById('wOut');
  if (days == null) { out.innerHTML = '<div class="dim">选两个日期后即时计算。</div>'; return; }
  if (days < 0) { out.innerHTML = '<div class="red">后一个日期早于前一个。</div>'; return; }
  out.innerHTML = `<div class="ok" style="font-size:18px"><b>${days}</b> 天
    <span style="font-size:13px;color:var(--muted)">（约 ${(days / 30).toFixed(1)} 个月 / ${(days / 365).toFixed(2)} 年）</span></div>
    <div class="hint">自然日直减（止日 − 起日，两端不重复计入）。法定期间的计算规则
   （开始的时日不计算在内、届满日遇节假日顺延等）按民诉法及其解释另行核对。</div>`;
};
window.calcCapRun = function() {
  const v = calcParseAmt(document.getElementById('capIn').value);
  const out = document.getElementById('capOut');
  if (!(v > 0)) { out.innerHTML = '<div class="dim">输入金额后即时转换（支持「10万」写法）。</div>'; return; }
  out.innerHTML = `<div class="ok" style="font-size:18px"><b>${rmbCap(v)}</b></div>
    <div class="hint">口径：中间连续的「0」只写一个零字；角后不带「整」；角位为零、分位非零写「零X分」。</div>`;
};

/* ============ 批 58：劳动三件（解除补偿 / 二倍工资 / 未休年假）——纯本地计算 ============ */
/* 月差（整月；终止月日期小于起始月日期时减一月） */
function calcMonthsDiff(d1, d2) {
  const a = d1.split('-').map(Number), b = d2.split('-').map(Number);
  let m = (b[0] - a[0]) * 12 + (b[1] - a[1]);
  if (b[2] < a[2]) m -= 1;
  return m;
}
/* 解除补偿（劳动合同法第46/47/48/87/97条、实施条例第20/27条）：
   scenario: economic=N / notice=N+1 / illegal=2N；cityAvg>0 时判三倍封顶 + 12 年封顶；
   批 63 增强：minWage>0 判最低工资兜底（实施条例第27条）；start<2008-01-01 给出分段参考（第97条第3款，简化口径） */
function severanceCalc(scenario, start, end, avgWage, lastWage, cityAvg, minWage) {
  if (!start || !end || !(avgWage > 0)) return null;
  const m = calcMonthsDiff(start, end);
  if (m < 0) return null;
  let wage = avgWage, floored = false;
  if (minWage > 0 && wage < minWage) { wage = minWage; floored = true; }
  let months = m < 6 ? 0.5 : (m < 12 ? 1 : Math.floor(m / 12) + (m % 12 >= 6 ? 1 : (m % 12 > 0 ? 0.5 : 0)));
  let base = wage, capped = false;
  if (cityAvg > 0 && wage > 3 * cityAvg) { capped = true; base = 3 * cityAvg; months = Math.min(months, 12); }
  const n = Math.round(months * base * 100) / 100;
  let amount = n, notice = 0;
  if (scenario === 'notice') { notice = lastWage > 0 ? lastWage : 0; amount = Math.round((n + notice) * 100) / 100; }
  else if (scenario === 'illegal') { amount = Math.round(n * 2 * 100) / 100; }
  /* 批 63：2008-01-01 前入职的分段参考（旧规段：每满1年1个月、不满1年按1年、最多12个月；三倍封顶/12年封顶仅限后段） */
  let seg = null;
  if (start < '2008-01-01') {
    const mPre = Math.max(0, calcMonthsDiff(start, '2008-01-01'));
    const mPost = Math.max(0, calcMonthsDiff('2008-01-01', end));
    const monthsPre = Math.min(Math.ceil(mPre / 12), 12);
    let monthsPost = mPost < 6 ? 0.5 : (mPost < 12 ? 1 : Math.floor(mPost / 12) + (mPost % 12 >= 6 ? 1 : (mPost % 12 > 0 ? 0.5 : 0)));
    if (cityAvg > 0 && wage > 3 * cityAvg) monthsPost = Math.min(monthsPost, 12);
    const payPre = Math.round(monthsPre * wage * 100) / 100;
    const payPost = Math.round(monthsPost * base * 100) / 100;
    seg = {monthsPre, payPre, monthsPost, payPost, totalPay: Math.round((payPre + payPost) * 100) / 100};
  }
  return {months, base, capped, floored, n, notice, amount, seg};
}
/* 未签书面合同二倍工资差额（第82条、实施条例第6/7条）：满一个月次日起算，最多 11 个月 */
function dblWageCalc(start, sign, wage, today) {
  if (!start || !(wage > 0)) return null;
  const a = start.split('-').map(Number);
  const pad = n => String(n).padStart(2, '0');
  const s1 = (a[1] === 12 ? (a[0] + 1) + '-01' : a[0] + '-' + pad(a[1] + 1)) + '-' + pad(a[2]);
  const anniv = (a[0] + 1) + '-' + pad(a[1]) + '-' + pad(a[2]);
  let end = sign || today || '';
  if (!end || end > anniv) end = anniv;
  let m = calcMonthsDiff(s1, end);
  if (m < 0) m = 0;
  m = Math.min(m, 11);
  return {s1, end, months: m, amount: Math.round(m * wage * 100) / 100};
}
/* 未休年假折算（年休假条例第3/5条、实施办法第10/11/12条）：
   折算 = floor(档位 × 天数 ÷ 365)，不足 1 整天不享受；日工资 = 月工资 ÷ 21.75；未休另付 200% */
function leavePayCalc(capDays, workedDays, usedDays, wage) {
  if (!(capDays > 0) || !(workedDays >= 0) || !(wage > 0)) return null;
  const entitled = Math.floor(capDays * workedDays / 365);
  const unused = Math.max(0, entitled - (usedDays || 0));
  const daily = Math.round(wage / 21.75 * 100) / 100;
  const extra200 = Math.round(unused * daily * 2 * 100) / 100;
  const total300 = Math.round(unused * daily * 3 * 100) / 100;
  return {entitled, unused, daily, extra200, total300};
}
window.calcSeverRun = function() {
  const g = id => document.getElementById(id);
  const out = g('svOut');
  const s = g('svS').value;
  const r = severanceCalc(s, g('svA').value, g('svB').value,
    calcParseAmt(g('svW').value), calcParseAmt(g('svL').value), calcParseAmt(g('svC').value),
    calcParseAmt(g('svM') ? g('svM').value : ''));
  if (!r) { out.innerHTML = '<div class="dim">填齐情形、入职/解除日期与月工资后即时计算。</div>'; return; }
  const label = s === 'illegal' ? '违法解除赔偿金（2N）'
    : (s === 'notice' ? '经济补偿 N + 代通知金（N+1）' : '经济补偿（N）');
  let h = `<div class="ok" style="font-size:18px"><b>¥ ${calcFmt(r.amount)}</b>
    <span style="font-size:13px;color:var(--muted)">（${rmbCap(r.amount)}）</span>
    <span class="dim" style="font-size:13px">· ${label}</span></div>`;
  if (r.floored) h += `<div class="hint">月工资低于当地最低工资 → 已按最低工资计基数（实施条例第 27 条）。</div>`;
  h += `<div class="hint">工作年限按整月折算：<b>${r.months}</b> 个月工资（满 1 年＝1 个月；6 个月以上不满 1 年＝1 年；不满 6 个月＝半个月）
    ${r.capped ? '；<b>高收入封顶已生效</b>：基数按社平 3 倍 ' + calcFmt(r.base) + ' 元、年限不超 12 个月' : ''}。
    月工资基数 ${calcFmt(r.base)} 元 → 经济补偿 N = ${calcFmt(r.n)} 元${r.notice ? '；代通知金（上月工资）= ' + calcFmt(r.notice) + ' 元' : ''}。</div>`;
  if (r.seg) h += `<div class="hint"><b>2008-01-01 前入职 · 分段参考</b>（劳动合同法第 97 条第 3 款，简化口径）：
    2008 前段 ${r.seg.monthsPre} 个月 × 月工资 = ${calcFmt(r.seg.payPre)} 元（旧规：不满 1 年按 1 年、最多 12 个月）；
    2008 后段 ${r.seg.monthsPost} 个月 × 基数 = ${calcFmt(r.seg.payPost)} 元；<b>分段合计 ${calcFmt(r.seg.totalPay)} 元</b>。
    ⚠ 旧规段与地方口径存在差异（如封顶情形的适用），正式引用前请人工复核。</div>`;
  h += `<div class="hint">口径注：月工资＝解除前 12 个月平均应得工资（含奖金津贴；低于当地最低工资按最低工资，实施条例第 27 条）；代通知金按上月工资标准（第 20 条）；
    2N 按经济补偿标准的二倍（第 87 条）。未计零头天数与地方口径差异，请按个案复核。</div>`;
  out.innerHTML = h;
};
window.calcDblRun = function() {
  const g = id => document.getElementById(id);
  const out = g('dbOut');
  const n2 = new Date();
  const td = n2.getFullYear() + '-' + String(n2.getMonth() + 1).padStart(2, '0') + '-' + String(n2.getDate()).padStart(2, '0');
  const r = dblWageCalc(g('dbA').value, g('dbB').value, calcParseAmt(g('dbW').value), td);
  if (!r) { out.innerHTML = '<div class="dim">填入职日期与月工资后即时计算（签订日留空＝至今）。</div>'; return; }
  out.innerHTML = `<div class="ok" style="font-size:18px"><b>¥ ${calcFmt(r.amount)}</b>
    <span style="font-size:13px;color:var(--muted)">（${rmbCap(r.amount)}）</span></div>
    <div class="hint">差额期间：${r.s1} ～ ${r.end}，计 <b>${r.months}</b> 个月（最多 11 个月）；金额＝月工资 × 月数（差额＝另付一倍）。</div>
    <div class="hint">口径注：自用工之日起满一个月的次日起算（实施条例第 6 条）；满一年视为已订立无固定期限合同、该月起不再计（第 7 条）。
    各月差额仲裁时效起算与基数口径存在地方差异，请按当地裁审口径复核。</div>`;
};
window.calcLeaveRun = function() {
  const g = id => document.getElementById(id);
  const out = g('lvOut');
  const u = Number(g('lvU').value) || 0;
  const r = leavePayCalc(Number(g('lvY').value) || 0, Number(g('lvD').value) || 0, u, calcParseAmt(g('lvW').value));
  if (!r) { out.innerHTML = '<div class="dim">选年假档、填当年在职天数、已休天数与月工资后计算。</div>'; return; }
  out.innerHTML = `<div class="ok" style="font-size:18px"><b>${r.unused}</b> 天未休
    <span style="font-size:13px;color:var(--muted)">（折算应休 ${r.entitled} 天 − 已休 ${u} 天）</span></div>
    <div class="hint">日工资＝月工资 ÷ 21.75 = ¥ ${calcFmt(r.daily)}；未休部分<b>另应付 ¥ ${calcFmt(r.extra200)}</b>（200% 口径）；
    300% 全额口径 ¥ ${calcFmt(r.total300)}（含正常工作期间已付的 100%）。</div>
    <div class="hint">口径注：折算＝（当年已过/在职日历天数 ÷ 365）× 全年应休，不足 1 整天不享受（实施办法第 12 条；新入职跨年按第 5 条）；
    档位＝累计工作满 1 年不满 10 年 5 天 / 满 10 年不满 20 年 10 天 / 满 20 年 15 天（条例第 3 条）。</div>`;
};

/* ============ 批 63：劳动六件套第二期（加班工资 / 竞业限制补偿 / 工伤伤残补助金）——纯本地计算 ============ */
/* 加班工资（劳动法第44条；人社厅函〔2025〕1号「另外支付」口径）：小时工资＝月工资÷21.75÷8；
   工作日延时×1.5；休息日×2（安排补休则不付）；法定节假日×3 */
function overtimeCalc(wage, hWorkday, hRest, hHoliday) {
  if (!(wage > 0)) return null;
  const hourly = wage / 21.75 / 8;
  const payW = Math.round(hourly * 1.5 * (hWorkday || 0) * 100) / 100;
  const payR = Math.round(hourly * 2 * (hRest || 0) * 100) / 100;
  const payH = Math.round(hourly * 3 * (hHoliday || 0) * 100) / 100;
  return {hourly: Math.round(hourly * 100) / 100, payW, payR, payH,
          total: Math.round((payW + payR + payH) * 100) / 100};
}
/* 竞业限制月补偿（劳动合同法第23条；法释〔2020〕26号第36条；人社部竞业合规指引第13条）：
   月补偿＝max(月均工资×30%, 履行地最低工资)；竞业期超1年给 50% 参考口径 */
function noncompeteCalc(avgWage, minWage, months) {
  if (!(avgWage > 0) || !(months > 0)) return null;
  const m30 = Math.round(avgWage * 0.3 * 100) / 100;
  const monthly = Math.max(m30, minWage > 0 ? minWage : 0);
  return {m30, monthly, floored: minWage > m30, total: Math.round(monthly * months * 100) / 100,
          m50: Math.round(avgWage * 0.5 * 100) / 100, months};
}
/* 工伤一次性伤残补助金（工伤保险条例第35/36/37条；本人工资与60%/300%调整：第64条） */
const INJURY_MONTHS = {1: 27, 2: 25, 3: 23, 4: 21, 5: 18, 6: 16, 7: 13, 8: 11, 9: 9, 10: 7};
function injuryCalc(grade, wage, cityAvg) {
  const g = Math.round(grade);
  if (!INJURY_MONTHS[g] || !(wage > 0)) return null;
  let w = wage, adj = '';
  if (cityAvg > 0) {
    if (w > 3 * cityAvg) { w = 3 * cityAvg; adj = '高于统筹地区职工平均工资 300%，按 300% 计'; }
    else if (w < 0.6 * cityAvg) { w = 0.6 * cityAvg; adj = '低于统筹地区职工平均工资 60%，按 60% 计'; }
  }
  return {months: INJURY_MONTHS[g], base: Math.round(w * 100) / 100,
          amount: Math.round(INJURY_MONTHS[g] * w * 100) / 100, adj};
}
window.calcOvRun = function() {
  const g = id => document.getElementById(id);
  const out = g('ovOut');
  const r = overtimeCalc(calcParseAmt(g('ovW').value), Number(g('ov1').value) || 0,
    Number(g('ov2').value) || 0, Number(g('ov3').value) || 0);
  if (!r) { out.innerHTML = '<div class="dim">填月工资与各段加班小时数后即时计算（没有的段留空）。</div>'; return; }
  out.innerHTML = `<div class="ok" style="font-size:18px"><b>¥ ${calcFmt(r.total)}</b>
    <span style="font-size:13px;color:var(--muted)">（${rmbCap(r.total)}）</span></div>
    <div class="hint">小时工资＝月工资 ÷ 21.75 ÷ 8 = ¥ ${calcFmt(r.hourly)}；
    ① 工作日延时 ×1.5 = ¥ ${calcFmt(r.payW)}；② 休息日 ×2 = ¥ ${calcFmt(r.payR)}；③ 法定节假日 ×3 = ¥ ${calcFmt(r.payH)}。</div>
    <div class="hint">口径注：法定节假日为<b>另外支付</b>不低于 300%（人社厅函〔2025〕1 号；2025 年起法定节假日 13 天/年）；休息日加班先安排补休、补休则不付；
    工作日延时不低于 150%（劳动法第 44 条）。加班事实举证与时效按个案（解释一第 42 条）。</div>`;
};
window.calcNcRun = function() {
  const g = id => document.getElementById(id);
  const out = g('ncOut');
  const r = noncompeteCalc(calcParseAmt(g('ncW').value), calcParseAmt(g('ncM').value), Number(g('ncN').value) || 0);
  if (!r) { out.innerHTML = '<div class="dim">填「解除前 12 个月平均工资」与「竞业期限月数」后计算。</div>'; return; }
  let h = `<div class="ok" style="font-size:18px"><b>¥ ${calcFmt(r.monthly)}</b>/月
    <span style="font-size:13px;color:var(--muted)">× ${r.months} 个月 = 合计 ¥ ${calcFmt(r.total)}</span></div>
    <div class="hint">月均工资 30% ＝ ¥ ${calcFmt(r.m30)}${r.floored ? '；低于履行地最低工资 → 按最低工资 ¥ ' + calcFmt(r.monthly) + '/月 计（法释〔2020〕26号第 36 条）' : ''}。</div>`;
  if (r.months > 12) h += `<div class="hint">竞业期超过 1 年：人社部合规指引第 13 条参考口径——月补偿一般不宜低于月均工资的 50%（参考 ¥ ${calcFmt(r.m50)}/月）。</div>`;
  h += `<div class="hint">口径注：有约定按约定（不低于最低工资）；未约定补偿而劳动者履行的按本条支持；用人单位 3 个月未付，劳动者可请求解除竞业约定（第 38 条）；
    用人单位在期限内解除的，劳动者可请求额外支付 3 个月补偿（第 39 条）。</div>`;
  out.innerHTML = h;
};
window.calcInjRun = function() {
  const g = id => document.getElementById(id);
  const out = g('injOut');
  const r = injuryCalc(Number(g('injG').value), calcParseAmt(g('injW').value), calcParseAmt(g('injC').value));
  if (!r) { out.innerHTML = '<div class="dim">选伤残等级、填本人工资后计算。</div>'; return; }
  out.innerHTML = `<div class="ok" style="font-size:18px"><b>¥ ${calcFmt(r.amount)}</b>
    <span style="font-size:13px;color:var(--muted)">（${r.months} 个月 × 本人工资 ¥ ${calcFmt(r.base)}）</span></div>
    ${r.adj ? '<div class="hint">已调整：' + r.adj + '（工伤保险条例第 64 条）。</div>' : ''}
    <div class="hint">口径注：本人工资＝受伤前 12 个月平均月缴费工资；高于统筹地区职工平均工资 300% 按 300%、低于 60% 按 60%（第 64 条；填「统筹地区月平均工资」才判调整）。
    5–10 级解除/终止劳动合同时另有一次工伤医疗补助金 + 一次性伤残就业补助金（省级标准，请查当地）；1–4 级另有按月伤残津贴（第 35 条）。</div>`;
};

function viewCalc() {
  return `
  <div class="card">
    <h2>① 案件受理费（一审财产案件）</h2>
    <div class="row">标的额（诉讼请求金额）：
      <input id="cA" style="width:200px" inputmode="decimal" placeholder="如 10万 或 100000" oninput="calcFeeCaseRun()">
    </div>
    <div id="cAOut" style="margin-top:8px"><div class="dim">输入金额后即时计算。</div></div>
    <details><summary class="dim">法条依据（《诉讼费用交纳办法》第十三条第一项，2007-04-01 施行·现行有效，核验日 2026-10-09 元典）</summary>
      <div class="hint">财产案件按诉讼请求的金额或价额分段累计交纳：≤1万 每件50元；1–10万部分 2.5%；10–20万 2%；20–50万 1.5%；50–100万 1%；100–200万 0.9%；200–500万 0.8%；500–1000万 0.7%；1000–2000万 0.6%；超2000万部分 0.5%。</div>
      <div class="hint">适用中注意：诉讼请求未获全部支持的，受理费按比例分担（第二十九条）；调解/撤诉减半（第十五条）等，本表只算全额标准。</div>
    </details>
  </div>
  <div class="card">
    <h2>② 执行申请费</h2>
    <div class="row">执行金额或价额：
      <input id="cE" style="width:200px" inputmode="decimal" placeholder="如 50万 或 500000" oninput="calcFeeExecRun()">
    </div>
    <div id="cEOut" style="margin-top:8px"><div class="dim">输入金额后即时计算。</div></div>
    <details><summary class="dim">法条依据（第十四条第一项·现行有效，核验日 2026-10-09 元典）</summary>
      <div class="hint">≤1万 每件50元；1–50万部分 1.5%；50–500万 1%；500–1000万 0.5%；超1000万部分 0.1%。执行申请费由被执行人负担（第三十八条，本表只算数额）。</div>
    </details>
  </div>
  <div class="card">
    <h2>③ 保全申请费</h2>
    <div class="row">实际保全的财产数额：
      <input id="cP" style="width:200px" inputmode="decimal" placeholder="如 100万 或 1000000" oninput="calcFeePreserveRun()">
    </div>
    <div id="cPOut" style="margin-top:8px"><div class="dim">输入金额后即时计算。</div></div>
    <details><summary class="dim">法条依据（第十四条第二项·现行有效，核验日 2026-10-09 元典）</summary>
      <div class="hint">≤1000元或不涉及财产数额 每件30元；1000–10万部分 1%；超10万部分 0.5%；最多不超过 5000 元。</div>
    </details>
  </div>
  <div class="card">
    <h2>④ 迟延履行加倍部分利息</h2>
    <div class="row">未清偿金钱债务：
      <input id="dD" style="width:200px" inputmode="decimal" placeholder="如 12万" oninput="calcDelayRun()">
    </div>
    <div class="row" style="margin-top:6px">迟延起算日 <input id="dS" type="date" style="width:150px" onchange="calcDelayRun()">
      　截止日 <input id="dT" type="date" style="width:150px" onchange="calcDelayRun()"></div>
    <div id="dOut" style="margin-top:8px"><div class="dim">填「未清偿金额」和起止日期后即时计算。</div></div>
    <details><summary class="dim">法条依据（法释〔2014〕8号第一条·现行有效，核验日 2026-10-09 元典）</summary>
      <div class="hint">加倍部分债务利息 ＝ 债务人尚未清偿的生效法律文书确定的除一般债务利息之外的金钱债务 × 日万分之一点七五 × 迟延履行期间。起算与截止规则见该解释第二、三条；一般债务利息按文书确定方法另算。</div>
    </details>
  </div>
  <div class="card">
    <h2>⑤ 借款利息（通用单利）</h2>
    <div class="row">本金 <input id="lP" style="width:130px" inputmode="decimal" placeholder="如 20万" oninput="calcLoanRun()">
      年利率% <input id="lR" style="width:80px" inputmode="decimal" placeholder="如 3.85" oninput="calcLoanRun()">
      分母口径 <select id="lB" onchange="calcLoanRun()"><option value="360">÷360 银行惯例</option><option value="365">÷365 自然年</option></select>
    </div>
    <div class="row" style="margin-top:6px">起算日 <input id="lS" type="date" style="width:150px" onchange="calcLoanRun()">
      　截止日 <input id="lT" type="date" style="width:150px" onchange="calcLoanRun()"></div>
    <div id="lOut" style="margin-top:8px"><div class="dim">填本金、年利率（%）、起止日期后即时计算。</div></div>
    <div class="hint">单利公式：本金 × 年利率 ÷ 分母 × 天数。约定利率上限（民间借贷合同成立时一年期 LPR 的四倍）按个案自行核。</div>
  </div>
  <div class="card">
    <h2>⑥ 期间天数 · 数字大写</h2>
    <div class="row">起算日 <input id="wS" type="date" style="width:150px" onchange="calcDaysRun()">
      　截止日 <input id="wT" type="date" style="width:150px" onchange="calcDaysRun()"></div>
    <div id="wOut" style="margin-top:8px"><div class="dim">选两个日期后即时计算。</div></div>
    <div class="row" style="margin-top:10px">金额转大写：
      <input id="capIn" style="width:200px" inputmode="decimal" placeholder="如 100500" oninput="calcCapRun()">
    </div>
    <div id="capOut" style="margin-top:8px"><div class="dim">输入金额后即时转换（支持「10万」写法）。</div></div>
  </div>
  <div class="card">
    <h2>⑦ 律师费参考（厦门市律协指引 · 民诉一个审级）</h2>
    <div class="row">争议财产标的额：
      <input id="cL" style="width:200px" inputmode="decimal" placeholder="如 100万" oninput="calcFeeLawyerRun()">
    </div>
    <div id="cLOut" style="margin-top:8px"><div class="dim">输入争议标的额后即时计算（输出下限~上限区间）。</div></div>
    <details><summary class="dim">依据与口径（《厦门市律师服务收费指引》附件一·参考收费标准，检索核验日 2026-10-09）</summary>
      <div class="hint">来源：厦门市律师协会（第九届理事会审议通过生效，《厦门市律师服务收费指引》附件一「律师诉讼法律服务参考收费标准」，厦门律师网 xmls.cn 公示；另经人民法院判决书引用第八条交叉印证）。代理民事诉讼案件一个审级：不涉及财产关系的 1万~10万元/件；涉及财产关系的，10万以下部分 6000~10000 元，超 10 万部分分段累加：10–100万部分 6%~9%；100–500万 4%~6%；500–1000万 2%~4%；1000–5000万 1%~3%；5000万–1亿 0.5%~1.5%；1亿以上 0.25%~1%。</div>
      <div class="hint">口径注意：这是<b>参考标准非强制</b>，实际收费以委托代理合同为准；一个审级一份费（一审已代理、二审续代可酌减不宜超 50%；执行程序参照）；重大疑难复杂案件可在 5 倍内协商；反诉按另一案件单独计费。</div>
    </details>
  </div>
  <div class="card">
    <h2>⑧ 解除劳动合同补偿（经济补偿 N / N+1 / 2N）</h2>
    <div class="row">情形
      <select id="svS" onchange="calcSeverRun()">
        <option value="economic">一般情形解除（N）</option>
        <option value="notice">无过失性辞退（N+1，含代通知金）</option>
        <option value="illegal">违法解除（2N 赔偿金）</option>
      </select>
    </div>
    <div class="row" style="margin-top:6px">入职 <input id="svA" type="date" style="width:150px" onchange="calcSeverRun()">
      　解除/终止 <input id="svB" type="date" style="width:150px" onchange="calcSeverRun()"></div>
    <div class="row" style="margin-top:6px">解除前12个月平均工资 <input id="svW" style="width:140px" inputmode="decimal" placeholder="如 8000" oninput="calcSeverRun()">
      上月工资（N+1用） <input id="svL" style="width:120px" inputmode="decimal" placeholder="可留空" oninput="calcSeverRun()"></div>
    <div class="row" style="margin-top:6px">上年度职工月平均工资（判三倍封顶，可留空） <input id="svC" style="width:120px" inputmode="decimal" placeholder="如 8000" oninput="calcSeverRun()">
      当地最低工资（可留空） <input id="svM" style="width:110px" inputmode="decimal" placeholder="如 2030" oninput="calcSeverRun()"></div>
    <div id="svOut" style="margin-top:8px"><div class="dim">填齐情形、日期与月工资后即时计算。</div></div>
    <details><summary class="dim">法条依据（《劳动合同法》第 46/47/48/87/97 条、实施条例第 20/27 条；核验日 2026-10-13 · 元典法规库）</summary>
      <div class="hint">经济补偿：每满一年一个月工资；六个月以上不满一年按一年；不满六个月按半个月（第 47 条）。高收入封顶：月工资高于当地上年度职工月平均工资三倍的，按三倍数额计、年限不超过 12 年（同条）。代通知金：仅第 40 条无过失性辞退情形，按上一个月工资标准（实施条例第 20 条）。违法解除：按经济补偿标准的二倍支付赔偿金（第 87 条）。月工资＝解除前 12 个月平均应得工资，<b>低于当地最低工资的按最低工资计</b>（实施条例第 27 条）。2008-01-01 前入职：跨新法分段（第 97 条第 3 款）——2008 前年限按当时规定、2008 后按现行标准，结果区给出简化分段参考。</div>
    </details>
  </div>
  <div class="card">
    <h2>⑨ 未签书面合同二倍工资差额</h2>
    <div class="row">入职 <input id="dbA" type="date" style="width:150px" onchange="calcDblRun()">
      　补签/签订日 <input id="dbB" type="date" style="width:150px" onchange="calcDblRun()"> <span class="dim">留空＝至今</span></div>
    <div class="row" style="margin-top:6px">月工资 <input id="dbW" style="width:150px" inputmode="decimal" placeholder="如 5000" oninput="calcDblRun()"></div>
    <div id="dbOut" style="margin-top:8px"><div class="dim">填入职日期与月工资后即时计算。</div></div>
    <details><summary class="dim">法条依据（《劳动合同法》第 82 条、实施条例第 6/7 条；核验日 2026-10-13 · 元典法规库已补验）</summary>
      <div class="hint">自用工之日起超过一个月不满一年未订立书面劳动合同：每月支付二倍工资（第 82 条；差额＝另付一倍，满分一年内最多 11 个月）；起算＝满一个月的次日（实施条例第 6 条）；满一年视为已订立无固定期限合同并立即补订（第 7 条）。仲裁时效与基数存在个案/地方口径差异，请核对。</div>
    </details>
  </div>
  <div class="card">
    <h2>⑩ 未休年假工资折算</h2>
    <div class="row">年假档 <select id="lvY" onchange="calcLeaveRun()">
        <option value="5">满1年不满10年 · 5天</option>
        <option value="10">满10年不满20年 · 10天</option>
        <option value="15">满20年 · 15天</option>
      </select>
      当年在职天数 <input id="lvD" style="width:86px" inputmode="numeric" placeholder="如 286" oninput="calcLeaveRun()">
      已休 <input id="lvU" style="width:66px" inputmode="numeric" placeholder="0" oninput="calcLeaveRun()"></div>
    <div class="row" style="margin-top:6px">月工资 <input id="lvW" style="width:150px" inputmode="decimal" placeholder="如 6525" oninput="calcLeaveRun()"></div>
    <div id="lvOut" style="margin-top:8px"><div class="dim">填齐后即时计算（在职天数＝当年度已过/在职日历天数）。</div></div>
    <details><summary class="dim">法条依据（《职工带薪年休假条例》第3/5条、实施办法第10/11/12条；核验日 2026-10-13 · 元典法规库已补验）</summary>
      <div class="hint">天数档：累计工作满 1 年不满 10 年 5 天、满 10 年不满 20 年 10 天、满 20 年 15 天（条例第 3 条）。未休补偿：日工资收入的 300%（含已付 100%，即另付 200%）；日工资＝月工资 ÷ 月计薪天数 21.75（实施办法第 10、11 条）。折算＝（当年度已过日历天数 ÷ 365）× 全年应休天数，不足 1 整天不享受（第 12 条）。</div>
    </details>
  </div>
  <div class="card">
    <h2>⑪ 执行款清偿分配</h2>
    <div class="row">本金 <input id="exP" style="width:150px" inputmode="decimal" placeholder="执行标的本金" oninput="calcExecRun()">
      费用合计 <input id="exF" style="width:140px" inputmode="decimal" placeholder="诉讼费/执行费等" oninput="calcExecRun()">
      截止日 <input id="exED" type="date" style="width:150px" onchange="calcExecRun()"></div>
    <div class="row" style="margin-top:6px">一般利息总额 <input id="exIS" style="width:150px" inputmode="decimal" placeholder="直接填总额" oninput="calcExecRun()">
      或 年利率% <input id="exIR" style="width:100px" inputmode="decimal" placeholder="如 3.45" oninput="calcExecRun()">
      起算日 <input id="exSD" type="date" style="width:150px" onchange="calcExecRun()"></div>
    <div style="margin-top:8px" class="dim">回款逐笔（每行「日期 金额」，可从 Excel 复制两列直接粘贴）：</div>
    <textarea id="exRep" rows="3" style="width:100%;resize:vertical;font-family:var(--mono);font-size:13px" placeholder="2026-09-01 50000&#10;2026-10-15 30000" oninput="calcExecRun()"></textarea>
    <div class="row" style="margin-top:6px"><label style="display:flex;align-items:center;gap:6px"><input type="checkbox" id="exDL" style="width:auto" onchange="calcExecRun()"> 加倍部分（日万分之1.75，独立累计）</label>
      迟延起算日 <input id="exDD" type="date" style="width:150px" onchange="calcExecRun()"></div>
    <div id="exOut" style="margin-top:8px"><div class="dim">填本金 + 利息（总额或利率）+ 回款逐笔后即时计算；抵扣顺序＝费用 → 一般利息 → 本金。</div></div>
    <details><summary class="dim">法条依据（《民法典》第 561 条；法释〔2014〕8号第 1、4 条；核验日 2026-10-13 · 元典法规库）</summary>
      <div class="hint">清偿抵充顺序：实现债权的有关费用 → 利息 → 主债务（民法典第 561 条）。执行程序：先清偿生效法律文书确定的金钱债务、再清偿加倍部分债务利息（法释〔2014〕8号第 4 条）；加倍部分＝尚未清偿的除一般利息之外的金钱债务 × 日万分之一点七五 × 迟延履行期间（第 1 条）。本卡按「本金 ÷365」区间口径折算利息，与判决主文口径不一致时以判决为准。</div>
    </details>
  </div>
  <div class="card">
    <h2>⑫ 加班工资（工作日延时 / 休息日 / 法定节假日）</h2>
    <div class="row">月工资 <input id="ovW" style="width:150px" inputmode="decimal" placeholder="如 8000" oninput="calcOvRun()">
      工作日延时（小时） <input id="ov1" style="width:80px" inputmode="decimal" placeholder="0" oninput="calcOvRun()"></div>
    <div class="row" style="margin-top:6px">休息日（小时） <input id="ov2" style="width:80px" inputmode="decimal" placeholder="0" oninput="calcOvRun()">
      法定节假日（小时） <input id="ov3" style="width:80px" inputmode="decimal" placeholder="0" oninput="calcOvRun()"></div>
    <div id="ovOut" style="margin-top:8px"><div class="dim">填月工资与各段加班小时数后即时计算（没有的段留空）。</div></div>
    <details><summary class="dim">法条依据（《劳动法》第 44 条；人社厅函〔2025〕1 号；劳社部发〔2008〕3 号；核验日 2026-10-13 · 元典法规库）</summary>
      <div class="hint">工作日延时：不低于 150%；休息日工作又不能安排补休：不低于 200%；法定休假日工作：不低于 300%（劳动法第 44 条）。法定节假日为「另外支付」不低于 300%、不得以节假日本身带薪为由抵扣（人社厅函〔2025〕1 号）；2025 年起全体公民放假的节日共 13 天/年（放假办法 2024 修订）。
      折算：日工资＝月工资 ÷ 21.75，小时工资＝日工资 ÷ 8（劳社部发〔2008〕3 号口径）。</div>
    </details>
  </div>
  <div class="card">
    <h2>⑬ 竞业限制补偿（月补偿 / 合计）</h2>
    <div class="row">解除前12个月平均工资 <input id="ncW" style="width:140px" inputmode="decimal" placeholder="如 20000" oninput="calcNcRun()">
      履行地最低工资（可留空） <input id="ncM" style="width:110px" inputmode="decimal" placeholder="如 2030" oninput="calcNcRun()">
      竞业期限（月） <input id="ncN" style="width:80px" inputmode="decimal" placeholder="如 12" oninput="calcNcRun()"></div>
    <div id="ncOut" style="margin-top:8px"><div class="dim">填平均工资与竞业期限后即时计算（未约定补偿情形口径）。</div></div>
    <details><summary class="dim">法条依据（《劳动合同法》第 23/24 条；法释〔2020〕26号第 36–39 条；人社部竞业合规指引 2025；核验日 2026-10-13 · 元典法规库）</summary>
      <div class="hint">有约定按约定；未约定补偿的：按解除前 12 个月平均工资的 30%/月（不得低于履行地最低工资）（法释〔2020〕26号第 36 条）。竞业期超过 1 年的，人社部合规指引第 13 条口径：月补偿一般不宜低于 50%。
      用人单位 3 个月未付补偿：劳动者可请求解除竞业约定（第 38 条）；单位在期限内解除的：劳动者可请求额外 3 个月补偿（第 39 条）。违约金另按约定；违反约定须返还已付补偿并支付违约金（法释〔2025〕12号第 15 条）。</div>
    </details>
  </div>
  <div class="card">
    <h2>⑭ 工伤一次性伤残补助金</h2>
    <div class="row">伤残等级
      <select id="injG" onchange="calcInjRun()">
        <option value="1">一级（27 个月）</option>
        <option value="2">二级（25 个月）</option>
        <option value="3">三级（23 个月）</option>
        <option value="4">四级（21 个月）</option>
        <option value="5">五级（18 个月）</option>
        <option value="6">六级（16 个月）</option>
        <option value="7">七级（13 个月）</option>
        <option value="8">八级（11 个月）</option>
        <option value="9">九级（9 个月）</option>
        <option value="10" selected>十级（7 个月）</option>
      </select>
      本人工资 <input id="injW" style="width:120px" inputmode="decimal" placeholder="前12个月平均缴费工资" oninput="calcInjRun()">
      统筹地区月平均工资（可留空） <input id="injC" style="width:110px" inputmode="decimal" placeholder="判 60%/300%" oninput="calcInjRun()"></div>
    <div id="injOut" style="margin-top:8px"><div class="dim">选等级、填本人工资后即时计算。</div></div>
    <details><summary class="dim">法条依据（《工伤保险条例》第 35/36/37/64 条；核验日 2026-10-13 · 元典法规库）</summary>
      <div class="hint">标准：一至四级 27/25/23/21 个月、五至六级 18/16 个月、七至十级 13/11/9/7 个月的本人工资（第 35/36/37 条）。本人工资＝受伤前 12 个月平均月缴费工资，高于统筹地区职工平均工资 300% 按 300% 计、低于 60% 按 60% 计（第 64 条）。
      本卡只算<b>一次性伤残补助金</b>；5–10 级解除时另有一次性工伤医疗补助金 + 伤残就业补助金（省级标准）；1–4 级另有按月伤残津贴——请按当地规定另行核算。</div>
    </details>
  </div>
  <div class="hint" style="margin-top:10px">计算结果均为<b>草稿参考</b>：正式引用前按个案核对法条适用（减免、分担、上限等另有规定时以法条为准）。
    三个费用表依据《诉讼费用交纳办法》（国务院令第481号）第十三条、第十四条，2007-04-01 施行、现行有效；加倍利息依据法释〔2014〕8号第一条。核验日均为 2026-10-09（元典法规库）。
    劳动六件（⑧⑨⑩⑫⑬⑭）依据《劳动合同法》第 46/47/48/82/87/97 条、《劳动合同法实施条例》第 6/7/20/27 条、《职工带薪年休假条例》第 3/5 条及其实施办法第 10/11/12 条、《劳动法》第 44 条、人社厅函〔2025〕1 号、法释〔2020〕26号第 36–39 条、法释〔2025〕12号第 14/15 条、人社部竞业合规指引第 13 条、《工伤保险条例》第 35/36/37/64 条；⑪ 执行款清偿依据《民法典》第 561 条、法释〔2014〕8号第 1/4 条（核验日 2026-10-13，元典法规库）。</div>`;
}

/* ---------------- 主循环 ---------------- */
// 助理页对外暴露（onclick 内联 + smoke 探针）
window.asRun = asRun;
window.asMark = asMark;
window.asIgnore = asIgnore;
window.asIgnorePanel = asIgnorePanel;
window.asRemindCard = asRemindCard;
window.updateBell = updateBell;
const VIEWS = {home: viewHome, board: viewBoard, clients: viewClients, cases: viewCases, tasks: viewTasks,
               ledger: viewLedger, new: viewNew, writing: viewWriting, intake: viewIntake,
               fees: viewFees, deadlines: viewDeadlines, archive: viewArchive, letters: viewLetters, contracts: viewContracts, trace: viewTrace,
               scatter: viewScatter, sohan: viewSohan, assistant: viewAssistant, calc: viewCalc};
function pageHead() {
  const m = PAGE_META[TAB] || [TAB, ''];
  return `<div class="ptitle"><h1>${esc(m[0])}</h1>`
    + (m[1] ? `<span class="sub">${esc(m[1])}</span>` : '')
    + `<span class="right">${todayLine()}</span></div>`;
}
async function render() {
  const main = $('#main');
  window.scrollTo(0, 0);                // 批 77：切页回顶——长页滚动后切页，新页不再停在半腰
  main.style.opacity = '0.45';          // 批 69：切页加载反馈（旧页压暗＝正在加载）
  try {
    const html = await VIEWS[TAB]();
    // 批 C：台账助理用宽版（聊天区铺满视口；其余页保持 1180 居中）
    const wide = (TAB === 'assistant') ? ' wide' : '';
    main.innerHTML = `<div class="page${wide}">${pageHead()}${html}</div>`;
    main.style.opacity = '';              // 批 69：加载完成恢复
    if (TAB === 'board') boardBindDrag();   // 批 55b：泳道拖拽滚动 + 滚轮横移
    if (OVERVIEW) {
      const o = OVERVIEW, t = o.tasks || {};
      toast(`客户 ${(o.clients || {}).count || 0}｜案件夹 ${o.cases.count}｜未完成任务 ${t.open || 0}`
        + `（逾期 ${t.overdue || 0}）｜期限 7 日内 ${o.deadlines.due_count} 项`);
    } else { toast('就绪'); }
  } catch (e) {
    main.innerHTML = `<div class="page">${pageHead()}` +
      `<div class="card"><h2 class="red">加载失败</h2><pre>${esc(e.message)}</pre>
      <div class="hint">确认工作台进程在运行、台账文件可访问。</div></div></div>`;
    main.style.opacity = '';              // 批 69：失败也要恢复
  }
}
(async function init() {
  /* 批 47b：页脚地址动态化——页脚=真实连接地址，一眼自证「窗口连的是谁」
     （12:33 实踩：硬编码 8799 让用户误判窗口连错，实际已连 8899） */
  try { document.getElementById('footAddr').textContent = '127.0.0.1:' + (location.port || '8899'); } catch (e) {}
  try { fetch('/api/health').then(r => r.json()).then(h => {
    const el = document.getElementById('footLedger');
    if (el && h && h.ledger) el.textContent = h.ledger;
  }).catch(() => {}); } catch (e) {}
  TAB = tabFromHash();
  renderNav();
  try { OVERVIEW = await api('/api/overview'); } catch (e) {}
  await render();
  if (!location.hash) location.replace(location.pathname + '#home');
})();

/* ---------- 心跳：证明「还有页面开着」；服务空闲会自动关停 ---------- */
let IDLE_LIMIT = 120;
function idleBanner(text) {
  let el = document.getElementById('idle-banner');
  if (!el) {
    el = document.createElement('div');
    el.id = 'idle-banner';
    el.style.cssText = 'position:sticky;top:0;z-index:99;background:var(--alert-wash);color:var(--alert);' +
      'padding:8px 14px;font-size:13px;border-bottom:1px solid oklch(0.89 0.05 22)';
    document.body.insertBefore(el, document.body.firstChild);
  }
  el.textContent = text;
  el.style.display = 'block';
}
function idleUnbanner() {
  const el = document.getElementById('idle-banner');
  if (el) el.style.display = 'none';
}
async function beat() {
  if (QUITTING) return;
  try {
    const r = await fetch('/api/ping', {cache: 'no-store'});
    if (!r.ok) throw new Error('bad');
    const d = await r.json();
    if (d && d.idle_limit) IDLE_LIMIT = d.idle_limit;
    idleUnbanner();
  } catch (e) {
    idleBanner('工作台已自动关闭（长时间没在用时，后台会自己停）。' +
      '双击桌面「法律工作台」图标即可重新打开本页。');
  }
  updateBell();          // 铃铛挂心跳（不新增轮询请求）
}
setInterval(beat, 15000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) beat(); });
window.addEventListener('focus', beat);
window.addEventListener('hashchange', () => { const k = tabFromHash(); if (k !== TAB) go(k); });
beat();

/* ---------- 优雅关停（2026-09-13）：替代桌面「关闭法律工作台」图标 ---------- */
async function quitWorkbench() {
  if (!confirm('确认关闭工作台后台服务？\n下次双击桌面「法律工作台」会自动再启动。')) return;
  QUITTING = true;
  try { await api('/api/quit'); } catch (e) { /* 服务已断也视作成功 */ }
  document.body.innerHTML =
    '<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;'
    + 'height:100vh;font:15px/1.8 -apple-system,\'PingFang SC\',\'Microsoft YaHei\',sans-serif;'
    + 'color:var(--muted);background:var(--paper)">'
    + '<div style="font:600 22px/1.4 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:var(--ink)">法律工作台已关闭</div>'
    + '<div style="margin-top:10px">现在可以直接关闭这个窗口。</div>'
    + '<div>下次双击桌面「法律工作台」图标会自动再启动。</div></div>';
  setTimeout(() => { try { window.close(); } catch (e) {} }, 1200);
}

/* ============================================================ 批 39d 留痕与文件管理
   双区语义：**过程区** = 案卷内既有 `08-过程文件`；**定稿区** = 案卷内既有编号目录。
   **不新建目录**（新建＝制造第三种口径）。本页只做：分区判定 / 体检 / 归集。
   归集＝**只移动不删除**，且必须人点确认。 */
let TRACE_IDX = null, TRACE_CASE = null, TRACE_SEL = new Set();

async function viewTrace() {
  CURRENT_VIEW = 'trace';
  const d = await api('/api/trace/index');
  TRACE_IDX = d;
  const want = hashParam('case');
  if (want) return traceOpen(want);        // 有 case 参数 → 直接渲染该案详情
  const rowsHtml = traceRowsHtml();
  return `
  <div class="card">
    <h2 style="margin:0 0 6px">留痕与文件管理 <span class="muted" style="font-size:12px">批 39d · 只读体检 + 一键归集（只移动）</span></h2>
    <div class="muted" style="font-size:12px;line-height:1.9">
      <b>双区口径</b>（不新建目录，直接用案卷既有结构）：<br>
      · <b>过程区</b> = ${esc((d.conventions.process || []).join(' / '))} —— 草稿、备份、旧版本都进这里<br>
      · <b>定稿区</b> = ${esc((d.conventions.final || []).join(' / '))} —— 只放定稿/盖章/提交版<br>
      · <b>过期版本不删只移</b>（本项目铁律：案卷只移动不删除）
    </div>
  </div>
  <div class="card">
    <div class="kpis" style="display:flex">
      <div class="kpi"><b>${d.case_count}</b><span>案卷</span></div>
      <div class="kpi"><b class="warn">${d.family_total}</b><span>版本族（同一文书多版本）</span></div>
      <div class="kpi"><b>${d.alias_total}</b><span>目录口径不一</span></div>
      <div class="kpi"><b>${d.naming_total}</b><span>命名缺锚点</span></div>
      <div class="kpi"><b class="red">${d.junk_total}</b><span>中间产物残留</span></div>
      <div class="kpi"><b class="warn">${d.misplace_total || 0}</b><span>错位（该在别处）</span></div>
    </div>
    <div class="muted" style="font-size:12px;margin-top:8px">
      体检时间 ${esc(d.generated_at)} · 点案件名进入分区详情（<b>只读</b>，不会动任何文件）
    </div>
  </div>
  <div class="card">
    <table class="tbl">
      <thead><tr><th>案件</th><th class="r">过程区</th><th class="r">定稿区</th>
        <th class="r">版本族</th><th class="r">口径不一</th><th class="r">命名缺锚</th><th class="r">残留</th><th class="r">错位</th><th></th></tr></thead>
      <tbody>${rowsHtml}</tbody>
    </table>
  </div>`;
}

function traceRowsHtml() {
  const d = TRACE_IDX || { cases: [] };
  const rows = d.cases || [];
  const body = rows.length ? rows.map(c => `
    <tr>
      <td><b>${esc(c.case)}</b></td>
      <td class="r n">${c.process_count}</td>
      <td class="r n">${c.final_count}</td>
      <td class="r n${c.family_count ? ' warn' : ''}">${c.family_count || ''}</td>
      <td class="r n${c.alias_count ? ' warn' : ''}">${c.alias_count || ''}</td>
      <td class="r n${c.naming_count ? ' warn' : ''}">${c.naming_count || ''}</td>
      <td class="r n${c.junk_count ? ' bad' : ''}">${c.junk_count || ''}</td>
      <td class="r n${c.misplace_count ? ' warn' : ''}">${c.misplace_count || ''}</td>
      <td><button class="btn sm" onclick="traceGoto('${fid(c.case)}')">分区详情</button></td>
    </tr>`).join('') : `<tr><td colspan="9" class="muted">没有匹配的案卷</td></tr>`;
  return body;
}

/* 局部重绘用（保留 patchTrace 兼容 smoke 断言） */
function patchTrace() {
  patchInner('traceRows', traceRowsHtml());
}


/* ============================================================ 批 39e：错位体检区块
   三分类口径（程序性 / 过程性 / 证据）见 trace_ops.classify_role。
   **只报不改** —— 归位动作必须另走确认流程。 */
function traceMisplaceHtml(d) {
  const mp = d.misplace || {};
  if (!mp.total && !mp.unknown_n) {
    return `<div class="card"><h3 style="margin:0 0 6px">⑤ 错位体检
      <span class="muted" style="font-size:12px">只报不改</span></h3>
      <div class="muted">没有发现错位</div></div>`;
  }
  const sec = (title, list, n, cols) => {
    if (!n) return '';
    const rows = (list || []).slice(0, 30).map(x => {
      // 归位复选框：仅「有计划目标」且「非待人工确认」的可勾
      const canRefile = x.suggest && !x.pending;
      const chk = canRefile
        ? `<input type="checkbox" class="refile-chk" data-rel="${esc(x.rel || x.name)}" data-dest="${esc(x.suggest)}">`
        : `<span class="muted" style="font-size:11px">—</span>`;
      const act = canRefile ? '' :
        `<div class="muted" style="font-size:11px">需你判后再归</div>`;
      return `<tr>
      <td>${chk}</td>
      <td style="font-size:12px">${esc(x.name)}</td>
      <td class="muted" style="font-size:11px">${esc(x.where)}</td>
      <td>${x.pending
          ? `<span class="tag warn">待人工确认</span><div class="muted" style="font-size:11px">${esc(x.pending)}</div>`
          : (x.suggest ? `<b>→ ${esc(x.suggest)}</b>` : '<span class="muted">—</span>')}
          ${act}</td>
      <td class="muted" style="font-size:11px">${esc(x.why)}</td>
    </tr>`;
    }).join('');
    return `<div style="margin-top:10px">
      <div style="font-size:13px;font-weight:500">${title} <span class="muted">（${n} 件）</span></div>
      <table class="tbl"><thead><tr><th style="width:28px"></th><th>文件</th><th>现在在哪</th><th>建议去</th><th>判据</th></tr></thead>
      <tbody>${rows}</tbody></table>
      ${n > 30 ? `<div class="muted" style="font-size:12px">（仅显示前 30 条，共 ${n} 条）</div>` : ''}
    </div>`;
  };
  return `<div class="card">
    <h3 style="margin:0 0 6px">⑤ 错位体检<span class="muted" style="font-size:12px">只报不改</span></h3>
    <div class="muted" style="font-size:12px;line-height:1.9;margin-bottom:8px">
      <b>三分类口径</b>：<b>程序性</b>＝要交法院的文书（起诉状/申请书/清单）·
      <b>证据</b>＝当事人或第三方给的原始材料 · <b>过程性</b>＝办案中间产物（AI 草稿/对照件/备份），
      唯一去处是 <b>08-过程文件</b>。<br>
      这里列出「该在 A 目录却躺在 B 目录」的文件。判不出的进「待人工判定」，<b>不猜</b>。<br>
      <b>带「待人工确认」标记的请你自己看</b>——工具只能看出「它像要交的文书」，
      <b>看不出「它到底交没交出去」</b>（名字带「撤回/备查/草稿/如申请」的尤其可疑）。
    </div>
    ${sec('① 程序性文件躺在过程区（最该先搬）', mp.court_in_proc, mp.court_in_proc_n)}
    ${sec('② 过程性文件躺在正式区', mp.proc_in_court, mp.proc_in_court_n)}
    ${sec('③ 证据材料躺在错区', mp.evidence_in_wrong, mp.evidence_in_wrong_n)}
    <div class="muted" style="font-size:12px;margin-top:10px">
      另有 <b>${mp.unknown_n || 0}</b> 件判据未命中（待人工判定）——这类**不报明细**，避免噪音。
    </div>
    <div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--line)">
      <button class="btn" onclick="traceRefile()">归位选中项</button>
      <button class="btn ghost" onclick="traceRefilePickAll()">全选可归位项</button>
      <span class="muted" style="font-size:12px;margin-left:8px">
        勾选上面表格左侧的框 → 点「归位选中项」。执行前会先给你看清单，二次确认后才动；
        <b>只移动不删除</b>，并写 <code>_归位留痕.md</code>（可回溯）。
      </span>
    </div>
  </div>`;
}
window.traceMisplaceHtml = traceMisplaceHtml;

/* 批 39e：错位归位（勾选 → 预览 → 二次确认 → 执行） */
function traceRefilePickAll() {
  document.querySelectorAll('.refile-chk').forEach(c => { c.checked = true; });
}

function traceRefilePick() {
  const out = [];
  document.querySelectorAll('.refile-chk:checked').forEach(c => {
    out.push({ rel: c.getAttribute('data-rel'), dest: c.getAttribute('data-dest') });
  });
  return out;
}

async function traceRefile() {
  const moves = traceRefilePick();
  if (!moves.length) { alert('请先勾选要归位的文件'); return; }
  const caseName = TRACE_CASE || hashParam('case');
  if (!caseName) { alert('未取到案卷名，请重新进入该案卷'); return; }
  let plan;
  try {
    plan = await post('/api/trace/refile', { case: caseName, moves: moves, confirm: false });
  } catch (e) { alert('预览失败：' + (e.message || e)); return; }
  if (plan.blocked_count) {
    alert('有 ' + plan.blocked_count + ' 项被拦，请先剔除：\n' +
          plan.blocked.slice(0, 5).map(b => b.rel + ' — ' + b.why).join('\n'));
    return;
  }
  const lines = plan.items.slice(0, 20).map(i => '  ' + i.name + '  →  ' + i.dest).join('\n');
  const more = plan.items.length > 20 ? ('\n  … 另 ' + (plan.items.length - 20) + ' 件') : '';
  if (!confirm('将移动 ' + plan.items.length + ' 个文件（只移动不删除，会写留痕）：\n\n' +
               lines + more + '\n\n确认执行？')) return;
  try {
    const r = await post('/api/trace/refile', { case: caseName, moves: moves, confirm: true });
    alert('已归位 ' + r.moved + ' 件' + (r.failed && r.failed.length ? ('，失败 ' + r.failed.length) : '') +
          '。\n留痕：' + ((r.logs || [])[0] || '—'));
    await traceGoto(caseName);
  } catch (e) { alert('执行失败：' + (e.message || e)); }
}

window.traceRefile = traceRefile;
window.traceRefilePickAll = traceRefilePickAll;

function fid(s) { return String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'"); }

/* 批 39d：显式挂 window（与散件页同约定，供 smoke 探针与内联 onclick 调用） */
window.viewTrace = viewTrace;
window.traceOpen = traceOpen;
window.traceGoto = traceGoto;
window.traceBack = traceBack;
window.traceGather = traceGather;
window.traceRowsHtml = traceRowsHtml;
window.patchTrace = patchTrace;
window.fid = fid;

/* 进单案详情：**必须走框架渲染**（viewTrace 返回 HTML 字符串由 render() 落 DOM）。
   ⚠️ 2026-09-17 实修：原先按钮直接 onclick="traceOpen(...)" → 返回值被丢弃、页面不动
   （无 JS 报错，静默失效）。改为改 hash + 调 render()，与框架同一路径。 */
async function traceGoto(name) {
  location.hash = 'trace?case=' + encodeURIComponent(name);
  TAB = 'trace';
  renderNav();
  await render();
}

/* 返回总览：清掉 hash 里的 case 参数 */
async function traceBack() {
  location.hash = 'trace';
  TAB = 'trace';
  renderNav();
  await render();
}

async function traceOpen(name) {
  const d = await api('/api/trace/case?case=' + encodeURIComponent(name));
  TRACE_CASE = d; TRACE_SEL = new Set();
  const fams = d.version_families || [];
  return `
  <div class="card">
    <button class="btn sm" onclick="traceBack()">← 返回总览</button>
    <h2 style="margin:8px 0 4px">${esc(d.case)}</h2>
    <div class="muted" style="font-size:12px">
      共 ${d.total} 个文件 · 过程区 <b>${d.process_count}</b> · 定稿区 <b>${d.final_count}</b>
      · 残留 <b class="bad">${d.junk.length}</b> · 版本族 <b>${d.version_family_count}</b>
    </div>
  </div>
  <div class="card">
    <h3 style="margin:0 0 6px">① 版本族（同一文书的多个版本）<span class="muted" style="font-size:12px">只报不改</span></h3>
    <div class="muted" style="font-size:12px;margin-bottom:8px">
      一族内 <b>有定稿标记的留定稿区，其余（v1/v2/无标记）建议归入过程区</b>。
      勾选后点下方「归集到过程区」—— 同一族**至少保留定稿的那份**，其余才移。
    </div>
    ${fams.length ? fams.map((g, i) => `
      <details style="margin:6px 0;border:1px solid var(--line);border-radius:6px;padding:6px 10px">
        <summary style="cursor:pointer">
          <b>${esc(g.sig)}</b> · ${g.count} 版
          ${g.has_final ? '<span class="tag ok">含定稿</span>' : '<span class="tag warn">无定稿</span>'}
          · 目录 ${esc(g.dir === '.' ? '案卷根' : g.dir)}
        </summary>
        <table class="tbl" style="margin-top:6px">
          <thead><tr><th style="width:34px"></th><th>文件</th><th class="r">版本</th><th>日期</th><th>标记</th><th>位置</th></tr></thead>
          <tbody>${g.files.map(f => {
            const keep = (f.marks || []).indexOf('final') >= 0;
            const inProc = /^08-过程文件/.test(g.dir);
            return `<tr>
              <td>${(keep || inProc) ? '<span class="muted">—</span>'
                : `<input type="checkbox" class="traceChk" value="${esc(f.name)}" data-dir="${esc(g.dir)}">`}</td>
              <td>${esc(f.name)}</td>
              <td class="r n">${f.ver === null ? '' : f.ver}</td>
              <td class="muted" style="font-size:11px">${esc((f.dates || []).join(' '))}</td>
              <td>${(f.marks || []).map(m => `<span class="tag ${m === 'final' ? 'ok' : m === 'draft' ? 'warn' : 'muted'}">${m}</span>`).join(' ')}</td>
              <td class="muted" style="font-size:11px">${esc(g.dir === '.' ? '案卷根' : g.dir)}</td>
            </tr>`;
          }).join('')}</tbody>
        </table>
      </details>`).join('') : '<div class="muted">没有检测到多版本文书</div>'}
    <div style="margin-top:10px">
      <button class="btn sm" onclick="traceGather('${fid(d.case)}')">归集勾选项到过程区</button>
      <span class="muted" style="font-size:12px">（先出计划让你核对，再确认；只移动不删除）</span>
    </div>
  </div>
  <div class="card">
    <h3 style="margin:0 0 6px">② 目录口径体检<span class="muted" style="font-size:12px">只报不改</span></h3>
    ${d.alias_dirs.length ? `<table class="tbl"><thead><tr><th>目录</th><th>建议统一为</th><th>原因</th></tr></thead><tbody>
      ${d.alias_dirs.map(a => `<tr><td>${esc(a.dir)}</td><td><b>${esc(a.suggest)}</b></td>
        <td class="muted" style="font-size:12px">${esc(a.why)}</td></tr>`).join('')}</tbody></table>`
      : '<div class="muted">目录名口径一致</div>'}
  </div>
  <div class="card">
    <h3 style="margin:0 0 6px">③ 命名合规体检<span class="muted" style="font-size:12px">只报不改</span></h3>
    <div class="muted" style="font-size:12px;margin-bottom:6px">
      同行口径（Trialday·邹义 / 文鳐·谢国才）：文件名宜含
      <b>序号 + 文书名 + 日期 + 版本/定稿标记</b>。以下定稿区文件缺锚点。
    </div>
    ${d.naming_issues.length ? `<table class="tbl"><thead><tr><th>文件</th><th>问题</th></tr></thead><tbody>
      ${d.naming_issues.map(x => `<tr><td style="font-size:12px">${esc(x.rel)}</td>
        <td class="muted" style="font-size:12px">${esc(x.why)}</td></tr>`).join('')}</tbody></table>
      ${d.naming_issue_count > d.naming_issues.length
        ? `<div class="muted" style="font-size:12px;margin-top:6px">（仅显示前 ${d.naming_issues.length} 条，共 ${d.naming_issue_count} 条）</div>` : ''}`
      : '<div class="muted">命名合规</div>'}
  </div>
  <div class="card">
    <h3 style="margin:0 0 6px">④ 中间产物残留<span class="muted" style="font-size:12px">只报不改</span></h3>
    ${d.junk.length ? `<table class="tbl"><thead><tr><th>文件</th><th>原因</th></tr></thead><tbody>
      ${d.junk.slice(0, 40).map(x => `<tr><td style="font-size:12px">${esc(x.rel)}</td>
        <td class="muted" style="font-size:12px">${esc(x.reason)}</td></tr>`).join('')}</tbody></table>
      ${d.junk.length > 40 ? `<div class="muted" style="font-size:12px;margin-top:6px">（仅显示前 40 条，共 ${d.junk.length} 条）</div>` : ''}`
      : '<div class="muted">无残留</div>'}
  </div>
  ${traceMisplaceHtml(d)}`;
}

async function traceGather(caseName) {
  const chks = Array.from(document.querySelectorAll('.traceChk:checked'));
  if (!chks.length) { alert('请先勾选要归集的文件'); return; }
  const rels = chks.map(c => (c.getAttribute('data-dir') === '.' ? '' : c.getAttribute('data-dir') + '/') + c.value);
  const plan = await api('/api/trace/gather', {}, {});
  const body = { case: caseName, rels: rels };
  const p = await apiPost('/api/trace/gather', body);
  if (p.error) { alert('预览失败：' + p.error); return; }
  if (!p.count) { alert('没有可归集的条目' + (p.blocked.length ? '\\n被拦：' + p.blocked.map(b => b.rel + '（' + b.why + '）').join('\\n') : '')); return; }
  const line = p.items.map(i => '  ' + i.rel + '  →  ' + i.name).join('\\n');
  const blocked = p.blocked.length ? '\\n\\n被拦 ' + p.blocked_count + ' 项：\\n' + p.blocked.map(b => '  ' + b.rel + '（' + b.why + '）').join('\\n') : '';
  if (!confirm('将把 ' + p.count + ' 个文件移动到：\\n' + p.proc_dir + '\\n\\n' + line + blocked + '\\n\\n只移动，不删除。确认执行？')) return;
  const r = await post('/api/trace/gather', Object.assign({ confirm: true }, body));
  if (r.error) { alert('执行失败：' + r.error); return; }
  alert('已移动 ' + r.moved + ' 个文件\\n留痕：' + (r.log || '（无）'));
  traceOpen(caseName);
}

/* ================= 批 39h：归档区导入（批量催收件 / 差旅报销）=================
   用户 2026-09-18：「加导入的功能？之前我不是提过类似的要求」→ 照批 30 发票归案同一模式：
   上传只落暂存区 → 人点确认才落盘；只新增不覆盖；写留痕；不写台账。 */
let ARCHIVE_CTX = {zones: [], staged: null, zone: '差旅报销'};

function archiveImportCardHtml() {
  const z = ARCHIVE_CTX.zone;
  const subs = (ARCHIVE_CTX.zones.find(x => x.zone === z) || {}).subs || [];
  const subOpts = subs.map(v => `<option>${esc(v)}</option>`).join('');
  const now = new Date();
  const ym = now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0');
  return `<div class="card"><h2>导入到归档区（批量催收件 / 差旅报销）</h2>
    <div class="row">
      <select id="arZone" onchange="archiveZoneChange()">
        ${ARCHIVE_CTX.zones.map(x => `<option value="${esc(x.zone)}"${x.zone === z ? ' selected' : ''}>${esc(x.zone)}</option>`).join('')}
      </select>
      <input type="file" id="arFiles" multiple>
      <button class="act" onclick="archiveUpload()">上传到暂存区</button>
      <button class="act ghost" onclick="archiveRefresh()">刷新清单</button>
    </div>
    <div class="hint">上传<b>只写暂存区</b>（案卷不动）；下面选好「目标 + 月份/对象」再点导入，
      <b>点确认才真的挪文件</b>。重名自动加 <code>-2</code>；每次导入写留痕（可回溯）。</div>
    <div class="row" id="arCtl">
      <span class="dim">差旅报销：</span>
      <input id="arMonth" value="${esc(ym)}" size="8" placeholder="YYYY-MM">
      <select id="arSub"><option value="">（交通方式）</option>${subOpts}</select>
      <span class="dim">｜批量催收件：</span>
      <input id="arGroup" size="14" placeholder="对象名（如 徐富强）">
    </div>
    <div id="arOut"></div>
    <div id="arZoneOut"></div></div>`;
}

window.archiveZoneChange = async function() {
  const sel = $('#arZone');
  if (sel) ARCHIVE_CTX.zone = sel.value;
  await archiveRefresh();
};

window.archiveRefresh = async function() {
  const out = $('#arOut');
  try {
    const [st, zs] = await Promise.all([
      api('/api/archive/staged'),
      api('/api/archive/zone?zone=' + encodeURIComponent(ARCHIVE_CTX.zone))]);
    ARCHIVE_CTX.staged = st;
    const z = $('#arZoneOut');
    if (z) z.innerHTML = archiveZoneHtml(zs);
    out.innerHTML = archiveStagedHtml(st);
  } catch (e) { if (out) out.innerHTML = `<div class="red">读取失败：${esc(e.message)}</div>`; }
};

window.archiveUpload = async function() {
  const out = $('#arOut'), inp = $('#arFiles');
  if (!inp || !inp.files || !inp.files.length) {
    out.innerHTML = '<div class="warn">先选文件，再点「上传到暂存区」。</div>'; return;
  }
  const fd = new FormData();
  for (const fl of inp.files) fd.append('files', fl, fl.name);
  out.innerHTML = '<div class="dim">上传中…</div>';
  try {
    const r = await fetch('/api/archive/upload', {method: 'POST', body: fd});
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
    inp.value = '';
    await archiveRefresh();
  } catch (e) { out.innerHTML = `<div class="red">上传失败：${esc(e.message)}</div>`; }
};

function archiveStagedHtml(st) {
  if (!st || !st.total) return '<div class="dim">暂存区是空的 —— 上传后会出现在这里。</div>';
  let h = `<div class="hint">暂存区 ${st.total} 件待导入：</div><table>
    <tr><th>来源批次</th><th>文件</th><th class="r">大小</th><th>操作</th></tr>`;
  (st.batches || []).forEach(b => (b.files || []).forEach(f => {
    h += `<tr><td class="dim">${esc(b.batch)}</td><td>${esc(f.name)}</td>
      <td class="r">${Math.round(f.size / 1024)} KB</td>
      <td><button class="act" onclick="archiveImportOne('${fid(f.rel)}')">导入</button></td></tr>`;
  }));
  return h + '</table>';
}

function archiveZoneHtml(z) {
  if (!z || !z.exists) return '<div class="dim">归档区还没建（首次导入时会自动建）。</div>';
  let h = `<div class="hint">归档区「${esc(z.zone)}」现有 <b>${z.total}</b> 件 · ${esc(z.desc)}</div>
    <table><tr><th>分组</th><th class="r">件数</th><th class="r">大小</th><th>样例</th></tr>`;
  (z.groups || []).forEach(g => {
    h += `<tr><td>${esc(g.name)}</td><td class="r">${g.count}</td>
      <td class="r">${Math.round(g.size / 1024)} KB</td>
      <td class="dim">${esc((g.sample || []).join('、'))}</td></tr>`;
  });
  return h + '</table>';
}

window.archiveImportOne = async function(rel) {
  const zone = ARCHIVE_CTX.zone;
  const body = {zone: zone, rel: rel};
  const mEl = $('#arMonth'), sEl = $('#arSub'), gEl = $('#arGroup');
  if (zone === '差旅报销') {
    body.month = (mEl && mEl.value || '').trim();
    body.sub = (sEl && sEl.value || '').trim();
    if (!body.sub) { alert('请先选「交通方式」（打车/动车/…）'); return; }
  } else {
    body.group = (gEl && gEl.value || '').trim();
    if (!body.group) { alert('请先填「对象名」（如 徐富强）'); return; }
  }
  const out = $('#arOut');
  try {
    const pv = await post('/api/archive/import', body);
    if (!pv.ok) {
      out.innerHTML = `<div class="red">不能导入：${esc((pv.warnings || []).join('；'))}</div>`;
      return;
    }
    const p = pv.plan || {};
    const dstShow = p.dst.replace(/^.*A个案./, 'A个案\\');
    if (!confirm('确认导入？\n\n' + p.orig_name + '\n→ ' + dstShow
                 + '\n\n（只移动，写留痕）')) return;
    const r = await post('/api/archive/import', Object.assign({confirm: true}, body));
    out.innerHTML = `<div class="ok">已导入：${esc(r.newname || '')} → <code>${esc(dstShow)}</code></div>`;
    await archiveRefresh();
  } catch (e) { out.innerHTML = `<div class="red">导入失败：${esc(e.message)}</div>`; }
};

window.archiveImportCardHtml = archiveImportCardHtml;










