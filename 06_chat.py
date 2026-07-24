#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
import ollama
from raglib.search_engine import SearchEngine
SYSTEM='''あなたは日本語Wikipediaを根拠に回答するアシスタントです。参考資料を優先し、資料にない事実を断定しないでください。不足している場合は不足していると明記してください。'''

def main()->int:
    p=argparse.ArgumentParser(); p.add_argument('question',nargs='?'); p.add_argument('--index-dir',type=Path,default=Path('index')); p.add_argument('--model',default='qwen3:14b'); a=p.parse_args(); engine=SearchEngine(a.index_dir)
    def answer(q):
        results=engine.search(q,6); context='\n\n---\n\n'.join(f'【記事】{r.title}\n【URL】{r.url}\n{r.text}' for r in results)
        response=ollama.chat(model=a.model,messages=[{'role':'system','content':SYSTEM},{'role':'user','content':f'【参考資料】\n{context}\n\n【質問】\n{q}'}]); print(response['message']['content'])
    if a.question: answer(a.question)
    else:
        while True:
            q=input('\n質問> ').strip()
            if q in {'/exit','/quit'}: break
            if q: answer(q)
    return 0
if __name__=='__main__': raise SystemExit(main())
