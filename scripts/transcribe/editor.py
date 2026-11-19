#!/usr/bin/env python3
"""採譜の確認・修正用の GUI を起動する

  録音(WAV)を読み込む -> 採譜モデルで MIDI を作る -> 録音と MIDI を再生して確かめる ->
  ピアノロールで間違いを直す -> 直した音を録音に合わせて再調整する -> MIDI に保存

使い方: python3 scripts/transcribe/editor.py [--port 8765] [--projects output/editor]
        ブラウザで http://127.0.0.1:8765/ を開く。プロジェクトは --projects の下に保存される
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
from piano_transcribe.editor import server  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('--port', type=int, default=8765)
ap.add_argument('--projects', default='output/editor')
a = ap.parse_args()
server.serve(a.port, a.projects)
