#!/usr/bin/env python3
"""楽譜を手がかりにした採譜の自動補正の規則を、PEDB で検証する

採譜モデル(Kong)の出力を楽譜と対応付け(alignment.align)、違いごとに次の規則を試す。
  追加: 楽譜にあって出力にない音 -> 楽譜から予測した時刻の近く(±100ms)で、その音高の発音の出力の山が τ 以上なら足す
        (山の位置に発音時刻を置く。refine.find_onset)
  削除: 楽譜になく出力にある音 -> 次のどちらかに当たれば消す
        再検出: 同じ音高の直前の音が、この音の始まる直前(30ms 以内)に終わっている(モデルは同じ音高の
                新しい発音で前の音を打ち切るので、1つの音が割れるとこの形になる)
        倍音:   1〜2 オクターブ・12度下の音が ±50ms 以内に始まっている
正解: 同時記録MIDI と matchファイル。
  追加が正しい = その楽譜の音が実際に弾かれていて、置いた時刻が正しい発音から 50ms 以内
  削除が正しい = その音が同時記録MIDI のどの音とも対応しない(採譜の誤り)
調整は DEV の曲だけで見て決め、TEST(それ以外の曲)で精度を報告する。

前提: output/transcription/kong_frames/<演奏名>.npz(save_kong_frames.py)
使い方: python3 scripts/transcribe/eval_score_autofix.py [--split dev|test|all]
"""
import argparse
import glob
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
from alignment import align, notation  # noqa: E402
from music_io import midi, musicxml, pedb  # noqa: E402
from piano_transcribe import evaluate as ev, refine  # noqa: E402

DEV_PIECES = ['bac-inv001', 'chp-etd003', 'moz-snt331-1', 'chp-nct002', 'deb-pld108', 'bet-snt014-1', 'sch-knd007']
TAUS = [0.02, 0.05, 0.1, 0.2, 0.3]

ap = argparse.ArgumentParser()
ap.add_argument('--data')
ap.add_argument('--frames', default='output/transcription/kong_frames')
ap.add_argument('--split', choices=['dev', 'test', 'all'], default='dev')
a = ap.parse_args()
root = pedb.data_dir(a.data)
perfs = [p for p in pedb.list_performances(root) if os.path.exists(os.path.join(a.frames, p + '.npz'))]
if a.split != 'all':
    perfs = [p for p in perfs if (pedb.piece_of(p) in DEV_PIECES) == (a.split == 'dev')]

adds = []   # (手がかり, 弾かれていたか, 置いた時刻が正しいか)
dels = []   # (種類, 採譜の誤りか)
for p in perfs:
    f = pedb.perf_files(root, p)
    z = np.load(os.path.join(a.frames, p + '.npz'))
    out = {k: z[k].astype(np.float32) for k in ('reg_onset_output', 'velocity_output', 'frame_output')}
    kn = z['notes']
    ref = midi.read_midi_notes(f['mid'])
    o = ev.constant_offset(kn, ref)
    xml = sorted(glob.glob(os.path.join(f['dir'], '*.xml')))[0]
    tpqn, score, dups = musicxml.load_musicxml(xml)
    match, _ = pedb.load_match(f['match'])
    # 正解: 楽譜の音 -> 弾かれた発音時刻(録音の時刻に直す)
    played = {}
    for m in match:
        if m['note_id'] != '*' and m['status'] == 0:
            played.setdefault(m['note_id'], m['on'] + o)
    groups = {i: g for g in dups for i in g}
    # 採譜の出力の各音が本物か(同時記録MIDI と対応するか)
    real = {e for e, _ in ev.match_notes(kn - [o, o, 0, 0], ref)}
    order = np.lexsort((kn[:, 2], kn[:, 0]))
    rows, miss = align.align(tpqn, score, dups, kn)
    ev_t = {}
    for r in rows:
        if r['status'] == 0 and r['err'] == 0 and r['note_id'] in score:
            ev_t.setdefault(score[r['note_id']]['stime'] / tpqn, []).append(r['on'])
    tm = notation.TimeMap(list(ev_t), [float(np.median(v)) for v in ev_t.values()])
    for i in miss:
        n = score.get(i)
        if n is None or n['kind'] != 'chord':
            continue
        pred = float(np.interp(n['stime'] / tpqn, tm.b, tm.t))
        t, e = refine.find_onset(out, n['pitch'], pred, 0.1)
        truth = next((played[g] for g in groups.get(i, [i]) if g in played), None)
        adds.append((e, truth is not None, truth is not None and abs(t - truth) <= 0.05))
    sk = kn[order]
    for r in rows:
        if r['note_id'] != '*':
            continue
        j = r['id']
        on, off, pch = sk[j, 0], sk[j, 1], sk[j, 2]
        # 再検出: 同じ音高の直前の音が、この音の始まる直前(30ms 以内)に終わっている(1つの音が割れた)
        redet = np.any((sk[:, 2] == pch) & (sk[:, 0] < on - 0.03) & (sk[:, 1] >= on - 0.03))
        harm = np.any(np.isin(pch - sk[:, 2], [12, 19, 24]) & (np.abs(sk[:, 0] - on) <= 0.05))
        kind = '再検出' if redet else ('倍音' if harm else 'その他')
        dels.append((kind, int(order[j]) not in real))

print(f'== {a.split}: {len(perfs)} 演奏 ==')
A = np.array(adds, dtype=object)
e = A[:, 0].astype(float)
pl, ok = A[:, 1].astype(bool), A[:, 2].astype(bool)
print(f'楽譜にあって出力にない音 {len(A):,}(うち実際に弾かれていた {pl.sum():,}、弾かれていなかった {(~pl).sum():,})')
print('  τ     足す音   正しい(弾かれていて50ms以内)  弾かれていない音を足した  弾かれていた音のうち足せた割合')
for tau in TAUS:
    s = e >= tau
    print(f'  {tau:<5} {s.sum():6,}   {ok[s].mean() if s.any() else float("nan"):6.1%}                  '
          f'{(~pl[s]).sum():5,}                    {(s & ok).sum() / max(pl.sum(), 1):6.1%}')
D = dels
print(f'\n楽譜になく出力にある音 {len(D):,}(うち採譜の誤り {sum(x[1] for x in D):,})')
for kind in ['再検出', '倍音', 'その他']:
    k = [x for x in D if x[0] == kind]
    if k:
        print(f'  {kind}: {len(k):,} 音、うち採譜の誤り {np.mean([x[1] for x in k]):.1%}')
