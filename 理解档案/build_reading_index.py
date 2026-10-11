"""Build a local, provenance-preserving reading queue; never edits source notes."""
from pathlib import Path
import re, json, hashlib, unicodedata

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent
if (OUT / '阅读进度.json').exists():
    raise SystemExit('Reading index already exists; preserve reviewed progress. Rebuild only with an explicit migration.')
groups = {}
for p in sorted((ROOT / 'mi-notes').rglob('*.md')):
    if any(x in p.parts for x in ['分析', '.cache', 'assets']):
        continue
    s = p.read_text(encoding='utf-8-sig')
    fm = re.match(r'^---\s*\n(.*?)\n---\s*\n', s, re.S)
    if not fm:
        continue
    meta = dict(re.findall(r'^(\w+):\s*"(.*)"$', fm[1], re.M))
    title = meta.get('title', p.stem)
    body = s[fm.end():].strip()
    body = re.sub(r'^#\s+' + re.escape(title) + r'\s*\n', '', body, count=1)
    # Only ignore formatting whitespace/zero-width and escaped markdown punctuation.
    normalized = ''.join(c for c in body if not c.isspace() and unicodedata.category(c) != 'Cf')
    normalized = re.sub(r'\\([.*_~#>\-])', r'\1', normalized)
    key = hashlib.sha256(normalized.encode()).hexdigest()
    path = p.relative_to(ROOT).as_posix()
    if key in groups:
        groups[key]['copies'].append(path)
        continue
    date = re.match(r'^(20\d\d)[.年/-](\d{1,2})[.月/-](\d{1,2})', title)
    groups[key] = dict(title=title, path=path, copies=[path], body=body,
                       date=('%s-%02d-%02d' % (date[1], int(date[2]), int(date[3]))) if date else meta.get('created','')[:10],
                       dated=bool(date), chars=len(body), status='unread', sha256=key)
entries = sorted(groups.values(), key=lambda e:(e['date'],e['title'],e['path']))
for i,e in enumerate(entries):
    e['entry_id'] = f'N{i+1:04d}'
OUT.mkdir(exist_ok=True)
(OUT/'索引.json').write_text(json.dumps(entries, ensure_ascii=False, indent=2),encoding='utf-8')
queue=[]; batch=''; ids=[]
batch_dir=OUT/'原文阅读批次'; batch_dir.mkdir(exist_ok=True)
for e in entries:
    if not e['dated'] and not any(w in e['title'] for w in ['年度回顾','爱人的信','理想恋人','自由落体','旧工业','走出考场','惟余沉默','想家']):
        continue
    clean='\n'.join(line for line in e['body'].splitlines() if line.strip() and not all(unicodedata.category(c)=='Cf' or c.isspace() for c in line))
    text=f"\n[{e['entry_id']}] {e['title']}\n原文：{e['path']}\n{clean}\n"
    # Long entries span several batches, with explicit part counts.
    pieces=[text[j:j+6500] for j in range(0,len(text),6500)]
    for part, piece in enumerate(pieces):
        segment=(f"\n[{e['entry_id']} part {part+1}/{len(pieces)}]\n" if len(pieces)>1 else '')+piece
        if batch and len(batch)+len(segment)>7000:
            name=f'{len(queue)+1:03d}.txt'; (batch_dir/name).write_text(batch,encoding='utf-8')
            queue.append(dict(file=name, entries=ids, status='unread')); batch=''; ids=[]
        batch+=segment; ids.append(e['entry_id'])
if batch:
    name=f'{len(queue)+1:03d}.txt'; (batch_dir/name).write_text(batch,encoding='utf-8')
    queue.append(dict(file=name, entries=ids, status='unread'))
(OUT/'阅读进度.json').write_text(json.dumps(queue,ensure_ascii=False,indent=2),encoding='utf-8')
(OUT/'待分类标题.tsv').write_text('\n'.join(f"{e['entry_id']}\t{e['date']}\t{e['title']}\t{e['chars']}\t{e['path']}" for e in entries if not e['dated']),encoding='utf-8')
print(json.dumps(dict(source_files=sum(len(e['copies']) for e in entries),unique_bodies=len(entries),dated_entries=sum(e['dated'] for e in entries),reading_batches=len(queue)),ensure_ascii=False))
