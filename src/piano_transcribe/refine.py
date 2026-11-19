"""人が修正した音を、録音(採譜モデルのフレームごとの出力)に合わせて詰め直す

人は「この音高の音が、だいたいこの時刻にある」ことを指定する(追加・音高の変更・移動)。
ここでは、その音高の発音の出力(reg_onset_output)の山を指定時刻の近くで探し、
発音時刻(山の位置を前後のフレームから小数点以下まで補間)・ベロシティ(山の位置の velocity_output)・
離鍵(frame_output が下がる時刻)を決め直す。モデルのしきい値を下回る弱い音も、位置を指定すれば拾える。

出力の形: kong.Transcriber.transcribe(..., keep_output=True) の3つ目(100フレーム/秒 × 88鍵)。
"""
import numpy as np

FPS = 100
BEGIN_NOTE = 21
VELOCITY_SCALE = 128


def _peak_shift(x, n):
    """山の位置 n の小数点以下の補正(Kong らの後処理と同じ式)"""
    if n <= 0 or n >= len(x) - 1:
        return 0.0
    a, b, c = x[n - 1], x[n], x[n + 1]
    if a > c:
        den = b - c
    else:
        den = b - a
    return float(np.clip((c - a) / den / 2, -0.5, 0.5)) if den > 0 else 0.0


def find_onset(out, pitch, t, window=0.08):
    """t の前後 window 秒で、その音高の発音の出力が最も大きい(近いほど優先)位置。
    戻り値: (発音秒, 山の高さ 0〜1)。範囲外の音高なら (t, 0)"""
    k = int(pitch) - BEGIN_NOTE
    x = out['reg_onset_output']
    if not 0 <= k < x.shape[1]:
        return t, 0.0
    lo, hi = max(0, int((t - window) * FPS)), min(len(x), int((t + window) * FPS) + 2)
    if hi <= lo:
        return t, 0.0
    seg = x[lo:hi, k]
    w = np.exp(-0.5 * ((np.arange(lo, hi) / FPS - t) / window) ** 2)
    n = lo + int(np.argmax(seg * (0.5 + 0.5 * w)))
    return (n + _peak_shift(x[:, k], n)) / FPS, float(x[n, k])


def velocity_at(out, pitch, t_on):
    k = int(pitch) - BEGIN_NOTE
    v = out['velocity_output']
    n = int(round(t_on * FPS))
    if not (0 <= k < v.shape[1] and 0 <= n < len(v)):
        return None
    return float(np.clip(v[n, k] * VELOCITY_SCALE, 1, 127))


def find_offset(out, pitch, t_on, t_limit=None, frame_threshold=0.1, max_dur=10.0):
    """発音の後、その音高の frame_output が frame_threshold を下回る時刻。t_limit(同じ音高の次の発音)で打ち切る"""
    k = int(pitch) - BEGIN_NOTE
    f = out['frame_output']
    if not 0 <= k < f.shape[1]:
        return None
    n0 = int(t_on * FPS) + 2
    n1 = min(len(f), int((t_on + max_dur) * FPS))
    if t_limit is not None:
        n1 = min(n1, int(t_limit * FPS))
    if n1 <= n0:
        return t_limit if t_limit is not None else t_on + 0.05
    below = np.where(f[n0:n1, k] < frame_threshold)[0]
    n = n0 + below[0] if len(below) else n1
    return max(n / FPS, t_on + 0.03)


def refine_notes(out, notes, ids, window=0.08, weak=0.02):
    """notes: [{id, on, off, pitch, vel, ...}] のうち ids の音を詰め直す。
    fixed_off / fixed_vel が真の音は、人が決めた離鍵・ベロシティを残す。
    戻り値: {id: {on, off, vel, evidence, weak}}。evidence は発音の出力の山の高さ。
    weak=True は、その付近に発音の手がかりがほとんどない(人の指定どおりの時刻を残す)"""
    ids = set(ids)
    by_pitch = {}
    for n in notes:
        by_pitch.setdefault(int(n['pitch']), []).append(n)
    res = {}
    for n in notes:
        if n['id'] not in ids:
            continue
        p = int(n['pitch'])
        t, ev = find_onset(out, p, float(n['on']), window)
        is_weak = ev < weak
        if is_weak:
            t = float(n['on'])
        nxt = [m['on'] for m in by_pitch[p] if m is not n and m['on'] > t + 0.03]
        r = dict(on=t, evidence=ev, weak=bool(is_weak))
        if not n.get('fixed_vel'):
            v = velocity_at(out, p, t)
            r['vel'] = int(round(v)) if v is not None and not is_weak else int(n['vel'])
        if not n.get('fixed_off'):
            off = find_offset(out, p, t, min(nxt) if nxt else None)
            r['off'] = off if off is not None and not is_weak else max(float(n['off']), t + 0.05)
        res[n['id']] = r
    return res


def roll_image(out, key='frame_output'):
    """ピアノロールの背景用: フレーム × 88鍵 の 0〜255 の配列"""
    return np.clip(np.asarray(out[key]) * 255, 0, 255).astype(np.uint8)
