# Tarefa E (20/11/2026): garantir episódios dublados no TorBox (2 Broke Girls e séries vigiadas)

Pedido: "eu quero garanti que os episodios de 2 broke girls seja baixado e subido no torbox ou realdebrid quando eles aparecerem na lista, as vezes aparece pelo fenixflix mas as vezes o fenixflix cai, dai quero garantir que vai ta lá pra eu assistir sem me preocupar se caiu ou nao"

## Diagnóstico (medido)
- `2 Broke Girls` (`tt1845307`):
  - T1–T4 já têm pacotes dublados/dual em cache no TorBox.
  - T5 (22 episódios): só 3 episódios em cache no TorBox (5x10 em 480p, 5x11 e 5x14 em 720p). Os outros 19 só aparecem via link direto (FenixFlix / MegaEmbed).
  - T6 (22 episódios): 0 episódios dublados em cache no TorBox. Só link direto.
- Os links diretos do `direct_memory.json` expiram (token vence / conexão resetada / 403 / 404) e o FenixFlix cai com frequência.
- Real-Debrid bloqueia `2 Broke Girls` com HTTP 451 (`infringing_file`); conforme regra do projeto, não contornamos 451 e usamos o **TorBox** (principal).
- **Mecanismo verificado ao vivo:**
  - TorBox `webdl/createwebdownload` recusa IPs diretos (`UNSUPPORTED_SITE`).
  - **TorBox `torrents/createtorrent` aceita `.torrent` com BEP-19 WebSeed (`url-list`)** e baixa direto da URL do FenixFlix (~250 MB em <10 s) para o cache do TorBox (`nexus-*.latm.tb-cdn.cx`), onde o StremThru/Comet reconhece e toca nativamente como torrent dublado pronto (`⚡ TorBox`, `▶ Assistir`).
  - Quando a URL direta falha para o TorBox (ex.: link travado por IP do MegaEmbed ou HLS remuxado), o A71 serve o arquivo temporário por uma rota autenticada de uso único (`/archive-seed/<token>/<arquivo>`) via `PUBLIC_BASE_URL` e apaga o temporário assim que o TorBox termina.

## O que vai mudar
1. **`comet/services/direct_archive.py` (novo):**
   - Lista de séries vigiadas + índice persistente em `data/direct_archive.json` (inicia com `tt1845307` — `2 Broke Girls`).
   - **Gatilho 1 (ao abrir na lista):** `maybe_enqueue(media_id, direct_streams, has_cached_pt_hd)` chamado de `stream.py` em 2º plano (sem atrasar a resposta). Se a série é vigiada, não tem torrent PT HD pronto no debrid e há link direto PT validado, coloca o episódio na fila.
   - **Gatilho 2 (varredura automática + manutenção):** `start_service()` (em `app.py`) varre periodicamente os episódios faltantes das séries vigiadas (pulando os que já têm torrent PT HD em cache no TorBox), sincroniza `data/direct_archive.json` com as tabelas `torrents` e `debrid_availability` do SQLite (impedindo expiração de cache) e faz keepalive leve (1 KB a cada 18 dias) antes do prazo de 30 dias de inatividade do TorBox.
   - **Pipeline por episódio (1 por vez, respeitando slots livres do TorBox):** baixa para `/tmp`, calcula peças SHA-1 (512 KB), valida vídeo/áudio/duração com `ffprobe` (rejeita arquivo corrompido ou outro corte), cria `.torrent` com `url-list` (FenixFlix direto → fallback `/archive-seed/<token>/...`), sobe no TorBox, aguarda `download_present=True`, grava em `data/direct_archive.json` + SQLite (`torrents` e `debrid_availability`) e apaga o temporário.
   - **Telegram:** `/arquivar` (estado), `/arquivar <série ou tt...>` (adicionar), `/desarquivar <série ou tt...>` (parar de vigiar).
2. **Ganchos pequenos:**
   - `comet/api/app.py`: registrar `direct_archive.router` e `"comet.services.direct_archive"` em `start_service()`.
   - `comet/api/endpoints/stream.py`: chamar `maybe_enqueue(...)` em 2º plano quando `direct_streams` traz PT-BR.
   - `comet/services/telegram_bot.py`: adicionar `"comet.services.direct_archive"` em `_PLUGINS`.
3. **Testes:**
   - Unidade em `scripts/regress.py` (`unit_direct_archive`): bencode/info_hash determinístico, escolha do candidato MP4/FenixFlix, rejeição de outro corte, sincronia com estado JSON.
   - Verificação ao vivo: `py_compile`, reinício do Comet, `.\scripts\regress.ps1`, teste de `/stream/series/tt1845307:5:1.json` retornando `▶ Assistir` via TorBox ⚡ mesmo sem FenixFlix, e início da varredura real dos episódios de `2 Broke Girls` (T5 e T6).
