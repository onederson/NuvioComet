# HANDOFF: NuvioComet (Comet do A71 para o app Nuvio)

*Última atualização: 21/11/2026 (horário de Brasília)*

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
- **Fontes:** Torrentio, MediaFusion (limite de 10 s), 2× Comet, Zilean, StremThru, Peerflix, SeaDex, Nyaa, Prowlarr, BRSites, addons (BeTor, BestCine), link direto FenixFlix e **MegaEmbed**.
- **MegaEmbed (19/11, `direct_addons._fetch_mgeb`):** lê a página `https://mgeb.space/embed/<imdb|tmdb>[/T/E]` (reservas: .lat, .casa, .quest) e tira os vídeos de `var sources = [...]`: MP4 de IPTV (720p, faixa PT medida com ffprobe) e HLS do site (480p). Entra como link direto dublado, com validação, memória e corte de episódio do `direct_addons`. MP4 vem antes do HLS. Desliga com `MGEB_ENABLED=false`; domínios em `MGEB_DOMAINS`. Especificação em `Embed/`. Leva ~8–9 s (página ~5 s + ffprobe).

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
  - **Legenda PT embutida (19/11, `pt_subs.py`, tarefa D):** sem áudio PT, o ▶ Assistir prefere uma versão com legenda PT-BR **dentro do arquivo** (sincronizada) antes da resolução: 1080p Disney+ com PT ganha da 4K B-Global sem PT. Lê até 3 outras versões em 2º plano e lembra por série quais grupo/serviço trazem PT (`data/pt_sub_groups.json`). Se o arquivo tem PT embutido, o servidor de legendas não manda externa. Motivo: a externa de outra versão desliza (+10 s → +40 s no Bleach 3x1) e nenhum atraso manual acerta.
    - **Nuvio:** se a última legenda escolhida na série era externa e a lista vem vazia, ele não liga a embutida ("Nenhuma"). Escolher a embutida uma vez na série resolve.
  - **Novo (09/11):** um torrent SD/480p dublado só vira ▶ Assistir se não houver nada pronto em HD. Costuma ser outra versão da obra (Hunter x Hunter 1999 × 2011). Ele continua na lista.
  - **Fonte que falhou perde a vez (09/11, `failed_sources.py`):** se você dá play numa fonte e, em até 60 s, em outra do mesmo título, a 1ª fica marcada para aquele título por 30 dias (`data/failed_sources.json`). O ▶ Assistir pula a marcada enquanto houver alternativa dublada, inclusive link direto. Tocar de novo a fonte marcada tira a marca. A URL de play leva `&ep_key=` (ID exato com temporada:episódio).
- **Busca progressiva (09/11, `orchestration._scrape_progressive`):** na 1ª busca de um título, a resposta sai com o que chegou em 6 s, se já houver resultado em PT. Sem PT, espera todas as fontes, como antes. O que chega depois (Prowlarr, que leva de 5 a 25 s, e MediaFusion) é filtrado à parte e vai para o cache, então a próxima abertura já vem completa. Para ajustar: `PROGRESSIVE_SOFT_S` (0 desliga) e `PROGRESSIVE_MIN_RESULTS`, como variáveis de ambiente do processo. 1ª busca medida: de 12 a 15 s (antes até 30 s), dos quais uns 4 s são a consulta ao TorBox/RD.
- **Homônimos pelo arquivo (09/11):** depois da consulta ao debrid, o nome do **arquivo** escolhido dentro do pacote também passa pelas regras de homônimo (`TitleMatcher.homonym_blocked`, `TorrentManager.drop_homonym_files`). Os títulos de episódio da homônima valem também sem o número da parte ("World War Three (2)" → "world war three"). Um trecho que também aparece num título nosso só bloqueia pelo SxxEyy ("the daleks" × "evolution of the daleks"). Ainda passam pacotes **não baixados** sem ano e sem título de episódio no nome ("Doctor Who S01 1080p"): pelo nome não dá para saber de qual série são.
- **Streamings no estilo Disney+ (09/11, `comet/services/streamings.py`):** uma **Coleção** do Nuvio, "Streamings", fixada no topo da tela inicial, com 11 pastas: Para Você, Netflix, Prime Video, Disney+, HBO Max, Apple TV+, Paramount+, Globoplay, Crunchyroll, MUBI e Pluto TV.
  - **Na tela inicial (Moderna):** cada pasta é um bloco 16:9 de vidro na cor da marca com o logo oficial (TMDB). Com foco, o destaque do topo mostra uma **imagem parada** em alta resolução (fundo do título em alta + logo do serviço).
    - **10/11:** saíram o brilho animado do foco e o **vídeo** do destaque. O vídeo do Nuvio toma a tela inteira, escondendo as fileiras, e tremia (zoom do ffmpeg). Não volte com `heroVideoUrl` nem `focusGifUrl`.
  - **Dentro da pasta (10/11):** abre **como a tela inicial**, em fileiras com destaque no topo (`viewMode: FOLLOW_LAYOUT`), sem abas. São **45 fileiras no modelo da página da Netflix** (`streamings.TABS`, referência: `Área de Transferência (1).png`):
    - **Top 10:** "Brasil: top 10 em séries hoje" e "em filmes hoje".
    - **Pessoais** (`_PERSONAL_TABS`): Continuar assistindo, Principais escolhas do dia para você, "Porque você viu <título>" (série e filme), Minha lista, Filmes que você começou e Filmes da minha lista.
      - Usam a biblioteca do Stremio + Trakt e só mostram o que está no serviço ("onde assistir" do TMDB no Brasil, cache de 3 dias).
      - O título do "Porque você viu" fica em `data/svc_art/because.json` e muda uma vez por dia, no aquecimento. O nome da fileira vem do manifesto, que o Nuvio só relê ao abrir o app.
    - **Gêneros** (`_GENRE_TABS`, TMDB do serviço no Brasil): Anime, Ficção, Para ver e relaxar, Séries emocionantes, Romances, Episódios curtinhos, Dramas coreanos, Dramas adolescentes, Família, Ação, Crime e mistério, Crimes reais, Suspense, Mistérios, Terror, Séries dos EUA/japonesas/em espanhol/brasileiras, Filmes brasileiros, Comédias, Ficção científica, Fantasia, Guerra, Filmes de anime, Para as crianças, "Com pressa?", Documentários, Clássicos e os Aclamados pela crítica.
    - **Sem repetir títulos:** cada fileira tira os 12 primeiros das fileiras de cima, do jeito que aparecem na tela, e o Top 10 do mesmo tipo (`_seen_above`/`_dedup`). Se sobrar pouco, completa com a página seguinte. As pessoais podem repetir, como na Netflix.
    - Fileira vazia (ex.: MUBI sem animes) não aparece no Nuvio.
    - **Carga:** no máximo 4 fileiras buscadas no TMDB ao mesmo tempo (`_FETCH_SEM`). Fileira incompleta (limite do TMDB) fica só 10 min no cache. `_warm_rows` aquece as ~450 fileiras devagar, com pausa entre elas, depois de cada reinício e às 05:10. Sem a pausa, o "Animes dublados" estourou o tempo limite de 15 s na regressão.
  - **Cards no estilo Netflix (10/11, `comet/services/artwork.py`, valem para todas as fileiras):**
    - **Top 10** (dos serviços e o "Top 10 de hoje" da tela inicial): `/art/top10w/<n>/<pôster>?b=&p=`, card 16:9 com o número gigante só no contorno cinza e o pôster em pé por cima da ponta, com o logo do serviço e a etiqueta embaixo. Substituiu o número dourado (`/art/svc/rank`, removido).
    - **Card horizontal** (`/art/land`, chave de cache `landv4`): cena mais viva, logo do título embaixo à esquerda, logo do serviço pequeno no canto de cima à esquerda e o quadradinho vermelho "TOP 10" no canto de cima à direita. O Nuvio guarda a imagem pelo endereço: ao mudar o desenho, suba também `_CARD_V` em `streamings.py` (vai como `&v=` no `landscapePoster`), senão a TV continua com o card antigo.
    - **Etiqueta** no pé, ao centro: "Novo episódio | Assista já" (episódio de hoje ou ontem), "Nova temporada" (temporada estreou há até 30 dias), "Novidade" (série ou filme novo), "Novo episódio" (até 10 dias) e "Dublado" (branco). Regra das séries: `catalogs._series_badge`, com os dados de `_tv_info`.
    - **Fundo da tela (10/11, `streamings._hi_background`):** o `background` de todo card é a cena do TMDB em tamanho `original`, nunca o card (`/art/...`) e nunca `w1280`. Antes a pasta usava o próprio card esticado como fundo (no Top 10, o número + pôster) e a tela inicial ficava em 1280 px; só a página do título vinha em 4K.
    - **Fileiras pessoais da tela inicial** (Para você, Minha lista, Novos episódios, Acabou de chegar dublado; `stremio_library._build_landscape_meta`): sem serviço informado, perguntam ao TMDB onde o título passa (`resolve_single_watch_provider`) e calculam a etiqueta da série (`_series_badge`). Antes saíam sem logo e sem etiqueta.
    - **Logo do serviço só nos originais**, nas pastas: séries pela rede do TMDB e filmes pelo estúdio (`streamings.is_original`, `_ORIG_COMPANIES`). Na tela inicial o logo continua mostrando onde o título passa.
  - **Catálogos:** `comet_sv_<aba>` (8 no manifesto, com `showInHome: false`; o serviço vai no `genre`). Sem serviço, usam a Netflix. As fileiras "📺 Nos streamings" e "⭐ Originais dos streamings" saíram da tela inicial (`showInHome: false`) e continuam no Explorar.
  - **Artes:** `/art/svc/{tile|hero|logo}/<serviço>.<ext>` (`focus` e `video` ainda respondem, mas não são mais gerados nem usados) e `/art/svc/rank/<serviço>-<n>.jpg`, em `data/svc_art/`. São refeitas todo dia às 05:10 (o fundo e o vídeo seguem o que está em alta). Fonte Montserrat (OFL) em `data/fonts/`. Para trocar o desenho, suba `_ART_VERSION` (7 desde 10/11).
  - **Coleção:** `GET /nuvio/colecoes.json`, sem token (só tem links das artes e o id do addon). Para instalar na TV, veja `config/nuvio-ajustes.md` ("Coleções").
- **Top 10 oficial da Netflix (10/11, `official_top10.py`):** a pasta da Netflix usa o Top 10 que a própria Netflix publica para o Brasil (arquivo `all-weeks-countries.tsv`, ~33 MB, semanal, sai às terças). É baixado no máximo a cada 12 h, em 2º plano (leva ~3 min no A71), e cada título é casado com o TMDB conferindo se está na Netflix BR (homônimos como "The One"). Estado em `data/official_top10.json`.
  - Os outros serviços **não publicam** Top 10: Prime, Disney+, Max, Apple, Paramount+ e Globoplay continuam com "em alta no TMDB para aquele serviço". O FlixPatrol junta todos, mas bloqueia acesso automático (403) e a API dele é paga.
- **Cards em todas as fileiras (10/11, `streamings.land_enrich`):** Estreias da semana, Acabou de chegar dublado, Minha lista, Para hoje à noite, Natal novos e "Chegou esta semana" ganham o card com logo do título, logo do serviço e etiqueta (cache de 6 h; o que não fica pronto em 8 s entra no próximo pedido). `_land_url` reconhece o card embrulhado pela capa "ao vivo" (`/art/live.jpg?u=`).
- **Selo + Dublado (10/11):** quando há dublado pronto, o selo vira "Novidade | Dublado", "Novo episódio | Dublado", "Nova temporada | Dublado" ou TOP 10 no canto + "Dublado" embaixo (`catalogs._with_dub`, `artwork.VALID_BADGES`). Antes o "Dublado" só aparecia sem outro selo.
- **Fileiras novas (10/11):** "Chegou esta semana nos streamings" (`comet_svnew`: filme com lançamento digital no BR há até 10 dias; série com episódio nos últimos 7 dias), "Filmes curtos (até 1h30)" (`comet_mood_short`, dentro do "Para cada clima") e "Séries completas para o fim de semana" (`comet_mood_weekend`: encerradas, episódios até 50 min).
- **Podcasts (10/11, `podcasts.py`):** fileira "Podcasts mais ouvidos" com o ranking do Brasil da Apple (sem chave, 6 h). Cada podcast é uma série (`cometlive:pod:<id>`); episódios do RSS do próprio podcast. 1ª fonte: vídeo com a capa + áudio (ffmpeg, como a rádio, sem avançar); 2ª: o áudio direto (dá para avançar).
- **Filmes dos games (10/11, `games.py`):** fileira "Filmes dos games": 15 sagas (God of War, Kingdom Hearts, The Last of Us, Uncharted, FF VII, Resident Evil, Spider-Man, Red Dead, Batman Arkham, Horizon, Metal Gear, Star Wars Jedi, Mortal Kombat, Ghost of Tsushima, Death Stranding), cada jogo um episódio. Vídeos do YouTube achados pelo `yt-dlp` (venv separado em `/opt/ytdlp`; atualizar com `/opt/ytdlp/bin/pip install -U yt-dlp` se parar): uma versão **dublada** e uma **legendada**, com pelo menos 45 min, sem repetir vídeo na saga. Escolhas em `data/games.json` (14 dias; `_PICK_V` refaz). Para tocar, `/game/play/<id>.m3u8` repassa o HLS do YouTube (até 1080p, dá para avançar). O link vale para o IP de casa: no 4G pode não abrir. Para pôr outro jogo/saga: lista `SAGAS`.
- **Link de convidado (10/11):** `GUEST_LINK_TOKEN` no `.env`. Para outro perfil do Nuvio (cada perfil tem seus addons): tudo igual, menos as fileiras pessoais (`GUEST_HIDDEN_CATALOGS`). Já existia o link infantil (`KIDS_LINK_TOKEN`). Os plays do convidado ainda contam no "gosto" e na "fonte que falhou".
- **Rodada do fim da tarde (10/11, `nightly._evening_loop`):** às 17:30, só o próximo episódio das séries em andamento (cache do debrid vale 1 dia), para o play da noite sair do cache e o aviso de dublado no Telegram chegar antes. Log `🌆`. Os avisos agora dizem "no Nuvio".
- **Minha TV e fileiras extras:** a 1ª fonte também ganha "▶ Assistir" (toca sozinha).
- **Rádio:** a 1ª opção é vídeo (`/radio/<id>/tv.ts`: capa parada + áudio, ffmpeg), porque o player do Nuvio espera um quadro de vídeo.
- **Rádio e TV ao vivo tocam direto (10/11):** a 1ª opção de todo `cometlive:` (Pluto, rádio, Minha TV) ganha "▶ Assistir" no nome (`stream.py`), para casar com o regex do Nuvio, como nos filmes.
- **Animes dublados (10/11):** a lista de animes com torrent PT no banco (análise de ~42 mil nomes, ~13 s no A71) fica guardada 1 h (`catalogs._anime_pt_series`). A análise começa 20 s depois de ligar (`warmup._warm_anime_pt`) e continua mesmo se um pedido estourar os 15 s. Antes a fileira vinha vazia a cada reinício.
- **Fileiras:** 47 catálogos (42 na tela inicial), agrupadas (`row_groups.py`). Inclui:
  - **Grupos com nome do dia (20/11):** na tela inicial o Nuvio não mostra o filtro do grupo, que sorteia um membro por dia. Por isso o nome traz o membro de hoje: "Gêneros · Para a família", "Para cada clima · Mais bem avaliados" (`row_groups.day_label`, aplicado em `catalog_manifest`). O Nuvio só relê o nome quando o app abre.
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

### 10/11/2026
13. **Streamings mais limpos** (Seção 3): bloco sem vídeo nem animação no foco, destaque parado em alta resolução, pasta do serviço em fileiras como a tela inicial e sem repetir o Top 10. Coleção reinstalada na TV pelo "Gerenciar pelo celular" e conferida na TV. `REGRESS OK` (52 pass, 1 warn).
15. **Leva de novidades (noite de 10/11, `docs/tarefas/A.md`):** Top 10 oficial da Netflix, cards com logo/etiqueta nas fileiras que faltavam, selo "Novidade | Dublado", "Chegou esta semana nos streamings", filmes curtos, séries para o fim de semana, podcasts, filmes dos games, link de convidado e rodada das 17:30 (tudo na Seção 3). Já existiam e ficaram como estavam: logos e gêneros das rádios/TV, pré-busca do próximo episódio (`prefetch`) e aviso de dublado no Telegram.
14. **Página dos streamings igual à da Netflix** (Seção 3): 45 fileiras por serviço (pessoais incluídas), Top 10 com número vazado + pôster, etiquetas da Netflix, logo só nos originais e sem títulos repetidos entre as fileiras. `REGRESS OK` (52 pass, 1 warn; manifesto com 92 catálogos).
    - Fora do alcance do servidor: card do Top 10 mais estreito, como o da Netflix (o Nuvio usa um formato só por fileira), fileiras que misturam filmes e séries (cada catálogo é de um tipo só) e o nome do episódio embaixo do card.

### 17/11/2026
16. **Trailer não vira filme** (`filtering.TitleMatcher.extra_video_blocked`): torrent ou arquivo com "trailer", "teaser" ou "featurette" no nome sai da lista, salvo se a palavra estiver no título da obra ("Trailer Park Boys"). Vale no `filter_worker` (busca nova) e no `drop_homonym_files` (arquivo escolhido depois do debrid, inclusive o que já estava no cache). Antes, "A Odisseia" tocava "Official Teaser Trailer 4K.mkv" como ▶ Assistir. `REGRESS OK` (52 pass, 1 warn). Patch: 88 arquivos.
17. **Nuvio no projetor** (Horlat T9N, `192.168.3.64:5555`, Android 12, armeabi-v7a): NuvioTV 1.1.0 instalado e logado no Nuvio Sync. Ajustes em `config/nuvio-ajustes.md` ("Projetor").

### 17/11/2026 (`docs/tarefas/B.md`)
18. **Pacote BR com episódio errado:** nomes `Nome.04.21.720p` (TT.EE) e "Episódio NN" agora são lidos (`stremthru.br_episode_hint`). Play sem `media_id` usa o `ep_key`, e temporada+episódio sempre exigem o arquivo certo. Antes, o 2 Broke Girls T4 dublado abria o primeiro arquivo do pacote.
19. **Outro corte de episódios (Netflix):** link direto e torrent sondado com duração muito diferente da do episódio no TMDB (ex.: Meu Nome é Farah, 42 min × 129 min) ficam fora do ▶ Assistir e dos atalhos, com o aviso "⚠️ Outra divisão de episódios". Medições em `data/direct_durations.json`.
20. **Streamings com "+" no nome** (Apple TV+, Disney+, Paramount+) caíam na Netflix: o `parse_qs` trocava o `+` por espaço. Consertado em `catalog.py` e `streamings._service_from_genre`.
21. **Dublado escondido em "Multi-Audio" (anime da Crunchyroll):** sem dublado confirmado, o Comet sonda até 2 releases Multi-Audio/MULTi/Dual; achando PT-BR, a próxima abertura já mostra 🇧🇷 (Diários de uma Apotecária T2). NCOP/NCED/trailer não viram atalho nem ▶ Assistir.
    - `REGRESS OK` (52 pass, 1 warn). Patch: 88 arquivos.

### 18/11/2026 (revisão geral)
22. **Conferido ao vivo:** `REGRESS OK` (52 pass, 1 warn: "Minha lista · filmes" vazia porque a lista do Trakt só tem uma série). Patch do PC idêntico ao do A71. Smoke OK de hora em hora. Play real com o episódio e o dublado certos: Apotecária 2x1/2x12/2x24 e 2 Broke Girls 4x21/4x22/2x5, vídeo em até 2,8 s. NuvioTV 1.1.0 é a última versão estável (1.2.0 ainda beta).
    - Pequenos, em aberto: na 1ª abertura a linha 🔊 pode sair incompleta até a sondagem terminar; a busca falha em 2 de 32 frases ("anime de ninja", "filme de dinossauro").
23. **Episódio errado em pacote de temporada (`playback.py`):** desde 02/11, play com `media_type=series` e `media_id` só da série (o que o nosso link sempre manda) virava "filme" e zerava temporada/episódio. O link guardado ficava sem episódio e todo episódio do mesmo pacote abria o primeiro que foi tocado (4x21 e 4x22 do 2 Broke Girls davam o mesmo arquivo). Agora só vira filme quando o `ep_key` não tem `T:E` (Sagas). Conferido: 4x20/4x21/4x22 abrem arquivos diferentes, `exact_episode`. `REGRESS OK` (52 pass, 1 warn).
24. **Teste "vídeo de uma fonte + áudio PT de outra" (só medição, nada no Comet):** juntar sem recodificar é rápido no A71 (11× o tempo real). A sincronia é o problema: Heartland (0,02 s) e Wish Dragon (−1,86 s constante) encaixam; On the Waterfront, 2 Broke Girls 3x10 e Apotecária 2x12 (BD × CR) não encaixam; 2 Broke Girls 4x21 encaixa, mas o atraso cresce nos intervalos comerciais (2,7 → 6,2 s). Medição por correlação do áudio (faixa no mesmo idioma ou envelope de volume da faixa PT). Nos filmes populares o 4K dublado já existe.
25. **🎬 Imagem de uma fonte + áudio PT de outra (`av_mux.py`, `docs/tarefas/C.md`): pronto no servidor e DESLIGADO** (`AV_MUX`, padrão falso). Escolhe o par, mede a sincronia em 2º plano, oferece "🎬 4K + 🇧🇷 áudio" e entrega HLS fMP4 com a sincronia conferida (erro 0,000 s). Na Fire TV o Nuvio congela no 1º quadro, inclusive com o HLS padrão do ffmpeg: falta descobrir se é HEVC em HLS nesse player. Numpy em `/opt/avsync`. Pendência #6.
26. **Memória dos links diretos dublados (`direct_addons.py`):** o FenixFlix passou a noite de 18/11 respondendo vazio de forma intermitente (2 Broke Girls 5x01 tocou dublado às 13:26 e sumiu à noite; 4x22 e Apotecária iam e voltavam). Todo link direto PT que passa na validação fica guardado por episódio, 7 dias, em `data/direct_memory.json` (até 6 por episódio). Quando a fonte vem sem dublado, os guardados são revalidados e os que abrem voltam com "♻️ Link guardado". Conferido com a fonte forçada vazia (4x22 volta dublado). `REGRESS OK` (52 pass, 1 warn).
    - Em aberto: projetor (192.168.3.64, Hisilicon armv7, 2 GB, Wi-Fi 2,4 GHz) com o player reabrindo o link 5 a 8 vezes em segundos em 2 Broke Girls 5x01 e Apotecária 3x01. O TorBox entrega 26 MB/s no A71; falta medir no projetor (estava desligado). TV Bro instalado nele.

### 20/11/2026 (`docs/tarefas/E.md` e `docs/tarefas/F.md`)
27. **Arquivamento automático no TorBox (`direct_archive.py`):** vigia séries prioritárias (inicia com *2 Broke Girls* `tt1845307`, configurável via `/arquivar` e `/desarquivar` no Telegram). Quando falta torrent PT-BR HD em cache no TorBox, baixa do link direto (FenixFlix > MegaEmbed MP4 > HLS), valida com `ffprobe`, monta `.torrent` BEP-19 (`url-list`) e sobe no TorBox (`seed=3`, contando apenas `active == True` contra o limite real do plano). Quando o TorBox não acessa o link direto (ou porta `:7443`), abre sob demanda um túnel `cloudflared` (ARM64 em `/usr/local/bin/cloudflared`, porta 443 `trycloudflare.com`) servindo `/archive-seed/<token>/<arquivo>` e encerra após 3 min ocioso; sincroniza em `torrents` + `debrid_availability` e faz keepalive a cada 18 dias.
28. **Biblioteca offline no A71 + `/baixar` no Telegram (`offline_library.py`):** aceita `/baixar <filme ou série> [T5 | T5E01 | original]` ou texto natural (`"Baixar todas as temporadas de 2 broke girls dublado"`), `/baixados`, `/cancelar` e `/apagar`. Busca pelas próprias fontes do Comet (`?nolocal=1`: TorBox ⚡ > RD ⚡ > FenixFlix > MegaEmbed), baixa para `/opt/comet/data/offline/` preservando `OFFLINE_MIN_FREE_GB=15` GB livres, valida com `ffprobe`, e sobe também no TorBox se ainda não houver cópia PT-BR HD em cache lá. Em `stream.py`, qualquer título salvo localmente é inserido no topo como **`▶ Assistir · 💾 Local`** (`inject_local_into_streams`, `/offline/{token}/{media_id}/{filename}`, HTTP 206 `Range` nativo) rebaixando o prefixo `"▶ Assistir"` da melhor fonte online para `"☁️ ..."` logo abaixo (sem removê-la da lista).
    - **Ajustes de 21/11 (após revisão Opus 5.5):** gravação em disco e cálculo SHA-1 em thread (`asyncio.to_thread`) tanto no HTTP quanto no HLS (zero bloqueio no event loop do Comet); `ffmpeg` HLS com timeout proporcional à duração (mín. 40 min filmes / 15 min séries), checagem contínua de `/cancelar` (`proc.terminate()`/`kill()`) e `try/finally` para `CancelledError` (sem processos órfãos nem file handles abertos); contagem separada de falhas, resumo parcial da temporada e parada imediata da fila em `low_disk` (listando os pedidos descartados); `build_local_stream` não emite token `"local"` inválido sem token real; em `stream.py`, quando há arquivo local, `search_media` (6 s com `asyncio.shield`), `direct_streams` (3 s) e todo o pós-processamento ficam protegidos por `try/except` para nunca falhar nem demorar se fontes online caírem; cooldown de 7 dias com `last_upgrade_try_at` e sonda de altura na URL (`_ffprobe_url_height`) no `direct_archive.py` (tentando o próximo candidato quando o 1º ainda é `< 650p`, retornando `None` sem inflar `archived_count` se continuar `< 650p`, e apagando o torrent 480p antigo no TorBox e nas tabelas `torrents` / `debrid_availability` via `remove_hash_from_db` quando o upgrade para `>= 650p` conclui ou quando um torrent expira do cache).
    - Conferido ao vivo no *2 Broke Girls*: 40 episódios já arquivados automaticamente no TorBox (incluindo upload real via `cloudflared` em 18 s pro S06E18 720p); `/stream/.../tt1845307:5:1.json` retorna 85 fontes com `▶ Assistir · 💾 720p` em 1º (`Range 206` OK), `☁️ 720p` (melhor online curada) em 2º, `🎧 Original · 1080p` em 3º e `⚡ 720p · 🇧🇷✔ Dublado` (TorBox) em 4º.
    - `REGRESS OK` (**54 pass, 1 warn**; 22 testes unitários locais OK; 0 erros no log). `smoke.disable` removido. Patch regerado em LF.
    - **Ajustes de 21/11 (2ª revisão, `/claudevalidateopus` sobre `7ab67bf`):**
      - Removido o envio reserva ao `catbox.moe`. Nenhum vídeo vai para host público; se o túnel falhar, o arquivamento só tenta de novo depois.
      - O túnel `cloudflared` não aponta mais para o Comet (`:8000`). Ele aponta para um servidor HTTP mínimo em `127.0.0.1:8788` (`ARCHIVE_SEED_PORT`), aberto junto com o túnel e fechado com ele, que só responde `GET`/`HEAD` em `/archive-seed/<token>/<arquivo>` (com `Range`). Qualquer outra rota dá 404 e outro método dá 405. Conferido ao vivo: seed 200/206, e `/manifest.json`, `/admin`, `/status` e `/` com 404 pelo `trycloudflare.com`. A rota `/archive-seed` saiu do Comet; o `router` vazio fica porque o `main.py` o inclui.
      - `build_local_stream` só usa o token recebido se `_is_valid_token` aceitar. Uma config base64 comum cai no `_get_internal_token()` (ou não gera stream), o que evita 403 no play de `/offline`.
      - **Nota sobre `remove_hash_from_db`:** apaga todas as linhas de `torrents` (e de `debrid_availability`) com o `info_hash` antigo. Se algum scraper tivesse indexado o mesmo hash, a linha dele também sairia, mas na prática o hash é exclusivo do `.torrent` single-file gerado pelo `direct_archive`.
      - 24 testes unitários OK (2 novos: webseed isolado e ausência de host público). `REGRESS OK` (54 pass, 1 warn). Play local do 2 Broke Girls 5x01 com 206. Patch regerado em LF.
      - **Ressalvas da 3ª revisão (commit seguinte):**
        - O webseed agora aceita no máximo 6 conexões simultâneas (as demais recebem 503). Cada conexão tem prazo de 60 s para envio travado e de 30 min no total.
        - Pedido com vários intervalos ou Range malformado recebe o arquivo inteiro (200) em vez de 416.
        - Os testes usam porta escolhida pelo sistema.
        - Corrigido o comentário do `include_router` em `comet/api/app.py`.
        - **Teste real ponta a ponta:** um vídeo de teste único de 16 MB subiu pelo túnel e foi apagado em seguida. O TorBox fez um único `GET Range: bytes=0-<fim>` (206) e concluiu em 23 s.

---

## 5. Pendências e próximos passos

| # | O quê | De quem |
|---|---|---|
| 1 | **1ª busca ainda leva de 12 a 15 s.** Uns 6 s vêm do corte da busca progressiva, para esperar o BrSites, que traz os dublados BR em 5,5 a 6 s. Outros ~4 s são a consulta ao TorBox/RD. O autoplay do Nuvio espera 3 s, então numa 1ª busca ainda pode cair na lista. Ideia: aquecer antes os episódios prováveis (próximo da série em andamento), como o `prefetch` já faz | Claude |
| 2 | Pacotes de homônimos **não baixados** sem ano nem título de episódio no nome (Seção 3) | — |
| 3 | Ideias sem dono: pedido por foto | — |
| 4 | Top 10 real dos outros streamings (só a Netflix publica). Fonte possível: API paga do FlixPatrol | usuário decide |
| 5 | Perfis na TV: criar o perfil de convidado no Nuvio e instalar nele o link de convidado | usuário |
| 6 | 🎬 vídeo + áudio PT de fontes diferentes: o Nuvio na Fire TV congela no HLS (ver `docs/tarefas/C.md`, "Para continuar") | Claude |

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
