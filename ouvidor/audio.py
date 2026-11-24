"""Captura do microfone e da saída do sistema (WASAPI loopback)."""

import logging
import queue
import threading
import time
import wave
from pathlib import Path

import numpy as np
import pyaudiowpatch as pa
import soxr

from . import winapi

log = logging.getLogger(__name__)

RATE = 16_000
GAP = 0.3  # s: atraso acima disto é buraco e vira silêncio
MARGIN = 1.0  # s: folga mantida ao preencher silêncio sem receber blocos
WATCHDOG = 4.0  # s: microfone sem entregar blocos é considerado morto

MIC, SYSTEM = "eu", "outro"
WAV_NAMES = {MIC: "microfone.wav", SYSTEM: "sistema.wav"}


def list_devices() -> tuple[list[str], list[str]]:
    """Nomes de (microfones, saídas) WASAPI."""
    p = pa.PyAudio()
    try:
        devs = list(p.get_device_info_generator_by_host_api(host_api_type=pa.paWASAPI))
        mics = [d["name"] for d in devs if d["maxInputChannels"] > 0 and not d["isLoopbackDevice"]]
        outs = [d["name"] for d in devs if d["maxOutputChannels"] > 0]
        return mics, outs
    finally:
        p.terminate()


def find_device(p, kind: str, name: str) -> dict:
    """Dispositivo configurado ou, se ele não estiver presente, o padrão do Windows."""
    devs = list(p.get_device_info_generator_by_host_api(host_api_type=pa.paWASAPI))
    if kind == MIC:
        for d in devs:
            if name and d["name"] == name and d["maxInputChannels"] > 0 and not d["isLoopbackDevice"]:
                return d
        index = p.get_host_api_info_by_type(pa.paWASAPI)["defaultInputDevice"]
        if index < 0:
            raise OSError("nenhum microfone disponível")
        return p.get_device_info_by_index(index)
    for d in devs:
        if name and d["isLoopbackDevice"] and d["name"].startswith(name):
            return d
    try:
        return p.get_default_wasapi_loopback()
    except LookupError as e:
        raise OSError("nenhuma saída de áudio disponível") from e


class Timeline:
    """Converte blocos nativos em PCM 16 kHz mono contínuo, alinhado ao relógio.

    O loopback não entrega blocos quando nada toca, e trocas de dispositivo
    deixam buracos. Tudo isso é preenchido com silêncio para que os dois
    canais fiquem sincronizados.
    """

    def __init__(self, sink):
        self._sink = sink
        self._resampler = None
        self._rate = None
        self._base = 0.0  # segundos já cobertos por formatos anteriores
        self._native = 0  # amostras nativas no formato atual

    @property
    def covered(self) -> float:
        return self._base + (self._native / self._rate if self._rate else 0.0)

    def set_rate(self, rate: int) -> None:
        self._flush()
        self._rate = rate
        self._resampler = soxr.ResampleStream(rate, RATE, 1, dtype="float32")

    def _flush(self) -> None:
        if self._resampler is not None:
            self._emit(self._resampler.resample_chunk(np.zeros(0, np.float32), last=True))
            self._base, self._native = self.covered, 0
            self._resampler = None

    def _emit(self, samples) -> None:
        if len(samples):
            self._sink((np.clip(samples, -1, 1) * 32767).astype(np.int16))

    def _feed(self, mono) -> None:
        self._native += len(mono)
        self._emit(self._resampler.resample_chunk(mono))

    def add(self, mono, end_time: float) -> None:
        """Bloco cujo fim ocorreu em `end_time` segundos após o início."""
        self.fill(end_time - len(mono) / self._rate, GAP)
        self._feed(mono)

    def fill(self, until: float, tolerance: float = 0.0) -> None:
        missing = until - self.covered
        if missing <= tolerance:
            return
        if self._resampler is None:
            self.set_rate(RATE)
        left = round(missing * self._rate)
        while left > 0:  # fatias de 1 s para não alocar muito de uma vez
            n = min(left, self._rate)
            self._feed(np.zeros(n, np.float32))
            left -= n

    def finish(self, until: float) -> None:
        self.fill(until)
        self._flush()


class Recorder:
    """Grava os dois canais em .wav e entrega o PCM a `on_pcm(canal, int16)`."""

    def __init__(self, folder: Path, mic="", output="", on_pcm=None, on_status=None):
        self._names = {MIC: mic, SYSTEM: output}
        self._on_pcm = on_pcm or (lambda kind, pcm: None)
        self._on_status = on_status or (lambda text: None)
        self._folder = folder
        self._blocks: queue.SimpleQueue = queue.SimpleQueue()
        self._stop = threading.Event()
        self._streams: dict = {}
        self._gen = 0
        self._pa = None
        self._last_block = {MIC: 0.0, SYSTEM: 0.0}
        self.levels = {MIC: 0.0, SYSTEM: 0.0}
        self.error: Exception | None = None
        self.warning = ""  # canal sem captura neste momento
        self.opened: set = set()  # canais que abriram ao menos uma vez

    # -- ciclo de vida

    def start(self) -> None:
        self._fingerprint = self._read_fingerprint()
        self._waves, self._lines = {}, {}
        self.t0 = time.monotonic()
        self._open()
        if not self._streams:
            self._close()
            raise OSError("não foi possível abrir o microfone nem a saída do sistema")
        try:
            for kind, name in WAV_NAMES.items():
                w = wave.open(str(self._folder / name), "wb")
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(RATE)
                self._waves[kind] = w
                self._lines[kind] = Timeline(lambda pcm, k=kind: self._write(k, pcm))
        except OSError:
            self._close()
            raise
        self._writer = threading.Thread(target=self._write_loop, name="captura", daemon=True)
        self._monitor = threading.Thread(target=self._monitor_loop, name="monitor", daemon=True)
        self._writer.start()
        self._monitor.start()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    def stop(self) -> None:
        self._end = self.elapsed
        self._stop.set()
        self._monitor.join()
        self._close()
        self._blocks.put(None)
        self._writer.join()
        self.levels = {MIC: 0.0, SYSTEM: 0.0}

    # -- streams

    def _open(self) -> None:
        self._pa = pa.PyAudio()
        self._gen += 1
        self._open_missing()

    def _open_missing(self) -> None:
        problems = []
        for kind in (MIC, SYSTEM):
            if kind in self._streams:
                continue
            try:
                dev = find_device(self._pa, kind, self._names[kind])
                rate = int(dev["defaultSampleRate"])
                ch = max(1, int(dev["maxInputChannels"]))
                self._last_block[kind] = time.monotonic()
                self._streams[kind] = self._pa.open(
                    format=pa.paInt16, channels=ch, rate=rate, input=True,
                    input_device_index=dev["index"], frames_per_buffer=rate // 20,
                    stream_callback=self._callback(kind, ch, rate),
                )
                self.opened.add(kind)
                log.info("%s: %s (%d Hz, %d canais)", kind, dev["name"], rate, ch)
            except OSError as e:
                log.warning("falha ao abrir %s: %s", kind, e)
                problems.append("microfone" if kind == MIC else "áudio do sistema")
        self.warning = f"Sem captura de {' e '.join(problems)}" if problems else ""
        self._on_status(self.warning)

    def _close(self) -> None:
        for s in self._streams.values():
            try:
                s.close()
            except OSError:
                log.debug("erro ao fechar stream", exc_info=True)
        self._streams = {}
        if self._pa:
            self._pa.terminate()
            self._pa = None

    def _callback(self, kind, channels, rate):
        gen = self._gen

        def cb(data, frames, info, status):
            now = time.monotonic()
            self._last_block[kind] = now
            self._blocks.put((kind, gen, channels, rate, data, now - self.t0))
            return None, pa.paContinue

        return cb

    # -- escrita

    def _write(self, kind, pcm) -> None:
        self._waves[kind].writeframes(pcm.tobytes())
        self._on_pcm(kind, pcm)

    def _write_loop(self) -> None:
        formats = {}
        try:
            while True:
                try:
                    item = self._blocks.get(timeout=0.25)
                except queue.Empty:
                    item = ()
                if item is None:
                    break
                if item:
                    kind, gen, ch, rate, data, end = item
                    mono = np.frombuffer(data, np.int16).reshape(-1, ch).mean(axis=1, dtype=np.float32)
                    mono /= 32768
                    if formats.get(kind) != (gen, rate):
                        formats[kind] = (gen, rate)
                        self._lines[kind].set_rate(rate)
                    self.levels[kind] = float(np.sqrt(np.mean(mono**2))) if len(mono) else 0.0
                    self._lines[kind].add(mono, end)
                # Só preenche com a fila vazia: atraso de escrita não é silêncio.
                if self._blocks.empty():
                    now = time.monotonic()
                    for kind, line in self._lines.items():
                        line.fill(now - self.t0 - MARGIN, GAP)
                        if now - self._last_block[kind] > 0.5:
                            self.levels[kind] = 0.0
            for line in self._lines.values():
                line.finish(self._end)
        except Exception as e:  # disco cheio etc.: precisa chegar à interface
            log.exception("falha na gravação")
            self.error = e
            self._on_status(f"Erro na gravação: {e}")
        finally:
            for w in self._waves.values():
                try:
                    w.close()
                except OSError:
                    log.exception("falha ao fechar o .wav")

    # -- troca de dispositivo

    def _read_fingerprint(self):
        try:
            return winapi.audio_fingerprint()
        except OSError:
            log.debug("não foi possível ler os dispositivos", exc_info=True)
            return None

    def _needs_reconnect(self) -> bool:
        current = self._read_fingerprint()
        if current is not None and current != self._fingerprint:
            log.info("dispositivos de áudio mudaram")
            return True
        for kind in (MIC, SYSTEM):
            stream = self._streams.get(kind)
            try:
                if stream is not None and not stream.is_active():
                    return True
            except OSError:
                return True
        return MIC in self._streams and time.monotonic() - self._last_block[MIC] > WATCHDOG

    def _monitor_loop(self) -> None:
        wait, last = 2.0, time.monotonic()
        while not self._stop.wait(1.0):
            since = time.monotonic() - last
            if since < wait or self.error:
                continue
            reconnect = self._needs_reconnect()
            if not reconnect and len(self._streams) == 2:
                continue
            # Trocas em sequência (Bluetooth alternando perfil) aumentam a espera.
            wait = min(wait * 2, 30.0) if since < 60 else 2.0
            last = time.monotonic()
            if not reconnect:  # só falta um canal: tenta abri-lo sem parar o outro
                self._open_missing()
                continue
            self._on_status("Reconectando áudio…")
            # A lista do PortAudio só é atualizada após encerrar tudo.
            self._close()
            self._fingerprint = self._read_fingerprint()
            try:
                self._open()
            except Exception as e:
                log.exception("falha ao reconectar")
                self._on_status(f"Sem áudio: {e}")


def read_wav(path: Path):
    """Gera blocos de 1 s em int16 de um .wav gravado pelo Ouvidor."""
    with wave.open(str(path), "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (RATE, 1, 2):
            raise ValueError(f"{path.name}: esperado WAV 16 kHz mono de 16 bits")
        while data := w.readframes(RATE):
            yield np.frombuffer(data, np.int16)
