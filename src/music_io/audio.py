"""録音(WAV)の読み込み。外部ライブラリ不要"""
import wave

import numpy as np


def load_wav(path):
    """モノラル float32 と標本化周波数。16/24/32bit に対応"""
    w = wave.open(path)
    sw, nc, sr = w.getsampwidth(), w.getnchannels(), w.getframerate()
    raw = w.readframes(w.getnframes())
    if sw == 2:
        x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    elif sw == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        x = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = np.where(x >= 1 << 23, x - (1 << 24), x).astype(np.float32) / 2 ** 23
    else:
        x = np.frombuffer(raw, np.int32).astype(np.float32) / 2 ** 31
    return x.reshape(-1, nc).mean(1), sr


def resample(x, sr, sr_new):
    """FFT による標本化周波数の変換(採譜モデルの入力用)"""
    if sr == sr_new:
        return x
    n = int(round(len(x) * sr_new / sr))
    X = np.fft.rfft(x)
    m = n // 2 + 1
    Y = np.zeros(m, np.complex64)
    k = min(m, len(X))
    Y[:k] = X[:k]
    return (np.fft.irfft(Y, n) * (n / len(x))).astype(np.float32)
