"""音符1つを1行とする表

楽譜の音符ごとに、対応する演奏音の 発音時刻・ベロシティ・局所テンポ・タイミングのずれ・音の長さの比 などを求める。
"""
import csv

import numpy as np

from music_io import pedb
from score_analysis import melody

from . import align

COLS = ['performance', 'note_id', 'bar', 'beat', 'staff', 'voice', 'pitch', 'pitch_name', 'dur_beats', 'ornament',
        'melody', 'status', 'n_perf_notes', 'onset', 'offset', 'velocity', 'velocity_z', 'local_tempo_bpm',
        'timing_dev_ms', 'event_dev_ms', 'chord_async_ms', 'articulation']


def local_fit(x, y, x0, half):
    """x0 の周り ±half の範囲で三重立方重み付きの局所線形回帰。(予測値, 傾き) を返す"""
    d = np.abs(x - x0)
    h = half
    while (d < h).sum() < 3 and h < 1e4:
        h *= 1.5
    w = np.clip(1 - (d / h) ** 3, 0, None) ** 3
    sw = w.sum()
    mx, my = (w * x).sum() / sw, (w * y).sum() / sw
    vx = (w * (x - mx) ** 2).sum()
    b = (w * (x - mx) * (y - my)).sum() / vx if vx > 0 else 0.0
    return my + b * (x0 - mx), b


def analyze(root, perf, half_beats=4.0):
    """PEDB の1演奏について、matchファイル(人手で修正済みの対応付け)から表を作る"""
    f = pedb.perf_files(root, perf)
    tpqn, score, dups = pedb.load_fmt3x(f['fmt3x'])
    match, missing = pedb.load_match(f['match'])
    return analyze_match(perf, tpqn, score, dups, match, missing, half_beats)


def analyze_midi(perf, fmt3x_path, notes, half_beats=4.0, params=None):
    """演奏の音符の配列 [発音秒, 離鍵秒, 音高, ベロシティ](MIDI や採譜の結果)を楽譜と対応付けてから表を作る。
    戻り値: (行のリスト, 要約, 対応付けの結果(演奏音のリスト, 弾かれなかった楽譜音ID))"""
    tpqn, gscore, dups = pedb.load_fmt3x(fmt3x_path, grace=True)
    match, missing = align.align(tpqn, gscore, dups, notes, params)
    _, score, _ = pedb.load_fmt3x(fmt3x_path)
    rows, summ = analyze_match(perf, tpqn, score, dups, match, missing, half_beats)
    return rows, summ, (match, missing)


def analyze_match(perf, tpqn, score, dups, match, missing, half_beats=4.0):
    """1演奏分の表(行のリスト)と要約を返す。match, missing は load_match の形。
    half_beats: テンポを平滑化する窓の半幅(四分音符)"""

    # 楽譜音符ごとに演奏音を集める(装飾音は複数の演奏音が1つの楽譜音に対応)
    by_id = {}
    for p in match:
        if p['note_id'] != '*':
            by_id.setdefault(p['note_id'], []).append(p)
    shared = set()
    for group in dups:  # 共有音は、弾かれた方の演奏音を他方にも割り当てる
        played = [i for i in group if i in by_id]
        for i in group:
            if i not in by_id and played:
                by_id[i] = by_id[played[0]]
                shared.add(i)

    # 拍位置ごとの「出来事の時刻」= 正しく弾かれた非装飾音の発音時刻の中央値
    ev = {}
    for ps in by_id.values():
        for p in ps:
            if p['status'] == 0 and p['err'] == 0:
                ev.setdefault(p['stime'], []).append(p['on'])
    st = np.array(sorted(ev))
    et = np.array([np.median(ev[s]) for s in st])
    beats = st / tpqn  # 四分音符単位
    pred, slope = {}, {}
    for s, b in zip(st, beats):
        pred[s], slope[s] = local_fit(beats, et, b, half_beats)
    ev_time = dict(zip(st, et))

    vel_all = np.array([p['vel'] for p in match if p['note_id'] != '*'], float)
    vmu, vsd = vel_all.mean(), vel_all.std() or 1.0

    mel = melody.top_voice(score)

    rows = []
    for nid, n in sorted(score.items(), key=lambda kv: (kv[1]['stime'], kv[1]['pitch'] or 0)):
        ps = by_id.get(nid, [])
        s = n['stime']
        if not ps:
            status = 'missing'
        elif any(p['err'] == 1 for p in ps):
            status = 'pitch_error'
        else:
            status = 'shared' if nid in shared else 'ok'
        r = dict(performance=perf, note_id=nid, bar=n['bar'], beat=s / tpqn, staff=n['staff'],
                 voice=n['voice'], pitch=n['pitch'], pitch_name=n['pitch_name'],
                 dur_beats=n['dur_ticks'] / tpqn, ornament=n['ornament'],
                 melody=int(nid in mel),
                 status=status, n_perf_notes=len(ps))
        if ps:
            main = [p for p in ps if p['status'] == 0] or ps
            on = min(p['on'] for p in ps)  # 装飾音は最初の音の時刻
            off = max(p['off'] for p in ps)
            vel = max(p['vel'] for p in main)
            r.update(onset=on, offset=off, velocity=vel, velocity_z=(vel - vmu) / vsd)
            if s in pred:
                spb = slope[s]
                r.update(local_tempo_bpm=60 / spb if spb > 0 else None,
                         timing_dev_ms=(on - pred[s]) * 1000,
                         event_dev_ms=(ev_time[s] - pred[s]) * 1000,
                         chord_async_ms=(on - ev_time[s]) * 1000,
                         articulation=(off - on) / (n['dur_ticks'] / tpqn * spb)
                         if spb > 0 and n['dur_ticks'] > 0 else None)
        rows.append(r)
    summary = dict(performance=perf, n_score_notes=len(score), n_perf_notes=len(match),
                   extra=sum(1 for p in match if p['note_id'] == '*'), missing=len(missing),
                   pitch_errors=sum(1 for p in match if p['err'] == 1),
                   mean_tempo_bpm=60 / np.median([slope[s] for s in st if slope[s] > 0]),
                   vel_mean=vmu, vel_sd=vsd)
    return rows, summary


def fmt(v):
    if v is None:
        return ''
    if isinstance(v, (float, np.floating)):
        return f'{v:.4f}'
    return v


def write_rows(path, rows, header=True, mode='w'):
    with open(path, mode, newline='') as f:
        w = csv.writer(f)
        if header:
            w.writerow(COLS)
        for r in rows:
            w.writerow([fmt(r.get(c)) for c in COLS])
