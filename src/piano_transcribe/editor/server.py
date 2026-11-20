"""採譜の確認・修正用の GUI(ブラウザで開く)のサーバー

流れ: 録音(WAV)を読み込む -> 採譜モデルで MIDI を作る -> 録音と MIDI を再生して確かめる ->
      ピアノロールで間違いを直す -> 直した音を録音に合わせて詰め直す(refine.py) -> MIDI に保存
楽譜(MusicXML)を指定すると、楽譜と照合して違いと補正の提案を出し(score.py)、元の楽譜と
MIDI から書き起こした楽譜を並べて表示する。違いごとの判定は project.json に残る。
楽譜の機能には music21 と verovio が必要(なければ楽譜なしで動く)。

起動: python3 scripts/transcribe/editor.py [--port 8765] [--projects output/editor]
      ブラウザで http://127.0.0.1:8765/ を開く

作業の単位は「プロジェクト」(<projects>/<名前>/)。中身:
  audio.wav     録音の複製
  model.npz     採譜モデルの結果(音符・ペダル・見落としの候補・フレームごとの出力)
  score.xml     楽譜(MusicXML の複製。指定したときだけ)
  project.json  修正後の音符・ペダル・削除した音・違いごとの判定・保存時刻
  <名前>.mid    保存した MIDI
標準ライブラリの http.server だけで動く。外部に公開しない(127.0.0.1 で待ち受ける)前提。
"""
import concurrent.futures
import contextlib
import io
import json
import mimetypes
import os
import re
import shutil
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

from music_io import audio, midi
from piano_transcribe import refine

STATIC = os.path.abspath(os.path.join(os.path.dirname(__file__), 'static'))
CAND_THRESHOLD = 0.1  # 見落としの候補を取り出す発音のしきい値(モデルの既定は 0.3)


def safe_name(s):
    s = os.path.splitext(os.path.basename(s))[0]
    return re.sub(r'[^\w.-]+', '_', s).strip('._') or 'untitled'


class Store:
    """プロジェクトの保存場所と、採譜の実行(裏で1つずつ)"""

    def __init__(self, root):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)
        self.jobs = {}
        self.lock = threading.Lock()
        self.transcriber = None
        self.outputs = {}  # 名前 -> フレームごとの出力(最近使った2つ)
        # verovio は最初に使ったスレッドでしか正しく動かないので、楽譜の描画は1本のスレッドで行う
        self.render_thread = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    def path(self, name, *parts):
        name = safe_name(name)
        return os.path.join(self.root, name, *parts)

    def list(self):
        out = []
        for name in sorted(os.listdir(self.root)):
            d = os.path.join(self.root, name)
            if not os.path.isdir(d):
                continue
            info = dict(name=name, ready=os.path.exists(os.path.join(d, 'model.npz')),
                        saved=os.path.exists(os.path.join(d, 'project.json')))
            job = self.jobs.get(name)
            if job and job['state'] != 'ready':
                info['state'] = job['state']
            out.append(info)
        return out

    def create(self, name, wav_src=None, data=None):
        """録音からプロジェクトを作り、採譜を始める。同じ名前・同じ大きさの録音のプロジェクトがあればそれを開く"""
        size = len(data) if data is not None else os.path.getsize(wav_src)
        base, k = safe_name(name), 1
        name = base
        while os.path.exists(self.path(name, 'audio.wav')):
            if os.path.getsize(self.path(name, 'audio.wav')) == size:
                if os.path.exists(self.path(name, 'model.npz')) or name in self.jobs:
                    return name
                break
            k += 1
            name = f'{base}-{k}'
        d = self.path(name)
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, 'audio.wav')
        if data is not None:
            with open(dst, 'wb') as f:
                f.write(data)
        else:
            shutil.copyfile(wav_src, dst)
        try:
            x, sr = audio.load_wav(dst)  # 読めるかを先に確かめる
        except Exception as e:
            shutil.rmtree(d, ignore_errors=True)
            raise ValueError(f'WAV として読めません(PCM 16/24/32bit のみ対応): {e}')
        self.jobs[name] = dict(state='queued', done=0, total=0, started=time.time(), message='順番待ち',
                               duration=len(x) / sr)
        threading.Thread(target=self._transcribe, args=(name,), daemon=True).start()
        return name

    def _transcribe(self, name):
        job = self.jobs[name]

        class Progress(io.TextIOBase):
            def write(self, s):
                m = re.search(r'Segment (\d+) / (\d+)', s)
                if m:
                    job['done'], job['total'] = int(m.group(1)) + 1, int(m.group(2)) + 1
                return len(s)
        with self.lock:  # モデルは1つなので、採譜は1つずつ
            try:
                from piano_transcribe import kong
                if self.transcriber is None:
                    job.update(state='loading', message='採譜モデルを読み込み中')
                    with contextlib.redirect_stdout(Progress()):
                        self.transcriber = kong.Transcriber()
                job.update(state='running', message='採譜中', started=time.time())
                x, sr = audio.load_wav(self.path(name, 'audio.wav'))
                with contextlib.redirect_stdout(Progress()):
                    notes, pedal, out = self.transcriber.transcribe(x, sr, keep_output=True)
                cand, _ = kong.events_from_output(out, onset_threshold=CAND_THRESHOLD)
                cand = _new_candidates(cand, notes)
                np.savez_compressed(self.path(name, 'model.npz'), notes=notes, pedal=pedal, cand=cand,
                                    duration=len(x) / sr, **{k: v.astype(np.float16) for k, v in out.items()})
                job.update(state='ready', message='完了')
            except Exception as e:  # 画面に理由を出す
                traceback.print_exc()
                job.update(state='error', message=f'{type(e).__name__}: {e}')

    def status(self, name):
        name = safe_name(name)
        job = self.jobs.get(name)
        if job is None:
            return dict(state='ready' if os.path.exists(self.path(name, 'model.npz')) else 'missing')
        return dict(job, elapsed=time.time() - job['started'])

    def output(self, name):
        name = safe_name(name)
        if name not in self.outputs:
            z = np.load(self.path(name, 'model.npz'))
            self.outputs[name] = {k: z[k].astype(np.float32) for k in z.files
                                  if k not in ('notes', 'pedal', 'cand', 'duration')}
            while len(self.outputs) > 2:
                self.outputs.pop(next(iter(self.outputs)))
        return self.outputs[name]

    def load(self, name):
        z = np.load(self.path(name, 'model.npz'))
        to_notes = lambda a, pre, src: [dict(id=f'{pre}{i}', on=float(r[0]), off=float(r[1]), pitch=int(r[2]),
                                             vel=int(round(r[3])), src=src) for i, r in enumerate(a)]
        res = dict(name=safe_name(name), duration=float(z['duration']),
                   model_notes=to_notes(z['notes'], 'm', 'model'),
                   candidates=to_notes(z['cand'], 'c', 'cand'),
                   model_pedal=[[float(a), float(b)] for a, b in z['pedal']])
        pj = self.path(name, 'project.json')
        if os.path.exists(pj):
            res['saved'] = json.load(open(pj, encoding='utf-8'))
        sx = self.path(name, 'score.xml')
        res['score'] = os.path.exists(sx)
        if res['score'] and os.path.exists(sx + '.name'):
            res['score_name'] = open(sx + '.name', encoding='utf-8').read().strip()
        return res

    def set_score(self, name, data=None, path=None, label=None):
        """楽譜(MusicXML)をプロジェクトに登録する。読めるかを先に確かめる"""
        from music_io import musicxml
        if path is not None:
            path = os.path.expanduser(path)
            if not os.path.isfile(path):
                raise ValueError(f'楽譜のファイルがありません: {path}')
            data = open(path, 'rb').read()
        if data and data[:2] == b'PK':
            raise ValueError('圧縮された MusicXML(.mxl)には未対応です。非圧縮の .musicxml / .xml を使ってください')
        dst = self.path(name, 'score.xml')
        tmp = dst + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(data)
        try:
            _, notes, _, measures = musicxml.read(tmp)
        except Exception as e:
            os.remove(tmp)
            raise ValueError(f'MusicXML として読めません: {e}')
        os.replace(tmp, dst)
        with open(dst + '.name', 'w', encoding='utf-8') as f:
            f.write(label or os.path.basename(path or 'score.xml'))
        return dict(notes=len(notes), measures=len(measures))

    def compare(self, name, notes):
        """楽譜と照合して、違い・提案・小節の時刻・楽譜2つの SVG を返す"""
        from alignment import notation
        from piano_transcribe.editor import score
        xml = self.path(name, 'score.xml')
        if not os.path.exists(xml):
            raise ValueError('楽譜が指定されていません')
        t0 = time.time()
        res = score.analyze(xml, notes, self.output(name))
        svgs = self.render_thread.submit(notation.render_svgs, [res.pop('original_xml'), res.pop('performance_xml')]).result()
        res.update(original_svg=svgs[0], performance_svg=svgs[1], seconds=round(time.time() - t0, 2))
        return res

    def save(self, name, body):
        body = dict(body, saved_at=time.strftime('%Y-%m-%d %H:%M:%S'))
        with open(self.path(name, 'project.json'), 'w', encoding='utf-8') as f:
            json.dump(body, f, ensure_ascii=False)
        notes = [(n['on'], n['off'], n['pitch'], n['vel']) for n in body['notes']]
        mid = self.path(name, safe_name(name) + '.mid')
        midi.write_midi(mid, notes, body.get('pedal', []))
        rel = os.path.relpath(mid)
        return dict(saved_at=body['saved_at'], midi=mid if rel.startswith('..') else rel)


def _new_candidates(cand, notes, tol=0.05):
    """しきい値を下げて得た音のうち、すでにある音(同じ音高で発音±50ms)でないもの"""
    keep = []
    for c in cand:
        same = notes[notes[:, 2] == c[2], 0]
        if not len(same) or np.min(np.abs(same - c[0])) > tol:
            keep.append(c)
    return np.array(keep).reshape(-1, 4)


def make_handler(store):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, code, body, ctype='application/json; charset=utf-8', headers=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get('Content-Length') or 0)
            return self.rfile.read(n) if n else b''

        def _route(self):
            u = urllib.parse.urlparse(self.path)
            parts = [urllib.parse.unquote(p) for p in u.path.strip('/').split('/') if p]
            return parts, urllib.parse.parse_qs(u.query)

        def do_GET(self):
            try:
                parts, q = self._route()
                if not parts or parts[0] != 'api':
                    f = os.path.normpath(os.path.join(STATIC, *(parts or ['index.html'])))
                    if not f.startswith(STATIC) or not os.path.isfile(f):
                        return self._send(404, {'error': 'not found'})
                    ctype = mimetypes.guess_type(f)[0] or 'application/octet-stream'
                    if ctype.startswith('text/') or ctype.endswith('javascript'):
                        ctype += '; charset=utf-8'
                    return self._send(200, open(f, 'rb').read(), ctype)
                if parts == ['api', 'projects']:
                    return self._send(200, store.list())
                if len(parts) >= 3 and parts[1] == 'projects':
                    name = parts[2]
                    sub = parts[3] if len(parts) > 3 else ''
                    if sub == 'status':
                        return self._send(200, store.status(name))
                    if sub == '':
                        return self._send(200, store.load(name))
                    if sub == 'audio':
                        return self._send(200, open(store.path(name, 'audio.wav'), 'rb').read(), 'audio/wav')
                    if sub == 'roll':
                        img = refine.roll_image(store.output(name))
                        return self._send(200, img.tobytes(), 'application/octet-stream',
                                          {'X-Frames': str(img.shape[0]), 'X-Keys': str(img.shape[1]), 'X-Fps': '100'})
                    if sub == 'midi':
                        p = store.path(name, safe_name(name) + '.mid')
                        return self._send(200, open(p, 'rb').read(), 'audio/midi',
                                          {'Content-Disposition': f'attachment; filename="{safe_name(name)}.mid"'})
                self._send(404, {'error': 'not found'})
            except FileNotFoundError as e:
                self._send(404, {'error': str(e)})
            except Exception as e:
                traceback.print_exc()
                self._send(500, {'error': f'{type(e).__name__}: {e}'})

        def do_POST(self):
            try:
                parts, q = self._route()
                if parts == ['api', 'upload']:
                    name = q.get('name', ['upload'])[0]
                    return self._send(200, {'name': store.create(name, data=self._body())})
                if parts == ['api', 'open']:
                    body = json.loads(self._body() or b'{}')
                    path = os.path.expanduser(body['path'])
                    if not os.path.isfile(path):
                        return self._send(400, {'error': f'ファイルがありません: {path}'})
                    name = store.create(body.get('name') or path, wav_src=path)
                    if body.get('score'):
                        store.set_score(name, path=body['score'])
                    return self._send(200, {'name': name})
                if len(parts) == 4 and parts[1] == 'projects' and parts[3] == 'score':
                    raw = self._body()
                    if raw[:1] == b'{':
                        b = json.loads(raw)
                        return self._send(200, store.set_score(parts[2], path=b['path']))
                    return self._send(200, store.set_score(parts[2], data=raw, label=q.get('name', [None])[0]))
                if len(parts) == 4 and parts[1] == 'projects':
                    name, sub = parts[2], parts[3]
                    body = json.loads(self._body() or b'{}')
                    if sub == 'compare':
                        return self._send(200, store.compare(name, body['notes']))
                    if sub == 'refine':
                        res = refine.refine_notes(store.output(name), body['notes'], body['ids'])
                        return self._send(200, res)
                    if sub == 'save':
                        return self._send(200, store.save(name, body))
                self._send(404, {'error': 'not found'})
            except ValueError as e:
                self._send(400, {'error': str(e)})
            except ImportError as e:
                self._send(400, {'error': f'楽譜の機能には music21 と verovio が必要です(pip install -e .[score]): {e}'})
            except Exception as e:
                traceback.print_exc()
                self._send(500, {'error': f'{type(e).__name__}: {e}'})
    return Handler


def serve(port=8765, projects='output/editor', host='127.0.0.1'):
    store = Store(projects)
    httpd = ThreadingHTTPServer((host, port), make_handler(store))
    print(f'採譜の確認・修正: http://{host}:{port}/  (プロジェクト: {store.root})  終了は Ctrl+C', flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
