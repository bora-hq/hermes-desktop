"""Bridge server: Hermes Agent <-> HTTP API with STT/TTS + session persistence.

Endpoints:
    POST /api/chat  {"message": "...", "session_id": "..."}  -> {"text": "...", "session_id": "..."}
    POST /api/chat  multipart audio (webm) + session_id      -> {"user_text": "...", "text": "...", "audio_url": "...", "session_id": "..."}
    GET  /api/health                                         -> {"status": "ok", "stt": true, "tts": true}
    GET  /api/audio/<id>                                     -> audio/mpeg
    GET  /api/sessions                                       -> [{"id", "title", "preview", "updated_at", "message_count"}, ...]
    GET  /api/sessions/<id>                                  -> {"id", "title", "messages": [...]}
    DELETE /api/sessions/<id>                                -> {"deleted": true}

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
import base64
import zipfile
from pathlib import Path
from datetime import datetime, UTC
from email import policy
from email.parser import BytesParser
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server
from dataclasses import asdict

AUDIO_CACHE = Path(tempfile.gettempdir()) / "hermes-audio"
AUDIO_CACHE.mkdir(exist_ok=True)

SETTINGS_CACHE = Path(tempfile.gettempdir()) / "hermes-settings"
SETTINGS_CACHE.mkdir(exist_ok=True)
SETTINGS_FILE = SETTINGS_CACHE / "settings.json"

SESSIONS_CACHE = Path(tempfile.gettempdir()) / "hermes-sessions"
SESSIONS_CACHE.mkdir(exist_ok=True)
SESSIONS_DB = SESSIONS_CACHE / "sessions.db"

COLLAB_CACHE = Path(tempfile.gettempdir()) / "hermes-collab"
COLLAB_CACHE.mkdir(exist_ok=True)
COLLAB_DIR = COLLAB_CACHE

_AUDIO_ID_RE = re.compile(r"^[a-f0-9]+$")

# Default settings
DEFAULT_SETTINGS = {
    "ttsVoice": "pt-BR-AntonioNeural",
    "whisperModel": "base",
    "sttLanguage": "pt",
    "autoStartBridge": True,
    "saveAudio": True,
    "forceDarkMode": False,
}

# ── Whisper singleton (avoids reloading the ~500MB model per request) ──
_WHISPER = None
_CURRENT_WHISPER_MODEL = "base"


def get_whisper():
    """Lazy-load WhisperModel once and reuse across requests/threads."""
    global _WHISPER, _CURRENT_WHISPER_MODEL
    if _WHISPER is None:
        from faster_whisper import WhisperModel
        _WHISPER = WhisperModel(_CURRENT_WHISPER_MODEL, device="cpu", compute_type="int8")
    return _WHISPER


def reload_whisper(model_name: str):
    """Reload Whisper with a different model."""
    global _WHISPER, _CURRENT_WHISPER_MODEL
    from faster_whisper import WhisperModel
    _WHISPER = WhisperModel(model_name, device="cpu", compute_type="int8")
    _CURRENT_WHISPER_MODEL = model_name


# ── Settings persistence ───


def load_settings() -> dict:
    """Load settings from JSON file, merge with defaults."""
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r") as f:
                saved = json.load(f)
            # Merge with defaults (handles new keys added in updates)
            return {**DEFAULT_SETTINGS, **saved}
        except Exception as e:
            print(f"Failed to load settings: {e}", file=sys.stderr)
    return DEFAULT_SETTINGS.copy()


def save_settings(settings: dict) -> bool:
    """Save settings to JSON file."""
    try:
        # Validate against known keys
        validated = {k: settings.get(k, DEFAULT_SETTINGS[k]) for k in DEFAULT_SETTINGS}
        with open(SETTINGS_FILE, "w") as f:
            json.dump(validated, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Failed to save settings: {e}", file=sys.stderr)
        return False


def apply_settings(settings: dict) -> dict:
    """Apply settings that require runtime changes (Whisper model, etc.)."""
    result = {"whisperReloaded": False}

    # Handle Whisper model change
    if "whisperModel" in settings and settings["whisperModel"] != _CURRENT_WHISPER_MODEL:
        try:
            reload_whisper(settings["whisperModel"])
            result["whisperReloaded"] = True
        except Exception as e:
            print(f"Failed to reload Whisper model: {e}", file=sys.stderr)

    # Note: TTS voice is applied per-request via preview/chat endpoints
    # forceDarkMode is frontend-only
    # autoStartBridge / saveAudio are handled by frontend/Tauri

    return result


# ── Session persistence ───


def init_sessions_db():
    """Initialize SQLite database for session persistence."""
    import sqlite3
    conn = sqlite3.connect(SESSIONS_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            preview TEXT NOT NULL DEFAULT '',
            message_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            deleted_at TEXT,
            meta TEXT DEFAULT '{}'
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_deleted ON sessions(deleted_at) WHERE deleted_at IS NULL")
    conn.commit()
    conn.close()


def get_session(session_id: str):
    """Get a single session by ID."""
    import sqlite3
    conn = sqlite3.connect(SESSIONS_DB)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        "SELECT * FROM sessions WHERE id = ? AND deleted_at IS NULL",
        (session_id,)
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def list_sessions(limit: int = 50, offset: int = 0, search: str = "", include_deleted: bool = False, sort: str = "-updated_at"):
    """List sessions with pagination, search, and sort."""
    import sqlite3
    conn = sqlite3.connect(SESSIONS_DB)
    conn.row_factory = sqlite3.Row
    
    valid_sorts = {"updated_at", "-updated_at", "created_at", "-created_at"}
    sort_col = sort[1:] if sort.startswith("-") else sort
    sort_dir = "DESC" if sort.startswith("-") else "ASC"
    if sort_col not in valid_sorts:
        sort_col = "updated_at"
        sort_dir = "DESC"
    
    where = "WHERE deleted_at IS NULL" if not include_deleted else ""
    params = []
    
    if search:
        where += (" AND " if where else "WHERE ") + "(title LIKE ? OR preview LIKE ? OR id LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
    
    # Count total
    count_query = f"SELECT COUNT(*) FROM sessions {where}"
    cursor = conn.execute(count_query, params)
    total = cursor.fetchone()[0]
    
    # Fetch page
    query = f"SELECT * FROM sessions {where} ORDER BY {sort_col} {sort_dir} LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    cursor = conn.execute(query, params)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows, total


def create_session(title: str = ""):
    """Create a new session."""
    import sqlite3
    session_id = f"sid_{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC).isoformat().replace('+00:00', 'Z')
    
    conn = sqlite3.connect(SESSIONS_DB)
    conn.execute(
        "INSERT INTO sessions (id, title, preview, message_count, created_at, updated_at) VALUES (?, ?, ?, 0, ?, ?)",
        (session_id, title or "Nova conversa", "", now, now)
    )
    conn.commit()
    conn.close()
    return session_id


def update_session(session_id: str, title: str = None, preview: str = None, message_count: int = None, meta: dict = None):
    """Update session fields."""
    import sqlite3
    conn = sqlite3.connect(SESSIONS_DB)
    
    updates = ["updated_at = ?"]
    params = [datetime.now(UTC).isoformat().replace('+00:00', 'Z')]
    
    if title is not None:
        updates.append("title = ?")
        params.append(title)
    if preview is not None:
        updates.append("preview = ?")
        params.append(preview[:200])
    if message_count is not None:
        updates.append("message_count = ?")
        params.append(str(message_count))
    if meta is not None:
        updates.append("meta = ?")
        params.append(json.dumps(meta))
    
    params.append(session_id)
    conn.execute(f"UPDATE sessions SET {', '.join(updates)} WHERE id = ? AND deleted_at IS NULL", params)
    conn.commit()
    conn.close()


def delete_session(session_id: str, hard: bool = False):
    """Soft delete (default) or hard delete a session."""
    import sqlite3
    conn = sqlite3.connect(SESSIONS_DB)
    if hard:
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    else:
        conn.execute(
            "UPDATE sessions SET deleted_at = ? WHERE id = ?",
            (datetime.now(UTC).isoformat().replace('+00:00', 'Z'), session_id)
        )
    conn.commit()
    conn.close()


def restore_session(session_id: str):
    """Restore a soft-deleted session."""
    import sqlite3
    conn = sqlite3.connect(SESSIONS_DB)
    conn.execute("UPDATE sessions SET deleted_at = NULL WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()


# Call init on module load
init_sessions_db()


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


# ── STT / TTS ───


def speech_to_text(audio_path: str, language: str = "pt") -> str | None:
    """Transcribe audio file. Returns text, or None on error."""
    try:
        model = get_whisper()
        segments, _ = model.transcribe(audio_path, language=language)
        return " ".join(s.text for s in segments).strip() or None
    except ImportError:
        print("faster_whisper not installed", file=sys.stderr)
        return None
    except Exception as e:
        print(f"STT error: {e}", file=sys.stderr)
        return None


def text_to_speech(text: str) -> str | None:
    """Synthesize text to MP3. Returns file path, or None on error/empty."""
    return text_to_speech_with_voice(text, "pt-BR-AntonioNeural")


def text_to_speech_with_voice(text: str, voice: str) -> str | None:
    """Synthesize text to MP3 with specific voice. Returns file path, or None on error/empty."""
    if not text.strip():
        return None
    audio_id = uuid.uuid4().hex[:12]
    mp3_path = AUDIO_CACHE / f"{audio_id}.mp3"
    try:
        import edge_tts
        import asyncio

        async def _gen():
            await edge_tts.Communicate(text, voice).save(str(mp3_path))

        asyncio.run(_gen())
        if mp3_path.exists() and mp3_path.stat().st_size > 0:
            return str(mp3_path)
    except ImportError:
        print("edge_tts not installed", file=sys.stderr)
    except Exception as e:
        print(f"TTS error: {e}", file=sys.stderr)
    return None


# ── WSGI ───


def _json_response(start_response, status, hdrs, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    # Add security headers
    security_headers = [
        ("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self' http://127.0.0.1:8420 ws://127.0.0.1:8420; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"),
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("X-XSS-Protection", "1; mode=block"),
        ("Referrer-Policy", "strict-origin-when-cross-origin"),
        ("Permissions-Policy", "microphone=(), camera=(), geolocation=()")
    ]
    all_headers = hdrs + security_headers
    start_response(status, all_headers)
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

    # ── Settings endpoints (GET allowed) ──
    if path == "/api/settings":
        if method == "GET":
            settings = load_settings()
            return _json_response(start_response, "200 OK", hdrs, {
                "settings": settings,
                "availableVoices": [
                    {"id": "pt-BR-AntonioNeural", "name": "Antônio", "lang": "pt-BR", "gender": "Male"},
                    {"id": "pt-BR-FranciscaNeural", "name": "Francisca", "lang": "pt-BR", "gender": "Female"},
                    {"id": "pt-BR-ThalitaMultilingualNeural", "name": "Thalita (Multilíngue)", "lang": "pt-BR", "gender": "Female"},
                    {"id": "en-US-GuyNeural", "name": "Guy", "lang": "en-US", "gender": "Male"},
                    {"id": "en-US-JennyNeural", "name": "Jenny", "lang": "en-US", "gender": "Female"},
                    {"id": "es-ES-AlvaroNeural", "name": "Álvaro", "lang": "es-ES", "gender": "Male"},
                    {"id": "fr-FR-HenriNeural", "name": "Henri", "lang": "fr-FR", "gender": "Male"},
                ],
                "availableModels": [
                    {"id": "tiny", "label": "tiny (~39 MB) — mais rápido, menos preciso"},
                    {"id": "base", "label": "base (~74 MB) — equilibrado"},
                    {"id": "small", "label": "small (~244 MB) — mais preciso"},
                    {"id": "medium", "label": "medium (~769 MB) — muito preciso"},
                    {"id": "large-v3", "label": "large-v3 (~1.5 GB) — máximo precisão"},
                ],
                "availableLanguages": [
                    {"id": "pt", "label": "Português"},
                    {"id": "en", "label": "English"},
                    {"id": "es", "label": "Español"},
                    {"id": "fr", "label": "Français"},
                    {"id": "auto", "label": "Detecção automática"},
                ],
            })

        if method == "POST":
            body_size = int(environ.get("CONTENT_LENGTH", 0))
            body = json.loads(environ["wsgi.input"].read(body_size)) if body_size else {}

            # Validate keys
            valid_keys = set(DEFAULT_SETTINGS.keys())
            filtered = {k: v for k, v in body.items() if k in valid_keys}

            if save_settings(filtered):
                apply_result = apply_settings(filtered)
                current = load_settings()
                return _json_response(start_response, "200 OK", hdrs, {
                    "ok": True,
                    "settings": current,
                    **apply_result
                })
            return _json_response(start_response, "500 Internal Server Error", hdrs, {"error": "failed to save"})

    if path == "/api/settings/preview-tts" and method == "POST":
        body_size = int(environ.get("CONTENT_LENGTH", 0))
        body = json.loads(environ["wsgi.input"].read(body_size)) if body_size else {}
        voice = body.get("voice", "pt-BR-AntonioNeural")
        text = body.get("text", "Olá, sou o Hermes.")

        audio_path = text_to_speech_with_voice(text, voice)
        if audio_path:
            audio_url = f"/api/audio/{Path(audio_path).stem}"
            return _json_response(start_response, "200 OK", hdrs, {"audio_url": audio_url})
        return _json_response(start_response, "500 Internal Server Error", hdrs, {"error": "tts failed"})

    # ── Onboarding endpoints (GET allowed) ──
    if path == "/api/onboarding/voices" and method == "GET":
        return _json_response(start_response, "200 OK", hdrs, {
            "voices": [
                {"id": "pt-BR-AntonioNeural", "name": "Antônio", "lang": "pt-BR", "gender": "Male"},
                {"id": "pt-BR-FranciscaNeural", "name": "Francisca", "lang": "pt-BR", "gender": "Female"},
                {"id": "pt-BR-ThalitaMultilingualNeural", "name": "Thalita (Multilíngue)", "lang": "pt-BR", "gender": "Female"},
                {"id": "en-US-GuyNeural", "name": "Guy", "lang": "en-US", "gender": "Male"},
                {"id": "en-US-JennyNeural", "name": "Jenny", "lang": "en-US", "gender": "Female"},
            ]
        })

    if path == "/api/onboarding/preview-tts" and method == "POST":
        body_size = int(environ.get("CONTENT_LENGTH", 0))
        body = json.loads(environ["wsgi.input"].read(body_size)) if body_size else {}
        voice = body.get("voice", "pt-BR-AntonioNeural")
        text = body.get("text", "Olá, sou o Hermes.")

        audio_path = text_to_speech_with_voice(text, voice)
        if audio_path:
            audio_url = f"/api/audio/{Path(audio_path).stem}"
            return _json_response(start_response, "200 OK", hdrs, {"audio_url": audio_url})
        return _json_response(start_response, "500 Internal Server Error", hdrs, {"error": "tts failed"})

    if path == "/api/onboarding/test-mic" and method == "POST":
        ct = environ.get("CONTENT_TYPE", "")
        is_multipart = "multipart/form-data" in ct

        if not is_multipart:
            return _json_response(start_response, "400 Bad Request", hdrs, {"error": "multipart required"})

        body = environ["wsgi.input"].read(int(environ.get("CONTENT_LENGTH", 0)))
        session_id, audio_data = _parse_multipart(body, ct)

        if not audio_data:
            return _json_response(start_response, "400 Bad Request", hdrs, {"error": "audio required"})

        tmp = AUDIO_CACHE / f"mic_{uuid.uuid4().hex[:8]}.webm"
        tmp.write_bytes(audio_data)
        try:
            # Get language from form data
            language = "pt"
            if "boundary=" in ct:
                # Re-parse for language field
                for part in BytesParser(policy=policy.default).parsebytes(
                    (f"Content-Type: {ct}\r\n\r\n".encode() + body).decode("latin-1", errors="replace").encode("latin-1")
                ).iter_parts():
                    if part.get_param("name", header="content-disposition") == "language":
                        language = part.get_content()

            user_text = speech_to_text(str(tmp), language=language)
            if not user_text:
                return _json_response(start_response, "200 OK", hdrs, {
                    "text": "", "stt_error": True, "error": "no_speech_detected"
                })
            return _json_response(start_response, "200 OK", hdrs, {"text": user_text})
        finally:
            tmp.unlink(missing_ok=True)

    # ── Design System endpoints ──
    if path == "/api/design-system/scan" and method == "POST":
        body_size = int(environ.get("CONTENT_LENGTH", 0))
        body = json.loads(environ["wsgi.input"].read(body_size)) if body_size else {}
        workspace = body.get("workspace", ".")
        output = body.get("output", "design-system.json")
        
        try:
            # Import and run design system engine
            sys.path.insert(0, str(Path(__file__).parent))
            from design_system.engine import DesignSystemEngine
            
            engine = DesignSystemEngine(workspace)
            ds = engine.scan_workspace()
            engine.save(output)
            
            return _json_response(start_response, "200 OK", hdrs, {
                "ok": True,
                "tokens": len(ds.tokens),
                "components": len(ds.components),
                "output": output,
                "metadata": ds.metadata
            })
        except Exception as e:
            return _json_response(start_response, "500 Internal Server Error", hdrs, {"error": str(e)})

    if path == "/api/design-system/tokens" and method == "GET":
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from design_system.engine import DesignSystemEngine
            
            engine = DesignSystemEngine(".")
            if Path("design-system.json").exists():
                engine.load("design-system.json")
            else:
                engine.scan_workspace()
            
            # Filter by type if requested
            from urllib.parse import parse_qs
            query = parse_qs(environ.get("QUERY_STRING", ""))
            token_type = query.get("type", [""])[0]
            
            if token_type:
                tokens = engine.get_tokens_by_type(token_type)
            else:
                tokens = engine.design_system.tokens
            
            return _json_response(start_response, "200 OK", hdrs, {
                "tokens": {k: asdict(v) for k, v in tokens.items()},
                "count": len(tokens)
            })
        except Exception as e:
            return _json_response(start_response, "500 Internal Server Error", hdrs, {"error": str(e)})

    if path == "/api/design-system/theme.css" and method == "GET":
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from design_system.engine import DesignSystemEngine
            
            engine = DesignSystemEngine(".")
            if Path("design-system.json").exists():
                engine.load("design-system.json")
            else:
                engine.scan_workspace()
            
            css = engine.generate_css_variables("hds")
            start_response("200 OK", [
                ("Content-Type", "text/css; charset=utf-8"),
                ("Cache-Control", "public, max-age=3600"),
            ])
            return [css.encode("utf-8")]
        except Exception as e:
            return _json_response(start_response, "500 Internal Server Error", hdrs, {"error": str(e)})

    if path == "/api/design-system/tailwind.config.js" and method == "GET":
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from design_system.engine import DesignSystemEngine
            
            engine = DesignSystemEngine(".")
            if Path("design-system.json").exists():
                engine.load("design-system.json")
            else:
                engine.scan_workspace()
            
            config = engine.generate_tailwind_config()
            
            js_content = f"/** Generated by Hermes Design System */\nmodule.exports = {json.dumps(config, indent=2)};"
            
            start_response("200 OK", [
                ("Content-Type", "application/javascript; charset=utf-8"),
                ("Cache-Control", "public, max-age=3600"),
            ])
            return [js_content.encode("utf-8")]
        except Exception as e:
            return _json_response(start_response, "500 Internal Server Error", hdrs, {"error": str(e)})

    # ── /api/audio/<id> (GET allowed) ──
    if path.startswith("/api/audio/"):
        audio_id = path.split("/")[-1]
        if not _AUDIO_ID_RE.fullmatch(audio_id):
            return _json_response(start_response, "400 Bad Request", hdrs, {"error": "bad audio id"})
        mp3 = AUDIO_CACHE / f"{audio_id}.mp3"
        if mp3.exists():
            # Add security headers for audio endpoint
            audio_headers = [
                ("Content-Type", "audio/mpeg"),
                ("Access-Control-Allow-Origin", "http://127.0.0.1:8080"),
                ("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"),
                ("X-Content-Type-Options", "nosniff"),
            ]
            start_response("200 OK", audio_headers)
            return [mp3.read_bytes()]
        return _json_response(start_response, "404 Not Found", hdrs, {"error": "not found"})

    # ── Sessions endpoints (GET allowed) ──
    if path == "/api/sessions":
        if method == "GET":
            # Parse query params
            from urllib.parse import parse_qs
            query = parse_qs(environ.get("QUERY_STRING", ""))
            limit = min(int(query.get("limit", ["50"])[0]), 200)
            offset = int(query.get("offset", ["0"])[0])
            search = query.get("search", [""])[0]
            include_deleted = query.get("include_deleted", ["false"])[0].lower() == "true"
            sort = query.get("sort", ["-updated_at"])[0]
            
            result = list_sessions(limit, offset, search, include_deleted, sort)
            return _json_response(start_response, "200 OK", hdrs, {
                "sessions": result[0],
                "total": result[1],
                "limit": limit,
                "offset": offset
            })
        
        if method == "POST":
            body_size = int(environ.get("CONTENT_LENGTH", 0))
            body = json.loads(environ["wsgi.input"].read(body_size)) if body_size else {}
            title = body.get("title", "Nova conversa")
            session_id = create_session(title)
            return _json_response(start_response, "201 Created", hdrs, {
                "id": session_id,
                "title": title,
                "preview": "",
                "messageCount": 0,
                "createdAt": datetime.now(UTC).isoformat().replace('+00:00', 'Z'),
                "updatedAt": datetime.now(UTC).isoformat().replace('+00:00', 'Z')
            })

    if path.startswith("/api/sessions/") and path != "/api/sessions":
        # Extract session ID from path
        session_id = path.split("/")[-1]
        
        if method == "GET":
            session = get_session(session_id)
            if not session:
                return _json_response(start_response, "404 Not Found", hdrs, {"error": "not found"})
            return _json_response(start_response, "200 OK", hdrs, session)
        
        if method == "PATCH":
            body_size = int(environ.get("CONTENT_LENGTH", 0))
            body = json.loads(environ["wsgi.input"].read(body_size)) if body_size else {}
            title = body.get("title")
            preview = body.get("preview")
            message_count = body.get("messageCount")
            meta = body.get("meta")
            
            update_session(session_id, title=title, preview=preview, message_count=message_count, meta=meta)
            session = get_session(session_id)
            if not session:
                return _json_response(start_response, "404 Not Found", hdrs, {"error": "not found"})
            return _json_response(start_response, "200 OK", hdrs, session)
        
        if method == "DELETE":
            hard = environ.get("QUERY_STRING", "").find("hard=true") >= 0
            delete_session(session_id, hard=hard)
            return _json_response(start_response, "200 OK", hdrs, {"ok": True, "deletedAt": datetime.now(UTC).isoformat().replace('+00:00', 'Z')})

    if path.startswith("/api/sessions/") and path.endswith("/restore") and method == "POST":
        session_id = path.split("/")[-2]
        restore_session(session_id)
        session = get_session(session_id)
        return _json_response(start_response, "200 OK", hdrs, session)

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

    # ── Collaboration endpoints ──
    if path == "/api/collab/rooms" and method == "POST":
        body_size = int(environ.get("CONTENT_LENGTH", 0))
        body = json.loads(environ["wsgi.input"].read(body_size)) if body_size else {}
        room_name = body.get("room", f"room-{uuid.uuid4().hex[:8]}")
        user_id = body.get("userId", f"user-{uuid.uuid4().hex[:6]}")
        user_name = body.get("userName", "Anonymous")
        
        room_path = COLLAB_DIR / room_name
        room_path.mkdir(parents=True, exist_ok=True)
        
        meta = {
            "name": room_name,
            "createdAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "owner": user_id,
            "users": [{"id": user_id, "name": user_name, "color": f"hsl({hash(user_id) % 360}, 70%, 50%)"}]
        }
        (room_path / "meta.json").write_text(json.dumps(meta))
        
        return _json_response(start_response, "201 Created", hdrs, {"room": room_name, "meta": meta})

    if path.startswith("/api/collab/rooms/") and method == "GET" and not path.endswith("/users"):
        room_name = path.split("/")[-1]
        room_path = COLLAB_DIR / room_name
        meta_path = room_path / "meta.json"
        
        if not meta_path.exists():
            return _json_response(start_response, "404 Not Found", hdrs, {"error": "room not found"})
        
        meta = json.loads(meta_path.read_text())
        return _json_response(start_response, "200 OK", hdrs, {"room": room_name, "meta": meta})

    if path.endswith("/users") and method == "POST" and "/api/collab/rooms/" in path:
        room_name = path.split("/")[-2]
        room_path = COLLAB_DIR / room_name
        
        body_size = int(environ.get("CONTENT_LENGTH", 0))
        body = json.loads(environ["wsgi.input"].read(body_size)) if body_size else {}
        user_id = body.get("userId", f"user-{uuid.uuid4().hex[:6]}")
        user_name = body.get("userName", "Anonymous")
        color = body.get("color", f"hsl({hash(user_id) % 360}, 70%, 50%)")
        
        meta_path = room_path / "meta.json"
        if not meta_path.exists():
            return _json_response(start_response, "404 Not Found", hdrs, {"error": "room not found"})
        
        meta = json.loads(meta_path.read_text())
        if not any(u["id"] == user_id for u in meta.get("users", [])):
            meta.setdefault("users", []).append({"id": user_id, "name": user_name, "color": color})
            meta_path.write_text(json.dumps(meta))
        
        return _json_response(start_response, "200 OK", hdrs, {"users": meta["users"]})

    if path == "/api/collab/document" and method == "POST":
        body_size = int(environ.get("CONTENT_LENGTH", 0))
        body = json.loads(environ["wsgi.input"].read(body_size)) if body_size else {}
        room_name = body.get("room", "default")
        content = body.get("content", "")
        comments = body.get("comments", {})
        
        room_path = COLLAB_DIR / room_name
        room_path.mkdir(parents=True, exist_ok=True)
        
        doc_path = room_path / "document.json"
        doc_data = {
            "content": content,
            "comments": comments,
            "updatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "version": (json.loads(doc_path.read_text()).get("version", 0) + 1) if doc_path.exists() else 1
        }
        doc_path.write_text(json.dumps(doc_data))
        
        return _json_response(start_response, "200 OK", hdrs, {"ok": True, "version": doc_data["version"]})

    if path.startswith("/api/collab/document/") and method == "GET":
        room_name = path.split("/")[-1]
        doc_path = COLLAB_DIR / room_name / "document.json"
        
        if not doc_path.exists():
            return _json_response(start_response, "404 Not Found", hdrs, {"error": "document not found"})
        
        doc = json.loads(doc_path.read_text())
        return _json_response(start_response, "200 OK", hdrs, doc)

    if path == "/api/collab/snapshots" and method == "POST":
        body_size = int(environ.get("CONTENT_LENGTH", 0))
        body = json.loads(environ["wsgi.input"].read(body_size)) if body_size else {}
        room_name = body.get("room", "default")
        label = body.get("label", "")
        snapshot = body.get("snapshot")
        
        room_path = COLLAB_DIR / room_name
        snapshots_dir = room_path / "snapshots"
        snapshots_dir.mkdir(parents=True, exist_ok=True)
        
        snap_id = f"snap-{uuid.uuid4().hex[:12]}"
        snap_data = {
            "id": snap_id,
            "label": label,
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "snapshot": snapshot,
            "author": body.get("author", {})
        }
        (snapshots_dir / f"{snap_id}.json").write_text(json.dumps(snap_data))
        
        return _json_response(start_response, "201 Created", hdrs, {"snapshot": snap_data})

    if path.startswith("/api/collab/snapshots/") and method == "GET":
        room_name = path.split("/")[-2]
        snapshots_dir = COLLAB_DIR / room_name / "snapshots"
        
        if not snapshots_dir.exists():
            return _json_response(start_response, "200 OK", hdrs, {"snapshots": []})
        
        snapshots = []
        for f in sorted(snapshots_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:50]:
            snapshots.append(json.loads(f.read_text()))
        
        return _json_response(start_response, "200 OK", hdrs, {"snapshots": snapshots})

    if path == "/api/design/parse-intent" and method == "POST":
        body_size = int(environ.get("CONTENT_LENGTH", 0))
        body = json.loads(environ["wsgi.input"].read(body_size)) if body_size else {}
        transcript = body.get("transcript", "").lower()
        
        intent = {"action": None, "params": {}}
        
        if any(w in transcript for w in ["escuro", "dark", "modo escuro"]):
            intent = {"action": "setTheme", "params": {"theme": "dark"}}
        elif any(w in transcript for w in ["claro", "light", "modo claro"]):
            intent = {"action": "setTheme", "params": {"theme": "light"}}
        elif any(w in transcript for w in ["botão", "button", "adicion"]):
            if "botão" in transcript or "button" in transcript:
                intent = {"action": "insertComponent", "params": {"type": "button"}}
            elif "card" in transcript or "cartão" in transcript:
                intent = {"action": "insertComponent", "params": {"type": "card"}}
            elif "input" in transcript or "campo" in transcript or "caixa de texto" in transcript:
                intent = {"action": "insertComponent", "params": {"type": "input"}}
            elif "título" in transcript or "heading" in transcript:
                intent = {"action": "insertComponent", "params": {"type": "heading"}}
            elif "texto" in transcript or "parágrafo" in transcript or "paragraph" in transcript:
                intent = {"action": "insertComponent", "params": {"type": "text"}}
            elif "imagem" in transcript or "image" in transcript:
                intent = {"action": "insertComponent", "params": {"type": "image"}}
            elif "container" in transcript or "caixa" in transcript or "box" in transcript:
                intent = {"action": "insertComponent", "params": {"type": "container"}}
            elif "grade" in transcript or "grid" in transcript:
                intent = {"action": "insertComponent", "params": {"type": "grid"}}
        elif "apagar" in transcript or "deletar" in transcript or "delete" in transcript:
            intent = {"action": "deleteElement"}
        elif "duplicar" in transcript or "duplicate" in transcript:
            intent = {"action": "duplicateElement"}
        elif "cor" in transcript or "color" in transcript:
            intent = {"action": "updateStyle", "params": {"property": "color"}}
        elif "fundo" in transcript or "background" in transcript:
            intent = {"action": "updateStyle", "params": {"property": "backgroundColor"}}
        
        return _json_response(start_response, "200 OK", hdrs, intent)

    # ── Export/Handoff endpoints ──
    if path == "/api/design/export/html" and method == "POST":
        body_size = int(environ.get("CONTENT_LENGTH", 0))
        body = json.loads(environ["wsgi.input"].read(body_size)) if body_size else {}
        html = body.get("html", "")
        tokens = body.get("tokens", {})
        components = body.get("components", {})
        title = body.get("title", "Hermes Prototype")
        
        # Generate self-contained HTML bundle
        css_vars = []
        for name, token in tokens.items():
            if token.get("type") == "color":
                css_vars.append(f"  --hds-{name.replace('_', '-')}: {token['value']};")
        
        bundle = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>
    :root {{
{chr(10).join(css_vars)}
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: system-ui, sans-serif; }}
  </style>
</head>
<body>
  {html}
  <script>
    // Design tokens available as CSS custom properties
    console.log('Hermes Prototype loaded. Tokens:', Object.keys({json.dumps({k: v.get('value', '') for k, v in tokens.items() if v.get('type') == 'color'})}));
  </script>
</body></html>"""
        
        return _json_response(start_response, "200 OK", hdrs, {
            "format": "html",
            "filename": f"{title.lower().replace(' ', '-')}.html",
            "content": bundle
        })

    if path == "/api/design/export/tailwind" and method == "POST":
        body_size = int(environ.get("CONTENT_LENGTH", 0))
        body = json.loads(environ["wsgi.input"].read(body_size)) if body_size else {}
        tokens = body.get("tokens", {})
        
        # Generate Tailwind config
        colors = {}
        spacing = {}
        fontSize = {}
        borderRadius = {}
        boxShadow = {}
        
        for name, token in tokens.items():
            if token.get("type") == "color":
                colors[name.replace('_', '-')] = token['value']
            elif token.get("type") == "spacing":
                spacing[name.replace('_', '-')] = token['value']
            elif token.get("type") == "typography":
                if 'fontSize' in name:
                    fontSize[name.replace('_', '-')] = token['value']
            elif token.get("type") == "radius":
                borderRadius[name.replace('_', '-')] = token['value']
            elif token.get("type") == "shadow":
                boxShadow[name.replace('_', '-')] = token['value']
        
        config = f"""/** @type {{import('tailwindcss').Config}} */
module.exports = {{
  content: ['./*.html', './**/*.{{js,ts,jsx,tsx,vue,svelte}}'],
  theme: {{
    extend: {{
      colors: {json.dumps(colors, indent=6)},
      spacing: {json.dumps(spacing, indent=6)},
      fontSize: {json.dumps(fontSize, indent=6)},
      borderRadius: {json.dumps(borderRadius, indent=6)},
      boxShadow: {json.dumps(boxShadow, indent=6)},
    }},
  }},
  plugins: [],
}}"""
        
        return _json_response(start_response, "200 OK", hdrs, {
            "format": "tailwind.config.js",
            "filename": "tailwind.config.js",
            "content": config
        })

    if path == "/api/design/export/code-bundle" and method == "POST":
        body_size = int(environ.get("CONTENT_LENGTH", 0))
        body = json.loads(environ["wsgi.input"].read(body_size)) if body_size else {}
        html = body.get("html", "")
        tokens = body.get("tokens", {})
        components = body.get("components", {})
        framework = body.get("framework", "react")  # react, vue, svelte
        
        # Generate component files based on framework
        files = {}
        
        if framework == "react":
            # Extract components from HTML and create React components
            files["src/components/index.js"] = "// Auto-generated React components from Hermes Design Canvas\n"
            files["src/components/index.js"] += "export { Button } from './Button';\n"
            files["src/components/index.js"] += "export { Card } from './Card';\n"
            files["src/components/index.js"] += "export { Input } from './Input';\n"
            
            files["src/components/Button.jsx"] = '''import React from 'react';

export const Button = ({ children, variant = 'primary', size = 'md', ...props }) => {
  const baseStyles = {
    padding: '12px 24px',
    borderRadius: '8px',
    fontWeight: 500,
    border: 'none',
    cursor: 'pointer',
    fontFamily: 'inherit',
    transition: 'all 0.15s',
  };
  
  const variants = {
    primary: { background: 'var(--hds-accent)', color: 'var(--hds-bg)' },
    secondary: { background: 'transparent', color: 'var(--hds-fg)', border: '1px solid var(--hds-border)' },
    danger: { background: 'transparent', color: 'var(--hds-danger)', border: '1px solid var(--hds-danger)' }
  };
  
  const sizes = {
    sm: { padding: '8px 16px', fontSize: '0.875rem' },
    md: { padding: '12px 24px', fontSize: '0.95rem' },
    lg: { padding: '16px 32px', fontSize: '1.1rem' }
  };
  
  const style = { ...baseStyles, ...variants[variant], ...sizes[size], ...props.style };
  
  return <button style={style} {...props}>{children}</button>;
};'''
            
            files["src/components/Card.jsx"] = '''import React from 'react';

export const Card = ({ title, children, ...props }) => {
  return (
    <div style={{
      padding: '24px',
      background: 'var(--hds-card)',
      border: '1px solid var(--hds-border)',
      borderRadius: '12px',
      ...props.style
    }} {...props}>
      {title && <h3 style={{ margin: '0 0 8px' }}>{title}</h3>}
      {children}
    </div>
  );
};'''
            
            files["src/components/Input.jsx"] = '''import React from 'react';

export const Input = ({ placeholder, ...props }) => {
  return (
    <input
      type="text"
      placeholder={placeholder}
      style={{
        padding: '12px 16px',
        background: 'var(--hds-bg)',
        border: '1px solid var(--hds-border)',
        borderRadius: '8px',
        color: 'var(--hds-fg)',
        width: '100%',
        fontFamily: 'inherit',
        fontSize: '1rem',
        ...props.style
      }}
      {...props}
    />
  );
};'''
            
            files["package.json"] = json.dumps({
                "name": "hermes-design-bundle",
                "version": "1.0.0",
                "main": "src/components/index.js",
                "peerDependencies": {
                    "react": ">=18"
                },
                "devDependencies": {
                    "tailwindcss": "^3.4.0"
                }
            }, indent=2)
            
            files["tailwind.config.js"] = '''/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {}
  },
  plugins: [],
}'''
        
        elif framework == "vue":
            files["src/components/Button.vue"] = '''<script setup>
defineProps({
  variant: { type: String, default: 'primary' },
  size: { type: String, default: 'md' }
});
</script>

<template>
  <button :class="['hds-btn', `hds-btn--${variant}`, `hds-btn--${size}`]">
    <slot />
  </button>
</template>

<style scoped>
.hds-btn {
  padding: 12px 24px;
  border-radius: 8px;
  font-weight: 500;
  border: none;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.15s;
}
.hds-btn--primary { background: var(--hds-accent); color: var(--hds-bg); }
.hds-btn--secondary { background: transparent; color: var(--hds-fg); border: 1px solid var(--hds-border); }
.hds-btn--danger { background: transparent; color: var(--hds-danger); border: 1px solid var(--hds-danger); }
.hds-btn--sm { padding: 8px 16px; font-size: 0.875rem; }
.hds-btn--lg { padding: 16px 32px; font-size: 1.1rem; }
</style>'''
            
            files["package.json"] = json.dumps({
                "name": "hermes-design-bundle",
                "version": "1.0.0",
                "main": "src/components/index.js",
                "peerDependencies": {
                    "vue": ">=3"
                },
                "devDependencies": {
                    "tailwindcss": "^3.4.0"
                }
            }, indent=2)
        
        elif framework == "svelte":
            files["src/components/Button.svelte"] = '''<script>
  export let variant = 'primary';
  export let size = 'md';
</script>

<button class="hds-btn hds-btn--{variant} hds-btn--{size}">
  <slot />
</button>

<style>
  .hds-btn {
    padding: 12px 24px;
    border-radius: 8px;
    font-weight: 500;
    border: none;
    cursor: pointer;
    font-family: inherit;
    transition: all 0.15s;
  }
  .hds-btn--primary { background: var(--hds-accent); color: var(--hds-bg); }
  .hds-btn--secondary { background: transparent; color: var(--hds-fg); border: 1px solid var(--hds-border); }
  .hds-btn--danger { background: transparent; color: var(--hds-danger); border: 1px solid var(--hds-danger); }
  .hds-btn--sm { padding: 8px 16px; font-size: 0.875rem; }
  .hds-btn--lg { padding: 16px 32px; font-size: 1.1rem; }
</style>'''
            
            files["package.json"] = json.dumps({
                "name": "hermes-design-bundle",
                "version": "1.0.0",
                "main": "src/components/index.js",
                "peerDependencies": {
                    "svelte": ">=4"
                },
                "devDependencies": {
                    "tailwindcss": "^3.4.0"
                }
            }, indent=2)
        
        # Create ZIP in memory
        import zipfile
        import io
        
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for filename, content in files.items():
                zf.writestr(filename, content)
        
        zip_buffer.seek(0)
        zip_b64 = base64.b64encode(zip_buffer.read()).decode('ascii')
        
        return _json_response(start_response, "200 OK", hdrs, {
            "format": "code-bundle",
            "framework": framework,
            "filename": f"hermes-design-bundle-{framework}.zip",
            "content_b64": zip_b64,
            "files": list(files.keys())
        })

    if path == "/api/design/export/pptx" and method == "POST":
        body_size = int(environ.get("CONTENT_LENGTH", 0))
        body = json.loads(environ["wsgi.input"].read(body_size)) if body_size else {}
        html = body.get("html", "")
        tokens = body.get("tokens", {})
        title = body.get("title", "Hermes Prototype")
        
        # Generate PPTX using python-pptx if available, otherwise return instructions
        try:
            from pptx import Presentation
            from pptx.util import Inches, Pt, Emu
            from pptx.dml.color import RGBColor
            from pptx.enum.text import PP_ALIGN
            import io
            
            prs = Presentation()
            prs.slide_width = Inches(13.333)
            prs.slide_height = Inches(7.5)
            
            # Extract colors from tokens
            bg_color = "#030712"
            fg_color = "#f5f5f0"
            accent_color = "#ff8c00"
            for name, token in tokens.items():
                if token.get("type") == "color":
                    if "bg" in name.lower() or "background" in name.lower():
                        bg_color = token['value']
                    elif "fg" in name.lower() or "foreground" in name.lower():
                        fg_color = token['value']
                    elif "accent" in name.lower() or "primary" in name.lower():
                        accent_color = token['value']
            
            def hex_to_rgb(hex_color):
                hex_color = hex_color.lstrip('#')
                return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
            
            bg_rgb = hex_to_rgb(bg_color)
            fg_rgb = hex_to_rgb(fg_color)
            accent_rgb = hex_to_rgb(accent_color)
            
            # Slide 1: Title
            slide_layout = prs.slide_layouts[0]  # Title slide
            slide = prs.slides.add_slide(slide_layout)
            slide.background.fill.solid()
            slide.background.fill.fore_color.rgb = RGBColor(*bg_rgb)
            
            if slide.shapes.title:
                slide.shapes.title.text = title
                slide.shapes.title.text_frame.paragraphs[0].font.color.rgb = RGBColor(*fg_rgb)
                slide.shapes.title.text_frame.paragraphs[0].font.size = Pt(44)
            
            if len(slide.placeholders) > 1:
                slide.placeholders[1].text = "Generated from Hermes Design Canvas"
                slide.placeholders[1].text_frame.paragraphs[0].font.color.rgb = RGBColor(*accent_rgb)
                slide.placeholders[1].text_frame.paragraphs[0].font.size = Pt(20)
            
            # Slide 2: Design Tokens
            slide_layout = prs.slide_layouts[1]  # Title and content
            slide = prs.slides.add_slide(slide_layout)
            slide.background.fill.solid()
            slide.background.fill.fore_color.rgb = RGBColor(*bg_rgb)
            
            slide.shapes.title.text = "Design Tokens"
            slide.shapes.title.text_frame.paragraphs[0].font.color.rgb = RGBColor(*fg_rgb)
            
            content = slide.placeholders[1]
            tf = content.text_frame
            tf.clear()
            
            for token_type in ["color", "spacing", "typography", "radius", "shadow"]:
                type_tokens = {k: v for k, v in tokens.items() if v.get("type") == token_type}
                if type_tokens:
                    p = tf.add_paragraph()
                    p.text = f"{token_type.capitalize()}:"
                    p.font.bold = True
                    p.font.color.rgb = RGBColor(*accent_rgb)
                    p.font.size = Pt(18)
                    
                    for name, token in list(type_tokens.items())[:10]:
                        p = tf.add_paragraph()
                        p.text = f"  {name}: {token.get('value', '')}"
                        p.font.color.rgb = RGBColor(*fg_rgb)
                        p.font.size = Pt(12)
                        p.level = 1
            
            # Save to bytes
            pptx_buffer = io.BytesIO()
            prs.save(pptx_buffer)
            pptx_buffer.seek(0)
            pptx_b64 = base64.b64encode(pptx_buffer.read()).decode('ascii')
            
            return _json_response(start_response, "200 OK", hdrs, {
                "format": "pptx",
                "filename": f"{title.lower().replace(' ', '-')}.pptx",
                "content_b64": pptx_b64
            })
        except ImportError:
            return _json_response(start_response, "200 OK", hdrs, {
                "format": "pptx",
                "error": "python-pptx not installed. Run: pip install python-pptx",
                "instructions": "Install python-pptx to generate PPTX files"
            })

    if path == "/api/design/export/figma" and method == "POST":
        body_size = int(environ.get("CONTENT_LENGTH", 0))
        body = json.loads(environ["wsgi.input"].read(body_size)) if body_size else {}
        html = body.get("html", "")
        tokens = body.get("tokens", {})
        components = body.get("components", {})
        figma_token = body.get("figma_token", "")
        file_key = body.get("file_key", "")
        
        # Generate Figma plugin manifest + code
        plugin_manifest = {
            "name": "Hermes Design Import",
            "id": "hermes-design-import",
            "api": "1.0.0",
            "main": "code.js",
            "ui": "ui.html",
            "capabilities": [],
            "enableProposedApi": False,
            "documentAccess": "dynamic-page"
        }
        
        plugin_code = f'''// Hermes Design Canvas → Figma Plugin
// Usage: 
// 1. Create new Figma plugin (Figma → Plugins → Development → New Plugin)
// 2. Copy this code to code.js
// 3. Copy ui.html to ui.html
// 4. Run plugin in Figma

figma.showUI(__html__, {{ width: 400, height: 600 }});

const designTokens = {json.dumps(tokens, indent=2)};
const components = {json.dumps(components, indent=2)};

figma.ui.onmessage = async (msg) => {{
  if (msg.type === 'create-tokens') {{
    await createDesignTokens(designTokens);
  }}
  if (msg.type === 'create-components') {{
    await createComponents(components);
  }}
  if (msg.type === 'import-html') {{
    await importHTML(msg.html);
  }}
}};

async function createDesignTokens(tokens) {{
  for (const [name, token] of Object.entries(tokens)) {{
    if (token.type === 'color') {{
      const hex = token.value;
      const r = parseInt(hex.slice(1, 3), 16) / 255;
      const g = parseInt(hex.slice(3, 5), 16) / 255;
      const b = parseInt(hex.slice(5, 7), 16) / 255;
      
      await figma.createPaintStyleAsync(name, {{
        type: 'SOLID',
        color: {{ r, g, b }}
      }});
    }}
  }}
  figma.ui.postMessage({{ type: 'tokens-done', count: Object.keys(tokens).length }});
}}

async function createComponents(components) {{
  // Create component frames for each registered component
  for (const [name, comp] of Object.entries(components)) {{
    const frame = figma.createFrame();
    frame.name = name;
    frame.resize(300, 200);
    frame.fills = [{{ type: 'SOLID', color: {{ r: 0.95, g: 0.95, b: 0.95 }} }}];
    figma.currentPage.appendChild(frame);
  }}
  figma.ui.postMessage({{ type: 'components-done', count: Object.keys(components).length }});
}}

async function importHTML(html) {{
  // Parse HTML and create Figma nodes
  // This is a simplified version - real implementation would need HTML parser
  const frame = figma.createFrame();
  frame.name = "Imported from Hermes";
  frame.resize(1200, 800);
  figma.currentPage.appendChild(frame);
  figma.ui.postMessage({{ type: 'html-done' }});
}}'''

        plugin_ui = '''<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>
    body { font-family: system-ui; padding: 16px; background: #0d1117; color: #e6edf3; }
    button { background: #ff8c00; color: #0d1117; border: none; padding: 10px 16px; border-radius: 6px; cursor: pointer; margin: 8px 0; width: 100%; font-weight: 500; }
    button:hover { background: #e67e22; }
    .status { margin-top: 16px; font-size: 14px; color: #8b949e; }
  </style>
</head>
<body>
  <h2>Hermes Design Import</h2>
  <p>Import design tokens & components from Hermes Design Canvas</p>
  <button onclick="createTokens()">Criar Design Tokens</button>
  <button onclick="createComponents()">Criar Componentes</button>
  <button onclick="importHTML()">Importar HTML</button>
  <div class="status" id="status">Aguardando...</div>
  
  <script>
    function createTokens() {
      document.getElementById('status').textContent = 'Criando tokens...';
      parent.postMessage({ pluginMessage: { type: 'create-tokens' } }, '*');
    }
    function createComponents() {
      document.getElementById('status').textContent = 'Criando componentes...';
      parent.postMessage({ pluginMessage: { type: 'create-components' } }, '*');
    }
    function importHTML() {
      document.getElementById('status').textContent = 'Importando HTML...';
      parent.postMessage({ pluginMessage: { type: 'import-html', html: document.body.innerHTML } }, '*');
    }
    window.onmessage = (event) => {
      if (event.data.pluginMessage) {
        const msg = event.data.pluginMessage;
        if (msg.type === 'tokens-done') document.getElementById('status').textContent = `Tokens criados: ${msg.count}`;
        if (msg.type === 'components-done') document.getElementById('status').textContent = `Componentes criados: ${msg.count}`;
        if (msg.type === 'html-done') document.getElementById('status').textContent = 'HTML importado!';
      }
    };
  </script>
</body></html>'''

        return _json_response(start_response, "200 OK", hdrs, {
            "format": "figma-plugin",
            "manifest": plugin_manifest,
            "code_js": plugin_code,
            "ui_html": plugin_ui,
            "instructions": "1. Create new Figma plugin (Figma → Plugins → Development → New Plugin)\n2. Copy code.js and ui.html\n3. Run plugin in Figma\n4. Click buttons to import tokens/components"
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