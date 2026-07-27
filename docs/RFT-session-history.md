# RFT — Session History Sidebar

> **Task:** `t_8e92aa34` | **Parent:** `t_16d26433` | **Status:** Em escrita
> **Depende de:** `t_ff408e24` (Design aprovado) ✅

---

## 1. Visão e Escopo

### 1.1 O que é
Sidebar lateral (desktop) / drawer (mobile) que mostra histórico de sessões do Hermes Voice. Permite buscar, retomar, excluir e criar novas sessões. Persistido no bridge (SQLite) e sincronizado no frontend (IndexedDB).

### 1.2 Para quem
- **P1 Dev Hermes** — retoma contextos de coding/debug anteriores
- **P2 Voice-First** — continua conversas de dias atrás
- **P3 Builder** — integra via bridge API para listar/retomar sessões

### 1.3 Anti-scope
- ❌ Não é gerenciador de arquivos de áudio (só MP3s via `/api/audio/<id>`)
- ❌ Não edita mensagens individuais (só retomar/excluir sessão inteira)
- ❌ Não exporta/importa sessões (v0.2)

---

## 2. Arquitetura

### 2.1 Stack
| Camada | Tech | Localização |
|--------|------|-------------|
| Frontend (Web) | Vanilla JS + IndexedDB | `web/index.html` → `SessionSidebar` component |
| Frontend (Tauri) | Mesmo código via WebView | `src-tauri/` |
| Bridge API | Python WSGI + SQLite | `bridge-server.py` |
| Persistência Bridge | SQLite (`sessions.db`) | `/tmp/hermes-sessions/` ou `~/.hermes/sessions.db` |
| Persistência Frontend | IndexedDB | `hermes-sessions` database |

### 2.2 Fluxo de Dados
```
App inicia
    ↓
Frontend carrega sessões do IndexedDB (instant)
    ↓
Frontend sincroniza com bridge GET /api/sessions (background)
    ↓
User interage (busca, retoma, exclui)
    ↓
Frontend otimista → sync com bridge (POST/DELETE)
    ↓
Bridge persiste no SQLite → retorna confirmado
    ↓
Frontend confirma / rollback se erro
```

### 2.3 Decisões Técnicas
| Decisão | Rationale |
|---------|-----------|
| SQLite no bridge | ACID, queries complexas (busca, ordenação), leve |
| IndexedDB no frontend | Offline-first, sync posterior, busca local instantânea |
| `session_id` = UUID v4 | Único, não sequencial, seguro para URL |
| Soft delete (`deleted_at`) | Permite undo, auditoria, sync conflitos |
| Bridge é source of truth | Frontend é cache; bridge resolve conflitos |

---

## 3. Modelo de Dados

### 3.1 Schema SQLite (`sessions` table)
```sql
CREATE TABLE sessions (
    id              TEXT PRIMARY KEY,           -- UUID v4
    title           TEXT NOT NULL DEFAULT '',   -- Auto-gerado da 1ª msg user
    preview         TEXT NOT NULL DEFAULT '',   -- Última msg (user ou agent) truncada
    message_count   INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,              -- ISO8601 UTC
    updated_at      TEXT NOT NULL,              -- ISO8601 UTC
    deleted_at      TEXT,                       -- NULL = ativo, ISO8601 = deletado
    meta            TEXT DEFAULT '{}'           -- JSON: {model, voice, ...}
);

CREATE INDEX idx_sessions_updated ON sessions(updated_at DESC);
CREATE INDEX idx_sessions_deleted ON sessions(deleted_at) WHERE deleted_at IS NULL;
```

### 3.2 Schema IndexedDB (frontend)
```javascript
// db: hermes-sessions, version: 1
// objectStore: sessions (keyPath: "id")
// indexes: "updated_at", "title", "deleted_at"
```

### 3.3 Session Object (API)
```json
{
  "id": "sid_550e8400-e29b-41d4-a716-446655440000",
  "title": "Refatoração do bridge-server.py",
  "preview": "Usuário: Preciso separar STT/TTS em módulos...",
  "messageCount": 12,
  "createdAt": "2026-07-26T14:30:00Z",
  "updatedAt": "2026-07-26T14:45:00Z",
  "deletedAt": null,
  "meta": { "model": "nemotron-3-ultra", "voice": "pt-BR-AntonioNeural" }
}
```

---

## 4. API Endpoints (Bridge)

### 4.1 `GET /api/sessions`
Lista sessões (paginado, filtrado, ordenado)

**Query params:**
| Param | Tipo | Default | Descrição |
|-------|------|---------|-----------|
| `limit` | int | 50 | Max resultados (1-200) |
| `offset` | int | 0 | Paginação |
| `search` | string | "" | Busca em title, preview, id |
| `include_deleted` | boolean | false | Inclui soft-deleted |
| `sort` | string | `-updated_at` | `updated_at`, `-updated_at`, `created_at`, `-created_at` |

**Response 200:**
```json
{
  "sessions": [Session, ...],
  "total": 42,
  "limit": 50,
  "offset": 0
}
```

### 4.2 `GET /api/sessions/<id>`
Retorna sessão completa (inclui mensagens)

**Response 200:**
```json
{
  "id": "sid_...",
  "title": "...",
  "messages": [
    {"role": "user", "content": "...", "timestamp": "...", "audio_url": null},
    {"role": "agent", "content": "...", "timestamp": "...", "audio_url": "/api/audio/abc123"}
  ],
  "meta": {...}
}
```

### 4.3 `POST /api/sessions`
Cria nova sessão vazia

**Request:** `{ "title": "Nova conversa" }` (opcional)

**Response 201:**
```json
{ "id": "sid_new_uuid", "title": "Nova conversa", "messageCount": 0, ... }
```

### 4.4 `PATCH /api/sessions/<id>`
Atualiza título / meta

**Request:** `{ "title": "Novo título" }`

### 4.5 `DELETE /api/sessions/<id>`
Soft delete (define `deleted_at = NOW()`)

**Response 200:** `{ "ok": true, "deletedAt": "2026-07-27T10:00:00Z" }`

### 4.6 `POST /api/sessions/<id>/restore`
Restaura sessão soft-deleted

**Response 200:** `{ "ok": true }`

### 4.7 `DELETE /api/sessions/<id>/purge`
Hard delete (remove do DB + áudios associados) — **admin only / confirm 2x**

---

## 5. Fluxos de UI

### 5.1 Estados da Sidebar
| Estado | Descrição |
|--------|-----------|
| **Carregando** | Spinner no header, lista vazia |
| **Vazia** | Ilustração + "Nenhuma sessão ainda" + btn "Nova sessão" |
| **Com resultados** | Lista scrollável, busca no topo |
| **Buscando** | Debounce 150ms, filtra local (IndexedDB) + sync remoto |
| **Excluindo** | Item com opacity 0.5, spinner no btn "Excluir" |
| **Erro sync** | Banner discreto "Sync falhou — retry" |

### 5.2 Interações
| Ação | Frontend (otimista) | Bridge | Rollback se erro |
|------|---------------------|--------|------------------|
| **Busca** | Filtra IndexedDB instantâneo | GET `/api/sessions?search=` (bg) | N/A |
| **Retomar** | Seta `currentSessionId`, carrega msgs IndexedDB | GET `/api/sessions/<id>` (valida) | Volta sessão anterior |
| **Nova sessão** | Cria local, navega | POST `/api/sessions` | Remove local |
| **Excluir** | Move para "lixeira" visual | DELETE `/api/sessions/<id>` | Restaura local |
| **Restaurar** | Volta para lista ativa | POST `/api/sessions/<id>/restore` | Mantém na lixeira |

### 5.3 Componentes
| Componente | Responsabilidade |
|------------|------------------|
| `SessionSidebar` | Shell: header, search, list, footer, mobile drawer |
| `SessionList` | Virtualized list (ou simples), render `SessionItem` |
| `SessionItem` | Card: título, preview, timestamp, meta, ações (retomar/excluir) |
| `SearchInput` | Debounce, clear btn, aria-label |
| `DeleteConfirmModal` | Modal acessível, foco no btn "Cancelar" |
| `EmptyState` | Ilustração + copy + CTA |

### 5.4 Responsivo
| Breakpoint | Comportamento |
|------------|---------------|
| **Desktop (≥769px)** | Sidebar fixa 380px, border-left, sempre visível |
| **Tablet (481-768px)** | Sidebar colapsável, btn ☰ no header abre drawer |
| **Mobile (≤480px)** | Drawer full-height, swipe para fechar, backdrop |

---

## 6. Integrações

### 6.1 Bridge Python (`bridge-server.py`)
```python
# Novo módulo: session_store.py
class SessionStore:
    def __init__(self, db_path: Path):
        self.db = sqlite3.connect(db_path)
        self._init_schema()
    
    def list_sessions(self, limit, offset, search, include_deleted, sort): ...
    def get_session(self, session_id): ...
    def create_session(self, title): ...
    def update_session(self, session_id, title): ...
    def delete_session(self, session_id): ...
    def restore_session(self, session_id): ...
    def purge_session(self, session_id): ...
```

### 6.2 Frontend IndexedDB (`web/session-db.js`)
```javascript
const SessionDB = {
  async init() { ... },
  async getAll() { ... },
  async search(query) { ... },
  async upsert(session) { ... },
  async delete(id) { ... },
  async syncFromBridge(sessions) { ... },  // merge strategy
}
```

### 6.3 Sync Strategy
```
Bridge = source of truth
Frontend = cache (IndexedDB)

On load:
  1. Render IndexedDB instantly
  2. Fetch bridge → merge (bridge wins on conflict)
  3. Save merged to IndexedDB

On mutation (optimistic):
  1. Update IndexedDB immediately
  2. POST/DELETE to bridge
  3. On success: confirm
  4. On error: rollback IndexedDB, show toast, retry btn
```

### 6.4 Tauri Commands (para store nativo opcional)
```rust
#[tauri::command]
async fn sessions_list(...) -> Result<Vec<Session>, String> { ... }
#[tauri::command]
async fn sessions_delete(id: String) -> Result<(), String> { ... }
```

---

## 7. Segurança e Transações

| Aspecto | Tratamento |
|---------|------------|
| **SQL Injection** | Prepared statements (sqlite3 parameterized) |
| **Path Traversal** | Session ID = UUID v4 (regex `^[a-f0-9-]+$`) |
| **Soft Delete** | `deleted_at` + index parcial → não vaza em listagens normais |
| **Áudio associado** | Purge remove MP3s de `/tmp/hermes-audio/<session_id>/` |
| **Concorrência** | SQLite WAL mode + `IMMEDIATE` transactions |

---

## 8. Plano de Deploy

| Etapa | Comando | Validação |
|-------|---------|-----------|
| Dev | `./scripts/dev.sh` | Sidebar carrega, busca, retoma, exclui |
| Test | `pytest tests/ -v -k session` | 8+ testes pass |
| Build | `npm run build` | `.deb` inclui `session_store.py` + migrations |
| Release | Tag `v0.1.0` | GitHub Actions builda 3 plataformas |

---

## 9. Critérios de Pronto (Definition of Done)

- [ ] `GET /api/sessions` com paginação, busca, sort
- [ ] `GET /api/sessions/<id>` retorna mensagens completas
- [ ] `POST /api/sessions` cria sessão vazia
- [ ] `PATCH /api/sessions/<id>` atualiza título
- [ ] `DELETE /api/sessions/<id>` soft delete
- [ ] `POST /api/sessions/<id>/restore` restaura
- [ ] SQLite schema + migração idempotente
- [ ] IndexedDB schema + init no load
- [ ] Sync strategy: load instant → background merge → confirm
- [ ] Otimistic UI: retomar, nova, excluir, restaurar
- [ ] Busca local (IndexedDB) + debounce + sync remoto
- [ ] Mobile drawer: abre/fecha, swipe, backdrop, focus trap
- [ ] Delete confirm modal acessível
- [ ] Empty state + loading + error states
- [ ] Testes pytest: `test_sessions_list`, `test_sessions_search`, `test_sessions_crud`, `test_sessions_soft_delete`, `test_sessions_restore`, `test_sessions_sync_conflict`
- [ ] Testes Playwright: desktop sidebar + mobile drawer
- [ ] Acessível: ARIA roles, keyboard nav, focus management, contraste

---

## 10. Pendências / Decisões Futuras

1. **Mensagens no bridge?** Atualmente mensagens só no frontend. Bridge poderia persistir para sync multi-device (v0.3)
2. **Busca full-text (FTS5)?** SQLite FTS5 para busca em mensagens (v0.2)
3. **Export/Import JSON?** Nice-to-have v0.2
4. **Tags/categorias?** Fora de escopo
5. **Auto-título via LLM?** Usar primeira msg user → summarizar (v0.2)