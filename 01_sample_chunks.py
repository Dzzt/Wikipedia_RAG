#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, random, sqlite3, time
from pathlib import Path
from tqdm import tqdm
from raglib import Article, BuildConfig, ChunkBuilder
from raglib.utils import read_json

def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument('--input',type=Path,default=Path(r'data\ja_wiki.jsonl'))
    p.add_argument('--output',type=Path,default=Path(r'sample\sample.sqlite'))
    p.add_argument('--target-chunks',type=int,default=500000)
    p.add_argument('--seed',type=int,default=20260721)
    p.add_argument('--config',type=Path,default=Path(r'configs\build_config.json'))
    p.add_argument('--overwrite',action='store_true')
    a=p.parse_args()
    if a.output.exists():
        if not a.overwrite: raise FileExistsError(f'{a.output} exists; use --overwrite')
        a.output.unlink()
    cfg=BuildConfig(**read_json(a.config)); builder=ChunkBuilder(cfg); rng=random.Random(a.seed)
    con=sqlite3.connect(a.output)
    con.execute('CREATE TABLE sample(slot INTEGER PRIMARY KEY, chunk_id INTEGER, title TEXT, text TEXT)')
    seen=0; started=time.perf_counter()
    with a.input.open('r',encoding='utf-8') as f:
        bar=tqdm(desc='articles',unit='article',dynamic_ncols=True)
        for line_no,line in enumerate(f,1):
            if not line.strip(): continue
            obj=json.loads(line); meta=obj.get('meta') or {}
            art=Article(str(meta.get('id') or f'line:{line_no}'),str(meta.get('title') or '(無題)'),str(meta.get('url') or ''),str(obj.get('text') or ''))
            for c in builder.build(art):
                seen+=1; rec=(c.chunk_id,c.title,c.document_text())
                if seen<=a.target_chunks:
                    con.execute('INSERT INTO sample(slot,chunk_id,title,text) VALUES(?,?,?,?)',(seen-1,*rec))
                else:
                    slot=rng.randrange(seen)
                    if slot<a.target_chunks:
                        con.execute('UPDATE sample SET chunk_id=?,title=?,text=? WHERE slot=?',(*rec,slot))
            if line_no%5000==0: con.commit()
            bar.update(1); bar.set_postfix(seen=seen,sample=min(seen,a.target_chunks),refresh=False)
    con.commit(); con.close(); bar.close()
    print(f'Sample: {a.output.resolve()}'); print(f'Seen chunks: {seen:,}'); print(f'Sample chunks: {min(seen,a.target_chunks):,}'); print(f'Elapsed: {time.perf_counter()-started:.1f}s')
    return 0
if __name__=='__main__': raise SystemExit(main())
