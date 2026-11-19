"""旋律の判定

暫定: 上段(staff 1)で各拍位置の最高音を旋律とみなす。
"""


def top_voice(score, staff=1):
    """旋律とみなす音符IDの集合。score: music_io.pedb.load_fmt3x の音符の辞書"""
    top = {}
    for n in score.values():
        if n['staff'] == staff and n['pitch'] is not None:
            top[n['stime']] = max(top.get(n['stime'], -1), n['pitch'])
    return {i for i, n in score.items() if n['staff'] == staff and n['pitch'] is not None and n['pitch'] == top[n['stime']]}
