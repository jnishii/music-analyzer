"""演奏を楽譜に書き起こし、元の楽譜との違いを求める

演奏の音(MIDI や採譜の結果)を楽譜と対応付け(align.py、または matchファイル)、
元の楽譜の小節・拍子・調・音部記号を型にして、演奏した音を置き直した楽譜(MusicXML)を作る。
  正しく弾いた音   楽譜の位置と音価のまま。前後の出来事から求めたその場のテンポで、演奏の位置が楽譜から
                   RHYTHM_TOL 拍以上かつ RHYTHM_MS 以上ずれていれば「リズム」の違いとして色を付け、
                   MOVE_TOL 拍以上ずれていれば、ずれた位置(格子に丸めた位置)に置く
  弾き間違い       楽譜の位置に、弾いた音高で置く
  余分な音         演奏の時刻を拍に写し、格子(16分音符・3連符)に丸めて置く
  弾かれなかった音 置かない(休符になる)
  装飾の実際の音   書かない(主音で代表させる)
元の楽譜にも、弾かれなかった音・弾き間違えた音・リズムの違う音に色を付ける(music_io.musicxml.colored)。

依存: music21(楽譜の組み立てと MusicXML の書き出し)
"""
import numpy as np

from music_io import musicxml
from music_io.pedb import midi_pitch

from . import align

COLORS = {'missing': '#E34948', 'extra': '#E34948', 'pitch': '#EB6834', 'rhythm': '#6250D6'}
LABELS = {'missing': '抜け', 'extra': '余分', 'pitch': '弾き間違い', 'rhythm': 'リズム'}
RHYTHM_TOL = 0.25   # 拍(四分音符)。これ以上、かつ RHYTHM_MS 以上ずれた音をリズムの違いとして色を付ける
RHYTHM_MS = 80
MOVE_TOL = 0.5      # 拍。これ以上ずれた音は、演奏の楽譜でずれた位置に置く(それ未満は書かれた位置のまま)
GRID = 12           # 1拍を 12 等分した格子(16分音符と3連符の両方を表せる)


def _quantize(beat):
    """16分音符(1/4)と8分3連符(1/3)の格子の近い方に丸める"""
    a, b = round(beat * 4) / 4, round(beat * 3) / 3
    return a if abs(a - beat) <= abs(b - beat) + 0.02 else b


class TimeMap:
    """演奏の時刻 <-> 楽譜の拍。正しく弾かれた音の出来事(同じ楽譜時刻の音の発音の中央値)から作る"""

    def __init__(self, beats, times):
        self.b, self.t = align._clean_anchors(beats, times, line=False)

    def beat(self, t):
        return float(np.interp(t, self.t, self.b))

    def local_beat(self, t, exclude_beat, half=2.0, min_side=2):
        """t の付近の出来事だけで局所的な直線を当てはめて拍に写す(exclude_beat の出来事は使わない)。
        自分を含めないので、その出来事全体のずれ(リズムの違い)が見える。
        前後それぞれに min_side 個以上の出来事がないとき(曲の端、長い休符の前後)は判定できないので None。
        戻り値: (拍, その場のテンポ 拍/秒)"""
        d = self.b - exclude_beat
        before = (d < -1e-9) & (d >= -half)
        after = (d > 1e-9) & (d <= half)
        if before.sum() < min_side or after.sum() < min_side:
            return None
        k = before | after
        c = np.polyfit(self.t[k], self.b[k], 1)
        return float(np.polyval(c, t)), float(c[0])


def compare(xml_path, perf_rows, missing):
    """元の楽譜(MusicXML)と、対応付けの結果(load_match の形)から、違いのリストと書き起こしの材料を作る。
    戻り値: (違いのリスト, 演奏の楽譜に置く音のリスト, 元の楽譜の音符ID -> 違いの種類)"""
    tpqn, score, dups, measures = musicxml.read(xml_path)
    groups = {i: g for g in dups for i in g}
    starts = np.array([m['start'] for m in measures]) / tpqn
    meas_of = lambda b: measures[max(0, int(np.searchsorted(starts, b + 1e-9)) - 1)]

    ev = {}
    for r in perf_rows:
        if r['status'] == 0 and r['err'] == 0 and r['note_id'] in score:
            ev.setdefault(score[r['note_id']]['stime'] / tpqn, []).append(r['on'])
    tm = TimeMap(list(ev), [float(np.median(v)) for v in ev.values()])

    diffs, place, orig_kind = [], [], {}
    staff_pitch = {}  # 段ごとの音高の代表(余分な音の段を決める)
    for n in score.values():
        staff_pitch.setdefault(n['staff'], []).append(n['pitch'])
    staff_mid = {s: np.median(v) for s, v in staff_pitch.items()}

    def where(b):
        m = meas_of(b)
        return m['number'], b - m['start'] / tpqn

    for r in perf_rows:
        p = midi_pitch(r['pitch_name'])
        if r['note_id'] != '*' and r['note_id'] in score:
            n = score[r['note_id']]
            if r['status'] == 1 or n['kind'] != 'chord':
                continue   # 装飾の実際の音・装飾音符は書かない
            b0, dur = n['stime'] / tpqn, n['dur_ticks'] / tpqn
            item = dict(beat=b0, dur=dur, pitch=p, staff=n['staff'], voice=n['voice'], name=None, kind=None,
                        note_id=r['note_id'], on=r['on'])
            if r['err'] == 1:
                item.update(kind='pitch')
                orig_kind[r['note_id']] = 'pitch'
                mn, off = where(b0)
                diffs.append(dict(kind='pitch', measure=mn, beat=off, ids=[r['note_id']], on=r['on'],
                                  desc=f"{n['pitch_name']} → {_plain_name(p)}"))
            else:
                item['name'] = n['pitch_name']
                lb = None if ('Arp' in n['ornament'] or 'Fer' in n['ornament']) else tm.local_beat(r['on'], b0)
                if lb is not None:
                    bl, bps = lb
                    dev = bl - b0
                    ms = abs(dev) / bps * 1000 if bps > 0 else 0
                    if abs(dev) >= RHYTHM_TOL and ms >= RHYTHM_MS:
                        item['kind'] = 'rhythm'
                        if abs(dev) >= MOVE_TOL:
                            item['beat'] = _quantize(bl)
                        orig_kind[r['note_id']] = 'rhythm'
                        mn, off = where(b0)
                        diffs.append(dict(kind='rhythm', measure=mn, beat=off, ids=[r['note_id']], on=r['on'],
                                          desc=f"{n['pitch_name']} {abs(dev):.2f}拍({ms:.0f}ms){'遅い' if dev > 0 else '早い'}"))
            place.append(item)
        elif r['note_id'] == '*':
            b = _quantize(tm.beat(r['on']))
            st = min(staff_mid, key=lambda s: abs(staff_mid[s] - p)) if staff_mid else 1
            place.append(dict(beat=b, dur=None, pitch=p, staff=st, voice=9, name=None, kind='extra', note_id=None,
                              on=r['on'], off=r['off']))
            mn, off = where(b)
            diffs.append(dict(kind='extra', measure=mn, beat=off, ids=[], on=r['on'], desc=_plain_name(p)))
    for i in missing:
        if i not in score or score[i]['kind'] != 'chord':
            continue
        if any(g in score and g not in missing for g in groups.get(i, [])):
            continue   # 共有音の片方が弾かれていれば抜けではない
        n = score[i]
        orig_kind[i] = 'missing'
        mn, off = where(n['stime'] / tpqn)
        diffs.append(dict(kind='missing', measure=mn, beat=off, ids=[i], on=None, desc=n['pitch_name']))
    # 余分な音の長さ: 同じ段の次の発音まで(ペダル込みの採譜では離鍵が当てにならないため)。1/4〜1拍に収める
    by_staff = {}
    for it in place:
        by_staff.setdefault(it['staff'], set()).add(it['beat'])
    for it in place:
        if it['dur'] is None:
            later = [b for b in by_staff[it['staff']] if b > it['beat'] + 1e-6]
            gap = (min(later) - it['beat']) if later else 1.0
            it['dur'] = float(np.clip(_quantize(gap), 0.25, 1.0))
    order = {'missing': 0, 'pitch': 1, 'extra': 2, 'rhythm': 3}
    diffs.sort(key=lambda d: (_num(d['measure']), d['beat'], order[d['kind']]))
    return diffs, place, orig_kind, measures, tpqn


def _num(m):
    try:
        return float(m)
    except ValueError:
        return 1e9


def _plain_name(p):
    from music_io.pedb import pitch_name
    return pitch_name(p)


def build_performance_score(xml_path, place, measures, tpqn):
    """元の楽譜を型にして、place の音を置いた楽譜(MusicXML の文字列)を作る"""
    from music21 import chord, converter, note, stream, tie
    from music21.musicxml.m21ToXml import GeneralObjectExporter

    sc = converter.parse(xml_path, forceSource=True)   # music21 のキャッシュ(書き換えた楽譜が残ることがある)を使わない
    # 音符につながる線(スラーなど)は外す。ピアノの大括弧(StaffGroup)は残す
    for sp in list(sc.recurse().getElementsByClass('Spanner')):
        if any(isinstance(e, note.GeneralNote) for e in sp.getSpannedElements()):
            sc.remove(sp, recurse=True)
    parts = list(sc.parts)
    starts = [m['start'] / tpqn for m in measures]
    lens = [m['length'] / tpqn for m in measures]
    fifths = [m['fifths'] for m in measures]
    for si, part in enumerate(parts, 1):
        ms = list(part.getElementsByClass(stream.Measure))
        for m in ms:
            for v in list(m.voices):
                m.remove(v)
            for e in list(m.notesAndRests):
                m.remove(e)
        # この段に置く音を小節ごとに分ける(小節をまたぐ音はタイで分ける)
        per = [[] for _ in ms]
        for it in place:
            if it['staff'] != si:
                continue
            b, d = it['beat'], max(it['dur'], 1 / GRID)
            first = True
            while d > 1e-6:
                k = max(0, int(np.searchsorted(starts, b + 1e-9)) - 1)
                if k >= len(ms):
                    break
                end = starts[k] + lens[k]
                seg = min(d, end - b) if end - b > 1e-6 else d
                per[k].append(dict(it, off=b - starts[k], qd=seg, tie_start=d - seg > 1e-6, tie_stop=not first))
                b, d, first = b + seg, d - seg, False
        for k, m in enumerate(ms):
            items = sorted(per[k], key=lambda x: (x['off'], -x['pitch']))
            # 声部: 同じ位置・同じ長さの音は和音にまとめ、重なる音は別の声部へ
            groups = []
            for it in items:
                q = round(it['off'] * GRID) / GRID, round(it['qd'] * GRID) / GRID
                if groups and groups[-1]['key'] == q and groups[-1]['voice_hint'] == it['voice']:
                    groups[-1]['items'].append(it)
                else:
                    groups.append(dict(key=q, voice_hint=it['voice'], items=[it]))
            # 重ならない限り、楽譜と同じ声部(なければ空いている声部)に入れる
            voices = []
            fits = lambda vv, o, d: all(gg['key'][0] + gg['key'][1] <= o + 1e-6 or o + d <= gg['key'][0] + 1e-6
                                        for gg in vv['groups'])
            for g in sorted(groups, key=lambda g: (g['voice_hint'], g['key'][0])):
                o, d = g['key']
                c = [vv for vv in voices if vv['hint'] == g['voice_hint'] and fits(vv, o, d)] or \
                    [vv for vv in voices if fits(vv, o, d)]
                if c:
                    c[0]['groups'].append(g)
                else:
                    voices.append(dict(hint=g['voice_hint'], groups=[g]))
            mlen = lens[k] if k < len(lens) else m.duration.quarterLength
            for vi, vv in enumerate(voices, 1):
                v = stream.Voice(id=str(vi))
                for g in sorted(vv['groups'], key=lambda g: g['key'][0]):
                    o, d = g['key']
                    if d <= 0:
                        continue
                    ns = []
                    for it in g['items']:
                        nn = note.Note(_m21_name(it, fifths[min(k, len(fifths) - 1)]))
                        if it['kind']:
                            nn.style.color = COLORS[it['kind']]
                        if it['tie_start'] and it['tie_stop']:
                            nn.tie = tie.Tie('continue')
                        elif it['tie_start']:
                            nn.tie = tie.Tie('start')
                        elif it['tie_stop']:
                            nn.tie = tie.Tie('stop')
                        ns.append(nn)
                    el = ns[0] if len(ns) == 1 else chord.Chord(ns)
                    el.quarterLength = d
                    v.insert(o, el)
                v.makeRests(fillGaps=True, refStreamOrTimeRange=[0.0, mlen], inPlace=True, hideRests=vi > 1)
                m.insert(0, v)
            if not voices:
                r = note.Rest(quarterLength=mlen)
                r.fullMeasure = True
                m.insert(0, r)
    for part in parts:
        part.partName = ''
        part.partAbbreviation = ''
        for ins in part.recurse().getElementsByClass('Instrument'):
            ins.partName = ''
            ins.instrumentName = ''
    ex = GeneralObjectExporter(sc)
    return ex.parse().decode('utf-8')


def _m21_name(it, fifths):
    """music21 の音名。楽譜どおりの音は楽譜の綴り、それ以外は調号に合わせて # か b で綴る"""
    if it.get('name'):
        n = it['name']
        return n[0] + n[1:].replace('b', '-').replace('#', '#') if len(n) > 1 else n
    p = it['pitch']
    sharp = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    flat = ['C', 'D-', 'D', 'E-', 'E', 'F', 'G-', 'G', 'A-', 'A', 'B-', 'B']
    return (flat if fifths < 0 else sharp)[p % 12] + str(p // 12 - 1)
