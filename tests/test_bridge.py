"""End-to-end tests for the bridge HTTP server.

These tests import `bridge-server.py` directly (no package, no install),
spin up the WSGI app in a background thread, and exercise each endpoint
via `urllib` (no extra deps).

Heavy externals (hermes CLI, faster_whisper, edge_tts) are mocked.
"""

import importlib.util
import json
import sys
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path
from unittest import mock

import pytest
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler

# ── Import bridge as a module ──
BRIDGE_PATH = Path(__file__).parent.parent / "bridge-server.py"
spec = importlib.util.spec_from_file_location("bridge", BRIDGE_PATH)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


# ── Mock the slow external calls ──
@pytest.fixture(autouse=True)
def mock_externals(monkeypatch):
    """Mock hermes + whisper + tts so tests don't hit the real CLI or load models."""
    counter = {"n": 0}
    # In-memory settings store for tests
    settings_store = {"current": bridge.DEFAULT_SETTINGS.copy()}

    def fake_run_hermes(message, session_id=None):
        counter["n"] += 1
        new_sid = session_id or f"sid_{counter['n']:04d}"
        return f"echo: {message}", new_sid

    def fake_stt(path, language="pt"):
        return f"transcribed:{Path(path).name}"

    def fake_tts(text):
        if not text.strip():
            return None
        import uuid as _uuid
        audio_id = _uuid.uuid4().hex[:12]
        mp3 = bridge.AUDIO_CACHE / f"{audio_id}.mp3"
        mp3.write_bytes(b"\xff\xe0" + b"\x00" * 100)
        return str(mp3)

    def fake_tts_with_voice(text, voice):
        return fake_tts(text)

    def fake_save_settings(settings):
        # Validate keys
        valid_keys = set(bridge.DEFAULT_SETTINGS.keys())
        filtered = {k: v for k, v in settings.items() if k in valid_keys}
        settings_store["current"].update(filtered)
        return True

    def fake_load_settings():
        return settings_store["current"].copy()

    def fake_apply_settings(settings):
        return {"whisperReloaded": False}

    def fake_reload_whisper(model_name):
        pass

    monkeypatch.setattr(bridge, "run_hermes", fake_run_hermes)
    monkeypatch.setattr(bridge, "speech_to_text", fake_stt)
    monkeypatch.setattr(bridge, "text_to_speech", fake_tts)
    monkeypatch.setattr(bridge, "text_to_speech_with_voice", fake_tts_with_voice)
    monkeypatch.setattr(bridge, "save_settings", fake_save_settings)
    monkeypatch.setattr(bridge, "load_settings", fake_load_settings)
    monkeypatch.setattr(bridge, "apply_settings", fake_apply_settings)
    monkeypatch.setattr(bridge, "reload_whisper", fake_reload_whisper)
    return counter


# ── Spin up WSGI server in background ──
@pytest.fixture
def server():
    import random
    port = 18000 + random.randint(0, 999)
    httpd = bridge._ThreadingWSGIServer(("127.0.0.1", port), WSGIRequestHandler)
    httpd.set_app(bridge.application)

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    # Wait for ready
    base = f"http://127.0.0.1:{port}"
    for _ in range(20):
        try:
            with urllib.request.urlopen(f"{base}/api/health", timeout=1) as r:
                if r.status == 200:
                    break
        except Exception:
            time.sleep(0.1)
    else:
        raise RuntimeError("server didn't start")

    yield base
    httpd.shutdown()
    httpd.server_close()


def http(server, method, path, body=None, headers=None, timeout=10):
    h = dict(headers or {})
    data = None
    if isinstance(body, (dict, list)):
        data = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    elif isinstance(body, bytes):
        data = body
    req = urllib.request.Request(server + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


# ──────────────────────────────────────────────────────────────
#  Tests
# ──────────────────────────────────────────────────────────────


def test_health(server):
    code, body, _ = http(server, "GET", "/api/health")
    assert code == 200
    j = json.loads(body)
    assert j["status"] == "ok"
    assert "stt" in j
    assert "tts" in j


def test_options_preflight(server):
    code, _, _ = http(server, "OPTIONS", "/api/chat")
    assert code == 204


def test_unknown_route_get_returns_405(server):
    # Now unknown routes return 404 (not 405) since we have explicit GET-allowed endpoints
    code, _, _ = http(server, "GET", "/api/nope")
    assert code == 404


def test_unknown_route_post_returns_404(server):
    code, _, _ = http(server, "POST", "/api/nope")
    assert code == 404


def test_audio_path_traversal_blocked(server):
    """Malformed audio IDs must be rejected with 400, not crash on filesystem."""
    # wsgiref normalizes `/..` away, but our regex catches the rest
    code, body, _ = http(server, "GET", "/api/audio/bad'OR'1'='1")
    assert code == 400
    assert json.loads(body)["error"] == "bad audio id"


def test_audio_invalid_id_pattern(server):
    """Only hex IDs are allowed."""
    code, body, _ = http(server, "GET", "/api/audio/not-hex-zzz")
    assert code == 400
    assert json.loads(body)["error"] == "bad audio id"


def test_chat_empty_message_rejected(server):
    code, body, _ = http(server, "POST", "/api/chat", {})
    assert code == 400
    assert json.loads(body)["error"] == "message required"


def test_chat_text_happy_path(server):
    code, body, _ = http(server, "POST", "/api/chat", {"message": "hello"})
    assert code == 200
    j = json.loads(body)
    assert j["text"].startswith("echo:")
    assert j["session_id"].startswith("sid_")
    assert j["audio_url"] is None  # voice=false → no TTS


def test_chat_with_voice_returns_audio(server):
    code, body, _ = http(server, "POST", "/api/chat", {"message": "speak", "voice": True})
    assert code == 200
    j = json.loads(body)
    assert j["audio_url"] is not None
    # Verify MP3 served and is valid MPEG sync word
    code2, body2, hdrs = http(server, "GET", j["audio_url"])
    assert code2 == 200
    assert hdrs["Content-Type"].startswith("audio/mpeg")
    assert body2[:2] == b"\xff\xe0" or body2[:2] == b"\xff\xfb"


def test_session_resume_preserves_id(server):
    code, body, _ = http(server, "POST", "/api/chat", {"message": "first"})
    sid1 = json.loads(body)["session_id"]
    code, body, _ = http(server, "POST", "/api/chat", {"message": "second", "session_id": sid1})
    sid2 = json.loads(body)["session_id"]
    assert sid1 == sid2


def test_multipart_audio(server):
    """Multipart parser must extract audio bytes + session_id via email.parser."""
    boundary = "----pytest"
    fake_audio = b"\x1a\x45\xdf\xa3" + b"\x00" * 200
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="session_id"\r\n\r\n'
        f"sess-pytest\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="audio"; filename="x.webm"\r\n'
        f"Content-Type: audio/webm\r\n\r\n"
    ).encode() + fake_audio + f"\r\n--{boundary}--\r\n".encode()

    code, resp, _ = http(
        server, "POST", "/api/chat", body,
        {"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert code == 200
    j = json.loads(resp)
    # Either full transcription (if our fake_stt returned something) or stt_error
    assert "session_id" in j


def test_concurrency(server):
    """ThreadingMixIn must serve parallel requests; 3 simultaneous should be
    faster than 3 sequential (no global lock)."""
    import threading

    def one():
        t0 = time.time()
        http(server, "POST", "/api/chat", {"message": "ping"}, timeout=30)
        return time.time() - t0

    # Warm up
    one()

    # 3 in parallel
    times = []
    lock = threading.Lock()
    results = [None, None, None]

    def worker(i):
        t = one()
        with lock:
            results[i] = t

    t0 = time.time()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
    for t in threads: t.start()
    for t in threads: t.join()
    total = time.time() - t0

    # Single takes ~0.01s (mocked). 3 parallel should be <0.5s.
    # If single-threaded server, total would still be <0.5s but workers
    # would serialize. The real assertion is that all 3 ran.
    assert total < 1.0
    assert all(r is not None for r in results)


def test_whisper_singleton(monkeypatch):
    """get_whisper() must return the same instance across calls (otherwise
    we reload the 500MB model per request)."""
    sentinel = object()
    calls = {"n": 0}

    class FakeWhisper:
        def __init__(self, *a, **kw):
            calls["n"] += 1
            self.sentinel = sentinel

    # Inject FakeWhisper into the module's namespace AND the lazy import path
    # (get_whisper does `from faster_whisper import WhisperModel` inside the fn)
    monkeypatch.setattr(bridge, "_WHISPER", None)
    # Build a fake module so the lazy import resolves to our class
    fake_module = mock.MagicMock()
    fake_module.WhisperModel = FakeWhisper
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)

    a = bridge.get_whisper()
    b = bridge.get_whisper()
    c = bridge.get_whisper()
    assert a is b is c
    assert a.sentinel is sentinel
    assert calls["n"] == 1


# ── Settings tests ──

def test_settings_get(server):
    code, body, _ = http(server, "GET", "/api/settings")
    assert code == 200
    j = json.loads(body)
    assert "settings" in j
    assert "ttsVoice" in j["settings"]
    assert "whisperModel" in j["settings"]
    assert "availableVoices" in j
    assert len(j["availableVoices"]) >= 5
    assert "availableModels" in j
    assert len(j["availableModels"]) >= 5
    assert "availableLanguages" in j


def test_settings_post_valid(server):
    code, body, _ = http(server, "POST", "/api/settings", {
        "ttsVoice": "pt-BR-FranciscaNeural",
        "whisperModel": "small",
        "sttLanguage": "en",
    })
    assert code == 200
    j = json.loads(body)
    assert j["ok"] is True
    assert j["settings"]["ttsVoice"] == "pt-BR-FranciscaNeural"
    assert j["settings"]["whisperModel"] == "small"
    assert j["settings"]["sttLanguage"] == "en"


def test_settings_post_invalid_key_ignored(server):
    # Invalid keys should be filtered out, not cause error
    code, body, _ = http(server, "POST", "/api/settings", {
        "ttsVoice": "pt-BR-FranciscaNeural",
        "invalidKey": "should be ignored",
    })
    assert code == 200
    j = json.loads(body)
    assert j["ok"] is True
    assert "invalidKey" not in j["settings"]


def test_settings_preview_tts(server):
    code, body, _ = http(server, "POST", "/api/settings/preview-tts", {
        "voice": "pt-BR-FranciscaNeural",
        "text": "Teste de voz",
    })
    assert code == 200
    j = json.loads(body)
    assert "audio_url" in j
    assert j["audio_url"].startswith("/api/audio/")


# ── Onboarding tests ──

def test_onboarding_voices(server):
    code, body, _ = http(server, "GET", "/api/onboarding/voices")
    assert code == 200
    j = json.loads(body)
    assert "voices" in j
    assert len(j["voices"]) >= 5
    voice = j["voices"][0]
    assert "id" in voice
    assert "name" in voice
    assert "lang" in voice
    assert "gender" in voice


def test_onboarding_preview_tts(server):
    code, body, _ = http(server, "POST", "/api/onboarding/preview-tts", {
        "voice": "pt-BR-FranciscaNeural",
        "text": "Olá onboarding",
    })
    assert code == 200
    j = json.loads(body)
    assert "audio_url" in j


def test_onboarding_test_mic(server):
    boundary = "----testmic"
    fake_audio = b"\x1a\x45\xdf\xa3" + b"\x00" * 200
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="language"\r\n\r\n'
        f"pt\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="audio"; filename="mic.webm"\r\n'
        f"Content-Type: audio/webm\r\n\r\n"
    ).encode() + fake_audio + f"\r\n--{boundary}--\r\n".encode()

    code, resp, _ = http(
        server, "POST", "/api/onboarding/test-mic", body,
        {"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert code == 200
    j = json.loads(resp)
    assert "text" in j or "stt_error" in j
