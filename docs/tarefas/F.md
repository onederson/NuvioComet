# Tarefa F (20/11/2026): baixar qualquer conteúdo localmente no A71 + subir no TorBox + preferência local no Nuvio

Pedido: "levando em consideração essa ideia, quero que no bot do telegram eu consiga botar pra baixar qualquer conteudo pra assistir depois, exemplo. Baixar todas as temporadas de 2 broke girls dublado, dai o bot vai baixar tudo localmente no a71 e vai subir tambem no torbox, dai a preferencia vai ser pegar localmente e usar, e ele vai pegar das proprias fontes que tem no no cometa71, se der pra pegar de fora também ótimo, mas se nao pode pegar das que tem no cometa71"

## Diagnóstico e solução
- O Galaxy A71 tem **100 GB livres** em `/data`.
- Medição ao vivo revelou que o TorBox bloqueia WebSeeds em porta não-padrão (`:7443`), mas **aceita imediatamente (3–6 s)** via túnel HTTPS porta 443 (`cloudflared tunnel --url http://127.0.0.1:8000` → `https://<hash>.trycloudflare.com/archive-seed/<token>/<arquivo>`), sem limite de tamanho e sem depender de servidor terceiro.
- Unificamos `direct_archive.py` (arquivamento automático no TorBox) + `offline_library.py` (biblioteca local no A71 pedida pelo Telegram):
  1. **`comet/services/direct_archive.py`**:
     - Túnel `cloudflared` sob demanda (reaproveitado na fila e fechado após ociosidade) para servir arquivos do A71 ao TorBox na porta 443.
     - Função pública `upload_local_file_to_torbox(...)` para filmes e séries (tenta FenixFlix direto → `cloudflared` porta 443 → Catbox se <= 195 MB), gravando no SQLite (`torrents` + `debrid_availability`) e mantendo keepalive a cada 18 dias.
  2. **`comet/services/offline_library.py`** (implementado via Maestro):
     - Salva vídeos em `/opt/comet/data/offline/{movies|series}/...` com índice em `data/offline_library.json` e proteção de disco (`OFFLINE_MIN_FREE_GB=15`).
     - Busca fontes usando o próprio Comet A71 (`GET http://127.0.0.1:8000/{token}/stream/{type}/{id}.json?nolocal=1`), aproveitando todos os scrapers, filtros de homônimos/trailer/outro corte, TorBox, RD, FenixFlix e MegaEmbed.
     - Baixa localmente (MP4/MKV direto via HTTP streaming com `ffprobe` + cálculo SHA-1 em paralelo, ou HLS remuxado com `ffmpeg -c copy`).
     - Sobe também no TorBox se ainda não houver cópia PT-BR HD em cache lá.
     - Serve o arquivo local na rota autenticada `/offline/{token}/{media_id}/{filename}` (com HTTP 206 `Range` nativo para seek instantâneo).
     - Comandos no Telegram: `/baixar` (e texto natural `"baixar ..."`), `/baixados`, `/cancelar`, `/apagar`, com notificações de início, progresso por temporada e conclusão.
  3. **Ganchos pequenos:**
     - `comet/api/app.py`: registrar `offline_library.router` e `offline_library.start_service`.
     - `comet/api/endpoints/stream.py`: se existir arquivo local no disco (e não for `?nolocal=1`), retornar imediatamente (`< 0,1 s`) com **`▶ Assistir` · `💾 Local no A71`** no topo (suportando autoplay instantâneo no Nuvio).
     - `comet/services/telegram_bot.py`: registrar `comet.services.offline_library` em `_PLUGINS` e encaminhar frases começando com `baixar`/`baixa` para os plugins.

## Verificação
1. Testes unitários locais (`scripts/test_direct_archive.py` e `scripts/test_offline_library.py`).
2. `py_compile`, deploy no A71, reinício do Comet e `.\scripts\regress.ps1` (incluindo `unit_direct_archive` e `unit_offline_library`).
3. Teste ao vivo no *2 Broke Girls* T5E01: baixar localmente no A71 + subir no TorBox via `cloudflared` + confirmar `/stream/series/tt1845307:5:1.json` retornando `▶ Assistir` local em `< 0,1 s` com HTTP 206 `Range` funcionando + confirmar cópia em cache no TorBox ⚡.
4. Regerar `comet-patches/ptbr-first.patch` e remover `data/smoke.disable`.
