"""楽譜を手がかりにした採譜の補正の提案(統合 GUI のサーバー側)

採譜の結果(人の修正を含む)を楽譜と対応付け、違いごとに「録音の根拠」と「提案」を付ける。
提案は自動では当てはめない。PEDB での検証(scripts/transcribe/eval_score_autofix.py)で、どの規則も
黙って直せるほど正しくなかったため(楽譜にない音の追加 73%、削除 79〜91%、TEST)。人が1件ずつ、
またはまとめて受け入れる。

  抜け       楽譜から予測した時刻の ±100ms で、その音高の発音の手がかりを探す(refine.find_onset)。
             足す音の発音時刻・ベロシティ(方法 A)・離鍵を提案する。手がかりの強さを信頼度とする
  余分       再検出(同じ音高の直前の音が 30ms 以内に終わっている)/ 倍音(1〜2 オクターブ・12度下の音が ±50ms)/
             その他 に分け、削除を提案する。信頼度は検証での正しさ
  弾き間違い 楽譜の音高と弾いた音高のどちらに発音の手がかりが強いかを比べ、楽譜の音高への変更を提案する
  リズム     提案なし(演奏の表現として扱う)
"""
import numpy as np

from alignment import align, notation
from music_io import musicxml

from .. import refine

# 削除の提案が正しかった割合(PEDB の TEST、scripts/transcribe/eval_score_autofix.py)
DELETE_PRECISION = {'redetect': 0.88, 'harmonic': 0.905, 'other': 0.786}
DELETE_LABEL = {'redetect': '再検出(1つの音が割れた)', 'harmonic': '倍音', 'other': 'その他'}
WEAK = 0.02   # これ未満は録音に発音の手がかりがない(refine.py と同じ)


def analyze(xml_path, notes, out):
    """notes: [{id, on, off, pitch, vel}], out: 採譜モデルのフレームごとの出力。
    戻り値: dict(diffs, measures, original_xml, performance_xml)"""
    if not notes:
        raise ValueError('音がありません')
    arr = np.array([(n['on'], n['off'], n['pitch'], n['vel']) for n in notes], float)
    order = np.lexsort((arr[:, 2], arr[:, 0]))   # align.align の演奏音の番号はこの並び
    sk = arr[order]
    nid = lambda row: notes[int(order[row])]['id']
    tpqn, score, dups = musicxml.load_musicxml(xml_path)
    rows, miss = align.align(tpqn, score, dups, arr)
    diffs, place, orig_kind, measures, tpqn, tm = notation.compare(xml_path, rows, miss)
    beat_to_time = lambda b: float(np.interp(b, tm.b, tm.t)) if len(tm.b) > 1 else 0.0

    out_diffs = []
    for d in diffs:
        item = dict(kind=d['kind'], measure=d['measure'], beat=float(d['beat']), desc=d['desc'], score_ids=d['ids'])
        if d['kind'] == 'missing':
            p = int(d['pitch'])
            pred = beat_to_time(d['score_beat'])
            t, e = refine.find_onset(out, p, pred, 0.1)
            strong = e >= WEAK
            t = t if strong else pred
            nxt = sk[(sk[:, 2] == p) & (sk[:, 0] > t + 0.03), 0]
            off = refine.find_offset(out, p, t, float(nxt.min()) if len(nxt) else None) if strong else t + 0.25
            vel = refine.velocity_at(out, p, t) if strong else None
            item.update(key='missing:' + d['ids'][0], time=t, confidence=float(e),
                        reason=f'録音の発音の手がかり {e:.2f}' + ('' if strong else '(ほぼなし)'),
                        suggestion=dict(action='add', label='楽譜の音を足す',
                                        note=dict(on=t, off=float(off), pitch=p, vel=int(round(vel or 40)),
                                                  weak=not strong)))
        elif d['kind'] == 'extra':
            j = int(d['row'])
            on, pch = sk[j, 0], sk[j, 2]
            redet = np.any((sk[:, 2] == pch) & (sk[:, 0] < on - 0.03) & (sk[:, 1] >= on - 0.03))
            harm = np.any(np.isin(pch - sk[:, 2], [12, 19, 24]) & (np.abs(sk[:, 0] - on) <= 0.05))
            cat = 'redetect' if redet else ('harmonic' if harm else 'other')
            item.update(key='extra:' + nid(j), time=float(on), note_id=nid(j), confidence=DELETE_PRECISION[cat],
                        reason=f'{DELETE_LABEL[cat]}(検証で削除が正しかった割合 {DELETE_PRECISION[cat]:.0%})',
                        suggestion=dict(action='delete', label='この音を消す'))
        elif d['kind'] == 'pitch':
            j = int(d['row'])
            on, played = sk[j, 0], int(sk[j, 2])
            sp = score[d['ids'][0]]['pitch']
            e_play = refine.find_onset(out, played, on, 0.05)[1]
            e_score = refine.find_onset(out, sp, on, 0.05)[1]
            conf = e_score / (e_score + e_play + 1e-9)
            item.update(key='pitch:' + d['ids'][0], time=float(on), note_id=nid(j), confidence=float(conf),
                        reason=f'録音の手がかり 楽譜の音高 {e_score:.2f} / 弾いた音高 {e_play:.2f}',
                        suggestion=dict(action='set_pitch', label='楽譜の音高にする', pitch=int(sp)))
        else:   # rhythm
            j = int(d['row'])
            item.update(key='rhythm:' + d['ids'][0], time=float(sk[j, 0]), note_id=nid(j), confidence=None,
                        reason='演奏の表現として扱う(提案なし)', suggestion=None)
        out_diffs.append(item)

    mt = []
    for m in measures:
        b0, b1 = m['start'] / tpqn, (m['start'] + m['length']) / tpqn
        mt.append(dict(n=m['number'], t0=beat_to_time(b0), t1=beat_to_time(b1)))
    perf_xml = notation.build_performance_score(xml_path, place, measures, tpqn)
    orig_xml = musicxml.colored(xml_path, {i: notation.COLORS[k] for i, k in orig_kind.items()})
    return dict(diffs=out_diffs, measures=mt, original_xml=orig_xml, performance_xml=perf_xml,
                labels=notation.LABELS, colors=notation.COLORS)
