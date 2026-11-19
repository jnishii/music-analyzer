#!/usr/bin/env python3
"""自動で作った音符ごとの表を、matchファイル(人手で修正済み)から作った表と列ごとに比べる

使い方: python3 scripts/performance/compare_note_tables.py [--ref output/note_tables] output/note_tables_kong [...]
  曲を DEV(対応付けの調整に使った曲)と TEST に分けて集計する。
  両方の表で値のある音だけを比べる(楽譜音IDで突き合わせる)。
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
from music_io import pedb  # noqa: E402

DEV_PIECES = ['bac-inv001', 'chp-etd003', 'moz-snt331-1', 'chp-nct002', 'deb-pld108', 'bet-snt014-1', 'sch-knd007']
COLS = [('local_tempo_bpm', '局所テンポ'), ('timing_dev_ms', 'タイミングのずれ(ms)'), ('event_dev_ms', '拍のずれ(ms)'),
        ('chord_async_ms', '和音内の時間差(ms)'), ('velocity_z', '強さ(velocity_z)'), ('articulation', '音の長さの比')]

ap = argparse.ArgumentParser()
ap.add_argument('tables', nargs='+')
ap.add_argument('--ref', default='output/note_tables')
a = ap.parse_args()
ref = pd.read_csv(os.path.join(a.ref, 'all_notes.csv'))
for t in a.tables:
    est = pd.read_csv(os.path.join(t, 'all_notes.csv'))
    d = ref.merge(est, on=['performance', 'note_id'], suffixes=('_ref', '_est'))
    d['split'] = np.where(d.performance.map(pedb.piece_of).isin(DEV_PIECES), 'DEV', 'TEST')
    print(f'\n== {t}(正解: {a.ref}) ==')
    for sp in ['DEV', 'TEST']:
        x = d[d.split == sp]
        st = (x.status_ref == x.status_est).mean()
        print(f'-- {sp}: {x.performance.nunique()}演奏 {len(x):,}音  状態(ok/missing など)の一致 {st:.1%}')
        print('  列                      相関   差の絶対値の中央値 / 90%点   演奏ごとの相関の中央値')
        for c, name in COLS:
            y = x[[c + '_ref', c + '_est', 'performance']].dropna()
            if len(y) < 10:
                continue
            e = np.abs(y[c + '_est'] - y[c + '_ref'])
            r = np.corrcoef(y[c + '_ref'], y[c + '_est'])[0, 1]
            rp = y.groupby('performance').apply(lambda g: np.corrcoef(g[c + '_ref'], g[c + '_est'])[0, 1] if len(g) > 5 else np.nan,
                                               include_groups=False)
            print(f'  {name:16s}  {r:6.3f}   {np.median(e):8.3f} / {np.percentile(e, 90):8.3f}          {np.nanmedian(rp):.3f}')
