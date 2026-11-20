#!/usr/bin/env python3
"""採譜モデル(Kong ら)のフレームごとの出力を、PEDB の各演奏について保存する

後から、しきい値を変えた取り出し直しや、人が追加した音の発音時刻・ベロシティの推定(refine.py)の検証に使う。
使い方: python3 scripts/transcribe/save_kong_frames.py [--data ...] [--out output/transcription/kong_frames] [演奏名 ...]
出力: <out>/<演奏名>.npz(notes, pedal, reg_onset_output, velocity_output, frame_output を float16 で)
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
from music_io import audio, pedb  # noqa: E402
from piano_transcribe import kong  # noqa: E402

KEYS = ['reg_onset_output', 'velocity_output', 'frame_output']

ap = argparse.ArgumentParser()
ap.add_argument('perfs', nargs='*')
ap.add_argument('--data')
ap.add_argument('--out', default='output/transcription/kong_frames')
a = ap.parse_args()
root = pedb.data_dir(a.data)
os.makedirs(a.out, exist_ok=True)
tr = None
for i, p in enumerate(a.perfs or pedb.list_performances(root)):
    path = os.path.join(a.out, p + '.npz')
    if os.path.exists(path):
        continue
    if tr is None:
        tr = kong.Transcriber()
    x, sr = audio.load_wav(pedb.perf_files(root, p)['wav'])
    notes, pedal, out = tr.transcribe(x, sr, keep_output=True)
    np.savez_compressed(path, notes=notes, pedal=pedal, **{k: out[k].astype(np.float16) for k in KEYS})
    print(f'[{i + 1}] {p}', flush=True)
