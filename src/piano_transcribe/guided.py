"""録音 + 楽譜 -> 各音の発音時刻とベロシティの推定(楽譜情報つき、学習済みモデルなし)

楽譜は {音符ID: dict(stime, dur_ticks, pitch)} と TPQN(四分音符あたりの tick 数)で受け取る
(music_io.pedb.load_fmt3x の形)。楽譜の分析コードには依存しない。

手順
 1. 楽譜(fmt3x)の音符を仮のテンポで並べたクロマ列と、録音のクロマ列を DTW で大まかに対応付ける
 2. 各音の予測時刻の前後 ±150ms で、その音高の倍音成分の立ち上がり(スペクトルフラックス)の山を探す
 3. 立ち上がり直後の倍音エネルギーを特徴量として、ベロシティを線形回帰で推定する
    (回帰の係数は scripts/transcribe/eval_transcription.py が同時記録MIDIから学習して保存する)

2026-11 の8演奏での評価: 発音時刻の誤差の中央値 約10ms(テンポの安定した曲では 4〜8ms)、
ベロシティの相関 約0.65。ルバートの大きい曲では DTW が部分的に外れる。
"""
import json

import numpy as np

SR = 44100
N = 4096       # 窓長(約93ms)
HOP = 220      # 約5ms
FR = HOP / SR
DTW_DOWN = 8   # DTW は 40ms 単位


def pitch_energy(x, sr=SR):
    """各フレーム・各MIDI音高(21..108)の倍音エネルギー(線形)"""
    nf = (len(x) - N) // HOP
    win = np.hanning(N).astype(np.float32)
    freqs = np.fft.rfftfreq(N, 1 / sr)
    W = np.zeros((88, len(freqs)), np.float32)
    for k, p in enumerate(range(21, 109)):
        f0 = 440 * 2 ** ((p - 69) / 12)
        for h in range(1, 7):
            f = f0 * h * np.sqrt(1 + 0.0004 * h * h)  # 弦の非調和性をおおまかに
            if f > sr / 2 - 200:
                break
            bw = max(f * 0.015, freqs[1] * 0.8)
            W[k] += np.exp(-0.5 * ((freqs - f) / bw) ** 2) / h
    E = np.empty((nf, 88), np.float32)
    for s in range(0, nf, 1500):
        idx = np.arange(s, min(nf, s + 1500))[:, None] * HOP + np.arange(N)
        S = np.abs(np.fft.rfft(x[idx] * win, axis=1)) ** 2
        E[s:s + len(idx)] = S @ W.T
    return E


def chroma(M):
    C = np.zeros((M.shape[0], 12), np.float32)
    for k in range(88):
        C[:, (k + 21) % 12] += M[:, k]
    return C / (np.linalg.norm(C, axis=1, keepdims=True) + 1e-9)


def dtw(cost, pen=0.3):
    """DTW(斜め・縦・横)。縦横の移動には pen を加える。録音側は途中から始まってよい。
    行ごとに累積最小でベクトル化している"""
    n, m = cost.shape
    INF = np.float32(1e9)
    D = np.empty((n, m), np.float32)
    D[0] = cost[0]
    for i in range(1, n):
        diag = np.full(m, INF, np.float32)
        diag[1:] = D[i - 1, :-1]
        base = np.minimum(diag, D[i - 1] + pen) + cost[i]
        Cc = np.cumsum(cost[i] + pen)
        D[i] = Cc + np.minimum.accumulate(base - Cc)
    j = int(np.argmin(D[-1, int(m * 0.8):])) + int(m * 0.8)
    i = n - 1
    path = [(i, j)]
    while i > 0:
        cands = []
        if j > 0:
            cands.append((D[i - 1, j - 1], i - 1, j - 1))
            cands.append((D[i, j - 1] + pen, i, j - 1))
        cands.append((D[i - 1, j] + pen, i - 1, j))
        _, i, j = min(cands)
        path.append((i, j))
    return np.array(path[::-1])


def estimate(x, sr, tpqn, score):
    """楽譜の音符ごとに推定値を返す: {音符ID: dict(t_dtw, t_est, f_peak, f_rise, pitch, dur_sec)}
    x: モノラルの録音, sr: 標本化周波数, tpqn と score は楽譜(上の説明)"""
    if sr != SR:
        raise ValueError(f'標本化周波数 {sr} には未対応(44100Hz のみ)')
    E = pitch_energy(x)
    L = np.log1p(E / (np.percentile(E, 99) * 1e-3))
    k = DTW_DOWN

    # 1. DTW
    A = L[: len(L) // k * k].reshape(-1, k, 88).mean(1)
    Aon = np.maximum(np.diff(A, axis=0, prepend=A[:1]), 0)
    s0 = min(n['stime'] for n in score.values() if n['pitch'] is not None)
    st = {i: n['stime'] - s0 for i, n in score.items()}  # 先頭の休符を詰める
    beats_total = max(st[i] + n['dur_ticks'] for i, n in score.items()) / tpqn
    act = np.where(A.sum(1) > np.percentile(A.sum(1), 10))[0]
    spb = (act[-1] - act[0]) * FR * k / beats_total  # 仮のテンポ(秒/拍)
    to_frame = lambda ticks: ticks / tpqn * spb / (FR * k)
    nS = int(to_frame(beats_total * tpqn)) + 1
    R = np.zeros((nS, 88), np.float32)
    Ron = np.zeros((nS, 88), np.float32)
    for i, n in score.items():
        if n['pitch'] is None or not 21 <= n['pitch'] <= 108:
            continue
        a = int(to_frame(st[i]))
        b = max(a + 1, int(to_frame(st[i] + n['dur_ticks'])))
        R[a:b, n['pitch'] - 21] += np.exp(-np.arange(b - a) * FR * k)
        Ron[a, n['pitch'] - 21] += 1
    fa = np.hstack([chroma(A), 0.7 * chroma(Aon)])
    fs = np.hstack([chroma(R), 0.7 * chroma(Ron)])
    fa /= np.linalg.norm(fa, axis=1, keepdims=True) + 1e-9
    fs /= np.linalg.norm(fs, axis=1, keepdims=True) + 1e-9
    path = dtw(1 - fs @ fa.T)
    sf = np.arange(nS)
    t_of = np.full(nS, np.nan)
    for a in np.unique(path[:, 0]):
        t_of[a] = path[path[:, 0] == a, 1].mean()
    ok = ~np.isnan(t_of)
    t_of = np.interp(sf, sf[ok], t_of[ok]) * FR * k
    sec_at = lambda ticks: np.interp(to_frame(ticks), sf, t_of)

    # 2-3. 発音時刻の微調整と強さの特徴量
    flux = np.vstack([np.zeros((2, 88)), np.maximum(L[2:] - L[:-2], 0)])
    out = {}
    for i, n in score.items():
        p = n['pitch']
        if p is None or not 21 <= p <= 108:
            continue
        t0 = float(sec_at(st[i]))
        a, b = max(2, int((t0 - 0.15) / FR)), min(len(L) - 2, int((t0 + 0.15) / FR))
        if b <= a:
            continue
        seg = flux[a:b, p - 21] * np.exp(-0.5 * ((np.arange(a, b) * FR - t0) / 0.08) ** 2)
        j = a + int(np.argmax(seg))
        y0, y1, y2 = flux[j - 1, p - 21], flux[j, p - 21], flux[j + 1, p - 21]
        off = 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2) if (y0 - 2 * y1 + y2) < 0 else 0
        t_est = (j + np.clip(off, -0.5, 0.5)) * FR + N / 2 / SR  # フレーム中心
        jj = int(t_est / FR)
        peak = L[jj:jj + 16, p - 21].max()  # 立ち上がり後 80ms の最大
        base = L[max(0, jj - 10):max(1, jj - 2), p - 21].min()
        out[i] = dict(pitch=p, t_dtw=t0, t_est=float(t_est), f_peak=float(peak), f_rise=float(peak - base),
                      dur_sec=float(sec_at(st[i] + n['dur_ticks']) - t0))
    return out


# --- ベロシティの回帰
def velocity_features(rows):
    p = np.array([r['pitch'] for r in rows], float)
    pk = np.array([r['f_peak'] for r in rows])
    rs = np.array([r['f_rise'] for r in rows])
    z = (p - 64) / 20
    return np.column_stack([np.ones_like(p), pk, rs, z, z * z, pk * z])


def fit_velocity(rows, vel):
    return np.linalg.lstsq(velocity_features(rows), np.asarray(vel, float), rcond=None)[0]


def predict_velocity(rows, beta):
    return np.clip(velocity_features(rows) @ np.asarray(beta), 1, 127)


def save_model(path, beta):
    json.dump(dict(velocity_beta=list(map(float, beta))), open(path, 'w'), indent=1)


def load_model(path):
    return np.array(json.load(open(path))['velocity_beta'])


# --- 採譜モデルの下書きとの組み合わせ
def assign_draft(est, draft, window=0.15):
    """楽譜の音符ごとの推定(estimate の戻り値)に、採譜モデルの下書きの音を1対1で割り当てる

    draft: (発音秒, 離鍵秒, 音高, ベロシティ) の配列(例: kong.Transcriber の出力)
    同じ音高で t_est から ±window 秒以内の音を候補とし、近い組から順に決める。
    両者の一定のずれ(方式ごとの遅れの違い)は先に差し引く。
    戻り値: ({音符ID: 下書きの行番号}, 下書きと t_est の一定のずれ(秒))
    """
    draft = np.asarray(draft).reshape(-1, 4)
    ids = list(est)
    t = np.array([est[i]['t_est'] for i in ids])
    p = np.array([est[i]['pitch'] for i in ids])
    d = []
    for k in range(len(ids)):
        c = draft[draft[:, 2] == p[k], 0]
        if len(c):
            j = np.argmin(np.abs(c - t[k]))
            if abs(c[j] - t[k]) <= window:
                d.append(c[j] - t[k])
    shift = float(np.median(d)) if d else 0.0
    cand = []
    for k in range(len(ids)):
        for j in np.where(draft[:, 2] == p[k])[0]:
            dist = abs(draft[j, 0] - shift - t[k])
            if dist <= window:
                cand.append((dist, k, j))
    used_k, used_j, out = set(), set(), {}
    for _, k, j in sorted(cand):
        if k not in used_k and j not in used_j:
            used_k.add(k)
            used_j.add(j)
            out[ids[k]] = int(j)
    return out, shift
