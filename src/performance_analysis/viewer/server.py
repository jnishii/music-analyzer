"""元の楽譜と、演奏から書き起こした楽譜を並べて表示し、違う所を示す(ブラウザで開く)

起動: python3 scripts/performance/score_viewer.py [--port 8766]
      ブラウザで http://127.0.0.1:8766/ を開く

楽譜: PEDB の演奏名(その曲の MusicXML)か、MusicXML のパス
演奏: match(PEDB の人手の対応付け)、midi(PEDB の同時記録MIDI)、kong(採譜モデルの出力)、
      editor:<名前>(採譜の確認・修正 GUI で保存した MIDI)、MIDI ファイルのパス
      match 以外は楽譜と自動で対応付ける(performance_analysis/align.py)
採譜の GUI とはファイル(保存した MIDI)だけでつながる。採譜のコードには依存しない。
"""
import concurrent.futures
import glob
import json
import os
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

from music_io import midi, musicxml, pedb
from performance_analysis import align, notation

STATIC = os.path.abspath(os.path.join(os.path.dirname(__file__), 'static'))
VEROVIO = {'pageWidth': 2100, 'pageHeight': 60000, 'scale': 35, 'adjustPageHeight': True, 'footer': 'none',
           'header': 'none', 'svgAdditionalAttribute': ['measure@n'], 'svgViewBox': True}


class Viewer:
    def __init__(self, data=None, editor='output/editor', trans='output/transcription'):
        self.root = pedb.data_dir(data)
        self.editor = os.path.abspath(editor)
        self.trans = os.path.abspath(trans)
        self.cache = {}
        # verovio は最初に使ったスレッドでしか正しく動かない(別スレッドでは読み込みに失敗する)ので、描画は1本のスレッドで行う
        self.render_thread = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    def options(self):
        pedb_perfs = pedb.list_performances(self.root) if os.path.isdir(self.root) else []
        eds = []
        if os.path.isdir(self.editor):
            for n in sorted(os.listdir(self.editor)):
                d = os.path.join(self.editor, n)
                if os.path.exists(os.path.join(d, n + '.mid')) or os.path.exists(os.path.join(d, 'model.npz')):
                    eds.append(dict(name=n, saved=os.path.exists(os.path.join(d, n + '.mid')),
                                    pedb=n in pedb_perfs))
        return dict(pedb=pedb_perfs, editor=eds)

    def _score_path(self, score):
        if score.startswith('pedb:'):
            d = os.path.join(self.root, score[5:])
            xs = sorted(glob.glob(os.path.join(d, '*.xml')))
            if not xs:
                raise ValueError(f'楽譜(MusicXML)がありません: {d}')
            return xs[0]
        path = os.path.expanduser(score[5:] if score.startswith('file:') else score)
        if not os.path.isfile(path):
            raise ValueError(f'楽譜のファイルがありません: {path}')
        return path

    def _performance(self, perf, score):
        """(演奏音のリスト(load_match の形), 弾かれなかった楽譜音ID, 入力の説明, 入力ファイル)"""
        pname = score[5:] if score.startswith('pedb:') else None
        if perf == 'match':
            if not pname:
                raise ValueError('match は PEDB の楽譜のときだけ使えます')
            f = pedb.perf_files(self.root, pname)
            m, miss = pedb.load_match(f['match'])
            return m, miss, 'PEDB の人手の対応付け(同時記録MIDI)', f['match']
        if perf == 'midi':
            if not pname:
                raise ValueError('midi は PEDB の楽譜のときだけ使えます')
            path, desc = pedb.perf_files(self.root, pname)['mid'], 'PEDB の同時記録MIDI(自動の対応付け)'
            notes = midi.read_midi_notes(path)
        elif perf == 'kong':
            if not pname:
                raise ValueError('kong は PEDB の楽譜のときだけ使えます')
            path, desc = os.path.join(self.trans, 'kong', pname + '.npz'), '採譜モデルの出力(自動の対応付け)'
            if not os.path.exists(path):
                raise ValueError(f'採譜の結果がありません: {path}(scripts/transcribe/eval_transcription.py で作る)')
            notes = np.load(path)['notes']
        elif perf.startswith('editor:'):
            name = perf[7:]
            d = os.path.join(self.editor, name)
            path = os.path.join(d, name + '.mid')
            if os.path.exists(path):
                desc = f'採譜の GUI で保存した MIDI: {name}(自動の対応付け)'
                notes = midi.read_midi_notes(path)
            else:
                path = os.path.join(d, 'model.npz')
                desc = f'採譜の GUI のモデルの出力(未保存): {name}(自動の対応付け)'
                notes = np.load(path)['notes']
        else:
            path = os.path.expanduser(perf[5:] if perf.startswith('file:') else perf)
            if not os.path.isfile(path):
                raise ValueError(f'演奏の MIDI ファイルがありません: {path}')
            desc, notes = f'MIDI ファイル: {path}(自動の対応付け)', midi.read_midi_notes(path)
        xml = self._score_path(score)
        tpqn, sc, dups = musicxml.load_musicxml(xml)
        m, miss = align.align(tpqn, sc, dups, notes)
        return m, miss, desc, path

    def compare(self, score, perf):
        xml = self._score_path(score)
        m, miss, desc, src = self._performance(perf, score)
        key = (score, perf, os.path.getmtime(xml), os.path.getmtime(src))
        if key in self.cache:
            return self.cache[key]
        t0 = time.time()
        diffs, place, orig_kind, measures, tpqn = notation.compare(xml, m, miss)
        perf_xml = notation.build_performance_score(xml, place, measures, tpqn)
        orig_xml = musicxml.colored(xml, {i: notation.COLORS[k] for i, k in orig_kind.items()})
        svgs = self.render_thread.submit(_render, [orig_xml, perf_xml]).result()
        counts = {k: sum(d['kind'] == k for d in diffs) for k in notation.LABELS}
        n_perf = sum(1 for r in m if r['status'] != 1)
        res = dict(original_svg=svgs[0], performance_svg=svgs[1], diffs=diffs, counts=counts, labels=notation.LABELS,
                   colors=notation.COLORS, source=desc, score=os.path.basename(xml), n_perf_notes=n_perf,
                   seconds=round(time.time() - t0, 2))
        self.cache = {key: res}   # 直近の1つだけ持つ
        return res


def _render(xmls):
    import verovio
    tk = verovio.toolkit()
    tk.setOptions(VEROVIO)
    svgs = []
    for data in xmls:
        if not tk.loadData(data):
            raise ValueError('楽譜の描画に失敗しました: ' + tk.getLog()[-300:])
        svgs.append(''.join(tk.renderToSVG(p) for p in range(1, tk.getPageCount() + 1)))
    return svgs


def make_handler(viewer):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, code, body, ctype='application/json; charset=utf-8'):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False, default=lambda o: None if o != o else float(o)).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            try:
                u = urllib.parse.urlparse(self.path)
                q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
                if u.path == '/api/options':
                    return self._send(200, viewer.options())
                if u.path == '/api/compare':
                    return self._send(200, viewer.compare(q['score'], q['perf']))
                rel = u.path.strip('/') or 'index.html'
                f = os.path.normpath(os.path.join(STATIC, rel))
                if not f.startswith(STATIC) or not os.path.isfile(f):
                    return self._send(404, {'error': 'not found'})
                ctype = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css'}.get(
                    os.path.splitext(f)[1], 'application/octet-stream') + '; charset=utf-8'
                self._send(200, open(f, 'rb').read(), ctype)
            except (ValueError, KeyError, FileNotFoundError) as e:
                self._send(400, {'error': str(e)})
            except Exception as e:
                traceback.print_exc()
                self._send(500, {'error': f'{type(e).__name__}: {e}'})
    return Handler


def serve(port=8766, data=None, editor='output/editor', trans='output/transcription', host='127.0.0.1'):
    v = Viewer(data, editor, trans)
    httpd = ThreadingHTTPServer((host, port), make_handler(v))
    print(f'楽譜の比較: http://{host}:{port}/  終了は Ctrl+C', flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
