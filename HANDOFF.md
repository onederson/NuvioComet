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
  - Para gravar uma chave no `.env` do A71 sem imprimir o valor, use `scripts/env_set.sh`.
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
  - **Para ligar (falta o usuário):**
    1. Criar um app em https://trakt.tv/oauth/applications (Redirect URI `urn:ietf:wg:oauth:2.0:oob`).
    2. Gravar `TRAKT_CLIENT_ID` e `TRAKT_CLIENT_SECRET` no `.env` com `env_set.sh`.
    3. Reiniciar o Comet.
    4. Mandar `/trakt` no Telegram e digitar o código em trakt.tv/activate.
  - O token fica em `/opt/comet/data/trakt_token.json` e é renovado sozinho. Teste: `unit_trakt`.

### Ajustes da TV
O registro completo está em `config/nuvio-ajustes.md`. O app não exporta ajustes; para refazer à mão depois de reinstalar, siga esse arquivo.

Mudanças de 09/11:
- **Pré-carregar fontes do próximo episódio:** LIGADO.
- **Você ainda está assistindo?:** confirmado DESLIGADO.
- **Preferir mesma fonte:** confirmado LIGADO.

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

---

## 5. Pendências e próximos passos

| # | O quê | De quem |
|---|---|---|
| 1 | Criar o app do Trakt e mandar `/trakt` (Seção 3) | usuário (2 min) + Claude grava as chaves |
| 2 | Confirmar se quer o enriquecimento TMDB também na "Tela inicial moderna" (hoje DESLIGADO na TV; o registro antigo dizia LIGADO). Ligado, o Nuvio troca as nossas descrições na tela inicial (notas, "onde você parou") pelas do TMDB | usuário |
| 3 | **Banco travado logo após reiniciar:** com muitos pedidos ao mesmo tempo no 1º minuto, o SQLite dá `database is locked` e algumas listas levam 60 s. Some depois do aquecimento. Melhoria: `busy_timeout` maior e menos gravações concorrentes na partida | Claude |
| 4 | **A 1ª busca de um episódio leva 8 a 17 s** (o Prowlarr pesa 5 a 16 s). Proposta: responder com as fontes rápidas e completar o resto em 2º plano. Ligado a isto: o autoplay "Assistir" do Nuvio espera 3 s; numa 1ª busca lenta ele pode cair na lista | Claude |
| 5 | Pacotes de temporada de séries homônimas sem ano no nome ainda passam (Doctor Who 1963 × 2005) | Claude |
| 6 | Ideias sem dono: fonte que falhou perde pontos, podcasts, pedido por foto | — |

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
