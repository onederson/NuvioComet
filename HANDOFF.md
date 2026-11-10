# HANDOFF: NuvioComet (Comet do A71 para o app Nuvio)

*Última atualização: 09/11/2026 (horário de Brasília)*

Este é o documento de passagem: regras, estado verificado e próximos passos.
- O histórico completo da fase Stremio (itens 1 a 35, medições, decisões) está em `docs/historico/HANDOFF-stremio.md`. Consulte quando precisar de detalhe antigo.
- O modo de trabalho está em `CLAUDE.md`.

---

## 1. Regras obrigatórias

### Segurança e restrições
- **Nunca imprima nem versione** segredos: `config/.env`, `config/duckdns.token`, chaves do RD, do TorBox e do Trakt, a API key do Prowlarr, os tokens dos links e a senha do backup.
  - O repositório `onederson/NuvioComet` é **público**.
  - Para gravar uma chave no `.env` do A71 sem imprimir o valor, use `scripts/env_set.sh`. Envie esse script em LF: com CRLF ele falha com "unmatched 'if'".
- **DuckDNS:** só o domínio `a71comet.duckdns.org`. A conta tem outro domínio; não mexa nele.
- **E-mail do git** (config local deste repo): `wandersonks02@gmail.com`.
- **AdGuard Home (Fase 1):** não mexa. Única exceção: o watchdog ajusta o `oom_score_adj` dele.
- **Bloqueio 451 do Real-Debrid:** não contorne. O que existe é legítimo: troca para outro torrent/TorBox e P2P rotulado.
- **Debrid:** não sugira outro debrid nem outra assinatura. O TorBox é o principal e o RD é a reserva.
- **Idioma:** respostas ao usuário em **PT-BR**, linguagem simples.

### Pegadinhas do ambiente (Windows)
- **Comandos no A71:** `.\scripts\a71.ps1 '<cmd>'` roda como root no Android; `.\scripts\a71.ps1 -Chroot '<cmd>'` roda dentro do Debian.
  - `git` só existe dentro do chroot.
  - Com o Bash do Windows, o `adb` converte caminhos `/data/...`; use o PowerShell para `adb push`/`pull`.
- **Comandos bloqueados:**
  - `rm /tmp...`: use `unlink`.
  - `Start-Sleep`: use laço com `[Threading.Thread]::Sleep`.
- **Envio de arquivos:** `adb push` um arquivo por vez, com o nome final completo. Tudo em LF (zero `\r`).

---

## 2. Arquitetura (resumo)

| Peça | Onde | Observação |
|---|---|---|
| Galaxy A71 (root) | `192.168.3.44`, ADB `:5555` | Debian 13 em chroot `/data/debian` |
| Comet (`g0ldyy/comet` @ `f56677f` + patch) | `/opt/comet` (chroot), porta 8000 | `comet-patches/ptbr-first.patch` = `git diff` de `/opt/comet` |
| Caddy + DuckDNS | `/etc/caddy/Caddyfile` | 443 (casa), **7443** (casa + 4G), 8443 (StremThru) |
| StremThru, Prowlarr, FlareSolverr | 8080, 9696, 8191 | debrid e indexadores |
| Watchdog | `/data/comet/watchdog.sh` (= `scripts/watchdog.sh`) | religa serviços, memória, smoke por hora, healthchecks |
| **Fire TV** com NuvioTV **1.1.0** | `192.168.3.29:5555` | modelo AFTMA08C15 (4K) |

- **Link do Nuvio:** `https://a71comet.duckdns.org:7443/<NUVIO_LINK_TOKEN>/manifest.json`. O token fica no `.env`.
  - Instalado na TV.
  - O Nuvio não troca addon de mesmo id: remova o antigo antes de instalar outro.
  - Para ver um manifesto novo, feche e abra o app. O botão "Atualizar addons" não basta.
- **Debrid:** TorBox ⚡ → RD ⚡ → baixar (`DEBRID_PRIMARY=torbox`).
- **Fontes:** Torrentio, MediaFusion (limite de 10 s), 2× Comet, Zilean, StremThru, Peerflix, SeaDex, Nyaa, Prowlarr, BRSites, addons (BeTor, BestCine) e link direto FenixFlix.

---

## 3. Como o servidor atende o Nuvio (estado em 09/11/2026)

### O que fica com o Nuvio (o servidor não faz)
- Sinopse e elenco com foto (enriquecimento TMDB do app, com "Arte" desligada para manter as nossas capas).
- Trailers, pular abertura (introdb), autoplay do próximo episódio e "Continuar assistindo".
- **Escolha da faixa PT:** a TV está em pt-BR, e o Nuvio escolhe o português mesmo quando o arquivo começa em inglês. Por isso a lista mostra `🔊 PT · EN` sem aviso, e PT fora da 1ª faixa não perde ponto no ▶ Assistir.
- **Fundo da página do título:** imagem original. O Nuvio desenha logo, notas e elenco por cima.

### O que o servidor faz
- **▶ Assistir** no topo da lista. O Nuvio está configurado para tocar sozinho a fonte cujo nome tem "Assistir" (regex, espera de 3 s), então o play abre direto.
  - **Ordem:** 4K PT > 1080p PT > 720p PT > link direto PT > melhor original (4K primeiro). Limite de 30 GB.
  - **Fora de casa:** a versão leve vem primeiro, pelo bitrate (`REMOTE_MAX_MBPS`, padrão 12).
  - **Novo (09/11):** um torrent SD/480p dublado só vira ▶ Assistir se não houver nada pronto em HD. Costuma ser outra versão da obra (Hunter x Hunter 1999 × 2011). Ele continua na lista.
  - **Fonte que falhou perde a vez (09/11, `failed_sources.py`):** se você dá play numa fonte e, em até 60 s, em outra do mesmo título, a 1ª fica marcada para aquele título por 30 dias (`data/failed_sources.json`). O ▶ Assistir pula a marcada enquanto houver alternativa dublada, inclusive link direto. Tocar de novo a fonte marcada tira a marca. A URL de play leva `&ep_key=` (ID exato com temporada:episódio).
- **Busca progressiva (09/11, `orchestration._scrape_progressive`):** na 1ª busca de um título, a resposta sai com o que chegou em 6 s, se já houver resultado em PT. Sem PT, espera todas as fontes, como antes. O que chega depois (Prowlarr, que leva de 5 a 25 s, e MediaFusion) é filtrado à parte e vai para o cache, então a próxima abertura já vem completa. Para ajustar: `PROGRESSIVE_SOFT_S` (0 desliga) e `PROGRESSIVE_MIN_RESULTS`, como variáveis de ambiente do processo. 1ª busca medida: de 12 a 15 s (antes até 30 s), dos quais uns 4 s são a consulta ao TorBox/RD.
- **Homônimos pelo arquivo (09/11):** depois da consulta ao debrid, o nome do **arquivo** escolhido dentro do pacote também passa pelas regras de homônimo (`TitleMatcher.homonym_blocked`, `TorrentManager.drop_homonym_files`). Os títulos de episódio da homônima valem também sem o número da parte ("World War Three (2)" → "world war three"). Um trecho que também aparece num título nosso só bloqueia pelo SxxEyy ("the daleks" × "evolution of the daleks"). Ainda passam pacotes **não baixados** sem ano e sem título de episódio no nome ("Doctor Who S01 1080p"): pelo nome não dá para saber de qual série são.
- **Streamings no estilo Disney+ (09/11, `comet/services/streamings.py`):** uma **Coleção** do Nuvio, "Streamings", fixada no topo da tela inicial, com 11 pastas: Para Você, Netflix, Prime Video, Disney+, HBO Max, Apple TV+, Paramount+, Globoplay, Crunchyroll, MUBI e Pluto TV.
  - **Na tela inicial (Moderna):** cada pasta é um bloco 16:9 de vidro na cor da marca com o logo oficial (TMDB). Com foco, o bloco ganha um brilho que passa (GIF), e o destaque do topo vira o **vídeo** daquele serviço: os títulos em alta passando com zoom lento, ~17 s, gerado com ffmpeg. Depois do vídeo fica o fundo do título em alta, com o logo do serviço.
  - **Dentro da pasta:** abas 🏆 Top 10 séries, 🏆 Top 10 filmes (cards com o número gigante, estilo Netflix), ✨ Originais (pela rede: Netflix, Prime, Disney+, Max, Apple, Paramount+, Globoplay e Crunchyroll), 🔥 Séries em alta, 🍿 Filmes em alta, 🆕 Novidades, 💎 Séries aclamadas e 💎 Filmes aclamados. A aba aparece como "🏆 Top 10 séries · Netflix" porque o Nuvio junta o nome do catálogo com o filtro.
  - **Catálogos:** `comet_sv_<aba>` (8 no manifesto, com `showInHome: false`; o serviço vai no `genre`). Sem serviço, usam a Netflix. As fileiras "📺 Nos streamings" e "⭐ Originais dos streamings" saíram da tela inicial (`showInHome: false`) e continuam no Explorar.
  - **Artes:** `/art/svc/{tile|focus|hero|logo|video}/<serviço>.<ext>` e `/art/svc/rank/<serviço>-<n>.jpg`, em `data/svc_art/`. São refeitas todo dia às 05:10 (o fundo e o vídeo seguem o que está em alta). Fonte Montserrat (OFL) em `data/fonts/`. Para trocar o desenho, suba `_ART_VERSION`.
  - **Coleção:** `GET /nuvio/colecoes.json`, sem token (só tem links das artes e o id do addon). Para instalar na TV, veja `config/nuvio-ajustes.md` ("Coleções").
- **Rádio:** a 1ª opção é vídeo (`/radio/<id>/tv.ts`: capa parada + áudio, ffmpeg), porque o player do Nuvio espera um quadro de vídeo.
- **Fileiras:** 47 catálogos (42 na tela inicial), agrupadas (`row_groups.py`). Inclui:
  - fileiras pessoais;
  - Sua semana, Minha TV, futebol e retrospectiva;
  - datas comemorativas na época;
  - textos 100 % PT (`pt_text.py`).
- **Selos nas miniaturas dos episódios:** DUB, FILLER, FINAL e nota.
- **Biblioteca pessoal = Stremio + Trakt** (`comet/services/trakt.py`, novo em 09/11).
  - O Nuvio usa o Trakt como "Fonte para Biblioteca". O Comet lê do Trakt a lista de desejos, o histórico e o progresso pausado, e junta com a conta antiga do Stremio (`stremio_library.get_stremio_library`).
  - Minha lista, Novos episódios, Recomendados, Chegou dublado, Minha TV, gosto, madrugada e aquecimento passam a seguir o Nuvio.
  - **Conectado em 09/11.** O app do Trakt é PKCE e **não tem Client Secret**: o `trakt.py` só manda `client_secret` se ele existir no `.env`. O `TRAKT_CLIENT_ID` está no `.env` do A71. Na conexão vieram 37 títulos com histórico e 1 na lista de desejos.
  - O token fica em `/opt/comet/data/trakt_token.json` e é renovado sozinho. Teste: `unit_trakt`.

### Ajustes da TV
O registro completo está em `config/nuvio-ajustes.md`. O app não exporta ajustes; para refazer à mão depois de reinstalar, siga esse arquivo.

Mudanças de 09/11:
- **Pré-carregar fontes do próximo episódio:** LIGADO.
- **Você ainda está assistindo?:** confirmado DESLIGADO.
- **Preferir mesma fonte:** confirmado LIGADO.
- **TMDB na "Tela inicial moderna":** LIGADO, a pedido do usuário. "Arte" continua DESLIGADA, para manter as nossas capas.

---

## 4. Feito em 09/11/2026 (separação do Nuvio)

1. **Stremio guardado** (ver Seção 6): tag `stremio-20261109` no `CometToStremio`, backup completo no A71 e cópia no PC.
2. **Pasta e repositório próprios:** `C:\Users\Vendel\Desktop\Projetos\a71\NUVIO` → `onederson/NuvioComet` (branches `main` e `dev-nuvio`). A história do git veio junto.
3. **Código só do Stremio removido** (o comportamento do Nuvio vale para qualquer link):
   - **Arte de fundo com texto** (`/art/hero`, `live_art.hero_background`, `_hero_info`) e o aquecimento dela no `row_warm`, que desenhava um pôster 1920×1080 por título. Isso economiza CPU no A71.
   - **Troca da faixa PT pelo A71** (`&fix=1`, `_pt_fix_response`, `flag_plan.py`, `flag_proxy.py`, plano `pt_fix` no `media_info`). O Nuvio escolhe a faixa sozinho.
   - **Aviso "Começa em EN: troque o áudio"**.
   - **Chaves `is_nuvio_request`/`prefer_4k`:** 4K primeiro, rádio em vídeo e fundo limpo valem sempre.
4. **▶ Assistir sem SD dublado** quando há opção pronta em HD (teste `unit_nuvio_4k_natal`).
5. **FrostStream removido** do `DIRECT_ADDONS_URL`: responde 403 "Acesso negado". Backup do `.env` em `data/env.bak-1109b`.
6. **Trakt na biblioteca pessoal** (Seção 3).
7. **Testes:** `REGRESS OK` (49 pass, 1 warn: `comet_mylist` vazio, normal). Patch: 83 arquivos.

### Segunda leva (09/11, noite)
8. **Banco travado resolvido.** A causa não era a partida: era a atualização do mapa de animes (a cada 5 dias). Ela apagava e regravava ~230 mil linhas numa transação só, e o `execute_many` do `databases` grava linha a linha, montando o SQL em Python. Isso prendia o SQLite por 6 minutos e gerou 202 `database is locked`. No SQLite, `anime.py` agora monta as linhas e grava com o `executemany` nativo do sqlite3, numa thread (`_sqlite_write_mapping`). A transação continua única e leva **6 s**. Conferido: o resultado é idêntico ao do código antigo (entradas, ids e overrides) e houve 0 travas com a regressão rodando junto.
9. **Busca progressiva**, **homônimos pelo arquivo** e **fonte que falhou perde a vez** (Seção 3).
10. **Testes:** `REGRESS OK` (51 pass, 1 warn). Novos: `unit_homonimos_arquivo` e `unit_fonte_falhou`. Patch: 84 arquivos (novo: `comet/services/failed_sources.py`).
11. **Streamings no estilo Disney+** (Seção 3), instalado na TV. `REGRESS OK` (52 pass, 1 warn; novo `unit_streamings`). Patch: 85 arquivos (novo: `comet/services/streamings.py`).
12. **Repaginação visual (09/11, noite):**
    - **Cards:** a saída de todo catálogo passa por `streamings.nuvio_cards`. A arte `/art/land` (fundo + logo do título + selos) vira `landscapePoster`, que o card horizontal do Nuvio usa (sem isso ele mostrava o fundo cru). O "Top 10 filmes/séries hoje" da tela inicial ganhou o card com número gigante dourado (`/art/svc/rank/top-<n>.jpg`).
    - **Blocos dos streamings:** a cena do título em alta (originais primeiro; uma cena diferente por serviço), banhada na cor da marca, com o logo flutuando com brilho e borda de vidro. Com foco, os títulos em alta passam dentro do bloco (WebP animado, `focus/<serviço>.webp`). O "Para Você" é um mural inclinado com os pôsteres dos seus recomendados e da sua lista. `_ART_VERSION = 5`.
    - **Nomes das fileiras** sem emoji e com filme/série no nome (`catalog_manifest._CLEAN_NAMES`). "Ao vivo agora" e "Minha TV" com texto curto ("Agora: … · até 21:25" / "A seguir: …").
    - **Ordem da tela inicial** (enviada pela API da TV): Streamings, Top 10, Para você, Novos episódios, Minha lista, Em alta, Lançamentos, Dublados, clima, Natal, semana, gêneros, sagas, animes, sorteio, TV ao vivo, retrospectiva. Script: `order.py` (no histórico desta sessão); a lista está em `config/nuvio-ajustes.md`.
    - **Na TV:** tema Âmbar, fonte DM Sans, cards "Confortável", "Tipo de conteúdo" desligado (tirou o "- Filme") e **trailer automático desligado**. Os trailers do YouTube falham nesta Fire TV e deixavam o card, ou a tela toda no modo "No destaque", preta.
    - **Homônimos:** o log agora mostra os nomes removidos. No One Piece (anime), os 4 removidos eram do live-action, e a referência da regressão foi corrigida (pt 1 → 0).
    - **Pendente pequeno:** 10 `database is locked` (DEBUG) num mesmo segundo em `subtitles._persist_pt_filename`, durante a regressão junto com a geração das artes. Não afeta o uso.
    - **Pegadinha da TV:** no Addons do Nuvio, um OK na lista de instalados liga/desliga o addon. Na instalação, o Comet foi desligado sem querer e religado em seguida. Confira a chave do Comet: **verde = ligado**.

---

## 5. Pendências e próximos passos

| # | O quê | De quem |
|---|---|---|
| 1 | **1ª busca ainda leva de 12 a 15 s.** Uns 6 s vêm do corte da busca progressiva, para esperar o BrSites, que traz os dublados BR em 5,5 a 6 s. Outros ~4 s são a consulta ao TorBox/RD. O autoplay do Nuvio espera 3 s, então numa 1ª busca ainda pode cair na lista. Ideia: aquecer antes os episódios prováveis (próximo da série em andamento), como o `prefetch` já faz | Claude |
| 2 | Pacotes de homônimos **não baixados** sem ano nem título de episódio no nome (Seção 3) | — |
| 3 | Ideias sem dono: podcasts, pedido por foto | — |

---

## 6. Voltar ao Stremio (se precisar)

Três cópias da última versão do Stremio (09/11/2026):
- **git:** repo `onederson/CometToStremio`, tag `stremio-20261109` (também `stremio-final` = 07/11);
- **A71:** `/var/backups/stremio-20261109/` (chroot): `stremio-20261109.tar.gz` (`/opt/comet` sem `.venv`, `img_cache`, `live_art` e `subs_cache`, mais o Caddyfile), `comet.db.gz`, `env.bak`, `COMET_BASE_COMMIT` e `SHA256SUMS`;
- **PC:** `C:\Users\Vendel\Desktop\Projetos\a71\STREMIO\backups\stremio-20261109\` (fora do git; somas conferidas).

**Para restaurar**, no chroot:
1. Pare o watchdog e o Comet.
2. Faça um backup da pasta atual: `mv /opt/comet /opt/comet.nuvio`.
3. Extraia o tar com `cd /opt && tar xzf .../stremio-20261109.tar.gz`. Ele recria `/opt/comet`, e o Caddyfile sai em `/opt/etc/caddy/Caddyfile`: copie-o para `/etc/caddy/`. Depois copie a `.venv` de volta de `/opt/comet.nuvio`.
4. Restaure o `.env` (`env.bak`) e o banco (`gunzip comet.db.gz` em `data/`).
5. Religue o watchdog.

Alternativa: `git checkout stremio-20261109` no `..\STREMIO` e `.\scripts\deploy_patches.ps1`.

---

## 7. Mapa de arquivos

| Arquivo | Para quê |
|---|---|
| `README.md` | Visão geral e operação do dia a dia |
| `CLAUDE.md` | Modo de trabalho da IA |
| `config/nuvio-ajustes.md` | Todos os ajustes do app na TV (para refazer à mão) |
| `config/Caddyfile` | Espelho do `/etc/caddy/Caddyfile` |
| `comet-patches/ptbr-first.patch` | Todo o código próprio do Comet |
| `scripts/` | `a71.ps1`, `regress.ps1`/`regress.py`, `smoke.ps1`, `deploy_patches.ps1`, `watchdog.sh`... |
| `COMO-CRIAR-BOT-TELEGRAM.md` | Passo a passo do bot |
| `docs/historico/` | Documentos da fase Stremio (handoff, melhorias, tarefas antigas do agy) |
| `downloads/nuvio/` | APKs do Nuvio (fora do git) |
