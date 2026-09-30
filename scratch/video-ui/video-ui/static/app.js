'use strict';
const $ = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
const state = { boot: null, run: null, script: null, beats: [], sel: new Set(), timer: null, snap: '', drafts: {} };

async function api(path, opts) {
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch (e) { data = { error: '非 JSON 响应 ' + res.status }; }
  if (!res.ok && !data.error) data.error = 'HTTP ' + res.status;
  return data;
}
const postJSON = (p, body) => api(p, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fileURL = (run, rel) => rel ? '/files/' + encodeURIComponent(run) + '/' + rel.split('/').map(encodeURIComponent).join('/') : '';

/* ------------------------------------------------------------------ boot */
async function boot() {
  state.boot = await api('/api/bootstrap');
  renderEnv();
  renderRuns();
  // 默认加载本 run：URL 带 ?run= 就用它，否则取列表第一个（list_runs 已按 mtime 倒序 = 最新动过的）
  const runs = state.boot.runs || [];
  const wanted = new URLSearchParams(location.search).get('run');
  const pick = (wanted && runs.find(r => r.name === wanted)) || runs[0];
  if (pick) selectRun(pick.name);
}
function renderEnv() {
  const b = state.boot, c = b.config || {};
  $('#chip-dry').classList.toggle('hidden', !b.dry_run);
  $('#chip-key').innerHTML = 'zexapi key: <b>' + (b.key_present ? '已就绪' : '未设置') + '</b>';
  $('#chip-key').className = 'chip ' + (b.key_present ? 'ok' : 'warn');
  const gf = b.gflow || {};
  let gtxt, gok;
  if (!gf.exe_present) { gtxt = 'exe 缺失'; gok = false; }
  else if (!gf.logged_in) { gtxt = '未登录（服务器跑 gflow auth login）'; gok = false; }
  else if (!gf.project_ok) { gtxt = '已登录 · 缺 project'; gok = false; }
  else { gtxt = '已登录' + (gf.account ? ' · ' + gf.account : ''); gok = true; }
  $('#chip-gflow').innerHTML = 'gflow: <b>' + esc(gtxt) + '</b>';
  $('#chip-gflow').className = 'chip ' + (gok ? 'ok' : 'warn');
  const f = b.caps || {};
  $('#chip-ffmpeg').innerHTML = 'ffmpeg: <b>' + [f.ffmpeg ? '有' : '无', f.concat ? 'concat' : '无concat', f.delogo ? 'delogo' : '无delogo', f.libx264 ? 'x264' : ''].filter(Boolean).join(' / ') + '</b>';
  $('#chip-ffmpeg').className = 'chip ' + (f.ffmpeg && f.concat && f.delogo && f.libx264 ? 'ok' : 'warn');
  $('#runs-root').textContent = b.runs_root;
  $('#set-project').value = c.gflow_project || '';
  $('#set-video-model').value = c.video_model || '';
  $('#set-video-size').value = c.video_size || '';
  $('#set-image-backend').value = c.image_backend || 'gflow';
  const wm = c.watermark || {}, vo = c.voiceover || {};
  ['x', 'y', 'w', 'h'].forEach(k => { const el = $('#set-wm-' + k); if (el) el.value = wm[k]; });
  $('#set-vo-enabled').checked = vo.enabled !== false;
  $('#set-vo-lang').value = vo.language || 'American English';
  $('#set-vo-gender').value = vo.gender || 'female';
  syncHeaderH();
}
function renderRuns() {
  $('#runs-list').innerHTML = (state.boot.runs || []).map(r => {
    const active = state.run && state.run.name === r.name;
    return `<div class="run-row ${active ? 'active' : ''}">
      <button class="run-item" data-run="${esc(r.name)}">
        <span class="run-title">${r.mode === 'story' ? '<span class="mtag story">剧情</span>' : ''}${esc(r.title || r.name)}</span>
        <span class="run-meta">${r.duration || '?'}s · ${r.segments || '?'}段 · ${stageLabel((r.flags || {}).final ? 'done' : r.stage)} · ${r.size_mb || '?'}MB</span>
      </button>
      <button class="run-del" data-del="${esc(r.name)}" title="移到 video_runs/_trash（可找回）">✕</button>
    </div>`;
  }).join('') || '<p class="muted pad">还没有 run</p>';
  $$('#runs-list .run-item').forEach(el => el.onclick = () => selectRun(el.dataset.run));
  $$('#runs-list .run-del').forEach(el => el.onclick = () => delRun(el.dataset.del));
  renderRunSwitch();
}

/* ------------------------------------------------ 头部「omni 出片」旁的历史切换下拉 */
function renderRunSwitch() {
  const runs = (state.boot && state.boot.runs) || [];
  $('#run-switch-menu').innerHTML = runs.length ? runs.map(r => {
    const active = state.run && state.run.name === r.name;
    return `<div class="rs-item ${active ? 'active' : ''}">
      <button class="rs-item-main" data-switch="${esc(r.name)}">
        <span class="rs-item-title">${r.mode === 'story' ? '<span class="mtag story">剧情</span>' : ''}${esc(r.title || r.name)}</span>
        <span class="rs-item-meta">${esc(r.mtime || '')} · ${r.duration || '?'}s · ${stageLabel((r.flags || {}).final ? 'done' : r.stage)}</span></button>
      <button class="rs-item-del" data-del="${esc(r.name)}" title="删除（移到 video_runs/_trash，可找回）">✕</button>
    </div>`;
  }).join('') : '<p class="rs-empty">还没有 run</p>';
  $$('#run-switch-menu [data-switch]').forEach(el => el.onclick = () => {
    hideSwitch(); selectRun(el.dataset.switch);
  });
  $$('#run-switch-menu [data-del]').forEach(el => el.onclick = (e) => {
    e.stopPropagation();   // 只删不切、也别关菜单（方便连续删）
    delRun(el.dataset.del);
  });
}
function hideSwitch() { const m = $('#run-switch-menu'); if (m) m.classList.add('hidden'); }
$('#btn-run-switch').onclick = (e) => { e.stopPropagation(); renderRunSwitch(); $('#run-switch-menu').classList.toggle('hidden'); };
document.addEventListener('click', e => { if (!e.target.closest('#run-switch')) hideSwitch(); });
async function delRun(name) {
  const r = (state.boot.runs || []).find(x => x.name === name) || {};
  const label = (r.title || name) + '（' + (r.size_mb || '?') + 'MB，' + (r.duration || '?') + 's / ' + (r.segments || '?') + ' 段）';
  if (!confirm('删除 ' + label + ' ？\n\n整个 run 目录会移到 H:\\AI数据\\video_runs\\_trash\\，随时可以移回来。')) return;
  const res = await api('/api/run/' + encodeURIComponent(name), {
    method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirm: name }) });
  if (res.error) return alert(res.error);
  if (state.run && state.run.name === name) { stopPoll(); state.run = null; delete document.getElementById('work').dataset.mode; $('#empty').classList.remove('hidden'); $('#stagebar').classList.add('hidden'); $$('#work .card').forEach(el => el.classList.add('hidden')); }
  state.boot.runs = await api('/api/runs');
  renderRuns();
  alert(res.mode === 'moved' ? ('已移到：' + res.moved_to) : (res.note || '已删除'));
}
const stageLabel = (s) => ({ intake: '待剧本', script_review: '审剧本', frames: '生帧中', frames_review: '审帧', camera: '运镜', video: '出片', done: '已交付' }[s] || s || '—');

/* ------------------------------------------------------------------ run */
async function selectRun(name) {
  stopPoll();
  const data = await api('/api/run/' + encodeURIComponent(name));
  if (data.error) return alert(data.error);
  state.run = data.state; state.script = data.script; state.beats = data.beats || [];
  state.sel = new Set(); state.drafts = {};
  state.snap = JSON.stringify([data.state, data.beats || []]);
  renderRuns(); renderRun(); startPoll();
}
function startPoll() { state.timer = setInterval(refresh, 2500); }
function stopPoll() { if (state.timer) clearInterval(state.timer); state.timer = null; }
async function refresh() {
  if (!state.run) return;
  const data = await api('/api/run/' + encodeURIComponent(state.run.name));
  if (data.error) {
    // run 在别处被删了：别一直空轮询，退回空态并刷新列表
    if (/不存在|非法/.test(data.error)) {
      stopPoll(); state.run = null; state.snap = '';
      delete document.getElementById('work').dataset.mode;
      $('#stagebar').classList.add('hidden'); $('#empty').classList.remove('hidden');
      $$('#work .card').forEach(el => el.classList.add('hidden'));
      state.boot.runs = await api('/api/runs'); renderRuns();
    }
    return;
  }
  const snap = JSON.stringify([data.state, data.beats]);
  if (snap === state.snap) return;          // 状态没变就不重绘，避免冲掉正在输入的内容
  const wasActive = !!(state.run.job && state.run.job.active);
  const doneKind = (state.run.job || {}).kind;
  state.snap = snap;
  state.run = data.state; state.script = data.script; state.beats = data.beats || [];
  renderRun();
  if (wasActive && !(state.run.job || {}).active) {
    if (doneKind === 'ai_camera') Object.keys(state.drafts).forEach(k => {
      if (/^(cam|asm)\d+$/.test(k)) delete state.drafts[k];
    });
    state.boot.runs = await api('/api/runs'); renderRuns();
  }
}

function renderRun() {
  const r = state.run;
  const story = r.mode === 'story';
  $('#empty').classList.add('hidden');
  $('#stagebar').classList.remove('hidden');
  $$('#work .card').forEach(el => el.classList.add('hidden'));
  ['stage0', 'stage1', 'stage2', 'stage4', 'stage5', 'stage6', 'logcard'].forEach(id => {
    const el = $('#' + id); if (el) el.classList.remove('hidden');
  });
  $('#s0-title').textContent = story ? '角色 / 道具名册' : '本次投放';
  $('#s0-name').textContent = r.title + ' · ' + r.duration + 's · ' + r.segments + ' 段 / ' + r.total_frames + ' 帧';
  $('#s2-sub').textContent = story ? '逐帧串行，这一拍不出场的角色/道具不给模型看它的图'
                                   : '逐帧串行，产品未登场的帧不给模型看产品图';
  document.getElementById('work').dataset.mode = r.mode || 'product';
  $('#s0-product').classList.toggle('hidden', story);
  $('#s0-roster').classList.toggle('hidden', !story);
  if (story) { renderRoster(); }
  else {
    const chars = (r.assets || []).filter(a => a.kind === 'character');
    $('#anchor-state').textContent = '人锚: ' + (r.has_anchor
      ? (chars.length ? chars[0].name + '（第 1 张角色图）' : '已有 anchor 图')
      : '无（首帧可当锚点）');
    renderProductRoster();
    renderCard();
  }
  renderModeFields(story);
  renderBrief();
  renderScript(); renderFrames(); renderCamera(); renderSegments(); renderFinal(); renderLog();
  renderStageBar();
}

/* ------------------------------------------------------- 阶段导航条 */
function stageStates() {
  const r = state.run, f = r.flags || {}, segs = r.segments || 1;
  const camOk = Object.keys(r.camera || {}).filter(k => (r.camera[k] || '').trim()).length >= segs;
  return {
    stage0: story0Ready(r),
    stage1: !!f.script_approved,
    stage2: !!f.frames_gate,
    stage4: camOk,
    stage5: (f.segments_done || 0) >= segs && !!segs,
    stage6: !!f.final,
  };
}
function story0Ready(r) {
  return r.mode === 'story' ? (r.assets || []).length > 0 : !!r.has_product;
}
function renderStageBar() {
  const r = state.run, done = stageStates(), f = r.flags || {};
  const partial = {stage2: (f.frames_done || 0) > 0 && !done.stage2,
                   stage5: (f.segments_done || 0) > 0 && !done.stage5};
  $$('#stagebar .sb').forEach(el => {
    const id = el.dataset.goto;
    el.classList.toggle('done', !!done[id]);
    el.classList.toggle('part', !!partial[id]);
    el.classList.toggle('cur', (r.stage || '') === ({stage0: 'intake', stage1: 'script_review',
      stage2: 'frames', stage4: 'camera', stage5: 'video', stage6: 'done'}[id]));
  });
}
$('#stagebar').onclick = e => {
  const b = e.target.closest('.sb'); if (!b) return;
  const el = document.getElementById(b.dataset.goto);
  if (el) el.scrollIntoView({behavior: 'smooth', block: 'start'});
};

function fillStructures(story) {
  // 两种模式的叙事结构分开：产品=带货向，剧情=类型向（喜剧/悬疑等）。
  const sel = $('#ai-structure'), want = (state.run.structures || []).slice();
  const have = [...sel.options].map(o => o.value).filter(v => v);
  if (have.join('|') === want.join('|')) return;
  const cur = sel.value;
  sel.innerHTML = '<option value="">模型自选</option>' + want.map(v => `<option>${esc(v)}</option>`).join('');
  sel.value = want.includes(cur) ? cur : '';
}

function renderModeFields(story) {
  // Stage 1：剧情模式隐藏产品名/卖点推理，改标签 + 走「大纲→剧本」两步；产品模式原样。
  $('#lbl-ai-name').classList.toggle('hidden', story);
  $('#brief-row').classList.toggle('hidden', story);
  $('#brief-box').classList.toggle('hidden', story);
  $('#story-outline-box').classList.toggle('hidden', !story);
  $('#lbl-ai-brief').firstChild.textContent = story
    ? '故事前提 / 灵感（越具体越好：想要的情感、冲突、场景；人物道具都用第 0 步名册里的名字） '
    : '产品卖点 + 故事方向（越具体越好：材质/部件/朝向，以及想要的情感与场景） ';
  $('#ai-brief').placeholder = story
    ? '深夜的旧公寓，女友意外发现异地男友寄来的包裹，但里面只有一块旧怀表——她不理解，情绪从失落转向好奇。全程只出现女主与旧怀表。'
    : $('#ai-brief').placeholder;
  $('#btn-ai-script').textContent = story ? '② 按大纲写剧本（1 次 chat）' : '让 AI 写剧本';
  fillStructures(story);
  renderStoryDraft();
}

function renderStoryDraft() {
  const r = state.run;
  if (r.mode !== 'story') return;
  const busy = !!(r.job || {}).active, draft = r.story_draft || '';
  const ta = $('#story-draft');
  if (draft && !ta.dataset.dirty) ta.value = draft;
  if (r.story_raw && !ta.value.trim()) ta.value = r.story_raw;
  $('#story-state').textContent = busy ? '大纲生成中…（进度见日志）'
    : (draft ? `✓ 已有大纲（${draft.length} 字）` : '未写大纲');
  $('#story-state').className = 'chip ' + (draft ? 'ok' : 'warn');
  $('#btn-story-outline').disabled = busy || !(r.assets || []).length;
  $('#btn-story-draft-save').disabled = busy;
  $('#btn-ai-script').disabled = busy || !draft.trim();
  $('#btn-ai-script').title = draft.trim() ? '' : '剧情模式要先有故事大纲（点上面「写故事大纲」或自己粘一段）';
}

/* 产品模式：主图 + 细节图 + 多个角色图，同样用小图网格呈现 */
function renderProductRoster() {
  const r = state.run, busy = !!(r.job || {}).active;
  const assets = r.assets || [];
  const prods = assets.filter(a => a.kind === 'product');
  $('#btn-p-asset-add').disabled = busy;
  $('#btn-card').disabled = busy || !prods.length;
  $('#product-roster').innerHTML = assets.length ? assets.map(a => `
    <div class="asset ${a.kind}">
      <img src="${fileURL(r.name, a.thumb || a.file)}" alt="${esc(a.name)}">
      <div class="asset-body">
        <div class="row tight">
          <span class="badge ${a.kind === 'product' ? (a.main ? 'ok' : 'prop') : 'char'}">${a.main ? '产品主图' : (a.kind === 'product' ? '细节图' : '角色')}</span>
          <b class="asset-name" ${a.main ? '' : 'contenteditable="true"'} data-aid="${esc(a.id)}">${esc(a.name || '（未命名）')}</b>
        </div>
        <p class="muted asset-summary">${esc((a.card || {}).summary_zh || (a.kind === 'product'
          ? '产品图：登场帧按「主图 + 一张细节图」发给生图模型'
          : '角色图：第 1 张角色图就是人锚（锁脸基准）'))}</p>
        <div class="row tight">
          <button class="ghost sm danger" data-asset-del="${esc(a.id)}" ${busy ? 'disabled' : ''}>删除</button>
        </div>
      </div>
    </div>`).join('')
    : '<p class="muted">还没有产品图。用上面「加一张」传细节图，或「替换主图」传主图。</p>';
  $$('#product-roster [data-asset-del]').forEach(el => el.onclick = () => delAsset(el.dataset.assetDel));
  $$('#product-roster .asset-name[contenteditable]').forEach(el => el.onblur = () => renameAsset(el.dataset.aid, el.textContent.trim()));
}

function renderRoster() {
  const r = state.run, busy = !!(r.job || {}).active;
  const assets = r.assets || [];
  $('#asset-state').textContent = assets.length
    ? ('名册 ' + assets.length + ' 项 · ' + assets.filter(a => a.has_card).length + ' 已有特征卡')
    : '名册为空';
  $('#asset-state').className = 'chip ' + (assets.length && assets.every(a => a.has_card) ? 'ok' : '');
  $('#btn-asset-add').disabled = busy;
  $('#roster-box').innerHTML = assets.length ? assets.map(a => `
    <div class="asset ${a.kind}">
      <img src="${fileURL(r.name, a.thumb || a.file)}" alt="${esc(a.name)}">
      <div class="asset-body">
        <div class="row tight">
          <span class="badge ${a.kind === 'character' ? 'char' : 'prop'}">${a.kind === 'character' ? '角色' : '道具'}</span>
          <b class="asset-name" contenteditable="true" data-aid="${esc(a.id)}">${esc(a.name || '（未命名）')}</b>
          <span class="chip ${a.has_card ? 'ok' : 'warn'}">${a.has_card ? '有特征卡' : '未提取'}</span>
        </div>
        <p class="muted asset-summary">${esc((a.card || {}).summary_zh || (a.card || {}).summary_en || '还没提取特征，生帧时只按名字写')}</p>
        ${(a.uncertain || []).length ? `<p class="warn">待确认：${a.uncertain.map(esc).join('；')}</p>` : ''}
        <div class="row tight">
          <button class="ghost sm" data-asset-card="${esc(a.id)}" ${busy ? 'disabled' : ''}>提取特征（看图）</button>
          <button class="ghost sm danger" data-asset-del="${esc(a.id)}" ${busy ? 'disabled' : ''}>删除</button>
        </div>
      </div>
    </div>`).join('')
    : '<p class="muted">还没有角色/道具。用上面「加入名册」上传定妆图。</p>';
  $$('#roster-box [data-asset-card]').forEach(el => el.onclick = () => addAssetCard(el.dataset.assetCard));
  $$('#roster-box [data-asset-del]').forEach(el => el.onclick = () => delAsset(el.dataset.assetDel));
  $$('#roster-box .asset-name').forEach(el => el.onblur = () => renameAsset(el.dataset.aid, el.textContent.trim()));
}

function briefText(o, sp) {
  return ['卖点：' + (sp || ''),
    '故事方向：' + (o.title || '') + ' —— 钩子：' + (o.hook || '') + '；情感：' + (o.emotion || '')
    + '；场景：' + (o.scene || '') + '；产品入场：' + (o.product_entry || ''),
    o.why_fit ? ('为什么合适：' + o.why_fit) : ''].filter(x => x && !x.endsWith('：')).join('\n');
}
function renderBrief() {
  const r = state.run, b = r.brief || {}, busy = !!(r.job || {}).active;
  $('#brief-state').textContent = b.at ? ((b.options || []).length + ' 个方向 · ' + b.at) : '未推理';
  $('#brief-state').className = 'chip ' + (b.at ? 'ok' : '');
  $('#btn-brief').disabled = busy;
  $('#brief-box').innerHTML = (b.options || []).map((o, i) => `<div class="opt">
      <div class="row tight"><b>${esc(o.title || ('方向 ' + (i + 1)))}</b>
        <button class="ghost sm" data-pick-opt="${i}">填进卖点框</button></div>
      <p class="muted">钩子：${esc(o.hook || '')}<br>情感：${esc(o.emotion || '')} · 场景：${esc(o.scene || '')}<br>产品入场：${esc(o.product_entry || '')}</p>
      ${o.why_fit ? `<p class="muted">为什么合适：${esc(o.why_fit)}</p>` : ''}</div>`).join('')
    + ((b.avoid || []).length ? `<p class="warn">避开：${b.avoid.map(esc).join('；')}</p>` : '');
  $$('#brief-box [data-pick-opt]').forEach(el => el.onclick = () => {
    $('#ai-brief').value = briefText(b.options[+el.dataset.pickOpt], b.selling_points);
  });
}
$('#btn-brief').onclick = async () => {
  const res = await postJSON('/api/run/' + encodeURIComponent(state.run.name) + '/reason_brief', {
    hint: $('#ai-brief').value.trim(), country: $('#ai-country').value, platform: $('#ai-platform').value });
  if (res.error) return alert(res.error);
  $('#logcard').classList.remove('hidden');
};

/* ------------------------------------------- 剧情模式：① 故事大纲 → ② 剧本 */
$('#story-draft').oninput = () => { $('#story-draft').dataset.dirty = '1'; };
$('#btn-story-outline').onclick = async () => {
  const r = state.run;
  if (!(r.assets || []).length) return alert('先在第 0 步上传至少一个角色/道具图');
  if (!state.boot.key_present) return alert('没读到 ZEXAPI_API_KEY，写大纲要调 1 次 chat');
  const res = await postJSON('/api/run/' + encodeURIComponent(r.name) + '/story_outline', {
    idea: $('#ai-brief').value.trim(), platform: $('#ai-platform').value,
    country: $('#ai-country').value, structure: $('#ai-structure').value,
    extra: $('#ai-extra').value.trim()
  });
  if (res.error) return alert(res.error);
  $('#story-draft').dataset.dirty = '';   // 让轮询把 AI 生成的大纲填回框里
  $('#logcard').classList.remove('hidden');
};
$('#btn-story-draft-save').onclick = async () => {
  const res = await postJSON('/api/run/' + encodeURIComponent(state.run.name) + '/story_draft',
                             { text: $('#story-draft').value });
  if (res.error) return alert(res.error);
  if (res.state) { state.run = res.state; renderRun(); }
  $('#story-draft').dataset.dirty = '';
  alert('已保存 ' + res.chars + ' 字。现在可以点「② 按大纲写剧本」。');
};

function renderCard() {
  const r = state.run, c = r.product_card || {};
  const has = !!(c.summary_en || (c.parts || []).length);
  const busy = !!(r.job || {}).active;
  $('#card-state').textContent = has
    ? '基准卡就绪：' + (c.parts || []).length + ' 个部件' + ((c.uncertain || []).length ? ' · ' + c.uncertain.length + ' 处待确认' : '')
    : '无基准卡';
  $('#card-state').className = 'chip ' + (has ? 'ok' : 'warn');
  $('#btn-card').disabled = busy || !r.has_product;
  $('#card-box').innerHTML = has ? `<details open><summary>产品基准卡 — 生图时逐帧照抄，模型不得自编（点折叠）</summary>
    <table class="beats"><tbody>${(c.parts || []).map((p, i) => `<tr>
      <td class="zh">${esc(p.zh || '')}</td><td class="en">(${i + 1}) ${esc(p.en || '')}</td></tr>`).join('')}</tbody></table>
    <p class="muted"><b>EN</b> ${esc(c.summary_en || '')}</p>
    <p class="muted"><b>中</b> ${esc(c.summary_zh || '')}</p>
    <p class="muted">朝向 ${esc(c.orientation || '—')} · 材质 ${esc(c.material || '—')} · 配色 ${esc(c.colors || '—')}
      · 刻字 ${esc(c.engraving || '—')} · 尺寸参照 ${esc(c.size_ref || '—')}</p>
    ${(c.uncertain || []).length ? `<p class="warn">图上看不清，需你确认：${c.uncertain.map(esc).join('；')}</p>` : ''}
  </details>` : '';
}
$('#btn-card').onclick = async () => {
  const res = await postJSON('/api/run/' + encodeURIComponent(state.run.name) + '/product_card',
                             { notes: $('#card-notes').value });
  if (res.error) return alert(res.error);
  $('#logcard').classList.remove('hidden');
};

/* ------------------------------------------------- story mode: 角色/道具名册 */
$('#btn-asset-add').onclick = async () => {
  const r = state.run;
  const f = $('#inp-asset').files[0];
  if (!f) return alert('先选一张图片');
  const kind = $('#asset-kind').value;
  const name = $('#asset-name').value.trim();
  if (!name) return alert('给这个' + (kind === 'character' ? '角色' : '道具') + '起个名字，剧本要逐字引用');
  const fd = new FormData();
  fd.append('asset', f); fd.append('kind', kind); fd.append('name', name);
  const res = await fetch('/api/run/' + encodeURIComponent(r.name) + '/assets', { method: 'POST', body: fd })
    .then(x => x.json()).catch(e => ({ error: String(e) }));
  if (res.error) return alert(res.error);
  $('#inp-asset').value = ''; $('#asset-name').value = '';
  if (res.state) { state.run = res.state; renderRun(); }
};

async function addAssetCard(aid) {
  const res = await postJSON('/api/run/' + encodeURIComponent(state.run.name) + '/asset_card', { asset_id: aid });
  if (res.error) return alert(res.error);
  $('#logcard').classList.remove('hidden');
}
async function delAsset(aid) {
  const story = state.run.mode === 'story';
  const a = (state.run.assets || []).find(x => x.id === aid) || {};
  const what = a.main ? '产品主图（连同已提取的产品基准卡一起删掉）'
    : (a.kind === 'product' ? '这张产品细节图' : (a.kind === 'character' ? '这个角色图' : '这个道具图'));
  if (!confirm('删除' + what + '？已生成的帧不受影响。' + (story ? '但剧本里引用它的 cast 会校验不过。' : ''))) return;
  const res = await fetch('/api/run/' + encodeURIComponent(state.run.name) + '/assets/' + encodeURIComponent(aid),
                          { method: 'DELETE', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({}) }).then(x => x.json()).catch(e => ({ error: String(e) }));
  if (res.error) return alert(res.error);
  if (res.state) { state.run = res.state; renderRun(); }
}
async function renameAsset(aid, name) {
  const cur = ((state.run.assets || []).find(a => a.id === aid) || {}).name || '';
  if (!name || name === cur) return;
  const res = await postJSON('/api/run/' + encodeURIComponent(state.run.name) + '/assets/' + encodeURIComponent(aid),
                             { name: name });
  if (res.error) return alert(res.error);
  if (res.state) { state.run = res.state; renderRun(); }
}

/* ------------------------------------------------------------------ stage 1 */
const SAMPLE = `{
  "duration": 10,
  "segments_count": 1,
  "global_arc": "（S>=2 必填）全局起承转合一句话",
  "story_hook": "第 1 拍用什么人物/冲突/悬念抓人（不涉及产品）",
  "emotional_core": "怀旧 / 治愈 / 惊喜 / 共鸣 / 向往",
  "product_entry": "产品如何被剧情自然需要",
  "subject_bible": {
    "person": "fair skin, honey-blonde hair tied back, linen shirt, no jewellery",
    "scene": "warm loft at dusk, string lights, wooden table",
    "product": { "name": "sterling silver crescent moon pendant", "features": "opening to the RIGHT, double-row pave CZ on the moon edge, silver cat on inner lower curve, glossy black sphere on the right, 5-pointed star charm on the cable chain bail" }
  },
  "segments": [
    { "phase": "setup", "segment_arc": "本段小弧线", "beats": [
      { "id": 1, "beat_function": "hook", "story_beat": "…", "product_presence": "not_present", "shot_motivation": "…", "emotion": "curiosity", "dialogue": "Oh my god, what is this?", "sound": "soft piano", "duration": 1.2, "keyframe_prompt": "English, 40-90 words, same person and scene, no jewellery" }
    ] }
  ]
}`;
const STORY_SAMPLE = `{
  "duration": 10,
  "segments_count": 1,
  "mode": "story",
  "global_arc": "（S>=2 必填）全局起承转合一句话",
  "story_hook": "第 1 拍用什么人物/冲突/悬念抓人",
  "emotional_core": "怀旧 / 治愈 / 惊喜 / 共鸣 / 向往 / 心疼",
  "ending_note": "结尾怎么收（纯剧情可无行动号召）",
  "subject_bible": {
    "characters": [ { "name": "小美", "person": "East-Asian woman, soft round face, shoulder-length black hair, cream knit sweater, thin gold bracelet", "person_zh": "东亚女性，圆脸，齐肩黑发，米白针织衫，细金手链" } ],
    "props": [ { "name": "旧怀表", "product": "brass pocket watch, round case, engraved leaf pattern on the lid, chain on the left, warm patina", "product_zh": "黄铜怀表，圆形表壳，盖上有叶纹雕刻，左侧挂链，做旧包浆" } ],
    "scene": "old apartment at dusk, wooden desk, warm lamp, film grain",
    "scene_zh": "黄昏旧公寓，木桌，暖台灯，胶片颗粒",
    "tone": "安静、克制、怀旧"
  },
  "segments": [
    { "phase": "setup", "segment_arc": "本段小弧线", "beats": [
      { "id": 1, "beat_function": "hook", "story_beat": "小美在抽屉里发现一块旧怀表，愣住", "cast": ["小美", "旧怀表"], "shot": "特写", "shot_motivation": "先只给手和表，制造悬念", "emotion": "curiosity", "dialogue": "无", "sound": "soft piano", "duration": 1.2, "keyframe_prompt": "English, 40-90 words: who is in frame + action + environment + light + palette, restate each cast member's appearance anchors", "keyframe_prompt_zh": "上面英文的中文逐句对照" }
    ] }
  ],
  "slogan": "",
  "cta": ""
}`;
function renderScript() {
  const r = state.run;
  const j = r.job || {};
  $('#ai-seginfo').textContent = `${r.duration}s · ${r.segments} 段 · ${r.total_frames} 拍`;
  const src = r.script_source;
  const busy = j.active && j.kind === 'ai_script';
  $('#ai-state').textContent = (busy ? '生成中…（进度见下方日志）'
    : src === 'ai_failed' ? '△ 上次 3 次都没过校验，草稿在编辑框里'
    : src ? '上次来源：' + src : '未生成') + (r.country ? ' · 目标国家 ' + r.country : '');
  if (r.country && !$('#ai-country').value) $('#ai-country').value =
    [...$('#ai-country').options].some(o => o.value === r.country) ? r.country : '';
  $('#ai-state').className = 'chip ' + (busy ? '' : src && src !== 'ai_failed' ? 'ok' : src === 'ai_failed' ? 'warn' : '');
  $('#btn-ai-script').disabled = !!j.active || (r.mode === 'story' && !((r.story_draft || '') + '').trim());
  const zm = r.zh_missing || 0;
  $('#zh-state').textContent = zm ? `△ ${zm} 处缺中文对照` : (state.script ? '✓ 中文对照齐全' : '未载入剧本');
  $('#zh-state').className = 'chip ' + (zm ? 'warn' : (state.script ? 'ok' : ''));
  $('#btn-zh').disabled = !!j.active || !zm;
  const ta = $('#script-text');
  if (!ta.value.trim()) {
    if (r.ai_raw) ta.value = r.ai_raw;
    else if (state.script) ta.value = JSON.stringify(state.script, null, 2);
  }
  const approved = (r.review || {}).script_approved;
  $('#script-gate-state').textContent = approved ? '✓ 已通过（Stage 1.5）' : '未通过';
  $('#script-gate-state').className = 'chip ' + (approved ? 'ok' : 'warn');
  if (!state.script) { $('#script-table').innerHTML = ''; return; }
  const s = state.script;
  const story = r.mode === 'story';
  const nameBadge = (list) => (list && list.length)
    ? list.map(n => `<span class="badge cast">${esc(n)}</span>`).join(' ') : '<span class="muted">空镜</span>';
  let entryAt = null;
  if (!story) state.beats.forEach(b => { if (entryAt === null && ['natural', 'focus'].includes(b.product_presence)) entryAt = b.id; });
  const rows = state.beats.map(b => {
    const zh = b.keyframe_prompt_zh || '';
    const castCell = story ? nameBadge(b.cast)
      : (b.product_presence === 'not_present' ? '<span class="badge danger">⚠ 不应出现产品</span>'
         : '<span class="badge ok">✓ 产品' + (b.product_presence === 'focus' ? '为焦点' : '自然在场') + '</span>');
    return `
    <tr class="${story ? 'presence-' + ((b.cast || []).length ? 'natural' : 'not_present') : 'presence-' + esc(b.product_presence)}">
      <td>${b.id}<span class="sub">段${b.segment}</span></td>
      <td>${esc(b.beat_function)}</td>
      <td class="story">${esc(b.story_beat)}</td>
      <td>${castCell}</td>
      <td>${esc(b.emotion)}</td>
      <td>${esc(b.duration)}s</td>
      <td class="prompt-cell"><details><summary>${zh ? esc(zh.slice(0, 44)) + (zh.length > 44 ? '…' : '')
          : '<span class="needzh">缺中文对照</span>'}</summary>
        <p class="zh"><b>中</b> ${esc(zh || '（还没有中文对照，点上方「补中文对照」）')}</p>
        <p class="en"><b>EN</b> ${esc(b.keyframe_prompt)}</p></details></td>
    </tr>`;
  }).join('');
  const bible = (s.subject_bible || {});
  let bibleRows = '';
  if (story) {
    const line = (kind, name, en, zh) => `<tr><td>${kind}</td><td class="zh">${esc(zh || '（缺中文对照）')}</td><td class="en"><b>${esc(name)}</b> ${esc(String(en || ''))}</td></tr>`;
    bibleRows = (bible.characters || []).map(c => line('角色', c.name, c.person, c.person_zh)).join('')
      + (bible.props || []).map(p => line('道具', p.name, p.product, p.product_zh)).join('')
      + (bible.scene ? line('场景', 'scene', bible.scene, bible.scene_zh) : '');
  } else {
    bibleRows = ['product', 'person', 'scene'].filter(k => bible[k]).map(k => `<tr>
      <td>${k}</td><td class="zh">${esc(bible[k + '_zh'] || '（缺中文对照）')}</td><td class="en">${esc(String(bible[k]))}</td></tr>`).join('');
  }
  $('#script-table').innerHTML = `
    <p class="muted">${esc(s.story_hook || '')}</p>
    <p class="muted">情感核心：${esc(s.emotional_core || '')}${story ? '' : ' · 入场：' + esc(s.product_entry || '')}</p>
    ${story ? `<p class="highlight">生帧时按每拍 <code>cast</code> 只带出场角色/道具的参考图，没出场的绝不给模型看（防串脸/串道具）。</p>`
      : `<p class="highlight">产品从第 ${entryAt === null ? '（无）' : entryAt} 拍开始出现，之前所有帧不给模型看产品图。</p>`}
    ${bibleRows ? `<details class="bible"><summary>主体设定（发给生图模型的英文锚点 + 中文对照）</summary>
      <table class="beats"><thead><tr><th>字段</th><th>中文对照</th><th>英文（实际发出）</th></tr></thead><tbody>${bibleRows}</tbody></table></details>` : ''}
    <table class="beats"><thead><tr><th>拍</th><th>function</th><th>剧情</th><th>${story ? '出场角色/道具' : '产品'}</th><th>情绪</th><th>时长</th><th>keyframe_prompt</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}
$('#btn-script-sample') && ($('#btn-script-sample').onclick = () => {
  $('#script-text').value = (state.run && state.run.mode === 'story') ? STORY_SAMPLE : SAMPLE; });
$('#btn-script').onclick = async () => {
  const box = $('#script-text').value.trim();
  if (!box) { $('#script-report').innerHTML =
    '<p class="warn">△ 粘贴框是空的：AI 写剧本成功会自动载入，不必点这里；要手工载入请把完整 script.json（从 { 到 }）贴进框。</p>'; return; }
  const res = await postJSON('/api/run/' + encodeURIComponent(state.run.name) + '/script', { text: $('#script-text').value });
  const rep = $('#script-report');
  const parts = [];
  if (res.error) parts.push('<p class="bad">✗ ' + esc(res.error) + '</p>');
  (res.errors || []).forEach(e => parts.push('<p class="bad">✗ ' + esc(e) + '</p>'));
  (res.warnings || []).forEach(w => parts.push('<p class="warn">△ ' + esc(w) + '</p>'));
  if (res.accepted) parts.push('<p class="good">✓ 剧本校验通过，已写入 script.json。逐拍核对后点下方审核通过。</p>');
  rep.innerHTML = parts.join('');
  if (res.state) { state.run = res.state; }
  if (res.beats) { state.beats = res.beats; state.script = (await api('/api/run/' + encodeURIComponent(state.run.name))).script; }
  renderRun();
};
$('#btn-ai-script').onclick = async () => {
  const story = state.run.mode === 'story';
  const brief = $('#ai-brief').value.trim();
  const draft = $('#story-draft').value.trim();
  if (story && !draft) return alert('剧情模式要先写故事：点「① 先写故事大纲」，或自己把大纲粘进大纲框');
  if (!story && !brief) return alert('先写一句产品卖点 / 故事方向');
  if (!state.boot.key_present) return alert('没读到 ZEXAPI_API_KEY，AI 写剧本要调 1 次 chat');
  const res = await postJSON('/api/run/' + encodeURIComponent(state.run.name) + '/ai_script', {
    product_name: $('#ai-name').value.trim(), platform: $('#ai-platform').value,
    country: $('#ai-country').value,
    structure: $('#ai-structure').value, selling_points: brief || draft,
    extra: $('#ai-extra').value.trim(), story_draft: draft
  });
  if (res.error) return alert(res.error);
  $('#script-text').value = '';
  $('#logcard').classList.remove('hidden');
};
$('#btn-zh').onclick = async () => {
  const res = await postJSON('/api/run/' + encodeURIComponent(state.run.name) + '/translate_script', {});
  if (res.error) return alert(res.error);
  $('#logcard').classList.remove('hidden');
};
$('#btn-script-gate').onclick = () => gate('script', true);
async function gate(which, approve) {
  const res = await postJSON('/api/run/' + encodeURIComponent(state.run.name) + '/gate', { gate: which, approve: approve });
  if (res.error) return alert(res.error);
  state.run = res.state; renderRun();
}

/* ------------------------------------------------------------------ stage 2 */
function renderFrames() {
  const r = state.run, frames = r.frames || {}, review = r.frame_review || {};
  updateBackendHint();
  if (!state.beats.length) { $('#frames-grid').innerHTML = ''; return; }
  $('#frames-grid').innerHTML = state.beats.map(b => {
    const key = String(b.id).padStart(2, '0');
    const info = frames[key] || {}, rv = review[key] || {};
    const url = info.file ? fileURL(r.name, info.thumb || info.file) : '';
    const verdict = rv.verdict || 'pending';
    const badge = r.mode === 'story'
      ? ((b.cast || []).length ? `<span class="badge ok cast">${b.cast.length} 出场</span>` : '<span class="badge muted-badge">空镜</span>')
      : `<span class="badge ${b.product_presence === 'not_present' ? 'danger' : 'ok'}">${b.product_presence === 'not_present' ? '⚠无产品' : '✓有产品'}</span>`;
    return `<div class="frame ${verdict} st-${esc(info.status || 'idle')}" data-idx="${b.id}">
      <div class="frame-img">${url ? `<img loading="lazy" src="${url}" data-full="${fileURL(r.name, info.file)}">` : '<span class="ph">待生成</span>'}
        <label class="pick"><input type="checkbox" data-pick="${b.id}" ${state.sel.has(b.id) ? 'checked' : ''}></label>
        <span class="idx">${b.id}</span>
        ${badge}
      </div>
      <div class="frame-meta">
        <b>${esc(b.beat_function)}</b> · ${esc(b.duration)}s · ${esc(b.emotion)}
        <p>${esc(b.story_beat)}</p>
        <span class="status">${statusText(info)}</span>
        <div class="row tight">
          <button class="ghost sm" data-verdict="approved" data-idx="${b.id}">通过</button>
          <button class="ghost sm" data-verdict="rejected" data-idx="${b.id}">重拍</button>
          <button class="ghost sm" data-preview="${b.id}">prompt</button>
        </div>
        <input class="note" data-note="${b.id}" placeholder="审核意见（重拍时贴给我）" value="${esc(state.drafts['note' + b.id] != null ? state.drafts['note' + b.id] : (rv.note || ''))}">
        ${state.drafts['fp' + b.id] ? `<pre class="promptbox">${esc(state.drafts['fp' + b.id])}</pre>` : ''}
      </div></div>`;
  }).join('');
  const done = state.beats.filter(b => (frames[String(b.id).padStart(2, '0')] || {}).status === 'done').length;
  const appr = state.beats.filter(b => (review[String(b.id).padStart(2, '0')] || {}).verdict === 'approved').length;
  $('#frames-gate-state').textContent = `帧 ${done}/${state.beats.length} 已生成 · ${appr} 通过 · ` +
    ((r.review || {}).frames_approved ? '✓ 审核门已过' : '未过');
  $('#frames-gate-state').className = 'chip ' + ((r.review || {}).frames_approved ? 'ok' : 'warn');
  $$('#frames-grid [data-pick]').forEach(el => el.onchange = () => {
    const i = +el.dataset.pick; el.checked ? state.sel.add(i) : state.sel.delete(i);
  });
  $$('#frames-grid [data-note]').forEach(el => el.oninput = () => {
    state.drafts['note' + el.dataset.note] = el.value;
  });
  $$('#frames-grid [data-verdict]').forEach(el => el.onclick = () => {
    const idx = +el.dataset.idx;
    const note = ($(`#frames-grid [data-note="${idx}"]`) || {}).value || '';
    delete state.drafts['note' + idx];
    postJSON('/api/run/' + encodeURIComponent(r.name) + '/frame_review', { idx: idx, verdict: el.dataset.verdict, note: note }).then(d => { if (d.state) { state.run = d.state; renderRun(); } });
  });
  $$('#frames-grid [data-preview]').forEach(el => el.onclick = async () => {
    const idx = +el.dataset.preview;
    if (state.drafts['fp' + idx]) { delete state.drafts['fp' + idx]; return renderFrames(); }
    const d = await api('/api/run/' + encodeURIComponent(r.name) + '/preview_prompt?frame=' + idx + '&ref_mode=' + $('#f-refmode').value);
    const roleCn = (r) => /PRODUCT/.test(r) ? '产品原图' : /CHARACTER anchor/.test(r) ? '人锚肖像'
      : /frame 01/.test(r) ? '第 1 帧（人+景）' : /PREVIOUS/.test(r) ? '上一帧（动作衔接）' : '参考图';
    state.drafts['fp' + idx] = '【中文对照】' + (d.zh || '（缺，去第 1 步点「补中文对照」）')
      + '\n【参考图】' + (d.note || '') + '\n'
      + (d.refs || []).map((r, i) => '  ' + (i + 1) + ') ' + r + '  ← ' + roleCn(d.roles[i] || '')).join('\n')
      + ((d.refs || []).length ? '\n【实际发给生图模型的英文】\n' : '') + (d.prompt || d.error || '');
    renderFrames();
  });
  $$('#frames-grid img').forEach(img => img.onclick = () => {
    const fr = img.closest('.frame'); if (fr) openLightbox(+fr.dataset.idx);
  });
}
function statusText(info) {
  if (!info.status || info.status === 'idle') return '未生成';
  if (info.status === 'running') return '生成中…';
  if (info.status === 'failed') return '✗ ' + (info.error || '').slice(0, 120);
  return info.backend + ' · ' + (info.seconds || '?') + 's' + (info.refs_note ? ' · ' + info.refs_note : '');
}
$('#btn-frames-all').onclick = () => startFrames('all');
$('#btn-frames-sel').onclick = () => startFrames(Array.from(state.sel));
async function startFrames(targets) {
  if (!targets.length && targets !== 'all') return alert('先勾选要重拍的帧');
  const res = await postJSON('/api/run/' + encodeURIComponent(state.run.name) + '/frames', {
    targets: targets, backend: $('#f-backend').value, ref_mode: $('#f-refmode').value });
  if (res.error) return alert(res.error);
  state.sel.clear();
}
$('#btn-frames-gate').onclick = () => gate('frames', true);
const BACKEND_HINT = {
  gflow: '走 gflow-cli（Google Flow 额度），单帧约 30-90 秒。',
  doubao: '走豆包网页 · {model}：免费，每帧弹一次 Chrome（20-90 秒），别关它；登录掉了会直接报错。',
  zexapi: '走 zexapi nano_banana_pro-2K，花 zexapi 额度。'
};
function updateBackendHint() {
  const el = $('#f-hint');
  if (!el) return;
  const m = ((state.boot || {}).config || {}).doubao_model || 'Seedream 4.5';
  el.textContent = (BACKEND_HINT[$('#f-backend').value] || '').replace('{model}', m);
}
$('#f-backend').onchange = updateBackendHint;

/* ------------------------------------------------------------------ stage 4/5/6 */
function renderCamera() {
  const r = state.run, segs = r.segments || 0;
  if (!segs) { $('#camera-box').innerHTML = ''; return; }
  const locked = !(r.review || {}).frames_approved;
  const busy = !!(r.job || {}).active;
  const segsList = ((state.script || {}).segments) || [];
  $('#camera-box').innerHTML = Array.from({ length: segs }, (_, i) => i + 1).map(k => {
    const saved = (r.camera || {})[String(k)] || '';
    const src = (r.camera_source || {})[String(k)];
    const val = state.drafts['cam' + k] != null ? state.drafts['cam' + k] : saved;
    const warn = (r.camera_warning || {})[String(k)];
    const phase = (segsList[k - 1] || {}).phase || '';
    return `<div class="seg-card"><h4>第 ${k} 段运镜提示词 <em>(${esc(phase)})</em></h4>
      <textarea rows="6" data-cam="${k}" ${locked ? 'disabled' : ''} placeholder="英文 150-300 词，运镜跟人物走，不刻意推向产品">${esc(val)}</textarea>
      <div class="row tight">
        <button class="ghost sm" data-cam-ai="${k}" ${locked || busy ? 'disabled' : ''}>AI 写这段</button>
        <button class="ghost sm" data-cam-save="${k}" ${locked ? 'disabled' : ''}>保存并预览组装结果</button>
        <span class="muted">${val ? prompts_words(val) + ' 个英文词' : '未填'}${src ? ' · ' + esc(src) : ''} ${warn ? '· △ ' + esc(warn) : ''}</span></div>
      <pre class="promptbox${state.drafts['asm' + k] ? '' : ' hidden'}" data-cam-prev="${k}">${esc(state.drafts['asm' + k] || '')}</pre></div>`;
  }).join('') + (locked ? '<p class="warn">关键帧审核门（Stage 3）过了才能填运镜词。</p>' : '');
  $$('#camera-box [data-cam]').forEach(el => el.oninput = () => {
    state.drafts['cam' + el.dataset.cam] = el.value;
  });
  $$('#camera-box [data-cam-save]').forEach(el => el.onclick = async () => {
    const k = el.dataset.camSave;
    const res = await postJSON('/api/run/' + encodeURIComponent(r.name) + '/camera', { segment: +k, text: $(`[data-cam="${k}"]`).value });
    if (res.error) return alert(res.error);
    delete state.drafts['cam' + k];
    state.drafts['asm' + k] = res.assembled || '';
    state.run = (await api('/api/run/' + encodeURIComponent(r.name))).state;
    state.snap = JSON.stringify([state.run, state.beats]);
    renderCamera(); renderSegments();
  });
  $$('#camera-box [data-cam-ai]').forEach(el => el.onclick = () => aiCamera([+el.dataset.camAi]));
  const j = r.job || {}, cs = r.camera_source || {};
  const vo = r.voiceover || {};
  $('#cam-vo').textContent = vo.enabled === false ? '本 run 口播：关闭（视频无旁白）'
    : '口播语言：' + (vo.language || '未设') + '（' + (vo.source || '') + '）· 性别 ' + (vo.gender || '?');
  const camBusy = j.active && j.kind === 'ai_camera';
  $('#cam-state').textContent = camBusy ? '生成中…（进度见日志）'
    : Object.keys(cs).length ? '来源 ' + Object.keys(cs).sort().map(k => '段' + k + ' ' + cs[k]).join('，') : '未生成';
  $('#cam-state').className = 'chip ' + (camBusy || !Object.keys(cs).length ? '' : 'ok');
  $('#btn-cam-all').disabled = !!j.active || locked;
}
async function aiCamera(targets) {
  const res = await postJSON('/api/run/' + encodeURIComponent(state.run.name) + '/ai_camera', { segments: targets });
  if (res.error) return alert(res.error);
  $('#logcard').classList.remove('hidden');
}
$('#btn-cam-all').onclick = () => aiCamera('all');
const prompts_words = (t) => (String(t).match(/[A-Za-z][A-Za-z'\-]*/g) || []).length;

function renderSegments() {
  const r = state.run, segs = r.segments || 0, st = r.segment_status || {};
  if (!segs) { $('#segments-box').innerHTML = ''; return; }
  $('#segments-box').innerHTML = Array.from({ length: segs }, (_, i) => i + 1).map(k => {
    const info = st[String(k)] || {};
    const url = info.file ? fileURL(r.name, info.file) : '';
    return `<div class="seg-card"><h4>第 ${k} 段 <span class="chip ${info.status === 'done' ? 'ok' : (info.status === 'failed' ? 'warn' : '')}">${esc(info.status || '未开始')}</span>
      ${info.task_id ? `<code>${esc(info.task_id)}</code>` : ''} ${info.progress != null && info.status !== 'done' ? esc(info.progress) + '%' : ''}</h4>
      ${info.probe ? `<p class="muted">${info.probe.width}×${info.probe.height} · ${info.probe.vcodec} · ${info.probe.duration}s · ${esc(info.model)} ${esc(info.size)}</p>` : ''}
      ${info.error ? `<p class="bad">${esc(info.error)}</p>` : ''}
      ${url ? `<video src="${url}" controls playsinline></video>` : ''}
      <div class="row tight"><button class="ghost sm" data-seg="${k}">${info.status === 'done' ? '重跑这段' : '只跑这段'}</button></div></div>`;
  }).join('');
  $$('#segments-box [data-seg]').forEach(el => el.onclick = async () => {
    const res = await postJSON('/api/run/' + encodeURIComponent(r.name) + '/segments', { targets: [+el.dataset.seg] });
    if (res.error) alert(res.error);
  });
}
$('#btn-seg-all').onclick = async () => {
  const res = await postJSON('/api/run/' + encodeURIComponent(state.run.name) + '/segments', { targets: 'all' });
  if (res.error) alert(res.error);
};

function renderFinal() {
  const r = state.run, f = r.final || {}, flags = r.flags || {}, j = r.job || {};
  const segs = r.segments || 1;
  $('#final-box').innerHTML = f.clean
    ? `<video src="${fileURL(r.name, f.clean)}" controls playsinline></video>
       <p class="good">成片：<code>${esc(r.name)}/${esc(f.clean)}</code> · 策略 ${esc(f.strategy || '')} · ${f.probe ? f.probe.width + '×' + f.probe.height + ' / ' + f.probe.duration + 's' : ''}</p>
       <p class="muted">分段：<code>segments/segment_1..${segs}.mp4</code> · 关键帧 <code>frames/</code> · 剧本 <code>script.json</code> · 记录 <code>task_log.json</code></p>`
    : `<p class="muted">${flags.segments_done >= segs && segs ? '全部段已出片，点上面按钮拼接。' : '先把全部段生成完（当前 ' + (flags.segments_done || 0) + '/' + segs + '）。'}</p>`;
  $('#btn-finalize').disabled = !(flags.segments_done >= segs && segs);
  const sc = r.selfcheck || {}, items = sc.items || [];
  const auto = items.filter(i => i.verdict !== 'eyes');
  $('#btn-selfcheck').disabled = !!j.active;
  $('#selfcheck-state').textContent = sc.at ? sc.summary + ' · ' + sc.at : '未跑';
  $('#selfcheck-state').className = 'chip ' + (auto.length ? (auto.some(i => i.verdict === 'fail') ? 'warn'
    : auto.some(i => i.verdict === 'warn') ? 'warn' : 'ok') : '');
  $('#selfcheck-box').innerHTML = auto.length ? auto.map(i => `
    <div class="sc ${i.verdict}"><b>${{ pass: '✓', warn: '△', fail: '✗' }[i.verdict] || '·'}</b>
      <span>${esc(i.label)}</span><span class="muted">${esc(i.detail || '')}</span></div>`).join('') : '';
  const eyes = items.filter(i => i.verdict === 'eyes').map(i => i.label)
    .concat(items.length ? [] : (state.boot.eyes_checks || []).map(x => x[1]));
  $('#checklist').innerHTML = `<h4>要人眼核对的 ${eyes.length} 项（自动化查不了）</h4><ul class="checks">${
    eyes.map(t => `<li><label><input type="checkbox"> ${esc(t)}</label></li>`).join('')}</ul>`;
}
$('#btn-selfcheck').onclick = async () => {
  const res = await postJSON('/api/run/' + encodeURIComponent(state.run.name) + '/selfcheck', {});
  if (res.error) alert(res.error);
};
$('#btn-finalize').onclick = async () => {
  const res = await postJSON('/api/run/' + encodeURIComponent(state.run.name) + '/finalize', {});
  if (res.error) alert(res.error);
};

function renderLog() {
  const j = state.run.job || {};
  $('#logcard').classList.toggle('hidden', !(j.lines || []).length);
  $('#log').textContent = (j.lines || []).map(l => l[0] + '  ' + l[1]).join('\n');
  $('#log').scrollTop = $('#log').scrollHeight;
  const busy = j.active;
  ['#btn-frames-all', '#btn-frames-sel', '#btn-seg-all', '#btn-finalize'].forEach(sel => { const el = $(sel); if (el) el.classList.toggle('busy', !!busy); });
  if (busy && j.current) $('#log').textContent = '[' + j.current + ']\n' + $('#log').textContent;
}

/* ------------------------------------------------------------------ chrome */
function newMode() {
  const el = document.querySelector('input[name="new-mode"]:checked');
  return el ? el.value : 'product';
}
function syncNewModal() {
  const story = newMode() === 'story';
  $('#new-product-row').classList.toggle('hidden', story);
  $('#new-story-hint').classList.toggle('hidden', !story);
  $('#new-title-label').firstChild.textContent = story ? '短片名 ' : '产品/战役名 ';
  $('#new-title').placeholder = story ? '旧怀表 30s' : '月猫坠链 20s';
}
$$('input[name="new-mode"]').forEach(el => el.onchange = syncNewModal);
$('#btn-new').onclick = () => { syncNewModal(); $('#new-modal').classList.remove('hidden'); };
$('#btn-cancel').onclick = () => $('#new-modal').classList.add('hidden');
$('#btn-create').onclick = async () => {
  const title = $('#new-title').value.trim();
  if (!title) return alert('起个名');
  const fd = new FormData();
  fd.append('title', title); fd.append('duration', $('#new-duration').value || '10');
  fd.append('mode', newMode());
  if (newMode() === 'product' && $('#new-product').files[0]) fd.append('product', $('#new-product').files[0]);
  const res = await api('/api/run', { method: 'POST', body: fd });
  if (res.error) return alert(res.error);
  $('#new-modal').classList.add('hidden');
  state.boot.runs = await api('/api/runs');
  await selectRun(res.name);
};
$('#inp-product').onchange = async () => {
  const f = $('#inp-product').files[0]; if (!f) return;
  const fd = new FormData(); fd.append('product', f);
  const res = await api('/api/run/' + encodeURIComponent(state.run.name) + '/product', { method: 'POST', body: fd });
  if (res.error) return alert(res.error);
  state.snap = ''; refresh();
};
$('#btn-p-asset-add').onclick = async () => {
  const f = $('#inp-p-asset').files[0];
  if (!f) return alert('先选一张图片');
  const fd = new FormData();
  fd.append('asset', f);
  fd.append('kind', $('#p-asset-kind').value);
  fd.append('name', $('#p-asset-name').value.trim());
  const res = await fetch('/api/run/' + encodeURIComponent(state.run.name) + '/assets', { method: 'POST', body: fd })
    .then(x => x.json()).catch(e => ({ error: String(e) }));
  if (res.error) return alert(res.error);
  $('#inp-p-asset').value = ''; $('#p-asset-name').value = '';
  if (res.state) { state.run = res.state; state.snap = JSON.stringify([res.state, state.beats || []]); renderRun(); }
};
$('#ai-country').onchange = async () => {
  const res = await postJSON('/api/run/' + encodeURIComponent(state.run.name) + '/country',
                             { country: $('#ai-country').value });
  if (res.error) return alert(res.error);
  state.snap = ''; refresh();
};
$('#btn-settings').onclick = () => $('#settings-panel').classList.toggle('hidden');
$('#btn-key').onclick = async () => {
  const res = await postJSON('/api/session_key', { key: $('#set-key').value });
  state.boot.key_present = res.key_present; renderEnv();
  if (!res.key_present) alert('清空了；环境变量 ZEXAPI_API_KEY 也没设');
};
$('#btn-save-settings').onclick = async () => {
  const res = await postJSON('/api/settings', {
    gflow_project: $('#set-project').value, image_backend: $('#set-image-backend').value,
    video_model: $('#set-video-model').value, video_size: $('#set-video-size').value,
    watermark: { x: +$('#set-wm-x').value, y: +$('#set-wm-y').value, w: +$('#set-wm-w').value, h: +$('#set-wm-h').value },
    voiceover: { enabled: $('#set-vo-enabled').checked, language: $('#set-vo-lang').value, gender: $('#set-vo-gender').value }
  });
  state.boot.config = res.config; renderEnv(); $('#settings-panel').classList.add('hidden');
};
$('#lightbox').onclick = e => { if (e.target === $('#lightbox')) closeLightbox(); };
document.addEventListener('keydown', e => {
  const lb = $('#lightbox');
  if (lb.classList.contains('hidden')) return;
  if (e.key === 'Escape') closeLightbox();
  else if (e.key === 'ArrowLeft') lbStep(-1);
  else if (e.key === 'ArrowRight') lbStep(1);
});
$('#lb-prev').onclick = e => { e.stopPropagation(); lbStep(-1); };
$('#lb-next').onclick = e => { e.stopPropagation(); lbStep(1); };
$('#lightbox-img').onclick = e => { e.stopPropagation(); $('#lightbox').classList.toggle('lb-zoom'); };

/* 关键帧灯箱：左右逐帧切换（只列已出图的帧，按拍序） */
function frameShots() {
  const r = state.run, frames = r.frames || {};
  return state.beats.map(b => {
    const info = frames[String(b.id).padStart(2, '0')] || {};
    return info.file ? { id: b.id, full: fileURL(r.name, info.file), beat: b } : null;
  }).filter(Boolean);
}
function openLightbox(id) {
  state.lbList = frameShots();
  state.lbAt = state.lbList.findIndex(f => f.id === id);
  if (state.lbAt < 0) return;
  $('#lightbox').classList.remove('lb-zoom');   // 每次打开回到「适应」
  showLb();
  $('#lightbox').classList.remove('hidden');
}
function closeLightbox() { $('#lightbox').classList.add('hidden'); }
function showLb() {
  const f = state.lbList[state.lbAt]; if (!f) return;
  const img = $('#lightbox-img');
  img.src = f.full;
  img.title = '点击放大 / 还原';
  const b = f.beat;
  $('#lb-cap').textContent = `帧 ${f.id} · ${state.lbAt + 1}/${state.lbList.length} · ${b.beat_function || ''} · ${b.story_beat || ''}`;
  const many = state.lbList.length > 1;
  $('#lb-prev').classList.toggle('hidden', !many);
  $('#lb-next').classList.toggle('hidden', !many);
}
function lbStep(d) {
  if (!state.lbList || !state.lbList.length) return;
  state.lbAt = (state.lbAt + d + state.lbList.length) % state.lbList.length;
  showLb();
}

/* 头部会因窄屏换行变高，粘性偏移（阶段条 top / 卡片 scroll-margin）要跟真实高度对齐 */
function syncHeaderH() {
  const h = document.querySelector('header');
  if (h) document.documentElement.style.setProperty('--hdr-h', h.offsetHeight + 'px');
}
window.addEventListener('resize', syncHeaderH);
window.addEventListener('load', syncHeaderH);

boot();
