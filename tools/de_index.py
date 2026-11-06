"""germany-law index for the overlay: official abbreviation (jurabk and amtabk from verzeichnis.json, plus GG) -> act
folder, and every article file the repo has ever held keyed by its normalised number ('823a', '16a').
Writes de_index.json.   python de_index.py <germany-law clone (bare is fine)> [ref]"""
import json
import re
import subprocess
import sys
from pathlib import Path

repo = sys.argv[1]
ref = sys.argv[2] if len(sys.argv) > 2 else 'main'
ROMAN = {'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5, 'VI': 6, 'VII': 7, 'VIII': 8, 'IX': 9, 'X': 10, 'XI': 11, 'XII': 12, 'XIV': 14}


def git(*a):
    return subprocess.run(['git', '-C', repo, *a], capture_output=True).stdout.decode('utf-8', 'replace')


def akey(s: str) -> str:
    """Abbreviation key: no spaces, dots or hyphens, case kept (KV vs Kv matter little here, but StGB vs STGB don't)."""
    s = re.sub(r'\bSGB\s+([IVX]+)\b', lambda m: f'SGB{ROMAN.get(m.group(1), m.group(1))}', s.strip())
    return re.sub(r'[\s.\-]', '', s).lower()


def nkey(stem: str) -> str:
    return re.sub(r'\d+', lambda m: str(int(m.group())), stem.lower())


verz = json.loads(git('show', f'{ref}:verzeichnis.json'))
aliases, clash = {}, set()
for v in verz:
    for ab in {v.get('jurabk') or '', v.get('amtabk') or ''}:
        if not ab:
            continue
        k = akey(ab)
        if k in aliases and aliases[k] != v['slug']:
            clash.add(k)
        aliases.setdefault(k, v['slug'])
for k in clash:  # an abbreviation two acts share is left unresolved
    aliases.pop(k, None)
aliases['gg'] = 'grundgesetz/artikel'
names = set(git('log', ref, '--format=', '--name-only', '--no-renames').split('\n')) | set(git('ls-tree', '-r', '--name-only', ref).split('\n'))
arts = {}
for p in names:
    if not p.endswith('.md') or p.endswith(('/index.md', '/inhaltsuebersicht.md', 'README.md', 'REPRODUCE.md')):
        continue
    folder, _, name = p.rpartition('/')
    if folder:
        arts.setdefault(folder, {})[nkey(name[:-3])] = p
json.dump({'aliases': aliases, 'arts': arts}, open(Path(__file__).with_name('de_index.json'), 'w', encoding='utf-8'), ensure_ascii=False)
print(len(aliases), 'abbreviations;', len(clash), 'shared ones left out;', sum(len(v) for v in arts.values()), 'article files (ever)')
for k in ('bgb', 'stgb', 'zpo', 'asylg', 'sgb5', 'gg', 'vwgo', 'ao', 'estg'):
    f = aliases.get(k)
    print(k, f, len(arts.get(f or '', {})))
