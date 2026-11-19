#!/usr/bin/env python3
"""演奏MIDIと楽譜の対応付け(performance_analysis/align.py)を、matchファイル(人手で修正済み)と比べて評価する

入力の演奏
  --source midi  同時記録MIDI
  --source kong  採譜モデルの出力(<trans>/kong/<演奏名>.npz、eval_transcription.py が作る)
                 正解は「同時記録MIDIの音と発音±50msで対応した推定音」に match のラベルを移したもの。
                 対応する推定音のない楽譜音は、正解でも「抜け」とみなす

指標
  演奏音のラベル   各演奏音に付けた楽譜音ID(余分な音は '*')が正解と同じ割合
  楽譜音の対応     各楽譜音に対応させた演奏音(なしを含む)が正解と同じ割合
  抜けた音・余分な音の検出の 適合率・再現率、装飾の音のラベルの正しさ
  共有音(2声部が共有する音)は同じ組なら同じ楽譜音とみなす。

調整(align.PARAMS)には DEV_PIECES の曲だけを使い、それ以外の曲(TEST)で精度を測る。
使い方: python3 scripts/performance/eval_alignment.py [--source midi|kong] [--split dev|test|all] [演奏名 ...]
"""
import argparse
import csv
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
from music_io import midi, pedb  # noqa: E402
from performance_analysis import align  # noqa: E402

# 調整に使う曲(採譜の評価で guided の調整に使った8演奏の曲)
DEV_PIECES = ['bac-inv001', 'chp-etd003', 'moz-snt331-1', 'chp-nct002', 'deb-pld108', 'bet-snt014-1', 'sch-knd007']

ap = argparse.ArgumentParser()
ap.add_argument('perfs', nargs='*')
ap.add_argument('--data')
ap.add_argument('--source', choices=['midi', 'kong'], default='midi')
ap.add_argument('--split', choices=['dev', 'test', 'all'], default='all')
ap.add_argument('--trans', default='output/transcription')
ap.add_argument('--out', default='output/alignment')
ap.add_argument('--params', help='align.PARAMS を上書きする JSON')
ap.add_argument('--quiet', action='store_true')
a = ap.parse_args()
root = pedb.data_dir(a.data)
perfs = a.perfs or pedb.list_performances(root)
if a.split != 'all':
    perfs = [p for p in perfs if (pedb.piece_of(p) in DEV_PIECES) == (a.split == 'dev')]
params = json.loads(a.params) if a.params else None
os.makedirs(a.out, exist_ok=True)


def one_to_one(est_on, est_p, ref_on, ref_p, tol):
    """同じ音高で発音時刻の差が tol 以内の組を、差の小さい順に1対1で対応させる。{推定の番号: 正解の番号}"""
    cand = []
    for p in np.intersect1d(est_p, ref_p):
        ie, ir = np.where(est_p == p)[0], np.where(ref_p == p)[0]
        d = np.abs(est_on[ie][:, None] - ref_on[ir][None, :])
        for x, y in zip(*np.where(d <= tol)):
            cand.append((d[x, y], ie[x], ir[y]))
    out, used = {}, set()
    for _, e, r in sorted(cand):
        if e not in out and r not in used:
            out[e] = r
            used.add(r)
    return out


def ratio(a_, b_):
    return a_ / b_ if b_ else float('nan')


tot = {}
rows_out = []
for p in perfs:
    f = pedb.perf_files(root, p)
    tpqn, score, dups = pedb.load_fmt3x(f['fmt3x'], grace=True)
    match, _ = pedb.load_match(f['match'])
    ref = midi.read_midi_notes(f['mid'])
    canon = {i: sorted(g)[0] for g in dups for i in g}
    cid = lambda i: canon.get(i, i)
    # 正解: 同時記録MIDIの各音のラベル
    m_on = np.array([x['on'] for x in match])
    m_p = np.array([pedb.midi_pitch(x['pitch_name']) for x in match])
    mm = one_to_one(m_on, m_p, ref[:, 0], ref[:, 2], 0.002)
    ref_lab = {r: (cid(match[k]['note_id']), match[k]['status']) for k, r in mm.items()}
    if a.source == 'midi':
        perf = ref
        lab = {j: ref_lab.get(j, ('*', -1)) for j in range(len(ref))}
    else:
        z = np.load(os.path.join(a.trans, 'kong', p + '.npz'))
        perf = z['notes']
        d = []
        for pch in np.unique(ref[:, 2]):
            te = perf[perf[:, 2] == pch, 0]
            if len(te):
                for t in ref[ref[:, 2] == pch, 0]:
                    k = np.argmin(np.abs(te - t))
                    if abs(te[k] - t) <= 0.15:
                        d.append(te[k] - t)
        off = np.median(d)
        km = one_to_one(perf[:, 0] - off, perf[:, 2], ref[:, 0], ref[:, 2], 0.05)
        lab = {j: ref_lab.get(km[j], ('*', -1)) if j in km else ('*', -1) for j in range(len(perf))}
    # align は発音順に並べ替えた番号を返すので、元の番号に戻す
    order = np.lexsort((perf[:, 2], perf[:, 0]))
    res, miss = align.align(tpqn, score, dups, perf, params)
    pred = {int(order[r['id']]): (cid(r['note_id']), r['status']) for r in res}

    # 楽譜音 -> 演奏音(状態0)
    def score_map(L):
        out = {}
        for j, (nid, st) in L.items():
            if nid != '*' and st == 0:
                out[nid] = j
        return out
    ts, ps = score_map(lab), score_map(pred)
    sids = {cid(i) for i, n in score.items() if n['pitch'] is not None}
    s_ok = sum(ts.get(i) == ps.get(i) for i in sids)
    lab_ok = sum(lab[j][0] == pred[j][0] for j in lab)
    t_miss, p_miss = sids - set(ts), sids - set(ps)
    t_ext = {j for j, (nid, _) in lab.items() if nid == '*'}
    p_ext = {j for j, (nid, _) in pred.items() if nid == '*'}
    t_orn = {j for j, (_, st) in lab.items() if st == 1}
    r = dict(performance=p, piece=pedb.piece_of(p), n_perf=len(perf), n_score=len(sids),
             label_ok=lab_ok, score_ok=s_ok,
             miss_true=len(t_miss), miss_pred=len(p_miss), miss_hit=len(t_miss & p_miss),
             extra_true=len(t_ext), extra_pred=len(p_ext), extra_hit=len(t_ext & p_ext),
             orn_true=len(t_orn), orn_ok=sum(pred[j] == lab[j] for j in t_orn))
    rows_out.append(r)
    for k, v in r.items():
        if isinstance(v, int):
            tot[k] = tot.get(k, 0) + v
    if not a.quiet:
        print(f"{p:22s} 演奏音のラベル {r['label_ok'] / r['n_perf']:.4f}  楽譜音の対応 {r['score_ok'] / r['n_score']:.4f}"
              f"  抜け 正解{r['miss_true']} 推定{r['miss_pred']} 一致{r['miss_hit']}"
              f"  余分 正解{r['extra_true']} 推定{r['extra_pred']} 一致{r['extra_hit']}", flush=True)

t = tot
print(f"\n== {a.source}, {a.split}: {len(perfs)}演奏 ==")
print(f"演奏音のラベルの正解率 {ratio(t['label_ok'], t['n_perf']):.4f}   楽譜音の対応の正解率 {ratio(t['score_ok'], t['n_score']):.4f}")
print(f"抜けた音  正解 {t['miss_true']} 推定 {t['miss_pred']}  適合率 {ratio(t['miss_hit'], t['miss_pred']):.3f} 再現率 {ratio(t['miss_hit'], t['miss_true']):.3f}")
print(f"余分な音  正解 {t['extra_true']} 推定 {t['extra_pred']}  適合率 {ratio(t['extra_hit'], t['extra_pred']):.3f} 再現率 {ratio(t['extra_hit'], t['extra_true']):.3f}")
print(f"装飾の音  正解 {t['orn_true']}  ラベルの正解率 {ratio(t['orn_ok'], t['orn_true']):.3f}")
print(f"演奏ごとの楽譜音の対応の正解率: 最小 {min(r['score_ok'] / r['n_score'] for r in rows_out):.4f}  "
      f"中央値 {np.median([r['score_ok'] / r['n_score'] for r in rows_out]):.4f}")
path = os.path.join(a.out, f'eval_{a.source}_{a.split}.csv')
with open(path, 'w', newline='') as fo:
    w = csv.DictWriter(fo, fieldnames=list(rows_out[0]))
    w.writeheader()
    w.writerows(rows_out)
