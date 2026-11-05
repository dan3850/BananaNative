#!/usr/bin/env python3
"""Rebuild a canonical shader package from an existing map and complementary extractions.

No ROM access, no shader translation, no modification of inputs. The canonical
map dictates package order, index, stage and source; output is staged separately.
"""
import argparse
import collections
import functools
import pickle
import shutil
import struct
from pathlib import Path

MAGIC = b"BTCDBTCD"
STAGES = {"vs": "vertex", "hs": "hull", "ds": "domain", "gs": "geometry",
          "ps": "pixel", "cs": "compute", "ts": "task", "ms": "mesh"}

def read_map(path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip() or line.startswith("#") or line.startswith("package\t"):
                continue
            cells = line.rstrip("\n").split("\t")
            if len(cells) < 5:
                raise ValueError(f"Invalid map row: {line[:120]!r}")
            pkg, idx, stage, key, source = cells[:5]
            yield pkg, int(idx), stage, key, source

def record(st, ctl, code):
    if st not in STAGES:
        raise ValueError(f"Unknown shader stage: {st}")
    header = bytearray(MAGIC + bytes(0x30))
    struct.pack_into("<2I", header, 0x28, len(ctl), len(code))
    result = bytes(header) + code + ctl
    if result.find(b"BTCDBTCD", 8) != -1:
        raise ValueError("Unexpected record separator in shader")
    return result

def old_offsets(path):
    offsets = []
    with path.open("rb") as f:
        while True:
            at = f.tell()
            head = f.read(0x38)
            if not head:
                break
            if len(head) != 0x38 or head[:8] != MAGIC:
                raise ValueError(f"Corrupt old package {path} at {at}")
            ctl_len, code_len = struct.unpack_from("<2I", head, 0x28)
            offsets.append((at, 0x38 + code_len + ctl_len))
            f.seek(code_len + ctl_len, 1)
    return offsets

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile-map", type=Path, required=True)
    ap.add_argument("--old-map", type=Path, required=True)
    ap.add_argument("--old-pkg", type=Path, required=True)
    ap.add_argument("--parts", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    if args.out.exists():
        ap.error(f"Output already exists: {args.out} (will not overwrite)")
    rows = list(read_map(args.profile_map))
    old_rows = list(read_map(args.old_map))
    old_lookup = {r[3]: r for r in old_rows}
    if len(old_lookup) != len(old_rows):
        raise ValueError("Duplicate keys in old map")
    new_lookup = {}
    files = sorted(args.parts.glob("*.pkl"))
    for n, path in enumerate(files, 1):
        with path.open("rb") as f:
            keys, _ = pickle.load(f)
        for key in keys:
            if key in new_lookup:
                raise ValueError(f"Duplicate key in parts: {key}")
            new_lookup[key] = path
        if n % 5000 == 0:
            print(f"Indexed {n} parts, {len(new_lookup)} shaders", flush=True)
    expected = {r[3] for r in rows}
    if len(expected) != len(rows):
        raise ValueError("Duplicate keys in profile map")
    if old_lookup.keys() & new_lookup.keys():
        raise ValueError("Old and new sets overlap")
    actual = old_lookup.keys() | new_lookup.keys()
    if actual != expected:
        raise ValueError(f"Coverage mismatch: missing={len(expected-actual)}, extra={len(actual-expected)}")
    offsets = {}
    for pkg in {r[0] for r in old_rows}:
        offsets[pkg] = old_offsets(args.old_pkg / pkg)
    for pkg, idx, stage, key, _ in old_rows:
        if idx >= len(offsets[pkg]):
            raise ValueError(f"Missing old record {pkg}:{idx}")
    @functools.lru_cache(maxsize=3)
    def load_part(path):
        with path.open("rb") as f:
            return pickle.load(f)[0]
    args.out.mkdir(parents=True)
    out_pkg = args.out / "pkg"
    out_pkg.mkdir()
    out_map = args.out / "shader_map.tsv"
    count = 0
    open_out = {}
    open_old = {}
    try:
        with out_map.open("w", encoding="utf-8") as mf:
            mf.write("# Consolidated against original profile; original files unchanged\n")
            mf.write("package\tindex\tstage\tdm_ctl_sha16\tsource\n")
            next_index = collections.defaultdict(int)
            for pkg, idx, stage, key, source in rows:
                if pkg not in open_out:
                    open_out[pkg] = (out_pkg / pkg).open("wb")
                if idx != next_index[pkg]:
                    raise ValueError(f"Non-contiguous canonical indices in {pkg}: expected {next_index[pkg]}, got {idx}")
                next_index[pkg] += 1
                if key in new_lookup:
                    where, st, ctl, code = load_part(new_lookup[key])[key]
                    if STAGES[st] != stage:
                        raise ValueError(f"Stage mismatch for {key}: {st} vs {stage}")
                    data = record(st, ctl, code)
                else:
                    opkg, oidx, ostage, _, _ = old_lookup[key]
                    if ostage != stage:
                        raise ValueError(f"Old stage mismatch for {key}")
                    if opkg not in open_old:
                        open_old[opkg] = (args.old_pkg / opkg).open("rb")
                    at, length = offsets[opkg][oidx]
                    open_old[opkg].seek(at)
                    data = open_old[opkg].read(length)
                    if len(data) != length:
                        raise ValueError(f"Truncated old record {opkg}:{oidx}")
                open_out[pkg].write(data)
                mf.write("\t".join((pkg, str(idx), stage, key, source)) + "\n")
                count += 1
                if count % 10000 == 0:
                    print(f"Packed {count}/{len(rows)} shaders", flush=True)
    except BaseException:
        (args.out / "INCOMPLETE").write_text("Packing failed; do not use this output.\n")
        raise
    finally:
        for f in list(open_out.values()) + list(open_old.values()):
            f.close()
    print(f"DONE: {count} shaders, {len(open_out)} packages; output={args.out}", flush=True)

if __name__ == "__main__":
    main()
