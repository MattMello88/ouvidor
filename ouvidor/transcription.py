"""Transcrição local com faster-whisper, em pedaços de cerca de 30 segundos."""

import logging
import os
import queue
import threading
from dataclasses import dataclass

import numpy as np

from . import config as config_mod
from . import cuda
from .audio import RATE

log = logging.getLogger(__name__)

MAX_CHUNK = 30 * RATE
MIN_CHUNK = 24 * RATE
_WINDOW = RATE // 10


@dataclass(order=True)
class Segment:
    start: float
    end: float
    speaker: str
    text: str


class Chunker:
    """Acumula PCM e corta perto de 30 s no ponto mais silencioso."""

    def __init__(self, emit):
        self._emit = emit
        self._buf: list = []
        self._len = 0
        self._start = 0

    def add(self, pcm) -> None:
        self._buf.append(pcm)
        self._len += len(pcm)
        while self._len >= MAX_CHUNK:
            data = np.concatenate(self._buf)
            tail = data[MIN_CHUNK:MAX_CHUNK].astype(np.float32)
            energy = np.square(tail).reshape(-1, _WINDOW).mean(axis=1)
            cut = MIN_CHUNK + int(np.argmin(energy)) * _WINDOW + _WINDOW // 2
            self._push(data[:cut])
            self._buf, self._len = [data[cut:]], len(data) - cut

    def _push(self, data) -> None:
        self._emit(self._start / RATE, data)
        self._start += len(data)

    def flush(self) -> None:
        if self._len:
            self._push(np.concatenate(self._buf))
            self._buf, self._len = [], 0


def load_model(cfg, on_status=lambda text: None):
    """Carrega o Whisper. Em modo auto tenta CUDA e recua para a CPU."""
    from faster_whisper import WhisperModel

    on_status("Carregando modelo (o primeiro uso baixa da internet)…")
    root = str(config_mod.DATA_DIR / "modelos")

    def build(device, **kw):
        args = dict(device=device, download_root=root, **kw)
        try:  # já baixado: não depende de rede
            return WhisperModel(cfg.modelo_whisper, local_files_only=True, **args)
        except Exception:
            log.debug("modelo não carregou localmente", exc_info=True)
            return WhisperModel(cfg.modelo_whisper, **args)

    if cfg.dispositivo in ("auto", "cuda"):
        if cuda.available():
            try:
                model = build("cuda", compute_type="int8_float32")
                # O carregamento passa mesmo sem as DLLs; só a inferência prova.
                list(model.transcribe(np.zeros(RATE, np.float32), language="pt")[0])
                log.info("usando CUDA")
                return model, "cuda"
            except Exception as e:
                log.warning("CUDA indisponível, usando CPU: %s", e)
        elif cfg.dispositivo == "cuda":
            log.warning("CUDA pedido, mas sem GPU ou bibliotecas; usando CPU")
    threads = max(2, (os.cpu_count() or 4) // 2)  # deixa folga para a chamada
    return build("cpu", compute_type="int8", cpu_threads=threads), "cpu"


class Transcriber:
    """Thread que consome pedaços e acumula trechos rotulados."""

    def __init__(self, cfg, on_update=None, on_status=None, loader=load_model):
        self._cfg = cfg
        self._on_update = on_update or (lambda segments: None)
        self._on_status = on_status or (lambda text: None)
        self._loader = loader
        self._queue: queue.Queue = queue.Queue()
        self._chunkers: dict = {}
        self.segments: list[Segment] = []
        self.error: Exception | None = None
        self.device = ""
        self._thread = threading.Thread(target=self._run, name="transcricao", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def feed(self, kind: str, pcm) -> None:
        if self.error is not None:
            return  # o áudio continua no .wav; não acumular em memória
        if kind not in self._chunkers:
            self._chunkers[kind] = Chunker(lambda t, data, k=kind: self._queue.put((k, t, data)))
        self._chunkers[kind].add(pcm)

    def finish(self) -> list[Segment]:
        for c in self._chunkers.values():
            c.flush()
        self._queue.put(None)
        self._thread.join()
        return self.segments

    def _run(self) -> None:
        try:
            model, self.device = self._loader(self._cfg, self._on_status)
            self._on_status("")
            while (item := self._queue.get()) is not None:
                self._transcribe(model, *item)
        except Exception as e:
            log.exception("falha na transcrição")
            self.error = e
            self._on_status(f"Transcrição falhou: {e}. O áudio continua sendo gravado.")

    def _transcribe(self, model, kind, offset, pcm) -> None:
        if np.abs(pcm.astype(np.int32)).max(initial=0) < 50:  # silêncio: nem roda o VAD
            return
        found, _ = model.transcribe(
            pcm.astype(np.float32) / 32768,
            language=self._cfg.idioma or None,
            beam_size=5 if self.device == "cuda" else 1,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        new = [Segment(offset + s.start, offset + s.end, kind, s.text.strip())
               for s in found if s.text.strip()]
        if new:
            self.segments = sorted(self.segments + new)
            self._on_update(self.segments)
