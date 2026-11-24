"""Janela flutuante."""

import logging
import math
import os
import threading
from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QMenu, QMessageBox, QPushButton,
    QSystemTrayIcon, QVBoxLayout, QWidget)

from .. import config
from ..audio import MIC, SYSTEM
from ..session import Session, clock, reprocess
from .settings import SettingsDialog
from .tray import Tray

log = logging.getLogger(__name__)

# Só o painel recebe o estilo; diálogos e menus ficam com a aparência do Windows.
STYLE = """
#painel { background: #1d2127; border: 1px solid #3a414c; border-radius: 10px; }
#painel QLabel { color: #e8eaed; font: 9pt "Segoe UI"; }
#painel QLabel#tempo { font: 600 18pt "Segoe UI"; }
#painel QLabel#aviso { color: #1d2127; background: #f2c14e; border-radius: 5px; padding: 5px; }
#painel QLabel#estado { color: #aab2bd; }
#painel QPushButton { color: #e8eaed; background: #323945; border: 1px solid transparent;
    border-radius: 6px; padding: 6px 10px; font: 9pt "Segoe UI"; }
#painel QPushButton:hover { background: #404958; }
#painel QPushButton:focus { border-color: #8ab4f8; }
#painel QPushButton#iniciar:enabled { background: #1f7a4d; }
#painel QPushButton#parar:enabled { background: #b3382f; }
#painel QPushButton:disabled { color: #6b7380; background: #262b33; }
#painel QPushButton#icone { padding: 3px 7px; background: transparent; }
#painel QPushButton#icone:hover { background: #404958; }
"""
LGPD = "Gravando: avise os participantes de que a conversa está sendo gravada (LGPD)."


class Meter(QWidget):
    def __init__(self, name):
        super().__init__()
        self.setFixedHeight(6)
        self.setAccessibleName(name)
        self.setToolTip(name)
        self._value = 0.0

    def set_rms(self, rms: float) -> None:
        db = 20 * math.log10(max(rms, 1e-6))
        target = min(1.0, max(0.0, (db + 60) / 55))
        self._value = max(target, self._value * 0.8)
        self.update()

    def reset(self) -> None:
        self._value = 0.0
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#323945"))
        p.drawRoundedRect(self.rect(), 3, 3)
        if self._value > 0.02:
            p.setBrush(QColor("#e5604d" if self._value > 0.93 else "#4cc38a"))
            p.drawRoundedRect(QRectF(0, 0, self.width() * self._value, self.height()), 3, 3)


class FloatingWindow(QWidget):
    _status = Signal(str)
    _done = Signal(object, list)
    _started = Signal(object, str)

    def __init__(self, cfg=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Ouvidor")
        self.cfg = cfg or config.load()
        self.state = "parado"  # parado | iniciando | gravando | processando
        self.session: Session | None = None
        self.folder: Path | None = None
        self._drag: QPoint | None = None
        self._close_when_done = False
        self._warned = ""
        self._build()
        self.tray = Tray(self, self.stop, self.restore)
        self._status.connect(self._show_status)
        self._done.connect(self._finished)
        self._started.connect(self._on_started)
        self._timer = QTimer(self, interval=80)
        self._timer.timeout.connect(self._tick)
        self._notice_timer = QTimer(self, interval=15_000, singleShot=True)
        self._notice_timer.timeout.connect(self.notice.hide)
        self._set_state("parado")

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)
        panel = QFrame(objectName="painel")
        panel.setStyleSheet(STYLE)
        panel.setFixedWidth(300)
        outer.addWidget(panel)
        box = QVBoxLayout(panel)
        box.setContentsMargins(12, 8, 12, 12)

        top = QHBoxLayout()
        self.time = QLabel("00:00:00", objectName="tempo")
        self.settings_button = self._icon("⚙", "Configurações", self.open_settings)
        self.menu_button = self._icon("⋯", "Mais opções", self._menu)
        top.addWidget(self.time)
        top.addStretch()
        top.addWidget(self.settings_button)
        top.addWidget(self.menu_button)
        self.tray_button = self._icon("—", "Enviar para a bandeja", self.minimize)
        top.addWidget(self.tray_button)
        top.addWidget(self._icon("✕", "Fechar", self.close))
        box.addLayout(top)

        self.meters = {}
        for kind, label in ((MIC, "Eu"), (SYSTEM, "Outro")):
            row = QHBoxLayout()
            text = QLabel(label)
            text.setFixedWidth(40)
            self.meters[kind] = Meter(f"Nível de áudio: {label}")
            row.addWidget(text)
            row.addWidget(self.meters[kind])
            box.addLayout(row)

        self.notice = QLabel(LGPD, objectName="aviso", wordWrap=True)
        self.notice.hide()
        self.status = QLabel("", objectName="estado", wordWrap=True)
        box.addWidget(self.notice)
        box.addWidget(self.status)

        row = QHBoxLayout()
        self.start_button = QPushButton("Iniciar", objectName="iniciar")
        self.stop_button = QPushButton("Parar", objectName="parar")
        self.folder_button = QPushButton("Abrir pasta")
        for b, slot in ((self.start_button, self.start), (self.stop_button, self.stop),
                        (self.folder_button, self.open_folder)):
            b.clicked.connect(lambda checked=False, s=slot: s())
            row.addWidget(b)
        box.addLayout(row)

    def _icon(self, text, tip, slot):
        b = QPushButton(text, objectName="icone")
        b.clicked.connect(lambda checked=False: slot())
        b.setToolTip(tip)
        b.setAccessibleName(tip)
        return b

    # -- ações

    def start(self) -> None:
        if self.state != "parado":
            return
        session = Session(self.cfg, self._status.emit)
        self._set_state("iniciando")

        # Abrir o microfone pode bloquear (consentimento do Windows, Bluetooth).
        def target():
            try:
                session.start()
                self._started.emit(session, "")
            except Exception as e:
                log.exception("falha ao iniciar")
                self._started.emit(None, f"Não foi possível gravar: {e}")

        threading.Thread(target=target, name="inicio", daemon=True).start()

    def _on_started(self, session, problem) -> None:
        if session is None:
            self._set_state("parado")
            self._show_status(problem)
            closing, self._close_when_done = self._close_when_done, False
            if closing:
                self.close()
            return
        self.session, self.folder = session, session.folder
        self.notice.show()
        self._notice_timer.start()
        self._timer.start()
        self._warned = session.recorder.warning
        self._set_state("gravando")
        self._go_to_tray()
        if self._close_when_done:  # pediram para fechar enquanto iniciava
            self.stop()

    def _go_to_tray(self) -> None:
        # Sem bandeja, ou com um diálogo aberto, esconder deixaria o usuário sem acesso.
        if (self._close_when_done or not QSystemTrayIcon.isSystemTrayAvailable()
                or QApplication.activeModalWidget()):
            return
        self.hide()
        self.tray.notify("\n".join(filter(None, (self._warned, LGPD))), bool(self._warned))

    def minimize(self) -> None:
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.hide()
        else:  # sem bandeja, vai para a barra de tarefas
            self.showMinimized()

    def restore(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def stop(self) -> None:
        if self.state != "gravando":
            return
        self._timer.stop()
        self._notice_timer.stop()
        self.notice.hide()
        for m in self.meters.values():
            m.reset()
        session = self.session

        def work():
            session.stop_recording()
            return session.finish()

        self._run(session.folder, work)

    def _run(self, folder, work) -> None:
        self._set_state("processando")

        def target():
            try:
                problems = work()
            except Exception as e:
                log.exception("falha ao processar")
                problems = [str(e)]
            self._done.emit(folder, problems)

        threading.Thread(target=target, name="finalizacao", daemon=True).start()

    def _finished(self, folder, problems) -> None:
        self.session, self.folder = None, folder
        self._set_state("parado")
        self._show_status("\n".join(problems) if problems else "Pronto. Arquivos salvos.")
        closing, self._close_when_done = self._close_when_done, False
        if closing and not problems:
            self.close()
        elif self.isHidden():
            if problems:  # o texto completo do erro está na janela
                self.restore()
            else:
                self.tray.notify("Pronto. Arquivos salvos.")

    def open_folder(self) -> None:
        folder = self.folder or self.cfg.output_dir
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(folder)
        except OSError as e:
            self._show_status(f"Não foi possível abrir a pasta: {e}")

    def choose_reprocess(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Pasta da reunião", str(self.folder or self.cfg.output_dir))
        if path and self.state == "parado":
            folder = Path(path)
            self._run(folder, lambda: reprocess(folder, self.cfg, self._status.emit))

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.cfg, self)
        if dialog.exec():
            self.cfg = dialog.result_config()
            try:
                config.save(self.cfg)
            except OSError as e:
                self._show_status(f"Não foi possível salvar as configurações: {e}")
        dialog.deleteLater()

    def _menu(self) -> None:
        menu = QMenu(self)
        action = menu.addAction("Reprocessar pasta…", self.choose_reprocess)
        action.setEnabled(self.state == "parado")
        menu.exec(self.menu_button.mapToGlobal(self.menu_button.rect().bottomLeft()))
        menu.deleteLater()

    # -- estado

    def _set_state(self, state) -> None:
        self.state = state
        self.start_button.setEnabled(state == "parado")
        self.stop_button.setEnabled(state == "gravando")
        self.settings_button.setEnabled(state == "parado")
        self.tray.set_state(state)
        self._show_status("")

    def _show_status(self, text) -> None:
        if not text and self.state == "gravando" and self.session:
            text = self.session.recorder.warning
        text = text or {"parado": "", "iniciando": "Abrindo o áudio…", "gravando": "Gravando",
                        "processando": "Processando…"}[self.state]
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def _tick(self) -> None:
        rec = self.session.recorder
        self.time.setText(clock(rec.elapsed))
        self.tray.set_time(self.time.text())
        problem = f"Erro na gravação: {rec.error}" if rec.error else rec.warning
        if problem != self._warned:
            self._warned = problem
            if problem and self.isHidden():
                self.tray.notify(problem, True)
        for kind, meter in self.meters.items():
            meter.set_rms(rec.levels[kind])

    # -- janela

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self._drag is not None:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event):
        self._drag = None

    def closeEvent(self, event):
        if self.state == "parado":
            event.accept()
            self.tray.hide()
            QApplication.quit()
            return
        event.ignore()
        if self._close_when_done:
            return
        text = ("Parar a gravação e fechar quando a transcrição terminar?" if self.state == "gravando"
                else "Fechar quando terminar?")
        if QMessageBox.question(self, "Ouvidor", text) != QMessageBox.StandardButton.Yes:
            return
        if self.state == "parado":  # terminou enquanto a pergunta estava aberta
            QTimer.singleShot(0, self.close)
            return
        self._close_when_done = True
        self.stop()
