# Tarefa B (17/11/2026): episódios errados e Apple TV+ com a Netflix

Pedido: "2 Broke Girls dublado abre outro episódio; Meu Nome é Farah dublado traz episódio de 40 min no lugar do de 2 h; o streaming da Apple TV traz coisas da Netflix".

(Puxar vídeos do RedeCanais foi recusado: site sem licença.)

| # | Problema | Causa | Conserto | Como verificar |
|---|---|---|---|---|
| 1 | 2 Broke Girls T4 dublado abre outro episódio | pacote BR com nomes `2.Garotas.Apuros.04.21.720p...` (TT.EE): o parser não lê, e o play chegou sem `media_id`, então a regra estrita não rodou e foi o 1º arquivo (04.16) | `stremthru.br_episode_hint` (TT.EE e "Episódio NN" com a temporada do pacote); play sem `media_id` usa o `ep_key`; sem id da série, temporada+episódio ainda exigem o arquivo certo | `generate_download_link` no pacote `ad95136f`: E21 → `04.21`, E22 → `04.22`, também sem id |
| 2 | Meu Nome é Farah: dublado de 40 min | o FenixFlix (link direto) e os torrents `NF` seguem o corte da Netflix (~40 min); o TMDB/Nuvio numera os episódios originais (~2 h) | `direct_addons`: mede o vídeo (ffprobe) e compara com a duração do episódio no TMDB (`data/direct_durations.json`, 30 dias); outro corte vira "⚠️ PT-BR · outro corte" no fim da lista. `stream._apply_clean_layout(ep_runtime=)`: arquivo sondado de outro corte fica fora do ▶ Assistir e dos atalhos, com aviso | `tt21874046:2:12` e `:1:2`: ▶ Assistir no episódio inteiro (Adim.Farah); NF com aviso |
| 3 | Apple TV+ (e Disney+, Paramount+) mostrando a Netflix | o Nuvio manda `genre=Apple TV+` e o `parse_qs` troca `+` por espaço; o nome não casa e cai no padrão (Netflix) | `catalog.py`: `+` literal preservado; `streamings._service_from_genre` casa sem pontuação | catálogo `comet_sv_top10s` com Apple TV+: Slow Horses, Ted Lasso, Silo |

| 4 | Diários de uma Apotecária T2 sem dublado | nenhum release da T2 diz "PT-BR" no nome; os "Multi-Audio"/"MULTi" da Crunchyroll têm a faixa PT-BR, mas só os marcados como PT eram sondados. (O dublado visto antes era o S03E01 DUAL-BiOMA.) | `stream._apply_clean_layout`: sem dublado confirmado, sonda até 2 releases Multi-Audio/MULTi/Dual (ignora "Multi Subs"); o laço sonda até 4. Achando PT, a próxima abertura mostra 🇧🇷 | `tt26743760:2:6`: 2ª abertura com ▶ Assistir 🇧🇷✔ PT-BR 1080p |
| 5 | "💎 Máxima qualidade" apontando para NCOP | o arquivo de abertura sem créditos casava com "01" | `_EXTRA_RE` (NCOP/NCED/creditless/trailer/preview/PV) fora dos atalhos e do ▶ Assistir | atalho no 2x6 é episódio |

Limite: arquivo ainda não sondado (sem duração) não é reconhecido como outro corte; a 1ª abertura de um episódio com link direto ainda não medido pode sair sem o aviso (a medição termina em 2º plano e vale para a próxima).
