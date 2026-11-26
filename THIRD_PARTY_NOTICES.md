# 第三者の著作物

このリポジトリには、他者のコード・データ・フォント・画像は**同梱していません**
(vendor/ もありません。画面の HTML/JS/CSS は自作で、外部の CDN やフォントも読み込みません)。
以下は、利用者が `pip install` やダウンロードで別に入手して使うものです。
ライセンス文は各配布物に含まれます(`.venv/.../*.dist-info/`)。

## ライブラリ(pip で導入)

| 名前 | 用途 | 権利者 | ライセンス |
|---|---|---|---|
| NumPy | 数値計算 | NumPy Developers | BSD-3-Clause |
| pandas | 表の処理 | pandas Development Team | BSD-3-Clause |
| Matplotlib | 図 | Matplotlib Development Team | PSF 系(Matplotlib License) |
| SciPy | 信号処理(librosa 経由) | SciPy Developers | BSD-3-Clause |
| PyTorch | 採譜モデルの実行 | PyTorch Team | BSD-3-Clause ほか(Apache-2.0, MIT, BSL-1.0 を含む) |
| piano_transcription_inference | 採譜モデルの推論 | Qiuqiang Kong | MIT |
| torchlibrosa | 同上の依存 | Qiuqiang Kong | MIT |
| librosa | 音声の読み込み | Brian McFee、librosa development team | ISC |
| mido | MIDI の読み書き | Ole Martin Bjørndalen | MIT |
| music21 | MusicXML の読み込み・書き起こし | Michael Scott Asato Cuthbert | BSD-3-Clause |
| Verovio | 楽譜の浄書(MusicXML -> SVG) | RISM Digital Center ほか | **LGPL-3.0** |
| pytest | テスト(開発のみ) | pytest-dev | MIT |

## データ・モデル(リポジトリ外)

| 名前 | 用途 | 権利者 | 条件 |
|---|---|---|---|
| 採譜モデルの重み `CRNN_note_F1=0.9677_pedal_F1=0.9186.pth`(`models/`、git 管理外) | 録音の MIDI 化 | Qiuqiang Kong。https://zenodo.org/record/4034264 | CC BY 4.0(出典の表示が条件) |
| CrestMusePEDB 2nd edition(`output/` も含め git 管理外) | 評価・分析の題材 | CrestMuse プロジェクト | 登録制の研究用データ。**再配布・公開不可** |

出典: Qiuqiang Kong, Bochen Li, Xuchen Song, Yuan Wan, Yuxuan Wang,
"High-resolution Piano Transcription with Pedals by Regressing Onsets and Offsets Times",
IEEE/ACM TASLP, 2021.

## 注意(有料化・配布するとき)

- **Verovio は LGPL-3.0**。現在は pip で導入した共有ライブラリとして呼び出すだけで、同梱・改変はしていない。
  アプリとして配布するときは、LGPL の条件(ライセンス文の同梱、差し替え可能な形での利用)を守る。
- 採譜モデルの重みは CC BY 4.0 だが、学習に使われたデータ(MAESTRO。非営利の条件がある)の扱いは
  商用にする前に確認する。
- PEDB は研究用データなので、有料のアプリには含められない。
- 依存ライブラリのバージョンは環境で変わる。配布するときは、その時点の版のライセンスを確かめ直す。
