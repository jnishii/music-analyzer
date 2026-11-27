DATA=../data

editor:
		.venv/bin/python scripts/transcribe/editor.py &
		open -e /Applications/Safari.app http://127.0.0.1:8765/

viewer:
		.venv/bin/python scripts/performance/score_viewer.py
		open -e /Applications/Safari.app http://127.0.0.1:8766/
