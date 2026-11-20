#!/usr/bin/env python3
"""人が追加する音のベロシティ推定の比較(PEDB)

対象は「採譜モデル(Kong)が実際に見落とした音」。人が採譜の GUI で追加する音に最も近い。
その音を、正しい位置 / 人が置く程度のずれ(±40ms)から詰め直した位置 に追加したとして、ベロシティを推定する。
  A  モデルのベロシティ出力をその位置で読む(refine.velocity_at)
  B  倍音エネルギーからの推定。対応は同じ録音のモデルの音(モデルのベロシティ)に合わせる(velocity.py、正解は使わない)
  基準1 近く(±0.5秒)のモデルの音のベロシティの中央値
  基準2 その演奏のモデルの音のベロシティの中央値
絶対値ではなく相対的な強弱を見るため、推定はモデルの音の平均・標準偏差で、正解は正解の全音の平均・標準偏差で
演奏ごとに標準化(z)して比べる。あわせて、近く(±1秒)のモデルが検出した音との強弱の向きが正解と合う割合を出す。
比較のため、モデルが検出した音でも同じ指標を出す(A = モデルのベロシティそのもの)。

前提: scripts/transcribe/save_kong_frames.py でフレームごとの出力を保存してあること
使い方: python3 scripts/transcribe/eval_added_velocity.py [--frames output/transcription/kong_frames] [演奏名 ...]
"""
import argparse
import csv
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
from music_io import audio, midi, pedb  # noqa: E402
from piano_transcribe import evaluate as ev, refine, velocity  # noqa: E402

METHODS = ['A', 'B', 'near', 'median']
NAMES = {'A': 'A モデルのベロシティ出力', 'B': 'B 倍音エネルギー(演奏内で較正)', 'near': '基準1 近くの音の中央値',
         'median': '基準2 演奏の中央値'}
EV_BINS = [(0, 0.02, '0.02 未満(手がかりなし)'), (0.02, 0.1, '0.02〜0.1'), (0.1, 0.3, '0.1〜0.3'), (0.3, 9, '0.3 以上')]

ap = argparse.ArgumentParser()
ap.add_argument('perfs', nargs='*')
ap.add_argument('--data')
ap.add_argument('--frames', default='output/transcription/kong_frames')
ap.add_argument('--out', default='output/transcription/added_velocity')
ap.add_argument('--jitter', type=float, default=0.04)
a = ap.parse_args()
root = pedb.data_dir(a.data)
os.makedirs(a.out, exist_ok=True)
perfs = [p for p in (a.perfs or pedb.list_performances(root)) if os.path.exists(os.path.join(a.frames, p + '.npz'))]
rng = np.random.default_rng(0)

rows = []          # 1行 = 対象の1音 × 位置の条件
for p in perfs:
    f = pedb.perf_files(root, p)
    z = np.load(os.path.join(a.frames, p + '.npz'))
    out = {k: z[k].astype(np.float32) for k in ('reg_onset_output', 'velocity_output', 'frame_output')}
    kn = z['notes']
    ref = midi.read_midi_notes(f['mid'])
    o = ev.constant_offset(kn, ref)
    pairs = ev.match_notes(kn - [o, o, 0, 0], ref)
    det = dict((r, k) for k, r in pairs)
    x, sr = audio.load_wav(f['wav'])
    av = velocity.AcousticVelocity(x, sr).fit(kn[:, 2], kn[:, 0], kn[:, 3])
    km, ks = kn[:, 3].mean(), kn[:, 3].std() or 1
    tm, ts = ref[:, 3].mean(), ref[:, 3].std() or 1
    kmed = float(np.median(kn[:, 3]))
    # 近くの検出された音(強弱の向きの比較用): 正解の時刻・正解のベロシティ・モデルのベロシティ
    d_on = np.array([ref[r, 0] for r in det])
    d_true = np.array([ref[r, 3] for r in det])
    d_model = np.array([kn[k, 3] for k in det.values()])

    def add(r, kind, cond, t, vals, evidence):
        near = (np.abs(d_on - ref[r, 0]) <= 1.0) & (np.abs(d_true - ref[r, 3]) >= 10) & (np.abs(d_on - ref[r, 0]) > 1e-6)
        signs = {}
        for m, v in vals.items():
            if np.isnan(v) or not near.any():
                signs[m] = (0, 0)
            else:
                agree = np.sign(v - d_model[near]) == np.sign(ref[r, 3] - d_true[near])
                signs[m] = (int(agree.sum()), int(near.sum()))
        rows.append(dict(perf=p, kind=kind, cond=cond, pitch=int(ref[r, 2]), true=ref[r, 3], true_z=(ref[r, 3] - tm) / ts,
                         evidence=evidence, **{m: v for m, v in vals.items()},
                         **{m + '_z': (v - km) / ks for m, v in vals.items()},
                         **{m + '_agree': s[0] for m, s in signs.items()}, **{m + '_pairs': s[1] for m, s in signs.items()}))

    def estimate(pch, t):
        win = np.abs(kn[:, 0] - t) <= 0.5
        return dict(A=refine.velocity_at(out, pch, t) or np.nan, B=float(av.predict([pch], [t])[0]),
                    near=float(np.median(kn[win, 3])) if win.any() else kmed, median=kmed)

    for r in range(len(ref)):
        pch, t_true = ref[r, 2], ref[r, 0] + o
        evidence = refine.find_onset(out, pch, t_true, 0.03)[1]
        if r in det:   # 比較用: モデルが検出した音(A はモデルのベロシティそのもの)
            k = det[r]
            v = estimate(pch, kn[k, 0])
            v['A'] = kn[k, 3]
            add(r, 'detected', 'model', kn[k, 0], v, evidence)
            continue
        add(r, 'missed', 'exact', t_true, estimate(pch, t_true), evidence)
        t0 = t_true + rng.uniform(-a.jitter, a.jitter)
        t1, e1 = refine.find_onset(out, pch, t0, 0.08)
        t = t1 if e1 >= 0.02 else t0
        add(r, 'missed', 'placed', t, estimate(pch, t), evidence)
    print(p, f'見落とし {len(ref) - len(det)} / {len(ref)}', flush=True)

with open(os.path.join(a.out, 'notes.csv'), 'w', newline='') as fo:
    w = csv.DictWriter(fo, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)


def summarize(sel, label):
    if not sel:
        return
    tz = np.array([r['true_z'] for r in sel])
    print(f'\n-- {label}: {len(sel):,} 音')
    print('  方法                              相関(z)  平均絶対誤差(z)  偏り(z)  近くの音との強弱の向きの一致')
    for m in METHODS:
        ez = np.array([r[m + '_z'] for r in sel])
        ok = ~np.isnan(ez)
        ag = sum(r[m + '_agree'] for r in sel)
        pr = sum(r[m + '_pairs'] for r in sel)
        r_ = np.corrcoef(ez[ok], tz[ok])[0, 1] if ok.sum() > 2 and np.std(ez[ok]) > 0 else float('nan')
        print(f'  {NAMES[m]:30s}  {r_:6.3f}   {np.mean(np.abs(ez[ok] - tz[ok])):8.3f}      {np.mean(ez[ok] - tz[ok]):+6.3f}'
              f'    {ag / pr if pr else float("nan"):.1%}({pr:,}組)')


print(f'\n== {len(perfs)} 演奏 ==')
summarize([r for r in rows if r['kind'] == 'detected'], 'モデルが検出した音(比較用)')
for cond, name in [('exact', '見落とした音を正しい位置に追加'), ('placed', f'見落とした音を ±{a.jitter * 1000:.0f}ms ずれた位置に置いて詰め直し')]:
    sel = [r for r in rows if r['kind'] == 'missed' and r['cond'] == cond]
    summarize(sel, name)
    if cond == 'exact':
        for lo, hi, lab in EV_BINS:
            summarize([r for r in sel if lo <= r['evidence'] < hi], f'  {name} / 発音の手がかり {lab}')
print('\n正解の標準偏差(ベロシティ)の中央値:', round(float(np.median([np.std([r['true'] for r in rows if r['perf'] == p]) for p in perfs])), 1),
      '(z の 1 はおよそこの大きさ)')
