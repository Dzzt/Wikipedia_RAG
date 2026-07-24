#!/usr/bin/env python3
from __future__ import annotations
import argparse, sqlite3, time
from pathlib import Path
import numpy as np
from tqdm import tqdm
from raglib import BuildConfig, OllamaEmbedder
from raglib.utils import read_json, write_json

def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument('--sample-db',type=Path,default=Path(r'sample\sample.sqlite'))
    p.add_argument('--output',type=Path,default=Path(r'sample\sample_vectors.f32'))
    p.add_argument('--ids-output',type=Path,default=Path(r'sample\sample_ids.i64'))
    p.add_argument('--config',type=Path,default=Path(r'configs\build_config.json'))
    p.add_argument('--overwrite',action='store_true'); a=p.parse_args()
    for path in (a.output,a.ids_output):
        if path.exists() and not a.overwrite: raise FileExistsError(f'{path} exists; use --overwrite')
    cfg=BuildConfig(**read_json(a.config)); emb=OllamaEmbedder(cfg); con=sqlite3.connect(a.sample_db)
    count=con.execute('SELECT count(*) FROM sample').fetchone()[0]
    vec=np.memmap(a.output,mode='w+',dtype=np.float32,shape=(count,cfg.embedding_dimension))
    ids=np.memmap(a.ids_output,mode='w+',dtype=np.int64,shape=(count,))
    cur=con.execute('SELECT slot,chunk_id,text FROM sample ORDER BY slot')
    texts=[]; idbuf=[]; slots=[]; bar=tqdm(total=count,desc='sample embeddings',unit='chunk',dynamic_ncols=True); started=time.perf_counter()
    def flush():
        nonlocal texts,idbuf,slots
        if not texts: return
        matrix=emb.embed_texts([f'{cfg.document_prefix}{t}' for t in texts])
        for local,slot in enumerate(slots): vec[slot]=matrix[local]; ids[slot]=idbuf[local]
        vec.flush(); ids.flush(); bar.update(len(texts)); texts=[]; idbuf=[]; slots=[]
    for slot,chunk_id,text in cur:
        slots.append(slot); idbuf.append(chunk_id); texts.append(text)
        if len(texts)>=cfg.embedding_batch_size: flush()
    flush(); bar.close(); con.close()
    manifest={'count':count,'dimension':cfg.embedding_dimension,'vectors':str(a.output),'ids':str(a.ids_output),'elapsed_seconds':time.perf_counter()-started}
    write_json(a.output.with_suffix('.json'),manifest); print(manifest); return 0
if __name__=='__main__': raise SystemExit(main())
