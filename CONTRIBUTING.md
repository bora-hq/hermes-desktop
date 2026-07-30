# Contributing

PRs welcome. Antes de abrir:

1. **Rode os testes localmente:**
   ```bash
   pip install pytest
   pytest tests/ -v
   cd src-tauri && cargo check
   ```

2. **Siga o estilo:**
   - Python: `ruff` (config em `pyproject.toml` quando existir)
   - Rust: `cargo fmt` + `cargo clippy`
   - JS/HTML: vanilla, sem framework, sem bundler

3. **Para mudanças no `bridge-server.py`:**
   - Adicione um teste em `tests/test_bridge.py` cobrindo o endpoint
   - Não remova o `ThreadingMixIn` — concorrência depende dele
   - Não relaxe a regex `_AUDIO_ID_RE` — path traversal mitigation

4. **Para mudanças no Tauri:**
   - Rode `npm run build` antes de subir (cobre `cargo build --release`)
   - Veja a skill `tauri-v2-gotchas` para armadilhas conhecidas

5. **Não commite:**
   - `bridge.log`, `frontend.log`
   - `node_modules/`, `src-tauri/target/`
   - tokens, `.env`, dumps

## Conventional Commits (obrigatório)

Este projeto usa [Conventional Commits](https://www.conventionalcommits.org/) para version bump automático via [release-drafter](https://github.com/release-drafter/release-drafter).

### Formato

```
<type>(<scope>): <subject>

<body>

<footer>
```

### Types (determinam version bump)

| Type | Bump | Descrição |
|------|------|-----------|
| `feat` | **MINOR** | Nova funcionalidade |
| `fix` | **PATCH** | Correção de bug |
| `docs` | **PATCH** | Documentação |
| `chore` | **PATCH** | Manutenção, build, deps |
| `refactor` | **PATCH** | Refatoração sem mudança de behavior |
| `perf` | **PATCH** | Performance |
| `security` | **PATCH** | Segurança |
| `breaking` / `major` | **MAJOR** | Breaking change (ex: `feat!:`, `fix!:`) |

### Scopes sugeridos

- `bridge` — bridge WSGI Python
- `tauri` — Tauri/Rust layer
- `web` — frontend vanilla HTML/JS
- `tauri-release` — workflow de release
- `ci` — GitHub Actions
- `docs` — documentação
- `test` — testes

### Exemplos

```bash
# Feature → MINOR
git commit -m "feat(bridge): add streaming response endpoint"

# Bug fix → PATCH
git commit -m "fix(tauri): fix tray icon on Wayland"

# Breaking change → MAJOR
git commit -m "feat!(web): drop IE11 support"

# Chore → PATCH
git commit -m "chore(ci): upgrade pytest to 8.x"
```

### Commitlint (validado no CI + hook local)

- CI: workflow `commitlint.yml` roda em todo PR/push
- Local: hook `commit-msg` via husky valida antes do commit

```bash
# Instala hooks localmente (uma vez)
npm run prepare
```

## Version Bump Automático

1. Commits em `main` com conventional commits → release-drafter atualiza draft release
2. Labels `feat`/`fix`/`breaking` determinam MINOR/PATCH/MAJOR
3. Maintainer publica o draft → tag `vX.Y.Z` + assets (`.deb`, etc.) gerados pelo `tauri-release.yml`

## Arquitetura em uma frase

Frontend web (ou Tauri) → Bridge WSGI thread-safe → `hermes chat` CLI → modelo.

Mudanças que cruzam múltiplas camadas (ex: novo campo na resposta do Hermes) devem atualizar os 3 lugares: `bridge-server.py`, `src-tauri/src/main.rs`, `web/index.html`.
