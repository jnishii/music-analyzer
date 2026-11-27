# music-analyzer

楽譜と演奏を対応付けて、演奏の「楽譜からのずれ」（テンポ・タイミング・強さ）を分析するためのコード。
最初の題材は CrestMusePEDB 2nd edition（Disklavier による録音と同時記録MIDI、楽譜、対応付けの正解つき）。

## 構成

将来リポジトリを分けられるように、役割ごとにパッケージを分けている。

```
src/
  music_io/              共通の入出力(どの部分からも使う。他のパッケージに依存しない)
    midi.py              MIDIの読み書き(音符・ダンパーペダル)。外部ライブラリ不要
    audio.py             WAVの読み込み、標本化周波数の変換
    pedb.py              PEDB の読み込み(fmt3x, match, 演奏名 -> 曲名)
    musicxml.py          MusicXML の読み込み(fmt3x と同じ形。PEDB の全曲で fmt3x と照合済み)、音符に色を付けた書き出し
  alignment/             楽譜と演奏の対応付け(共通の土台)      依存: music_io
    align.py             演奏の音と楽譜の音の対応付け(matchファイルと同じ形で出力)
    notation.py          違い(抜け・余分・弾き間違い・リズム)と、演奏を楽譜に書き起こす。Verovio での描画
  piano_transcribe/      録音 -> 演奏MIDI                      依存: music_io, alignment
    kong.py              高解像度ピアノ採譜モデル(Kong ら)。torch が必要
    refine.py            人が直した音を、録音(モデルのフレームごとの出力)に合わせて詰め直す
    velocity.py          倍音エネルギーによるベロシティ推定(方法 B。比較用)
    editor/              採譜の確認・修正の統合 GUI(録音・MIDI・楽譜の照合と修正。ブラウザで開く)
    guided.py            楽譜情報つきの推定(学習済みモデルなし)、下書きの音の楽譜への割り当て
    evaluate.py          推定と正解の比較(音符単位のF1、発音誤差、ベロシティ、ペダル)
  score_analysis/        楽譜の分析                            依存: music_io
    melody.py            旋律の判定(暫定: 上段の最高音)
  performance_analysis/  楽譜と演奏MIDIの対比                  依存: music_io, score_analysis, alignment
    viewer/              元の楽譜と演奏から書き起こした楽譜を並べて表示する GUI
    note_table.py        音符1つを1行とする表(局所テンポ、タイミングのずれ、ベロシティ など)
scripts/
  transcribe/            editor.py, eval_transcription.py, report_kong.py, transcribe_to_midi.py, save_kong_frames.py,
                         eval_added_velocity.py, eval_refine_onset.py, eval_score_autofix.py
docs/
  kong_evaluation.md     採譜モデルの詳しい評価(打鍵・離鍵・ベロシティ・ペダル)と改善の検討
  alignment_evaluation.md  演奏MIDIと楽譜の対応付けの評価、全自動の表と人手の表の比較
  performance/           score_viewer.py, make_note_tables.py, align_midi.py, eval_alignment.py, compare_note_tables.py,
                         check_musicxml.py, plot_tempo.py
tests/
  test_dependencies.py   上の依存の向きが守られているかの検査
```

採譜と演奏分析のあいだは、コードではなく MIDI ファイルで受け渡す。
分けるときは music_io を小さな共通パッケージとして切り出し、各リポジトリがそれに依存する形にする。

## 準備

### 環境の構築(uv)

[uv](https://docs.astral.sh/uv/) を使う。Python の版は `.python-version`、パッケージの版は `uv.lock` で固定しているので、
別のマシンでも同じ環境になる(uv の導入は macOS なら `brew install uv`)。

```sh
uv sync --locked --all-extras     # .venv を作り、uv.lock の版を入れる。Python 3.12 がなければ uv が取得する
source .venv/bin/activate         # 以下の python3 のコマンドは、この状態で実行する(または uv run python3 ...)
python3 -m pytest tests
```

- `--all-extras` を省くと numpy しか入らない。一部だけなら `--extra analysis --extra dev` のように指定する
- `uv sync` は `.venv` を `uv.lock` の内容に合わせるので、手で追加したパッケージは削除される
- 依存を変えるときは `pyproject.toml` を編集して(または `uv add`)`uv lock` を実行し、`pyproject.toml` と `uv.lock` を一緒にコミットする
- 版を上げるときは `uv lock --upgrade`。上げた後は採譜が動くことを確認する

### 採譜モデルの重み

```sh
# 採譜モデルの重み(約170MB、git の管理外)
mkdir -p models
curl -L -o "models/CRNN_note_F1=0.9677_pedal_F1=0.9186.pth" \
  "https://zenodo.org/record/4034264/files/CRNN_note_F1%3D0.9677_pedal_F1%3D0.9186.pth?download=1"
```

重みの場所は環境変数 `KONG_CHECKPOINT` でも指定できる。torch を使わない部分(guided、演奏分析)は numpy だけで動く。

## データ

データはリポジトリに入れない（PEDB は登録制の研究用データ）。場所は次の順で決まる。

1. 各スクリプトの `--data` 引数
2. 環境変数 `PEDB_DIR`
3. 既定値 `~/git/6-python/data/pedb2_v0.0.1.b`

出力は `output/`（git の管理外）に書かれる。

## 使い方

```sh
# 録音 -> MIDI
python3 scripts/transcribe/transcribe_to_midi.py --method kong --wav 録音.wav --out 推定.mid
python3 scripts/transcribe/transcribe_to_midi.py --method hybrid --perf bac-inv001-o-p1 --out output/bac-inv001-o-p1.est.mid
python3 scripts/transcribe/eval_transcription.py            # 3方式の比較 -> output/transcription/
python3 scripts/transcribe/report_kong.py                   # Kong の詳しい評価 -> output/transcription/kong_report/

# 楽譜と演奏の対比
python3 scripts/performance/make_note_tables.py             # 人手の match から -> output/note_tables/
python3 scripts/performance/make_note_tables.py --source kong   # 録音 -> Kong -> 自動の対応付け -> output/note_tables_kong/
python3 scripts/performance/align_midi.py --midi 演奏.mid --fmt3x 楽譜_fmt3x.txt --out 出力   # 任意の MIDI を対応付け
python3 scripts/performance/eval_alignment.py --source midi --split test    # 対応付けの評価
python3 scripts/performance/compare_note_tables.py output/note_tables_kong  # 自動の表と人手の表の比較
python3 scripts/performance/plot_tempo.py bac-inv001-o-p1 bac-inv001-s-p1 bac-inv001-o-p2 bac-inv001-s-p2

python3 -m pytest tests
```

## 採譜の確認・修正(GUI)

```sh
python3 scripts/transcribe/editor.py          # ブラウザで http://127.0.0.1:8765/ を開く
```

1. 録音(WAV、PCM 16/24/32bit)のパスと、あれば楽譜(MusicXML)のパスを入れて「読み込む」か、「WAV を選ぶ」。
   採譜モデルが MIDI を作る(初回はモデルの読み込みに約1分、採譜は録音の長さの約1/3の時間)。
   楽譜は後から「楽譜を指定」/「MusicXML を選ぶ」でも指定できる(非圧縮の .musicxml / .xml)
2. スペースで再生。「録音 / MIDI / 両方」を切り替え、音量を調整して聴き比べる
3. ピアノロールで直す: ダブルクリックで追加、Delete で削除、ドラッグで移動(上下で音高)、右端で長さ、↑↓ で音高。
   「見落としの候補」を表示すると、モデルがしきい値未満で落とした音を点線で示す(ダブルクリックで受け入れ)
4. R(選択した音)/ Shift+R(修正した音すべて)で再調整: その音高・その時刻の近くで、録音に対するモデルの出力から
   発音時刻・ベロシティ・離鍵を決め直す。録音に手がかりがない音は赤い点線になり、指定した時刻のまま残る
5. 楽譜を指定していれば、楽譜と照合した違い(抜け・余分・弾き間違い・リズム)が左に並ぶ。違いを選ぶと、
   ピアノロール・録音波形・元の楽譜・MIDI から書き起こした楽譜がその箇所に移る。違いごとに判定する:
   提案を当てはめる(A: 採譜の誤りとして MIDI を直す)/ 演奏どおり(O)/ 不明(U)。J / K で次・前へ。
   提案には録音の手がかりや検証での正しさが付く。自動では当てはめない(下の「楽譜を手がかりにした補正」)。
   編集のたびに少し待ってから照合し直す。楽譜の小節をクリックするとその時刻へ移り、再生中は楽譜が追従する
6. ⌘/Ctrl+S で保存。`output/editor/<名前>/` に project.json(修正・違いごとの判定の記録)と MIDI ができる

再生の MIDI の音は簡単な合成音(確認用)。ペダルは表示のみで、編集はできない。楽譜の機能には music21 と verovio が必要。

### 楽譜を手がかりにした補正を自動で当てはめない理由

`scripts/transcribe/eval_score_autofix.py`(PEDB、採譜モデルの出力、調整に使っていない曲)での正しさ:
楽譜にあって MIDI にない音を、録音の手がかり(発音の出力 0.1 以上)があるときに足す規則は 73%
(弾かれていなかった音を補ってしまう)。楽譜にない音を消す規則は、倍音 91%・再検出 88%・その他 79%。
どれも黙って直せる水準ではないので、根拠つきの提案として人が判定する。

## 楽譜と演奏の比較(GUI)

```sh
python3 scripts/performance/score_viewer.py   # ブラウザで http://127.0.0.1:8766/ を開く
```

- 楽譜: PEDB の演奏名(その曲の MusicXML)か、MusicXML のパス
- 演奏: PEDB の人手の対応付け / 同時記録MIDI / 採譜モデルの出力、採譜の GUI で保存した MIDI、MIDI のパス。
  人手の対応付け以外は、楽譜と自動で対応付ける
- 左に元の楽譜、右に演奏から書き起こした楽譜を表示する。書き起こしは元の楽譜の小節・拍子・調を型にし、
  演奏した音を置き直したもの(弾かれなかった音は休符、余分な音は格子に丸めた位置、装飾の実際の音は書かない)
- 違いに色を付ける: 赤 = 抜け(元の楽譜)/ 余分(演奏の楽譜)、橙 = 弾き間違い、紫 = リズム
  (その場のテンポで 1/4 拍かつ 80ms 以上のずれ。半拍以上ずれた音は演奏の楽譜でずれた位置に置く)
- 右の一覧をクリックすると、両方の楽譜がその小節に移る。種類ごとに一覧を絞り込める。スクロールは両方で合わせられる
- 採譜の GUI の「楽譜と比べる」から、保存した MIDI をそのまま開ける(両方のサーバーを起動しておく)

採譜モデルの出力を使うときの「違い」には、演奏の違いと採譜の誤りの両方が含まれる。採譜の GUI で直してから比べるとよい。
繰り返し記号は展開しない(楽譜に書かれた順のまま)。演奏の楽譜ではスラー・ペダル記号を省く。

## PEDB のファイルについて

- `*auto_match*.txt` の列: ID, 発音(秒), 離鍵(秒), 音名, 発音ベロシティ, 離鍵ベロシティ, チャンネル,
  対応状態(0=通常, 1=装飾音, -1=余分な音), 楽譜時刻(tick), 楽譜音ID, 誤り(0=正, 1=音高の誤り, 3=余分な音), 飛ばし。
  `//Missing` 行は演奏されなかった楽譜音。
- `*_fmt3x.txt`: `//TPQN` が四分音符あたりの tick 数。`//DuplicateOnsets` は2声部が共有する音(演奏は1回)。
- 録音(WAV)は同時記録MIDIより約30〜60ms早い一定のずれがある(時計の進みの違いは見られない)。

## 採譜の評価

Kong の詳しい評価と改善の検討は [docs/kong_evaluation.md](docs/kong_evaluation.md)。

`scripts/transcribe/eval_transcription.py`、PEDB 全100演奏、正解は同時記録MIDI(2026-11-19)。
guided の調整に使った8演奏(DEV)を除いた92演奏(TEST)の値。発音時刻は方式ごと・演奏ごとの一定のずれを差し引いた後。

| | guided | kong | hybrid |
|---|---|---|---|
| 音符F1(発音±50ms) | 0.68 | **0.96** | 0.85 |
| 適合率 / 再現率 | 0.67 / 0.69 | 0.95 / 0.96 | 0.74 / 0.98 |
| 発音+離鍵F1(正解をペダルで伸ばした音の終わり) | 0.25 | **0.79** | 0.70 |
| 一致した音の発音誤差の中央値 | 8.2ms | **1.9ms** | 1.9ms |
| 楽譜の音符単位の発音誤差 中央値 / ≤10ms | 14.9ms / 38% | - | 3.6ms / 65% |
| 楽譜の音符単位のベロシティ 相関 / 平均絶対誤差 | 0.56 / 10.4 | - | 0.70 / 8.4 |
| ベロシティ(一致した音) 相関 / 線形補正後の平均絶対誤差 | - | 0.88 / 6.0 | - |
| ペダル(10msごとの一致)F1 | - | 0.88 | - |

- kong: 楽譜を使わずに、どの演奏でも F1 0.86 以上。発音時刻はほぼ 2ms 以内。
  ベロシティは相関が高いが絶対値がずれる(補正なしの平均絶対誤差 15)。線形補正(1曲抜き交差検証)で 6 程度
- 離鍵: MIDI の note-off は鍵を離した時刻だが、kong は音が止んだ時刻(ペダルで伸びた音の終わり)を推定する。
  鍵を離した時刻で比べると F1 0.22 にとどまる。アーティキュレーションの分析にはこの違いに注意
- guided: 調整に使った8演奏(中央値 10.4ms)より、それ以外(14.9ms)で悪い。
  11演奏で DTW が全体として外れ、秒単位でずれる(ラフマニノフ、ベートーヴェン Op.13-1、ショパン ワルツ7番など)
- hybrid: kong の音を guided の予測時刻 ±150ms で楽譜の音符に割り当てる。割り当てられれば誤差は kong 並みだが、
  guided が外れた箇所では割り当てに失敗し、その誤りを引き継ぐ(割り当てられた割合 76%)。
  そのため「割り当てられない = 抜けた音」とみなす検出は使えない(適合率 0.12)。
  楽譜との対応付けは、録音のクロマではなく kong の MIDI と楽譜を直接対応付けるほうがよい(次の作業)
