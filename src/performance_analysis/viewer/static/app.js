// 元の楽譜と演奏から書き起こした楽譜を並べ、違いの一覧から両方の該当小節へ移る
'use strict';
const $ = (id) => document.getElementById(id);
let data = null;
const hidden = new Set();

function status(s) { $('status').textContent = s; }

async function getJSON(url) {
  const r = await fetch(url);
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

async function init() {
  const o = await getJSON('/api/options');
  const ss = $('score-sel'), ps = $('perf-sel');
  ss.innerHTML = '<option value="">(PEDB の演奏の楽譜)</option>' + o.pedb.map((p) => `<option value="pedb:${p}">${p}</option>`).join('');
  const ed = o.editor.map((e) => `<option value="editor:${e.name}">採譜の GUI: ${e.name}${e.saved ? '' : '(未保存)'}</option>`).join('');
  ps.innerHTML = '<option value="match">PEDB: 人手の対応付け</option><option value="midi">PEDB: 同時記録MIDI</option>' +
                 '<option value="kong">PEDB: 採譜モデルの出力</option>' + ed;
  const q = new URLSearchParams(location.search);
  if (q.get('perf')) ps.value = q.get('perf');
  let sc = q.get('score');
  // 採譜の GUI のプロジェクト名が PEDB の演奏名なら、その楽譜を選ぶ
  if (!sc && ps.value.startsWith('editor:') && o.pedb.includes(ps.value.slice(7))) sc = 'pedb:' + ps.value.slice(7);
  if (sc) { if (sc.startsWith('pedb:')) ss.value = sc; else $('score-path').value = sc.replace(/^file:/, ''); }
  if (sc && ps.value) show();
}

function params() {
  const score = $('score-path').value.trim() ? 'file:' + $('score-path').value.trim() : $('score-sel').value;
  const perf = $('perf-path').value.trim() ? 'file:' + $('perf-path').value.trim() : $('perf-sel').value;
  return { score, perf };
}

async function show() {
  const { score, perf } = params();
  if (!score) { status('楽譜を選んでください。'); return; }
  status('対応付けと楽譜の描画中…');
  try {
    data = await getJSON(`/api/compare?score=${encodeURIComponent(score)}&perf=${encodeURIComponent(perf)}`);
    history.replaceState(null, '', `?score=${encodeURIComponent(score)}&perf=${encodeURIComponent(perf)}`);
    $('orig').innerHTML = data.original_svg;
    $('perf').innerHTML = data.performance_svg;
    renderDiffs();
    const c = data.counts;
    status(`${data.score} / ${data.source}: 違い ${data.diffs.length} 件(${data.seconds} 秒)`);
  } catch (e) { status('表示できませんでした: ' + e.message); }
}

function renderDiffs() {
  const c = data.counts;
  $('summary').textContent = `演奏の音 ${data.n_perf_notes}(装飾の実際の音を除く)`;
  $('legend').innerHTML = Object.entries(data.labels).map(([k, l]) =>
    `<li><input type="checkbox" data-k="${k}" ${hidden.has(k) ? '' : 'checked'}><span class="sw" style="background:${data.colors[k]}"></span>${l} ${c[k]}</li>`).join('');
  $('legend').querySelectorAll('input').forEach((el) => { el.onchange = () => { el.checked ? hidden.delete(el.dataset.k) : hidden.add(el.dataset.k); renderDiffs(); }; });
  const ol = $('diffs');
  ol.innerHTML = '';
  data.diffs.forEach((d) => {
    if (hidden.has(d.kind)) return;
    const li = document.createElement('li');
    li.innerHTML = `<span class="kind" style="color:${data.colors[d.kind]}">● ${data.labels[d.kind]}</span> <span class="m">${d.measure}小節 ${(+d.beat + 1).toFixed(2).replace(/\.?0+$/, '')}拍目</span> `;
    li.appendChild(document.createTextNode(d.desc));
    li.onclick = () => { ol.querySelectorAll('.active').forEach((x) => x.classList.remove('active')); li.classList.add('active'); goto(d.measure); };
    ol.appendChild(li);
  });
}

function measureEl(sheet, n) { return sheet.querySelector(`g.measure[data-n="${CSS.escape(String(n))}"]`); }

// 小節の範囲: 五線の線だけから求める(小節をまたぐクレッシェンドなどを含めない)
function measureRect(m) {
  const lines = m.querySelectorAll(':scope > g.staff > path');
  if (!lines.length) return m.getBoundingClientRect();
  let l = Infinity, t = Infinity, r = -Infinity, b = -Infinity;
  for (const x of lines) { const q = x.getBoundingClientRect(); l = Math.min(l, q.left); t = Math.min(t, q.top); r = Math.max(r, q.right); b = Math.max(b, q.bottom); }
  return { left: l, top: t - 12, width: r - l, height: b - t + 24, bottom: b + 12 };
}

// 小節を枠の上の方に出し、薄く色を付ける
function reveal(sheet, n, flash = true) {
  const m = measureEl(sheet, n);
  if (!m) return false;
  const sr = sheet.getBoundingClientRect(), mr = measureRect(m);
  sheet.scrollTop += mr.top - sr.top - 40;
  if (flash) {
    const r2 = measureRect(m);
    const hl = document.createElement('div');
    hl.className = 'hl';
    hl.style.left = (r2.left - sr.left - 4) + 'px';
    hl.style.top = (r2.top - sr.top + sheet.scrollTop - 4) + 'px';
    hl.style.width = (r2.width + 8) + 'px';
    hl.style.height = (r2.height + 8) + 'px';
    sheet.appendChild(hl);
    setTimeout(() => { hl.style.opacity = 0; }, 1200);
    setTimeout(() => hl.remove(), 2600);
  }
  return true;
}

let syncing = false;
function goto(n) {
  syncing = true;
  reveal($('orig'), n);
  reveal($('perf'), n);
  setTimeout(() => { syncing = false; }, 100);
}

// スクロールを合わせる: 動かした側の、枠の上端に最初に見えている小節に、もう一方をそろえる
function topMeasure(sheet) {
  const top = sheet.getBoundingClientRect().top;
  for (const m of sheet.querySelectorAll('g.measure')) {
    const r = m.getBoundingClientRect();
    if (r.bottom > top + 10) return m.dataset.n;
  }
  return null;
}
let syncTimer = null;
for (const [a, b] of [['orig', 'perf'], ['perf', 'orig']]) {
  $(a).addEventListener('scroll', () => {
    if (syncing || !$('sync').checked) return;
    clearTimeout(syncTimer);
    syncTimer = setTimeout(() => {
      const n = topMeasure($(a));
      if (n === null) return;
      syncing = true;
      reveal($(b), n, false);
      setTimeout(() => { syncing = false; }, 100);
    }, 120);
  });
}

$('btn-show').onclick = show;
$('btn-reload').onclick = show;
$('layout').onchange = (e) => {
  document.body.classList.toggle('only-orig', e.target.value === 'orig');
  document.body.classList.toggle('only-perf', e.target.value === 'perf');
};
init().catch((e) => status('サーバーに接続できません: ' + e.message));
