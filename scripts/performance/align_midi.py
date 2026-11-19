#!/usr/bin/env python3
"""演奏MIDIを楽譜と対応付け、matchファイル形式の対応と、音符ごとの表を書き出す

使い方: python3 scripts/performance/align_midi.py --midi 演奏.mid --fmt3x 楽譜_fmt3x.txt --out 出力の名前(拡張子なし)
  出力: <out>.match.txt(人が確認・修正できる) と <out>.csv(音符ごとの表)
  修正した match ファイルから表を作り直すには performance_analysis.note_table.analyze_match を使う。
  MIDI はキーボードで録ったもの、採譜モデルの出力(scripts/transcribe/transcribe_to_midi.py --method kong)のどちらでもよい。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
from music_io import midi, pedb  # noqa: E402
from performance_analysis import note_table as nt  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('--midi', required=True)
ap.add_argument('--fmt3x', required=True)
ap.add_argument('--out', required=True)
ap.add_argument('--half', type=float, default=4.0)
a = ap.parse_args()
name = os.path.basename(a.out)
rows, summ, (m, miss) = nt.analyze_midi(name, a.fmt3x, midi.read_midi_notes(a.midi), a.half)
_, score, _ = pedb.load_fmt3x(a.fmt3x, grace=True)
pedb.write_match(a.out + '.match.txt', m, miss, score)
nt.write_rows(a.out + '.csv', rows)
n_extra = sum(p['note_id'] == '*' for p in m)
print(f"演奏音 {len(m)}(余分 {n_extra})、弾かれなかった楽譜音 {len(miss)}、平均テンポ {summ['mean_tempo_bpm']:.1f} -> {a.out}.match.txt, {a.out}.csv")
