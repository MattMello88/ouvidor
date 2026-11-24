"""Uma reunião do início ao fim: gravação, transcrição, resumo e arquivos."""

import logging
from datetime import datetime
from pathlib import Path

from . import summary
from .audio import MIC, SYSTEM, WAV_NAMES, Recorder, read_wav
from .config import Config, write_atomic
from .transcription import Transcriber

log = logging.getLogger(__name__)

TRANSCRIPT, SUMMARY = "transcricao.md", "resumo.md"
_TITLE = "Reunião de %d/%m/%Y às %H:%M"


def clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02}:{s // 60 % 60:02}:{s % 60:02}"


def format_lines(segments) -> str:
    return "\n\n".join(
        f"**[{clock(s.start)}] {'Eu' if s.speaker == MIC else 'Outro'}:** {s.text}" for s in segments)


def new_folder(base: Path, now: datetime) -> Path:
    name, n = now.strftime("%Y-%m-%d_%H-%M-%S"), 1
    while True:
        folder = base / (name if n == 1 else f"{name}_{n}")
        try:
            folder.mkdir(parents=True)
            return folder
        except FileExistsError:
            n += 1


class Session:
    """Os callbacks podem vir de qualquer thread."""

    def __init__(self, cfg: Config, on_status=None, transcriber_factory=Transcriber):
        self._cfg = cfg
        self._on_status = on_status or (lambda text: None)
        self.recorder: Recorder | None = None
        self.folder: Path | None = None
        self.title = ""
        self._transcriber = transcriber_factory(
            cfg, on_update=self._save_transcript, on_status=self._on_status)

    def start(self) -> None:
        now = datetime.now()
        self.folder = new_folder(self._cfg.output_dir, now)
        self.title = now.strftime(_TITLE)
        self.recorder = Recorder(
            self.folder, self._cfg.microfone, self._cfg.saida,
            on_pcm=self._transcriber.feed, on_status=self._on_status)
        try:
            self.recorder.start()
        except Exception:
            try:
                self.folder.rmdir()
            except OSError:
                pass
            raise
        self._transcriber.start()

    def _save_transcript(self, segments) -> None:
        body = format_lines(segments) or "_Nenhuma fala detectada._"
        write_atomic(self.folder / TRANSCRIPT, f"# {self.title}\n\n{body}\n")

    def stop_recording(self) -> None:
        self.recorder.stop()

    def load_audio(self, folder: Path) -> None:
        """Alimenta a transcrição com os .wav de uma reunião já gravada."""
        names = {k: n for k, n in WAV_NAMES.items() if (folder / n).exists()}
        if not names:
            raise FileNotFoundError(f"nenhum áudio do Ouvidor em {folder}")
        self.folder = folder
        try:
            self.title = datetime.strptime(folder.name[:19], "%Y-%m-%d_%H-%M-%S").strftime(_TITLE)
        except ValueError:
            self.title = folder.name
        self._transcriber.start()
        for kind, name in names.items():
            for pcm in read_wav(folder / name):
                self._transcriber.feed(kind, pcm)

    def finish(self) -> list[str]:
        """Termina transcrição e resumo. Devolve os problemas; vazio se deu tudo certo."""
        self._on_status("Finalizando transcrição…")
        segments = self._transcriber.finish()
        problems = []
        if self.recorder and self.recorder.error:
            problems.append(f"Gravação incompleta: {self.recorder.error}")
        if self.recorder and MIC not in self.recorder.opened:
            problems.append("O microfone não foi capturado. Veja a permissão em Configurações do "
                            "Windows > Privacidade > Microfone.")
        if self.recorder and SYSTEM not in self.recorder.opened:
            problems.append("O áudio do sistema não foi capturado.")
        if self._transcriber.error:
            problems.append(f"Transcrição incompleta: {self._transcriber.error}. "
                            "Use Reprocessar para tentar de novo.")
        else:
            self._save_transcript(segments)
            problems += self._summarize(segments)
        self._on_status("")
        return problems

    def _summarize(self, segments) -> list[str]:
        if not segments:
            write_atomic(self.folder / SUMMARY, f"# {self.title}\n\n_Nenhuma fala detectada._\n")
            return []
        if self._cfg.provedor == "nenhum":
            return []
        self._on_status("Gerando resumo…")
        try:
            text = summary.summarize(format_lines(segments), self._cfg)
        except Exception as e:
            log.exception("falha no resumo")
            return [f"Resumo não gerado: {e}. A transcrição foi salva."]
        write_atomic(self.folder / SUMMARY, f"# {self.title}\n\n{text}\n")
        return []


def reprocess(folder: Path, cfg: Config, on_status=None, transcriber_factory=Transcriber) -> list[str]:
    """Refaz transcrição e resumo a partir dos .wav de uma pasta."""
    session = Session(cfg, on_status, transcriber_factory)
    session.load_audio(folder)
    return session.finish()
