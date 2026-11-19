"""パッケージ間の依存の向きを検査する

将来リポジトリを分けられるように、依存は次の向きだけに限る。
  music_io             -> (なし)
  piano_transcribe     -> music_io
  score_analysis       -> music_io
  performance_analysis -> music_io, score_analysis
scripts/ の下のフォルダも、対応するパッケージと同じ規則に従う。
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
OURS = {'music_io', 'piano_transcribe', 'score_analysis', 'performance_analysis'}
ALLOWED = {
    'music_io': set(),
    'piano_transcribe': {'music_io'},
    'score_analysis': {'music_io'},
    'performance_analysis': {'music_io', 'score_analysis'},
}
SCRIPT_DIRS = {'transcribe': 'piano_transcribe', 'score': 'score_analysis', 'performance': 'performance_analysis'}


def imported_packages(path):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split('.')[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split('.')[0])
    return names & OURS


def check(files, pkg):
    bad = []
    for f in files:
        extra = imported_packages(f) - ALLOWED[pkg] - {pkg}
        if extra:
            bad.append(f'{f.relative_to(ROOT)} -> {sorted(extra)}')
    return bad


def test_packages():
    bad = []
    for pkg in ALLOWED:
        bad += check((ROOT / 'src' / pkg).rglob('*.py'), pkg)
    assert not bad, '依存の向きの違反:\n' + '\n'.join(bad)


def test_scripts():
    bad = []
    for d, pkg in SCRIPT_DIRS.items():
        bad += check((ROOT / 'scripts' / d).rglob('*.py'), pkg)
    assert not bad, '依存の向きの違反:\n' + '\n'.join(bad)
