#!/usr/bin/env python3
"""Resumable IDRK extractor. One entry per subprocess, persisted before advancing.
Run with DM_GAME set; does not modify existing shader outputs.
"""
import argparse
import hashlib
import json
import os
import pickle
import struct
import subprocess
import sys
from pathlib import Path

MAGIC = b'IDRK0000'
CHUNK = 1024 * 1024

def entries(src, start=0):
    src.seek(0, 2)
    size = src.tell()
    pos = start
    while pos + 0x30 <= size:
        src.seek(pos)
        h = src.read(0x30)
        if h[:8] != MAGIC:
            src.seek(pos)
            block = src.read(min(CHUNK, size - pos))
            offset = block.find(MAGIC, 1)
            pos += offset if offset >= 0 else max(1, len(block) - 7)
            continue
        esz, csz = struct.unpack_from('<QQ', h, 8)
        if esz < 0x30 or csz > esz or pos + esz > size:
            pos += 8
            continue
        yield pos, pos + esz, pos + esz - csz, csz, struct.unpack_from('<I', h, 0x2c)[0]
        pos += esz

def worker():
    import runpy
    split = runpy.run_path(str(Path(__file__).with_name('shader_split.py')))
    src = Path(sys.argv[2])
    dest = Path(sys.argv[3])
    label = sys.argv[4]
    result = []
    split['idrk_shaders'](str(src), label, result)
    keys = split['uniq_keys'](result)
    pairs = split['var_pairs'](result)
    with dest.open('wb') as f:
        pickle.dump((keys, pairs), f, protocol=pickle.HIGHEST_PROTOCOL)
        f.flush()
        os.fsync(f.fileno())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('source', help='NUP virtual path to .fdata')
    ap.add_argument('output', help='new output directory (existing extraction untouched)')
    ap.add_argument('--max-entry-mib', type=int, default=64)
    ap.add_argument('--timeout', type=int, default=120)
    args = ap.parse_args()
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
    from romfs import open as romopen
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    parts = output / 'parts'
    parts.mkdir(exist_ok=True)
    statefile = output / 'checkpoint.json'
    identity = hashlib.sha256(args.source.encode()).hexdigest()
    if statefile.exists():
        state = json.loads(statefile.read_text())
        if state['identity'] != identity:
            raise SystemExit('checkpoint belongs to a different source')
    else:
        state = {'identity': identity, 'next': 0, 'processed': 0, 'saved': 0, 'skipped': 0}
    scratch = output / 'current.idrk'
    temp = output / 'current.pkl'
    with romopen(args.source, 'rb') as source:
        for offset, end, data_offset, size, flags in entries(source, state['next']):
            number = state['processed']
            target = parts / f'{offset:012x}.pkl'
            if target.exists():
                state['saved'] += 1
            elif size > args.max_entry_mib * 1024 * 1024:
                state['skipped'] += 1
                print(f'SKIP {offset:#x}: stored entry {size} bytes exceeds limit', flush=True)
            else:
                source.seek(offset)
                with scratch.open('wb') as dst:
                    remaining = end - offset
                    while remaining:
                        block = source.read(min(CHUNK, remaining))
                        if not block:
                            raise IOError('truncated source')
                        dst.write(block)
                        remaining -= len(block)
                try:
                    result = subprocess.run(
                        [sys.executable, __file__, '--worker', str(scratch), str(temp), f'IDRK@{offset:#x}'],
                        timeout=args.timeout, check=False,
                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                    if result.returncode == 0 and temp.exists():
                        temp.replace(target)
                        state['saved'] += 1
                    else:
                        state['skipped'] += 1
                        print(f'SKIP {offset:#x}: child exit {result.returncode}: {result.stderr[-250:].decode(errors="replace")}', flush=True)
                except subprocess.TimeoutExpired:
                    state['skipped'] += 1
                    print(f'SKIP {offset:#x}: timed out', flush=True)
                finally:
                    scratch.unlink(missing_ok=True)
                    temp.unlink(missing_ok=True)
            state['processed'] = number + 1
            state['next'] = end
            tmpstate = statefile.with_suffix('.tmp')
            tmpstate.write_text(json.dumps(state, indent=2))
            tmpstate.replace(statefile)
            if state['processed'] % 100 == 0:
                print(f"PROGRESS entries={state['processed']} saved={state['saved']} skipped={state['skipped']} offset={end:#x}", flush=True)
    print('DONE', json.dumps(state), flush=True)

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--worker':
        worker()
    else:
        main()
