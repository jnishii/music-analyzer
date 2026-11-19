"""高解像度ピアノ採譜モデル(Kong ら 2021, bytedance/piano_transcription)の呼び出し

楽譜を使わずに、録音だけから音符(発音・離鍵・ベロシティ)とダンパーペダルを推定する。
MAESTRO(Disklavier の演奏)で学習済みのモデル。入力は 16kHz のモノラル。

必要なもの
  pip install torch piano_transcription_inference
  重み: 環境変数 KONG_CHECKPOINT か models/CRNN_note_F1=0.9677_pedal_F1=0.9186.pth
        (https://zenodo.org/record/4034264 から取得。git の管理外)
"""
import contextlib
import functools
import os

import numpy as np

from music_io import audio

SR = 16000
DEFAULT_CHECKPOINT = 'models/CRNN_note_F1=0.9677_pedal_F1=0.9186.pth'
CHECKPOINT_URL = 'https://zenodo.org/record/4034264/files/CRNN_note_F1%3D0.9677_pedal_F1%3D0.9186.pth?download=1'


def checkpoint_path(path=None):
    p = path or os.environ.get('KONG_CHECKPOINT') or DEFAULT_CHECKPOINT
    if not os.path.exists(p) or os.path.getsize(p) < 1.6e8:
        # 見つからないときにパッケージが勝手に wget でホームに落とすのを避ける
        raise FileNotFoundError(f'採譜モデルの重みがありません: {p}\n  curl -L -o "{p}" "{CHECKPOINT_URL}"')
    return p


def default_device():
    import torch
    return 'mps' if torch.backends.mps.is_available() else 'cpu'


@contextlib.contextmanager
def _full_torch_load():
    """重みファイルは numpy の値も含むため、torch 2.6 以降の既定(weights_only=True)では読めない"""
    import torch
    orig = torch.load
    torch.load = functools.partial(orig, weights_only=False)
    try:
        yield
    finally:
        torch.load = orig


class Transcriber:
    def __init__(self, checkpoint=None, device=None):
        import torch
        from piano_transcription_inference import PianoTranscription
        device = device or default_device()
        with _full_torch_load():
            self.pt = PianoTranscription(checkpoint_path=checkpoint_path(checkpoint), device=torch.device('cpu'))
        # パッケージは cuda 以外では CPU に置いたままにするので、ここで移す
        self.pt.model.to(device)
        self.pt.model.eval()

    def transcribe(self, x, sr):
        """戻り値: (音符の配列 [発音秒, 離鍵秒, 音高, ベロシティ], ペダル区間の配列 [踏んだ秒, 離した秒])"""
        import torch
        y = audio.resample(np.asarray(x, np.float32), sr, SR)
        with torch.no_grad():
            d = self.pt.transcribe(y, None)
        notes = np.array(sorted((e['onset_time'], e['offset_time'], e['midi_note'], e['velocity'])
                                for e in d['est_note_events'])).reshape(-1, 4)
        pedal = np.array(sorted((e['onset_time'], e['offset_time']) for e in d['est_pedal_events'])).reshape(-1, 2)
        return notes, pedal
