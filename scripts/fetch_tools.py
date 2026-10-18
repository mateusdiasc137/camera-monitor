"""Baixa os binários de terceiros fixados em tools/manifest.json e confere o hash (SHA-256 ou SHA-512).

Uso: python scripts/fetch_tools.py [--force]
Resultado: tools/<nome>/<versão>/<binário>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT / "tools"
MANIFEST = TOOLS_DIR / "manifest.json"


def platform_key() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(machine)
    if system not in ("windows", "linux") or arch is None:
        raise SystemExit(f"Plataforma não suportada: {system}/{machine}")
    return f"{system}-{arch}"


def digest_of(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_extract(archive: Path, dest: Path) -> None:
    dest_resolved = dest.resolve()

    def check(name: str) -> None:
        target = (dest / name).resolve()
        if not target.is_relative_to(dest_resolved):
            raise SystemExit(f"Entrada suspeita no arquivo compactado: {name}")

    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for name in zf.namelist():
                check(name)
            zf.extractall(dest)
    else:
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                check(member.name)
                if member.issym() or member.islnk():
                    raise SystemExit(f"Link não esperado no arquivo compactado: {member.name}")
            try:
                tf.extractall(dest, filter="data")
            except TypeError:  # Python 3.11.0-3.11.3 (Debian 12/MX): sem filtro; os nomes já foram conferidos
                tf.extractall(dest)  # noqa: S202


def tool_dir(name: str) -> Path:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return TOOLS_DIR / name / manifest[name]["version"]


def fetch(name: str, spec: dict, force: bool) -> Path:
    key = platform_key()
    entry = spec["assets"].get(key)
    if entry is None:
        raise SystemExit(f"{name}: sem binário para {key} no manifesto")
    dest = TOOLS_DIR / name / spec["version"]
    if dest.exists() and not force:
        print(f"{name} {spec['version']}: já presente em {dest.relative_to(ROOT)}")
        return dest

    url = spec["url"].format(version=spec["version"], asset=entry["asset"])
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / entry["asset"]
        print(f"{name}: baixando {url}")
        with urllib.request.urlopen(url, timeout=120) as resp, archive.open("wb") as out:
            shutil.copyfileobj(resp, out)
        # O hash publicado pelo projeto: SHA-256 (MediaMTX) ou SHA-512 (Caddy).
        algorithm = "sha512" if "sha512" in entry else "sha256"
        actual = digest_of(archive, algorithm)
        if actual != entry[algorithm]:
            raise SystemExit(f"{name}: {algorithm} diverge (esperado {entry[algorithm]}, obtido {actual})")
        print(f"{name}: {algorithm.upper()} conferido")
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        safe_extract(archive, dest)
    print(f"{name} {spec['version']}: instalado em {dest.relative_to(ROOT)}")
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="baixa de novo mesmo se já existir")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for name, spec in manifest.items():
        fetch(name, spec, args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
