#!/usr/bin/env python3
"""全演奏について「音符1つを1行とする表」を作る

使い方: python3 scripts/performance/make_note_tables.py [--source match|midi|kong] [--data PEDBのフォルダ] [--out 出力フォルダ] [--half 4]
  --source match  人手で修正済みの matchファイルの対応付けを使う(既定)
  --source midi   同時記録MIDIを楽譜と自動で対応付ける(performance_analysis/align.py)
  --source kong   採譜モデルの出力(<trans>/kong/<演奏名>.npz)を楽譜と自動で対応付ける。録音 -> 表 の全自動
出力: <out>/notes/<演奏名>.csv, <out>/all_notes.csv, <out>/summary.csv
      自動の対応付けでは <out>/match/<演奏名>.match.txt も書く(matchファイルと同じ形式。人が確認・修正できる)
"""
import argparse
import csv
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
from music_io import midi, pedb  # noqa: E402
from performance_analysis import note_table as nt  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('--data', help='PEDBのフォルダ(既定: 環境変数 PEDB_DIR か ~/git/6-python/data/pedb2_v0.0.1.b)')
ap.add_argument('--source', choices=['match', 'midi', 'kong'], default='match')
ap.add_argument('--trans', default='output/transcription', help='--source kong のときの採譜結果の場所')
ap.add_argument('--out', help='既定: output/note_tables(match 以外は output/note_tables_<source>)')
ap.add_argument('--half', type=float, default=4.0, help='テンポを平滑化する窓の半幅(四分音符)')
a = ap.parse_args()
out = a.out or ('output/note_tables' if a.source == 'match' else f'output/note_tables_{a.source}')

root = pedb.data_dir(a.data)
os.makedirs(os.path.join(out, 'notes'), exist_ok=True)
if a.source != 'match':
    os.makedirs(os.path.join(out, 'match'), exist_ok=True)
all_path = os.path.join(out, 'all_notes.csv')
summaries = []
perfs = pedb.list_performances(root)
if a.source == 'kong':
    perfs = [p for p in perfs if os.path.exists(os.path.join(a.trans, 'kong', p + '.npz'))]
for i, perf in enumerate(perfs):
    f = pedb.perf_files(root, perf)
    if a.source == 'match':
        rows, summ = nt.analyze(root, perf, a.half)
    else:
        notes = midi.read_midi_notes(f['mid']) if a.source == 'midi' else np.load(os.path.join(a.trans, 'kong', perf + '.npz'))['notes']
        rows, summ, (m, miss) = nt.analyze_midi(perf, f['fmt3x'], notes, a.half)
        _, score, _ = pedb.load_fmt3x(f['fmt3x'], grace=True)
        pedb.write_match(os.path.join(out, 'match', perf + '.match.txt'), m, miss, score)
    nt.write_rows(os.path.join(out, 'notes', perf + '.csv'), rows)
    nt.write_rows(all_path, rows, header=(i == 0), mode='w' if i == 0 else 'a')
    summaries.append(summ)
with open(os.path.join(out, 'summary.csv'), 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(summaries[0]))
    w.writeheader()
    for s in summaries:
        w.writerow({k: nt.fmt(v) for k, v in s.items()})
print(len(summaries), 'performances ->', out)
