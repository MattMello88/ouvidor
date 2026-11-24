"""Tela de configurações."""

import threading
from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout, QWidget)

from .. import config, cuda
from ..audio import list_devices

PROVIDERS = {"claude": "Claude", "openai": "OpenAI", "ollama": "Ollama (local e gratuito)",
             "nenhum": "Não gerar resumo"}
LANGUAGES = {"pt": "Português", "en": "Inglês", "es": "Espanhol", "": "Detectar"}
DEVICES = {"auto": "Automático", "cpu": "CPU", "cuda": "GPU NVIDIA"}


class SettingsDialog(QDialog):
    _cuda = Signal(str, bool)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configurações do Ouvidor")
        self.setMinimumWidth(460)
        self._cfg = cfg
        layout = QVBoxLayout(self)
        self.form = QFormLayout()
        layout.addLayout(self.form)

        try:
            mics, outs = list_devices()
        except OSError:
            mics, outs = [], []
        self.microfone = self._device(mics, cfg.microfone)
        self.saida = self._device(outs, cfg.saida)
        self.modelo = self._combo({m: m for m in config.MODELS}, cfg.modelo_whisper)
        self.idioma = self._combo(LANGUAGES, cfg.idioma)
        self.dispositivo = self._combo(DEVICES, cfg.dispositivo)
        self.nome = QLineEdit(cfg.nome_usuario)
        self.pasta = QLineEdit(cfg.pasta_saida)
        self.pasta.setPlaceholderText(str(cfg.output_dir))
        self.provedor = self._combo(PROVIDERS, cfg.provedor)
        self.form.addRow("Microfone", self.microfone)
        self.form.addRow("Saída de áudio", self.saida)
        self.form.addRow("Modelo do Whisper", self.modelo)
        self.form.addRow("Idioma", self.idioma)
        self.form.addRow("Transcrever com", self.dispositivo)
        if cuda.has_gpu() and not cuda.installed():
            self.cuda_button = QPushButton("Baixar aceleração NVIDIA (mais de 1 GB)")
            self.cuda_button.clicked.connect(self._install)
            self.form.addRow("", self.cuda_button)
            self._cuda.connect(self._cuda_progress)
        self.form.addRow("Seu nome", self.nome)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        browse = QPushButton("Escolher…")
        browse.clicked.connect(self._browse)
        h.addWidget(self.pasta)
        h.addWidget(browse)
        self.form.addRow("Pasta de saída", row)
        self.form.addRow("Resumo com", self.provedor)

        self.fields = {}
        self._rows = {"claude": [], "openai": [], "ollama": []}
        for provider, name, label in (
            ("claude", "chave_claude", "Chave de API"), ("claude", "modelo_claude", "Modelo"),
            ("openai", "chave_openai", "Chave de API"), ("openai", "modelo_openai", "Modelo"),
            ("ollama", "modelo_ollama", "Modelo"), ("ollama", "url_ollama", "Endereço"),
        ):
            edit = QLineEdit(getattr(cfg, name))
            if name.startswith("chave"):
                edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.fields[name] = edit
            self.form.addRow(label, edit)
            self._rows[provider].append(edit)
        self.privacy = QLabel("A transcrição é enviada a este provedor. O áudio não sai do computador.",
                              wordWrap=True)
        layout.addWidget(self.privacy)
        self.provedor.currentIndexChanged.connect(self._toggle)
        self._toggle()

        buttons = QDialogButtonBox()
        buttons.addButton("Salvar", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Cancelar", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _combo(self, options, current):
        c = QComboBox()
        for value, label in options.items():
            c.addItem(label, value)
        if c.findData(current) < 0:
            c.addItem(current, current)
        c.setCurrentIndex(c.findData(current))
        return c

    def _device(self, names, current):
        return self._combo({"": "Padrão do Windows", **{n: n for n in names}}, current)

    def _toggle(self) -> None:
        provider = self.provedor.currentData()
        for p, widgets in self._rows.items():
            for w in widgets:
                self.form.setRowVisible(w, p == provider)
        self.privacy.setVisible(provider in ("claude", "openai"))
        self.adjustSize()

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Pasta de saída", str(self._cfg.output_dir))
        if path:
            self.pasta.setText(path)

    def _install(self) -> None:
        self.cuda_button.setEnabled(False)

        def emit(text, failed=False):
            try:
                self._cuda.emit(text, failed)
            except RuntimeError:  # o diálogo já foi fechado; o download continua
                pass

        def work():
            try:
                cuda.install(emit)
                emit("Aceleração NVIDIA instalada")
            except Exception as e:
                emit(f"Falhou, tente de novo: {e}", True)

        threading.Thread(target=work, daemon=True).start()

    def _cuda_progress(self, text, failed) -> None:
        self.cuda_button.setText(text)
        self.cuda_button.setEnabled(failed)

    def result_config(self) -> config.Config:
        values = {n: e.text().strip() for n, e in self.fields.items()}
        return replace(
            self._cfg, microfone=self.microfone.currentData(), saida=self.saida.currentData(),
            modelo_whisper=self.modelo.currentData(), idioma=self.idioma.currentData(),
            dispositivo=self.dispositivo.currentData(), provedor=self.provedor.currentData(),
            nome_usuario=self.nome.text().strip(), pasta_saida=self.pasta.text().strip(), **values)
