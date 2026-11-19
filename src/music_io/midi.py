"""標準MIDIファイルの読み書き。外部ライブラリ不要

音符は (発音秒, 離鍵秒, 音高, ベロシティ) の並びで扱う。
ペダル(CC64)は (踏んだ秒, 離した秒) の区間の並びで扱う。
"""
import struct

import numpy as np


def _vlq(b, i):
    v = 0
    while True:
        c = b[i]
        i += 1
        v = (v << 7) | (c & 0x7f)
        if c < 0x80:
            return v, i


def read_midi_events(path):
    """チャンネルイベントを (秒, ステータス, データ) の並びで返す。テンポ変化を考慮して tick -> 秒"""
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
    tempo, last_tick, last_sec = 500000, 0, 0.0
    out = []
    for e in events:
        last_sec += (e[0] - last_tick) * tempo / div / 1e6
        last_tick = e[0]
        if e[1] == 'meta':
            if e[2] == 0x51:
                tempo = int.from_bytes(e[3], 'big')
        else:
            out.append((last_sec, e[2], e[3]))
    return out


def read_midi_notes(path):
    """音符(発音秒, 離鍵秒, 音高, ベロシティ)の配列"""
    on, notes = {}, []
    for t, st, d in read_midi_events(path):
        if (st & 0xf0) not in (0x80, 0x90):
            continue
        key = (st & 0x0f, d[0])
        if (st & 0xf0) == 0x90 and d[1] > 0:
            on.setdefault(key, []).append((t, d[1]))
        elif on.get(key):
            t0, v = on[key].pop(0)
            notes.append((t0, t, d[0], v))
    return np.array(sorted(notes))


def read_midi_pedal(path, cc=64, threshold=64):
    """ペダル(既定はダンパー CC64)を踏んでいた区間 (踏んだ秒, 離した秒) の配列"""
    spans, down = [], None
    for t, st, d in read_midi_events(path):
        if (st & 0xf0) != 0xb0 or d[0] != cc:
            continue
        if d[1] >= threshold and down is None:
            down = t
        elif d[1] < threshold and down is not None:
            spans.append((down, t))
            down = None
    return np.array(spans).reshape(-1, 2)


def write_midi(path, notes, pedal=(), tpq=480, bpm=120):
    """notes: (発音秒, 離鍵秒, 音高, ベロシティ) の並び。pedal: (踏んだ秒, 離した秒) の並び。形式0・1トラック"""
    us = int(60e6 / bpm)
    tick = lambda s: int(round(s * 1e6 / us * tpq))
    ev = []
    for on, off, p, v in notes:
        ev.append((tick(on), 1, 0x90, int(p), int(v)))
        ev.append((tick(max(off, on + 0.02)), 0, 0x80, int(p), 64))
    for a, b in pedal:
        ev.append((tick(a), 2, 0xb0, 64, 127))
        ev.append((tick(b), 0, 0xb0, 64, 0))
    ev.sort()

    def vlq(v):
        b = [v & 0x7f]
        v >>= 7
        while v:
            b.append((v & 0x7f) | 0x80)
            v >>= 7
        return bytes(reversed(b))
    data = bytearray(b'\x00\xff\x51\x03' + us.to_bytes(3, 'big'))
    last = 0
    for t, _, st, p, v in ev:
        data += vlq(t - last) + bytes([st, p, v])
        last = t
    data += b'\x00\xff\x2f\x00'
    with open(path, 'wb') as f:
        f.write(b'MThd' + struct.pack('>IHHH', 6, 0, 1, tpq))
        f.write(b'MTrk' + struct.pack('>I', len(data)) + data)
