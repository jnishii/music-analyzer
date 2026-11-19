"""CrestMusePEDB 2nd edition の読み込み

各演奏フォルダ(例: bac-inv001-o-p1)には次のファイルがある。
  *.wav               録音(Disklavier で MIDI と同時に記録)
  *.mid               同時記録の演奏MIDI
  *.xml               楽譜(MusicXML)
  *_fmt3x.txt         楽譜を音符単位の表にしたもの
  *auto_match*.txt    演奏音と楽譜音の対応(人手で修正済み)
"""
import glob
import os
import re
import struct
import wave

import numpy as np

DEFAULT_DATA_DIR = os.path.expanduser('~/git/6-python/data/pedb2_v0.0.1.b')


def data_dir(arg=None):
    """データの場所: 引数 > 環境変数 PEDB_DIR > 既定値"""
    return os.path.expanduser(arg or os.environ.get('PEDB_DIR') or DEFAULT_DATA_DIR)


def list_performances(root):
    return sorted(os.path.basename(d) for d in glob.glob(os.path.join(root, '*-p[0-9]')) if os.path.isdir(d))


def perf_files(root, perf):
    d = os.path.join(root, perf)
    one = lambda pat: (glob.glob(os.path.join(d, pat)) or [None])[0]
    return dict(dir=d, wav=one('*.wav'), mid=one('*.mid'), fmt3x=one('*fmt3x.txt'), match=one('*auto_match*.txt'))


PC = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}


def midi_pitch(name):
    m = re.match(r'([A-G])([#b]*)(-?\d+)$', name)
    if not m:
        return None
    acc = m.group(2).count('#') - m.group(2).count('b')
    return 12 * (int(m.group(3)) + 1) + PC[m.group(1)] + acc


def load_fmt3x(path):
    """戻り値: (TPQN, {音符ID: 音符情報}, 共有音のグループのリスト)"""
    tpqn = None
    notes, dups = {}, []
    for line in open(path, encoding='utf-8', errors='replace'):
        line = line.rstrip('\n')
        if line.startswith('//TPQN:'):
            tpqn = int(line.split(':')[1])
            continue
        if line.startswith('//DuplicateOnsets:'):
            # 2つの声部が同じ時刻・同じ音高を共有する音。演奏では1回だけ弾かれる
            t = [x for x in line.split('\t')[1:] if x]
            dups.append(t[2:])
            continue
        if line.startswith('//') or not line.strip():
            continue
        c = line.split('\t')
        if len(c) < 9 or c[6] != 'chord':
            continue
        stime, bar, staff, voice = int(c[0]), int(c[1]), int(c[2]), int(c[3])
        dur, n = int(c[7]), int(c[8])
        pitches, nots, ids = c[9:9 + n], c[9 + n:9 + 2 * n], c[9 + 2 * n:9 + 3 * n]
        for p, o, i in zip(pitches, nots, ids):
            main = p.split(',')[0]  # 装飾音は "B4,C5,A4" のように並ぶ。先頭が主音
            notes[i] = dict(note_id=i, stime=stime, bar=bar, staff=staff, voice=voice,
                            dur_ticks=dur, pitch_name=main, pitch=midi_pitch(main),
                            ornament=o if o != 'N..' else '', ornament_notes=p)
    return tpqn, notes, dups


def load_match(path):
    """戻り値: (演奏音のリスト, 演奏されなかった楽譜音IDのリスト)

    列: ID 発音 離鍵 音名 発音ベロシティ 離鍵ベロシティ チャンネル 対応状態 楽譜時刻 楽譜音ID 誤り 飛ばし
      対応状態: 0=通常, 1=装飾音, -1=余分な音 / 誤り: 0=正, 1=音高の誤り, 3=余分な音
    """
    perf, missing = [], []
    for line in open(path, encoding='utf-8', errors='replace'):
        if line.startswith('//Missing'):
            missing.append(line.split()[-1])
            continue
        if line.startswith('//') or not line.strip():
            continue
        c = line.split()
        perf.append(dict(id=int(c[0]), on=float(c[1]), off=float(c[2]), pitch_name=c[3],
                         vel=int(c[4]), offvel=int(c[5]), status=int(c[7]), stime=int(c[8]),
                         note_id=c[9], err=int(c[10])))
    return perf, missing


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


def _vlq(b, i):
    v = 0
    while True:
        c = b[i]
        i += 1
        v = (v << 7) | (c & 0x7f)
        if c < 0x80:
            return v, i


def read_midi_notes(path):
    """標準MIDIファイルから音符(発音秒, 離鍵秒, 音高, ベロシティ)の配列を返す。外部ライブラリ不要"""
    b = open(path, 'rb').read()
    assert b[:4] == b'MThd'
    _, ntr, div = struct.unpack('>HHH', b[8:14])
    i, events = 14, []
    for _ in range(ntr):
        L = struct.unpack('>I', b[i + 4:i + 8])[0]
        d = b[i + 8:i + 8 + L]
        i += 8 + L
        j = tick = rs = 0
        while j < len(d):
            dt, j = _vlq(d, j)
            tick += dt
            s = d[j]
            if s == 0xff:
                ty = d[j + 1]
                ln, j2 = _vlq(d, j + 2)
                events.append((tick, 'meta', ty, d[j2:j2 + ln]))
                j = j2 + ln
                continue
            if s in (0xf0, 0xf7):
                ln, j2 = _vlq(d, j + 1)
                j = j2 + ln
                continue
            if s & 0x80:
                rs = s
                j += 1
            n = 1 if (rs & 0xf0) in (0xc0, 0xd0) else 2
            events.append((tick, 'ch', rs, tuple(d[j:j + n])))
            j += n
    events.sort(key=lambda e: e[0])
    # テンポ変化を考慮して tick -> 秒
    tempo, last_tick, last_sec = 500000, 0, 0.0
    sec_of = []
    for e in events:
        last_sec += (e[0] - last_tick) * tempo / div / 1e6
        last_tick = e[0]
        sec_of.append(last_sec)
        if e[1] == 'meta' and e[2] == 0x51:
            tempo = int.from_bytes(e[3], 'big')
    on, notes = {}, []
    for e, t in zip(events, sec_of):
        if e[1] != 'ch' or (e[2] & 0xf0) not in (0x80, 0x90):
            continue
        key = (e[2] & 0x0f, e[3][0])
        if (e[2] & 0xf0) == 0x90 and e[3][1] > 0:
            on.setdefault(key, []).append((t, e[3][1]))
        elif on.get(key):
            t0, v = on[key].pop(0)
            notes.append((t0, t, e[3][0], v))
    return np.array(sorted(notes))
