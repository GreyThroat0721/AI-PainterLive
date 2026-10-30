/* AI 绘画直播间 · 控制面板逻辑 */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
let S = null;
let modelOptions = [];
let qrTimer = null;

async function http(path, body, method) {
  const opts = { method: method || (body ? 'POST' : 'GET') };
  if (body) { opts.headers = { 'Content-Type': 'application/json' }; opts.body = JSON.stringify(body); }
  const res = await fetch(path, opts);
  const text = await res.text();
  try { return JSON.parse(text); } catch { return { ok: res.ok, raw: text }; }
}
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const basename = (p) => String(p || '').split(/[\\/]/).pop();
const confirmTwice = (msg) => window.confirm(msg);

/* 输入框正在编辑时不覆盖，避免定时刷新把主人正在敲的字冲掉 */
function setVal(sel, value) {
  const el = $(sel);
  if (!el || el === document.activeElement) return;
  el.value = value;
}
function isEditingBasic() {
  const el = document.activeElement;
  return !!el && ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)
    && !!el.closest('#page-basic') && el.type !== 'button' && el.type !== 'checkbox';
}

async function loadState(skipForms) {
  S = await http('/api/state');
  renderOverview(); renderQuota(); renderReview(); renderMemory();
  renderCards('knowledge', 'knowledgeList'); renderCards('known', 'knownList');
  renderPorts(); renderAbout();
  if (!skipForms && !isEditingBasic()) renderBasic();
}

/* ---------- 总览 ---------- */
function renderOverview() {
  const sess = S.session || {};
  $('#stRevenue').textContent = '¥' + (sess.revenue_yuan ?? 0).toFixed(2);
  $('#stCost').textContent = '¥' + (sess.cost_yuan ?? 0).toFixed(4);
  $('#stGen').textContent = sess.generated ?? 0;
  $('#stFree').textContent = (sess.free_left ?? 0) + ' / ' + (sess.free_pool ?? 0);
  const c = S.comfy || {};
  $('#stComfy').textContent = c.online ? '在线' : '离线';
  $('#stComfy').style.color = c.online ? 'var(--ok)' : 'var(--err)';
  $('#comfyBox').innerHTML = c.online
    ? `<div>地址：${esc(c.url)}</div><div>版本：${esc(c.system?.comfyui_version || '—')}</div><div>模型：${esc((c.models || []).slice(0, 2).join(', ') || '—')}</div>`
    : `<div style="color:var(--err)">离线</div><div>地址：${esc(c.url || '')}</div><div class="muted">${esc(c.error || '')}</div>`;
  const last = S.last_danmaku || {};
  const dmInfo = `收到弹幕 <b>${S.danmaku_count ?? 0}</b> 条` +
    (last.text ? ` · 最近：${esc(last.uname)}「${esc(String(last.text).slice(0, 16))}」` : '');
  $('#sessionInfo').innerHTML = `第 <b>${sess.seq || 0}</b> 场 · ${sess.running ? '直播中' : '已关闭'} · API ${S.api_enabled ? '已连接' : '已断开'} · 免费池剩余 <b>${sess.free_left ?? 0}</b><br>${dmInfo}`;
  $('#liveDot').classList.toggle('on', !!sess.running);
  $('#liveText').textContent = sess.running ? '直播中' : '未开播';
  const badge = $('#reviewBadge');
  const n = (S.review || []).length;
  badge.textContent = n;
  badge.classList.toggle('hidden', n === 0);
}

function setProgress(pct, title) {
  $('#progBar').style.width = (pct || 0) + '%';
  $('#progPct').textContent = (pct || 0) + '%';
  if (title) $('#progTitle').textContent = title;
}

/* ---------- 基础设置 ---------- */
function renderBasic() {
  const b = S.config.basic, api = S.config.api, bili = S.config.bili, l2d = b.live2d || {};
  setVal('#setPersona', b.persona || '');
  setVal('#setMasterName', b.master_name || '');
  setVal('#setMasterBili', b.master_bili_name || '');
  setVal('#setTrigger', b.trigger || '');
  setVal('#setImageDir', b.image_dir || '');
  setVal('#setL2dName', l2d.name || '');
  setVal('#setL2dDir', l2d.models_dir || '');
  $('#setL2dEnabled').checked = !!l2d.enabled;
  $('#setWebSearch').checked = !!b.web_search;
  $('#setChatReply').checked = b.chat_reply !== false;
  setVal('#setChatInterval', b.chat_interval ?? 8);
  setVal('#setMaxChars', b.max_chars ?? 40);
  setVal('#setThrottle', b.danmaku_throttle ?? 3);
  setVal('#setMasterUid', b.master_uid || '');
  $("#setVoiceRate").value = b.voice_rate || '+6%';

  const l2dSel = $('#setL2dModel');
  const models = S.live2d_models || [];
  l2dSel.innerHTML = (models.length ? models : [l2d.model || '（未检测到模型）'])
    .map(m => `<option value="${esc(m)}">${esc(m)}</option>`).join('');
  l2dSel.value = l2d.model || models[0] || '';

  $('#setSessdata').value = '';
  $('#setSessdata').placeholder = bili.sessdata_set ? '已登录（留空不修改）' : '粘贴 SESSDATA';
  $('#biliLoginHint').textContent = bili.sessdata_set ? '已登录 ✓' : '未登录';
  $('#biliLoginHint').style.color = bili.sessdata_set ? 'var(--ok)' : 'var(--dim)';
  $('#setBuvid').value = '';
  setVal('#setRoomId', bili.room_id || 0);
  setVal('#setApiBase', api.base_url || '');
  $('#setApiKey').value = '';
  $('#setApiKey').placeholder = api.api_key_set ? '已设置（留空不修改）' : 'sk-...';
  setVal('#setApiPrice', api.price_per_1k ?? 0.001);

  const sel = $('#setApiModel');
  if (!modelOptions.length) modelOptions = api.model ? [api.model] : [];
  const list = modelOptions.includes(api.model) ? modelOptions : [api.model, ...modelOptions].filter(Boolean);
  sel.innerHTML = list.map(m => `<option value="${esc(m)}">${esc(m)}</option>`).join('');
  sel.value = api.model || '';

  const sizeSel = $('#setSize');
  sizeSel.innerHTML = (S.sizes || []).map(s => `<option value="${s.w}x${s.h}">${esc(s.label)}</option>`).join('');
  sizeSel.value = `${b.width}x${b.height}`;

  // 声线：后端返回 [{id,name,engine}]，必须带 value 和 engine
  const voices = S.voices || [];
  const voiceSel = $('#setVoice');
  voiceSel.innerHTML = voices.length
    ? voices.map(v => `<option value="${esc(v.id)}" data-engine="${esc(v.engine)}">${esc(v.name)}</option>`).join('')
    : '<option value="">（未获取到声线）</option>';
  const target = b.voice || (voices[0] ? voices[0].id : '');
  if ([...voiceSel.options].some(o => o.value === target)) voiceSel.value = target;
  $('#voiceHint').textContent = voices.length
    ? `共 ${voices.length} 个声线（edge-tts 需联网，系统声线离线可用）`
    : '未获取到声线，点「试听」或刷新页面重试';
}

async function saveBasic() {
  const [w, h] = ($('#setSize').value || '1216x832').split('x').map(Number);
  const vSel = $('#setVoice');
  const engine = vSel.selectedOptions[0]?.dataset.engine || 'edge';
  const basic = {
    persona: $('#setPersona').value,
    master_name: $('#setMasterName').value || '主人',
    master_bili_name: $('#setMasterBili').value,
    trigger: $('#setTrigger').value,
    voice: $('#setVoice').value,
    voice_engine: engine,
    voice_rate: $('#setVoiceRate').value,
    image_dir: $('#setImageDir').value,
    width: w, height: h,
    web_search: $('#setWebSearch').checked,
    chat_reply: $('#setChatReply').checked,
    chat_interval: Number($('#setChatInterval').value) || 0,
    max_chars: Number($('#setMaxChars').value) || 0,
    danmaku_throttle: Number($('#setThrottle').value) || 0,
    master_uid: $('#setMasterUid').value.trim(),
    overlay_integrated: $('#setOverlayIntegrated').checked,
    live2d: {
      enabled: $('#setL2dEnabled').checked,
      name: $('#setL2dName').value,
      model: $('#setL2dModel').value,
      models_dir: $('#setL2dDir').value,
    },
  };
  const api = { base_url: $('#setApiBase').value, model: $('#setApiModel').value, price_per_1k: Number($('#setApiPrice').value) || 0.001 };
  const key = $('#setApiKey').value.trim();
  if (key) api.api_key = key;
  const bili = { room_id: Number($('#setRoomId').value) || 0 };
  const sess = $('#setSessdata').value.trim();
  if (sess) bili.sessdata = sess;
  const buvid = $('#setBuvid').value.trim();
  if (buvid) bili.buvid = buvid;

  await http('/api/config?bili=1', { basic, api, bili });
  $('#saveHint').textContent = '已保存 ' + new Date().toLocaleTimeString();
  await loadState(true);
}

async function fetchModels() {
  $('#modelHint').textContent = '拉取中…';
  const r = await http('/api/models');
  if (!r.ok) { $('#modelHint').textContent = '拉取失败：' + (r.reason || ''); return; }
  modelOptions = r.models || [];
  const sel = $('#setApiModel');
  sel.innerHTML = modelOptions.map(m => `<option value="${esc(m)}">${esc(m)}</option>`).join('');
  sel.value = r.current || modelOptions[0] || '';
  $('#modelHint').textContent = `共 ${modelOptions.length} 个模型`;
}

async function testModel() {
  $('#modelHint').textContent = '测试中…';
  await http('/api/config', { api: { model: $('#setApiModel').value } });
  const r = await http('/api/model/test', {});
  $('#modelHint').textContent = r.ok ? `连通 ✓ ${r.latency_ms}ms · ${r.reply || ''}` : `不通：${r.reason || ''}`;
}

async function testVoice() {
  const sel = $('#setVoice');
  if (!sel.value) { $('#voiceHint').textContent = '先选一个声线'; return; }
  const voice = sel.value;
  const engine = sel.selectedOptions[0]?.dataset.engine || 'edge';
  $('#voiceHint').textContent = '合成中…';
  const r = await http('/api/voice/test', { voice, engine });
  // 只由后端播放，前端不再重复播一遍（否则双声）
  if (!r.ok) { $('#voiceHint').textContent = `失败：${r.reason || ''}`; return; }
  if (r.fallback) {
    $('#voiceHint').textContent = `⚠️ ${voice} 在你这不可用，刚才响的是系统降级音，换个音色试试`;
    $('#voiceHint').style.color = 'var(--warn)';
  } else {
    $('#voiceHint').textContent = `试听中：${voice}（本机播放，就是直播用的声音）`;
    $('#voiceHint').style.color = '';
  }
}

/* ---------- B站扫码登录 ---------- */
async function openQr() {
  $('#qrModal').classList.remove('hidden');
  await refreshQr();
}

async function refreshQr() {
  clearInterval(qrTimer);
  $('#qrStatus').textContent = '正在获取二维码…';
  const r = await http('/api/bili/qrcode');
  if (!r.ok) { $('#qrStatus').textContent = '失败：' + (r.reason || ''); return; }
  $('#qrImg').src = r.img + '&t=' + Date.now();
  $('#qrStatus').textContent = '请用手机 B站 App 扫码';
  qrTimer = setInterval(async () => {
    const p = await http('/api/bili/qrcode/poll?key=' + encodeURIComponent(r.key));
    $('#qrStatus').textContent = p.message || '';
    if (p.status === 'success') {
      clearInterval(qrTimer);
      $('#qrStatus').textContent = '登录成功，已写入弹幕账号';
      $('#biliLoginHint').textContent = '已登录 ✓';
      $('#biliLoginHint').style.color = 'var(--ok)';
      setTimeout(async () => { $('#qrModal').classList.add('hidden'); await loadState(); }, 1200);
    } else if (p.status === 'expired') {
      clearInterval(qrTimer);
    }
  }, 2000);
}

/* ---------- 额度 ---------- */
function renderQuota() {
  const q = S.config.quota, b = S.config.basic;
  setVal('#setFreePerSession', q.free_per_session ?? 50);
  setVal('#setGiftPerQuota', q.gift_per_quota ?? 1);
  setVal('#setMaxTotalPaid', q.max_total_paid ?? 10);
  setVal('#setInheritCap', q.inherit_cap ?? 5);
  $('#setRefundRejected').checked = !!q.refund_rejected_paid;
  setVal('#triggerView', b.trigger || '');
  $('#triggerPreview').textContent = (b.trigger || '') + ' 银色机甲少女';

  const info = $('#ownerInfo');
  if (info) {
    const mc = S.master_check || {};
    const parts = [];
    parts.push('B站给的主播uid: ' + (S.owner_uid ? S.owner_uid : '未取到（进房间后才有）'));
    if (mc.uid) {
      parts.push('最近弹幕: ' + (mc.uname || '?') + '（uid ' + mc.uid + '）');
      parts.push('判定: ' + (mc.result ? '是主人 ✓' : '不是主人 ✗'));
      parts.push('你填的: ' + (mc.master_uid || '(空)') + ' / ' + (mc.master_name || '(空)'));
    }
    info.innerHTML = parts.join('<br>');
  }

  const filter = ($('#quotaFilter').value || '').toLowerCase();
  const rows = (S.quota || []).filter(r => !filter ||
    String(r.uid).includes(filter) || String(r.name || '').toLowerCase().includes(filter));
  $('#quotaCount').textContent = `共 ${(S.quota || []).length} 人 · 免费池 ${(S.session || {}).free_left ?? 0}/${(S.session || {}).free_pool ?? 0}`;
  $('#quotaTable tbody').innerHTML = rows.map(r => `
    <tr>
      <td>${esc(r.uid)}</td><td>${esc(r.name)}</td>
      <td><b>${r.paid ?? 0}</b></td><td>${r.total_paid ?? 0}</td>
      <td>${r.free_used ?? 0}</td><td>${(r.gifts_yuan ?? 0).toFixed(2)}</td>
      <td>
        <button class="btn tiny" data-q="+1" data-uid="${esc(r.uid)}" data-name="${esc(r.name)}">+1</button>
        <button class="btn tiny" data-q="-1" data-uid="${esc(r.uid)}" data-name="${esc(r.name)}">-1</button>
        <button class="btn tiny" data-q="set" data-uid="${esc(r.uid)}" data-name="${esc(r.name)}">改</button>
        <button class="btn tiny" data-q="del" data-uid="${esc(r.uid)}" data-name="${esc(r.name)}" style="color:#e06c6c">删</button>
      </td>
    </tr>`).join('') || '<tr><td colspan="7" class="muted">还没有人点过图</td></tr>';
}

async function saveQuotaRules() {
  await http('/api/config', { quota: {
    free_per_session: Number($('#setFreePerSession').value) || 0,
    gift_per_quota: Number($('#setGiftPerQuota').value) || 1,
    max_total_paid: Number($('#setMaxTotalPaid').value) || 0,
    inherit_cap: Number($('#setInheritCap').value) || 0,
    refund_rejected_paid: $('#setRefundRejected').checked,
  }});
  await loadState();
}

async function quotaSet(uid, name) {
  const paid = prompt('付费额度（留空不改）', '');
  if (paid === null || paid === '') return;
  await http('/api/quota/set', { uid, name, paid: Number(paid) });
  await loadState();
}

/* ---------- 审核 ---------- */
function renderReview() {
  $('#setFullReview').checked = !!(S.config.review || {}).full_review;
  const list = S.review || [];
  $('#reviewHint').textContent = S.config.review.full_review
    ? `全图审查开启：${list.length} 张待审` : '全图审查关闭：出图直接放出';
  const box = $('#reviewList');
  if (!list.length) { box.innerHTML = '<div class="card muted">暂无待审图片</div>'; return; }
  box.innerHTML = list.map(it => `
    <div class="review-card">
      <img src="/rawimg/${encodeURIComponent(basename(it.img))}" alt="">
      <div class="meta">
        <div>点单人：<b>${esc(it.uname)}</b>（UID ${esc(it.uid)}）</div>
        <div>内容：${esc(it.subject || '')} · ${it.kind === 'paid' ? '付费额度' : '免费池'}</div>
      </div>
      <div class="acts">
        <button class="btn primary" data-rv="approve" data-id="${esc(it.id)}">通过并放出</button>
        <button class="btn danger" data-rv="reject" data-id="${esc(it.id)}">拒绝</button>
      </div>
    </div>`).join('');
}

/* ---------- 记忆 ---------- */
function renderMemory() {
  const m = S.config.memory || {};
  $$('input[name="memMode"]').forEach(el => el.checked = el.value === (m.mode || 'danmaku'));
  setVal('#setMemInterval', m.interval_sec ?? 300);
  setVal('#setMemMax', m.max_people ?? 100);

  const filter = ($('#memoryFilter').value || '').toLowerCase();
  const list = (S.memory || []).filter(it => !filter ||
    String(it.uid).includes(filter) || String(it.name || '').toLowerCase().includes(filter) ||
    JSON.stringify(it.notes || []).toLowerCase().includes(filter));
  $('#memoryList').innerHTML = list.map(it => `
    <div class="mem-item">
      <div class="head"><b>${esc(it.name)}</b><span class="muted">UID ${esc(it.uid)} · 分 ${(it.score ?? 0).toFixed(1)}</span></div>
      <div class="notes">${(it.notes || []).map(n => '· ' + esc(n)).join('<br>') || '<span class="muted">（无内容）</span>'}</div>
      <div class="btns">
        <button class="btn tiny" data-mm="edit" data-uid="${esc(it.uid)}" data-name="${esc(it.name)}">加一条</button>
        <button class="btn tiny" data-mm="del" data-uid="${esc(it.uid)}">删除</button>
      </div>
    </div>`).join('') || '<div class="card muted">还没有记忆</div>';
}

/* ---------- 卡片 ---------- */
function renderCards(bucket, boxId) {
  const cards = S[bucket] || [];
  $('#' + boxId).innerHTML = cards.map(c => `
    <div class="k-card">
      <h3>${esc(c.title)} ${c.enabled === false ? '<span class="tag">已停用</span>' : ''}</h3>
      <div class="tags">${(c.tags || []).map(t => `<span class="tag">${esc(t)}</span>`).join('')}</div>
      <p>${esc(c.body)}</p>
      <div class="acts">
        <button class="btn tiny" data-card="toggle" data-bucket="${bucket}" data-id="${esc(c.id)}">${c.enabled === false ? '启用' : '停用'}</button>
        <button class="btn tiny" data-card="del" data-bucket="${bucket}" data-id="${esc(c.id)}">删除</button>
      </div>
    </div>`).join('') || '<div class="card muted">还没有卡片。</div>';
}

/* ---------- 直播画面 / 端口 ---------- */
function renderPorts() {
  const mode = S.overlay_mode || 'integrated';
  const canIntegrated = S.overlay_integrated !== false;
  $$('input[name="overlayMode"]').forEach(el => el.checked = el.value === mode);
  const box = $('#setOverlayIntegrated');
  if (box) box.checked = canIntegrated;
  $('#integratedCard').classList.toggle('hidden', !canIntegrated || mode !== 'integrated');
  $('#customCard').classList.toggle('hidden', mode !== 'custom');

  $('#portTable tbody').innerHTML = (S.ports || []).map(p => `
    <tr><td>${esc(p.name)}</td><td><code>${esc(p.url)}</code></td>
    <td><button class="btn tiny" data-copy="${esc(p.url)}">复制</button></td></tr>`).join('');

  $('#itemTable tbody').innerHTML = (S.overlay_items || []).map(it => `
    <tr>
      <td><input type="checkbox" data-ov="${esc(it.key)}" ${it.enabled ? 'checked' : ''}></td>
      <td>${esc(it.name)}</td>
      <td><code>${esc(it.url)}</code></td>
      <td><button class="btn tiny" data-copy="${esc(it.url)}">复制</button></td>
    </tr>`).join('');
}

function renderAbout() {
  const a = S.about || {};
  $('#aboutAuthor').textContent = a.author || 'F天青';
  $('#aboutBili').textContent = a.bili || 'https://space.bilibili.com/453851400';
  $('#aboutBili').href = a.bili || 'https://space.bilibili.com/453851400';
  // 这张表已经从「关于」页删了，取不到就跳过 —— 以前这里直接 .innerHTML，
  // 元素一没就抛 TypeError，整段初始化中断，表现就是左侧页签全部点不动
  const aboutTable = $('#portTableAbout tbody');
  if (aboutTable) {
    aboutTable.innerHTML = (S.ports || []).map(p => `
      <tr><td>${esc(p.name)}</td><td><code>${esc(p.url)}</code></td>
      <td><button class="btn tiny" data-copy="${esc(p.url)}">复制</button></td></tr>`).join('');
  }
}

/* ---------- 事件 ---------- */
function feed(html) {
  const box = $('#eventFeed');
  const div = document.createElement('div');
  div.innerHTML = `<span class="t">${new Date().toLocaleTimeString()}</span> ` + html;
  box.appendChild(div);
  while (box.children.length > 120) box.removeChild(box.firstChild);
  box.scrollTop = box.scrollHeight;
}

function bind() {
  $$('.nav-item').forEach(btn => btn.addEventListener('click', () => {
    $$('.nav-item').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    $$('.page').forEach(p => p.classList.add('hidden'));
    $('#page-' + btn.dataset.page).classList.remove('hidden');
  }));

  $('#btnFullscreen').addEventListener('click', () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else document.documentElement.requestFullscreen();
  });

  $('#btnLiveStart').addEventListener('click', async () => {
    if (!confirmTwice('确定开启直播？将连接弹幕并启用接入 API。')) return;
    await http('/api/live/start', { confirm: true });
    await loadState();
  });
  $('#btnLiveStop').addEventListener('click', async () => {
    if (!confirmTwice('确定关闭直播？会断开弹幕与接入 API，本场数据保留。')) return;
    await http('/api/live/stop', { confirm: true });
    await loadState();
  });
  $('#btnLiveNew').addEventListener('click', async () => {
    if (!confirmTwice('开启新的一场？本场收益/花费/出图数归零，免费池重新填满。')) return;
    await http('/api/live/new', { confirm: true });
    await loadState();
  });

  $('#btnComfyConnect').addEventListener('click', async () => {
    $('#comfyBox').textContent = '连接中…';
    await http('/api/comfy/connect', { url: S.config.comfy.url });
    await loadState();
  });

  $('#btnSaveBasic').addEventListener('click', saveBasic);
  $('#btnFetchModels').addEventListener('click', fetchModels);
  $('#btnTestModel').addEventListener('click', testModel);
  $('#btnVoiceTest').addEventListener('click', testVoice);
  $('#btnBiliQr').addEventListener('click', openQr);
  $('#btnQrRefresh').addEventListener('click', refreshQr);
  $('#btnQrClose').addEventListener('click', () => {
    clearInterval(qrTimer);
    $('#qrModal').classList.add('hidden');
  });
  $('#btnSaveQuota').addEventListener('click', saveQuotaRules);
  $('#btnOpenFolder').addEventListener('click', () => http('/api/open-folder', { path: $('#setImageDir').value }));
  $('#btnOpenModels').addEventListener('click', () => http('/api/open-folder', { path: $('#setL2dDir').value }));
  $('#btnL2dPreview').addEventListener('click', () => window.open('/live2d?host=0', '_blank', 'width=520,height=760'));

  $('#btnTestDanmaku').addEventListener('click', async () => {
    const text = $('#testText').value.trim();
    if (!text) return;
    await http('/api/inject', { uname: '测试观众', uid: '999', text });
    feed('注入弹幕：' + esc(text));
  });
  $('#btnTestGift').addEventListener('click', async () => {
    const gift = $('#testGift').value.trim() || '小心心';
    const coin = Number($('#testCoin').value) || 1000;
    await http('/api/inject', { uname: '测试观众', uid: '999', gift, coin });
    feed(`注入礼物：${esc(gift)} ${coin} 金瓜子`);
  });

  $('#quotaTable').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-q]');
    if (!btn) return;
    const { q, uid, name } = btn.dataset;
    if (q === 'set') return quotaSet(uid, name);
    if (q === 'del') {
      if (!window.confirm('把 ' + (name || uid) + ' 从额度名单删掉？')) return;
      await http('/api/quota/remove', { uid });
      await loadState();
      return;
    }
    await http('/api/quota/grant', { uid, name, delta: q === '+1' ? 1 : -1 });
    await loadState();
  });
  const btnOwner = $('#btnTakeOwner');
  if (btnOwner) btnOwner.addEventListener('click', async () => {
    const mc = (S.master_check || {});
    if (!mc.uid) return window.alert('还没收到弹幕，先发一条');
    $('#setMasterUid').value = mc.uid;
    if (mc.uname) $('#setMasterBili').value = mc.uname;
    await saveConfig();
    feed('已把 ' + (mc.uname || mc.uid) + ' 设为主人');
    await loadState();
  });
  $('#btnQuotaSet').addEventListener('click', () => quotaSet($('#qUid').value.trim(), $('#qName').value.trim()));
  $('#btnQuotaGrant').addEventListener('click', async () => {
    const uid = $('#qUid').value.trim();
    if (!uid) return window.alert('先填 UID');
    await http('/api/quota/grant', { uid, name: $('#qName').value.trim(), delta: 1 });
    await loadState();
  });
  $('#quotaFilter').addEventListener('input', renderQuota);

  $('#btnSaveTrigger').addEventListener('click', async () => {
    await http('/api/config', { basic: { trigger: $('#triggerView').value } });
    await loadState();
  });
  $('#btnCopyTrigger').addEventListener('click', () => navigator.clipboard?.writeText($('#triggerView').value));

  $('#setFullReview').addEventListener('change', async (e) => {
    await http('/api/config', { review: { full_review: e.target.checked } });
    await loadState();
  });
  $('#reviewList').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-rv]');
    if (!btn) return;
    const { rv, id } = btn.dataset;
    if (rv === 'reject' && !confirmTwice('确定拒绝？这张不会放出，也不会重新生成。')) return;
    btn.disabled = true;
    await http('/api/review/' + rv, { id });
    await loadState();
  });

  $('#btnSaveMemory').addEventListener('click', async () => {
    const mode = $$('input[name="memMode"]').find(el => el.checked)?.value || 'danmaku';
    await http('/api/memory/mode', { mode, interval_sec: Number($('#setMemInterval').value) || 300,
      max_people: Number($('#setMemMax').value) || 100 });
    await loadState();
  });
  $('#btnMemorySet').addEventListener('click', async () => {
    const uid = $('#mUid').value.trim();
    if (!uid) return window.alert('先填 UID');
    await http('/api/memory/set', { uid, name: $('#mName').value.trim(), note: $('#mNote').value.trim() });
    $('#mNote').value = '';
    await loadState();
  });
  $('#memoryList').addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-mm]');
    if (!btn) return;
    const { mm, uid, name } = btn.dataset;
    if (mm === 'del') {
      if (!confirmTwice('删除 ' + (name || uid) + ' 的记忆？')) return;
      await http('/api/memory/delete', { uid });
    } else {
      const note = prompt('追加一条记忆', '');
      if (!note) return;
      await http('/api/memory/set', { uid, name, note });
    }
    await loadState();
  });
  $('#memoryFilter').addEventListener('input', renderMemory);

  $('#btnKnownSave').addEventListener('click', async () => {
    await http('/api/cards/upsert', { bucket: 'known', card: { title: $('#nTitle').value, body: $('#nBody').value } });
    $('#nTitle').value = ''; $('#nBody').value = '';
    await loadState();
  });

  document.body.addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-card]');
    if (btn) {
      const { card: act, bucket, id } = btn.dataset;
      const list = bucket === 'knowledge' ? S.knowledge : S.known;
      const item = (list || []).find(c => c.id === id);
      if (!item) return;
      if (act === 'del') {
        if (!confirmTwice('删除这张卡片？')) return;
        await http('/api/cards/delete', { bucket, id });
      } else if (act === 'toggle') {
        await http('/api/cards/upsert', { bucket, card: { ...item, enabled: item.enabled === false } });
      }
      await loadState();
      return;
    }
    const copy = e.target.closest('button[data-copy]');
    if (copy) {
      try {
        await navigator.clipboard.writeText(copy.dataset.copy);
        copy.textContent = '已复制';
        setTimeout(() => copy.textContent = '复制', 1200);
      } catch {
        window.prompt('手动复制', copy.dataset.copy);
      }
    }
  });

  $$('input[name="overlayMode"]').forEach(el => el.addEventListener('change', async () => {
    await http('/api/overlay', { mode: el.value });
    await loadState();
  }));
  $('#setOverlayIntegrated').addEventListener('change', async (e) => {
    await http('/api/overlay', { integrated: e.target.checked });
    await loadState();
  });
  document.body.addEventListener('change', async (e) => {
    const box = e.target.closest('input[data-ov]');
    if (!box) return;
    await http('/api/overlay', { items: { [box.dataset.ov]: box.checked } });
    await loadState();
  });

  const es = new EventSource('/events');
  es.onmessage = (e) => {
    let ev; try { ev = JSON.parse(e.data); } catch { return; }
    const t = ev.type;
    if (t === 'danmaku') feed(`<span class="k">弹幕</span> ${esc(ev.uname)}：${esc(ev.text)}`);
    else if (t === 'draw') feed(ev.ok
      ? `<span class="k">点图</span> ${esc(ev.uname)}：${esc(ev.subject)} <span style="color:#7ef2a6">已接单</span>`
      : `<span class="k">点图</span> ${esc(ev.uname)}：${esc(ev.subject)} <span style="color:var(--err)">未接 · ${esc(ev.reason || '')}</span>`);
    else if (t === 'queued') feed(`<span class="k">排队</span> ${esc(ev.uname)} · ${esc(ev.subject)}（第 ${ev.pos} 位）`);
    else if (t === 'start') { feed(`<span class="k">开始作画</span> ${esc(ev.uname)} · ${esc(ev.subject)}`); setProgress(0, '正在为 ' + ev.uname + ' 生成：' + ev.subject); }
    else if (t === 'progress') setProgress(Math.floor(ev.value / ev.max * 100));
    else if (t === 'done') { feed(`<span class="k">出图</span> ${esc(ev.file)}`); setProgress(100, '完成：' + (ev.subject || '')); }
    else if (t === 'review') { feed(`<span class="k">待审</span> ${esc(ev.item.uname)}`); loadState(); }
    else if (t === 'rejected') feed(`<span class="k">已拒绝</span> ${esc(ev.item.uname)}`);
    else if (t === 'gift') feed(`<span class="k">礼物</span> ${esc(ev.uname)} ${esc(ev.gift)}×${ev.num}（¥${ev.yuan}，额度+${ev.granted}）`);
    else if (t === 'say') feed(`<span class="k">主播</span> ${esc(ev.text)}`);
    else if (t === 'error') { feed(`<span style="color:var(--err)">出错：${esc(ev.message)}</span>`); setProgress(0, '出图失败'); }
    else if (t === 'link') feed(ev.connected ? '弹幕已连接' : '弹幕断开');
    else if (t === 'live') { feed(ev.new_session ? '新的一场直播' : (ev.running ? '直播已开启' : '直播已关闭')); loadState(); }
  };

  setInterval(async () => {
    const r = await fetch('/api/balance').then(r => r.json()).catch(() => null);
    if (r?.ok) $('#stBalance').textContent = '¥' + (r.total ?? '—');
  }, 30000);
  setInterval(() => loadState(true), 20000);
}

(async function boot() {
  await loadState();
  const b = await http('/api/balance');
  if (b?.ok) $('#stBalance').textContent = '¥' + (b.total ?? '—');
  bind();
})();
