# Hermes Desktop

Desktop client para [Hermes Agent](https://hermes-agent.nousresearch.com/docs).
Dois frontends sobre o mesmo bridge:

- **Web** (`web/index.html`) — single-file HTML, abre no navegador
- **Tauri** (`src-tauri/`) — app nativo leve (~14 MB stripped) usando WebView do sistema

## O que faz

Janela de chat com **texto e voz** (STT + TTS) que se conecta ao `hermes chat` CLI rodando local.

```
┌──────────────────────────────────────────────────────────────┐
│  Frontend (web ou Tauri WebView)                             │
│      ↓ HTTP                                                  │
│  Bridge Server (Python WSGI, porta 8420)  ← thread-safe      │
│      ↓ subprocess                                            │
│  Hermes Agent (hermes chat -q)                               │
│      ↓ API                                                   │
│  LLM (OpenRouter, etc.)                                      │
└──────────────────────────────────────────────────────────────┘
```

## Quick start

### Pré-requisitos

**Sistema (Fedora 43 testado):**
```bash
sudo dnf install rust cargo nodejs npm webkit2gtk4.1-devel \
  libsoup3-devel libayatana-appindicator-gtk3-devel \
  openssl-devel pkg-config python3-pip
```

**Python (opcional — só pra STT/TTS):**
```bash
pip install faster-whisper edge-tts pytest
```

**Hermes CLI:** siga [docs oficiais](https://hermes-agent.nousresearch.com/docs) e garanta que `hermes chat -q "ping" --quiet` funciona no seu shell.

### Rodar web (mais rápido)

```bash
./launch.sh
# Frontend: http://127.0.0.1:8080
# Bridge:   http://127.0.0.1:8420/api/health
```

`Ctrl+C` mata os dois processos.

### Rodar Tauri desktop (binário nativo)

```bash
npm install
npm run tauri dev
```

Compila (~1-2 min na primeira vez, <30s incremental) e abre a janela.

### Build de produção

```bash
npm run build
# Saída:
#   src-tauri/target/release/hermes-voice-frontend  (binário ~14 MB)
#   src-tauri/target/release/bundle/deb/*.deb       (instalador)
```

`./src-tauri/target/release/hermes-voice-frontend` roda standalone (assume que `launch.sh` está ativo pra ter o bridge).

## Endpoints do bridge

| Método | Path | Body | Resposta |
|--------|------|------|----------|
| GET | `/api/health` | — | `{status, stt, tts}` |
| GET | `/api/audio/<id>` | — | `audio/mpeg` (MP3) |
| POST | `/api/chat` | `{message, session_id?, voice?}` | `{text, session_id, audio_url?}` |
| POST | `/api/chat` (multipart) | `audio` file + `session_id` field | `{user_text, text, audio_url, session_id}` |

`session_id` é gerado pelo Hermes no primeiro request e retornado na resposta. Use-o no request seguinte pra manter contexto.

## Arquitetura

```
hermes-desktop/
├── bridge-server.py        # Python WSGI, ThreadingMixIn
├── launch.sh               # Sobe bridge + http.server
├── web/
│   └── index.html          # Frontend (vanilla JS, ~250 linhas)
├── src-tauri/
│   ├── Cargo.toml          # Deps Rust
│   ├── tauri.conf.json     # Config Tauri v2
│   ├── icons/              # PNG, ICO (gerados de SVG)
│   └── src/main.rs         # 5 commands Tauri → bridge
├── tests/
│   └── test_bridge.py      # 10 testes pytest
└── .github/
    └── workflows/ci.yml    # pytest + cargo check
```

### Por que Tauri e não Electron?

| | Tauri | Electron |
|---|---|---|
| Binário | ~14 MB | ~120 MB |
| Runtime | WebView do sistema | Chromium embarcado |
| Backend | Rust (rápido, type-safe) | Node.js |
| Startup | <500ms | ~1-2s |

Tauri usa o WebView nativo (WebKitGTK no Linux, WebView2 no Windows, WebKit no macOS).

### Por que bridge server e não chamar `hermes` direto do Tauri?

- **Reuso**: mesmo bridge serve web e Tauri, e pode ser usado por curl, scripts, outros frontends
- **State**: sessão fica no servidor, não precisa serializar
- **Cache de áudio**: MP3 servidos via URL, navegador faz cache
- **Threading**: `ThreadingMixIn` permite requests paralelos (ver `tests/test_bridge.py::test_concurrency`)

## Configuração

| Env var | Default | Descrição |
|---------|---------|-----------|
| `HERMES_BRIDGE_URL` | `http://127.0.0.1:8420` | URL do bridge (Tauri lê isso) |
| `HERMES_PROFILE` | `coder` | Profile do Hermes a usar (lido no `bridge-server.py` se quiser) |

Edite `web/index.html` linha 94 (`const API = ...`) pra mudar a URL do bridge na versão web.

## Desenvolvimento

```bash
# Atalho: build + run + watch
cd src-tauri
cargo run                       # rápido, sem CLI
# ou
cargo run --release             # otimizado

# Lint Python
ruff check bridge-server.py

# Testes
pytest tests/ -v

# Lint Rust
cargo clippy
```

## Troubleshooting

**Tauri app fecha imediatamente ao abrir:**
- Tauri v2 por padrão encerra quando a última janela fecha. Use `tray-icon` se quiser manter vivo.

**`/api/health` retorna `stt: false`:**
- `faster-whisper` não está instalado: `pip install faster-whisper`

**`/api/health` retorna `tts: false`:**
- `edge-tts` não está instalado: `pip install edge-tts`

**`hermes chat` trava ou dá timeout:**
- O `hermes chat -q` está demorando mais que 120s. Verifique conexão com OpenRouter.

**Tauri build falha com "PluginInitialization" panic:**
- Veja a skill `tauri-v2-gotchas` (erro comum: `"plugins": { "shell": { "allowlist": [...] } }` — use `{ "open": true }`).

**Web frontend não conecta ao bridge:**
- Verifique se `bridge-server.py` está rodando: `curl http://127.0.0.1:8420/api/health`
- Veja o CORS — o bridge libera tudo (`Access-Control-Allow-Origin: *`)

**`whisper` re-carrega a cada request:**
- Não deveria — `get_whisper()` é singleton. Se vir, check se a função não foi chamada fora do handler.

## Licença

MIT. Veja [LICENSE](LICENSE).

## Créditos

Construído com [Hermes Agent](https://hermes-agent.nousresearch.com) — open-source AI agent framework por Nous Research.
