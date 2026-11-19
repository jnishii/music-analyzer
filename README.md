# music-analysis

楽譜と演奏を対応付けて、演奏の「楽譜からのずれ」（テンポ・タイミング・強さ）を分析するためのコード。
最初の題材は CrestMusePEDB 2nd edition（Disklavier による録音と同時記録MIDI、楽譜、対応付けの正解つき）。

## 構成

```
music_analysis/
  pedb.py          PEDB の読み込み(fmt3x, match, WAV, MIDI)。MIDIの読み込みは外部ライブラリ不要
  note_table.py    音符1つを1行とする表(局所テンポ、タイミングのずれ、ベロシティ など)
  transcribe.py    録音+楽譜 -> 発音時刻とベロシティの推定(楽譜情報つき、学習済みモデルなし)、MIDI書き出し
scripts/
  make_note_tables.py    全演奏の表を作る
  eval_transcription.py  録音からの推定を同時記録MIDIと比べて評価する
  transcribe_to_midi.py  録音からMIDIを作る
  plot_tempo.py          テンポ・強さの図
```

## データ

データはリポジトリに入れない（PEDB は登録制の研究用データ）。場所は次の順で決まる。

1. 各スクリプトの `--data` 引数
2. 環境変数 `PEDB_DIR`
3. 既定値 `~/git/6-python/data/pedb2_v0.0.1.b`

出力は `output/`（git の管理外）に書かれる。

## 使い方

```sh
pip install -r requirements.txt

python3 scripts/make_note_tables.py                     # -> output/note_tables/
python3 scripts/plot_tempo.py bac-inv001-o-p1 bac-inv001-s-p1 bac-inv001-o-p2 bac-inv001-s-p2
python3 scripts/eval_transcription.py                   # -> output/eval/ (既定の8演奏)
python3 scripts/transcribe_to_midi.py --perf bac-inv001-o-p1 --out output/bac-inv001-o-p1.est.mid
```

## PEDB のファイルについて

- `*auto_match*.txt` の列: ID, 発音(秒), 離鍵(秒), 音名, 発音ベロシティ, 離鍵ベロシティ, チャンネル,
  対応状態(0=通常, 1=装飾音, -1=余分な音), 楽譜時刻(tick), 楽譜音ID, 誤り(0=正, 1=音高の誤り, 3=余分な音), 飛ばし。
  `//Missing` 行は演奏されなかった楽譜音。
- `*_fmt3x.txt`: `//TPQN` が四分音符あたりの tick 数。`//DuplicateOnsets` は2声部が共有する音(演奏は1回)。
- 録音(WAV)は同時記録MIDIより約30〜60ms早い一定のずれがある(時計の進みの違いは見られない)。

## これまでの評価（2026-11、8演奏）

録音からの推定 vs 同時記録MIDI（`scripts/eval_transcription.py`）

- 発音時刻: 誤差の中央値 約10ms。テンポの安定した曲は 4〜8ms、ルバートの大きい曲は DTW が部分的に外れる。
- 和音内の時間差(数十ms)はほぼ再現できない。
- ベロシティ: 相関 約0.65、平均絶対誤差 約9。

## 今後

- 学習済みの採譜モデル(高解像度ピアノ採譜など)を組み込み、同じ評価で比較する
- 楽譜の分析(和声機能、終止、フレーズ)の列を表に加える
- 楽譜と演奏MIDIの対応付けを自作し、matchファイルの正解で評価する
