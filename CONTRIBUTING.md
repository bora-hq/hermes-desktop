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

## Arquitetura em uma frase

Frontend web (ou Tauri) → Bridge WSGI thread-safe → `hermes chat` CLI → modelo.

Mudanças que cruzam múltiplas camadas (ex: novo campo na resposta do Hermes) devem atualizar os 3 lugares: `bridge-server.py`, `src-tauri/src/main.rs`, `web/index.html`.
