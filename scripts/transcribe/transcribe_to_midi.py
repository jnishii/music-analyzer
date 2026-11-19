#!/usr/bin/env python3
"""録音(WAV)からMIDIファイルを作る

方式(--method)
  kong    採譜モデルだけで推定する(楽譜は不要)。抜けた音・余分な音・離鍵・ペダルも推定される
  hybrid  kong の下書きの音を楽譜の音符に割り当て、割り当てられなかった楽譜の音は guided で補う
  guided  楽譜情報つきの推定(学習済みモデルなし)。楽譜の音はすべて弾かれたものとして書き出し、
          離鍵は楽譜の音価から決めた目安、ペダルは推定しない

使い方:
  python3 scripts/transcribe/transcribe_to_midi.py --method kong --wav 録音.wav --out 推定.mid
  python3 scripts/transcribe/transcribe_to_midi.py --method guided --wav 録音.wav --fmt3x 楽譜_fmt3x.txt --out 推定.mid
  PEDB の演奏名で指定する場合: --perf bac-inv001-o-p1
ベロシティ: kong/hybrid はモデルの出力そのまま(録音環境が違うと絶対値はずれる)。
  guided は --model の回帰係数(scripts/transcribe/eval_transcription.py が作る)を使う。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
from music_io import audio, midi, pedb  # noqa: E402
from piano_transcribe import guided  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('--method', choices=['kong', 'hybrid', 'guided'], default='kong')
ap.add_argument('--perf')
ap.add_argument('--data')
ap.add_argument('--wav')
ap.add_argument('--fmt3x')
ap.add_argument('--model', default='output/transcription/guided/velocity_model.json')
ap.add_argument('--device')
ap.add_argument('--out', required=True)
a = ap.parse_args()
if a.perf:
    f = pedb.perf_files(pedb.data_dir(a.data), a.perf)
    a.wav, a.fmt3x = a.wav or f['wav'], a.fmt3x or f['fmt3x']
if a.method != 'kong' and not a.fmt3x:
    ap.error(f'--method {a.method} には楽譜(--fmt3x か --perf)が必要です')

x, sr = audio.load_wav(a.wav)
pedal = []
if a.method in ('kong', 'hybrid'):
    from piano_transcribe import kong
    knotes, pedal = kong.Transcriber(device=a.device).transcribe(x, sr)

if a.method != 'kong':
    tpqn, score, _ = pedb.load_fmt3x(a.fmt3x)
    est = guided.estimate(x, sr, tpqn, score)
    rows = list(est.values())
    if os.path.exists(a.model):
        vel = guided.predict_velocity(rows, guided.load_model(a.model))
    else:
        print('ベロシティの回帰モデルがないため、強さの特徴量を 30〜100 に線形に割り当てます', file=sys.stderr)
        pk = [r['f_peak'] for r in rows]
        lo, hi = min(pk), max(pk)
        vel = [30 + 70 * (v - lo) / (hi - lo + 1e-9) for v in pk]
    gnotes = {i: (r['t_est'], r['t_est'] + max(0.05, r['dur_sec'] * 0.9), r['pitch'], v)
              for (i, r), v in zip(est.items(), vel)}

if a.method == 'kong':
    notes = knotes
elif a.method == 'guided':
    notes = list(gnotes.values())
else:
    assign, shift = guided.assign_draft(est, knotes)
    fill = [(on + shift, off + shift, p, v) for i, (on, off, p, v) in gnotes.items() if i not in assign]
    print(f'楽譜の音 {len(est)} のうち {len(assign)} に下書きの音を割り当て、{len(fill)} を楽譜からの推定で補いました',
          file=sys.stderr)
    notes = list(map(tuple, knotes)) + fill
midi.write_midi(a.out, notes, pedal)
print(len(notes), 'notes ->', a.out)
