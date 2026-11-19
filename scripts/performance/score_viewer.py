#!/usr/bin/env python3
"""元の楽譜と、演奏から書き起こした楽譜を並べて表示し、違う所を示す GUI を起動する

使い方: python3 scripts/performance/score_viewer.py [--port 8766] [--data PEDBのフォルダ] [--editor output/editor]
        ブラウザで http://127.0.0.1:8766/ を開く
        採譜の GUI(scripts/transcribe/editor.py)で保存した MIDI も「演奏」として選べる
依存: music21, verovio
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
from performance_analysis.viewer import server  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('--port', type=int, default=8766)
ap.add_argument('--data')
ap.add_argument('--editor', default='output/editor', help='採譜の GUI のプロジェクトの場所')
ap.add_argument('--trans', default='output/transcription')
a = ap.parse_args()
server.serve(a.port, a.data, a.editor, a.trans)
