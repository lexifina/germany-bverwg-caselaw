"""One German federal-court case-law repo from Seán Fobbe's CC0 "Corpus der Entscheidungen" CSV (Zenodo), straight into
git fast-import.   python build.py <court> <csv> <out repo dir> --today YYYY-MM-DD --snapshot YYYY-MM-DD --record <zenodo id>

One commit per decision (Kind: court), dated the day decided (noon Berlin); then one Lexifina documentation commit with
README, REPRODUCE.md, corpus.json, decision-index.tsv, unresolved.csv and the judgment overlay
judgments/germany/bund/acts/<path>.jsonl: citations found in the text ("§ 823 Abs. 1 BGB", "§§ 280, 281 BGB",
"Art. 3 Abs. 1 GG", "§ 15 SGB V") linked to germany-law files, each row pinned to the article's own version on the day
decided (reconcile.fix_row). Deterministic for the same input."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import regex

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'nl-caselaw'))
from reconcile import fix_row, versions  # noqa: E402  (law_commit = the article's own version)

LAW_CORPUS = 'germany/bund/acts'
BERLIN = timezone(timedelta(hours=1))
COMMITTER = b'Lexifina <corpus@lexifina.com>'
EPOCH_NOON = 11 * 3600
ISO = re.compile(r'^\d{4}-\d{2}-\d{2}$')
COURTS = {'bverfg': 'Bundesverfassungsgericht', 'bverwg': 'Bundesverwaltungsgericht', 'bfh': 'Bundesfinanzhof',
          'bag': 'Bundesarbeitsgericht', 'bpatg': 'Bundespatentgericht'}
ROMAN = {'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'V': 5, 'VI': 6, 'VII': 7, 'VIII': 8, 'IX': 9, 'X': 10, 'XI': 11, 'XII': 12, 'XIV': 14}
SIGN = re.compile(r'§§?|\bArt(?:ikel|\.)|\bArtt\.')
QUAL = r'(?:Abs\.|Absatz|Satz|S\.|Nr\.|Nrn\.|Buchst\.|lit\.|Alt\.|Halbsatz|Halbs\.|Hs\.|Var\.|Unterabs\.|UAbs\.)'
# The body between the sign and the abbreviation: numbers, qualifiers and joiners only (bounded: at most 120 chars).
BODY = regex.compile(r'\s*((?:\d+\s?[a-z]?\b|' + QUAL + r'|und|bis|sowie|oder|,|;|–|-|\s)+?)\s*'
                     r'(SGB\s+[IVX]+|[A-ZÄÖÜ][A-Za-zÄÖÜäöüß]*[A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9]*(?:\s(?:19|20)\d\d)?|GG)\b')
NUM = re.compile(r'(' + QUAL + r')?\s*(\d+)\s?([a-z]?)\b')


def akey(s: str) -> str:
    s = re.sub(r'\bSGB\s+([IVX]+)\b', lambda m: f'SGB{ROMAN.get(m.group(1), m.group(1))}', s.strip())
    return re.sub(r'[\s.\-]', '', s).lower()


def slug(s: str) -> str:
    return re.sub(r'-{2,}', '-', re.sub(r'[^A-Za-z0-9._-]+', '-', s)).strip('-.')[:120] or 'x'


def email_local(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')[:60] or 'decision'


class Resolver:
    def __init__(self, idx):
        self.aliases, self.arts = idx['aliases'], idx['arts']
        self.timeouts = 0

    def from_text(self, text: str) -> list[tuple[str, str]]:
        out = []
        for s in SIGN.finditer(text or ''):
            win = text[s.end():s.end() + 160]
            try:
                m = BODY.match(win, timeout=0.05)
            except TimeoutError:
                self.timeouts += 1
                continue
            if not m:
                continue
            folder = self.aliases.get(akey(m.group(2))) or (self.aliases.get(akey(m.group(2).rsplit(' ', 1)[0])) if ' ' in m.group(2) else None)
            if not folder:
                continue
            files = self.arts.get(folder, {})
            how = re.sub(r'\s+', ' ', text[s.start():s.end() + m.end()]).strip()[:160]
            for q, n, suf in NUM.findall(m.group(1)):
                if q:
                    continue
                p = files.get(f'{int(n)}{suf}')
                if p:
                    out.append((p, how))
        return out


def rows_of(court: str, csv: str) -> list[dict]:
    con = duckdb.connect()
    con.execute("SET memory_limit='24GB'")
    src = f"read_csv_auto('{Path(csv).as_posix()}', sample_size=-1, max_line_size=50000000, all_varchar=true)"
    cols = {r[0] for r in con.execute(f'describe select * from {src}').fetchall()}
    g = lambda c: c if c in cols else 'NULL'
    if court == 'bag':
        q = (f"select doc_id, text, Datum as datum, SpruchkoerperAZ || ' ' || Registerzeichen || ' ' || Ordinalzahl || '/' || "
             f"right(Eingangsjahr, 2) || coalesce(ZusatzAZ, '') as aktenzeichen, NULL as ecli, NULL as url, NULL as typ, NULL as leitsatz, "
             f"NULL as titel from {src}")
    else:
        url = 'url_html' if 'url_html' in cols else g('url')
        leit = 'leitsatz' if 'leitsatz' in cols else g('text_leitsatz')
        q = (f"select doc_id, text, datum, aktenzeichen, {g('ecli')} as ecli, {url} as url, {g('entscheidung_typ')} as typ, "
             f"{leit} as leitsatz, {g('titel') if 'titel' in cols else g('name')} as titel, {g('spruchkoerper_az')} as spruchkoerper, "
             f"{g('bverfge')} as bverfge, {g('zitiervorschlag')} as zitiervorschlag from {src}")
    cur = con.execute(q)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('court'), ap.add_argument('csv'), ap.add_argument('out')
    ap.add_argument('--today', required=True), ap.add_argument('--snapshot', required=True), ap.add_argument('--record', required=True)
    ap.add_argument('--law-repo', default=str(Path.home() / 'Documents' / 'caselaw-law-mirrors' / 'germany-law.git'))
    ap.add_argument('--chunk-bytes', type=int, default=2_500_000_000)
    a = ap.parse_args()
    court, name = a.court, COURTS[a.court]
    out = Path(a.out)
    res = Resolver(json.load(open(HERE / 'de_index.json', encoding='utf-8')))
    ver = versions(Path(a.law_repo), ref='main')
    recs = [r for r in rows_of(court, a.csv) if (r.get('text') or '').strip()]
    for r in recs:
        r['day'] = (r['datum'] or '')[:10]
    recs.sort(key=lambda r: (r['day'] or '9999', r.get('aktenzeichen') or '', r['doc_id']))
    print(f'{court}: {len(recs)} decisions', file=sys.stderr, flush=True)
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f'{out} is not empty')
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(out)], check=True)
    fi = subprocess.Popen(['git', '-C', str(out), 'fast-import', '--quiet', '--done', f'--export-marks={out / ".git" / "marks"}',
                          '--big-file-threshold=64m'], stdin=subprocess.PIPE)
    w = fi.stdin.write
    overlay, dindex, used, undated = {}, [], set(), []
    mark = raw = clamp = 0
    checkpoints, first = [], None
    for r in recs:
        day = r['day']
        if not ISO.match(day or ''):
            undated.append(r)
            continue
        first = first or day
        az = (r.get('aktenzeichen') or r['doc_id']).strip()
        base = slug(r['ecli'].replace(':', '_')) if r.get('ecli') else slug(f'{az}-{day}')
        path = f'decisions/{court}/{day[:4]}/{base}.md'
        if path in used:
            path = f"decisions/{court}/{day[:4]}/{base}--{slug(r['doc_id'])[-24:]}.md"
        used.add(path)
        cites = res.from_text(r['text'])
        for ref in {az, r.get('ecli') or ''}:
            if ref:
                dindex.append(ref + chr(9) + path)
        fm = {'ecli': r.get('ecli'), 'court': name, 'spruchkoerper': r.get('spruchkoerper'), 'aktenzeichen': az, 'decided': day,
              'typ': r.get('typ'), 'bverfge': r.get('bverfge'), 'zitiervorschlag': r.get('zitiervorschlag'),
              'source_url': r.get('url'), 'corpus_doc_id': r['doc_id']}
        lines = ['---'] + [f'{k}: {json.dumps(v, ensure_ascii=False)}' for k, v in fm.items() if v not in (None, '', 'NA')]
        uniq = sorted({p for p, _ in cites})
        if uniq:
            lines.append('cites_law: ' + json.dumps([f'{LAW_CORPUS}/{p}' for p in uniq], ensure_ascii=False))
        title = (r.get('titel') or '').strip()
        title = f'{name}, {az} vom {day}' + (f': {title}' if title and title != 'NA' else '')
        lines += ['---', '', f'# {title}', '']
        if r.get('leitsatz') and r['leitsatz'] not in ('NA', ''):
            lines += ['## Leitsatz', '', r['leitsatz'].strip(), '']
        lines += ['## Entscheidung', '', r['text'].strip(), '']
        body = '\n'.join(lines).replace('\r\n', '\n').encode('utf-8')
        raw += len(body)
        d0 = date.fromisoformat(day)
        if d0.year < 1970:
            clamp += 1
            ts = EPOCH_NOON + clamp - 3600
        else:
            ts = int(datetime(d0.year, d0.month, d0.day, 12, tzinfo=BERLIN).timestamp())
        author = f'{name} {az}'.replace('<', '').replace('>', '')[:200]
        email = f'{court}-{email_local(az)}@caselaw.invalid'
        tr = ['Kind: court', f'In-force: {day}', f'Decided: {day}', f'Court: {name}', f'Docket: {az}']
        if r.get('ecli'):
            tr.append(f"ECLI: {r['ecli']}")
        if r.get('url') and r['url'] != 'NA':
            tr.append(f"Source: {r['url']}")
        seen = set()
        for p, how in cites:
            if p not in seen and len(seen) < 60:
                seen.add(p)
                tr.append(f'Cites: {LAW_CORPUS}/{p} ({how[:120]})')
        what = (r.get('typ') or 'Entscheidung')
        msg = (f'{name} {az}: {what}'[:200] + '\n\n' + '\n'.join(tr) + '\n').encode('utf-8')
        mark += 1
        w(b'commit refs/heads/main\nmark :%d\n' % mark)
        w(b'author %s <%s> %d +0100\ncommitter %s %d +0100\n' % (author.encode('utf-8'), email.encode(), ts, COMMITTER, ts))
        w(b'data %d\n%s\n' % (len(msg), msg))
        w(b'M 100644 inline %s\ndata %d\n%s\n' % (path.encode('utf-8'), len(body), body))
        if raw >= (len(checkpoints) + 1) * a.chunk_bytes:
            checkpoints.append(mark)
        counts = {}
        for p, _ in cites:
            counts[p] = counts.get(p, 0) + 1
        done = set()
        for p, how in cites:
            if p in done:
                continue
            done.add(p)
            row = {'decided': day, 'name': f'{name} {az}', 'court': name, 'citation': r.get('ecli') or az,
                   'url': r.get('url') or '', 'how': how, 'mentions': counts[p], 'decision': path}
            overlay.setdefault(p, []).append(fix_row(row, p, ver))
    docs = {}
    for p, rows in overlay.items():
        rows.sort(key=lambda x: (x['decided'], x['citation']))
        docs[f'judgments/{LAW_CORPUS}/{p[:-3]}.jsonl'] = ''.join(json.dumps(x, ensure_ascii=False, sort_keys=True) + '\n' for x in rows)
    docs['decision-index.tsv'] = 'reference' + chr(9) + 'path' + chr(10) + ''.join(x + chr(10) for x in sorted(set(dindex)))
    n_refs = sum(len(v) for v in overlay.values())
    in_rec = sum(1 for v in overlay.values() for x in v if 'law_commit' in x)
    gl_path = f'germany/{court}/caselaw'
    docs['corpus.json'] = json.dumps({
        'corpus': f'de/{court}/caselaw', 'path': gl_path, 'name': f'Case law: Germany, {name}', 'label': f'Germany case law ({name})',
        'language': 'de', 'kind': 'judgments', 'history': 'in-force',
        'coverage': f'{mark} published decisions of the {name}, one file and one commit each, dated the day decided; {n_refs} citations of federal law linked to germany-law ({in_rec} inside its record).',
        'history_from': first, 'record_to': a.snapshot, 'checked_on': a.today,
        'record_note': 'Court decisions, not laws: every commit is Kind: court. In-force is the day decided. The record ends at the corpus snapshot named in REPRODUCE.md.',
        'in_force_trailer': 'In-force', 'correction_trailer': 'Corrected',
        'dates': {'commit': 'decided', 'time': '12:00 Europe/Berlin'},
        'source': {'publisher': name, 'dataset': f'Corpus der Entscheidungen ({court.upper()}), Zenodo record {a.record}', 'updated': a.snapshot},
        'licence': 'CC0 1.0 (the decisions are official works, § 5 UrhG)',
        'overlay': {'path': f'judgments/{LAW_CORPUS}/<path>.jsonl', 'law_corpus': LAW_CORPUS},
    }, ensure_ascii=False, indent=2) + '\n'
    docs['README.md'] = f"""# German case law: {name}

Part of [lexifina/global-law](https://github.com/lexifina/global-law), one git history for every country's law: this is the case law of the {name}, Germany, which global-law reads at `{gl_path}/`.

Every published decision of the {name} in the corpus this is built from, as a git history: one file per decision, one
commit per decision, dated the day it was decided. {mark:,} decisions from {first} to {a.snapshot}.

## Questions as git commands

| Question | Command |
|---|---|
| A decision | `cat decisions/{court}/<year>/<ECLI or Aktenzeichen>.md` |
| By Aktenzeichen or ECLI | `git log --grep='^Docket: 1 BvR 123/20$' --stat` · `grep '1 BvR 123/20' decision-index.tsv` |
| Decisions given in a window | `git log --since=2024-03-01 --until=2024-03-31 --format='%ad %an' --date=short` |
| Decisions citing § 823 BGB | `cat judgments/germany/bund/acts/bgb/0823.jsonl` |
| The provision as the court applied it | each overlay row's `law_commit` is the provision's own version in germany-law on the day decided; germany-law's record for acts other than the Grundgesetz starts 2019-06-10, so older decisions give `law_record_from` instead |
| Who wrote a line? | `git blame decisions/...`: each line is credited to the decision (court and Aktenzeichen) |

## Dates and authors
The commit date is the day the decision was given, at noon Berlin (`In-force:` and `Decided:`). The author is the court
and the Aktenzeichen, at an address that cannot exist. These are court rulings, not laws: every commit is `Kind: court`.

## The judgment overlay
Citations of federal law in the decision's text ("§ 823 Abs. 1 BGB", "Art. 3 Abs. 1 GG") are linked to the provision
files of lexifina/germany-law by the official abbreviations in its `verzeichnis.json`: {n_refs:,} links, {in_rec:,} of them
inside germany-law's record. Abbreviations two acts share are not linked.

## Source and licence
The decisions are official works of the {name} (§ 5 UrhG); each file links to the court's own page where the corpus gives
one. Built from the CC0 "Corpus der Entscheidungen" CSV on Zenodo (record {a.record}, snapshot {a.snapshot}). This
repository is dedicated to the public domain under CC0 1.0.

## What it does not show
Decisions the court did not publish, and decisions after {a.snapshot}.
"""
    docs['REPRODUCE.md'] = f"""# Reproducing this repository

Input: Zenodo record {a.record}, the CSV "Datensatz" file of the {name} corpus (snapshot {a.snapshot}); lexifina/germany-law
`main` for the overlay (`tools/de_index.py`).

```
python tools/de_index.py <germany-law clone>
python tools/build.py {court} <csv> <empty folder> --today {a.today} --snapshot {a.snapshot} --record {a.record}
```

Gives {mark:,} decision commits and one documentation commit.
"""
    for f in ('build.py', 'de_index.py'):
        docs[f'tools/{f}'] = (HERE / f).read_text(encoding='utf-8')
    ts = int(datetime.fromisoformat(a.today + 'T12:00:00+01:00').timestamp())
    msg = f'Documentation: README, REPRODUCE, corpus.json, decision index and the judgment overlay ({n_refs} citations)\n'.encode()
    w(b'commit refs/heads/main\nauthor Lexifina <corpus@lexifina.com> %d +0100\ncommitter %s %d +0100\n' % (ts, COMMITTER, ts))
    w(b'data %d\n%s\n' % (len(msg), msg))
    for p in sorted(docs):
        b = docs[p].encode('utf-8')
        w(b'M 100644 inline %s\ndata %d\n%s\n' % (p.encode('utf-8'), len(b), b))
    w(b'done\n')
    fi.stdin.close()
    if fi.wait():
        raise SystemExit('fast-import failed')
    marks = dict(l.split() for l in (out / '.git' / 'marks').read_text().splitlines())
    (out / '.git' / 'push-checkpoints').write_text(''.join(marks[f':{m}'] + '\n' for m in checkpoints))
    print(json.dumps({'group': court, 'decisions': mark, 'undated': len(undated), 'overlay_rows': n_refs, 'in_record': in_rec,
                      'regex_timeouts': res.timeouts, 'raw_gb': round(raw / 1e9, 2), 'checkpoints': len(checkpoints)}), flush=True)


if __name__ == '__main__':
    main()
