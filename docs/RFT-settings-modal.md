# RFT — Settings Modal

> **Task:** `t_6a7ab070` | **Parent:** `t_16d26433` | **Status:** Em escrita
> **Depende de:** `t_3fd569a1` (Design aprovado) ✅

---

## 1. Visão e Escopo

### 1.1 O que é
Modal de configurações do Hermes Voice acessível via botão ⚙️ no header. Permite configurar voz TTS, modelo STT, idioma, testar microfone, toggles gerais e zona de perigo.

### 1.2 Para quem
- **P1 Dev Hermes** — troca voz/modelo frequente
- **P2 Voice-First** — configura uma vez no onboarding
- **P3 Builder** — integra via bridge API

### 1.3 Anti-scope
- ❌ Não edita config do Hermes Agent (modelo LLM, skills, etc.)
- ❌ Não gerencia múltiplos perfis de usuário
- ❌ Não é página — é modal overlay

---

## 2. Arquitetura

### 2.1 Stack
| Camada | Tech | Localização |
|--------|------|-------------|
| Frontend (Web) | Vanilla HTML/CSS/JS | `web/index.html` → componente `SettingsModal` |
| Frontend (Tauri) | Mesmo código via WebView | `src-tauri/` |
| Bridge API | Python WSGI | `bridge-server.py` |
| Persistência Web | `localStorage` | `hermes-voice-settings` |
| Persistência Desktop | `tauri-plugin-store` | `settings.json` |

### 2.2 Fluxo de Dados
```
User abre modal
    ↓
Frontend carrega settings do localStorage/store
    ↓
User altera campos
    ↓
User clica "Salvar"
    ↓
Frontend salva no localStorage/store
    ↓
Frontend notifica bridge (opcional) → POST /api/settings
    ↓
Bridge aplica: troca voz TTS, recarrega Whisper se modelo mudou
```

### 2.3 Decisões Técnicas
| Decisão | Rationale |
|---------|-----------|
| `localStorage` + `tauri-plugin-store` | Simples, sem backend, sync manual se necessário |
| Bridge recebe `/api/settings` (POST) | Permite reload de Whisper model sem restart app |
| Whisper singleton no bridge | Mudança de modelo = reload singleton (lazy) |
| TTS preview via bridge `/api/chat` com `voice:true` | Reusa pipeline existente, testa voz real |

---

## 3. Modelo de Dados

### 3.1 Schema `Settings` (JSON)
```json
{
  "ttsVoice": "pt-BR-AntonioNeural",
  "whisperModel": "base",
  "sttLanguage": "pt",
  "autoStartBridge": true,
  "saveAudio": true,
  "forceDarkMode": false,
  "version": 1
}
```

### 3.2 Validação
| Campo | Tipo | Valores válidos | Default |
|-------|------|-----------------|---------|
| `ttsVoice` | string | Lista `TTS_VOICES` (7 vozes) | `pt-BR-AntonioNeural` |
| `whisperModel` | string | `["tiny","base","small","medium","large-v3"]` | `base` |
| `sttLanguage` | string | `["pt","en","es","fr","auto"]` | `pt` |
| `autoStartBridge` | boolean | - | `true` |
| `saveAudio` | boolean | - | `true` |
| `forceDarkMode` | boolean | - | `false` |

### 3.3 Migração
- `version` no schema → futuro `migrateSettings(v1→v2)`

---

## 4. API Endpoints (Bridge)

### 4.1 `GET /api/settings`
Retorna settings atuais do bridge (merge: defaults + persisted + runtime)

```json
{
  "ttsVoice": "pt-BR-AntonioNeural",
  "whisperModel": "base",
  "sttLanguage": "pt",
  "autoStartBridge": true,
  "saveAudio": true,
  "forceDarkMode": false,
  "availableVoices": [...],
  "availableModels": [...],
  "availableLanguages": [...]
}
```

### 4.2 `POST /api/settings`
Atualiza settings no bridge + persiste

**Request:**
```json
{
  "ttsVoice": "pt-BR-FranciscaNeural",
  "whisperModel": "small",
  "sttLanguage": "en",
  "autoStartBridge": true,
  "saveAudio": true,
  "forceDarkMode": false
}
```

**Response 200:**
```json
{
  "ok": true,
  "settings": { ... },
  "whisperReloaded": false
}
```

**Response 400:** Campo inválido
```json
{ "error": "invalid_field", "field": "whisperModel", "message": "Modelo 'huge' não suportado" }
```

### 4.3 `POST /api/settings/preview-tts`
Gera preview da voz selecionada

**Request:** `{ "voice": "pt-BR-FranciscaNeural", "text": "Olá, sou a Francisca." }`

**Response:** `{ "audio_url": "/api/audio/abc123def456" }`

### 4.4 `GET /api/health` (já existe)
Já retorna `stt: true/false` — bridge usa `whisperModel` ativo.

---

## 5. Fluxos de UI

### 5.1 Estados do Modal
| Estado | Descrição |
|--------|-----------|
| **Fechado** | Default. Abre via botão ⚙️ no header |
| **Abrindo** | Animação slideUp + fadeIn backdrop (200ms) |
| **Aberto** | Focus trap no modal. ESC fecha. Click backdrop fecha. |
| **Salvando** | Botão "Salvar" disabled + loading. POST `/api/settings` |
| **Erro** | Toast inline no modal (não fecha). Retry disponível. |
| **Sucesso** | Toast "Salvo" → fecha após 800ms |
| **Fechando** | Animação fadeOut + slideDown (150ms) |

### 5.2 Seções (Tabs implícitas via scroll)
| Seção | Campos | Validação |
|-------|--------|-----------|
| **Voz (TTS)** | Select voz (7) + btn "Ouvir prévia" | Preview chama `/api/settings/preview-tts` |
| **Transcrição (STT)** | Select modelo (5) + select idioma (5) | Mudança de modelo → avisa "Recarregará Whisper" |
| **Microfone** | Visualizador nível + btn "Testar" | `getUserMedia` + `MediaRecorder` → visualizador tempo real |
| **Geral** | 3 toggles (auto-start, save audio, dark mode) | Dark mode aplica imediato no DOM |
| **Zona de Perigo** | "Limpar cache" + "Restaurar padrões" | Confirmação nativa `confirm()` |

### 5.3 Responsivo
- **Desktop (≤560px):** Modal centralizado, max-width 560px
- **Mobile (>560px):** Modal full-screen, border-radius 0, footer stacked

---

## 6. Integrações

### 6.1 Bridge Python (`bridge-server.py`)
```python
# Novos endpoints
@app.route("/api/settings", methods=["GET"])
def get_settings(): ...

@app.route("/api/settings", methods=["POST"])
def update_settings(): ...

@app.route("/api/settings/preview-tts", methods=["POST"])
def preview_tts(): ...
```

### 6.2 Whisper Singleton Reload
```python
# Em update_settings, se whisperModel mudou:
global _WHISPER
_WHISPER = None  # Força reload no próximo get_whisper()
```

### 6.3 Tauri Commands (para store)
```rust
#[tauri::command]
async fn get_settings() -> Result<Settings, String> { ... }

#[tauri::command]
async fn save_settings(settings: Settings) -> Result<(), String> { ... }
```

### 6.4 Frontend Web (`web/index.html`)
- Componente `SettingsModal` (ES6 class ou IIFE)
- `localStorage` key: `hermes-voice-settings`
- Sync com bridge: `fetch("/api/settings")` on open, `POST` on save

---

## 7. Segurança e Transações

| Aspecto | Tratamento |
|---------|------------|
| **CORS** | Bridge já libera `*` — OK para local |
| **Validação** | Bridge valida todos os campos (whitelist) |
| **Path traversal** | Audio ID regex `^[a-f0-9]+$` já existe |
| **Persistência** | `localStorage` (web) + `tauri-plugin-store` (desktop) — não sensível |
| **LGPD** | Nenhum dado pessoal — só preferências de UI/voz |

---

## 8. Plano de Deploy

| Etapa | Comando | Artefato |
|-------|---------|----------|
| Dev | `./scripts/dev.sh` | Bridge 8420 + Web 8080 |
| Test | `pytest tests/ -v -k settings` | 13+ testes |
| Build | `npm run build` | `.deb` / `.msi` / `.dmg` |
| Release | Tag `v0.1.0` → GitHub Actions | Assets no release |

---

## 9. Critérios de Pronto (Definition of Done)

- [ ] `GET /api/settings` retorna schema completo
- [ ] `POST /api/settings` valida + persiste + recarrega Whisper se modelo mudou
- [ ] `POST /api/settings/preview-tts` retorna `audio_url` tocável
- [ ] Modal abre/fecha (ESC, backdrop, X, botão Cancelar)
- [ ] Focus trap funcionando (Tab circula dentro do modal)
- [ ] `localStorage` + `tauri-plugin-store` sincronizam
- [ ] Dark mode toggle aplica imediato no DOM (sem reload)
- [ ] TTS preview toca voz real via bridge
- [ ] Mic test visualizador anima em tempo real
- [ ] Zona de perigo: limpa cache `/tmp/hermes-audio/`, reseta defaults
- [ ] Testes pytest: `test_settings_get`, `test_settings_post_valid`, `test_settings_post_invalid`, `test_settings_preview_tts`, `test_whisper_reload_on_model_change`
- [ ] Responsivo: desktop + mobile (Chrome DevTools device toolbar)
- [ ] Acessível: ARIA labels, roles, focus-visible, contraste WCAG AA

---

## 10. Pendências / Decisões Futuras

1. **Sync cross-device?** Não v1 — só local
2. **Import/Export settings JSON?** Nice-to-have v0.2
3. **Perfil por projeto?** Fora de escopo
4. **Notificação sistema (Tauri)?** Só se `autoStartBridge` falhar