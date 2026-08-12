---
format: 1080x1920
duration: 70s
message: "O problema não é falta de sistema — é falta de visibilidade de ponta a ponta"
arc: PAS — qualificação → dor → virada → marca → demonstração → integração → síntese → CTA
audience: executivos B2B (CFO, diretores de compras e financeiro) de empresas maduras que já têm ERP
mode: autonomous
music: confident minimal tech underscore, discreet corporate electronic pulse, voice-dominant
language: pt-BR
---

## Video direction

- **Palette system** (de `frame.md`): frames de conteúdo em ground branco `bg`;
  `primary` (azul GOEVO #0099FF) é o único acento — eyebrows, numerais, barras,
  pills; headlines near-black `text`; corpo `text-muted`. Três exceções
  deliberadas de atmosfera (papel de capa/fechamento do preset): **Frame 3**
  roda em ground near-black monocromático (o momento preto e branco exigido
  pelo cliente — sem nenhum acento de cor); **Frames 4 e 9** rodam em ground
  gradiente azul profundo (#00489E→#0099FF, as cores oficiais GOEVO) com tipo
  branco e o logo branco oficial. A barra tricolor (vermelho/verde/laranja)
  aparece apenas dentro dos assets de logo e como micro-acento de status no
  mock de UI (chip "Aprovado ✓" usa `positive`); nunca como cor de texto.
- **Motion grammar + reveal model**: settles longos `power3` (suave, nunca
  bouncy; overshoot só no spring-pop pontual de badges); cada peça revela no
  momento em que a narração a nomeia — nada de front-load; reveals distribuídos
  pela metade final de cada frame. Durante holds, no máximo subtle jitter; nada
  de breathing ou pan/push tardio.
- **Rhythm / held frames**: Frame 3 (a virada) e Frame 8 (a síntese) são os
  beats de leitura parada — texto assenta e SEGURA (stillness antes do clímax
  e antes do CTA). Frame 4 é um sting curto de marca. Os demais desenvolvem
  reveals contínuos pautados pela voz.
- **Negative list**: nunca — slideshow (tudo em cena nos primeiros 25% e
  congela), screensaver (elementos flutuando independentes), breathing/loops
  infinitos, `Math.random`/`Date.now`, sombras em cards (só tint 4% + borda
  20%), cantos quadrados em conteúdo, logos de terceiros redesenhados (ERPs
  entram como chips de TEXTO), nomes/e-mails de clientes reais no mock,
  números de ROI/indicadores não presentes no roteiro (dados do mock são
  fictícios e genéricos), texto encostado na borda direita ou no rodapé
  (interface do Instagram) — conteúdo planejado no topo ~83% (caption band).

## Frame 1 — Etiqueta e qualificação

- scene: Etiqueta "Governança de Gastos" anima na abertura; a pergunta de capa "Sua empresa compra. Mas governa?" pontua; três conquistas da empresa madura (ERP, regras de aprovação, equipe experiente) assentam como pills com check
- voiceover: "Sua empresa já tem ERP, regras de aprovação e uma equipe de compras experiente."
- duration: 4.949s
- transition_in: cut
- status: animated
- src: compositions/frames/01-etiqueta-qualificacao.html
- type: hook
- persuasion: Qualificação do público — falar só com quem já tem estrutura
- beat: reconhecimento + tensão latente
- blueprint: kinetic-type-beats (Adapt)
- asset_candidates:
- focal: a pergunta de capa "Sua empresa compra. Mas governa?"
- sfx: whoosh-short, click-soft

narrativeRole: Filtra o público em 4 segundos (empresa madura, já tem tecnologia) e arma a tensão — a pergunta da capa insinua que ter não é governar.
keyMessage: Você já tem tudo isso — e mesmo assim algo escapa.

Adapt: mantém o motor de beats centrados do kinetic-type (sub-shape B); em vez
de beats que se substituem, os três itens da qualificação ACUMULAM como pills
— a linha de capa segura o centro como âncora enquanto a lista cresce embaixo.
Scene 1 (0.0–0.8s): ground branco; a etiqueta tag-pill "GOVERNANÇA DE GASTOS" desliza de cima para o terço superior com settle longo (`gsap-effects`), accent-line cobalt desenha embaixo dela (`stat-bars-and-fills` scaleX). Centered, densidade baixa.
Scene 2 (0.8–2.0s): a pergunta de capa "Sua empresa compra. Mas governa?" revela por palavra (per-word staggered reveal → `dynamic-content-sequencing`) como h2 near-black no centro; "governa?" recebe underline sweep cobalt (`css-marker-patterns` highlight) no instante em que assenta. Centered, ~55% da largura.
Scene 3 (2.0–4.1s): conforme a voz nomeia cada item — "ERP", "regras de aprovação", "equipe de compras experiente" — três tinted cards com check cobalt assentam um a um sob a pergunta (spring-pop suave → `spring-pop-entrance` registro smooth, stagger em cue de voz, não simultâneo). Coluna central, 3 camadas de profundidade (etiqueta / pergunta / pills).
Scene 4 (4.1–4.9s): held read — tudo assentado; no máximo subtle jitter (`sine-wave-loop` baixa amplitude) na etiqueta.

## Frame 2 — A dor fragmentada

- scene: E-mails, balões de WhatsApp e propostas espalhadas se acumulam e cercam o quadro; o "financeiro" enxerga só uma fatia do gasto através de uma janela estreita
- voiceover: "Mas as solicitações ainda chegam por e-mail e WhatsApp? As propostas ficam espalhadas? E o financeiro só enxerga parte do gasto quando o pedido já foi emitido?"
- duration: 9.451s
- transition_in: push-slide LEFT
- status: outline
- src: compositions/frames/02-dor-fragmentada.html
- type: pain_point
- persuasion: Pain validation — três perguntas retóricas que o gestor responde "sim" mentalmente
- beat: frustração + overwhelm
- blueprint: overwhelm-surround (Reproduce)
- asset_candidates:
- focal: o card do financeiro revelado sob o mock central (a morph do signature move)
- sfx: pop, riser

narrativeRole: Amplia a dor estrutural: pessoas e sistemas desconectados; o caos visual cresce a cada pergunta da narração.
keyMessage: O processo vive espalhado em e-mail, WhatsApp e planilhas — e o financeiro vê só o fim.

Reproduce: as quatro cenas canônicas do overwhelm-surround, com o morph
central (produto → pessoa) como signature move preservado; câmera estática —
o mundo cerca o sujeito.
Scene 1 (0.0–2.0s): "as solicitações ainda chegam por e-mail e WhatsApp?" — três mocks reconhecíveis assentam em stagger (janela de e-mail ao centro full-size, balão de WhatsApp e planilha flanqueando a ~0.86) (`spring-pop-entrance` smooth + float de baixa amplitude `sine-wave-loop`). Triptych no terço médio, ground branco.
Scene 2 (2.0–4.1s): "As propostas ficam espalhadas?" — cards de propostas/cotações (PDF, XLS, "Proposta_v3_final.pdf") espalham-se ao redor como density markers em stagger (`spring-pop-entrance`), alguns levemente rotacionados — a bagunça é o argumento. Densidade sobe para densa, 3 camadas.
Scene 3 (4.1–6.8s): "E o financeiro só enxerga parte do gasto..." — signature move: o mock central desbota o conteúdo, o container remodela (`card-morph-anchor` via scaleX/scaleY) e revela embaixo o card do FINANCEIRO (avatar + label) espiando por uma janela estreita — uma barra de gasto aparece cortada, só a fatia final visível (clip estreito). Centered, foco por `depth-of-field-blur` nos mocks vizinhos.
Scene 4 (6.8–9.5s): "...quando o pedido já foi emitido?" — bolhas de demanda ("Pedido emitido", "NF chegou", "Urgente!", "Quem aprovou?") fecham radialmente de todos os lados sobre o card (posições cos/sin pré-fixadas, entrada em stagger radial → `gsap-effects` + `spring-pop-entrance`); o card NÃO se move — cercado, não zoomed. Hold no estado lotado.

## Frame 3 — A virada em preto e branco

- scene: O quadro drena a cor para preto e branco; "O problema não é falta de sistema." assenta sozinho; "FALTA DE VISIBILIDADE" toma a tela em tipografia gigante, "de ponta a ponta" fecha embaixo
- voiceover: "O problema não é falta de sistema. É falta de visibilidade de ponta a ponta."
- duration: 4.587s
- transition_in: zoom-through
- status: animated
- src: compositions/frames/03-virada-visibilidade.html
- type: pain_point
- persuasion: Reenquadramento — nega o diagnóstico óbvio (falta de sistema) e nomeia o real
- beat: clareza + gravidade
- blueprint: kinetic-type-beats (Adapt)
- asset_candidates:
- focal: "FALTA DE VISIBILIDADE" em tipografia gigante
- sfx: impact-bass-1

narrativeRole: O momento conceitual do vídeo (exigência do cliente: destacar "falta de visibilidade" em preto e branco). Pausa cromática marca a tese.
keyMessage: Falta de visibilidade de ponta a ponta — essa é a tese do vídeo.

Adapt: sub-shape B (multi-beat statement build) do kinetic-type, reduzido a
dois beats graves em vez de barrage — o held read é o ponto; monocromático
integral (ground near-black, tipo branco, zero acento de cor).
Scene 1 (0.0–1.6s): ground near-black monocromático; "O problema não é falta de sistema." assenta centrado por palavra (per-word staggered reveal → `dynamic-content-sequencing`, settle `power3`), branco, h3 — sozinho na tela. Centered, silêncio ~70%.
Scene 2 (1.6–3.1s): no cue "É falta de visibilidade" a primeira linha sobe e apaga parcialmente (fade para 30%) enquanto "FALTA DE VISIBILIDADE" chega GIGANTE em duas linhas — letter-tracking aperta de largo para fechado enquanto escala (`gsap-effects` letter-spacing tween + scale), branco puro, ocupando ~70% da largura. Hierarquia por tamanho 4:1.
Scene 3 (3.1–4.6s): "de ponta a ponta" completa embaixo em eyebrow tracking largo (`dynamic-content-sequencing`); held read absoluto — stillness antes da revelação da marca; subtle jitter no máximo.

## Frame 4 — Conheça o GOEVO SCM

- scene: A cor retorna em azul GOEVO; o logo branco se forma no centro com a barra tricolor; "GOEVO SCM" assina a revelação
- voiceover: "Conheça o GOEVO SCM."
- duration: 1.877s
- transition_in: blur-crossfade
- status: animated
- src: compositions/frames/04-revelacao-goevo.html
- type: product_intro
- persuasion: Revelação da marca só depois da dor estabelecida
- beat: alívio + confiança
- blueprint: logo-assemble-lockup (Adapt)
- asset_candidates: assets/goevo-logo-branco.png — logo oficial branco com barra tricolor, para fundo azul
- focal: assets/goevo-logo-branco.png
- roles: goevo-logo-branco = cutout (herói central)
- sfx: sparkle

narrativeRole: Revelação da marca (~26s, como na referência: produto só aparece com a dor clara). O retorno da cor é o próprio alívio.
keyMessage: Existe um nome para a solução: GOEVO SCM.

Adapt: variante text-clear bloom do logo-assemble — sem beat de texto prévio
(o Frame 3 fez esse papel); o bloom do logo inteiro a partir do zero é o
signature move preservado. Frame estático, element-level.
Scene 1 (0.0–0.4s): o ground inunda em gradiente azul profundo (#00489E→#0099FF) — a cor retornando após o preto e branco é o beat emocional; um ambient glow suave pulsa uma vez atrás do centro vazio (`ambient-glow-bloom`).
Scene 2 (0.4–1.1s): no cue "Conheça o GOEVO" — o logo branco (asset) faz spring-BLOOM do zero no centro exato (escala 0→1 com settle crítico, leve rotação de -4°→0 → `spring-pop-entrance`, registro suave); o glow assenta atrás dele. Centered, logo ~62% da largura, 3 camadas (glow / logo / ground).
Scene 3 (1.1–1.9s): no cue "SCM" — um tag-pill branco translúcido "SCM · Supply Chain Management" pop no ponto inferior do lockup (`spring-pop-entrance` — o único overshoot do frame, pontuação merecida); hold limpo.

## Frame 5 — O fluxo de ponta a ponta

- scene: Fluxo horizontal animado atravessa a tela: Requisições → Cotações → Aprovações → Pedidos → Contratos → Fornecedores → Recebimentos → Notas Fiscais, estações conectadas por uma linha que se desenha; fecha em "um único processo"
- voiceover: "Uma plataforma que conecta requisições, cotações, aprovações, pedidos, contratos, fornecedores, recebimentos e notas fiscais em um único processo."
- duration: 9.216s
- transition_in: push-slide LEFT
- status: animated
- src: compositions/frames/05-fluxo-ponta-a-ponta.html
- type: feature_showcase
- persuasion: Show-don't-tell — o "ponta a ponta" da tese vira um desenho concreto
- beat: clareza + controle
- blueprint: spatial-pan-stations (Reproduce)
- asset_candidates:
- focal: o callout terminal "UM ÚNICO PROCESSO"
- sfx: click-soft, whoosh-short, chime

narrativeRole: Exigência do cliente: mostrar o fluxo completo do GOEVO em formato horizontal animado. Cada estação acende quando a narração a nomeia.
keyMessage: Oito etapas, um único processo conectado.

Reproduce: variante Hook-timeline do spatial-pan-stations aplicada ao fluxo do
produto — oito estações pré-posicionadas numa linha horizontal em um canvas
oversized, câmera virtual pana para a esquerda estação a estação (o signature
move), callout spring-pop em cada parada; terminal com o punchline.
Scene 1 (0.0–1.2s): ground branco; a linha do processo (fio cobalt fino, terço médio da tela) desenha-se à frente (`svg-path-draw`); a câmera `.world` abre na estação 1 — "REQUISIÇÕES" (ícone de formulário + label) com callout box spring-pop de origem no triângulo (`spring-pop-entrance` + `viewport-change` PAN).
Scene 2 (1.2–6.9s): a câmera PANA esquerda em ease-in-out parada a parada (`viewport-change` sequenciado por `multi-phase-camera`, alvo por `coordinate-target-zoom`), centrando cada estação NO CUE da narração: "COTAÇÕES" → "APROVAÇÕES" → "PEDIDOS" → "CONTRATOS" → "FORNECEDORES" → "RECEBIMENTOS" → "NOTAS FISCAIS" (~0.85s por estação); em cada parada o label revela (`discrete-text-sequence`) e a linha se estende à frente (`svg-path-draw`); a estação anterior desliza para fora do quadro. Full-width strip, ícones line-art cobalt.
Scene 3 (6.9–9.2s): no cue "um único processo" — pan final: a linha completa fecha num callout largo "UM ÚNICO PROCESSO" (tinted card com borda cobalt) que spring-pop centrado, os oito pontos das estações condensam-se em miniatura dentro dele; câmera trava e hold.

## Frame 6 — ERP registra, GOEVO governa

- scene: Split em dois painéis lado a lado: à esquerda o ERP (registro, transação); à direita o GOEVO como camada de governança cobrindo "antes, durante e depois" da decisão — os três tempos acendem em sequência
- voiceover: "Enquanto o ERP registra a transação, o GOEVO organiza e governa tudo o que acontece antes, durante e depois da decisão de compra."
- duration: 7.787s
- transition_in: crossfade
- status: outline
- src: compositions/frames/06-erp-e-goevo.html
- type: feature_showcase
- persuasion: Negative contrast sem atacar — o ERP continua essencial; o GOEVO ocupa o espaço vazio
- beat: entendimento + segurança
- blueprint: comparison-split (Adapt)
- asset_candidates: assets/goevo-logo-quadrado.png — ícone quadrado colorido da marca para o painel GOEVO
- focal: o card GOEVO (direita) com os três tempos
- roles: goevo-logo-quadrado = supporting (ícone no card direito)
- sfx: whoosh, click-soft

narrativeRole: Posiciona o produto sem ameaçar o ERP existente (tese do Roteiro: complemento, não substituição).
keyMessage: O ERP registra; o GOEVO organiza e governa antes, durante e depois.

Adapt: mantém o signature move (entrada em espelho das duas alas com tilt
book-open `rotateY`); muda a pontuação — em vez de um badge por card, TRÊS
badges de tempo assentam no card GOEVO, um por cue de voz. Em 9:16 os cards
empilham levemente sobrepostos na vertical do terço médio (aspecto do preset),
mantendo a leitura lado a lado.
Scene 1 (0.0–1.9s): "Enquanto o ERP registra a transação" — card ERP entra da ala esquerda com tilt +rotateY abrindo como livro (`split-tilt-cards`), tinted card com ícone de banco de dados + "ERP" + sub "Registra a transação"; glow ambiente cobalt fraco no lado esquerdo (`ambient-glow-bloom`). Split-screen 50/48.
Scene 2 (1.9–3.7s): "o GOEVO organiza e governa" — card GOEVO entra da ala direita em espelho (−rotateY, ~0.2s atrás do esquerdo por doutrina, aqui esticado ao cue), ícone quadrado da marca + "GOEVO SCM" + sub "Organiza e governa"; leve dominância de escala (1.06x) sobre o ERP.
Scene 3 (3.7–6.5s): "antes, durante e depois da decisão de compra" — três pill badges "ANTES" / "DURANTE" / "DEPOIS" spring-pop na borda interna do card GOEVO, um em cada palavra falada (~15% sobrepostos ao card; o único overshoot do frame) (`spring-pop-entrance`).
Scene 4 (6.5–7.8s): held; float idle em oposição de fase (esquerda sin(t), direita sin(t+π)) de amplitude mínima (`sine-wave-loop` subtle jitter).

## Frame 7 — Budget e aprovação na tela

- scene: Mock de dashboard de budget (dados fictícios): três medidores — Orçado, Comprometido, Realizado — contam até seus valores; chips de integração (TOTVS, SAP, OMIE, Senior, + outros) assentam; um card de aprovação desliza e recebe "Aprovado ✓"
- voiceover: "Com integração aos principais ERPs, regras personalizáveis e visão de orçado, comprometido e realizado, cada gestor sabe o que está acontecendo e o que precisa decidir."
- duration: 10.261s
- transition_in: crossfade
- status: animated
- src: compositions/frames/07-budget-aprovacao.html
- type: feature_showcase
- persuasion: Prova visual — a tela do produto como evidência, não tutorial
- beat: confiança + controle
- blueprint: dataviz-countup (Adapt)
- asset_candidates: assets/goevo-logo-quadrado.png — ícone da marca no header do mock de dashboard
- focal: os três metric cards Orçado / Comprometido / Realizado
- roles: goevo-logo-quadrado = supporting (header do mock)
- sfx: click, typing, chime

narrativeRole: Exigência do cliente: exibir o dashboard de budget e uma aprovação. Integrações em chips de texto (sem logos de terceiros redesenhados); dados fictícios, sem nomes de clientes.
keyMessage: Orçado, comprometido e realizado visíveis antes da decisão — integrado ao seu ERP.

Adapt: modo count-up do dataviz sem push-through de câmera (frame travado,
tratamento Dashboard do preset — a exceção densa); o count-up número+barra no
MESMO ease é o signature move preservado, triplicado nos três medidores; o
card de aprovação é o hero final.
Scene 1 (0.0–2.2s): "Com integração aos principais ERPs" — slide-header assenta no topo: eyebrow cobalt "INTEGRADO AO SEU ERP" + h3 "Governança em tempo real"; abaixo, chips de texto TOTVS · SAP · OMIE · Senior · +outros pop em stagger por cue (`spring-pop-entrance` suave). Mock de dashboard (card tinted grande, header com ícone da marca) já emoldura o terço médio, ainda vazio.
Scene 2 (2.2–3.9s): "regras personalizáveis" — um card de regras compacto revela dentro do mock: "Alçadas por valor · Centro de custo · Projeto" com toggles cobalt (`dynamic-content-sequencing`).
Scene 3 (3.9–7.4s): "visão de orçado, comprometido e realizado" — o hero: três metric cards empilham (9:16 → stacked), cada um NO SEU cue de palavra: "ORÇADO R$ 1,20 mi" / "COMPROMETIDO R$ 780 mil" / "REALIZADO R$ 460 mil" — numeral cobalt conta para cima com scale crescendo (`counting-dynamic-scale`) enquanto a barra cobalt enche no MESMO ease (`stat-bars-and-fills`); dados fictícios.
Scene 4 (7.4–10.3s): "cada gestor sabe o que está acontecendo e o que precisa decidir" — card de aprovação desliza de baixo (`spring-pop-entrance` suave): "Requisição #1042 · Materiais · R$ 12.500" com botão pill "Aprovar"; no fim do cue o chip flip para "Aprovado ✓" em `positive` (`discrete-text-sequence`); hold no dashboard completo.

## Frame 8 — Pare de reconstruir

- scene: Fundo limpo; "Pare de reconstruir o histórico dos gastos." assenta em duas batidas — "Pare" pesado primeiro, o resto da frase completa
- voiceover: "Pare de reconstruir o histórico dos gastos."
- duration: 2.581s
- transition_in: cut
- status: animated
- src: compositions/frames/08-pare-de-reconstruir.html
- type: benefit_highlight
- persuasion: Síntese imperativa — devolve a dor como ordem de mudança
- beat: urgência
- blueprint: kinetic-type-beats (Adapt)
- asset_candidates:
- focal: "Pare" em h1 pesado
- sfx: impact-bass-2

narrativeRole: Retoma a dor em uma linha antes do CTA (fórmula da referência: retomar a dor → próximo passo).
keyMessage: Chega de reconstruir o passado — governe no presente.

Adapt: statement-relay de UM statement (o low-tempo do kinetic-type): duas
batidas e um held read — o frame mais parado do vídeo depois do 3.
Scene 1 (0.0–0.7s): ground branco; "Pare" assenta sozinho, h1 near-black pesado, big→small scale-down suave (`spring-pop-entrance` registro smooth). Centered, silêncio ~70%.
Scene 2 (0.7–1.7s): "de reconstruir o histórico dos gastos." completa por palavra (`dynamic-content-sequencing`); no cue "reconstruir", um strike-through cobalt risca a palavra (`css-marker-patterns`) — parar de refazer é o gesto visual.
Scene 3 (1.7–2.6s): held read; subtle jitter no máximo.

## Frame 9 — CTA e missão

- scene: CTA "Clique no link" assenta; a missão "Visibilidade para comprar melhor e governar seus gastos." escreve-se como assinatura; o logo branco GOEVO fecha forte com "+20 anos de mercado"
- voiceover: "Clique no link e conheça uma nova forma de comprar melhor e governar seus gastos."
- duration: 4.757s
- transition_in: blur-crossfade
- status: animated
- src: compositions/frames/09-cta-missao.html
- type: cta
- persuasion: CTA único conectado ao funil (link na bio) + assinatura de autoridade
- beat: motivação + inevitabilidade
- blueprint: logo-assemble-lockup (Adapt)
- asset_candidates: assets/goevo-logo-branco.png — logo oficial branco para o encerramento com força
- focal: assets/goevo-logo-branco.png
- roles: goevo-logo-branco = cutout (lockup final)
- sfx: riser, whoosh-cinematic

narrativeRole: Um único próximo passo (exigência do cliente); encerra com a missão do Roteiro 1 e a marca limpa.
keyMessage: Clique no link — visibilidade para comprar melhor e governar seus gastos.

Adapt: CTA do logo-assemble em frame estático (sem push de câmera): CTA →
missão → bloom do lockup; o bloom do logo inteiro + hold longo é o signature
move preservado (lockup segura ~35% do runtime).
Scene 1 (0.0–1.3s): ground gradiente azul profundo (mesma família do Frame 4) com anéis concêntricos fracos de fechamento (atmosphere do preset); no cue "Clique no link" — um cta-button pill branco (texto azul) "Clique no link" spring-pop no centro superior (`spring-pop-entrance`, overshoot pontual).
Scene 2 (1.3–2.9s): "uma nova forma de comprar melhor e governar seus gastos" — a missão "Visibilidade para comprar melhor e governar seus gastos." escreve-se por palavra em branco, h3, abaixo do CTA (`dynamic-content-sequencing` com fade-through); os anéis expandem uma vez (`center-outward-expansion` sutil).
Scene 3 (2.9–4.8s): o logo branco GOEVO faz spring-BLOOM do zero no centro (`spring-pop-entrance` suave) tomando o quadro como herói; "+20 anos de mercado" fade discreto embaixo (`gsap-effects`); lockup HOLD estático até o fim — a marca fecha forte.
