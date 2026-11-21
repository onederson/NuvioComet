# Tarefa C (18/11/2026): imagem de uma fonte + áudio PT de outra (🎬)

Pedido: "reproduzir o título em 4K usando uma fonte e, ao mesmo tempo, a faixa de áudio PT-BR de outra" (o dublado muitas vezes só existe em resolução menor).

## Estado: pronto no servidor, **desligado** (`AV_MUX`, padrão falso)
Na Fire TV, o player do Nuvio congela no 1º quadro com qualquer HLS desse conteúdo, inclusive o HLS padrão gerado pelo ffmpeg sem nenhum ajuste nosso. Por isso a opção não aparece para o usuário.

## O que existe (`comet/services/av_mux.py`, `av_sync_calc.py`)
1. **Escolha do par** (`pick_pair`): dublada = 1ª PT entre as 3 primeiras; vídeo = maior resolução sem PT, pronta no debrid, em MKV.
2. **Medição em 2º plano** na 1ª abertura: compara o áudio das duas fontes em 20/50/80 % (mesmo idioma: forma de onda; idiomas diferentes: envelope de volume). Só aceita os 3 pontos com o mesmo deslocamento (até 0,15 s) e pico destacado (≥ 2). Numpy em `/opt/avsync` (venv separado). Resultado em `data/av_mux.json`.
3. **Opção "🎬 4K + 🇧🇷 áudio"** logo depois do ▶ Assistir, só em casa.
4. **Entrega HLS**: índice de quadros-chave lido do `Cues` do MKV (HTTP Range), um pedaço por quadro-chave; ffmpeg contínuo por sessão (vídeo copiado, áudio PT recodificado em AAC para o corte exato), fMP4 por pipe separado em `init.mp4` + `moof/mdat`. Reinicia a sessão ao pular. Tempos absolutos com `frag_discont` + `use_editlist 0` + `avoid_negative_ts disabled` + `output_ts_offset 10`.

## Medições
| Título | Resultado |
|---|---|
| Heartland (4K + PT 1080p) | encaixa, +0,02 s |
| Wish Dragon (4K + PT FenixFlix) | encaixa, −1,86 s |
| On the Waterfront, 2 Broke Girls 3x10, Apotecária 2x12 | não encaixam (outra montagem) |
| 2 Broke Girls 4x21 | encaixa, mas o atraso cresce nos intervalos (2,7 → 6,2 s): recusado |

- Sincronia conferida no áudio entregue (pedaços 200–202 do Wish Dragon): erro 0,000 s; Heartland 0,02 s (atraso do AAC).
- Velocidade: 1º pedaço em 6 a 10 s, depois 0,1 a 2 s por pedaço de 9 s.

## Lições (para não repetir)
- `ffmpeg -ss` recua ~0,13 s: mirar no quadro-chave exato cai no anterior. Mirar em +0,2 s.
- Com `-copyts`, `-t` de saída conta do zero: limitar nas entradas.
- Muxer `segment`/`hls` em TS: o ExoPlayer do Nuvio registra `PesReader: Unexpected start code prefix` (lê o cabeçalho PES 9 bytes adiante) no 1º PES de cada pedaço.
- Muxer HLS do ffmpeg em fMP4 zera os tempos a cada sessão (e por trilha); o muxer MP4 direto só mantém o tempo com `frag_discont`.
- Link direto do FenixFlix muda de caminho a cada pedido: identificar por servidor + nome do arquivo.

## Para continuar
1. Confirmar na TV se um HLS H.264 (sem HEVC) toca. O teste ficou inconclusivo: o "Continuar" do Nuvio reabre o endereço salvo e não pega a lista nova.
2. Se só o HEVC em HLS falha: testar entregar MKV contínuo (sem HLS), ou HLS com `hvc1`/`hev1` diferente.
3. Para testar na TV sem mexer nos ajustes: `data/avmux_first` com o id do título faz o 🎬 virar "▶ Assistir 🎬" (o Nuvio toca sozinho). Ligar a opção: `AV_MUX=True` no `.env`.
4. O próprio player do Nuvio tem "Atraso de áudio" (−60 a +60 s) no painel Áudio: serve para acertar na mão a faixa de uma mesma fonte.
