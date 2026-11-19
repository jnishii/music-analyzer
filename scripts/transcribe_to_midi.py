#!/usr/bin/env python3
"""録音(WAV)と楽譜(fmt3x)から、発音時刻とベロシティを推定してMIDIファイルにする

使い方:
  python3 scripts/transcribe_to_midi.py --wav 録音.wav --fmt3x 楽譜_fmt3x.txt --out 推定.mid \
      [--model output/eval/velocity_model.json]
  PEDB の演奏名で指定する場合: python3 scripts/transcribe_to_midi.py --perf bac-inv001-o-p1 --out 推定.mid

注意: 楽譜にある音はすべて弾かれたものとして書き出す(抜けた音・余分な音は判定しない)。
      離鍵時刻は楽譜の音価から決めた目安で、実際の離鍵やペダルは推定していない。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from music_analysis import pedb, transcribe as tr  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('--perf')
ap.add_argument('--data')
ap.add_argument('--wav')
ap.add_argument('--fmt3x')
ap.add_argument('--model', default='output/eval/velocity_model.json')
ap.add_argument('--out', required=True)
a = ap.parse_args()
if a.perf:
    f = pedb.perf_files(pedb.data_dir(a.data), a.perf)
    a.wav, a.fmt3x = a.wav or f['wav'], a.fmt3x or f['fmt3x']

est = tr.estimate(a.wav, a.fmt3x)
rows = list(est.values())
if os.path.exists(a.model):
    vel = tr.predict_velocity(rows, tr.load_model(a.model))
else:
    print('ベロシティの回帰モデルがないため、強さの特徴量を 30〜100 に線形に割り当てます', file=sys.stderr)
    pk = [r['f_peak'] for r in rows]
    lo, hi = min(pk), max(pk)
    vel = [30 + 70 * (x - lo) / (hi - lo + 1e-9) for x in pk]
notes = [(r['t_est'], r['t_est'] + max(0.05, r['dur_sec'] * 0.9), r['pitch'], v) for r, v in zip(rows, vel)]
tr.write_midi(a.out, notes)
print(len(notes), 'notes ->', a.out)
