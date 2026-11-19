#!/usr/bin/env python3
"""録音からの推定を、同時記録MIDI(=matchファイルの値)と比べて評価する

使い方: python3 scripts/eval_transcription.py [--data ...] [--out output/eval] 演奏名 [演奏名 ...]
  演奏名を省略すると、既定の8演奏で評価する。
  ベロシティは「1演奏を除いて学習 -> 除いた演奏で評価」の交差検証。
  最後に全演奏で学習した回帰係数を <out>/velocity_model.json に保存する(transcribe_to_midi.py が使う)。
"""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from music_analysis import pedb, transcribe as tr  # noqa: E402

DEFAULT = ['bac-inv001-o-p1', 'bac-inv001-s-p2', 'chp-etd003-s-p1', 'moz-snt331-1-s-p1',
           'chp-nct002-s-p1', 'deb-pld108-s-p1', 'bet-snt014-1-s-p1', 'sch-knd007-s-p1']

ap = argparse.ArgumentParser()
ap.add_argument('perfs', nargs='*')
ap.add_argument('--data')
ap.add_argument('--out', default='output/eval')
a = ap.parse_args()
root = pedb.data_dir(a.data)
perfs = a.perfs or DEFAULT
os.makedirs(a.out, exist_ok=True)

# 推定(結果はキャッシュ)
res = {}
for p in perfs:
    cache = os.path.join(a.out, f'{p}.estimate.json')
    if os.path.exists(cache):
        est = json.load(open(cache))
    else:
        f = pedb.perf_files(root, p)
        est = tr.estimate(f['wav'], f['fmt3x'])
        json.dump(est, open(cache, 'w'))
    match, _ = pedb.load_match(pedb.perf_files(root, p)['match'])
    rows = []
    for m in match:  # 正しく弾かれた非装飾音だけを評価する
        if m['status'] == 0 and m['err'] == 0 and m['note_id'] in est:
            rows.append(dict(est[m['note_id']], t_true=m['on'], vel_true=m['vel']))
    res[p] = rows
    print(p, len(rows), 'notes', flush=True)


def tstats(e):
    x = np.abs(e) * 1000
    return dict(median_ms=np.median(x), p90_ms=np.percentile(x, 90),
                within10=(x <= 10).mean(), within25=(x <= 25).mean(), within50=(x <= 50).mean())


report = {'onset': {}, 'velocity': {}}
print('\n== 発音時刻(録音とMIDIの一定のずれを差し引いた後の絶対誤差) ==')
alle = {'t_dtw': [], 't_est': []}
for p, rows in res.items():
    t = np.array([[r['t_true'], r['t_dtw'], r['t_est']] for r in rows])
    out = {}
    for i, key in [(1, 't_dtw'), (2, 't_est')]:
        e = t[:, i] - t[:, 0]
        o = np.median(e)
        alle[key].append(e - o)
        out[key] = dict(tstats(e - o), offset_ms=o * 1000)
    report['onset'][p] = out
    s = out['t_est']
    print(f"{p:22s} n={len(t):5d} DTWのみ {out['t_dtw']['median_ms']:6.1f}ms | 微調整後 中央値 {s['median_ms']:5.1f}ms"
          f"  ≤10ms {s['within10']:.0%} ≤25ms {s['within25']:.0%} ≤50ms {s['within50']:.0%}  一定のずれ {s['offset_ms']:.0f}ms")
for key in alle:
    report['onset']['ALL_' + key] = tstats(np.concatenate(alle[key]))
print('全体(微調整後)', {k: round(float(v), 3) for k, v in report['onset']['ALL_t_est'].items()})

print('\n== ベロシティ(1演奏抜き交差検証) ==')
ys, yh = [], []
for p in perfs:
    train = [r for q in perfs if q != p for r in res[q]]
    beta = tr.fit_velocity(train, [r['vel_true'] for r in train])
    y = np.array([r['vel_true'] for r in res[p]], float)
    h = tr.predict_velocity(res[p], beta)
    ys.append(y)
    yh.append(h)
    report['velocity'][p] = dict(r=float(np.corrcoef(y, h)[0, 1]), mae=float(np.abs(y - h).mean()), true_sd=float(y.std()))
    print(f"{p:22s} 相関 {report['velocity'][p]['r']:.2f}  平均絶対誤差 {report['velocity'][p]['mae']:4.1f}  真値の標準偏差 {y.std():4.1f}")
y, h = np.concatenate(ys), np.concatenate(yh)
report['velocity']['ALL'] = dict(r=float(np.corrcoef(y, h)[0, 1]), mae=float(np.abs(y - h).mean()))
print('全体 相関 %.2f  平均絶対誤差 %.1f' % (report['velocity']['ALL']['r'], report['velocity']['ALL']['mae']))

allrows = [r for p in perfs for r in res[p]]
tr.save_model(os.path.join(a.out, 'velocity_model.json'), tr.fit_velocity(allrows, [r['vel_true'] for r in allrows]))
json.dump(report, open(os.path.join(a.out, 'report.json'), 'w'), indent=1, default=float)
print('\n->', a.out)
