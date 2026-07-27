# RFT — Onboarding First-Run

> **Task:** `t_d9377031` | **Parent:** `t_16d26433` | **Status:** Em escrita
> **Depende de:** `t_8c699407` (Design aprovado) ✅

---

## 1. Visão e Escopo

### 1.1 O que é
Fluxo de primeira execução (first-run) do Hermes Voice que guia o usuário em 4 passos:
1. **Boas-vindas** — value props
2. **Permissão microfone** — concede acesso + explica privacidade
3. **Seleção voz TTS** — escolhe voz + preview
4. **Teste microfone** — grava ~3s → envia pro STT local → confirma funcionando

Ao final, salva flag `onboardingComplete=true` no `localStorage` (web) + Tauri store (desktop) para não repetir.

### 1.2 Para quem
- **P2 Voice-First** (early adopter não-técnico) — principal beneficiário
- **P1 Dev Hermes** — passa rápido, mas valida config
- **P3 Builder** — vê como integrar onboarding no próprio app

### 1.3 Anti-scope
- ❌ Não configura modelo LLM / API keys (fora de escopo)
- ❌ Não configura Whisper model download (usa `base` padrão)
- ❌ Não cria conta / cloud sync (local-first)
- ❌ Não é tutorial de features (só setup essencial)

---

## 2. Arquitetura

### 2.1 Stack
| Camada | Tech | Localização |
|--------|------|-------------|
| UI | Vanilla HTML/CSS/JS (single file) | `web/onboarding.html` ou embed em `index.html` |
| Bridge API | Python WSGI (endpoints preview/test) | `bridge-server.py` |
| STT | faster-whisper (já carrega no bridge) | Singleton `_WHISPER` |
| TTS | edge-tts (já carrega no bridge) | `text_to_speech()` |
| Persistência Web | `localStorage` | `hermes-voice-onboarding` |
| Persistência Tauri | `tauri-plugin-store` | `onboarding.json` |

### 2.2 Decisões Técnicas
| Decisão | Rationale |
|---------|-----------|
| Single-page (hash nav ou step state) | Simplicidade, sem router, carrega instantâneo |
| Endpoints dedicados `/api/onboarding/*` | Separação clara, testável, não polui `/api/chat` |
| Preview TTS via bridge | Usa mesma pipeline produção (edge-tts → MP3) |
| Teste mic grava webm/opus → STT local | Formato nativo MediaRecorder, sem conversão |
| Flag persistida em 2 lugares | Web + Desktop independentes, mas mesmo fluxo |

---

## 3. Modelo de Dados

### 3.1 `localStorage` / Tauri Store
```json
{
  "onboardingComplete": true,
  "completedAt": "2026-07-27T10:30:00Z",
  "version": 1,
  "settings": {
    "ttsVoice": "pt-BR-AntonioNeural",
    "micGranted": true,
    "micTested": true
  }
}
```

### 3.2 Estado Transitório (durante fluxo)
```javascript
{
  currentStep: 1,           // 1-5
  selectedVoiceId: "pt-BR-AntonioNeural",
  micGranted: false,
  micTested: false,
  micTestBlob: null         // webm/opus gravado no passo 4
}
```

---

## 4. API Endpoints (Bridge)

### 4.1 `POST /api/onboarding/preview-tts`
Gera preview da voz selecionada

**Request:**
```json
{ "voice": "pt-BR-FranciscaNeural", "text": "Olá, sou o Hermes." }
```

**Response 200:**
```json
{ "audio_url": "/api/audio/abc123def" }
```
*Usa `text_to_speech()` existente + cache `/tmp/hermes-audio/`*

### 4.2 `POST /api/onboarding/test-mic`
Recebe áudio webm/opus → transcreve via faster-whisper → retorna texto

**Request:** `multipart/form-data`
- `audio` (file): webm/opus
- `language` (string, opcional): `pt`, `en`, `auto` — default `pt`

**Response 200:**
```json
{ "text": "olá teste do microfone", "duration_ms": 2840 }
```

**Response 200 (STT falhou):**
```json
{ "text": "", "stt_error": true, "error": "no_speech_detected" }
```

### 4.3 `GET /api/onboarding/voices`
Lista vozes Edge TTS disponíveis (cacheado)

**Response 200:**
```json
{
  "voices": [
    { "id": "pt-BR-AntonioNeural", "name": "Antônio", "lang": "pt-BR", "gender": "Male" },
    { "id": "pt-BR-FranciscaNeural", "name": "Francisca", "lang": "pt-BR", "gender": "Female" },
    ...
  ]
}
```

---

## 5. Fluxos de UI (Step by Step)

### 5.1 Passo 1 — Boas-vindas
| Elemento | Comportamento |
|----------|---------------|
| Headline | "Bem-vindo ao Hermes Voice" |
| Subtitle | "Seu agente local com voz — privado, rápido, seu" |
| Value props | 3 cards: 🔒 Privado (STT/TTS locais), ⚡ Rápido (Whisper int8 + Edge TTS), 🎛️ Seu (você controla tudo) |
| CTA | "Começar" → step 2 |
| Skip | "Pular configuração" → step 5 (com defaults) |

### 5.2 Passo 2 — Permissão Microfone
| Elemento | Comportamento |
|----------|---------------|
| Ícone animado | 🎙️ pulsa enquanto aguarda |
| Lista permissões | 1. Microfone (obrigatório) — status pending/granted/denied<br>2. Notificações (opcional) — status |
| Botão principal | "Permitir microfone" → `navigator.mediaDevices.getUserMedia({audio: true})` |
| Botão secundário | "Agora não" → step 3 (sem mic, mas avisa que voz não funciona) |
| Copy | "O Hermes usa `faster-whisper` local — seu áudio nunca sai do dispositivo." |
| Auto-advance | Se concede permissão → 500ms → step 3 |

### 5.3 Passo 3 — Seleção Voz TTS
| Elemento | Comportamento |
|----------|---------------|
| Radio cards | 5 vozes (3 pt-BR + 2 en-US), avatar com inicial, nome, lang, gender |
| Seleção | Click no card → destaca (borda laranja + bg sutil) → habilita "Continuar" |
| Preview | Botão "Ouvir" no card → `POST /api/onboarding/preview-tts` → toca MP3 |
| Default | `pt-BR-AntonioNeural` pré-selecionado |
| Copy | "Tecnologia: `edge-tts` (Microsoft) — roda localmente via bridge." |

### 5.4 Passo 4 — Teste Microfone
| Elemento | Comportamento |
|----------|---------------|
| Visualizador | 16 barras animadas (frequency data via Web Audio API) |
| Botão central | 96px círculo, laranja → vermelho pulsante ao gravar |
| Gravação | `MediaRecorder` webm/opus, max 5s, min 500ms |
| Feedback | "Gravando... fale agora" → "Processando..." → resultado |
| Sucesso | ✅ Texto transcrito + "Teste OK" → habilita "Finalizar" |
| Erro | ❌ "Não detectamos fala / erro STT" → pode tentar de novo |
| Skip | "Pular teste" → step 5 (marca `micTested=false`) |

### 5.5 Passo 5 — Conclusão
| Elemento | Comportamento |
|----------|---------------|
| Ícone | ✓ verde animado (pop-in) |
| Headline | "Pronto!" |
| Subtitle | "Sua voz, sua sessão, seu Hermes." |
| Resumo | 3 highlights animados: Microfone ✓/—, Voz TTS [nome], Teste áudio ✓/— |
| CTA | "Abrir Hermes" → `window.location.href = 'index.html'` (ou abre app Tauri) |
| Persistência | Salva `onboardingComplete=true` + settings no `localStorage` / Tauri store |

---

## 6. Componentes e Estados

### 6.1 Componentes Reutilizáveis
| Componente | Props | Estados |
|------------|-------|---------|
| `StepHeader` | `number`, `total`, `title`, `subtitle` | — |
| `VoiceCard` | `voice`, `selected`, `onSelect`, `onPreview` | `hover`, `selected`, `previewing` |
| `MicVisualizer` | `analyserNode` | `idle`, `recording`, `processing` |
| `PermissionRow` | `icon`, `title`, `desc`, `status` | `pending`, `granted`, `denied` |
| `ProgressBar` | `step`, `total` | animated width |
| `CompletionSummary` | `micGranted`, `voiceId`, `micTested` | — |

### 6.2 Estados Globais
| Estado | Variável | Persistência |
|--------|----------|--------------|
| Step atual | `currentStep` (1-5) | Memória (reset se reload no meio) |
| Voz selecionada | `selectedVoiceId` | `localStorage` no final |
| Mic concedido | `micGranted` (bool) | `localStorage` no final |
| Mic testado | `micTested` (bool) | `localStorage` no final |
| Onboarding completo | `onboardingComplete` (bool) | `localStorage` + Tauri store |

---

## 7. Integrações

### 7.1 Bridge (`bridge-server.py`)
```python
# Novos endpoints em application()
if path == "/api/onboarding/voices" and method == "GET":
    return list_voices()

if path == "/api/onboarding/preview-tts" and method == "POST":
    return preview_tts(body["voice"], body.get("text"))

if path == "/api/onboarding/test-mic" and method == "POST":
    return test_mic(audio_file, body.get("language", "pt"))
```

### 7.2 Tauri (`src-tauri/src/main.rs`)
```rust
#[tauri::command]
async fn onboarding_complete(settings: OnboardingSettings) -> Result<(), String> {
    // Salva no tauri-plugin-store
    store.set("onboarding", json!(settings))?;
    Ok(())
}

#[tauri::command]
async fn onboarding_status() -> Result<OnboardingStatus, String> {
    // Lê do store, retorna { complete: bool, version: int }
}
```

### 7.3 Web (`web/onboarding.js` ou inline)
```javascript
const Onboarding = {
  async init() {
    const saved = JSON.parse(localStorage.getItem('hermes-voice-onboarding') || '{}');
    if (saved.onboardingComplete) return window.location.href = 'index.html';
    this.renderStep(1);
  },
  async saveAndComplete() { ... }
}
```

---

## 8. Segurança e Privacidade

| Aspecto | Tratamento |
|---------|------------|
| **Áudio no teste** | Processado local (bridge), não salvo, não logado |
| **Permissão mic** | Solicitada via `getUserMedia` — usuário vê prompt nativo do browser/OS |
| **Dados persistidos** | Só `voiceId` + flags boolean — zero PII |
| **TLS/HTTPS** | Dev: `http://localhost` (permite mic). Prod: Tauri usa `https://tauri.localhost` ou scheme custom |
| **CORS** | Bridge já libera `Access-Control-Allow-Origin: *` |

---

## 9. Plano de Deploy

| Etapa | Comando | Validação |
|-------|---------|-----------|
| Dev | Abre `web/onboarding.html` | Fluxo 1→5 completa, flag salva, redireciona pra `index.html` |
| Test | `pytest tests/ -k onboarding` | 5 testes: step nav, mic perm, voice select, mic test, persistence |
| Tauri Dev | `npm run tauri dev` | Onboarding roda no WebView, Tauri store salva |
| Build | `npm run build` | `.deb` inclui onboarding, primeira execução mostra fluxo |

---

## 10. Critérios de Pronto (Definition of Done)

- [ ] `GET /api/onboarding/voices` retorna 5+ vozes
- [ ] `POST /api/onboarding/preview-tts` gera MP3 válido (sync word FF E0/FB)
- [ ] `POST /api/onboarding/test-mic` transcreve áudio webm/opus → texto
- [ ] Step 1: copy correto, CTA navega, skip pula pro fim
- [ ] Step 2: `getUserMedia` chamado, status visual atualiza, auto-advance se granted
- [ ] Step 3: 5 vozes renderizadas, radio selection funciona, preview toca áudio
- [ ] Step 4: visualizador anima, gravação webm/opus, envio bridge, resultado mostrado
- [ ] Step 5: resumo correto, flag `onboardingComplete=true` salva no localStorage
- [ ] Recarregando página após completo → redireciona pra `index.html` (não mostra onboarding)
- [ ] Tauri: `tauri-plugin-store` persiste entre execuções
- [ ] Testes pytest: `test_onboarding_voices`, `test_onboarding_preview_tts`, `test_onboarding_test_mic`, `test_onboarding_persistence`, `test_onboarding_skip_flow`
- [ ] Testes Playwright: fluxo completo headless + visual
- [ ] Acessível: ARIA labels, roles, focus order, keyboard nav (Enter/Space no mic btn), contraste WCAG AA
- [ ] `prefers-reduced-motion` respeitado (animações desligadas)
- [ ] Responsivo: 320px-1440px testado
- [ ] Copy revisado (português natural, sem jargão desnecessário)

---

## 11. Pendências / Decisões Futuras

1. **Voz customizada (upload)?** Fora de escopo v0.1
2. **Múltiplos idiomas no onboarding?** Detectar `navigator.language` → pré-selecionar voz (v0.2)
3. **Pular steps individuais vs pular tudo?** Atualmente "Pular configuração" pula tudo. Manter simples.
4. **Onboarding re-executável?** Menu "Reconfigurar" no Settings → limpa flag + reabre (v0.2)
5. **Analytics/telemetria?** Zero — local-first. Não adicionar.