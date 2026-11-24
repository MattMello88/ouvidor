"""Ponto de entrada da janela. Com `--autoteste ARQUIVO PASTA`, valida o pacote sem interface."""

import json
import logging
import logging.handlers
import os
import sys
from pathlib import Path

from ouvidor import config


def _setup_logging() -> None:
    if sys.stderr is None:  # .exe sem console: tqdm e outros escrevem aqui
        sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        config.DATA_DIR / "ouvidor.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format="%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s")


def _self_test(report: Path, folder: Path) -> int:
    try:
        import anthropic

        from ouvidor.audio import list_devices
        from ouvidor.session import TRANSCRIPT, reprocess
        from ouvidor.ui import window  # noqa: F401

        result = {"dispositivos": list_devices(), "anthropic": anthropic.__version__,
                  "problemas": reprocess(folder, config.Config(provedor="nenhum")),
                  "transcricao": (folder / TRANSCRIPT).read_text("utf-8")}
        ok = not result["problemas"]
    except Exception as e:
        logging.exception("autoteste")
        result, ok = {"erro": repr(e)}, False
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    return 0 if ok else 1


def main() -> int:
    _setup_logging()
    if len(sys.argv) == 4 and sys.argv[1] == "--autoteste":
        return _self_test(Path(sys.argv[2]), Path(sys.argv[3]))
    from PySide6.QtWidgets import QApplication

    from ouvidor.ui.window import FloatingWindow

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # a janela some na bandeja; fechar encerra
    window = FloatingWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
