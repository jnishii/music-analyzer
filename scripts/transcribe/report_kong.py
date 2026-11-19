#!/usr/bin/env python3
"""採譜モデル(Kong ら)の推定を、打鍵タイミング・打鍵の終わり・ベロシティ・ペダルについて詳しく評価する

前提: scripts/transcribe/eval_transcription.py を実行して <trans>/kong/<演奏名>.npz ができていること
使い方: python3 scripts/transcribe/report_kong.py [--data ...] [--trans output/transcription]
出力: <trans>/kong_report/ に report.md, 図(PNG), notes.csv(一致した音ごとの値)
"""
import argparse
import csv
import os
import sys

import matplotlib
import numpy as np

matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
from music_io import midi, pedb  # noqa: E402
from piano_transcribe import evaluate as ev  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('--data')
ap.add_argument('--trans', default='output/transcription')
a = ap.parse_args()
root = pedb.data_dir(a.data)
out = os.path.join(a.trans, 'kong_report')
os.makedirs(out, exist_ok=True)
perfs = [p for p in pedb.list_performances(root) if os.path.exists(os.path.join(a.trans, 'kong', p + '.npz'))]

# --- 図の体裁
for fam in ['Hiragino Sans', 'Noto Sans CJK JP', 'IPAexGothic']:
    if any(fam == f.name for f in matplotlib.font_manager.fontManager.ttflist):
        plt.rcParams['font.family'] = fam
        break
INK, INK2, GRID, SURF = '#0b0b0b', '#52514e', '#e9e8e4', '#fcfcfb'
C1, C2, C3 = '#2a78d6', '#eb6834', '#1baf7a'
SEQ = LinearSegmentedColormap.from_list('seq', ['#cde2fb', '#86b6ef', '#3987e5', '#1c5cab', '#0d366b'])
plt.rcParams.update({'font.size': 10, 'axes.edgecolor': '#bdbcb6', 'axes.labelcolor': INK2, 'text.color': INK,
                     'xtick.color': INK2, 'ytick.color': INK2, 'axes.grid': True, 'grid.color': GRID,
                     'axes.spines.top': False, 'axes.spines.right': False, 'figure.facecolor': SURF,
                     'axes.facecolor': SURF, 'lines.linewidth': 2, 'legend.frameon': False,
                     'axes.titlelocation': 'left', 'axes.titlesize': 11})


def save(fig, name):
    fig.tight_layout()
    fig.savefig(os.path.join(out, name), dpi=150)
    plt.close(fig)


def hist(ax, x, bins, color, label=None):
    ax.hist(x, bins=bins, color=color, alpha=0.85, label=label, edgecolor=SURF, linewidth=0.5)


# --- 1. 演奏ごとに集める
PED_THR = [32, 48, 64, 80, 96]
N, FN, FP, CH, PERF = [], [], [], [], []
PED_EV = {'down': [], 'up': []}
for p in perfs:
    f = pedb.perf_files(root, p)
    z = np.load(os.path.join(a.trans, 'kong', p + '.npz'))
    est, epedal = z['notes'].copy(), z['pedal']
    ref = midi.read_midi_notes(f['mid'])
    rped = {t: midi.read_midi_pedal(f['mid'], threshold=t) for t in PED_THR}
    sound = ev.extend_by_pedal(ref, rped[64])
    o = ev.constant_offset(est, ref)
    est[:, :2] -= o
    epedal = epedal - o
    pairs = ev.match_notes(est, ref)
    ie, ir = (np.array(pairs).T if pairs else (np.array([], int), np.array([], int)))
    end = ref[:, 1].max()
    pdown = lambda t, P: bool(((P[:, 0] <= t) & (t < P[:, 1])).any()) if len(P) else False
    # 一致した音
    on_err = (est[ie, 0] - ref[ir, 0]) * 1000
    tt = ref[ir, 0]
    drift = np.polyfit(tt / 60, on_err, 1)[0] if len(tt) > 10 else np.nan  # ms/分
    vr = np.corrcoef(est[ie, 3], ref[ir, 3])[0, 1]
    b1, b0 = np.polyfit(est[ie, 3], ref[ir, 3], 1)
    for e, r in zip(ie, ir):
        N.append(dict(perf=p, piece=pedb.piece_of(p), pitch=ref[r, 2], t=ref[r, 0], vel_true=ref[r, 3], vel_est=est[e, 3],
                      on_err=(est[e, 0] - ref[r, 0]) * 1000, dur_true=ref[r, 1] - ref[r, 0],
                      rel_err=(est[e, 1] - ref[r, 1]) * 1000, snd_err=(est[e, 1] - sound[r, 1]) * 1000,
                      ped_at_release=pdown(ref[r, 1], rped[64]), dur_snd=sound[r, 1] - ref[r, 0]))
    # 抜けた音(正解にあって推定にない)と余分な音(推定にあって正解にない)の分類
    me, mr = set(ie.tolist()), set(ir.tolist())
    for r in range(len(ref)):
        if r in mr:
            continue
        same = est[est[:, 2] == ref[r, 2], 0]
        near = len(same) and np.min(np.abs(same - ref[r, 0])) <= 0.15
        FN.append(dict(perf=p, pitch=ref[r, 2], vel=ref[r, 3], dur=ref[r, 1] - ref[r, 0],
                       kind='時刻のずれ(50〜150ms)' if near else '検出なし'))
    for e in range(len(est)):
        if e in me:
            continue
        t, pch = est[e, 0], est[e, 2]
        rs = ref[ref[:, 2] == pch]
        if len(rs) and np.min(np.abs(rs[:, 0] - t)) <= 0.15:
            kind = '時刻のずれ(50〜150ms)'
        elif len(rs) and ((rs[:, 0] < t) & (t < sound[ref[:, 2] == pch, 1])).any():
            kind = '鳴っている音の再検出'
        elif (np.isin(pch - ref[np.abs(ref[:, 0] - t) <= 0.05, 2], [12, 19, 24])).any():
            kind = '倍音(1〜2オクターブ・12度上)'
        else:
            kind = 'その他'
        FP.append(dict(perf=p, pitch=pch, vel=est[e, 3], kind=kind))
    # 和音内の時間差: 一致した音のうち、正解の発音が 40ms 以内に並ぶ組
    order = np.argsort(ref[ir, 0])
    ir_s, ie_s = ir[order], ie[order]
    for k in range(len(ir_s)):
        for m in range(k + 1, len(ir_s)):
            dt = ref[ir_s[m], 0] - ref[ir_s[k], 0]
            if dt > 0.04:
                break
            CH.append((dt * 1000, (est[ie_s[m], 0] - est[ie_s[k], 0]) * 1000,
                       ref[ir_s[m], 3] - ref[ir_s[k], 3], est[ie_s[m], 3] - est[ie_s[k], 3]))
    # ペダル
    ps = {t: ev.pedal_scores(epedal, rped[t], end)['f1'] for t in PED_THR}
    for kind, col in [('down', 0), ('up', 1)]:
        R, E = rped[64][:, col], epedal[:, col] if len(epedal) else np.array([])
        used = set()
        hit = 0
        for t in R:
            if not len(E):
                break
            j = int(np.argmin(np.abs(E - t)))
            if abs(E[j] - t) <= 0.3 and j not in used:
                used.add(j)
                hit += 1
                PED_EV[kind].append((E[j] - t) * 1000)
        PED_EV.setdefault(kind + '_n', []).append((hit, len(R), len(E)))
    s, _ = ev.note_scores(est, ref)
    PERF.append(dict(perf=p, piece=pedb.piece_of(p), dur=end, n_ref=len(ref), f1=s['f1'], precision=s['precision'],
                     recall=s['recall'], const_offset_ms=o * 1000, drift_ms_per_min=drift,
                     on_mae=np.mean(np.abs(on_err)), vel_r=vr, vel_slope=b1, vel_icpt=b0,
                     vel_mae_raw=np.mean(np.abs(est[ie, 3] - ref[ir, 3])),
                     ped_ratio=float(np.mean([pdown(t, rped[64]) for t in np.arange(0, end, 0.1)])),
                     **{f'ped_f1_{t}': ps[t] for t in PED_THR}))
    print(p, flush=True)

col = lambda rows, k: np.array([r[k] for r in rows])
oe = col(N, 'on_err')
pitch, vt, vest = col(N, 'pitch'), col(N, 'vel_true'), col(N, 'vel_est')
piece = col(N, 'piece')
pieces = sorted(set(piece))
pf = {k: col(PERF, k) for k in PERF[0] if k not in ('perf', 'piece')}
md = []
pct = lambda x, q: float(np.percentile(x, q))

# --- 2. 打鍵タイミング
ax_ = np.abs(oe)
md += ['## 1. 打鍵タイミング(発音時刻)', '',
       f'一致した音 {len(oe):,} 音(100演奏)。演奏ごとの一定のずれ(録音とMIDIの時計の差)を差し引いた後の誤差。', '',
       '| 指標 | 値 |', '|---|---|',
       f'| 絶対誤差の中央値 | {np.median(ax_):.1f} ms |', f'| 絶対誤差の平均 | {ax_.mean():.1f} ms |',
       f'| 90% 点 / 99% 点 | {pct(ax_, 90):.1f} ms / {pct(ax_, 99):.1f} ms |',
       f'| 5 ms 以内 / 10 ms 以内 / 20 ms 以内 | {(ax_ <= 5).mean():.1%} / {(ax_ <= 10).mean():.1%} / {(ax_ <= 20).mean():.1%} |',
       f'| 誤差の標準偏差(符号つき) | {oe.std():.1f} ms |',
       f'| 一定のずれ(演奏ごと) | {pf["const_offset_ms"].min():.0f} 〜 {pf["const_offset_ms"].max():.0f} ms(中央値 {np.median(pf["const_offset_ms"]):.0f} ms) |',
       f'| 演奏中の時刻の進み(誤差の傾き) | 中央値 {np.median(pf["drift_ms_per_min"]):+.2f} ms/分、最大 {np.max(np.abs(pf["drift_ms_per_min"])):.2f} ms/分 |',
       '']
fig, axs = plt.subplots(1, 2, figsize=(11, 3.8), gridspec_kw=dict(width_ratios=[2, 1]))
hist(axs[0], np.clip(oe, -30, 30), np.arange(-30, 31, 1), C1)
axs[0].set_xlabel('推定 − 正解 (ms)')
axs[0].set_ylabel('音の数')
axs[0].set_title(f'発音時刻の誤差(中央値 {np.median(ax_):.1f} ms、±30ms の外は端に寄せた)')
hist(axs[1], pf['const_offset_ms'], 20, C1)
axs[1].set_xlabel('一定のずれ (ms)')
axs[1].set_ylabel('演奏の数')
axs[1].set_title('演奏ごとの一定のずれ(録音とMIDI)')
save(fig, 'onset_error.png')

# 音域・強さ別
bins_p = [21, 36, 48, 60, 72, 84, 109]
bins_v = [0, 30, 45, 60, 75, 90, 128]
fig, axs = plt.subplots(1, 2, figsize=(11, 3.6), sharey=True)
rows_tab = []
for ax, x, bins, lab, name in [(axs[0], pitch, bins_p, '音高(MIDI番号)', '音域'), (axs[1], vt, bins_v, '正解のベロシティ', '強さ')]:
    med, p90, labels = [], [], []
    for lo, hi in zip(bins[:-1], bins[1:]):
        s = (x >= lo) & (x < hi)
        med.append(np.median(ax_[s]))
        p90.append(pct(ax_[s], 90))
        labels.append(f'{lo}–{hi - 1}')
        rows_tab.append(f'| {name} {lo}–{hi - 1} | {s.sum():,} | {med[-1]:.1f} | {p90[-1]:.1f} |')
    xs = np.arange(len(labels))
    ax.bar(xs - 0.2, med, 0.36, color=C1, label='中央値')
    ax.bar(xs + 0.2, p90, 0.36, color=C2, label='90% 点')
    ax.set_xticks(xs, labels)
    ax.set_xlabel(lab)
    ax.grid(axis='x', visible=False)
axs[0].set_ylabel('発音時刻の絶対誤差 (ms)')
axs[0].legend()
axs[0].set_title('音域・強さ別の発音時刻の誤差')
save(fig, 'onset_by_pitch_velocity.png')
md += ['音域・強さ別(図 onset_by_pitch_velocity.png):', '', '| 区分 | 音の数 | 中央値 (ms) | 90% 点 (ms) |', '|---|---|---|---|'] + rows_tab + ['']

# 和音内の時間差
ch = np.array(CH)
rc = np.corrcoef(ch[:, 0], ch[:, 1])[0, 1]
big = ch[:, 0] >= 10
md += ['和音内の時間差(正解の発音が 40ms 以内に並ぶ音の組、図 chord_asynchrony.png):', '',
       f'- 組の数 {len(ch):,}。正解の時間差と推定の時間差の相関 {rc:.2f}、差の誤差の中央値 {np.median(np.abs(ch[:, 1] - ch[:, 0])):.1f} ms',
       f'- 正解の時間差が 10ms 以上の組({big.sum():,} 組): 相関 {np.corrcoef(ch[big, 0], ch[big, 1])[0, 1]:.2f}、'
       f'どちらの音が先かの一致 {(np.sign(ch[big, 1]) == np.sign(ch[big, 0])).mean():.1%}', '']
dv_t, dv_e = ch[:, 2], ch[:, 3]
bigv = np.abs(dv_t) >= 10
rv = np.corrcoef(dv_t, dv_e)[0, 1]
md += ['和音内のベロシティの差(同じ組。旋律と伴奏の強さの差などの分析に相当、図 chord_velocity.png):', '',
       f'- 正解の差と推定の差の相関 {rv:.2f}、差の誤差の中央値 {np.median(np.abs(dv_e - dv_t)):.1f}',
       f'- 正解の差が 10 以上の組({bigv.sum():,} 組): どちらが強いかの一致 {(np.sign(dv_e[bigv]) == np.sign(dv_t[bigv])).mean():.1%}、'
       f'推定の差 / 正解の差 の中央値 {np.median(dv_e[bigv] / dv_t[bigv]):.2f}', '']
fig, ax = plt.subplots(figsize=(5.2, 4.6))
hb = ax.hexbin(dv_t, dv_e, gridsize=40, cmap=SEQ, bins='log', mincnt=1, extent=(-50, 50, -50, 50))
ax.plot([-50, 50], [-50, 50], color=INK2, lw=1)
ax.set_xlabel('正解のベロシティの差')
ax.set_ylabel('推定のベロシティの差')
ax.set_title(f'和音内のベロシティの差(相関 {rv:.2f})')
fig.colorbar(hb, ax=ax, label='組の数(対数)')
save(fig, 'chord_velocity.png')
fig, ax = plt.subplots(figsize=(5.2, 4.6))
hb = ax.hexbin(ch[:, 0], np.clip(ch[:, 1], -20, 60), gridsize=40, cmap=SEQ, bins='log', mincnt=1, extent=(0, 40, -20, 60))
ax.plot([0, 40], [0, 40], color=INK2, lw=1)
ax.set_xlabel('正解の時間差 (ms)')
ax.set_ylabel('推定の時間差 (ms)')
ax.set_title(f'和音内の時間差(相関 {rc:.2f})')
fig.colorbar(hb, ax=ax, label='組の数(対数)')
save(fig, 'chord_asynchrony.png')

# 抜けた音・余分な音
nref, nest = sum(r['n_ref'] for r in PERF), len(N) + len(FP)
md += ['## 2. 抜けた音・余分な音', '',
       f'正解 {nref:,} 音のうち一致 {len(N):,}(再現率 {len(N) / nref:.1%})、推定 {nest:,} 音のうち一致(適合率 {len(N) / nest:.1%})。',
       f'演奏ごとの F1: 中央値 {np.median(pf["f1"]):.3f}、最小 {pf["f1"].min():.3f}。', '',
       '| 種類 | 内訳 | 音の数 | 割合 |', '|---|---|---|---|']
for name, rows in [('抜けた音', FN), ('余分な音', FP)]:
    kinds = col(rows, 'kind')
    for k in sorted(set(kinds), key=lambda k: -(kinds == k).sum()):
        md.append(f'| {name} | {k} | {(kinds == k).sum():,} | {(kinds == k).mean():.0%} |')
fnv = col(FN, 'vel')
fn_det = col(FN, 'kind') == '検出なし'
md += ['', '強さ別の再現率(図 recall_by_velocity.png):', '', '| 正解のベロシティ | 正解の音 | 再現率 |', '|---|---|---|']
rec = []
for lo, hi in zip(bins_v[:-1], bins_v[1:]):
    n_hit = ((vt >= lo) & (vt < hi)).sum()
    n_miss = ((fnv >= lo) & (fnv < hi)).sum()
    rec.append(n_hit / (n_hit + n_miss))
    md.append(f'| {lo}–{hi - 1} | {n_hit + n_miss:,} | {rec[-1]:.1%} |')
md.append('')
fig, ax = plt.subplots(figsize=(6, 3.4))
xs = np.arange(len(rec))
ax.bar(xs, rec, 0.6, color=C1)
for x, r in zip(xs, rec):
    ax.text(x, r + 0.005, f'{r:.1%}', ha='center', va='bottom', fontsize=9, color=INK2)
ax.set_xticks(xs, [f'{lo}–{hi - 1}' for lo, hi in zip(bins_v[:-1], bins_v[1:])])
ax.set_ylim(min(rec) - 0.08, 1.0)
ax.set_xlabel('正解のベロシティ')
ax.set_ylabel('再現率')
ax.grid(axis='x', visible=False)
ax.set_title('強さ別の再現率(弱い音ほど検出されにくい)')
save(fig, 'recall_by_velocity.png')

# --- 3. 打鍵の終わり
rel, snd = col(N, 'rel_err'), col(N, 'snd_err')
pr = col(N, 'ped_at_release').astype(bool)
dur, dsnd = col(N, 'dur_true'), col(N, 'dur_snd')
ok_rel = np.abs(rel) <= np.maximum(50, 200 * dur)
ok_snd = np.abs(snd) <= np.maximum(50, 200 * dsnd)
md += ['## 3. 打鍵の終わり(離鍵)', '',
       'Kong のモデルは「音が止んだ時刻」(ペダルを踏んでいればペダルを離すまで伸びる)を推定する。',
       '正解は MIDI の離鍵(鍵を離した時刻)と、それをペダル(CC64≥64)で伸ばした「音の終わり」の2通り。',
       '「許容内」は mir_eval の基準 |誤差| ≤ max(50ms, 正解の音長の20%)。', '',
       '| 離鍵時のペダル | 音の数 | 正解 = 離鍵: 中央値 / 許容内 | 正解 = 音の終わり: 中央値 / 許容内 |', '|---|---|---|---|']
for name, s in [('離していた', ~pr), ('踏んでいた', pr), ('全体', np.ones_like(pr))]:
    md.append(f'| {name} | {s.sum():,} | {np.median(np.abs(rel[s])):.0f} ms / {ok_rel[s].mean():.0%} | '
              f'{np.median(np.abs(snd[s])):.0f} ms / {ok_snd[s].mean():.0%} |')
md += ['', f'符号つきの中央値(ペダルを離していた音、推定 − 離鍵): {np.median(rel[~pr]):+.0f} ms', '']
fig, axs = plt.subplots(1, 2, figsize=(11, 3.8), sharey=False)
b = np.arange(-300, 301, 10)
hist(axs[0], np.clip(rel[~pr], -300, 300), b, C1)
axs[0].set_title(f'ペダルを離していた音: 推定 − 離鍵(中央値 {np.median(rel[~pr]):+.0f} ms)')
axs[0].set_xlabel('ms(±300ms の外は端に寄せた)')
axs[0].set_ylabel('音の数')
hist(axs[1], np.clip(rel[pr], -300, 3000), np.arange(-300, 3001, 50), C2, label='正解 = 離鍵')
hist(axs[1], np.clip(snd[pr], -300, 3000), np.arange(-300, 3001, 50), C1, label='正解 = ペダルで伸ばした音の終わり')
axs[1].set_title('ペダルを踏んでいた音')
axs[1].set_xlabel('推定 − 正解 (ms、-300〜3000ms の外は端に寄せた)')
axs[1].legend()
save(fig, 'offset_error.png')

# --- 4. ベロシティ
r_all = np.corrcoef(vest, vt)[0, 1]
z_r = np.median(pf['vel_r'])
md += ['## 4. ベロシティ', '',
       f'- 補正なし: 相関 {r_all:.2f}、平均絶対誤差 {np.abs(vest - vt).mean():.1f}、平均の差(推定 − 正解) {np.mean(vest - vt):+.1f}',
       f'- 演奏内の相関(velocity_z の精度に相当): 中央値 {z_r:.2f}、範囲 {pf["vel_r"].min():.2f}〜{pf["vel_r"].max():.2f}',
       f'- 演奏ごとの線形の当てはめ(正解 ≈ a + b×推定): b の中央値 {np.median(pf["vel_slope"]):.2f}'
       f'(範囲 {pf["vel_slope"].min():.2f}〜{pf["vel_slope"].max():.2f})', '']


# 補正方法の比較(1曲抜きの交差検証)
def feats(kind, v, p):
    z = (p - 64) / 20
    one = np.ones_like(v)
    return {'線形': [one, v], '線形+音高': [one, v, z, z * z, v * z], '2次': [one, v, v * v],
            '2次+音高': [one, v, v * v, z, z * z, v * z]}[kind]


cal = {}
perf_arr = col(N, 'perf')
for kind in ['線形', '線形+音高', '2次', '2次+音高']:
    pred = np.empty_like(vt)
    for pc in pieces:
        te = piece == pc
        X = np.column_stack(feats(kind, vest, pitch))
        beta = np.linalg.lstsq(X[~te], vt[~te], rcond=None)[0]
        pred[te] = X[te] @ beta
    pred = np.clip(pred, 1, 127)
    cal[kind] = pred
# 参考: 演奏ごとに正解で当てはめた線形補正(上限の目安。実際には使えない)
orc = np.empty_like(vt)
for p in perfs:
    s = perf_arr == p
    b1, b0 = np.polyfit(vest[s], vt[s], 1)
    orc[s] = np.clip(b0 + b1 * vest[s], 1, 127)
md += ['補正方法の比較(1曲抜きの交差検証。同じ曲の別演奏も学習から除く、図 velocity_calibration.png):', '',
       '| 補正 | 平均絶対誤差 | 相関 | 演奏内の相関の中央値 |', '|---|---|---|---|']


def within_r(pred):
    return np.median([np.corrcoef(pred[perf_arr == p], vt[perf_arr == p])[0, 1] for p in perfs])


md.append(f'| なし | {np.abs(vest - vt).mean():.1f} | {r_all:.3f} | {within_r(vest):.3f} |')
for k, v in cal.items():
    md.append(f'| {k} | {np.abs(v - vt).mean():.2f} | {np.corrcoef(v, vt)[0, 1]:.3f} | {within_r(v):.3f} |')
md += [f'| (参考)演奏ごとに正解で線形補正 | {np.abs(orc - vt).mean():.2f} | {np.corrcoef(orc, vt)[0, 1]:.3f} | {within_r(orc):.3f} |', '']

fig, axs = plt.subplots(1, 3, figsize=(13, 4))
hb = axs[0].hexbin(vest, vt, gridsize=45, cmap=SEQ, bins='log', mincnt=1, extent=(0, 127, 0, 127))
axs[0].plot([0, 127], [0, 127], color=INK2, lw=1)
xx = np.linspace(10, 120, 50)
b1, b0 = np.polyfit(vest, vt, 1)
axs[0].plot(xx, b0 + b1 * xx, color=C2, lw=2, label=f'線形の当てはめ 正解 ≈ {b0:.1f} + {b1:.2f}×推定')
axs[0].set_xlabel('推定のベロシティ')
axs[0].set_ylabel('正解のベロシティ')
axs[0].set_title(f'ベロシティ(補正なし、相関 {r_all:.2f})')
axs[0].legend(loc='upper left', fontsize=8)
fig.colorbar(hb, ax=axs[0], label='音の数(対数)')
# 音域ごとの誤差の偏り
oc = (pitch // 12 - 1).astype(int)
octs = np.arange(oc.min(), oc.max() + 1)
for v, c, lab in [(vest, C2, '補正なし'), (cal['線形'], C3, '線形'), (cal['線形+音高'], C1, '線形+音高')]:
    axs[1].plot(octs, [np.median((v - vt)[oc == o]) if (oc == o).sum() > 50 else np.nan for o in octs],
                color=c, marker='o', ms=5, label=lab)
axs[1].axhline(0, color=INK2, lw=1)
axs[1].set_xlabel('オクターブ(4 = 中央ハのオクターブ)')
axs[1].set_ylabel('推定 − 正解 の中央値')
axs[1].set_title('音域による偏り')
axs[1].legend()
axs[2].scatter(pf['vel_slope'], pf['vel_r'], s=22, color=C1, edgecolor=SURF, linewidth=1)
axs[2].set_xlabel('演奏ごとの線形補正の傾き b')
axs[2].set_ylabel('演奏内の相関')
axs[2].set_title('演奏ごとの違い(1点 = 1演奏)')
save(fig, 'velocity_calibration.png')

# --- 5. ペダル
pr_ok = pf['ped_ratio'] > 0.02
md += ['## 5. ペダル(ダンパー)', '',
       '正解の CC64 は連続値(半ペダルが多い)。Kong は踏む/離すの2値なので、正解をどこで区切るかで結果が変わる。',
       f'ペダルを使っている {pr_ok.sum()} 演奏の、10ms ごとの「踏んでいるか」の一致(F1):', '',
       '| 正解の区切り(CC64 ≥) | F1 の中央値 | F1 の平均 | 最小 |', '|---|---|---|---|']
for t in PED_THR:
    x = pf[f'ped_f1_{t}'][pr_ok]
    md.append(f'| {t} | {np.median(x):.3f} | {x.mean():.3f} | {x.min():.3f} |')
dn, up = np.array(PED_EV['down']), np.array(PED_EV['up'])
hn = np.array(PED_EV['down_n']).sum(0)
hu = np.array(PED_EV['up_n']).sum(0)
md += ['', '踏む・離す時刻(正解の区切り 64、±300ms 以内で最も近い推定と対応、図 pedal.png):', '',
       '| | 正解の回数 | 推定の回数 | 対応した割合 | 時刻の誤差 中央値(符号つき) | 絶対誤差の中央値 / 90% 点 |', '|---|---|---|---|---|---|',
       f'| 踏む | {hn[1]:,} | {hn[2]:,} | {hn[0] / hn[1]:.0%} | {np.median(dn):+.0f} ms | {np.median(np.abs(dn)):.0f} / {pct(np.abs(dn), 90):.0f} ms |',
       f'| 離す | {hu[1]:,} | {hu[2]:,} | {hu[0] / hu[1]:.0%} | {np.median(up):+.0f} ms | {np.median(np.abs(up)):.0f} / {pct(np.abs(up), 90):.0f} ms |', '']
fig, axs = plt.subplots(1, 3, figsize=(13, 3.8))
data_t = [pf[f'ped_f1_{t}'][pr_ok] for t in PED_THR]
bp = axs[0].boxplot(data_t, tick_labels=[f'{t}以上' for t in PED_THR], patch_artist=True, widths=0.5,
                    medianprops=dict(color=INK, lw=2), flierprops=dict(marker='o', ms=4, markerfacecolor=C1, markeredgecolor=SURF))
for b_ in bp['boxes']:
    b_.set(facecolor='#b7d3f6', edgecolor=C1)
axs[0].set_xlabel('正解の区切り(CC64)')
axs[0].set_ylabel('演奏ごとの F1')
axs[0].set_title('踏んでいるかの一致')
axs[0].grid(axis='x', visible=False)
for ax, d, c, lab in [(axs[1], dn, C1, '踏む'), (axs[2], up, C2, '離す')]:
    hist(ax, np.clip(d, -300, 300), np.arange(-300, 301, 10), c)
    ax.set_title(f'{lab}時刻の誤差(中央値 {np.median(d):+.0f} ms)')
    ax.set_xlabel('推定 − 正解 (ms)')
    ax.set_ylabel('回数')
save(fig, 'pedal.png')

# --- 演奏ごとの表
with open(os.path.join(out, 'per_performance.csv'), 'w', newline='') as fo:
    w = csv.DictWriter(fo, fieldnames=list(PERF[0]))
    w.writeheader()
    for r in PERF:
        w.writerow({k: (f'{v:.4f}' if isinstance(v, float) else v) for k, v in r.items()})
with open(os.path.join(out, 'notes.csv'), 'w', newline='') as fo:
    w = csv.DictWriter(fo, fieldnames=list(N[0]))
    w.writeheader()
    for r in N:
        w.writerow({k: (f'{v:.4f}' if isinstance(v, float) else v) for k, v in r.items()})

head = ['# 採譜モデル(Kong ら)の評価', '',
        f'PEDB 2nd edition の {len(perfs)} 演奏({len(pieces)} 曲)、正解は Disklavier の同時記録MIDI。'
        '音の一致は「同じ音高で発音時刻の差が 50ms 以内」(1対1)。図は同じフォルダの PNG。', '']
open(os.path.join(out, 'report.md'), 'w').write('\n'.join(head + md) + '\n')
print('->', out)
