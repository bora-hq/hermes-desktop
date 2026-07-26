"""Bridge server: Hermes Agent <-> HTTP API with STT/TTS.

Endpoints:
    POST /api/chat  {"message": "...", "session_id": "..."}  -> {"text": "...", "session_id": "..."}
    POST /api/chat  multipart audio (webm) + session_id      -> {"user_text": "...", "text": "...", "audio_url": "...", "session_id": "..."}
    GET  /api/health                                         -> {"status": "ok", "stt": true, "tts": true}
    GET  /api/audio/<id>                                     -> audio/mpeg

Threading: serves concurrent requests via ThreadingMixIn so the slow
`hermes chat` subprocess doesn't block parallel STT/TTS or follow-ups.
"""

import json
import re
import subprocess
import sys
import tempfile
import uuid
import io
from pathlib import Path
from email import policy
from email.parser import BytesParser
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server

AUDIO_CACHE = Path(tempfile.gettempdir()) / "hermes-audio"
AUDIO_CACHE.mkdir(exist_ok=True)

_AUDIO_ID_RE = re.compile(r"^[a-f0-9]+$")

# ── Whisper singleton (avoids reloading the ~500MB model per request) ──
_WHISPER = None


def get_whisper():
    """Lazy-load WhisperModel once and reuse across requests/threads."""
    global _WHISPER
    if _WHISPER is None:
        from faster_whisper import WhisperModel
        _WHISPER = WhisperModel("base", device="cpu", compute_type="int8")
    return _WHISPER


def run_hermes(message: str, session_id: str | None = None) -> tuple[str, str]:
    """Run Hermes CLI and parse the response.

    Returns: (response_text, session_id)

    Depends on `hermes chat --quiet` printing the session_id on a line
    of the form `session_id: <id>` on stderr OR stdout. If Hermes
    changes that format, sessions will silently reset to "" — re-grep
    the CLI to update.
    """
    cmd = ["hermes", "chat", "-q", message, "--quiet"]
    if session_id:
        cmd.extend(["--resume", session_id])

    # Use the main Hermes home, not the profile's home
    hermes_home = Path("/home/strondinha/.hermes")
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=120, cwd=str(hermes_home)
    )

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    new_session_id = session_id or ""
    response_lines = []

    for line in (stderr + "\n" + stdout).split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("↻ Resumed") or line.startswith("↻ Resuming"):
            continue
        if m := re.match(r"^session_id:\s+(\S+)", line):
            new_session_id = m.group(1)
            continue
        response_lines.append(line)

    return "\n".join(response_lines).strip(), new_session_id


# ── STT / TTS ──


def speech_to_text(audio_path: str) -> str | None:
    """Transcribe audio file. Returns text, or None on error."""
    try:
        model = get_whisper()
        segments, _ = model.transcribe(audio_path, language="pt")
        return " ".join(s.text for s in segments).strip() or None
    except ImportError:
        print("faster_whisper not installed", file=sys.stderr)
        return None
    except Exception as e:
        print(f"STT error: {e}", file=sys.stderr)
        return None


def text_to_speech(text: str) -> str | None:
    """Synthesize text to MP3. Returns file path, or None on error/empty."""
    if not text.strip():
        return None
    audio_id = uuid.uuid4().hex[:12]
    mp3_path = AUDIO_CACHE / f"{audio_id}.mp3"
    try:
        import edge_tts
        import asyncio

        async def _gen():
            await edge_tts.Communicate(text, "pt-BR-AntonioNeural").save(str(mp3_path))

        asyncio.run(_gen())
        if mp3_path.exists() and mp3_path.stat().st_size > 0:
            return str(mp3_path)
    except ImportError:
        print("edge_tts not installed", file=sys.stderr)
    except Exception as e:
        print(f"TTS error: {e}", file=sys.stderr)
    return None


# ── WSGI ──


def _json_response(start_response, status, hdrs, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    start_response(status, hdrs)
    return [body]


def _parse_multipart(body: bytes, content_type: str) -> tuple[str | None, bytes | None]:
    """Parse multipart/form-data using stdlib email.parser.

    Returns: (session_id, audio_bytes)
    """
    if "boundary=" not in content_type:
        return None, None
    ct_with_boundary = (
        f"Content-Type: multipart/form-data; boundary={content_type.split('boundary=')[-1].strip()}"
    )
    # Wrap in a fake HTTP message so BytesParser can parse it
    raw = (f"Content-Type: {content_type}\r\n\r\n".encode() + body).decode("latin-1", errors="replace").encode("latin-1")
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    session_id = None
    audio_data = None
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name == "audio":
            audio_data = part.get_payload(decode=True)
        elif name == "session_id":
            session_id = part.get_content()
    return session_id, audio_data


def application(environ, start_response):
    path = environ.get("PATH_INFO", "")
    method = environ.get("REQUEST_METHOD", "")
    hdrs = [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Access-Control-Allow-Origin", "*"),
        ("Access-Control-Allow-Methods", "POST, GET, OPTIONS"),
        ("Access-Control-Allow-Headers", "Content-Type"),
    ]

    if method == "OPTIONS":
        start_response("204 No Content", hdrs)
        return [b""]

    if path == "/api/health":
        stt = False
        tts = False
        try:
            import faster_whisper  # noqa: F401
            stt = True
        except ImportError:
            pass
        try:
            import edge_tts  # noqa: F401
            tts = True
        except ImportError:
            pass
        return _json_response(start_response, "200 OK", hdrs, {"status": "ok", "stt": stt, "tts": tts})

    if path.startswith("/api/audio/"):
        audio_id = path.split("/")[-1]
        if not _AUDIO_ID_RE.fullmatch(audio_id):
            return _json_response(start_response, "400 Bad Request", hdrs, {"error": "bad audio id"})
        mp3 = AUDIO_CACHE / f"{audio_id}.mp3"
        if mp3.exists():
            start_response("200 OK", [
                ("Content-Type", "audio/mpeg"),
                ("Access-Control-Allow-Origin", "*"),
            ])
            return [mp3.read_bytes()]
        return _json_response(start_response, "404 Not Found", hdrs, {"error": "not found"})

    if method != "POST":
        return _json_response(start_response, "405 Method Not Allowed", hdrs, {"error": "POST only"})

    # ── /api/chat ──
    if path == "/api/chat":
        ct = environ.get("CONTENT_TYPE", "")
        is_multipart = "multipart/form-data" in ct

        if is_multipart:
            body = environ["wsgi.input"].read(int(environ.get("CONTENT_LENGTH", 0)))
            session_id, audio_data = _parse_multipart(body, ct)

            if not audio_data:
                return _json_response(start_response, "400 Bad Request", hdrs, {"error": "audio required"})

            tmp = AUDIO_CACHE / f"in_{uuid.uuid4().hex[:8]}.webm"
            tmp.write_bytes(audio_data)
            try:
                user_text = speech_to_text(str(tmp))
                if not user_text:
                    return _json_response(start_response, "200 OK", hdrs, {
                        "text": "", "stt_error": True, "session_id": session_id or ""
                    })
                text, sid = run_hermes(user_text, session_id)
                audio_path = text_to_speech(text)
                audio_url = f"/api/audio/{Path(audio_path).stem}" if audio_path else None
                return _json_response(start_response, "200 OK", hdrs, {
                    "user_text": user_text,
                    "text": text,
                    "audio_url": audio_url,
                    "session_id": sid,
                })
            finally:
                tmp.unlink(missing_ok=True)
        else:
            body_size = int(environ.get("CONTENT_LENGTH", 0))
            body = json.loads(environ["wsgi.input"].read(body_size)) if body_size else {}
            message = body.get("message", "").strip()
            session_id = body.get("session_id") or None
            if not message:
                return _json_response(start_response, "400 Bad Request", hdrs, {"error": "message required"})
            text, sid = run_hermes(message, session_id)
            audio_path = text_to_speech(text) if body.get("voice") else None
            audio_url = f"/api/audio/{Path(audio_path).stem}" if audio_path else None
            return _json_response(start_response, "200 OK", hdrs, {
                "text": text, "session_id": sid, "audio_url": audio_url
            })

    return _json_response(start_response, "404 Not Found", hdrs, {"error": "not found"})


class _ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    """Each request handled in its own thread — slow `hermes chat` no
    longer blocks STT or follow-ups."""
    daemon_threads = True


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8420
    host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
    print(f"Hermes bridge -> http://{host}:{port}")
    make_server(host, port, application, server_class=_ThreadingWSGIServer).serve_forever()
