import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from ouvidor.config import Config
from ouvidor.ui import window as mod


class FakeRecorder:
    elapsed, error, warning = 5.0, None, ""
    levels = {"eu": 0.1, "outro": 0.0}


class FakeSession:
    problems, failure = [], None

    def __init__(self, cfg, on_status=None):
        self.recorder, self.folder = FakeRecorder(), cfg.output_dir

    def start(self):
        if self.failure:
            raise OSError(self.failure)

    def stop_recording(self):
        pass

    def finish(self):
        return self.problems


@pytest.fixture
def win(monkeypatch):
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    monkeypatch.setattr(mod, "Session", FakeSession)
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
    w = mod.FloatingWindow(Config())
    w.messages = []
    monkeypatch.setattr(w.tray, "notify", lambda text, warning=False: w.messages.append(text))
    w.show()
    yield w
    w._timer.stop()
    w.state = "parado"
    w.close()


def wait(w, state):
    end = time.monotonic() + 3
    while w.state != state and time.monotonic() < end:
        QTest.qWait(10)
    assert w.state == state


def test_iniciar_vai_para_bandeja_e_parar(win):
    tray = win.tray
    assert [a.text() for a in tray.menu.actions()] == ["Parar", "Abrir"]
    assert not tray.stop_action.isEnabled()
    win.start_button.click()
    wait(win, "gravando")
    assert win.isHidden() and tray.stop_action.isEnabled()
    assert "LGPD" in win.messages[0]
    tray.stop_action.trigger()
    wait(win, "parado")
    assert win.isHidden() and win.messages[-1].startswith("Pronto")


def test_abrir_mantem_gravacao(win):
    win.start()
    wait(win, "gravando")
    win.tray.open_action.trigger()
    assert win.isVisible() and win.state == "gravando"
    win.tray_button.click()
    assert win.isHidden() and win.state == "gravando"
    win.tray.open_action.trigger()
    assert win.isVisible()


def test_falha_ao_iniciar_mantem_janela(win, monkeypatch):
    monkeypatch.setattr(FakeSession, "failure", "sem áudio")
    win.start()
    wait(win, "parado")
    assert win.isVisible() and "sem áudio" in win.status.text()


def test_problema_no_fim_reabre_janela(win, monkeypatch):
    monkeypatch.setattr(FakeSession, "problems", ["Resumo não gerado"])
    win.start()
    wait(win, "gravando")
    win.tray.stop_action.trigger()
    wait(win, "parado")
    assert win.isVisible() and "Resumo" in win.status.text()


def test_aviso_de_microfone_na_bandeja(win):
    win.start()
    wait(win, "gravando")
    win.session.recorder.warning = "Sem captura de microfone"
    win._tick()
    win._tick()
    assert win.messages.count("Sem captura de microfone") == 1


def test_sem_bandeja_nao_esconde(win, monkeypatch):
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False))
    win.start()
    wait(win, "gravando")
    assert win.isVisible()
