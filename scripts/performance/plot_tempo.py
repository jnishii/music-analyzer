#!/usr/bin/env python3
"""音符ごとの表から、局所テンポの比較図と、1演奏の強さ・タイミングの図を描く

使い方: python3 scripts/performance/plot_tempo.py [--tables output/note_tables] [--beats-per-bar 4] 演奏名 [演奏名 ...]
  図は <tables>/plots/ に保存する。2枚目の詳細図は先頭の演奏について描く。
依存: pandas, matplotlib
"""
import argparse
import os

import matplotlib
import pandas as pd

matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('perfs', nargs='+')
ap.add_argument('--tables', default='output/note_tables')
ap.add_argument('--beats-per-bar', type=float, default=4, help='1小節の四分音符の数(4/4なら4, 3/4なら3)')
a = ap.parse_args()

for fam in ['Hiragino Sans', 'Noto Sans CJK JP', 'IPAexGothic']:
    if any(fam == f.name for f in matplotlib.font_manager.fontManager.ttflist):
        plt.rcParams['font.family'] = fam
        break
plt.rcParams.update({'font.size': 10, 'axes.edgecolor': '#bdbcb6', 'axes.labelcolor': '#52514e',
                     'xtick.color': '#52514e', 'ytick.color': '#52514e', 'axes.grid': True,
                     'grid.color': '#e9e8e4', 'axes.spines.top': False, 'axes.spines.right': False,
                     'figure.facecolor': '#fcfcfb', 'axes.facecolor': '#fcfcfb'})
COLORS = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948']
outdir = os.path.join(a.tables, 'plots')
os.makedirs(outdir, exist_ok=True)
dfs = {p: pd.read_csv(os.path.join(a.tables, 'notes', p + '.csv')) for p in a.perfs}
xpos = lambda d: d['beat'] / a.beats_per_bar + 1

fig, ax = plt.subplots(figsize=(11, 4.2))
for (p, d), c in zip(dfs.items(), COLORS):
    d = d.dropna(subset=['local_tempo_bpm']).groupby('beat', as_index=False)['local_tempo_bpm'].first()
    ax.plot(xpos(d), d['local_tempo_bpm'], color=c, lw=2, label=p)
ax.set_xlabel('小節')
ax.set_ylabel('局所テンポ(四分音符/分)')
ax.legend(frameon=False, ncol=2)
fig.tight_layout()
fig.savefig(os.path.join(outdir, 'tempo_' + '_'.join(a.perfs[:1]) + f'_{len(a.perfs)}.png'), dpi=150)

p0 = a.perfs[0]
d = dfs[p0]
d = d[d.status.isin(['ok', 'shared'])]
fig, axs = plt.subplots(2, 1, figsize=(11, 6.4), sharex=True)
for staff, c, lab in [(1, COLORS[0], '上段'), (2, COLORS[1], '下段')]:
    s = d[d.staff == staff].sort_values('beat')
    x = xpos(s)
    for ax, col in zip(axs, ['velocity', 'timing_dev_ms']):
        ax.scatter(x, s[col], s=10, color=c, alpha=0.35, linewidths=0)
        ax.plot(x, s[col].rolling(9, center=True, min_periods=3).mean(), color=c, lw=2, label=lab)
axs[0].set_ylabel('ベロシティ')
axs[1].set_ylabel('テンポ曲線からのずれ(ms)')
axs[1].axhline(0, color='#8a8983', lw=1)
axs[1].set_xlabel('小節')
axs[0].legend(frameon=False, ncol=2)
axs[0].set_title(f'{p0}: 強さ(上)とタイミングのずれ(下) 点=各音、線=9音の移動平均', loc='left')
fig.tight_layout()
fig.savefig(os.path.join(outdir, f'detail_{p0}.png'), dpi=150)
print('->', outdir)
