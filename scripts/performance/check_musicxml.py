#!/usr/bin/env python3
"""music_io.musicxml の読み込みを、PEDB の fmt3x と比べて確かめる(ID・楽譜時刻・音価・音高・段・種類)

使い方: python3 scripts/performance/check_musicxml.py [--data ...]
"""
import argparse
import collections
import glob
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
from music_io import musicxml, pedb  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('--data')
a = ap.parse_args()
root = pedb.data_dir(a.data)
C = collections.Counter()
ex = collections.defaultdict(list)
for p in pedb.list_performances(root):
    f = pedb.perf_files(root, p)
    xml = glob.glob(os.path.join(f['dir'], '*.xml'))[0]
    t1, s1, d1 = pedb.load_fmt3x(f['fmt3x'], grace=True)
    t2, s2, d2 = musicxml.load_musicxml(xml)
    for i, n in s1.items():
        m = s2.get(i)
        if m is None:
            C['xml にない'] += 1
            ex['xml にない'].append((p, i))
            continue
        C['対応'] += 1
        for k in ['pitch', 'staff', 'kind']:
            if n[k] != m[k]:
                C[k + ' が違う'] += 1
                ex[k + ' が違う'].append((p, i, n[k], m[k]))
        if n['stime'] * t2 != m['stime'] * t1:
            C['楽譜時刻が違う'] += 1
            ex['楽譜時刻が違う'].append((p, i, n['stime'] / t1, m['stime'] / t2))
        if n['kind'] == 'chord' and n['dur_ticks'] * t2 != m['dur_ticks'] * t1:
            C['音価が違う'] += 1
            ex['音価が違う'].append((p, i, n['dur_ticks'] / t1, m['dur_ticks'] / t2))
        if bool(n['ornament'][:2] not in ('', 'N.')) != bool(m['ornament'][:2] not in ('', 'N.')):
            C['装飾の有無が違う'] += 1
            ex['装飾の有無が違う'].append((p, i, n['ornament'], m['ornament']))
    extra = [i for i in s2 if i not in s1]
    C['fmt3x にない'] += len(extra)
    ex['fmt3x にない'] += [(p, i, s2[i]['pitch_name']) for i in extra[:3]]
    C['共有音の組 fmt3x / xml'] += 0
    C['共有音 fmt3x'] += len(d1)
    C['共有音 xml'] += len(d2)
for k, v in C.items():
    print(f'{k}: {v}', ex[k][:4] if ex.get(k) else '')
