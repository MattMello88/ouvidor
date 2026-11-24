"""Bibliotecas CUDA opcionais: detecção e download sob demanda do PyPI."""

import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from .config import DATA_DIR

DLL_DIR = DATA_DIR / "cuda"
PACKAGES = {"nvidia-cublas-cu12": "12.8.4.1", "nvidia-cudnn-cu12": "9.10.2.21"}
_registered = False


def has_gpu() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def installed() -> bool:
    return (DLL_DIR / "cublas64_12.dll").exists() and (DLL_DIR / "cudnn_ops64_9.dll").exists()


def available() -> bool:
    """Há GPU e as DLLs estão na pasta do Ouvidor ou no PATH."""
    global _registered
    if not has_gpu():
        return False
    if installed():
        if not _registered:
            os.add_dll_directory(str(DLL_DIR))
            os.environ["PATH"] = f"{DLL_DIR}{os.pathsep}{os.environ['PATH']}"
            _registered = True
        return True
    return any((Path(d) / "cublas64_12.dll").exists()
               for d in os.environ["PATH"].split(os.pathsep) if d)


def install(on_progress=lambda text: None) -> None:
    """Baixa os wheels oficiais da NVIDIA (mais de 1 GB), confere o SHA-256 e extrai as DLLs."""
    DLL_DIR.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=DLL_DIR.parent) as tmp:
        stage = Path(tmp) / "dlls"
        stage.mkdir()
        for name, version in PACKAGES.items():
            with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=60) as r:
                meta = json.load(r)
            info = next(u for u in meta["urls"] if u["filename"].endswith("win_amd64.whl"))
            wheel = Path(tmp) / "pacote.whl"
            digest, done = hashlib.sha256(), 0
            with urllib.request.urlopen(info["url"], timeout=60) as r, open(wheel, "wb") as out:
                while block := r.read(1 << 20):
                    out.write(block)
                    digest.update(block)
                    done += len(block)
                    on_progress(f"Baixando {name}: {done >> 20} de {info['size'] >> 20} MB")
            if digest.hexdigest() != info["digests"]["sha256"]:
                raise OSError(f"SHA-256 não confere em {info['filename']}")
            with zipfile.ZipFile(wheel) as z:
                for member in z.namelist():
                    if member.lower().endswith(".dll"):
                        with z.open(member) as src, open(stage / Path(member).name, "wb") as dst:
                            shutil.copyfileobj(src, dst, 1 << 20)
            wheel.unlink()
        shutil.rmtree(DLL_DIR, ignore_errors=True)
        os.replace(stage, DLL_DIR)
