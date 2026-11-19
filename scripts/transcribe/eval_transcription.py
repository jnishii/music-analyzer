#!/usr/bin/env python3
"""録音からの推定を、同時記録MIDI(と matchファイル)と比べて評価する

比べる方式
  guided  楽譜情報つきの推定(学習済みモデルなし, piano_transcribe/guided.py)
  kong    高解像度ピアノ採譜モデル(楽譜を使わない, piano_transcribe/kong.py)
  hybrid  kong の下書きの音を、guided の予測時刻の近くで楽譜の音符に割り当てたもの。
          割り当てられなかった楽譜の音は guided の推定で補う

指標
  音符単位: 同時記録MIDIの全音に対する 適合率・再現率・F1(発音±50ms / 発音+離鍵)
          離鍵は「鍵を離した時刻」と「ペダルで伸ばした音の終わり」の2通りで比べる(kong は後者を推定する)
  楽譜の音符単位: 正しく弾かれた非装飾音の発音誤差・ベロシティ(guided, hybrid のみ。kong は楽譜と対応しない)
  抜けた音の検出(hybrid): 下書きの音が割り当てられなかった楽譜の音 = 抜けた音 とみなす
  ペダル(kong): 踏んでいるかどうかの 10ms ごとの一致
  発音時刻は、方式ごと・演奏ごとに一定のずれ(録音とMIDIの時計のずれを含む)を差し引いてから比べる。
  ベロシティの補正(guided の回帰, kong の線形補正)は1曲抜きの交差検証(同じ曲の別演奏も学習から除く)。

使い方: python3 scripts/transcribe/eval_transcription.py [--data ...] [--out output/transcription] [演奏名 ...]
  演奏名を省略すると全演奏。推定結果は <out>/guided/, <out>/kong/ にキャッシュする(kong は .mid も書く)。
  guided の調整に使った8演奏(DEV)とそれ以外を分けて集計する。
  最後に全演奏で学習した guided のベロシティ回帰係数を <out>/guided/velocity_model.json に保存する。
"""
import argparse
import csv
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
from music_io import audio, midi, pedb  # noqa: E402
from piano_transcribe import evaluate as ev, guided  # noqa: E402

# guided の作成・調整に使った演奏
DEV = ['bac-inv001-o-p1', 'bac-inv001-s-p2', 'chp-etd003-s-p1', 'moz-snt331-1-s-p1',
       'chp-nct002-s-p1', 'deb-pld108-s-p1', 'bet-snt014-1-s-p1', 'sch-knd007-s-p1']

ap = argparse.ArgumentParser()
ap.add_argument('perfs', nargs='*')
ap.add_argument('--data')
ap.add_argument('--out', default='output/transcription')
ap.add_argument('--device', help='kong の計算に使う装置(既定: mps があれば mps)')
a = ap.parse_args()
root = pedb.data_dir(a.data)
perfs = a.perfs or pedb.list_performances(root)
for m in ['guided', 'kong']:
    os.makedirs(os.path.join(a.out, m), exist_ok=True)

_kong = None


def run_kong(x, sr):
    global _kong
    if _kong is None:
        from piano_transcribe import kong
        _kong = kong.Transcriber(device=a.device)
    return _kong.transcribe(x, sr)


def load_or_estimate(p):
    f = pedb.perf_files(root, p)
    gpath = os.path.join(a.out, 'guided', p + '.estimate.json')
    kpath = os.path.join(a.out, 'kong', p + '.npz')
    x = sr = None
    if not (os.path.exists(gpath) and os.path.exists(kpath)):
        x, sr = audio.load_wav(f['wav'])
    if os.path.exists(gpath):
        est = json.load(open(gpath))
    else:
        tpqn, score, _ = pedb.load_fmt3x(f['fmt3x'])
        est = guided.estimate(x, sr, tpqn, score)
        json.dump(est, open(gpath, 'w'))
    if os.path.exists(kpath):
        z = np.load(kpath)
        knotes, kpedal = z['notes'], z['pedal']
    else:
        knotes, kpedal = run_kong(x, sr)
        np.savez(kpath, notes=knotes, pedal=kpedal)
        midi.write_midi(os.path.join(a.out, 'kong', p + '.mid'), knotes, kpedal)
    return est, knotes, kpedal


# --- 1. 推定と、ベロシティの補正を除く評価
data = {}
for n, p in enumerate(perfs):
    f = pedb.perf_files(root, p)
    est, knotes, kpedal = load_or_estimate(p)
    ref = midi.read_midi_notes(f['mid'])
    ref_pedal = midi.read_midi_pedal(f['mid'])
    ref_sound = ev.extend_by_pedal(ref, ref_pedal)  # 音が止んだ時刻を終わりとした正解
    _, score, dups = pedb.load_fmt3x(f['fmt3x'])
    match, missing = pedb.load_match(f['match'])

    assign, shift = guided.assign_draft(est, knotes)
    ids = list(est)
    # 音符単位の比較用の音の並び
    gnotes = np.array([(e['t_est'], e['t_est'] + max(0.05, e['dur_sec'] * 0.9), e['pitch'], 64) for e in est.values()])
    unassigned = [i for i in ids if i not in assign]
    fill = np.array([(est[i]['t_est'] + shift, est[i]['t_est'] + shift + max(0.05, est[i]['dur_sec'] * 0.9),
                      est[i]['pitch'], 64) for i in unassigned]).reshape(-1, 4)
    hnotes = np.vstack([knotes, fill])

    r = dict(performance=p, piece=pedb.piece_of(p), dev=p in DEV, duration=float(ref[:, 1].max()))
    for name, notes in [('guided', gnotes), ('kong', knotes), ('hybrid', hnotes)]:
        o = ev.constant_offset(notes, ref)
        e = notes.copy()
        e[:, :2] -= o
        s, pairs = ev.note_scores(e, ref)
        s2, _ = ev.note_scores(e, ref_sound)
        r[name] = dict(s, offset_ms=o * 1000, n_match_sound=s2['n_match_offset'], sound_offset_f1=s2['offset_f1'])
        if name == 'kong' and pairs:
            i, j = np.array(pairs).T
            r['kong_vel'] = dict(est=e[i, 3].tolist(), true=ref[j, 3].tolist())
    r['pedal'] = ev.pedal_scores(kpedal, ref_pedal, r['duration'])

    # 楽譜の音符単位: 正しく弾かれた非装飾音
    rows = []
    for m in match:
        if m['status'] == 0 and m['err'] == 0 and m['note_id'] in est:
            i = m['note_id']
            k = assign.get(i)
            rows.append(dict(est[i], t_true=m['on'], vel_true=m['vel'],
                             t_hyb=knotes[k, 0] if k is not None else est[i]['t_est'] + shift,
                             v_kong=knotes[k, 3] if k is not None else np.nan))
    r['rows'] = rows

    # 抜けた音の検出(共有音は、同じ組のどれかが弾かれていれば弾かれたとみなす)
    group = {i: g for g in dups for i in g}
    played = {m['note_id'] for m in match if m['note_id'] != '*'}
    true_missing = {i for i in ids if not ({i} | set(group.get(i, [i]))) & played}
    pred_missing = {i for i in ids if not ({i} | set(group.get(i, [i]))) & set(assign)}
    r['missing'] = dict(true=len(true_missing), pred=len(pred_missing), hit=len(true_missing & pred_missing))
    data[p] = r
    print(f"[{n + 1}/{len(perfs)}] {p:22s} F1 guided {r['guided']['f1']:.3f} kong {r['kong']['f1']:.3f} "
          f"hybrid {r['hybrid']['f1']:.3f}", flush=True)

# --- 2. ベロシティ(1曲抜きの交差検証)
for p, r in data.items():
    train = [q for q in data.values() if q['piece'] != r['piece']]
    trows = [x for q in train for x in q['rows']]
    rows = r['rows']
    y = np.array([x['vel_true'] for x in rows], float)
    if not len(rows):
        continue
    # guided: 倍音エネルギーからの回帰
    vg = guided.predict_velocity(rows, guided.fit_velocity(trows, [x['vel_true'] for x in trows]))
    # kong / hybrid: 出力ベロシティを線形に補正(係数は他の曲の、割り当てられた音で求める)
    kv = np.array([v for q in train for v in q['kong_vel']['est']])
    kt = np.array([v for q in train for v in q['kong_vel']['true']])
    b1, b0 = np.polyfit(kv, kt, 1)
    vk = np.array([x['v_kong'] for x in rows])
    vh = np.where(np.isnan(vk), vg, np.clip(b0 + b1 * vk, 1, 127))
    vh_raw = np.where(np.isnan(vk), vg, vk)
    r['vel'] = dict(true=y, guided=vg, hybrid=vh, hybrid_raw=vh_raw)
    kve, kvt = np.array(r['kong_vel']['est']), np.array(r['kong_vel']['true'])
    r['kong_vel_cal'] = dict(raw=ev.velocity_scores(kve, kvt),
                             cal=ev.velocity_scores(np.clip(b0 + b1 * kve, 1, 127), kvt))


# --- 3. 集計
def onset_errors(rows, key):
    e = np.array([x[key] - x['t_true'] for x in rows])
    return (e - np.median(e)) * 1000 if len(e) else e


def summarize(group):
    out = {}
    rs = [data[p] for p in group]
    for m in ['guided', 'kong', 'hybrid']:
        nm, nmo, nms, ne, nr = (sum(x[m][k] for x in rs) for k in ['n_match', 'n_match_offset', 'n_match_sound', 'n_est', 'n_ref'])
        out[m] = dict(ev.prf(nm, ne, nr), **{'offset_' + k: v for k, v in ev.prf(nmo, ne, nr).items()},
                      **{'sound_offset_' + k: v for k, v in ev.prf(nms, ne, nr).items()},
                      macro_f1=float(np.mean([x[m]['f1'] for x in rs])),
                      onset_err_median_ms=float(np.median([x[m].get('onset_err_median_ms', np.nan) for x in rs])))
    for m, key in [('guided', 't_est'), ('hybrid', 't_hyb')]:
        out[m]['score_onset'] = ev.onset_error_rates(np.concatenate([onset_errors(x['rows'], key) for x in rs]))
        y = np.concatenate([x['vel']['true'] for x in rs if 'vel' in x])
        v = np.concatenate([x['vel'][m] for x in rs if 'vel' in x])
        out[m]['score_velocity'] = ev.velocity_scores(v, y)
    y = np.concatenate([x['vel']['true'] for x in rs if 'vel' in x])
    out['hybrid']['score_velocity_raw'] = ev.velocity_scores(np.concatenate([x['vel']['hybrid_raw'] for x in rs if 'vel' in x]), y)
    out['hybrid']['assigned_ratio'] = float(np.mean([np.mean([not np.isnan(r['v_kong']) for r in x['rows']]) for x in rs if x['rows']]))
    kve = np.concatenate([x['kong_vel']['est'] for x in rs])
    kvt = np.concatenate([x['kong_vel']['true'] for x in rs])
    out['kong']['velocity_raw'] = ev.velocity_scores(kve, kvt)
    out['kong']['velocity_cal_mae_mean'] = float(np.mean([x['kong_vel_cal']['cal']['mae'] for x in rs]))
    out['kong']['velocity_cal_r_mean'] = float(np.mean([x['kong_vel_cal']['cal']['r'] for x in rs]))
    t, pr, h = (sum(x['missing'][k] for x in rs) for k in ['true', 'pred', 'hit'])
    out['hybrid']['missing_detection'] = dict(ev.prf(h, pr, t), true=t, pred=pr)
    out['kong']['pedal_f1_mean'] = float(np.nanmean([x['pedal']['f1'] for x in rs if x['pedal']['ref_down_ratio'] > 0]))
    return out


groups = {'DEV(guidedの調整に使った8演奏)': [p for p in perfs if p in DEV],
          'TEST(それ以外)': [p for p in perfs if p not in DEV], 'ALL': perfs}
report = {}
for g, ps in groups.items():
    if not ps:
        continue
    s = report[g] = summarize(ps)
    print(f'\n== {g}: {len(ps)}演奏 ==')
    print('音符単位(同時記録MIDIの全音、発音±50ms)  適合率  再現率   F1   発音+離鍵F1(離鍵 / ペダルで伸ばした音の終わり)  発音誤差の中央値')
    for m in ['guided', 'kong', 'hybrid']:
        x = s[m]
        print(f"  {m:7s}                               {x['precision']:.3f}   {x['recall']:.3f}  {x['f1']:.3f}"
              f"     {x['offset_f1']:.3f} / {x['sound_offset_f1']:.3f}                          {x['onset_err_median_ms']:.1f}ms")
    print('楽譜の音符単位(正しく弾かれた非装飾音)  発音誤差 中央値 / 90%点 / ≤10ms / ≤25ms   ベロシティ 相関 / 平均絶対誤差')
    for m in ['guided', 'hybrid']:
        o, v = s[m]['score_onset'], s[m]['score_velocity']
        print(f"  {m:7s}  {o['median_ms']:5.1f}ms / {o['p90_ms']:6.1f}ms / {o['within10']:.0%} / {o['within25']:.0%}"
              f"      {v['r']:.2f} / {v['mae']:.1f}")
    hv = s['hybrid']['score_velocity_raw']
    print(f"  hybrid の下書きが割り当てられた割合 {s['hybrid']['assigned_ratio']:.1%}、"
          f"補正前ベロシティ 相関 {hv['r']:.2f} / 平均絶対誤差 {hv['mae']:.1f}")
    md = s['hybrid']['missing_detection']
    print(f"抜けた音の検出(hybrid): 正解 {md['true']}音, 検出 {md['pred']}音, 適合率 {md['precision']:.2f}, 再現率 {md['recall']:.2f}")
    kv = s['kong']['velocity_raw']
    print(f"kong ベロシティ(補正なし): 相関 {kv['r']:.2f} / 平均絶対誤差 {kv['mae']:.1f}   "
          f"線形補正後(演奏ごとの平均): 相関 {s['kong']['velocity_cal_r_mean']:.2f} / 平均絶対誤差 {s['kong']['velocity_cal_mae_mean']:.1f}")
    print(f"kong ペダル(10msごとの一致、ペダルのある演奏の平均) F1 {s['kong']['pedal_f1_mean']:.2f}")

# 演奏ごとの表
with open(os.path.join(a.out, 'per_performance.csv'), 'w', newline='') as fo:
    w = csv.writer(fo)
    w.writerow(['performance', 'dev', 'duration', 'guided_f1', 'kong_f1', 'hybrid_f1', 'kong_sound_offset_f1',
                'guided_score_onset_median_ms', 'hybrid_score_onset_median_ms', 'guided_vel_r', 'hybrid_vel_r',
                'kong_pedal_f1', 'missing_true', 'missing_pred', 'missing_hit'])
    for p, r in data.items():
        go, ho = onset_errors(r['rows'], 't_est'), onset_errors(r['rows'], 't_hyb')
        vr = lambda k: np.corrcoef(r['vel'][k], r['vel']['true'])[0, 1] if 'vel' in r and len(r['rows']) > 2 else np.nan
        w.writerow([p, int(r['dev']), f"{r['duration']:.1f}", f"{r['guided']['f1']:.4f}", f"{r['kong']['f1']:.4f}",
                    f"{r['hybrid']['f1']:.4f}", f"{r['kong']['sound_offset_f1']:.4f}",
                    f"{np.median(np.abs(go)):.2f}", f"{np.median(np.abs(ho)):.2f}", f"{vr('guided'):.3f}",
                    f"{vr('hybrid'):.3f}", f"{r['pedal']['f1']:.3f}", r['missing']['true'], r['missing']['pred'],
                    r['missing']['hit']])

allrows = [x for r in data.values() for x in r['rows']]
guided.save_model(os.path.join(a.out, 'guided', 'velocity_model.json'),
                  guided.fit_velocity(allrows, [x['vel_true'] for x in allrows]))
json.dump(report, open(os.path.join(a.out, 'report.json'), 'w'), indent=1, ensure_ascii=False, default=float)
print('\n->', a.out)
