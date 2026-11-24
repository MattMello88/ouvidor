"""Ícone na bandeja do Windows, com Parar e Abrir."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

COLORS = {"parado": "#5b6675", "iniciando": "#5b6675", "gravando": "#d93f34", "processando": "#d99a1c"}
LABELS = {"parado": "parado", "iniciando": "abrindo o áudio", "gravando": "gravando",
          "processando": "processando"}


def make_icon(color: str) -> QIcon:
    """Um microfone branco sobre um círculo da cor do estado."""
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(1, 1, 62, 62)
    p.setBrush(QColor("white"))
    p.drawRoundedRect(QRectF(24, 10, 16, 26), 8, 8)
    p.drawRect(QRectF(30, 44, 4, 8))
    p.drawRect(QRectF(22, 50, 20, 4))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor("white"), 4))
    p.drawArc(QRectF(18, 18, 28, 26), 180 * 16, 180 * 16)
    p.end()
    return QIcon(pix)


class Tray(QSystemTrayIcon):
    def __init__(self, window, on_stop, on_open):
        super().__init__(window)
        self._icons = {state: make_icon(color) for state, color in COLORS.items()}
        self.menu = QMenu(window)
        self.stop_action = self.menu.addAction("Parar", on_stop)
        self.open_action = self.menu.addAction("Abrir", on_open)
        self.setContextMenu(self.menu)
        self.activated.connect(
            lambda reason: on_open() if reason == self.ActivationReason.Trigger else None)
        self.messageClicked.connect(on_open)
        self.set_state("parado")
        self.show()

    def set_state(self, state: str) -> None:
        self.setIcon(self._icons[state])
        self.setToolTip(f"Ouvidor: {LABELS[state]}")
        self.stop_action.setEnabled(state == "gravando")

    def set_time(self, text: str) -> None:
        tip = f"Ouvidor: gravando {text}"
        if tip != self.toolTip():
            self.setToolTip(tip)

    def notify(self, text: str, warning: bool = False) -> None:
        icon = self.MessageIcon.Warning if warning else self.MessageIcon.Information
        self.showMessage("Ouvidor", text, icon)
