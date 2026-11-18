# Tarefa A (10/11/2026): leva de melhorias e novidades

Pedido: "faça todos" (lista de 10 itens enviada ao usuário em 10/11).

| # | Item | Já existia? | O que fazer | Como verificar |
|---|---|---|---|---|
| 1 | Logo e etiqueta nas fileiras que faltam | não | `_land_url` reconhece o card embrulhado pela capa "ao vivo" (`/art/live.jpg?u=`); `streamings.land_enrich` monta o card com logo + serviço + etiqueta para Estreias da semana, Acabou de chegar dublado, Minha lista, Para hoje à noite e Natal | `rows.py`: `land` e `p` > 0 nessas fileiras |
| 2 | Etiqueta "Dublado" | sim, mas só quando não havia outra | selos combinados: "Novidade \| Dublado", "Novo episódio \| Dublado", "Nova temporada \| Dublado" e TOP 10 + Dublado | `b=new_dub` etc. nas fileiras; imagem abre |
| 3 | Rádio/TV com logo e gêneros | sim (favicon + gêneros) | nada | — |
| 4 | 1ª busca mais rápida | madrugada às 04:00 | 2ª rodada às 17:30, só com o próximo episódio das séries em andamento | log `🌆` |
| 5 | Próximo episódio pronto | sim (`prefetch`) | nada | — |
| 6 | Chegou nos streamings | não | fileira `comet_svnew` (filme e série): estreias da semana nos streamings do Brasil, com a data | catálogo com itens |
| 7 | Perfis | link infantil já existe | link de convidado (`GUEST_LINK_TOKEN`): tudo menos as fileiras pessoais | manifesto sem fileiras pessoais |
| 8 | Aviso de episódio novo | sim (Telegram, só dublado, 04:00) | texto "no Nuvio"; roda também às 17:30 | — |
| 9 | Podcasts | não | fileira de podcasts BR (iTunes + RSS), episódios tocam como a rádio (capa + áudio) | catálogo, meta e stream |
| 10 | Coleções automáticas | família e "maratonar num dia" já existem | "Filmes curtos" (até 1h30) e "Séries para o fim de semana" | catálogos com itens |
