#!/usr/bin/env python3
"""Extract only relevant Pokopia SPIR-V entries from existing Vulkan shader libraries.

No shader translation, APK build, shader-library rewriting, or network access.
The generated ZIP contains already-compiled SPIR-V blobs and small metadata.
Compressed modules remain .spv.zst (no zstandard Python package required).
"""
import json
import os
from pathlib import Path
import struct
import sys
from zipfile import ZipFile, ZIP_DEFLATED

from vk_android_delta import Library

KEYS = (
    "3fb06ea7d8ed1272",  # world scene: early main pass
    "754327f84eca6eed",  # world scene: early main pass
    "15882e6f064fef79",  # lighting/composition
    "25fbeb7088c91840",  # lighting/composition
    "bd28fb4309ff5d77",  # lighting/composition
    "4e7e5539bd1600d2",  # light-effects pass
    "97b3b56827e33df2",  # late composite
    "ed5f4c6386ca621d",  # late composite
)
BIND = struct.Struct("<BBHBBHhHBBhHHI")
DEFAULT_PACK = Path.home() / "storage/downloads/Pokopia-Pack-Shaders-Novos"
DEFAULT_OUT = Path.home() / "storage/downloads/BananaNative-Pokopia-Sky-SPIRV.zip"


def binding_records(data):
    if len(data) % BIND.size:
        raise ValueError("Misaligned binding records")
    names = ("kind", "descriptor", "binding", "set", "flags", "count", "reg",
             "offset", "sampler_kind", "dim", "sampler_reg", "sampler_offset",
             "reserved", "bytes")
    return [dict(zip(names, BIND.unpack_from(data, i)))
            for i in range(0, len(data), BIND.size)]


def run():
    root = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else DEFAULT_PACK
    out = Path(sys.argv[2]).expanduser() if len(sys.argv) > 2 else DEFAULT_OUT
    libs = sorted((root / "shaders").glob("*/dmsh_vk.lib"))
    if not libs:
        raise SystemExit(f"Nenhuma biblioteca dmsh_vk.lib encontrada em {root / 'shaders'}")
    out.parent.mkdir(parents=True, exist_ok=True)
    records, found = [], set()
    with ZipFile(out, "w", ZIP_DEFLATED, compresslevel=3) as z:
        for path in libs:
            try:
                library = Library(str(path))  # mmap: does not load the 3+ GB library into RAM
            except Exception as exc:
                print(f"Aviso: biblioteca ignorada {path}: {exc}", flush=True)
                continue
            print(f"Examinando {path.parent.name}: {library.ne} entradas", flush=True)
            try:
                for key in KEYS:
                    i = library.index.get(key)
                    if i is None:
                        continue
                    _, entry, bindings, c1, modules = library.entry(i)
                    prefix = f"{path.parent.name}/{key}"
                    z.writestr(f"{prefix}/entry.bin", entry)
                    z.writestr(f"{prefix}/bindings.bin", bindings)
                    if c1:
                        z.writestr(f"{prefix}/c1.bin", c1)
                    mod_records = []
                    for variant, original_size, stored, codec in modules:
                        if codec not in (0, 1):
                            raise ValueError(f"Unsupported shader codec: {codec}")
                        suffix = ".spv.zst" if codec == 1 else ".spv"
                        name = f"{prefix}/variant-{variant}{suffix}"
                        z.writestr(name, bytes(stored))
                        mod_records.append({
                            "variant": variant, "filename": name, "codec": codec,
                            "decompressed_bytes": original_size,
                            "stored_bytes": len(stored),
                        })
                    record = {
                        "key": key, "library": path.parent.name, "game": library.game,
                        "chain": library.chain, "vk_chain": library.version,
                        "abi": library.abi, "bindings": binding_records(bindings),
                        "modules": mod_records,
                    }
                    records.append(record)
                    found.add(key)
                    print(f"  {key}: {len(modules)} módulos; {len(bindings)//BIND.size} bindings", flush=True)
            finally:
                library.close()
        z.writestr("index.json", json.dumps({
            "requested": list(KEYS), "found": sorted(found),
            "missing": sorted(set(KEYS) - found), "entries": records,
        }, ensure_ascii=False, indent=2))
    print(f"\nZIP criado: {out}\nTamanho: {out.stat().st_size / 1048576:.2f} MiB")
    if set(KEYS) - found:
        print("Não encontrados:", ", ".join(sorted(set(KEYS) - found)))
    print("Nenhuma APK ou biblioteca de shaders foi modificada.")


if __name__ == "__main__":
    run()
