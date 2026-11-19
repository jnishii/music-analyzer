"""楽譜と演奏(MIDI)の対比: 音符ごとの表、局所テンポ、タイミング・強さのずれ

演奏は MIDI(または対応付けファイル)として受け取る。採譜(piano_transcribe)のコードには依存しない。
依存してよいのは music_io と score_analysis。
"""
