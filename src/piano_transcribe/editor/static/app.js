// 採譜の確認・修正: ピアノロールと録音波形の表示・編集、録音と MIDI の再生、再調整、
// 楽譜との照合(違いの一覧・補正の提案・判定、元の楽譜と書き起こした楽譜の表示)、保存
'use strict';

const $ = (id) => document.getElementById(id);
const LO = 21, HI = 108;            // ピアノの音域
const KEYW = 46, RULER = 22, WAVE = 56, TOP = RULER + WAVE, PEDAL = 26;   // TOP より上が目盛りと波形の帯
const COLOR = { model: '#2a78d6', user: '#eb6834', cand: '#1baf7a', score: '#6250d6', muted: '#8a8983', weak: '#e34948',
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
  // 楽譜との照合
  score: false, diffs: [], judg: {}, measures: [], active: null, ghost: null, peaks: null,
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
const yOf = (p) => TOP + (HI - p) * S.view.rowH - S.view.y0;
const pOf = (y) => Math.round(HI - (y + S.view.y0 - TOP) / S.view.rowH + 0.5);
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
  const maxY = Math.max(0, (HI - LO + 1) * v.rowH - (rollBottom() - TOP));
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
  ctx2.beginPath(); ctx2.rect(KEYW, TOP, W - KEYW, rollBottom() - TOP); ctx2.clip();
  // 黒鍵の行と C の線
  for (let p = LO; p <= HI; p++) {
    const y = yOf(p);
    if (y > rollBottom() || y + v.rowH < TOP) continue;
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
    const c = n.src === 'cand' ? COLOR.cand : n.src === 'score' ? COLOR.score : (n.src === 'user' || n.edited) ? COLOR.user : COLOR.model;
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
  // 選んだ違いの提案(まだ足していない音)
  if (S.ghost) {
    const n = S.ghost, x = xOf(n.on), w = Math.max(3, (n.off - n.on) * v.pps), y = yOf(n.pitch);
    ctx2.setLineDash([4, 3]); ctx2.strokeStyle = COLOR.score; ctx2.lineWidth = 2;
    ctx2.strokeRect(x + 0.5, y + 1.5, w - 1, v.rowH - 3); ctx2.setLineDash([]);
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
  drawWave();
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
  for (let t = Math.ceil(S.view.t0 / st) * st; t < tOf(W); t += st) ctx2.fillRect(Math.round(xOf(t)), TOP, 1, rollBottom() - TOP);
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
  ctx2.fillStyle = '#fff'; ctx2.fillRect(0, TOP, KEYW, rollBottom() - TOP);
  ctx2.save();
  ctx2.beginPath(); ctx2.rect(0, TOP, KEYW, rollBottom() - TOP); ctx2.clip();
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
  ctx2.fillStyle = COLOR.grid; ctx2.fillRect(KEYW - 1, TOP, 1, rollBottom() - TOP);
  ctx2.fillStyle = '#fff'; ctx2.fillRect(0, 0, KEYW, TOP);
  ctx2.fillStyle = COLOR.text2; ctx2.font = '10px "Hiragino Sans", sans-serif'; ctx2.textBaseline = 'middle';
  ctx2.fillText('波形', 8, RULER + WAVE / 2);
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
const snapshot = () => JSON.stringify({ notes: S.notes, candidates: S.candidates, deleted: S.deleted, judg: S.judg });
// recompare: 音を変える操作なら楽譜との照合を予約する(判定だけの操作では不要)
function pushUndo(recompare = true) {
  S.undo.push(snapshot()); if (S.undo.length > 200) S.undo.shift(); S.redo = []; setDirty(true);
  if (recompare) scheduleCompare();
}
function restore(js) {
  const o = JSON.parse(js);
  S.notes = o.notes; S.candidates = o.candidates; S.deleted = o.deleted; S.judg = o.judg || {};
  S.sel = new Set([...S.sel].filter((id) => S.notes.some((n) => n.id === id) || S.candidates.some((n) => n.id === id)));
  setDirty(true); updatePanel(); renderDiffs(); scheduleCompare(); draw();
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
  if (y < TOP && x > KEYW) {             // 目盛りと波形: 再生位置
    S.cursor = Math.max(0, tOf(x));
    if (player.playing) player.play(S.cursor); else draw();
    drag = { mode: 'cursor' };
    return;
  }
  if (x < KEYW && y > TOP && y < rollBottom()) { player.preview(pOf(y), 70); return; }
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
    const h = x > KEYW && y > TOP && y < rollBottom() ? hit(x, y) : null;
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
  if (x < KEYW || y < TOP || y >= rollBottom()) return;
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
  if (e.key.toLowerCase() === 'p') { S.active && !S.sel.size ? playAroundDiff() : playAroundSelection(); return; }
  if (e.key.toLowerCase() === 'j') { stepDiff(1); return; }
  if (e.key.toLowerCase() === 'k') { stepDiff(-1); return; }
  if (e.key.toLowerCase() === 'a' && S.active) { applySuggestion(activeDiff()); return; }
  if (e.key.toLowerCase() === 'o' && S.active) { judge(activeDiff(), 'performance'); return; }
  if (e.key.toLowerCase() === 'u' && S.active) { judge(activeDiff(), 'unknown'); return; }
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
}

// 復号は音を出さない OfflineAudioContext で行う(再生用の AudioContext はユーザーの操作まで作れないため、
// 開いた直後に波形を出せない)。AudioBuffer は再生用のコンテキストでもそのまま使える
async function decodeAudio(buf) {
  S.audioBuf = await new OfflineAudioContext(1, 1, 44100).decodeAudioData(buf);
  buildPeaks();
  draw();
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
    followScore();
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
  const srcName = { model: 'モデルの出力', user: '人が追加', cand: '受け入れた候補', score: '楽譜の提案で追加' };
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
    '楽譜の提案で追加': S.notes.filter((n) => n.src === 'score').length,
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
      S.view.y0 = (HI - mid) * S.view.rowH - (rollBottom() - TOP) / 2;
    }
    clampView(); setDirty(false);
    history.replaceState(null, '', `?p=${encodeURIComponent(d.name)}`);
    $('proj-name').textContent = `${d.name}(${Math.round(d.duration)} 秒)` + (sv ? ` 保存 ${sv.saved_at}` : '');
    $('link-midi').classList.toggle('disabled', !sv);
    $('link-midi').href = `/api/projects/${encodeURIComponent(d.name)}/midi`;
    S.score = !!d.score; S.judg = (sv && sv.judgments) || {}; S.diffs = []; S.measures = []; S.active = null; S.ghost = null;
    $('sheet-orig').innerHTML = ''; $('sheet-perf').innerHTML = '';
    showScorePanel(); renderDiffs();
    updatePanel(); draw();
    await refreshProjects(d.name);
    const [ab, rr] = await Promise.all([api(`/api/projects/${encodeURIComponent(name)}/audio`),
                                        api(`/api/projects/${encodeURIComponent(name)}/roll`)]);
    buildRoll(await rr.arrayBuffer(), +rr.headers.get('X-Frames'), +rr.headers.get('X-Keys'), +rr.headers.get('X-Fps'));
    await decodeAudio(await ab.arrayBuffer());
    draw();
    status(`${d.name}: ${S.notes.length} 音。スペースで再生、ダブルクリックで音を追加、Delete で削除。` +
           (S.score ? ' 楽譜と照合しています…' : ' 楽譜を指定すると、楽譜との違いを確認できます。'));
    if (S.score) await compareScore();
  } catch (err) { status('開けませんでした: ' + err.message); }
}

async function save() {
  if (!S.name) return;
  // 違いごとの判定と、そのときの違いの一覧も残す(採譜の誤りか演奏の違いかの記録)
  const body = { notes: S.notes, candidates: S.candidates, deleted: S.deleted, pedal: S.pedal, judgments: S.judg,
                 diffs: S.diffs.map((d) => ({ key: d.key, kind: d.kind, measure: d.measure, beat: d.beat, desc: d.desc,
                                              reason: d.reason, judgment: (S.judg[d.key] || {}).status || null })) };
  try {
    const r = await (await api(`/api/projects/${encodeURIComponent(S.name)}/save`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })).json();
    setDirty(false);
    $('link-midi').classList.remove('disabled');
    status(`保存しました(${r.saved_at}): ${r.midi}`);
  } catch (err) { status('保存に失敗しました: ' + err.message); }
}


// ---- 録音の波形(目盛りの下の帯)
// 256 標本ごとの最小・最大をあらかじめ求めておき、縮小表示ではそれを、拡大表示では標本そのものを使う
function buildPeaks() {
  const x = S.audioBuf.getChannelData(0), B = 256, n = Math.ceil(x.length / B);
  const mn = new Float32Array(n), mx = new Float32Array(n);
  let peak = 1e-6;
  for (let i = 0; i < n; i++) {
    let a = 1, b = -1;
    for (let j = i * B, e = Math.min(x.length, j + B); j < e; j++) { const v = x[j]; if (v < a) a = v; if (v > b) b = v; }
    mn[i] = a; mx[i] = b; peak = Math.max(peak, -a, b);
  }
  S.peaks = { B, mn, mx, peak, sr: S.audioBuf.sampleRate, x };
}

function drawWave() {
  const y0 = RULER, h = WAVE, mid = y0 + h / 2;
  ctx2.fillStyle = '#fff'; ctx2.fillRect(KEYW, y0, W - KEYW, h);
  ctx2.fillStyle = COLOR.grid; ctx2.fillRect(KEYW, y0 + h - 1, W - KEYW, 1);
  // 選んだ音・違いの時間範囲を薄く示す
  const spans = S.notes.filter((n) => S.sel.has(n.id)).map((n) => [n.on, n.off]);
  if (S.ghost) spans.push([S.ghost.on, S.ghost.off]);
  ctx2.fillStyle = 'rgba(237, 161, 0, .22)';
  for (const [a, b] of spans) ctx2.fillRect(xOf(a), y0, Math.max(2, (b - a) * S.view.pps), h - 1);
  if (!S.peaks) return;
  const P = S.peaks, gain = +$('wave-gain').value, amp = (h / 2 - 3) * gain / P.peak;
  const spp = P.sr / S.view.pps;   // 1 画素あたりの標本数
  ctx2.fillStyle = COLOR.text2;
  for (let px = KEYW; px < W; px++) {
    const s0 = Math.floor(tOf(px) * P.sr), s1 = Math.floor(tOf(px + 1) * P.sr);
    if (s1 <= 0 || s0 >= P.x.length) continue;
    let a = 1, b = -1;
    if (spp >= P.B) {
      for (let i = Math.max(0, Math.floor(s0 / P.B)), e = Math.min(P.mn.length, Math.ceil(s1 / P.B)); i < e; i++) {
        if (P.mn[i] < a) a = P.mn[i]; if (P.mx[i] > b) b = P.mx[i];
      }
    } else {
      for (let j = Math.max(0, s0), e = Math.min(P.x.length, Math.max(s1, s0 + 1)); j < e; j++) { const v = P.x[j]; if (v < a) a = v; if (v > b) b = v; }
    }
    if (b < a) continue;
    const yt = Math.max(y0 + 1, mid - b * amp), yb = Math.min(y0 + h - 2, mid - a * amp);
    ctx2.fillRect(px, yt, 1, Math.max(1, yb - yt));
  }
}

// ---- 楽譜との照合
const KIND_LABEL = { missing: '抜け', extra: '余分', pitch: '弾き間違い', rhythm: 'リズム' };
const KIND_COLOR = { missing: '#E34948', extra: '#E34948', pitch: '#EB6834', rhythm: '#6250D6' };
const JUDG_LABEL = { fixed: '採譜を修正', performance: '演奏どおり', unknown: '不明' };
const kindsShown = new Set(['missing', 'extra', 'pitch', 'rhythm']);
let compareTimer = null, comparing = false, compareAgain = false;

function scheduleCompare() {
  if (!S.score || !$('auto-compare').checked) return;
  clearTimeout(compareTimer);
  compareTimer = setTimeout(compareScore, 1500);
}

async function compareScore() {
  if (!S.name || !S.score) return;
  if (comparing) { compareAgain = true; return; }
  comparing = true;
  status('楽譜と照合中…');
  try {
    const notes = S.notes.map((n) => ({ id: n.id, on: n.on, off: n.off, pitch: n.pitch, vel: n.vel }));
    const r = await (await api(`/api/projects/${encodeURIComponent(S.name)}/compare`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ notes }) })).json();
    S.diffs = r.diffs; S.measures = r.measures; lastFollow = null;
    for (const [id, svg] of [['sheet-orig', r.original_svg], ['sheet-perf', r.performance_svg]]) {
      const el = $(id), top = el.scrollTop;
      el.innerHTML = svg; el.scrollTop = top;
    }
    if (S.active && !S.diffs.some((d) => d.key === S.active)) { S.active = null; S.ghost = null; }
    renderDiffs(); draw();
    const todo = S.diffs.filter((d) => !S.judg[d.key]).length;
    status(`楽譜と照合しました(${r.seconds} 秒): 違い ${S.diffs.length} 件、うち未判定 ${todo} 件。J / K で順に見られます。`);
  } catch (err) { status('楽譜と照合できませんでした: ' + err.message); }
  comparing = false;
  if (compareAgain) { compareAgain = false; scheduleCompare(); }
}

function showScorePanel() {
  const hpct = +$('score-height').value;
  $('score-panel').hidden = !S.score || !hpct;
  $('score-panel').style.height = hpct + '%';
  $('diff-empty').hidden = S.score;
  $('diff-tools').hidden = !S.score;
  resize();
}

const activeDiff = () => S.diffs.find((d) => d.key === S.active);

function shownDiffs() {
  const st = $('diff-status').value;
  let ds = S.diffs.filter((d) => kindsShown.has(d.kind));
  if (st === 'todo') ds = ds.filter((d) => !S.judg[d.key]);
  if (st === 'done') ds = ds.filter((d) => S.judg[d.key]);
  if ($('diff-sort').value === 'doubt') {
    // 確認が必要な順: 提案の確からしさが 0.5 に近いもの(どちらとも言えないもの)から
    const doubt = (d) => d.confidence === null || d.confidence === undefined ? 1 : Math.abs(d.confidence - 0.5);
    ds = ds.slice().sort((a, b) => doubt(a) - doubt(b));
  }
  return ds;
}

function renderDiffs() {
  if (!S.score) { $('diff-list').innerHTML = ''; $('diff-detail').hidden = true; return; }
  const cnt = (k) => S.diffs.filter((d) => d.kind === k).length;
  const done = S.diffs.filter((d) => S.judg[d.key]).length;
  $('diff-summary').textContent = `違い ${S.diffs.length} 件(判定済み ${done})`;
  $('diff-kinds').innerHTML = Object.keys(KIND_LABEL).map((k) =>
    `<label><input type="checkbox" data-k="${k}" ${kindsShown.has(k) ? 'checked' : ''}><span style="color:${KIND_COLOR[k]}">●</span>${KIND_LABEL[k]} ${cnt(k)}</label>`).join('');
  $('diff-kinds').querySelectorAll('input').forEach((el) => {
    el.onchange = () => { el.checked ? kindsShown.add(el.dataset.k) : kindsShown.delete(el.dataset.k); renderDiffs(); };
  });
  const ol = $('diff-list');
  ol.innerHTML = '';
  for (const d of shownDiffs().slice(0, 500)) {
    const li = document.createElement('li');
    const j = S.judg[d.key];
    li.className = d.key === S.active ? 'active' : '';
    li.innerHTML = `<div class="head"><span class="kind" style="color:${KIND_COLOR[d.kind]}">● ${KIND_LABEL[d.kind]}</span>` +
      `<span class="m">${d.measure}小節 ${(d.beat + 1).toFixed(2).replace(/\.?0+$/, '')}拍</span>` +
      `<span class="badge ${j ? j.status : ''}">${j ? JUDG_LABEL[j.status] : '未判定'}</span></div>` +
      `<div class="desc"></div><div class="why"></div>`;
    li.querySelector('.desc').textContent = d.desc;
    li.querySelector('.why').textContent = d.reason;
    li.onclick = () => selectDiff(d);
    ol.appendChild(li);
  }
  renderDetail();
}

function renderDetail() {
  const d = activeDiff(), box = $('diff-detail');
  if (!d) { box.hidden = true; return; }
  box.hidden = false;
  const j = S.judg[d.key];
  box.innerHTML = `<div class="title"></div><div class="why"></div><div class="acts"></div>`;
  box.querySelector('.title').textContent = `${KIND_LABEL[d.kind]} ${d.measure}小節: ${d.desc}` + (j ? `(${JUDG_LABEL[j.status]})` : '');
  box.querySelector('.why').textContent = d.reason;
  const acts = box.querySelector('.acts');
  const btn = (label, title, fn) => { const b = document.createElement('button'); b.textContent = label; b.title = title; b.onclick = fn; acts.appendChild(b); };
  if (d.suggestion) btn(d.suggestion.label + '(A)', '採譜の誤りとして、提案どおりに MIDI を直す', () => applySuggestion(d));
  btn('演奏どおり(O)', '奏者がそのように弾いた。MIDI は直さない', () => judge(d, 'performance'));
  btn('不明(U)', '録音を聴いても判断できない', () => judge(d, 'unknown'));
  btn('前後を再生(P)', '録音と MIDI をこの付近で再生する', () => playAroundDiff());
  if (j) btn('判定を取り消す', '', () => judge(d, null));
}

function diffTime(d) {
  if (d.time !== undefined && d.time !== null) return d.time;
  const m = S.measures.find((x) => String(x.n) === String(d.measure));
  return m ? m.t0 : S.cursor;
}

function selectDiff(d) {
  S.active = d.key;
  S.ghost = d.suggestion && d.suggestion.action === 'add' ? d.suggestion.note : null;
  const t = diffTime(d);
  const span = (W - KEYW) / S.view.pps;
  S.view.t0 = t - span * 0.35; S.cursor = Math.max(0, t - 0.5);
  const pitch = d.suggestion && d.suggestion.note ? d.suggestion.note.pitch
    : (S.notes.find((n) => n.id === d.note_id) || {}).pitch;
  if (pitch) S.view.y0 = (HI - pitch) * S.view.rowH - (rollBottom() - TOP) / 2;
  S.sel = new Set(d.note_id && S.notes.some((n) => n.id === d.note_id) ? [d.note_id] : []);
  clampView();
  revealMeasure($('sheet-orig'), d.measure, true);
  revealMeasure($('sheet-perf'), d.measure, true);
  updatePanel(); renderDiffs(); draw();
  const li = $('diff-list').querySelector('li.active');
  if (li) li.scrollIntoView({ block: 'nearest' });
}

function stepDiff(k) {
  const ds = shownDiffs();
  if (!ds.length) return;
  const i = ds.findIndex((d) => d.key === S.active);
  selectDiff(ds[Math.min(ds.length - 1, Math.max(0, i < 0 ? 0 : i + k))]);
}

function judge(d, status) {
  if (!d) return;
  pushUndo(false);
  if (status) S.judg[d.key] = { status, kind: d.kind, measure: d.measure, desc: d.desc, at: new Date().toISOString() };
  else delete S.judg[d.key];
  renderDiffs();
  if (status && $('diff-status').value === 'todo') stepDiff(0);
}

// 提案を当てはめる。足す音は録音から求めた発音時刻・ベロシティ(方法 A)・離鍵で置く
async function applySuggestion(d, quiet = false) {
  if (!d || !d.suggestion) return;
  const sg = d.suggestion;
  if (!quiet) pushUndo();
  if (sg.action === 'add') {
    const n = { id: 's' + S.nextId++, on: sg.note.on, off: sg.note.off, pitch: sg.note.pitch, vel: sg.note.vel,
                src: 'score', edited: true, refined: !sg.note.weak, weak: !!sg.note.weak, from_diff: d.key };
    S.notes.push(n);
  } else if (sg.action === 'delete') {
    const n = S.notes.find((x) => x.id === d.note_id);
    if (n && n.src === 'model') S.deleted.push({ id: n.id, on: n.on, off: n.off, pitch: n.pitch, vel: n.vel, from_diff: d.key });
    S.notes = S.notes.filter((x) => x.id !== d.note_id);
  } else if (sg.action === 'set_pitch') {
    const n = S.notes.find((x) => x.id === d.note_id);
    if (n) { n.pitch = sg.pitch; markEdited(n); n.from_diff = d.key; }
  }
  S.judg[d.key] = { status: 'fixed', kind: d.kind, measure: d.measure, desc: d.desc, action: sg.action, at: new Date().toISOString() };
  if (quiet) return;
  S.ghost = null;
  if (sg.action === 'set_pitch') await refine([d.note_id]);   // 新しい音高で録音に合わせ直す
  updatePanel(); renderDiffs(); draw();
  if ($('diff-status').value === 'todo') stepDiff(0);
}

async function applyShown() {
  const ds = shownDiffs().filter((d) => d.suggestion && !S.judg[d.key]);
  if (!ds.length) { status('当てはめられる未判定の提案がありません。'); return; }
  const n = { add: 0, delete: 0, set_pitch: 0 };
  ds.forEach((d) => n[d.suggestion.action]++);
  if (!confirm(`一覧に出ている未判定の提案 ${ds.length} 件を当てはめます(足す ${n.add}、消す ${n.delete}、音高を直す ${n.set_pitch})。\n` +
               '提案は検証で 7〜9 割程度しか正しくありません。よろしいですか?')) return;
  pushUndo();
  for (const d of ds) await applySuggestion(d, true);
  const ids = ds.filter((d) => d.suggestion.action === 'set_pitch').map((d) => d.note_id);
  if (ids.length) await refine(ids);
  S.ghost = null;
  updatePanel(); renderDiffs(); draw();
  status(`${ds.length} 件の提案を当てはめました。元に戻す(⌘/Ctrl+Z)で取り消せます。`);
}

function playAroundDiff() {
  const d = activeDiff();
  if (!d) return;
  S.cursor = Math.max(0, diffTime(d) - 1.0);
  player.play(S.cursor, 2.5);
}

// ---- 楽譜の表示: 小節の位置、強調、クリックでその時刻へ、再生に追従
function measureEl(sheet, n) { return sheet.querySelector(`g.measure[data-n="${CSS.escape(String(n))}"]`); }
function measureRect(m) {
  const lines = m.querySelectorAll(':scope > g.staff > path');
  if (!lines.length) return m.getBoundingClientRect();
  let l = Infinity, t = Infinity, r = -Infinity, b = -Infinity;
  for (const x of lines) { const q = x.getBoundingClientRect(); l = Math.min(l, q.left); t = Math.min(t, q.top); r = Math.max(r, q.right); b = Math.max(b, q.bottom); }
  return { left: l, top: t - 12, width: r - l, height: b - t + 24, bottom: b + 12 };
}
function placeBox(sheet, m, cls) {
  const sr = sheet.getBoundingClientRect(), r = measureRect(m);
  let el = cls === 'cur' ? sheet.querySelector('.hl.cur') : null;
  if (!el) { el = document.createElement('div'); el.className = 'hl' + (cls ? ' ' + cls : ''); sheet.appendChild(el); }
  el.style.left = (r.left - sr.left - 4) + 'px';
  el.style.top = (r.top - sr.top + sheet.scrollTop - 4) + 'px';
  el.style.width = (r.width + 8) + 'px';
  el.style.height = (r.height + 8) + 'px';
  return el;
}
function revealMeasure(sheet, n, flash) {
  const m = measureEl(sheet, n);
  if (!m || $('score-panel').hidden) return;
  const sr = sheet.getBoundingClientRect(), mr = measureRect(m);
  if (mr.top < sr.top + 10 || mr.bottom > sr.bottom - 10) sheet.scrollTop += mr.top - sr.top - 20;
  if (flash) {
    const el = placeBox(sheet, m, '');
    setTimeout(() => { el.style.opacity = 0; }, 1200);
    setTimeout(() => el.remove(), 2600);
  }
}
let lastFollow = null;
function followScore() {
  if (!S.score || !S.measures.length || $('score-panel').hidden) return;
  const m = S.measures.find((x) => S.cursor >= x.t0 && S.cursor < x.t1);
  if (!m || m.n === lastFollow) return;
  lastFollow = m.n;
  for (const sheet of [$('sheet-orig'), $('sheet-perf')]) {
    revealMeasure(sheet, m.n, false);
    const el = measureEl(sheet, m.n);
    if (el) placeBox(sheet, el, 'cur');
  }
}
for (const id of ['sheet-orig', 'sheet-perf']) {
  $(id).addEventListener('click', (e) => {
    const g = e.target.closest('g.measure');
    if (!g) return;
    const m = S.measures.find((x) => String(x.n) === g.dataset.n);
    if (!m) return;
    const span = (W - KEYW) / S.view.pps;
    S.cursor = m.t0; S.view.t0 = m.t0 - span * 0.1; clampView();
    lastFollow = null; followScore(); draw();
    if (player.playing) player.play(S.cursor);
  });
}

async function setScore(body, label) {
  if (!S.name) { status('先にプロジェクトを開いてください。'); return; }
  try {
    const url = `/api/projects/${encodeURIComponent(S.name)}/score` + (label ? `?name=${encodeURIComponent(label)}` : '');
    const r = await (await api(url, { method: 'POST', body })).json();
    S.score = true; showScorePanel();
    status(`楽譜を指定しました(${r.measures} 小節、${r.notes} 音)。照合します…`);
    await compareScore();
  } catch (err) { status('楽譜を指定できませんでした: ' + err.message); }
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
$('btn-compare').onclick = compareScore;
$('auto-compare').onchange = (e) => { if (e.target.checked) scheduleCompare(); };
$('score-height').onchange = showScorePanel;
$('wave-gain').onchange = draw;
$('diff-status').onchange = renderDiffs;
$('diff-sort').onchange = renderDiffs;
$('btn-apply-shown').onclick = applyShown;
$('btn-score-path').onclick = () => { const p = $('score-path').value.trim(); if (p) setScore(JSON.stringify({ path: p })); };
$('score-file').onchange = (e) => { const f = e.target.files[0]; if (f) setScore(f, f.name); e.target.value = ''; };
$('btn-open-project').onclick = () => { const n = $('projects').value; if (n) openProject(n); };
$('btn-open-path').onclick = async () => {
  const path = $('wav-path').value.trim();
  if (!path) return;
  try {
    const score = $('score-path-new').value.trim() || undefined;
    const r = await (await api('/api/open', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                              body: JSON.stringify({ path, score }) })).json();
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
