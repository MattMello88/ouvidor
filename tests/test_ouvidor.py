import json
import threading
import wave
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import numpy as np
import pytest

from ouvidor import config, summary, winapi
from ouvidor.audio import MIC, RATE, SYSTEM, WAV_NAMES, Timeline
from ouvidor.session import SUMMARY, TRANSCRIPT, clock, format_lines, new_folder, reprocess
from ouvidor.transcription import MAX_CHUNK, MIN_CHUNK, Chunker, Segment, Transcriber


# --- áudio

def collect():
    out = []
    return out, Timeline(out.append)


@pytest.mark.parametrize("rate", [16000, 44100, 48000])
def test_timeline_tamanho_e_amplitude(rate):
    out, line = collect()
    line.set_rate(rate)
    step = rate // 20
    t = np.arange(rate * 3) / rate
    tone = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    for i in range(0, len(tone), step):
        line.add(tone[i:i + step], (i + step) / rate)
    line.finish(3.0)
    pcm = np.concatenate(out)
    assert abs(len(pcm) - 3 * RATE) <= 2
    assert abs(np.abs(pcm[RATE:2 * RATE]).max() / 32767 - 0.5) < 0.02


def test_timeline_preenche_buraco_e_troca_de_taxa():
    out, line = collect()
    line.set_rate(48000)
    line.add(np.ones(48000, np.float32) * 0.5, 1.0)
    line.set_rate(44100)  # dispositivo trocado; o próximo bloco só começa em 5 s
    line.add(np.ones(44100, np.float32) * 0.5, 6.0)
    line.finish(7.0)
    pcm = np.concatenate(out)
    assert abs(len(pcm) - 7 * RATE) <= 4
    assert np.abs(pcm[int(1.2 * RATE):int(4.8 * RATE)]).max() < 100
    assert pcm[int(5.5 * RATE)] > 15000
    assert np.abs(pcm[int(6.2 * RATE):]).max() < 100


def test_timeline_sem_nenhum_bloco_vira_silencio():
    out, line = collect()
    line.finish(2.5)
    assert abs(sum(map(len, out)) - 2.5 * RATE) <= 2


def test_timeline_ignora_jitter():
    out, line = collect()
    line.set_rate(RATE)
    for i in range(1, 11):
        line.add(np.zeros(800, np.float32), i * 0.05 + (0.2 if i % 3 == 0 else 0))
    line.finish(0.5)
    assert abs(sum(map(len, out)) - 8000) <= 2


# --- pedaços e transcrição

def test_chunker_corta_no_silencio_sem_perder_amostras():
    got = []
    c = Chunker(lambda t, d: got.append((t, d)))
    data = np.random.default_rng(0).integers(-9000, 9000, 70 * RATE).astype(np.int16)
    data[27 * RATE:28 * RATE] = 0
    for i in range(0, len(data), 1000):
        c.add(data[i:i + 1000])
    c.flush()
    assert np.array_equal(np.concatenate([d for _, d in got]), data)
    assert 27 <= len(got[0][1]) / RATE <= 28
    assert all(MIN_CHUNK <= len(d) <= MAX_CHUNK for _, d in got[:-1])
    assert got[1][0] == len(got[0][1]) / RATE


class FakeModel:
    def transcribe(self, audio, **kw):
        return [SimpleNamespace(start=1.0, end=2.0, text=" olá ")], None


def fake_factory(cfg, **kw):
    return Transcriber(cfg, loader=lambda c, s: (FakeModel(), "cpu"), **kw)


def failing_factory(cfg, **kw):
    def loader(c, s):
        raise OSError("sem internet")
    return Transcriber(cfg, loader=loader, **kw)


def test_transcriber_offsets_e_ordem():
    t = fake_factory(config.Config())
    t.start()
    t.feed(SYSTEM, np.full(40 * RATE, 1000, np.int16))
    t.feed(MIC, np.full(RATE * 5, 1000, np.int16))
    segs = t.finish()
    assert [s.start for s in segs] == sorted(s.start for s in segs)
    assert {s.speaker for s in segs} == {MIC, SYSTEM}
    assert segs[-1].start > 20


# --- sessão

@pytest.fixture
def meeting(tmp_path):
    for name in WAV_NAMES.values():
        with wave.open(str(tmp_path / name), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(np.full(RATE * 3, 1000, np.int16).tobytes())
    return tmp_path


def test_reprocessar_gera_arquivos(meeting, monkeypatch):
    monkeypatch.setattr(summary, "summarize", lambda text, cfg: "## Resumo\nok")
    assert reprocess(meeting, config.Config(), transcriber_factory=fake_factory) == []
    text = (meeting / TRANSCRIPT).read_text("utf-8")
    assert "**[00:00:01] Eu:** olá" in text and "**[00:00:01] Outro:** olá" in text
    assert "## Resumo" in (meeting / SUMMARY).read_text("utf-8")


def test_falha_no_modelo_nao_sobrescreve_transcricao(meeting):
    (meeting / TRANSCRIPT).write_text("anterior", "utf-8")
    problems = reprocess(meeting, config.Config(), transcriber_factory=failing_factory)
    assert "sem internet" in problems[0] and not (meeting / SUMMARY).exists()
    assert (meeting / TRANSCRIPT).read_text("utf-8") == "anterior"


def test_falha_no_resumo_mantem_transcricao(meeting):
    problems = reprocess(meeting, config.Config(), transcriber_factory=fake_factory)
    assert "chave" in problems[0] and (meeting / TRANSCRIPT).exists()


def test_reprocessar_sem_audio(tmp_path):
    with pytest.raises(FileNotFoundError):
        reprocess(tmp_path, config.Config())


def test_pasta_unica_e_formatos(tmp_path):
    now = datetime(2026, 1, 2, 3, 4, 5)
    assert new_folder(tmp_path, now) != new_folder(tmp_path, now)
    assert clock(3725.9) == "01:02:05"
    assert format_lines([Segment(0, 1, SYSTEM, "oi")]) == "**[00:00:00] Outro:** oi"


# --- configuração

def test_config_cifra_chave(tmp_path):
    path = tmp_path / "config.json"
    cfg = config.Config(chave_claude="segredo-çã", provedor="ollama")
    config.save(cfg, path)
    assert "segredo" not in path.read_text("utf-8")
    assert config.load(path) == cfg


def test_config_invalida_e_preservada(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{quebrado", "utf-8")
    assert config.load(path) == config.Config()
    assert (tmp_path / "config.invalido.json").exists()


def test_chave_de_outra_maquina_e_descartada(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"chave_claude": "dpapi:AAAA", "idioma": "en"}), "utf-8")
    cfg = config.load(path)
    assert cfg.chave_claude == "" and cfg.idioma == "en"


def test_windows():
    assert winapi.documents_dir().is_dir()
    assert winapi.audio_fingerprint() == winapi.audio_fingerprint()


# --- resumo

@pytest.fixture
def server():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            size = int(self.headers["Content-Length"])
            self.server.seen = (self.path, json.loads(self.rfile.read(size)))
            body = self.server.body
            self.send_response(self.server.code)
            self.send_header("Content-Type", self.server.ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    s = HTTPServer(("127.0.0.1", 0), Handler)
    s.code, s.ctype = 200, "application/json"
    threading.Thread(target=s.serve_forever, daemon=True).start()
    s.url = f"http://127.0.0.1:{s.server_port}"
    yield s
    s.shutdown()
    s.server_close()


def test_ollama(server):
    server.body = json.dumps({"message": {"content": "<think>x</think>## Resumo"}}).encode()
    cfg = config.Config(provedor="ollama", url_ollama=server.url)
    assert summary.summarize("a" * 60000, cfg) == "## Resumo"
    path, body = server.seen
    assert path == "/api/chat" and body["options"]["num_ctx"] > 20000
    assert "Matheus" in body["messages"][0]["content"]


def test_ollama_erro(server):
    server.code, server.body = 404, b'{"error":"model not found"}'
    with pytest.raises(summary.SummaryError, match="404"):
        summary.summarize("x", config.Config(provedor="ollama", url_ollama=server.url))


def sse(stop_reason):
    events = [
        {"type": "message_start", "message": {
            "id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "## Resumo"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
         "usage": {"output_tokens": 2}},
        {"type": "message_stop"},
    ]
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def claude(server, monkeypatch, stop_reason):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", server.url)
    server.ctype, server.body = "text/event-stream", sse(stop_reason)
    return summary.summarize("oi", config.Config(chave_claude="teste"))


def test_claude(server, monkeypatch):
    assert claude(server, monkeypatch, "end_turn") == "## Resumo"
    body = server.seen[1]
    assert body["model"] == "claude-opus-5-5" and body["fallbacks"] == "default"


@pytest.mark.parametrize("reason", ["refusal", "max_tokens"])
def test_claude_resposta_incompleta(server, monkeypatch, reason):
    with pytest.raises(summary.SummaryError):
        claude(server, monkeypatch, reason)


def test_claude_chave_invalida(server, monkeypatch):
    server.code = 401
    with pytest.raises(summary.SummaryError, match="inválida"):
        claude(server, monkeypatch, "end_turn")


def test_provedores_sem_chave():
    for p in ("claude", "openai"):
        with pytest.raises(summary.SummaryError, match="configure"):
            summary.summarize("x", config.Config(provedor=p))
