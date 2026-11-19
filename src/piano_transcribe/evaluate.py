"""推定した演奏(音符・ペダル)と正解の比較

音符は (発音秒, 離鍵秒, 音高, ベロシティ) の配列、ペダルは (踏んだ秒, 離した秒) の配列。
指標は採譜の評価で一般的なもの(mir_eval の音符単位の評価)に合わせている。
  - 発音のみ: 同じ音高で発音時刻の差が許容幅(既定50ms)以内なら正解
  - 発音+離鍵: さらに離鍵時刻の差が max(50ms, 正解の音長の20%) 以内
対応付けは差の小さい組から順に1対1で決める(mir_eval の最大マッチングとほぼ同じ結果になる)。
"""
import numpy as np


def match_notes(est, ref, onset_tol=0.05, offset=False):
    """1対1の対応 [(推定の番号, 正解の番号), ...]"""
    est, ref = np.asarray(est).reshape(-1, 4), np.asarray(ref).reshape(-1, 4)
    pairs = []
    for p in np.intersect1d(est[:, 2], ref[:, 2]):
        ie, ir = np.where(est[:, 2] == p)[0], np.where(ref[:, 2] == p)[0]
        d = np.abs(est[ie, 0][:, None] - ref[ir, 0][None, :])
        ok = d <= onset_tol
        if offset:
            tol = np.maximum(0.05, 0.2 * (ref[ir, 1] - ref[ir, 0]))
            ok &= np.abs(est[ie, 1][:, None] - ref[ir, 1][None, :]) <= tol[None, :]
        a, b = np.where(ok)
        used_e, used_r = set(), set()
        for k in np.argsort(d[a, b], kind='stable'):
            e, r = ie[a[k]], ir[b[k]]
            if e not in used_e and r not in used_r:
                used_e.add(e)
                used_r.add(r)
                pairs.append((e, r))
    return pairs


def prf(n_match, n_est, n_ref):
    p = n_match / n_est if n_est else 0.0
    r = n_match / n_ref if n_ref else 0.0
    return dict(precision=p, recall=r, f1=2 * p * r / (p + r) if p + r else 0.0)


def constant_offset(est, ref, window=0.15):
    """推定と正解の一定のずれ(秒)。正解の各音に最も近い同じ音高の推定音との差の中央値
    (録音とMIDIの時計のずれ、方式ごとの遅れを差し引くため)"""
    est, ref = np.asarray(est).reshape(-1, 4), np.asarray(ref).reshape(-1, 4)
    d = []
    for p in np.unique(ref[:, 2]):
        te = est[est[:, 2] == p, 0]
        if not len(te):
            continue
        for t in ref[ref[:, 2] == p, 0]:
            k = np.argmin(np.abs(te - t))
            if abs(te[k] - t) <= window:
                d.append(te[k] - t)
    return float(np.median(d)) if d else 0.0


def note_scores(est, ref, onset_tol=0.05):
    """音符単位の評価。est は一定のずれを差し引いた後のもの"""
    est, ref = np.asarray(est).reshape(-1, 4), np.asarray(ref).reshape(-1, 4)
    on = match_notes(est, ref, onset_tol)
    onoff = match_notes(est, ref, onset_tol, offset=True)
    out = dict(n_est=len(est), n_ref=len(ref), n_match=len(on), n_match_offset=len(onoff))
    out.update({k: v for k, v in prf(len(on), len(est), len(ref)).items()})
    out.update({'offset_' + k: v for k, v in prf(len(onoff), len(est), len(ref)).items()})
    if on:
        e, r = np.array(on).T
        err = np.abs(est[e, 0] - ref[r, 0]) * 1000
        out.update(onset_err_median_ms=float(np.median(err)), onset_err_mean_ms=float(err.mean()))
    return out, on


def pedal_scores(est, ref, end, hop=0.01):
    """ペダルを踏んでいるかどうかのフレーム単位(10ms)の一致"""
    t = np.arange(0, end, hop)

    def frames(spans):
        f = np.zeros(len(t), bool)
        for a, b in np.asarray(spans).reshape(-1, 2):
            f[(t >= a) & (t < b)] = True
        return f
    e, r = frames(est), frames(ref)
    return dict(prf((e & r).sum(), e.sum(), r.sum()), ref_down_ratio=float(r.mean()))


def velocity_scores(v_est, v_ref):
    v_est, v_ref = np.asarray(v_est, float), np.asarray(v_ref, float)
    return dict(r=float(np.corrcoef(v_est, v_ref)[0, 1]), mae=float(np.abs(v_est - v_ref).mean()), n=len(v_ref))


def onset_error_rates(err_ms):
    x = np.abs(np.asarray(err_ms))
    return dict(median_ms=float(np.median(x)), p90_ms=float(np.percentile(x, 90)),
                within10=float((x <= 10).mean()), within25=float((x <= 25).mean()), within50=float((x <= 50).mean()))


def extend_by_pedal(notes, pedal):
    """離鍵時にペダルを踏んでいれば、音の終わりをペダルを離した時刻まで伸ばす(同じ音高の次の発音で打ち切る)。
    MIDIの離鍵は「鍵を離した時刻」、採譜モデル(Kong ら)の離鍵は「音が止んだ時刻」なので、比べるときに使う"""
    notes = np.asarray(notes, float).reshape(-1, 4).copy()
    pedal = np.asarray(pedal).reshape(-1, 2)
    for k, (on, off, p, _) in enumerate(notes):
        i = np.where((pedal[:, 0] <= off) & (off < pedal[:, 1]))[0]
        if len(i):
            nxt = notes[(notes[:, 2] == p) & (notes[:, 0] > on), 0]
            notes[k, 1] = min(pedal[i[0], 1], nxt.min() if len(nxt) else np.inf)
    return notes
