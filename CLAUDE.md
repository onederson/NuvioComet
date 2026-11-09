# NuvioComet: modo de trabalho

Este projeto é o **Comet do A71 servindo o app Nuvio** (NuvioTV na Fire TV). Desde 09/11/2026 o trabalho é **só Nuvio**:
- não existe mais código "só para o Stremio" nem chaves `is_nuvio_request` para decidir comportamento;
- o link pessoal antigo do Stremio continua respondendo, mas recebe o mesmo comportamento do Nuvio.

A versão do Stremio está congelada na pasta `..\STREMIO` (repo `onederson/CometToStremio`, tag `stremio-20261109`). Não mexa nela daqui. Para voltar ao Stremio, veja "Voltar ao Stremio" no `HANDOFF.md`.

## Como trabalhar
- **Claude implementa direto.** Quando a tarefa tiver partes independentes, pode abrir subagentes, desde que cada um mexa em arquivos diferentes do A71.
- Use primeiro o que o Nuvio já faz sozinho: enriquecimento TMDB, elenco, trailers, pular abertura, autoplay, escolha da faixa PT e Trakt. Não refaça isso no servidor.

## Fluxo
1. **Antes de mexer no Comet:** crie `data/smoke.disable` no A71. Sem ele, a restauração automática do smoke desfaz edições.
2. **Planeje** tarefas grandes em `docs/tarefas/<letra>.md`: o que foi pedido e como verificar.
3. **Edite** em cópia local, que vai para o A71 com `adb push` + `mv`. Os arquivos precisam estar em LF. Envie só `.py`, nunca `__pycache__`.
4. **Verifique**, nesta ordem:
   - `py_compile` / `compileall`;
   - reinicie o Comet e espere ~1 min de aquecimento;
   - rode `.\scripts\regress.ps1`;
   - confira na TV quando a mudança for visível.
5. **Regenere** o `comet-patches/ptbr-first.patch` a partir do A71 (comando no `README.md`).
6. **Ao terminar:** apague `data/smoke.disable`.
7. **Commit e push** só quando o usuário pedir ou ao fechar uma leva aprovada.
   - Branch `dev-nuvio` para trabalho em andamento; `main` para o que está aprovado.
   - O repositório é **público**: antes do push, confira que nenhum valor do `config/.env` aparece nos arquivos.

## Cuidados
- Dois processos mexendo no mesmo arquivo do A71 se atropelam.
- Fire TV: `adb connect 192.168.3.29:5555`. `uiautomator dump` lê a tela do Nuvio.
  - Em Ajustes, as seções abrem e fecham com OK.
  - Chaves aparecem como `checkable="true" checked=...`.
- Atualize o Nuvio sempre que sair versão nova (GitHub `NuvioMedia/NuvioTV`). Se der problema, volte à anterior.
