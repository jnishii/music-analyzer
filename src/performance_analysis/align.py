"""演奏MIDIと楽譜の対応付け

入力: 楽譜(music_io.pedb.load_fmt3x(..., grace=True) の形)と、演奏の音符の配列 [発音秒, 離鍵秒, 音高, ベロシティ]
      (同時記録MIDI、キーボードで録ったMIDI、採譜モデルの出力のどれでもよい)
出力: matchファイル(music_io.pedb.load_match)と同じ形の演奏音のリストと、弾かれなかった楽譜音IDのリスト。
      そのまま note_table.analyze_match に渡せる。

手順
 1. 楽譜の「同時に鳴る音のまとまり」の並びと、演奏音の「ほぼ同時に弾かれたかたまり」の並びを、
    音の集合の食い違いを費用として DTW にかける(大まかな対応)。1つのまとまりが複数のかたまりを吸収すると罰を加えるので、
    同じ和音の繰り返しでも1対1の対応が選ばれる
 2. 対応した音から「楽譜の拍 -> 演奏の時刻」の写像を作る(外れ値を除き、折れ線で補間)
 3. 音高ごとに、楽譜音と演奏音を、写像による予測時刻との差が小さくなるよう順序を保って1対1に対応させる
    (動的計画法。どちらにも「抜け」を許す)。対応した音で写像を作り直し、窓を狭めてもう一度行う
 4. 抜けにした楽譜音を、同じ音高の残りの演奏音から広い窓で探し直す
 5. 残った演奏音を、装飾(トリル・ターンなど)の音、弾き間違い(近い音高)、余分な音 に分ける
"""
import bisect

import numpy as np

from music_io.pedb import midi_pitch, pitch_name

# 既定値は DEV の曲(scripts/performance/eval_alignment.py の DEV_PIECES)だけで決めた
PARAMS = dict(
    cluster_gap=0.035,    # 発音の間隔がこれ以内の演奏音を1つのかたまり(ほぼ同時に弾かれた音)とする
    dtw_hpen=0.3,         # DTW で、1つのまとまりが複数のかたまりを吸収するときの罰
    dtw_vpen=0.5,         # DTW で、1つのかたまりを複数のまとまりに対応させるときの罰
    passes=((0.2, 1.0), (0.05, 0.3)),    # 各回の (時刻差の尺度 秒, 対応を許す最大の時刻差 秒)
    skip=3.0,             # 音高ごとの対応付けで、楽譜音または演奏音を飛ばす費用(時刻差の尺度で測る)
    arp_tol=0.25,         # アルペジオ記号のある音は、時刻差をこの秒数まで割り引く
    grace_gap=0.07,       # 装飾音符の予測時刻: 主音の前(後打音も同様)にこの間隔で並べる
    orn_before=0.15,      # 装飾の音を探す範囲: 主音の予測時刻の前
    rescue=1.0,           # 抜けにした楽譜音を、同じ音高の残りの演奏音から探し直す窓(秒)
    perr_tol=0.08,        # 弾き間違いとみなす時刻差
    perr_semitones=2,     # 弾き間違いとみなす音高差
)


def _targets(score, dups):
    """対応付けの対象となる楽譜音(共有音の組は先頭の1つで代表させる)と、代表 -> 組 の辞書"""
    rep = {}
    for g in dups:
        g = [i for i in g if i in score]
        for i in g:
            rep[i] = g[0]
    T = []
    for i, n in score.items():
        if n['pitch'] is None or rep.get(i, i) != i:
            continue
        orn = {midi_pitch(x) for x in n['ornament_notes'].split(',')[1:]} - {None}
        T.append(dict(id=i, stime=n['stime'], pitch=n['pitch'], kind=n.get('kind', 'chord'), order=n.get('order', 0),
                      dur=n['dur_ticks'], orn=orn, arp='Arp' in n['ornament']))
    groups = {}
    for i, r in rep.items():
        groups.setdefault(r, []).append(i)
    return T, groups


def _events(T):
    """同時に鳴る音のまとまり。装飾音符は主音の前に、演奏順に別のまとまりとして並べる"""
    ev = {}
    for k, t in enumerate(T):
        key = (t['stime'], 1, 0) if t['kind'] == 'chord' else (t['stime'], 0, t['order'])
        ev.setdefault(key, []).append(k)
    keys = sorted(ev)
    return keys, [ev[k] for k in keys]


def _dtw(cost, hpen, vpen):
    """行 = 楽譜のまとまり、列 = 演奏音のかたまり。横の移動(1つのまとまりが続くかたまりを吸収)は hpen、
    縦の移動(1つのかたまりが続くまとまりにも対応)は vpen。行ごとに累積最小でベクトル化している"""
    n, m = cost.shape
    INF = np.float32(1e9)
    D = np.empty((n, m), np.float32)
    D[0] = np.cumsum(cost[0] + hpen) - hpen
    for i in range(1, n):
        diag = np.full(m, INF, np.float32)
        diag[1:] = D[i - 1, :-1]
        base = np.minimum(diag, D[i - 1] + vpen) + cost[i]
        C = np.cumsum(cost[i] + hpen)
        D[i] = C + np.minimum.accumulate(base - C)
    i, j = n - 1, m - 1
    path = [(i, j)]
    while i > 0 or j > 0:
        cands = []
        if i > 0 and j > 0:
            cands.append((D[i - 1, j - 1], i - 1, j - 1))
        if j > 0:
            cands.append((D[i, j - 1] + hpen, i, j - 1))
        if i > 0:
            cands.append((D[i - 1, j] + vpen, i - 1, j))
        _, i, j = min(cands)
        path.append((i, j))
    return path[::-1]


def _clean_anchors(b, t, line=True):
    """写像の点(拍, 時刻)から外れ値を除く: 時刻が戻らない最長の並び を残し、
    line=True なら局所的な直線からの大きな外れも除く(DTW から作る最初の写像だけ。
    休符の後などテンポが急に変わる箇所の正しい点も除いてしまうことがあるため)"""
    o = np.argsort(b, kind='stable')
    b, t = np.asarray(b, float)[o], np.asarray(t, float)[o]
    # 最長非減少部分列(時刻が戻らない最大の点の集合)
    n = len(t)
    if n < 3:
        return b, t
    tails, idx, prev = [], [], np.full(n, -1)
    for k in range(n):
        p = bisect.bisect_right(tails, t[k])
        if p == len(tails):
            tails.append(t[k])
            idx.append(k)
        else:
            tails[p] = t[k]
            idx[p] = k
        prev[k] = idx[p - 1] if p > 0 else -1
    keep, k = [], idx[-1]
    while k >= 0:
        keep.append(k)
        k = prev[k]
    keep = np.array(keep[::-1])
    b, t = b[keep], t[keep]
    # 前後の点を結ぶ直線から大きく外れる点を除く
    if line and len(b) >= 5:
        res = np.zeros(len(b))
        for k in range(1, len(b) - 1):
            lo, hi = max(0, k - 3), min(len(b), k + 4)
            nb = np.r_[b[lo:k], b[k + 1:hi]]
            nt = np.r_[t[lo:k], t[k + 1:hi]]
            if np.ptp(nb) > 0:
                c = np.polyfit(nb, nt, 1)
                res[k] = t[k] - np.polyval(c, b[k])
        mad = np.median(np.abs(res)) + 1e-3
        ok = np.abs(res) <= max(0.15, 6 * mad)
        b, t = b[ok], t[ok]
    # 同じ拍の点は平均にまとめる
    ub = np.unique(b)
    ut = np.array([t[b == x].mean() for x in ub])
    return ub, ut


def _pitch_dp(ts, tp, scale, maxd, skip, tol):
    """1つの音高について、楽譜音の予測時刻 ts(昇順)と演奏音の時刻 tp(昇順)を、順序を保って対応させる。
    費用 = 時刻差/scale(maxd を超える組は対応させない)、どちらかを飛ばすと skip。
    戻り値: [(楽譜側の番号, 演奏側の番号), ...]"""
    n, m = len(ts), len(tp)
    if not n or not m:
        return []
    d = np.abs(np.asarray(ts)[:, None] - np.asarray(tp)[None, :])
    d = np.maximum(d - np.asarray(tol)[:, None], 0)
    c = np.where(d <= maxd, d / scale, np.inf)
    idx = skip * np.arange(m + 1)
    D = np.empty((n + 1, m + 1))
    D[0] = idx
    for i in range(1, n + 1):
        base = np.empty(m + 1)
        base[0] = D[i - 1, 0] + skip
        base[1:] = np.minimum(D[i - 1, :-1] + c[i - 1], D[i - 1, 1:] + skip)
        D[i] = idx + np.minimum.accumulate(base - idx)
    out, i, j = [], n, m
    while i > 0 and j > 0:
        if np.isfinite(c[i - 1, j - 1]) and np.isclose(D[i, j], D[i - 1, j - 1] + c[i - 1, j - 1]):
            out.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif np.isclose(D[i, j], D[i - 1, j] + skip):
            i -= 1
        else:
            j -= 1
    return out[::-1]


def align(tpqn, score, dups, perf, params=None):
    """楽譜と演奏音を対応付ける。perf: [発音秒, 離鍵秒, 音高, ベロシティ] の配列
    戻り値: (演奏音のリスト(load_match と同じ形), 弾かれなかった楽譜音IDのリスト)"""
    P = dict(PARAMS, **(params or {}))
    perf = np.asarray(perf, float).reshape(-1, 4)
    order = np.lexsort((perf[:, 2], perf[:, 0]))
    perf = perf[order]
    T, groups = _targets(score, dups)
    keys, members = _events(T)
    beat = np.array([t['stime'] / tpqn for t in T])

    # 1. まとまりの並びと、演奏音のかたまりの並びの DTW(音の集合の食い違いだけを見る)
    pp = perf[:, 2].astype(int)
    cl = np.zeros(len(perf), int)
    start = perf[0, 0] if len(perf) else 0
    for j in range(1, len(perf)):
        if perf[j, 0] - start > P['cluster_gap']:
            cl[j] = cl[j - 1] + 1
            start = perf[j, 0]
        else:
            cl[j] = cl[j - 1]
    nc = cl[-1] + 1 if len(perf) else 0
    Cm = np.zeros((nc, 128))
    np.add.at(Cm, (cl, pp), 1)
    Em = np.zeros((len(keys), 128))   # まとまりの音高(主音 1、装飾の音 0.7、同じ音名の別オクターブ 0.4)
    for e, mem in enumerate(members):
        for k in mem:
            for q in range(T[k]['pitch'] % 12, 128, 12):
                Em[e, q] = max(Em[e, q], 0.4)
        for k in mem:
            for q in T[k]['orn']:
                Em[e, q] = max(Em[e, q], 0.7)
        for k in mem:
            Em[e, T[k]['pitch']] = 1.0
    Emain = (Em == 1.0).astype(float)
    # かたまりの音のうちまとまりにない割合 と、まとまりの音高のうちかたまりにない割合
    c_in = (Em @ Cm.T) / Cm.sum(1)[None, :]
    e_in = (Emain @ (Cm > 0).T) / Emain.sum(1)[:, None]
    cost = (0.7 * (1 - c_in) + 0.3 * (1 - e_in)).astype(np.float32)
    path = _dtw(cost, P['dtw_hpen'], P['dtw_vpen'])
    ab, at = [], []
    for e in range(len(keys)):
        if keys[e][1] != 1:
            continue
        cs = [c for ee, c in path if ee == e]
        ts = [perf[j, 0] for j in np.where(np.isin(cl, cs))[0] if Emain[e, pp[j]]]
        if ts:
            ab.append(keys[e][0] / tpqn)
            at.append(float(np.median(ts)))
    mb, mt = _clean_anchors(ab, at)

    # 2-3. 写像による予測時刻で、音高ごとに対応付ける(2回)
    n_grace = {}
    for t in T:
        if t['kind'] != 'chord':
            n_grace.setdefault((t['stime'], t['kind']), []).append(t['order'])
    assign = {}
    for scale, maxd in P['passes']:
        pred = np.interp(beat, mb, mt)
        # 装飾音符は主音の前に並べる
        for k, t in enumerate(T):
            if t['kind'] != 'chord':
                orders = sorted(n_grace[(t['stime'], t['kind'])])
                pos = len(orders) - orders.index(t['order'])
                pred[k] -= P['grace_gap'] * pos
        tol = np.array([P['arp_tol'] if t['arp'] else 0.0 for t in T])
        assign = {}
        for p in np.unique([t['pitch'] for t in T]):
            ks = [k for k in np.argsort(pred, kind='stable') if T[k]['pitch'] == p]
            js = np.where(pp == p)[0]
            for a, b in _pitch_dp(pred[ks], perf[js, 0], scale, maxd, P['skip'], tol[ks]):
                assign[ks[a]] = js[b]
        # 対応した通常の音で写像を作り直す
        ev_t = {}
        for k, j in assign.items():
            if T[k]['kind'] == 'chord':
                ev_t.setdefault(beat[k], []).append(perf[j, 0])
        mb, mt = _clean_anchors(list(ev_t), [float(np.median(v)) for v in ev_t.values()], line=False)
    pred = np.interp(beat, mb, mt)

    # 4. 抜けにした楽譜音を、同じ音高の残りの演奏音から広い窓で探し直す(低音だけ先に弾く、大きなアルペジオなど)。
    #    同じ音高ですでに対応した音との前後関係は崩さない
    used = set(assign.values())
    for p in np.unique([t['pitch'] for t in T]):
        ks = sorted([k for k in range(len(T)) if T[k]['pitch'] == p], key=lambda k: (beat[k], pred[k]))
        for x, k in enumerate(ks):
            if k in assign:
                continue
            lo = max([perf[assign[q], 0] for q in ks[:x] if q in assign], default=-np.inf)
            hi = min([perf[assign[q], 0] for q in ks[x + 1:] if q in assign], default=np.inf)
            c = [j for j in np.where((pp == p) & (perf[:, 0] > lo) & (perf[:, 0] < hi)
                                     & (np.abs(perf[:, 0] - pred[k]) <= P['rescue']))[0] if j not in used]
            if c:
                j = min(c, key=lambda j: abs(perf[j, 0] - pred[k]))
                assign[k] = j
                used.add(j)

    # 5. 残った演奏音の分類
    status = {}   # 演奏音の番号 -> (楽譜音の番号, 対応状態, 誤り)
    for k, j in assign.items():
        status[j] = (k, 0, 0)
    end_beat = lambda k: beat[k] + max(T[k]['dur'], 1) / tpqn
    # 装飾の音: 装飾のある音の、予測時刻の少し前から音価の終わりまでにある、装飾の音高(または主音)の音
    for k, t in enumerate(T):
        if not t['orn']:
            continue
        lo, hi = pred[k] - P['orn_before'], float(np.interp(end_beat(k), mb, mt))
        for j in np.where((perf[:, 0] >= lo) & (perf[:, 0] <= hi) & np.isin(pp, list(t['orn'] | {t['pitch']})))[0]:
            if j not in used:
                used.add(j)
                status[j] = (k, 1, 0)
    # 弾き間違い: 弾かれなかった楽譜音の近くにある、音高の近い残りの音
    missing = [k for k in range(len(T)) if k not in assign]
    for k in sorted(missing, key=lambda k: pred[k]):
        c = [j for j in np.where(np.abs(perf[:, 0] - pred[k]) <= P['perr_tol'])[0]
             if j not in used and 0 < abs(pp[j] - T[k]['pitch']) <= P['perr_semitones']]
        if c:
            j = min(c, key=lambda j: abs(perf[j, 0] - pred[k]))
            used.add(j)
            status[j] = (k, 0, 1)
            assign[k] = j

    rows = []
    for j in range(len(perf)):
        on, off, p, v = perf[j]
        if j in status:
            k, st, err = status[j]
            nid, stime = T[k]['id'], T[k]['stime']
        else:
            nid, st, err, stime = '*', -1, 3, int(round(float(np.interp(on, mt, mb)) * tpqn)) if len(mb) > 1 else 0
        rows.append(dict(id=j, on=float(on), off=float(off), pitch_name=pitch_name(p), vel=int(round(v)), offvel=0,
                         status=st, stime=int(stime), note_id=nid, err=err))
    miss_ids = []
    for k in range(len(T)):
        if k not in assign:
            miss_ids += groups.get(T[k]['id'], [T[k]['id']])
    return rows, miss_ids
