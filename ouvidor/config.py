"""Configurações em %APPDATA%/Ouvidor/config.json. Chaves de API ficam cifradas (DPAPI)."""

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from . import winapi

DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Ouvidor"
CONFIG_PATH = Path(os.environ.get("APPDATA", Path.home())) / "Ouvidor" / "config.json"

MODELS = ("tiny", "base", "small", "medium", "large-v3")
_SECRETS = ("chave_claude", "chave_openai")


@dataclass
class Config:
    microfone: str = ""  # nome do dispositivo; vazio = padrão do Windows
    saida: str = ""
    modelo_whisper: str = "small"
    idioma: str = "pt"  # vazio = detectar
    dispositivo: str = "auto"  # auto | cpu | cuda
    provedor: str = "claude"  # claude | openai | ollama | nenhum
    chave_claude: str = ""
    modelo_claude: str = "claude-opus-5-5"
    chave_openai: str = ""
    modelo_openai: str = ""
    modelo_ollama: str = "qwen3:8b"
    url_ollama: str = "http://localhost:11434"
    nome_usuario: str = "Matheus"
    pasta_saida: str = ""

    @property
    def output_dir(self) -> Path:
        return Path(self.pasta_saida) if self.pasta_saida else winapi.documents_dir() / "Ouvidor"


def load(path: Path = CONFIG_PATH) -> Config:
    try:
        raw = json.loads(path.read_text("utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("config.json não é um objeto")
    except FileNotFoundError:
        return Config()
    except ValueError:
        # Preserva o arquivo ilegível em vez de sobrescrevê-lo no próximo salvamento.
        os.replace(path, path.with_suffix(".invalido.json"))
        return Config()
    cfg = Config()
    for f in fields(Config):
        if isinstance(raw.get(f.name), str):
            setattr(cfg, f.name, raw[f.name])
    for name in _SECRETS:
        value = getattr(cfg, name)
        if value.startswith("dpapi:"):
            try:
                setattr(cfg, name, winapi.unprotect(value[6:]))
            except (OSError, ValueError):  # arquivo copiado de outro usuário ou máquina
                setattr(cfg, name, "")
    return cfg


def save(cfg: Config, path: Path = CONFIG_PATH) -> None:
    raw = asdict(cfg)
    for name in _SECRETS:
        if raw[name]:
            raw[name] = "dpapi:" + winapi.protect(raw[name])
    write_atomic(path, json.dumps(raw, indent=2, ensure_ascii=False))


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, "utf-8")
    os.replace(tmp, path)
