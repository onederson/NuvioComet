# Tarefa D (19/11/2026): legenda que desliza (Bleach TYBW)

Pedido: "precisei ficar sincando as legendas toda hora (...) quando pulou a abertura sozinho dissincou tudo". Validar e, se preciso, testar na Fire TV.

## Diagnóstico (medido)
- O ▶ Assistir do 3x1 era a versão **4K B-Global (ToonsHub)**: áudio JA, legendas EN/ZH/ID/TH/VI, **sem PT**.
- Sem legenda PT pronta, o servidor ofereceu só a "dual" (`/subs/dual/subsource/10046199`): a legenda EN **de outra versão**, traduzida por máquina.
- Comparando com a EN embutida no arquivo (que acompanha o vídeo), a diferença **cresce ao longo do episódio**: +10 s (0:45), +22 s (6:35), +33 s (9:49), +35 s (14:47), +40 s (fim). É outra montagem. Nenhum atraso manual serve para o episódio todo, e o pulo da abertura leva para um trecho com outra diferença.
- Havia versões prontas no TorBox com **PT-BR embutido** (Disney+ VARYG e Disney+ DUAL ToonsHub, 1080p).

## O que mudou
1. **`comet/services/pt_subs.py` (novo):** `has_pt_sub(resumo)`, `release_key(info)` (grupo + serviço de origem + resolução) e uma memória por série de quais versões trazem PT embutido (`data/pt_sub_groups.json`, 120 dias; um "não" não apaga um "sim" do mesmo mês).
2. **▶ Assistir sem áudio PT** (`stream._apply_clean_layout`, passo 3): a chave de escolha ganhou `_sub_pen` antes da resolução: 0 = PT lida no arquivo, 1 = versão lembrada como "tem PT", 2 = sem PT ou sem saber. A descrição mostra "📝 Legenda PT-BR no próprio arquivo (sincronizada)" quando confirmada.
3. **Leitura em 2º plano:** sem áudio PT e com o ▶ Assistir sem PT embutido, lê até 3 outras versões prontas (uma por grupo/serviço; primeiro as lembradas e as de DSNP/NF/AMZN/CR/MAX/ATVP). `_want_subs` faz ler o cabeçalho mesmo quando o áudio já tinha sido lido. Versões já lidas ensinam a memória a cada abertura.
4. **Servidor de legendas** (`api/endpoints/subtitles.py`): se o arquivo (achado pelo `videoSize` no `media_info`) já tem PT embutido, devolve lista vazia ("🗒️ Legendas puladas (PT embutida no arquivo)").

## Verificado
- 3x1, 3x3, 3x4, 3x5, 3x6, 3x8, 3x10: ▶ Assistir vira 1080p com PT embutido (as 3x7/3x9 convergem nas aberturas seguintes).
- 3x2: nenhuma versão pronta tem PT embutido (todas lidas). Continua 4K + legenda externa.
- **TV:** 3x1 tocou a Disney+ VARYG (`f01f713b`) e o servidor pulou a externa. No 3x3 o Nuvio ligou sozinho "Embutida · Portuguese (Brazil)", com atraso 0 ms.

## Pegadinha do Nuvio (código `PlayerRuntimeControllerTracks.kt`)
- O Nuvio guarda por série a última legenda escolhida. Se era uma **externa** e a lista de externas vem **vazia**, ele trata como "ainda carregando" e **não liga a embutida** (fica "Nenhuma"). Escolher a embutida uma vez na série resolve dali em diante (feito no Bleach).
- O atraso manual é guardado por episódio (`saveSubtitleDelayMs(videoId)`).

## Cuidado em testes
- Passar pelo `/playback` conta como play: marca "fonte que falhou" (plays seguidos em menos de 60 s) e entra no `play_log` (gosto). Para ler faixas, use o `media_info`/`probe_full`, não o link de play. Em 19/11 isso foi limpo (`tt14986406:3:1` e 16 linhas do `play_log` entre 22:45 e 22:50).

## Falta (não feito)
- Episódio sem nenhuma versão com PT (3x2): dá para acertar a legenda externa usando a EN embutida do próprio arquivo como referência, ou traduzir a EN embutida direto. Custa ler o arquivo inteiro (~70 s para 1,7 GB em casa).
