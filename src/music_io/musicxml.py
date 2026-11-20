"""MusicXML(partwise)の読み込みと、音符に色を付けた書き出し

load_musicxml は music_io.pedb.load_fmt3x(..., grace=True) と同じ形を返すので、fmt3x のない楽譜でも
対応付け(performance_analysis.align)や音符ごとの表に使える。
音符ID は PEDB の fmt3x と同じ「P<パート番号>-<小節番号>-<小節内の note 要素の通し番号(休符・和音の音も数える)>」。
PEDB の全演奏で、fmt3x と ID・楽譜時刻・音高が一致することを確かめている(scripts/performance/check_musicxml.py)。

扱わないもの: 繰り返し記号・D.C. などの展開(楽譜に書かれた順のまま読む)、パート間で異なる divisions の細かい扱い以外の拡張。
"""
import math
import xml.etree.ElementTree as ET

STEP = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
STEPS = 'CDEFGAB'
ORN = {'trill-mark': 'Tr', 'inverted-mordent': 'Im', 'mordent': 'Mr', 'turn': 'Tn', 'inverted-turn': 'It',
       'delayed-turn': 'Dt', 'delayed-inverted-turn': 'Dt', 'shake': 'Tr', 'tremolo': ''}
KEY_ORDER_SHARP = 'FCGDAEB'


def _midi(step, alter, octave):
    return 12 * (octave + 1) + STEP[step] + alter


def _name(step, alter, octave):
    return step + ('#' * alter if alter > 0 else 'b' * -alter) + str(octave)


def _key_alter(fifths, step):
    """調号による変化(+1/-1/0)"""
    if fifths > 0 and step in KEY_ORDER_SHARP[:fifths]:
        return 1
    if fifths < 0 and step in KEY_ORDER_SHARP[::-1][:-fifths]:
        return -1
    return 0


def _neighbor(step, octave, d, fifths):
    """全音階上の隣の音(d=+1 上, -1 下)。調号による変化をつける"""
    i = STEPS.index(step) + d
    o = octave + (i // 7)
    s = STEPS[i % 7]
    a = _key_alter(fifths, s)
    return _name(s, a, o), _midi(s, a, o)


def _measure_number(m, k):
    n = m.get('number', str(k))
    return n


def read(path):
    """MusicXML を読み、(TPQN, 音符の辞書, 共有音の組, 小節のリスト) を返す。
    小節: dict(number, start(tick), length(tick), beats, beat_type, fifths)"""
    root = ET.parse(path).getroot()
    if root.tag != 'score-partwise':
        raise ValueError('score-partwise 形式の MusicXML のみ対応')
    parts = root.findall('part')
    # TPQN = すべての divisions の最小公倍数
    divs = {int(d.text) for d in root.iter('divisions')} or {1}
    tpqn = 1
    for d in divs:
        tpqn = tpqn * d // math.gcd(tpqn, d)
    notes, measures = {}, []
    for pi, part in enumerate(parts, 1):
        div, fifths, beats, beat_type = 1, 0, 4, 4
        start = 0
        open_ties = {}   # (staff, voice, midi) -> 続く音を足し込む音符ID
        for mk, m in enumerate(part.findall('measure'), 1):
            mnum = _measure_number(m, mk)
            last_start = 0
            pending_grace = []
            cur = mx = 0
            k = 0
            chord_staff = 1
            for el in m:
                if el.tag == 'attributes':
                    if el.find('divisions') is not None:
                        div = int(el.findtext('divisions'))
                    if el.find('key/fifths') is not None:
                        fifths = int(el.findtext('key/fifths'))
                    if el.find('time/beats') is not None:
                        try:
                            beats = int(el.findtext('time/beats').split('+')[0])
                            beat_type = int(el.findtext('time/beat-type'))
                        except (ValueError, TypeError):
                            pass
                    continue
                if el.tag == 'backup':
                    cur -= int(el.findtext('duration'))
                    continue
                if el.tag == 'forward':
                    cur += int(el.findtext('duration'))
                    mx = max(mx, cur)
                    continue
                if el.tag != 'note':
                    continue
                k += 1
                nid = f'P{pi}-{mnum}-{k}'
                is_chord = el.find('chord') is not None
                is_grace = el.find('grace') is not None
                dur = int(el.findtext('duration') or 0)
                pos = last_start if is_chord else cur
                if not is_chord and not is_grace:
                    last_start = cur
                    cur += dur
                    mx = max(mx, cur)
                elif is_chord and not is_grace:
                    mx = max(mx, pos + dur)
                staff = int(el.findtext('staff') or 1)
                if is_chord:
                    staff = chord_staff   # 段をまたぐ和音は、最初の音の段にそろえる(fmt3x と同じ)
                else:
                    chord_staff = staff
                voice = el.findtext('voice') or '1'
                p = el.find('pitch')
                if p is None:
                    continue
                step, alter, octave = p.findtext('step'), int(round(float(p.findtext('alter') or 0))), int(p.findtext('octave'))
                midi = _midi(step, alter, octave)
                ties = {t.get('type') for t in el.findall('tie')} | {t.get('type') for t in el.findall('notations/tied')}
                stime = start + pos * tpqn // div
                dticks = dur * tpqn // div
                tkey = (staff, voice, midi)
                if 'stop' in ties and tkey in open_ties and not is_grace:
                    # タイで続く音: 最初の音の長さに足し、自分は音符にしない
                    first = notes[open_ties[tkey]]
                    first['dur_ticks'] = stime + dticks - first['stime']
                    if 'start' not in ties:
                        open_ties.pop(tkey)
                    continue
                orn = ''
                orn_notes = [_name(step, alter, octave)]
                for o in el.findall('notations/ornaments/*'):
                    code = ORN.get(o.tag)
                    if code:
                        orn = code
                if orn:
                    # 主音, 上の音, 下の音(accidental-mark は考慮しない簡略化)
                    orn_notes += [_neighbor(step, octave, +1, fifths)[0], _neighbor(step, octave, -1, fifths)[0]]
                arp = el.find('notations/arpeggiate') is not None
                fer = el.find('notations/fermata') is not None
                ornament = f"{orn or 'N'}.{'Arp1' if arp else ''}.{'Fer' if fer else ''}"
                kind = 'short-app' if is_grace else 'chord'
                n = dict(note_id=nid, stime=stime, bar=int(mnum) if mnum.lstrip('-').isdigit() else mk, staff=staff,
                         voice=int(voice) - 1 if voice.isdigit() else 0, dur_ticks=0 if is_grace else dticks,
                         pitch_name=orn_notes[0], pitch=midi, ornament=ornament if ornament != 'N..' else '',
                         ornament_notes=','.join(orn_notes), kind=kind, order=0, measure=mnum)
                notes[nid] = n
                if is_grace:
                    pending_grace.append(nid)
                elif pending_grace:
                    # 前打音の演奏順: 主音の前に並べる(主音と同じ楽譜時刻)
                    for o, g in enumerate(pending_grace):
                        notes[g]['stime'] = stime
                        notes[g]['order'] = o
                    n['order'] = len(pending_grace)
                    pending_grace = []
                if 'start' in ties and not is_grace:
                    open_ties[tkey] = nid
            for g in pending_grace:   # 小節の終わりに残った装飾音符は後打音とみなす
                notes[g]['kind'] = 'after-note'
            length = mx * tpqn // div
            if pi == 1:
                measures.append(dict(number=mnum, index=mk, start=start, length=length, beats=beats,
                                     beat_type=beat_type, fifths=fifths))
            start += length
    # 共有音: 同じ楽譜時刻・同じ音高で、別の声部にある通常の音(段が違うこともある。演奏では1回だけ弾かれる)
    groups = {}
    for i, n in notes.items():
        if n['kind'] == 'chord':
            groups.setdefault((n['stime'], n['pitch']), []).append(i)
    dups = [g for g in groups.values() if len(g) > 1]
    return tpqn, notes, dups, measures


def load_musicxml(path, grace=True):
    """load_fmt3x と同じ形 (TPQN, 音符の辞書, 共有音の組) を返す"""
    tpqn, notes, dups, _ = read(path)
    if not grace:
        notes = {i: n for i, n in notes.items() if n['kind'] == 'chord'}
    return tpqn, notes, dups


def colored(path, colors):
    """音符ID -> 色(#RRGGBB)の辞書に従って note 要素に color 属性を付けた MusicXML の文字列"""
    tree = ET.parse(path)
    root = tree.getroot()
    for pi, part in enumerate(root.findall('part'), 1):
        for mk, m in enumerate(part.findall('measure'), 1):
            mnum = _measure_number(m, mk)
            for k, el in enumerate(m.findall('note'), 1):
                c = colors.get(f'P{pi}-{mnum}-{k}')
                if c:
                    el.set('color', c)
                    for sub in ('stem', 'notehead', 'accidental'):
                        for x in el.findall(sub):
                            x.set('color', c)
    return ET.tostring(root, encoding='unicode')
