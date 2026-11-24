"""Linha de comando: gravar, reprocessar, listar dispositivos e instalar CUDA."""

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from . import config


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="ouvidor", description="Gravador e transcritor de reuniões")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gravar", help="grava até Ctrl+C e depois transcreve e resume")
    g.add_argument("--segundos", type=float, help="para sozinho depois deste tempo")
    g.add_argument("--so-audio", action="store_true", help="grava só os dois .wav")
    r = sub.add_parser("reprocessar", help="refaz transcrição e resumo de uma pasta")
    r.add_argument("pasta", type=Path)
    sub.add_parser("dispositivos", help="lista microfones e saídas")
    sub.add_parser("instalar-cuda", help="baixa as bibliotecas da NVIDIA (mais de 1 GB)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.ERROR)
    cfg = config.load()

    def status(text):
        if text:
            print(text, flush=True)

    if args.cmd == "dispositivos":
        from .audio import list_devices
        mics, outs = list_devices()
        print("Microfones:", *mics, sep="\n  ")
        print("Saídas:", *outs, sep="\n  ")
        return 0
    if args.cmd == "instalar-cuda":
        from . import cuda
        cuda.install(lambda t: print(f"\r{t}   ", end="", flush=True))
        print("\nPronto.")
        return 0

    from .audio import Recorder
    from .session import Session, new_folder, reprocess
    if args.cmd == "reprocessar":
        folder = args.pasta
        problems = reprocess(folder, cfg, status)
    else:
        print("Avise os participantes de que a conversa está sendo gravada (LGPD).")
        if args.so_audio:
            folder = new_folder(cfg.output_dir, datetime.now())
            rec = Recorder(folder, cfg.microfone, cfg.saida, on_status=status)
            rec.start()
            _wait(args.segundos)
            rec.stop()
            problems = [str(rec.error)] if rec.error else []
        else:
            session = Session(cfg, status)
            session.start()
            folder = session.folder
            _wait(args.segundos)
            session.stop_recording()
            problems = session.finish()
    print(f"Pasta: {folder}")
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


def _wait(seconds) -> None:
    print("Gravando. Ctrl+C para parar.", flush=True)
    end = time.monotonic() + seconds if seconds else None
    try:
        while end is None or time.monotonic() < end:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
