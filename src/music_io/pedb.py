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

DEFAULT_DATA_DIR = os.path.expanduser('~/git/6-python/data/pedb2_v0.0.1.b')


def data_dir(arg=None):
    """データの場所: 引数 > 環境変数 PEDB_DIR > 既定値"""
    return os.path.expanduser(arg or os.environ.get('PEDB_DIR') or DEFAULT_DATA_DIR)


def list_performances(root):
    return sorted(os.path.basename(d) for d in glob.glob(os.path.join(root, '*-p[0-9]')) if os.path.isdir(d))


def piece_of(perf):
    """演奏名から曲名(例: moz-snt331-1-s-p1 -> moz-snt331-1)"""
    return perf.rsplit('-', 2)[0]


def player_of(perf):
    return perf.rsplit('-', 1)[1]


def perf_files(root, perf):
    d = os.path.join(root, perf)
    one = lambda pat: (glob.glob(os.path.join(d, pat)) or [None])[0]
    return dict(dir=d, wav=one('*.wav'), mid=one('*.mid'), fmt3x=one('*fmt3x.txt'), match=one('*auto_match*.txt'))


NAMES = ['C', 'C#', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'G#', 'A', 'Bb', 'B']


def pitch_name(p):
    """MIDI番号 -> 音名(例: 60 -> C4)"""
    p = int(p)
    return f'{NAMES[p % 12]}{p // 12 - 1}'


PC = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}


def midi_pitch(name):
    m = re.match(r'([A-G])([#b]*)(-?\d+)$', name)
    if not m:
        return None
    acc = m.group(2).count('#') - m.group(2).count('b')
    return 12 * (int(m.group(3)) + 1) + PC[m.group(1)] + acc


GRACE_TYPES = ('short-app', 'after-note')


def load_fmt3x(path, grace=False):
    """戻り値: (TPQN, {音符ID: 音符情報}, 共有音のグループのリスト)

    grace=True のときは装飾音符(short-app: 前打音, after-note: 後打音)も含める。
    装飾音符は主音と同じ楽譜時刻を持ち、order(6列目)が同じ時刻・同じ声部の中での演奏順を表す。
    音符情報の kind は 'chord'(通常の音)か装飾音符の種類。
    """
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
        if len(c) < 9 or not (c[6] == 'chord' or (grace and c[6] in GRACE_TYPES)):
            continue
        stime, bar, staff, voice, order = int(c[0]), int(c[1]), int(c[2]), int(c[3]), int(c[5])
        dur, n = int(c[7]), int(c[8])
        pitches, nots, ids = c[9:9 + n], c[9 + n:9 + 2 * n], c[9 + 2 * n:9 + 3 * n]
        for p, o, i in zip(pitches, nots, ids):
            main = p.split(',')[0]  # 装飾音は "B4,C5,A4" のように並ぶ。先頭が主音
            notes[i] = dict(note_id=i, stime=stime, bar=bar, staff=staff, voice=voice,
                            dur_ticks=dur, pitch_name=main, pitch=midi_pitch(main),
                            ornament=o if o != 'N..' else '', ornament_notes=p, kind=c[6], order=order)
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


def write_match(path, perf, missing, score=None):
    """load_match の形の対応付けを、matchファイルと同じ形式で書き出す(人が確認・修正できるように)。
    score を渡すと、弾かれなかった楽譜音の楽譜時刻も書く"""
    with open(path, 'w', encoding='utf-8') as f:
        f.write('//Version: ScorePerfmMatch_v170104\n')
        f.write('// 自動の対応付け(music-analysis performance_analysis/align.py)\n')
        for p in sorted(perf, key=lambda p: p['on']):
            f.write('\t'.join(map(str, [p['id'], f"{p['on']:.6f}", f"{p['off']:.6f}", p['pitch_name'], p['vel'],
                                        p.get('offvel', 0), 0, p['status'], p['stime'], p['note_id'], p['err'], '-'])) + '\n')
        for i in missing:
            st = score[i]['stime'] if score and i in score else -1
            f.write(f'//Missing {st}\t{i}\n')
