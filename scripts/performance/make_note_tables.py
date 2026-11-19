#!/usr/bin/env python3
"""全演奏について「音符1つを1行とする表」を作る

使い方: python3 scripts/performance/make_note_tables.py [--data PEDBのフォルダ] [--out 出力フォルダ] [--half 4]
出力: <out>/notes/<演奏名>.csv, <out>/all_notes.csv, <out>/summary.csv
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
from music_io import pedb  # noqa: E402
from performance_analysis import note_table as nt  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('--data', help='PEDBのフォルダ(既定: 環境変数 PEDB_DIR か ~/git/6-python/data/pedb2_v0.0.1.b)')
ap.add_argument('--out', default='output/note_tables')
ap.add_argument('--half', type=float, default=4.0, help='テンポを平滑化する窓の半幅(四分音符)')
a = ap.parse_args()

root = pedb.data_dir(a.data)
os.makedirs(os.path.join(a.out, 'notes'), exist_ok=True)
all_path = os.path.join(a.out, 'all_notes.csv')
summaries = []
for i, perf in enumerate(pedb.list_performances(root)):
    rows, summ = nt.analyze(root, perf, a.half)
    nt.write_rows(os.path.join(a.out, 'notes', perf + '.csv'), rows)
    nt.write_rows(all_path, rows, header=(i == 0), mode='w' if i == 0 else 'a')
    summaries.append(summ)
with open(os.path.join(a.out, 'summary.csv'), 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(summaries[0]))
    w.writeheader()
    for s in summaries:
        w.writerow({k: nt.fmt(v) for k, v in s.items()})
print(len(summaries), 'performances ->', a.out)
