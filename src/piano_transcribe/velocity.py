"""音響的なベロシティ推定(演奏内の自己較正)

人が追加・修正した音のベロシティを、録音の倍音エネルギーから推定する。
エネルギーとベロシティの対応は録音環境・ピアノで変わるので、同じ録音で採譜モデルが検出した音
(モデルのベロシティ)に回帰を合わせ、その対応を追加した音に当てはめる。正解の MIDI は使わない。
得られる値は「モデルのベロシティと同じ尺度」で、絶対値ではなく曲の中の相対的な強弱を表す。

特徴量は guided.py と同じ(発音直後 80ms の倍音エネルギーの最大値、その前からの立ち上がり、音高)。
"""
import numpy as np

from . import guided

FR, N, SR = guided.FR, guided.N, guided.SR


class AcousticVelocity:
    def __init__(self, x, sr):
        if sr != SR:
            raise ValueError(f'標本化周波数 {sr} には未対応(44100Hz のみ)')
        E = guided.pitch_energy(x)
        self.L = np.log1p(E / (np.percentile(E, 99) * 1e-3))
        self.beta = None

    def features(self, pitches, times):
        """各音(音高, 発音秒)の特徴量の行のリスト"""
        rows = []
        n = len(self.L)
        for p, t in zip(pitches, times):
            k = int(p) - 21
            jj = int(round((t - N / 2 / SR) / FR))   # 窓の中心が発音時刻に来るフレーム
            if not 0 <= k < 88 or jj >= n - 1:
                rows.append(None)
                continue
            jj = max(jj, 0)
            peak = float(self.L[jj:jj + 16, k].max())
            base = float(self.L[max(0, jj - 10):max(1, jj - 2), k].min())
            rows.append(dict(pitch=p, f_peak=peak, f_rise=peak - base))
        return rows

    def fit(self, pitches, times, velocities):
        """同じ録音の、モデルが検出した音とそのベロシティで対応を求める"""
        rows = self.features(pitches, times)
        ok = [i for i, r in enumerate(rows) if r is not None]
        self.beta = guided.fit_velocity([rows[i] for i in ok], np.asarray(velocities, float)[ok])
        return self

    def predict(self, pitches, times):
        rows = self.features(pitches, times)
        out = np.full(len(rows), np.nan)
        ok = [i for i, r in enumerate(rows) if r is not None]
        if ok:
            out[ok] = guided.predict_velocity([rows[i] for i in ok], self.beta)
        return out
