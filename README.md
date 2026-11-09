# NuvioComet: o Comet do A71 para o app Nuvio

*Início da fase Nuvio: 05/11/2026 · Separado do Stremio em 09/11/2026 (horário de Brasília)*

> **🤖 Para outra IA continuar daqui:** leia `CLAUDE.md` (modo de trabalho) e `HANDOFF.md` (regras, estado e pendências) antes de rodar qualquer comando.

## 1. O que é

Um servidor **Comet** (addon no protocolo do Stremio) rodando 24 h num **Galaxy A71** com root, dentro de um Debian em chroot. O cliente é o app **NuvioTV** numa Fire TV 4K.

Ele faz cinco coisas:
- junta torrents de várias fontes;
- entrega pelo **TorBox** (principal) e pelo **Real-Debrid** (reserva);
- põe o **dublado PT-BR em primeiro lugar**;
- monta a tela inicial: fileiras pessoais, sagas, TV e rádio ao vivo, futebol e Minha TV;
- avisa e atende pedidos pelo Telegram.

O que o Nuvio já faz sozinho fica com ele: TMDB, elenco, trailers, pular abertura, autoplay, faixa de áudio PT e Trakt. Veja o `HANDOFF.md`, Seção 3.

| Uso | Endereço |
| :--- | :--- |
| Link do Nuvio (casa e 4G) | `https://a71comet.duckdns.org:7443/<NUVIO_LINK_TOKEN>/manifest.json` |
| Link Kids | `https://a71comet.duckdns.org:7443/<KIDS_LINK_TOKEN>/manifest.json` |
| Página de status | `https://a71comet.duckdns.org:7443/<PERSONAL_LINK_TOKEN>/status` |
| Bot do Telegram | `@comet_a71_casa_bot` (só responde ao dono) |

Os tokens ficam no `config/.env` e nunca vão para o git.

## 2. Operar

```powershell
.\scripts\a71.ps1 '<cmd>'            # comando como root no Android
.\scripts\a71.ps1 -Chroot '<cmd>'    # comando dentro do Debian
.\scripts\regress.ps1                # suíte completa (~50 testes); -Only nome1,nome2 roda só alguns
.\scripts\smoke.ps1                  # teste rápido (pula se existir data/smoke.disable)
.\scripts\deploy_patches.ps1         # reaplica o patch do Comet + smoke + regress
.\scripts\update_comet.ps1 -Check    # há versão nova do Comet original?
```

- **Reiniciar o Comet:** `.\scripts\a71.ps1 'pkill -f "python -m comet.main"'`. O watchdog religa em até 30 s. Espere ~1 min antes de testar.
- **Logs:** `/data/comet/comet.log`, `watchdog.log`, `caddy.log`, `stremthru.log`, `prowlarr.log`.
- **Regenerar o patch** depois de mudar código no A71: no chroot, rode `git -C /opt/comet diff -- . ':!comet/templates/index.html' ':!comet/templates/index.html.orig'` e grave a saída em `comet-patches/ptbr-first.patch` (LF).
  - Arquivo novo precisa de `git add -N` antes.
  - Arquivo apagado precisa de `git rm --cached`.
- **Fire TV:** `adb connect 192.168.3.29:5555`. Os ajustes do app estão registrados em `config/nuvio-ajustes.md`.

## 3. Branches

| Branch | Para quê |
| :--- | :--- |
| `main` | Versão aprovada (o que roda no A71) |
| `dev-nuvio` | Trabalho em andamento do Nuvio |

A fase Stremio está no repo `onederson/CometToStremio` (tag `stremio-20261109`) e na pasta `..\STREMIO`.
