#!/usr/bin/env python3
"""人が指定した音の発音時刻の再推定(refine.py)の評価(PEDB)

対象は採譜モデル(Kong)が見落とした音。正しい発音時刻から ±jitter の一様乱数でずらした位置に人が置いたとして、
refine.find_onset で詰め直し、手がかり(発音の出力の山)が境(0.02)以上なら詰め直した時刻、未満なら置いた時刻を使う。
比較のため、モデルが検出した音でも同じことをする(正解はモデル自身の発音時刻ではなく同時記録MIDI)。
時刻は、演奏ごとの一定のずれ(録音と MIDI)を差し引いて比べる。

前提: scripts/transcribe/save_kong_frames.py でフレームごとの出力を保存してあること
使い方: python3 scripts/transcribe/eval_refine_onset.py [--frames ...] [--jitter 0.04] [演奏名 ...]
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
from music_io import midi, pedb  # noqa: E402
from piano_transcribe import evaluate as ev, refine  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('perfs', nargs='*')
ap.add_argument('--data')
ap.add_argument('--frames', default='output/transcription/kong_frames')
ap.add_argument('--jitter', type=float, default=0.04)
ap.add_argument('--weak', type=float, default=0.02)
a = ap.parse_args()
root = pedb.data_dir(a.data)
perfs = [p for p in (a.perfs or pedb.list_performances(root)) if os.path.exists(os.path.join(a.frames, p + '.npz'))]
rng = np.random.default_rng(0)
res = {'missed': [], 'detected': []}   # (置いた位置の誤差, 詰め直した後の誤差, 手がかり) ms
for p in perfs:
    z = np.load(os.path.join(a.frames, p + '.npz'))
    out = {k: z[k].astype(np.float32) for k in ('reg_onset_output', 'velocity_output', 'frame_output')}
    kn = z['notes']
    ref = midi.read_midi_notes(pedb.perf_files(root, p)['mid'])
    o = ev.constant_offset(kn, ref)
    det = {r for _, r in ev.match_notes(kn - [o, o, 0, 0], ref)}
    for r in range(len(ref)):
        t_true = ref[r, 0] + o
        t0 = t_true + rng.uniform(-a.jitter, a.jitter)
        t1, e = refine.find_onset(out, ref[r, 2], t0, 0.08)
        t = t1 if e >= a.weak else t0
        res['detected' if r in det else 'missed'].append(((t0 - t_true) * 1000, (t - t_true) * 1000, e))

for kind, name in [('missed', 'モデルが見落とした音'), ('detected', 'モデルが検出した音(比較用)')]:
    x = np.array(res[kind])
    before, after, e = np.abs(x[:, 0]), np.abs(x[:, 1]), x[:, 2]
    strong = e >= a.weak
    print(f'\n== {name}: {len(x):,} 音(±{a.jitter * 1000:.0f}ms に置いて詰め直し、手がかりの境 {a.weak}) ==')
    print(f'  手がかりあり {strong.mean():.1%}')
    print(f'  全体:         置いた位置の誤差 中央値 {np.median(before):.1f}ms → 詰め直し後 {np.median(after):.1f}ms'
          f'(10ms 以内 {(before <= 10).mean():.0%} → {(after <= 10).mean():.0%})')
    if strong.any():
        print(f'  手がかりあり: 置いた位置の誤差 中央値 {np.median(before[strong]):.1f}ms → 詰め直し後 {np.median(after[strong]):.1f}ms'
              f'(10ms 以内 {(before[strong] <= 10).mean():.0%} → {(after[strong] <= 10).mean():.0%}、'
              f'50ms 超 {(after[strong] > 50).mean():.1%})')
