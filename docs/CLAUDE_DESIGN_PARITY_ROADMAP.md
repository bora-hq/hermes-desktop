# Hermes Voice — Claude Design Parity Roadmap

**Objetivo**: Trazer features equivalentes ao Claude Design para o Hermes Voice Desktop, com implementação paralela orquestrada e revisão de segurança integrada.

---

## 1. Análise Comparativa: Claude Design vs Hermes Voice Atual

| Capacidade | Claude Design | Hermes Voice (atual) | Gap |
|------------|---------------|----------------------|-----|
| **Design System Ingestion** | CSS/Figma/Repo → tokens, componentes, layouts | ❌ Nenhum | Crítico |
| **Live Prototyping** | Canvas interativo, preview em tempo real | Chat + voz apenas | Crítico |
| **Inline Editing** | Clique no elemento → edita props/texto | ❌ | Crítico |
| **Voice Commands no Canvas** | "Torne o botão maior", "Mude para dark mode" | STT/TTS só no chat | Alto |
| **Multi-user Collaboration** | Comentários, edição conjunta, versionamento | Single-user local | Médio |
| **Export/Handoff** | HTML, PPTX, Canva, Figma, Code bundles | Apenas web single-file | Alto |
| **Design System Enforcement** | Outputs respeitam tokens automaticamente | Hardcoded no CSS | Médio |
| **Claude Code Handoff** | Bundle estruturado para agente codar | ❌ | Médio |
| **Templates Library** | Landing, dashboard, deck, admin, 3D | 3 protótipos estáticos | Baixo |

---

## 2. Arquitetura de 4 Pilares (Paralelos)

```
┌─────────────────────────────────────────────────────────────────┐
│                    HERMES DESIGN ENGINE                         │
├──────────────┬──────────────┬──────────────┬──────────────────┤
│  DESIGN      │  PROTOTYPING │ COLLABORATION│  EXPORT/HANDOFF  │
│  SYSTEM      │  CANVAS      │  LAYER       │  PIPELINE        │
├──────────────┼──────────────┼──────────────┼──────────────────┤
│ • Token Ext  │ • Live iframe│ • CRDT/Yjs   │ • HTML bundle    │
│ • Comp.Reg.  │ • Voice CMDS │ • Comments   │ • PPTX generator │
│ • Theme eng  │ • Inline edit│ • Presence   │ • Figma plugin   │
│ • CSS ingest │ • Snapshot   │ • Versioning │ • Code bundle    │
└──────────────┴──────────────┴──────────────┴──────────────────┘
                              │
                    ┌─────────┴─────────┐
                    │  SECURITY LAYER   │
                    │ • CSP strict      │
                    │ • Sandbox iframe  │
                    │ • AuthZ per-proj  │
                    │ • Audit log       │
                    └───────────────────┘
```

---

## 3. Pilar 1: Design System Engine

### 3.1 Token Extraction (`design-system/extractor.py`)
```python
# Input: CSS file(s), Figma JSON, Tailwind config, repo path
# Output: design-tokens.json (spec v1)
{
  "colors": {"primary": "#ff8c00", "bg": "#030712", ...},
  "spacing": {"xs": "4px", "sm": "8px", ...},
  "typography": {"fontFamily": "Inter", "scale": {...}},
  "borderRadius": {"sm": "4px", "md": "8px", ...},
  "shadows": {"sm": "...", "lg": "..."},
  "components": {
    "button": {"variants": ["primary", "ghost", "danger"], "sizes": ["sm", "md", "lg"]},
    "card": {...},
    "input": {...}
  }
}
```

### 3.2 Component Registry (`design-system/registry.py`)
- Classe `ComponentRegistry` com CRUD
- Schema JSON Schema por componente
- Versionamento semântico (v1.0.0, v1.1.0)
- Validação: `registry.validate(component_def)`

### 3.3 Theme Engine (`design-system/theme.py`)
- `ThemeEngine(tokens).generate_css()` → CSS custom properties
- `ThemeEngine.apply(component_def, variant)` → computed styles
- Dark/light automático via `prefers-color-scheme` + override

### 3.4 CSS Ingestion Pipeline
```
Input (CSS/Tailwind/Figma) 
    → Parser (postcss/tinycss2) 
    → Token Normalizer 
    → Component Detector (heurísticas: .btn, .card, .input) 
    → Registry Populator 
    → design-tokens.json + component-registry.json
```

### 3.5 Endpoints Bridge
```
GET  /api/design-system/tokens
POST /api/design-system/ingest (multipart: css/files)
GET  /api/design-system/registry
POST /api/design-system/registry (create component)
PATCH /api/design-system/registry/:id
GET  /api/design-system/theme.css (generated CSS vars)
```

---

## 4. Pilar 2: Prototyping Canvas

### 4.1 Arquitetura Frontend (`web/design-canvas.js`)
```javascript
class DesignCanvas {
  constructor() {
    this.iframe = document.createElement('iframe');
    this.iframe.sandbox = 'allow-scripts allow-same-origin allow-forms';
    this.iframe.srcdoc = this.generateHTML();
    this.messagePort = null; // MessageChannel para comunicação segura
  }

  generateHTML() {
    // Injeta design-tokens.css + component library + user prototype
  }

  // Voice commands via bridge STT
  async handleVoiceCommand(transcript) {
    const intent = await this.parseIntent(transcript); // LLM local ou bridge
    this.executeIntent(intent);
  }

  // Inline editing
  enableInlineEdit(element) {
    // Overlay com props editor (similar a Figma/Chrome DevTools)
  }
}
```

### 4.2 Voice Command Parser (`bridge-server.py` → `/api/design/parse-intent`)
```python
INTENTS = {
    "style": r"(mude|troque|defina).*(cor|fundo|fonte|tamanho|espaçamento)",
    "layout": r"(adicione|remova|mova).*(coluna|linha|grid|flex)",
    "component": r"(crie|adicione|insira).*(botão|card|input|tabela|gráfico)",
    "theme": r"(modo escuro|dark mode|tema claro|light mode)",
    "export": r"(exporte|gere|salve).*(html|pptx|figma|código)"
}
```

### 4.3 Live Preview Features
- **Hot reload** via `postMessage` do iframe → parent
- **Snapshot/History**: `canvas.saveSnapshot(label)` → IndexedDB
- **Device toolbar**: mobile/tablet/desktop breakpoints
- **Console bridge**: erros do iframe → toast no Hermes

### 4.4 Endpoints Bridge
```
POST /api/design/parse-intent     # NL → structured intent
POST /api/design/preview          # Gera HTML do preview
POST /api/design/snapshot         # Salva snapshot
GET  /api/design/snapshots        # Lista snapshots
POST /api/design/snapshot/:id/restore
```

---

## 5. Pilar 3: Collaboration Layer

### 5.1 Tech Stack: Yjs + WebRTC (local-first, P2P)
```javascript
// y-websocket para signaling (opcional, pode ser bridge)
import * as Y from 'yjs';
import { WebrtcProvider } from 'y-webrtc';

const ydoc = new Y.Doc();
const yPrototype = ydoc.getMap('prototype'); // Estado do canvas
const yComments = ydoc.getArray('comments'); // Comentários threadados
const yPresence = ydoc.getMap('presence');   // Cursors, seleções

const provider = new WebrtcProvider('hermes-design-room', ydoc);
```

### 5.2 Data Model
```
Project (doc)
├── prototype (Y.Map)      # Estado serializado do canvas
├── comments (Y.Array)     # [{id, author, pos, text, resolved, createdAt}]
├── presence (Y.Map)       # {userId: {cursor, selection, color, name}}
├── versions (Y.Array)     # Snapshots nomeados com timestamp
└── settings (Y.Map)       # Permissões: {view: [], edit: [], admin: []}
```

### 5.3 Bridge Integration (Signaling + Persistence)
```
GET  /api/collab/projects
POST /api/collab/projects
GET  /api/collab/projects/:id/ydoc-state  # Initial sync
POST /api/collab/projects/:id/snapshot    # Persist named version
WS   /api/collab/ws/:projectId            # y-websocket signaling (opcional)
```

### 5.4 AuthZ (Security Review Required)
- Project owner → admin
- Invite links com scopes: `view`, `comment`, `edit`, `admin`
- Revogação instantânea via Yjs awareness
- Audit log imutável (append-only) em SQLite

---

## 6. Pilar 4: Export/Handoff Pipeline

### 6.1 Export Formats
| Formato | Uso | Implementação |
|---------|-----|---------------|
| **HTML Bundle** | Deploy direto, Netlify/Vercel | `index.html` + `tokens.css` + `components.js` + `assets/` |
| **PPTX** | Pitch decks | `python-pptx` + template mapping |
| **Figma Plugin** | Handoff para designers | Figma REST API + node generation |
| **Code Bundle** | Claude Code / Cursor / agente | `.hermes-design/` com spec + componentes React/Vue/Svelte |
| **PDF** | Aprovação stakeholder | Puppeteer/Playwright headless |

### 6.2 Code Bundle Structure (Claude Code Handoff)
```
.hermes-design/
├── design-tokens.json
├── component-registry.json
├── prototype-spec.json      # Estado do canvas
├── components/
│   ├── Button/Button.jsx
│   ├── Card/Card.jsx
│   └── ...
├── pages/
│   └── Dashboard.jsx
├── package.json             # Deps: tailwind, @radix-ui, etc.
├── tailwind.config.js       # Tokens mapeados
└── README.md                # Instruções para o agente
```

### 6.3 Endpoints Bridge
```
POST /api/design/export/html
POST /api/design/export/pptx
POST /api/design/export/figma    # Requer Figma token
POST /api/design/export/code-bundle
GET  /api/design/export/:id/download
```

---

## 7. Security Review Checklist (Obrigatório por Pilar)

### 7.1 Design System Engine
- [ ] **Input validation**: CSS parser não executa código (postcss safe mode)
- [ ] **Path traversal**: `ingest` sanitiza paths, só aceita upload multipart
- [ ] **Token injection**: Generated CSS vars prefixadas (`--hds-`) para evitar colisão
- [ ] **Size limits**: Max 5MB por upload, max 100 tokens, max 50 componentes

### 7.2 Prototyping Canvas
- [ ] **Iframe sandbox**: `allow-scripts allow-same-origin allow-forms` — **NÃO** `allow-top-navigation`, `allow-popups`, `allow-pointer-lock`
- [ ] **CSP strict**: `default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src data: blob:; connect-src 'self' http://127.0.0.1:8420; frame-ancestors 'self'`
- [ ] **MessageChannel only**: Comunicação parent↔iframe só via `MessageChannel`, não `postMessage("*")`
- [ ] **Voice command allowlist**: Intents permitidos em enum, rejeita fallback "execute JS"
- [ ] **Snapshot sanitization**: Strip `<script>`, `onclick`, `javascript:` URLs antes de salvar

### 7.3 Collaboration Layer
- [ ] **AuthZ per-project**: Middleware valida `user_id` ∈ project.members[scope]
- [ ] **Yjs auth**: `y-websocket` com token JWT no handshake
- [ ] **Rate limiting**: Max 100 ops/sec por usuário, max 10 projetos/usuário
- [ ] **Audit log**: Todos os writes em `collab_audit.log` (append-only, hash-chained)
- [ ] **Data retention**: Auto-purge projetos inativos > 90 dias (configurável)

### 7.4 Export/Handoff
- [ ] **No secrets in bundles**: Scan para `api_key`, `secret`, `password` antes de exportar
- [ ] **Figma OAuth**: Token armazenado criptografado (age/rage), nunca em log
- [ ] **Code bundle sandbox**: Geração em temp dir isolado, cleanup imediato
- [ ] **Puppeteer headless**: `--no-sandbox --disable-setuid-sandbox` apenas em container dedicado

### 7.5 Cross-Cutting
- [ ] **CSP Header** em todas as respostas bridge
- [ ] **HSTS** se HTTPS (Tauri prod usa `tauri://` scheme)
- [ ] **CSRF tokens** em mutações POST/PATCH/DELETE
- [ ] **Input sanitization** centralizada (`bleach` para HTML, `pydantic` para JSON)
- [ ] **Dependency scanning**: `cargo audit` + `pip-audit` + `npm audit` no CI
- [ ] **Secrets detection**: `gitleaks` no pre-commit + CI

---

## 8. Cronograma Paralelo (8 Semanas)

| Semana | Design System | Prototyping | Collaboration | Export/Handoff | Security |
|--------|---------------|-------------|---------------|----------------|----------|
| 1-2    | Extractor + Registry + Tokens API | Iframe sandbox + MessageChannel | Yjs setup + WebRTC signaling | HTML bundle generator | CSP, sandbox, authZ design |
| 3-4    | Theme Engine + CSS Ingestion | Voice intent parser + Inline edit | Comments + Presence + Versioning | PPTX + Code bundle | Input validation, rate limits |
| 5-6    | Component Detector + Figma import | Snapshots + Device toolbar | Invite links + Permissions | Figma plugin + PDF | Audit log, secrets scan |
| 7-8    | Polish + Tests + Docs | Polish + Voice UX | Persistence + Cleanup | Bundle templates + CI | Full pentest, dependency audit |

---

## 9. Integração no Ecossistema Existente

### 9.1 Bridge Server (`bridge-server.py`)
- Novos módulos: `design_system.py`, `design_canvas.py`, `collab.py`, `export.py`
- Rotas sob `/api/design/*` e `/api/collab/*`
- Thread-safe, reusa `ThreadingMixIn`

### 9.2 Frontend (`web/index.html` → `web/design.html` ou modal)
- Nova aba "Design" na sidebar (ao lado de Sessões)
- Lazy-load: `import('./design-canvas.js')` só quando abre
- Reusa `SessionDB` (IndexedDB) para snapshots locais

### 9.3 Tauri Commands (`src-tauri/src/main.rs`)
```rust
#[command]
async fn design_ingest_css(css: String) -> Result<DesignTokens, String>

#[command]
async fn design_export_bundle(project_id: String, format: ExportFormat) -> Result<Vec<u8>, String>

#[command]
async fn collab_join_project(project_id: String, role: String) -> Result<CollabSession, String>
```

### 9.4 CI/CD (`.github/workflows/`)
- `design-system-tests.yml`: pytest para extractor, registry, theme
- `canvas-e2e.yml`: Playwright testa iframe sandbox, voice commands
- `collab-load.yml`: k6/Yjs stress test 50 users concurrentes
- `export-smoke.yml`: Gera todos formatos, valida schema
- `security-scan.yml`: `cargo audit`, `pip-audit`, `npm audit`, `gitleaks`, `trivy`

---

## 10. Definição de Pronto (DoD) por Pilar

| Pilar | Critérios |
|-------|-----------|
| **Design System** | Ingest CSS → tokens + registry em <2s; Theme engine gera CSS vars válidas; 90%+ coverage testes |
| **Prototyping** | Iframe carrega <500ms; Voice command → visual change <1s; Inline edit persiste; CSP passa `csp-evaluator` |
| **Collaboration** | 5 usuários editam simultaneamente sem conflito; Comentários threadados; Offline-first (Yjs) |
| **Export** | HTML bundle roda `npx serve` sem erro; PPTX abre no PowerPoint; Figma plugin cria frames; Code bundle `npm install && npm run dev` |
| **Security** | Zero findings críticos/altos no `trivy` + `gitleaks` + `csp-evaluator`; Audit log imutável verificado |

---

## 11. Próximos Passos Imediatos

1. **Criar estrutura de pastas** no `hermes-frontend/`:
   ```
   design-system/
   design-canvas/
   collab/
   export/
   ```

2. **Implementar `design-system/extractor.py`** (MVP: Tailwind + CSS custom props)

3. **Adicionar endpoints `/api/design/*`** no `bridge-server.py`

4. **Criar `web/design-canvas.js`** com iframe sandbox + MessageChannel

5. **Configurar `y-websocket`** no bridge (ou usar `y-webrtc` puro P2P)

6. **Rodar security baseline** antes de qualquer código novo

---

> **Nota**: Esta implementação mantém a filosofia Hermes — **local-first, offline-capable, single-binary desktop**. Nada exige cloud obrigatório. Colaboração é P2P (WebRTC) com signaling opcional via bridge.