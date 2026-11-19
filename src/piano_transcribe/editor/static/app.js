// 採譜の確認・修正: ピアノロールの表示・編集、録音と MIDI の再生、再調整、保存
'use strict';

const $ = (id) => document.getElementById(id);
const LO = 21, HI = 108;            // ピアノの音域
const KEYW = 46, RULER = 22, PEDAL = 26;
const COLOR = { model: '#2a78d6', user: '#eb6834', cand: '#1baf7a', muted: '#8a8983', weak: '#e34948',
                text: '#0b0b0b', text2: '#52514e', grid: '#e9e8e4', black: '#f1f0ec', surface: '#fcfcfb' };
const NAMES = ['C', 'C#', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'G#', 'A', 'Bb', 'B'];
const pitchName = (p) => NAMES[p % 12] + (Math.floor(p / 12) - 1);
const isBlack = (p) => [1, 3, 6, 8, 10].includes(p % 12);

// ---- 状態
const S = {
  name: null, duration: 0,
  notes: [], candidates: [], deleted: [], pedal: [],
  sel: new Set(), cursor: 0,
  view: { t0: 0, pps: 120, y0: 0, rowH: 10 },
  undo: [], redo: [], dirty: false, nextId: 1,
  roll: null, audioBuf: null,
  showRoll: true, showCand: false,
};

const canvas = $('roll');
const ctx2 = canvas.getContext('2d');
let W = 0, H = 0;

function status(msg) { $('status').textContent = msg; }
function setDirty(v) { S.dirty = v; $('dirty').classList.toggle('on', v); }

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).error || msg; } catch (e) { /* 本文なし */ }
    throw new Error(msg);
  }
  return r;
}

// ---- 座標
const xOf = (t) => KEYW + (t - S.view.t0) * S.view.pps;
const tOf = (x) => S.view.t0 + (x - KEYW) / S.view.pps;
const yOf = (p) => RULER + (HI - p) * S.view.rowH - S.view.y0;
const pOf = (y) => Math.round(HI - (y + S.view.y0 - RULER) / S.view.rowH + 0.5);
const rollBottom = () => H - PEDAL;

function resize() {
  const r = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  W = r.width; H = r.height;
  canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
  ctx2.setTransform(dpr, 0, 0, dpr, 0, 0);
  clampView();
  draw();
}

function clampView() {
  const v = S.view;
  const maxY = Math.max(0, (HI - LO + 1) * v.rowH - (rollBottom() - RULER));
  v.y0 = Math.min(Math.max(0, v.y0), maxY);
  v.t0 = Math.min(Math.max(-0.5, v.t0), Math.max(0, S.duration - 1));
}

// ---- 描画
let drawPending = false;
function draw() {
  if (drawPending) return;
  drawPending = true;
  requestAnimationFrame(() => { drawPending = false; render(); });
}

function render() {
  const v = S.view;
  ctx2.clearRect(0, 0, W, H);
  ctx2.fillStyle = COLOR.surface;
  ctx2.fillRect(0, 0, W, H);
  ctx2.save();
  ctx2.beginPath(); ctx2.rect(KEYW, RULER, W - KEYW, rollBottom() - RULER); ctx2.clip();
  // 黒鍵の行と C の線
  for (let p = LO; p <= HI; p++) {
    const y = yOf(p);
    if (y > rollBottom() || y + v.rowH < RULER) continue;
    if (isBlack(p)) { ctx2.fillStyle = COLOR.black; ctx2.fillRect(KEYW, y, W - KEYW, v.rowH); }
    if (p % 12 === 0) { ctx2.fillStyle = COLOR.grid; ctx2.fillRect(KEYW, y + v.rowH - 1, W - KEYW, 1); }
  }
  drawTimeGrid(false);
  if (S.showRoll && S.roll) drawRoll();
  const t1 = tOf(W);
  // 見落としの候補
  if (S.showCand) {
    ctx2.setLineDash([3, 3]); ctx2.lineWidth = 1.5;
    for (const n of S.candidates) {
      if (n.off < v.t0 || n.on > t1) continue;
      const x = xOf(n.on), w = Math.max(3, (n.off - n.on) * v.pps), y = yOf(n.pitch);
      ctx2.strokeStyle = S.sel.has(n.id) ? COLOR.text : COLOR.muted;
      ctx2.strokeRect(x + 0.5, y + 1.5, w - 1, v.rowH - 3);
    }
    ctx2.setLineDash([]);
  }
  // 音符
  for (const n of S.notes) {
    if (n.off < v.t0 || n.on > t1) continue;
    const x = xOf(n.on), w = Math.max(3, (n.off - n.on) * v.pps), y = yOf(n.pitch);
    const c = n.src === 'cand' ? COLOR.cand : (n.src === 'user' || n.edited) ? COLOR.user : COLOR.model;
    ctx2.globalAlpha = 0.3 + 0.7 * Math.min(1, n.vel / 110);
    ctx2.fillStyle = c;
    roundRect(x, y + 1, w, v.rowH - 2, 2); ctx2.fill();
    ctx2.globalAlpha = 1;
    if (n.weak) {
      ctx2.setLineDash([3, 2]); ctx2.strokeStyle = COLOR.weak; ctx2.lineWidth = 1.5;
      roundRect(x, y + 1, w, v.rowH - 2, 2); ctx2.stroke(); ctx2.setLineDash([]);
    }
    if (S.sel.has(n.id)) {
      ctx2.strokeStyle = COLOR.text; ctx2.lineWidth = 2;
      roundRect(x, y + 1, w, v.rowH - 2, 2); ctx2.stroke();
    }
  }
  // 範囲選択の枠
  if (drag && drag.mode === 'box' && drag.moved) {
    ctx2.strokeStyle = COLOR.text2; ctx2.setLineDash([4, 3]); ctx2.lineWidth = 1;
    const x = Math.min(drag.x0, drag.x), y = Math.min(drag.y0, drag.y);
    ctx2.strokeRect(x + 0.5, y + 0.5, Math.abs(drag.x - drag.x0), Math.abs(drag.y - drag.y0));
    ctx2.setLineDash([]);
  }
  ctx2.restore();
  drawKeys();
  drawRuler();
  drawPedal();
  // 再生位置
  const xc = xOf(S.cursor);
  if (xc >= KEYW && xc <= W) {
    ctx2.fillStyle = player.playing ? COLOR.user : COLOR.text;
    ctx2.fillRect(Math.round(xc), 0, 1.5, H);
  }
}

function roundRect(x, y, w, h, r) {
  ctx2.beginPath();
  r = Math.min(r, w / 2, h / 2);
  ctx2.moveTo(x + r, y); ctx2.arcTo(x + w, y, x + w, y + h, r); ctx2.arcTo(x + w, y + h, x, y + h, r);
  ctx2.arcTo(x, y + h, x, y, r); ctx2.arcTo(x, y, x + w, y, r); ctx2.closePath();
}

function timeStep() {
  for (const s of [0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60]) if (s * S.view.pps >= 70) return s;
  return 120;
}
const fmtTime = (t) => { const m = Math.floor(t / 60), s = t - 60 * m; return `${m}:${s.toFixed(timeStep() < 1 ? 2 : 0).padStart(timeStep() < 1 ? 5 : 2, '0')}`; };

function drawTimeGrid() {
  const st = timeStep();
  ctx2.fillStyle = COLOR.grid;
  for (let t = Math.ceil(S.view.t0 / st) * st; t < tOf(W); t += st) ctx2.fillRect(Math.round(xOf(t)), RULER, 1, rollBottom() - RULER);
}

function drawRuler() {
  ctx2.fillStyle = '#fff'; ctx2.fillRect(KEYW, 0, W - KEYW, RULER);
  ctx2.fillStyle = COLOR.grid; ctx2.fillRect(KEYW, RULER - 1, W - KEYW, 1);
  ctx2.fillStyle = COLOR.text2; ctx2.font = '11px "Hiragino Sans", sans-serif'; ctx2.textBaseline = 'middle';
  const st = timeStep();
  for (let t = Math.max(0, Math.ceil(S.view.t0 / st) * st); t < tOf(W); t += st) {
    const x = Math.round(xOf(t));
    if (x < KEYW) continue;
    ctx2.fillRect(x, RULER - 6, 1, 6);
    ctx2.fillText(fmtTime(t), x + 3, RULER / 2 - 1);
  }
}

function drawKeys() {
  ctx2.fillStyle = '#fff'; ctx2.fillRect(0, RULER, KEYW, rollBottom() - RULER);
  ctx2.save();
  ctx2.beginPath(); ctx2.rect(0, RULER, KEYW, rollBottom() - RULER); ctx2.clip();
  ctx2.font = '10px "Hiragino Sans", sans-serif'; ctx2.textBaseline = 'middle';
  for (let p = LO; p <= HI; p++) {
    const y = yOf(p);
    if (isBlack(p)) { ctx2.fillStyle = '#3a3936'; ctx2.fillRect(0, y, KEYW * 0.6, S.view.rowH); }
    if (p % 12 === 0) {
      ctx2.fillStyle = COLOR.grid; ctx2.fillRect(0, y + S.view.rowH - 1, KEYW, 1);
      ctx2.fillStyle = COLOR.text2; ctx2.fillText(pitchName(p), KEYW * 0.62, y + S.view.rowH / 2);
    }
  }
  ctx2.restore();
  ctx2.fillStyle = COLOR.grid; ctx2.fillRect(KEYW - 1, RULER, 1, rollBottom() - RULER);
  ctx2.fillStyle = '#fff'; ctx2.fillRect(0, 0, KEYW, RULER);
}

function drawPedal() {
  const y = rollBottom();
  ctx2.fillStyle = '#fff'; ctx2.fillRect(0, y, W, PEDAL);
  ctx2.fillStyle = COLOR.grid; ctx2.fillRect(0, y, W, 1);
  ctx2.fillStyle = COLOR.text2; ctx2.font = '10px "Hiragino Sans", sans-serif'; ctx2.textBaseline = 'middle';
  ctx2.fillText('ペダル', 6, y + PEDAL / 2);
  ctx2.save(); ctx2.beginPath(); ctx2.rect(KEYW, y, W - KEYW, PEDAL); ctx2.clip();
  ctx2.fillStyle = COLOR.model; ctx2.globalAlpha = 0.45;
  for (const [a, b] of S.pedal) {
    if (b < S.view.t0 || a > tOf(W)) continue;
    roundRect(xOf(a), y + 6, Math.max(2, (b - a) * S.view.pps), PEDAL - 12, 2); ctx2.fill();
  }
  ctx2.restore(); ctx2.globalAlpha = 1;
}

// モデルの出力(フレーム × 88鍵)を、4096 フレームずつの画像にして貼る
function buildRoll(buf, frames, keys, fps) {
  const u8 = new Uint8Array(buf);
  const TILE = 4096, tiles = [];
  for (let f0 = 0; f0 < frames; f0 += TILE) {
    const w = Math.min(TILE, frames - f0);
    const c = document.createElement('canvas'); c.width = w; c.height = keys;
    const g = c.getContext('2d'); const img = g.createImageData(w, keys);
    for (let f = 0; f < w; f++) {
      for (let k = 0; k < keys; k++) {
        const a = u8[(f0 + f) * keys + k];
        if (a < 8) continue;
        const i = ((keys - 1 - k) * w + f) * 4;
        img.data[i] = 42; img.data[i + 1] = 120; img.data[i + 2] = 214; img.data[i + 3] = Math.min(255, a * 0.55);
      }
    }
    g.putImageData(img, 0, 0);
    tiles.push({ c, t0: f0 / fps, t1: (f0 + w) / fps });
  }
  S.roll = { tiles, keys };
}

function drawRoll() {
  const top = yOf(LO + S.roll.keys - 1), h = S.roll.keys * S.view.rowH;
  ctx2.imageSmoothingEnabled = false;
  for (const t of S.roll.tiles) {
    if (t.t1 < S.view.t0 || t.t0 > tOf(W)) continue;
    ctx2.drawImage(t.c, xOf(t.t0), top, (t.t1 - t.t0) * S.view.pps, h);
  }
  ctx2.imageSmoothingEnabled = true;
}

// ---- 編集の履歴
const snapshot = () => JSON.stringify({ notes: S.notes, candidates: S.candidates, deleted: S.deleted });
function pushUndo() { S.undo.push(snapshot()); if (S.undo.length > 200) S.undo.shift(); S.redo = []; setDirty(true); }
function restore(js) {
  const o = JSON.parse(js);
  S.notes = o.notes; S.candidates = o.candidates; S.deleted = o.deleted;
  S.sel = new Set([...S.sel].filter((id) => S.notes.some((n) => n.id === id) || S.candidates.some((n) => n.id === id)));
  setDirty(true); updatePanel(); draw();
}
function undo() { if (S.undo.length) { S.redo.push(snapshot()); restore(S.undo.pop()); } }
function redoAct() { if (S.redo.length) { S.undo.push(snapshot()); restore(S.redo.pop()); } }

function markEdited(n) { n.edited = true; n.refined = false; n.weak = false; }

// ---- 当たり判定
function hit(x, y) {
  const t = tOf(x), p = pOf(y);
  const near = (n) => n.pitch === p && t >= n.on - 2 / S.view.pps && t <= Math.max(n.off, n.on + 3 / S.view.pps) + 2 / S.view.pps;
  const sel = S.notes.filter((n) => S.sel.has(n.id) && near(n));
  const n = sel[0] || S.notes.find(near);
  if (n) return { n, cand: false, edge: Math.abs(xOf(n.off) - x) <= 6 && (n.off - n.on) * S.view.pps > 10 };
  if (S.showCand) { const c = S.candidates.find(near); if (c) return { n: c, cand: true, edge: false }; }
  return null;
}

// ---- マウス
let drag = null;
canvas.addEventListener('mousedown', (e) => {
  canvas.focus();
  ensureAudio();
  const r = canvas.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
  if (y < RULER && x > KEYW) {           // 目盛り: 再生位置
    S.cursor = Math.max(0, tOf(x));
    if (player.playing) player.play(S.cursor); else draw();
    drag = { mode: 'cursor' };
    return;
  }
  if (x < KEYW && y > RULER && y < rollBottom()) { player.preview(pOf(y), 70); return; }
  if (y >= rollBottom()) return;
  const h = hit(x, y);
  if (h) {
    if (e.shiftKey) { S.sel.has(h.n.id) ? S.sel.delete(h.n.id) : S.sel.add(h.n.id); }
    else if (!S.sel.has(h.n.id)) { S.sel = new Set([h.n.id]); }
    if (!h.cand) player.preview(h.n.pitch, h.n.vel, 0.25);
    const moving = S.notes.filter((n) => S.sel.has(n.id));
    drag = { mode: h.cand ? 'none' : (h.edge ? 'resize' : 'move'), x0: x, y0: y, moved: false,
             start: moving.map((n) => ({ n, on: n.on, off: n.off, pitch: n.pitch })), lastDp: 0 };
  } else {
    if (!e.shiftKey) S.sel.clear();
    S.cursor = Math.max(0, tOf(x));
    drag = { mode: 'box', x0: x, y0: y, x, y, moved: false, base: new Set(S.sel) };
  }
  updatePanel(); draw();
});

window.addEventListener('mousemove', (e) => {
  const r = canvas.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
  if (!drag) {
    const h = x > KEYW && y > RULER && y < rollBottom() ? hit(x, y) : null;
    canvas.style.cursor = h && h.edge ? 'ew-resize' : h ? 'grab' : 'default';
    return;
  }
  if (drag.mode === 'cursor') { S.cursor = Math.max(0, tOf(x)); draw(); return; }
  if (!drag.moved && Math.hypot(x - drag.x0, y - drag.y0) < 3) return;
  if (drag.mode === 'box') {
    drag.moved = true; drag.x = x; drag.y = y;
    const ta = tOf(Math.min(x, drag.x0)), tb = tOf(Math.max(x, drag.x0));
    const pa = pOf(Math.max(y, drag.y0)), pb = pOf(Math.min(y, drag.y0));
    S.sel = new Set(drag.base);
    const inBox = (n) => n.off >= ta && n.on <= tb && n.pitch >= pa && n.pitch <= pb;
    S.notes.filter(inBox).forEach((n) => S.sel.add(n.id));
    if (S.showCand) S.candidates.filter(inBox).forEach((n) => S.sel.add(n.id));
    updatePanel(); draw(); return;
  }
  if (drag.mode !== 'move' && drag.mode !== 'resize') return;
  if (!drag.moved) { pushUndo(); drag.moved = true; }
  const dt = (x - drag.x0) / S.view.pps;
  const dp = drag.mode === 'move' ? -Math.round((y - drag.y0) / S.view.rowH) : 0;
  for (const s of drag.start) {
    if (drag.mode === 'move') {
      s.n.on = s.on + dt; s.n.off = s.off + dt;
      s.n.pitch = Math.min(HI, Math.max(LO, s.pitch + dp));
    } else {
      s.n.off = Math.max(s.n.on + 0.02, s.off + dt); s.n.fixed_off = true;
    }
    markEdited(s.n);
  }
  if (dp !== drag.lastDp && drag.start.length) { drag.lastDp = dp; player.preview(drag.start[0].n.pitch, drag.start[0].n.vel, 0.2); }
  updatePanel(); draw();
});

window.addEventListener('mouseup', () => { if (drag) { drag = null; updatePanel(); draw(); } });

canvas.addEventListener('dblclick', (e) => {
  const r = canvas.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
  if (x < KEYW || y < RULER || y >= rollBottom()) return;
  const h = hit(x, y);
  if (h && h.cand) { acceptCandidates([h.n.id]); return; }
  if (h) return;
  pushUndo();
  const n = { id: 'u' + S.nextId++, on: tOf(x), off: tOf(x) + 0.25, pitch: pOf(y), vel: 50, src: 'user' };
  markEdited(n);
  S.notes.push(n);
  S.sel = new Set([n.id]);
  player.preview(n.pitch, n.vel, 0.25);
  status(`${pitchName(n.pitch)} を追加しました。R で録音に合わせて再調整できます。`);
  updatePanel(); draw();
});

canvas.addEventListener('wheel', (e) => {
  e.preventDefault();
  const r = canvas.getBoundingClientRect(), x = e.clientX - r.left;
  if (e.ctrlKey || e.metaKey) {
    const t = tOf(x);
    S.view.pps = Math.min(4000, Math.max(5, S.view.pps * Math.exp(-e.deltaY * 0.01)));
    S.view.t0 = t - (x - KEYW) / S.view.pps;
  } else if (e.shiftKey) {
    S.view.t0 += (e.deltaY || e.deltaX) / S.view.pps;
  } else {
    S.view.t0 += e.deltaX / S.view.pps;
    S.view.y0 += e.deltaY;
  }
  clampView(); draw();
}, { passive: false });

// ---- キーボード
window.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
  const mod = e.metaKey || e.ctrlKey;
  const selNotes = () => S.notes.filter((n) => S.sel.has(n.id));
  if (mod && e.key.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? redoAct() : undo(); return; }
  if (mod && e.key.toLowerCase() === 'y') { e.preventDefault(); redoAct(); return; }
  if (mod && e.key.toLowerCase() === 's') { e.preventDefault(); save(); return; }
  if (mod) return;
  if (e.key === ' ') { e.preventDefault(); ensureAudio(); player.playing ? player.stop() : player.play(S.cursor); return; }
  if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); deleteSelected(); return; }
  if (e.key === 'Enter') { acceptCandidates([...S.sel].filter((id) => S.candidates.some((c) => c.id === id))); return; }
  if (e.key.toLowerCase() === 'r') { e.preventDefault(); e.shiftKey ? refineAll() : refineSelected(); return; }
  if (e.key.toLowerCase() === 'p') { playAroundSelection(); return; }
  if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)) {
    const ns = selNotes();
    if (!ns.length) return;
    e.preventDefault(); pushUndo();
    for (const n of ns) {
      if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
        const d = (e.key === 'ArrowUp' ? 1 : -1) * (e.shiftKey ? 12 : 1);
        n.pitch = Math.min(HI, Math.max(LO, n.pitch + d));
      } else {
        const d = (e.key === 'ArrowRight' ? 1 : -1) * (e.shiftKey ? 0.05 : 0.01);
        n.on += d; n.off += d;
      }
      markEdited(n);
    }
    if (e.key === 'ArrowUp' || e.key === 'ArrowDown') player.preview(ns[0].pitch, ns[0].vel, 0.25);
    updatePanel(); draw();
  }
});

function deleteSelected() {
  if (!S.sel.size) return;
  pushUndo();
  for (const n of S.notes) if (S.sel.has(n.id) && n.src === 'model') S.deleted.push({ id: n.id, on: n.on, off: n.off, pitch: n.pitch, vel: n.vel });
  const nd = S.notes.length, nc = S.candidates.length;
  S.notes = S.notes.filter((n) => !S.sel.has(n.id));
  S.candidates = S.candidates.filter((n) => !S.sel.has(n.id));
  status(`${nd - S.notes.length} 音を削除、${nc - S.candidates.length} 個の候補を却下しました。`);
  S.sel.clear(); updatePanel(); draw();
}

function acceptCandidates(ids) {
  if (!ids.length) return;
  pushUndo();
  const set = new Set(ids);
  for (const c of S.candidates.filter((c) => set.has(c.id))) {
    const n = { ...c, src: 'cand' }; markEdited(n); S.notes.push(n);
  }
  S.candidates = S.candidates.filter((c) => !set.has(c.id));
  status(`${ids.length} 個の候補を受け入れました。`);
  updatePanel(); draw();
}

// ---- 再調整(サーバーで、録音に対するモデルの出力から決め直す)
async function refine(ids) {
  if (!ids.length) { status('再調整する音がありません。'); return; }
  status(`${ids.length} 音を再調整中…`);
  const payload = { ids, notes: S.notes.map((n) => ({ id: n.id, on: n.on, off: n.off, pitch: n.pitch, vel: n.vel,
                                                      fixed_off: !!n.fixed_off, fixed_vel: !!n.fixed_vel })) };
  try {
    const res = await (await api(`/api/projects/${encodeURIComponent(S.name)}/refine`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })).json();
    pushUndo();
    let weak = 0, moved = [];
    for (const n of S.notes) {
      const r = res[n.id];
      if (!r) continue;
      moved.push(Math.abs(r.on - n.on) * 1000);
      n.on = r.on;
      if (r.off !== undefined) n.off = r.off;
      if (r.vel !== undefined) n.vel = r.vel;
      n.refined = true; n.weak = r.weak; n.evidence = r.evidence;
      if (r.weak) weak++;
    }
    moved.sort((a, b) => a - b);
    const med = moved.length ? moved[Math.floor(moved.length / 2)] : 0;
    status(`${ids.length} 音を再調整しました(発音時刻の移動 中央値 ${med.toFixed(0)}ms)。` +
           (weak ? ` ${weak} 音は録音に手がかりがなく、指定した時刻のままです(赤い点線)。` : ''));
    updatePanel(); draw();
  } catch (err) { status('再調整に失敗しました: ' + err.message); }
}
const refineSelected = () => refine(S.notes.filter((n) => S.sel.has(n.id)).map((n) => n.id));
const refineAll = () => refine(S.notes.filter((n) => n.edited && !n.refined).map((n) => n.id));

// ---- 再生
let actx = null, audioGain = null, synthGain = null;
function ensureAudio() {
  if (actx) { if (actx.state === 'suspended') actx.resume(); return; }
  actx = new AudioContext();
  audioGain = actx.createGain(); audioGain.gain.value = +$('vol-audio').value; audioGain.connect(actx.destination);
  const comp = actx.createDynamicsCompressor(); comp.connect(actx.destination);
  synthGain = actx.createGain(); synthGain.gain.value = +$('vol-midi').value; synthGain.connect(comp);
  if (S.pendingAudio) decodeAudio(S.pendingAudio);
}

async function decodeAudio(buf) {
  if (!actx) { S.pendingAudio = buf; return; }
  S.pendingAudio = null;
  S.audioBuf = await actx.decodeAudioData(buf);
}

function voice(when, dur, pitch, vel, dest) {
  const f = 440 * Math.pow(2, (pitch - 69) / 12);
  const peak = 0.22 * Math.pow(vel / 127, 1.5);
  const tau = 0.4 + 2.2 * (HI - pitch) / (HI - LO);   // 低い音ほど長く響く
  const g = actx.createGain(), lp = actx.createBiquadFilter();
  lp.type = 'lowpass'; lp.frequency.value = Math.min(9000, f * 8);
  const o1 = actx.createOscillator(), o2 = actx.createOscillator(), g2 = actx.createGain();
  o1.type = 'triangle'; o1.frequency.value = f; o2.type = 'sine'; o2.frequency.value = 2 * f; g2.gain.value = 0.3;
  o1.connect(g); o2.connect(g2); g2.connect(g); g.connect(lp); lp.connect(dest);
  g.gain.setValueAtTime(0, when);
  g.gain.linearRampToValueAtTime(peak, when + 0.004);
  g.gain.setTargetAtTime(0, when + 0.004, tau);
  g.gain.setTargetAtTime(0, when + Math.max(0.03, dur), 0.04);
  const end = when + Math.max(0.03, dur) + 0.3;
  o1.start(when); o2.start(when); o1.stop(end); o2.stop(end);
  return { g, o1, o2, end };
}

const player = {
  playing: false, src: null, timer: null, voices: [], until: null,
  mode() { return document.querySelector('input[name=mode]:checked').value; },
  play(from, dur = null) {
    ensureAudio();
    this.stop();
    const mode = this.mode();
    this.playing = true; this.from = from; this.start = actx.currentTime + 0.06; this.until = dur ? from + dur : null;
    if (mode !== 'midi' && S.audioBuf) {
      this.src = actx.createBufferSource(); this.src.buffer = S.audioBuf; this.src.connect(audioGain);
      this.src.start(this.start, Math.max(0, from), dur || undefined);
    }
    this.queue = mode !== 'audio'
      ? S.notes.filter((n) => n.on >= from && (!this.until || n.on < this.until)).sort((a, b) => a.on - b.on) : [];
    this.qi = 0;
    // 音の予定入れと終わりの判定は一定間隔で行う(描画が間引かれても止まるように)
    this.timer = setInterval(() => this.schedule(), 25);
    this.schedule();
    $('btn-play').textContent = '■ 停止';
    this.tick();
  },
  schedule() {
    const now = actx.currentTime - this.start + this.from;
    if ((this.until && now >= this.until) || now > S.duration + 0.5) { this.stop(); return; }
    while (this.qi < this.queue.length && this.queue[this.qi].on < now + 0.3) {
      const n = this.queue[this.qi++];
      this.voices.push(voice(this.start + (n.on - this.from), n.off - n.on, n.pitch, n.vel, synthGain));
    }
    this.voices = this.voices.filter((v) => v.end > actx.currentTime);
  },
  tick() {
    if (!this.playing) return;
    S.cursor = this.from + Math.max(0, actx.currentTime - this.start);
    const span = (W - KEYW) / S.view.pps;
    if (S.cursor > S.view.t0 + span * 0.9 || S.cursor < S.view.t0) { S.view.t0 = S.cursor - span * 0.1; clampView(); }
    if ((this.until && S.cursor >= this.until) || S.cursor > S.duration + 0.5) { this.stop(); return; }
    draw();
    requestAnimationFrame(() => this.tick());
  },
  stop() {
    if (this.src) { try { this.src.stop(); } catch (e) { /* 停止済み */ } this.src = null; }
    clearInterval(this.timer); this.timer = null;
    if (actx) for (const v of this.voices) { v.g.gain.cancelScheduledValues(actx.currentTime); v.g.gain.setTargetAtTime(0, actx.currentTime, 0.02); }
    this.voices = [];
    this.playing = false;
    $('btn-play').textContent = '▶ 再生';
    draw();
  },
  preview(pitch, vel, dur = 0.4) {
    ensureAudio();
    voice(actx.currentTime + 0.01, dur, pitch, vel, synthGain);
  },
};

function playAroundSelection() {
  const ns = [...S.notes, ...S.candidates].filter((n) => S.sel.has(n.id));
  if (!ns.length) return;
  const t = Math.min(...ns.map((n) => n.on));
  S.cursor = Math.max(0, t - 1.0);
  player.play(S.cursor, 2.5);
}

// ---- パネル
function updatePanel() {
  const ns = S.notes.filter((n) => S.sel.has(n.id));
  const cs = S.candidates.filter((n) => S.sel.has(n.id));
  const info = $('sel-info'), vel = $('sel-vel');
  const srcName = { model: 'モデルの出力', user: '人が追加', cand: '受け入れた候補' };
  if (ns.length === 1 && !cs.length) {
    const n = ns[0];
    info.innerHTML = '';
    const rows = [['音名', `${pitchName(n.pitch)}(${n.pitch})`], ['発音', `${n.on.toFixed(3)} 秒`],
                  ['離鍵', `${n.off.toFixed(3)} 秒`], ['長さ', `${((n.off - n.on) * 1000).toFixed(0)} ms`],
                  ['由来', srcName[n.src] + (n.src === 'model' && n.edited ? '(修正)' : '')],
                  ['再調整', n.refined ? (n.weak ? '手がかりなし' : `済み(手がかり ${n.evidence.toFixed(2)})`) : '未']];
    for (const [k, v] of rows) { info.insertAdjacentHTML('beforeend', `<dt>${k}</dt><dd></dd>`); info.lastElementChild.textContent = v; }
    vel.disabled = false; vel.value = n.vel;
  } else {
    info.innerHTML = `<dt>${ns.length + cs.length ? `${ns.length} 音${cs.length ? `、候補 ${cs.length}` : ''}` : 'なし'}</dt>`;
    vel.disabled = !ns.length; vel.value = ns.length ? Math.round(ns.reduce((a, n) => a + n.vel, 0) / ns.length) : '';
  }
  const c = {
    '音の数': S.notes.length,
    '人が追加': S.notes.filter((n) => n.src === 'user').length,
    '受け入れた候補': S.notes.filter((n) => n.src === 'cand').length,
    '修正したモデルの音': S.notes.filter((n) => n.src === 'model' && n.edited).length,
    '削除': S.deleted.length,
    '未確認の候補': S.candidates.length,
    '再調整が未': S.notes.filter((n) => n.edited && !n.refined).length,
    '手がかりなし': S.notes.filter((n) => n.weak).length,
  };
  $('counts').innerHTML = Object.entries(c).map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('');
  $('btn-refine-all').disabled = !c['再調整が未'];
  $('btn-refine-sel').disabled = !ns.length;
  $('btn-undo').disabled = !S.undo.length;
  $('btn-redo').disabled = !S.redo.length;
}

$('sel-vel').addEventListener('change', (e) => {
  const v = Math.min(127, Math.max(1, Math.round(+e.target.value)));
  const ns = S.notes.filter((n) => S.sel.has(n.id));
  if (!ns.length) return;
  pushUndo();
  for (const n of ns) { n.vel = v; n.fixed_vel = true; markEdited(n); }
  updatePanel(); draw();
});

// ---- プロジェクトの読み込み・保存
async function refreshProjects(select) {
  const list = await (await api('/api/projects')).json();
  const sel = $('projects');
  sel.innerHTML = list.length ? '' : '<option value="">(なし)</option>';
  for (const p of list) {
    const o = document.createElement('option');
    o.value = p.name; o.textContent = p.name + (p.saved ? '(保存あり)' : p.ready ? '' : '(採譜中)');
    sel.appendChild(o);
  }
  if (select) sel.value = select;
}

async function waitReady(name) {
  const ov = $('overlay');
  for (;;) {
    const st = await (await api(`/api/projects/${encodeURIComponent(name)}/status`)).json();
    if (st.state === 'ready') { ov.hidden = true; return; }
    if (st.state === 'error' || st.state === 'missing') { ov.hidden = true; throw new Error(st.message || 'プロジェクトがありません'); }
    ov.hidden = false;
    $('overlay-msg').textContent = `${name}: ${st.message}`;
    $('overlay-progress').value = st.total ? st.done / st.total : 0;
    const est = st.duration ? `録音 ${Math.round(st.duration)} 秒。` : '';
    $('overlay-detail').textContent = `${est}経過 ${Math.round(st.elapsed)} 秒` +
      (st.total ? `(${st.done}/${st.total} 区間)` : '') + '。初回はモデルの読み込みに1分ほどかかります。';
    await new Promise((r) => setTimeout(r, 1000));
  }
}

async function openProject(name) {
  if (S.dirty && !confirm('保存していない変更があります。破棄して開きますか?')) return;
  try {
    player.stop();
    await waitReady(name);
    status(`${name} を読み込み中…`);
    const d = await (await api(`/api/projects/${encodeURIComponent(name)}`)).json();
    S.name = d.name; S.duration = d.duration;
    const sv = d.saved;
    S.notes = sv ? sv.notes : d.model_notes;
    S.candidates = sv ? sv.candidates : d.candidates;
    S.deleted = sv ? sv.deleted : [];
    S.pedal = sv ? sv.pedal : d.model_pedal;
    S.nextId = 1 + Math.max(0, ...S.notes.filter((n) => n.id.startsWith('u')).map((n) => +n.id.slice(1)));
    S.sel.clear(); S.undo = []; S.redo = []; S.cursor = 0; S.view.t0 = 0;
    // 音のある範囲が見えるように縦位置を合わせる
    if (S.notes.length) {
      const mid = S.notes.map((n) => n.pitch).sort((a, b) => a - b)[Math.floor(S.notes.length / 2)];
      S.view.y0 = (HI - mid) * S.view.rowH - (rollBottom() - RULER) / 2;
    }
    clampView(); setDirty(false);
    history.replaceState(null, '', `?p=${encodeURIComponent(d.name)}`);
    $('proj-name').textContent = `${d.name}(${Math.round(d.duration)} 秒)` + (sv ? ` 保存 ${sv.saved_at}` : '');
    $('link-midi').classList.toggle('disabled', !sv);
    $('link-midi').href = `/api/projects/${encodeURIComponent(d.name)}/midi`;
    updatePanel(); draw();
    await refreshProjects(d.name);
    const [ab, rr] = await Promise.all([api(`/api/projects/${encodeURIComponent(name)}/audio`),
                                        api(`/api/projects/${encodeURIComponent(name)}/roll`)]);
    buildRoll(await rr.arrayBuffer(), +rr.headers.get('X-Frames'), +rr.headers.get('X-Keys'), +rr.headers.get('X-Fps'));
    await decodeAudio(await ab.arrayBuffer());
    draw();
    status(`${d.name}: ${S.notes.length} 音。スペースで再生、ダブルクリックで音を追加、Delete で削除。`);
  } catch (err) { status('開けませんでした: ' + err.message); }
}

async function save() {
  if (!S.name) return;
  const body = { notes: S.notes, candidates: S.candidates, deleted: S.deleted, pedal: S.pedal };
  try {
    const r = await (await api(`/api/projects/${encodeURIComponent(S.name)}/save`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })).json();
    setDirty(false);
    $('link-midi').classList.remove('disabled');
    status(`保存しました(${r.saved_at}): ${r.midi}`);
  } catch (err) { status('保存に失敗しました: ' + err.message); }
}

// ---- ボタン類
$('btn-play').onclick = () => { ensureAudio(); player.playing ? player.stop() : player.play(S.cursor); };
$('vol-audio').oninput = (e) => { if (audioGain) audioGain.gain.value = +e.target.value; };
$('vol-midi').oninput = (e) => { if (synthGain) synthGain.gain.value = +e.target.value; };
document.querySelectorAll('input[name=mode]').forEach((r) => { r.onchange = () => { if (player.playing) player.play(S.cursor); }; });
const zoom = (f) => { const tc = tOf((KEYW + W) / 2); S.view.pps = Math.min(4000, Math.max(5, S.view.pps * f)); S.view.t0 = tc - (W - KEYW) / 2 / S.view.pps; clampView(); draw(); };
$('btn-zoom-in').onclick = () => zoom(1.5);
$('btn-zoom-out').onclick = () => zoom(1 / 1.5);
$('show-roll').onchange = (e) => { S.showRoll = e.target.checked; draw(); };
$('show-cand').onchange = (e) => { S.showCand = e.target.checked; draw(); };
$('btn-refine-sel').onclick = refineSelected;
$('btn-refine-all').onclick = refineAll;
$('btn-undo').onclick = undo;
$('btn-redo').onclick = redoAct;
$('btn-save').onclick = save;
$('btn-open-project').onclick = () => { const n = $('projects').value; if (n) openProject(n); };
$('btn-open-path').onclick = async () => {
  const path = $('wav-path').value.trim();
  if (!path) return;
  try {
    const r = await (await api('/api/open', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                              body: JSON.stringify({ path }) })).json();
    await refreshProjects(r.name); openProject(r.name);
  } catch (err) { status('読み込めませんでした: ' + err.message); }
};
$('wav-file').onchange = async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  status(`${f.name} を送信中…`);
  try {
    const r = await (await api(`/api/upload?name=${encodeURIComponent(f.name)}`, { method: 'POST', body: f })).json();
    await refreshProjects(r.name); openProject(r.name);
  } catch (err) { status('読み込めませんでした: ' + err.message); }
  e.target.value = '';
};
window.addEventListener('beforeunload', (e) => { if (S.dirty) { e.preventDefault(); e.returnValue = ''; } });
window.addEventListener('resize', resize);
canvas.tabIndex = 0;
resize();
updatePanel();
refreshProjects().then(() => {
  const p = new URLSearchParams(location.search).get('p');   // ?p=名前 でそのプロジェクトを開く
  if (p) openProject(p);
}).catch((err) => status('サーバーに接続できません: ' + err.message));
