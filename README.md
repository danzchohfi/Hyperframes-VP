# HyperFrames Video Pipeline

Suba um vídeo, escolha um brand book, opcionalmente uma LUT, e o sistema:

1. **transcreve** com Whisper (OpenAI API)
2. **detecta silêncios** com `ffmpeg silencedetect`
3. **corta + aplica LUT** num único pass via `ffmpeg lut3d`
4. **monta uma composition Hyperframes** (intro + corpo + outro + legendas com destaque palavra-a-palavra)
5. **renderiza** em MP4 via `npx hyperframes render`
6. **reframa** pro aspecto que você pediu (9:16, 16:9, 1:1)

Tudo rodando local — backend FastAPI + frontend SPA estático.

## Arquitetura

```
server/
  main.py            # FastAPI: 23 endpoints sob /api
  storage.py         # ProjectState filesystem-backed
  services/
    ffmpeg.py        # probe / normalize / silencedetect / cut+LUT / reframe
    whisper.py       # OpenAI Whisper (verbose_json, word timestamps)
    silence.py       # plan_keep_segments
    brand.py         # BrandBook (paleta, tipografia, intro/outro)
    composer.py      # build_composition: gera index.html Hyperframes
    render.py        # invoca `npx hyperframes render`
  web/
    index.html app.js styles.css   # SPA single-page
  projects/<id>/     # cada projeto vive aqui (gitignored)
vendor/gsap.min.js   # GSAP local — usado nas compositions geradas
```

## Pré-requisitos

- Python 3.11+
- Node 22+ (pra rodar `npx hyperframes`)
- `ffmpeg` no PATH
- `OPENAI_API_KEY` para a transcrição

## Rodar

```bash
cp .env.example .env       # e cole sua chave
./run.sh                   # cria .venv, instala deps, sobe em http://127.0.0.1:8765
```

A SPA abre em `/`. Os endpoints da API ficam em `/api/*`.

### Testar do iPad/celular

**Mesma rede Wi-Fi:** rode `HOST=0.0.0.0 ./run.sh` no Mac e abra
`http://<ip-do-mac>:8765` no iPad. (Mac → Configurações → Wi-Fi → Detalhes.)

**Redes diferentes (iPad fora de casa, Mac em casa):** use o `tunnel.sh`,
que cria uma URL pública temporária via Cloudflare:

```bash
# Terminal 1 no Mac:
./run.sh

# Terminal 2 no Mac (em paralelo):
brew install cloudflared      # uma vez
./tunnel.sh
```

O `tunnel.sh` imprime uma URL `https://*.trycloudflare.com` — abra ela no Safari
do iPad. A URL existe enquanto o `tunnel.sh` estiver rodando; fecha no Ctrl-C.

## Workflow remoto: como puxar melhorias

O ciclo é: você roda local, eu (Claude) commito melhorias na branch
`claude/setup-hyperframes-DRZSw`, você puxa quando quiser.

```bash
./update.sh    # git pull --ff-only + reinstala deps
./run.sh       # bounce o server (uvicorn já tem --reload pra hot-reload do código)
```

`update.sh` aborta se você tiver mudanças locais não commitadas — pra não pisar
em código que você quer guardar. Use `git stash` ou `git status` se isso acontecer.

Seus dados em `server/projects/` e o `.env` ficam intocados (gitignored).

**Para pedir mudanças**, é só me mandar a mensagem aqui. Eu commito e dou push,
você roda `./update.sh`.

## Pipeline (chamadas equivalentes via curl)

```bash
# criar projeto
PID=$(curl -s -X POST localhost:8765/api/projects -H content-type:application/json \
  -d '{"name":"Reel #1"}' | jq -r .id)

# upload do source
curl -s -X POST localhost:8765/api/projects/$PID/upload -F file=@meu-video.mp4

# brand book
curl -s -X PUT localhost:8765/api/projects/$PID/brand -H content-type:application/json \
  -d '{"name":"Acme","intro_title":"Acme","intro_subtitle":"Conteúdo todo dia",
       "outro_text":"Obrigado por assistir!",
       "palette":{"primary":"#34d399","accent":"#a78bfa"}}'

# LUT opcional
curl -s -X POST localhost:8765/api/projects/$PID/lut -F file=@look.cube

# transcrição (Whisper)
curl -s -X POST localhost:8765/api/projects/$PID/transcribe

# detectar silêncios
curl -s -X POST localhost:8765/api/projects/$PID/cut-silences \
  -H content-type:application/json -d '{"noise_db":-32,"min_silence":0.5}'

# aplicar cortes + LUT (gera graded.mp4)
curl -s -X POST localhost:8765/api/projects/$PID/apply

# renderizar (intro + corpo + legendas + outro) em 9:16
curl -s -X POST localhost:8765/api/projects/$PID/render \
  -H content-type:application/json -d '{"aspect":"9:16","include_captions":true}'

# reframar (ex.: gerar uma versão 16:9)
curl -s -X POST localhost:8765/api/projects/$PID/export \
  -H content-type:application/json -d '{"aspect":"16:9"}'
```

## Brand book

```jsonc
{
  "name": "Acme",
  "tagline": "Built for creators",
  "intro_title": "Acme",
  "intro_subtitle": "Conteúdo diário",
  "outro_text": "Obrigado por assistir!",
  "caption_position": "bottom",        // bottom | center
  "caption_color": null,               // default: palette.foreground
  "caption_highlight": null,           // default: palette.accent
  "palette": {
    "primary":    "#a78bfa",
    "secondary":  "#3b82f6",
    "background": "#06060a",
    "foreground": "#ffffff",
    "accent":     "#f472b6"
  },
  "typography": {
    "title_family": "Inter", "title_weight": "600",
    "body_family":  "Inter", "body_weight":  "300"
  }
}
```

## Como o composer gera a composition

`server/services/composer.py` materializa um projeto Hyperframes completo
em `server/projects/<id>/composition/`:

- `index.html` com 3 segmentos no timeline:
  - intro card (paleta + título)
  - body video (`<video muted>` + `<audio>` separado, conforme convenção do framework)
  - outro card
- legendas em linhas de até 28 chars, com `<span class="cw">` por palavra
  e classe `.hot` aplicada por um listener `hf-seek` que casa o tempo atual
  com `data-w-start`/`data-w-end`.
- `vendor/gsap.min.js` é copiado pra dentro do projeto pra render funcionar offline.

## Exportação para Final Cut Pro (FCPXML)

Além do MP4 renderizado, o app exporta um **FCPXML 1.10** que abre direto no
Final Cut. As decisões de corte do silêncio viram clipes na timeline, e cada
palavra da transcrição vira um marcador navegável.

```bash
# single-cam (só o source.mp4)
curl -X POST localhost:8765/api/projects/$PID/export/fcpxml \
  -H content-type:application/json \
  -d '{"multicam":false,"use_cuts":true,"include_word_markers":true}'

# multicam (use depois de subir ângulos via POST /angles)
curl -X POST localhost:8765/api/projects/$PID/export/fcpxml \
  -H content-type:application/json \
  -d '{"multicam":true,"primary_angle":0}'
```

No Final Cut: **Arquivo → Importar → XML** → escolha o `.fcpxml`. Os caminhos de
arquivo são absolutos (`file:///...`), então o vídeo precisa existir no
mesmo lugar (no Mac que rodou o pipeline).

### Multicam: subir ângulos extras

```bash
# o source.mp4 é sempre o ângulo 1; angles extras vão pra angles/
curl -X POST localhost:8765/api/projects/$PID/angles \
  -F file=@close-up.mp4 -F name="Close"
curl -X POST localhost:8765/api/projects/$PID/angles \
  -F file=@drone.mp4 -F name="Drone"
```

Sincronização entre ângulos é responsabilidade do editor no Final Cut
(use *Sync Clips* ou markers de claquete). O FCPXML coloca todos com offset 0.

## Sugestão de música por IA + Epidemic Sound

O endpoint `/music/suggest` manda o texto da transcrição pra OpenAI e volta com
mood, gênero, BPM, instrumentos e palavras-chave de busca. Se você setar
`EPIDEMIC_SOUND_TOKEN` no `.env`, `/music/search` chama a API parceira da
Epidemic Sound e devolve faixas. Sem token, o app monta uma URL pré-preenchida
da busca pública pra você abrir manualmente.

```bash
curl -X POST localhost:8765/api/projects/$PID/music/suggest
# → {mood, genres, bpm_min, bpm_max, energy, instruments, description, keywords, epidemic_search_url}

curl -X POST localhost:8765/api/projects/$PID/music/search \
  -H content-type:application/json -d '{"limit":10}'
# → {provider, tracks: [{id, title, artist, bpm, preview_url, ...}]}

curl -X POST localhost:8765/api/projects/$PID/music/select \
  -H content-type:application/json -d '{"track":{"id":"...","title":"..."}}'
```

> A forma exata da API parceira da Epidemic Sound varia por contrato. O
> `EpidemicSoundProvider` em `server/services/music.py` parametriza endpoint
> e shape do retorno — ajuste `_endpoint`/`_params`/`_normalize` se a sua
> conta tiver outro formato.

## Inspirado no Eddie AI: soundbites, roteiro, rough cut

O pipeline tem o núcleo do que o [Eddie AI](https://www.heyeddie.ai/) faz pra
A-roll de entrevista — transcrição, soundbites, tópicos, framework de história
e rough cut — adaptado pra rodar local.

```bash
# 1. Whisper já fez a transcrição (etapa 4 do pipeline).

# 2. Remove muletas ("ãhh", "tipo", "né", "uhm"…)
curl -X POST localhost:8765/api/projects/$PID/cut-fillers \
  -H content-type:application/json -d '{"language":"pt"}'
# → {count, duration}

# 3. IA escolhe os melhores trechos e agrupa por tópico
curl -X POST localhost:8765/api/projects/$PID/soundbites
# → {soundbites: [{id, start, end, text, topic, score, summary}, ...],
#    topics: [{id, name, summary, soundbite_ids}, ...]}

# 4. IA propõe estrutura de roteiro
curl -X POST localhost:8765/api/projects/$PID/story \
  -H content-type:application/json \
  -d '{"structure":"four_act"}'   # ou hero_journey | explainer | testimonial | before_after
# → {title, logline, chapters: [{id, name, summary, soundbite_ids, transition_note}, ...]}

# 5. Gera o rough cut (MP4 só com os trechos do roteiro, na ordem)
curl -X POST localhost:8765/api/projects/$PID/roughcut \
  -H content-type:application/json \
  -d '{"use_story":true, "apply_lut":true}'
# → roughcut.mp4 + chapter_markers
```

A etapa `apply` (cortes do silêncio + LUT) automaticamente subtrai os ranges
de muletas se você rodou `cut-fillers` antes. É um único pass do ffmpeg.

## B-roll com computer vision

Pra cada ângulo extra (multicam) ou o próprio source, dá pra rodar
auto-tagging via GPT-4o vision:

```bash
curl -X POST localhost:8765/api/projects/$PID/angles/0/tag
# → {description, summary, tags, categories, quality, has_people}
```

Sample 4 frames distribuídos no clip → analisa → grava no `angles[].tags`.
Os tags ficam na própria UI ao lado do nome do ângulo.

## Export pra Premiere Pro / DaVinci Resolve

Além do FCPXML (Final Cut), o sistema exporta **xmeml** (Final Cut Pro 7 XML)
que tanto Premiere quanto Resolve importam diretamente:

```bash
# usar o plano de cortes do silêncio
curl -X POST localhost:8765/api/projects/$PID/export/premiere \
  -H content-type:application/json -d '{"use_cuts":true}'

# ou usar o rough cut (do roteiro)
curl -X POST localhost:8765/api/projects/$PID/export/premiere \
  -H content-type:application/json -d '{"use_roughcut":true}'
```

No Premiere: **Arquivo → Importar → seleciona o .xml**. No Resolve: **File →
Import → Timeline → AAF/EDL/XML**. Os clipes vêm com in/out points e referência
ao `source.mp4` por path absoluto.

## Refinamentos recentes (Eddie × Hyperframes)

### Progresso ao vivo via SSE
A UI escuta `GET /api/projects/{id}/events` e recebe stage transitions e
mensagens em tempo real. Cards mudam de cor sozinhos, e um toast no canto
mostra o evento atual.

### Loudnorm + denoise no áudio
Toggles no card "Pipeline → Aplicar edição" e no rough cut. Aplica
`loudnorm=I=-14:LRA=11:TP=-1.5` (target social media) e `afftdn` num único
filtro complex.

### B-roll contextual
Depois de taggear ângulos via `🏷 Tag IA`, clique em `🎯 Posicionar B-roll
automaticamente`. A IA decide qual ângulo cobre cada soundbite, com in/out
points sugeridos. Ângulos com `quality != "ok"` são pulados.

```bash
curl -X POST localhost:8765/api/projects/$PID/place-broll
# → {placements: [{soundbite_id, angle_index, angle_in, angle_out,
#                  timeline_offset, timeline_duration, score, reason}], ...}
```

Os placements alimentam:
- FCPXML multitrack (lane="1" connected clip)
- Premiere/Resolve XML (segunda video track)

```bash
curl -X POST localhost:8765/api/projects/$PID/export/fcpxml \
  -H content-type:application/json \
  -d '{"include_broll":true,"use_roughcut":true}'

curl -X POST localhost:8765/api/projects/$PID/export/premiere \
  -H content-type:application/json \
  -d '{"include_broll":true,"use_roughcut":true}'
```

### Smart-crop reframing
Em vez de letterbox, o sistema detecta a posição horizontal do subject via
GPT-4o vision (mediana de 5 frames) e faz crop centrado nele. Bom pra
talking heads em vertical.

```bash
curl -X POST localhost:8765/api/projects/$PID/smart-reframe \
  -H content-type:application/json \
  -d '{"aspect":"9:16","use_roughcut":true}'
```

### SRT / VTT
```bash
curl -X POST "localhost:8765/api/projects/$PID/export/captions?fmt=srt"
curl -X POST "localhost:8765/api/projects/$PID/export/captions?fmt=vtt&use_roughcut=true"
```

### Caption styles
No BrandBook: `caption_style: minimal | tiktok | podcast`. Aplicado tanto na
preview Hyperframes quanto no render final:
- `minimal` — branca, peso 700, sombra discreta
- `tiktok` — uppercase 900, scale 1.15 na palavra ativa, contorno preto duplo
- `podcast` — peso 600, lowercase, chip semi-transparente com blur

### Render: roughcut + chapter cards
O endpoint `/render` agora aceita `source: "graded" | "roughcut" | "source"`
e `include_chapter_cards: true`. Quando ligado, cada capítulo do roteiro
ganha um cartão flash-in com nome + número, sobreposto ao corpo do vídeo.
A transcrição é re-timed automaticamente quando a fonte é o rough cut.

### Snapshots
Salve "fotos" do estado de cuts/soundbites/story/brand pra comparar versões.

```bash
curl -X POST localhost:8765/api/projects/$PID/snapshots \
  -H content-type:application/json -d '{"label":"v1 — antes do feedback"}'
curl localhost:8765/api/projects/$PID/snapshots
curl -X POST localhost:8765/api/projects/$PID/snapshots/<snap_id>/restore
```

### Brand presets
Salve um BrandBook como preset reutilizável em todos os projetos.

```bash
curl -X POST localhost:8765/api/brand-presets \
  -H content-type:application/json \
  -d '{"name":"Acme Corp","brand":{"palette":{"primary":"#0aa"}}}'
curl localhost:8765/api/brand-presets
curl -X POST localhost:8765/api/projects/$PID/brand/from-preset/acme-corp
```

### Speaker turns (heurístico)
```bash
curl -X POST "localhost:8765/api/projects/$PID/speakers?gap=1.2"
# → {turns: [{speaker:"A", start, end, text, word_count}, ...]}
```
Quando alternam pausas longas, alterna A/B. Não é diarização real, mas é o
suficiente pra entrevista um-a-um. Plugar pyannote.audio é o próximo passo.

### Chapter thumbnails
```bash
curl -X POST localhost:8765/api/projects/$PID/chapter-thumbs
# → grava thumbs/chapter_NN.jpg pegando o frame logo após o início do
#   primeiro soundbite de cada capítulo. UI mostra um grid clicável.
```

### Bundle export (.zip)
Empacota tudo (planos, MP4s, FCPXML/xmeml/SRT/VTT, brand.json, LUT, ângulos)
num zip pra mover entre máquinas / arquivar.
```bash
curl -X POST localhost:8765/api/projects/$PID/export/bundle
```

### Duplicar projeto
```bash
curl -X POST localhost:8765/api/projects/$PID/duplicate
# clona transcript + cuts + soundbites + story + brand + media. Renders não.
```

## Wave 3 — text-driven editing + reels + templates

### Render progress em tempo real
O endpoint `/render` agora streamea progresso (parsing do stdout do
`hyperframes render`). A UI pega via SSE e mostra `Capturing frame 90/240
(37%)` enquanto roda — em vez de só "running".

### Transcrição interativa (estilo Descript / Eddie)
Card "4b · Transcrição interativa". Cada palavra é clicável:
- **Click** → vídeo pula pra aquele timecode + começa a tocar
- **Shift-click** → estende seleção até essa palavra
- **Cmd/Ctrl-click** → adiciona/remove a palavra da seleção
- A palavra atualmente tocando recebe destaque automaticamente
- **✓ Manter seleção como cuts** → constrói `cuts.json` a partir das palavras
  marcadas. Sobrescreve qualquer plano de silêncio, vira o "manual cut".

```bash
curl -X POST localhost:8765/api/projects/$PID/cut-from-words \
  -H content-type:application/json \
  -d '{"keep":[{"start":1.0,"end":3.5},{"start":7.2,"end":9.0}],"pad":0.05}'
```

### Highlights reel
Auto-monta um teaser dos top soundbites totalizando ~30s:
```bash
curl -X POST localhost:8765/api/projects/$PID/highlights \
  -H content-type:application/json \
  -d '{"target_seconds":30,"apply_lut":true}'
# → highlights.mp4 + highlights.json
```

### Social copy (caption, hashtags, hook)
Gera copy pronto pra Instagram/TikTok/YouTube:
```bash
curl -X POST localhost:8765/api/projects/$PID/social-copy \
  -H content-type:application/json -d '{"language":"pt"}'
# → {hook, caption, long_caption, hashtags, thumbnail_title,
#    youtube_title, youtube_description}
```
UI tem botão "copiar" em cada campo.

### Templates Hyperframes
4 presets prontos (BrandBook + caption_style + aspect + chapter cards):

| ID | Aspect | Caption | Chapter cards |
|---|---|---|---|
| `talking_head_vertical` | 9:16 | tiktok | — |
| `podcast_horizontal` | 16:9 | podcast | sim |
| `tutorial_clean` | 9:16 | minimal | sim |
| `testimonial_square` | 1:1 | minimal (centro) | — |

```bash
curl localhost:8765/api/templates
curl -X POST localhost:8765/api/projects/$PID/template \
  -H content-type:application/json -d '{"template_id":"podcast_horizontal"}'
```

### Whisper com idioma explícito
```bash
curl -X POST localhost:8765/api/projects/$PID/transcribe \
  -H content-type:application/json -d '{"language":"pt"}'
```

## Wave 4 — UX polish

### Visual timeline of cuts
Logo abaixo do preview, uma faixa SVG mostra toda a duração do source com:
verde = ranges mantidos, vermelho = silêncios, âmbar = muletas. Atualiza
toda vez que `cuts.json` ou `fillers.json` muda.

### Stale-state warnings
`GET /api/projects/{id}` agora retorna `stale: {graded_vs_cuts,
soundbites_vs_transcript, story_vs_soundbites, roughcut_vs_story}`. A UI
mostra avisos âmbar quando algum artefato está desatualizado.

### Stage progress bar
Cada stage card recebe uma barrinha animada. O endpoint `/render` parse os
percentuais do `hyperframes render` e emite `progress` no SSE → barra
preenche em tempo real.

### Cancel render
Botão `⏹ Cancelar` aparece quando o render está ativo. Manda `SIGTERM` no
processo `npx hyperframes render` rodando.
```bash
curl -X POST localhost:8765/api/projects/$PID/render/cancel
curl localhost:8765/api/projects/$PID/render/status
```

### Audio-only export
MP3 / M4A / WAV a partir de qualquer fonte (graded, roughcut, source,
highlights). Bom pra publicar podcast ou exportar pro CapCut/Premiere
separadamente.
```bash
curl -X POST localhost:8765/api/projects/$PID/export/audio \
  -H content-type:application/json \
  -d '{"format":"mp3","source":"roughcut"}'
```

### Custom filler list
No card "Pipeline → Remover muletas", input de texto pra adicionar
muletas customizadas (ex.: `ah,ehh,sabe`). Adicionadas à lista padrão
do idioma.

## Wave 5 — captions burned in, ASS karaoke, server search

### Burned-in captions (sem precisar do Hyperframes render)
Caminho rápido pra Reels/TikTok: gera ASS com `\\kf` (karaoke por palavra)
+ chama `ffmpeg -vf ass`. Sai um MP4 já com legenda animada queimada.

```bash
curl -X POST localhost:8765/api/projects/$PID/export/burn-captions \
  -H content-type:application/json \
  -d '{"source":"roughcut","style":"tiktok"}'
```
Estilos: `tiktok` (uppercase + outline), `minimal` (branco com sombra),
`podcast` (chip discreto).

### ASS / SRT / VTT
```bash
curl -X POST "localhost:8765/api/projects/$PID/export/captions?fmt=ass&style=tiktok"
curl -X POST "localhost:8765/api/projects/$PID/export/captions?fmt=srt&use_roughcut=true"
curl -X POST "localhost:8765/api/projects/$PID/export/captions?fmt=vtt"
```

### Multi-project soundbite search
```bash
curl "localhost:8765/api/search?q=marketing&limit=20"
# → [{project_id, project_name, soundbite: {id, start, end, text, score, ...}}, ...]
```
Search bar na sidebar busca em tempo real (debounced).

### Server stats
```bash
curl localhost:8765/api/stats
# → {projects, soundbites, renders, presets, bytes_used, disk_total, disk_free, version}
```
Renderizado no rodapé da sidebar.

## Wave 6 — hook, peak thumb, emoji captions, render history

### Hook detection
Pega o melhor trecho de ~4s pra usar como abertura (Reels/TikTok hook):
```bash
curl -X POST localhost:8765/api/projects/$PID/hook \
  -H content-type:application/json -d '{"target_seconds":4}'
# → {start, end, duration, url}
```

### Peak thumbnail
Pega um frame de alta qualidade no momento do soundbite com maior score:
```bash
curl -X POST localhost:8765/api/projects/$PID/peak-thumbnail
# → thumbs/peak.jpg @ 1280px
```

### Emoji nas legendas (TikTok-style)
Decora a transcrição com emojis relevantes (LLM com fallback heurístico
local pra português + inglês):
```bash
curl -X POST localhost:8765/api/projects/$PID/captions/emojify \
  -H content-type:application/json -d '{"use_llm":true}'
```
Funciona em quem usar `export/captions` ou `export/burn-captions` depois —
os emojis ficam embutidos no texto da legenda.

### Render history
Cada export agora é registrado em `history.json`. UI mostra os 30 mais
recentes no card de exportação:
```bash
curl localhost:8765/api/projects/$PID/history
# → [{name, kind, url, bytes, ts, ...extra}, ...]
```

## Wave 7 — speakers, waveform, archive

### Speaker-aware captions
Quando `speakers.json` existe (rode `🎤 Detectar falas` antes), a exportação
de legendas pode prefixar cada cue com `[Speaker A]` / `[Speaker B]`:
```bash
curl -X POST "localhost:8765/api/projects/$PID/export/captions?fmt=srt&speaker_labels=true"
```

### Audio waveform
SVG com peaks RMS desenhado embaixo da timeline visual. Ajuda a achar
silêncios e momentos altos rapidamente.
```bash
curl "localhost:8765/api/projects/$PID/waveform?buckets=600"
# → {duration, buckets, peaks: [0..1, ...]}
```

### Arquivar projeto
Empacota tudo em zip (em `server/projects/_archive/`) e remove o projeto
da lista. Útil quando ficou pesado no disco.
```bash
curl -X POST localhost:8765/api/projects/$PID/archive
```

## Waves 34-42 — speech-aware cuts + UI moderna

### Cortes que respeitam a fala
`services/speech_cuts.py` arredonda qualquer timestamp pra fronteira de
palavra ou frase (segment do Whisper + pausa ≥ 0.35s após pontuação).
Aplicado em todos os pontos onde a gente corta:
- `cut-silences` agora descarta silêncios cujo meio cai dentro de uma palavra
  e snap cada range mantido pra borda de palavra
- `roughcut` snap a partir dos soundbites
- `highlights` e `hook` snap antes do encoding
- `vlog/assemble` snap por clipe pra fronteira de frase

### Vlog 1-click renderiza o MP4 final
`POST /vlog/auto-pipeline {auto_assemble: true}` (default true) agora não para
nas narrativas — pega a top, chama `vlog_assemble.run` inline, gera o MP4
brandado e os assets sociais, e retorna a URL final. `aspect: "auto"` faz
o orquestrador olhar a orientação dos clipes e escolher 9:16, 16:9 ou 1:1.

### Crossfade entre clipes de vlog
`POST /vlog/assemble {crossfade: 0.5}` faz um xfade de 0.5s entre cada bite
do vlog (vídeo via ffmpeg `xfade`, áudio via `acrossfade`).

### UI moderna
- Sistema de design completo: tokens CSS para dark (padrão) e light theme,
  shadows em 3 níveis, easing tokens, fonte Inter variável.
- Toggle de tema persistido em localStorage (sem FOUC).
- **Command palette ⌘K** com ~50 ações cobrindo todos os endpoints.
- Hero animado com 3 cards de modo (Podcast / Vlog / Multicâmera).
- Strip de progresso no header do projeto: cada etapa vira chip; a próxima
  pulsa em accent + botão primário "Next step" sugere a ação certa.
- Confirm dialog brandado substitui o `confirm()` nativo.

## Waves 18-32 — multicam inteligente + modo vlog

### Multicam inteligente

```bash
# 1. Suba câmeras (cada upload roda quality + face_analysis automaticamente)
curl -F file=@cam_a.mp4 -F name=Wide   localhost:8765/api/projects/$PID/angles
curl -F file=@cam_b.mp4 -F name=Marina localhost:8765/api/projects/$PID/angles
curl -F file=@cam_c.mp4 -F name=Lucas  localhost:8765/api/projects/$PID/angles

# 2. Sync de áudio (cross-correlation FFT entre source e cada câmera)
curl -X POST localhost:8765/api/projects/$PID/multicam-sync

# 3. Cluster de identidades (quem aparece em qual câmera)
curl -X POST localhost:8765/api/projects/$PID/face-identities
curl -X POST localhost:8765/api/projects/$PID/face-identities/thumbnails
# Renomeia: PUT /face-identities/person_1 {"name":"Marina"}

# 4. Se rodou diarização (/speakers), mapeia speaker → cluster
curl -X POST localhost:8765/api/projects/$PID/speakers/map-to-faces
# → {"A":"person_1","B":"person_2"}

# 5. Linha de tempo do sujeito por câmera (detecta quando sujeito muda dentro do clipe)
curl -X POST localhost:8765/api/projects/$PID/subject-timeline?target=source

# 6. Escolha automática de câmera por turno (split em mudanças de sujeito,
#    bonus +4 pra câmera que mostra o speaker, penalidade pra quem não mostra)
curl -X POST localhost:8765/api/projects/$PID/multicam-pick \
  -d '{"intervals":"turns","split_on_subject_change":true}'

# 7. Renderiza o multicam final (vídeo de cada turno vem da câmera escolhida,
#    áudio sempre do source)
curl -X POST localhost:8765/api/projects/$PID/multicam-render
```

Cada ângulo/clipe ganha automaticamente:
- `face_analysis: {face_presence, face_area_avg, face_x_avg, shot_type, subject_change}`
- `quality_check: {quality, blur_score, shake_score, brightness}` (OpenCV)
- `face_clusters: ["person_1", ...]` depois de `/face-identities`

A UI mostra: `close up` / `medium` / `wide` / `no face` + `⇄ sujeito mudou`
+ `⚠ shaky/blurry/dark` + thumbs de cada pessoa pra renomear inline.

### Modo Vlog (vários clipes → narrativa → vlog brandado)

```bash
# 1. Sobe N clipes (cada um normaliza + thumbnail automática)
for f in vlog_*.mp4; do
  curl -F file=@$f -F name=$(basename $f .mp4) \
    localhost:8765/api/projects/$PID/clips
done

# 2. 1-click: transcribe → face cluster → narrativas (devolve job_id)
curl -X POST localhost:8765/api/projects/$PID/vlog/auto-pipeline \
  -d '{"language":"pt","aspect":"9:16","apply_brand":true}'

# Ou passo-a-passo:
curl -X POST localhost:8765/api/projects/$PID/clips/transcribe-all
curl -X POST localhost:8765/api/projects/$PID/face-identities
curl -X POST localhost:8765/api/projects/$PID/vlog/group-takes  # detecta retakes
curl -X POST localhost:8765/api/projects/$PID/vlog/cull-takes   # esconde retakes piores
curl -X POST localhost:8765/api/projects/$PID/vlog/narratives   # 3-5 narrativas

# 3. Pra cada narrativa: storyboard, B-roll, edit, render
curl -X POST localhost:8765/api/projects/$PID/vlog/narratives/n1/storyboard
curl -X POST localhost:8765/api/projects/$PID/vlog/place-broll?narrative_id=n1
curl -X PUT  localhost:8765/api/projects/$PID/vlog/narratives/n1 \
  -d '{"sequence":[{"clip_id":"clip_xxx","start":0,"end":5,"reason":"intro"}]}'

# 4. Monta o vlog final
curl -X POST localhost:8765/api/projects/$PID/vlog/assemble \
  -d '{"narrative_id":"n1","aspect":"9:16","apply_brand":true,"chapter_cards":true}'
# → vlog-n1.mp4 + social_copy.json (caption/hashtags/hook auto-gerados)

# 5. Música pro vlog (mood baseado em todas as transcrições)
curl -X POST localhost:8765/api/projects/$PID/vlog/music-suggest?language=pt
```

A UI mostra:
- Cada clipe com thumb 480px + badges de pessoas presentes + `✓ txt`
- Cards de narrativa com **timeline SVG** mostrando cada bite (cada bloco
  pinta a thumbnail do clipe, com número e duração)
- `📰 Storyboard` gera 1 painel por bite a 25% da janela
- `🎯 B-roll` pede pra IA matchear ângulos tagged como B-roll inserts
- `✍ Editar narrativa` PUT pra reordenar/cortar bites manualmente
- `🎬 Montar este vlog` → renderiza com brand + chapter cards + social copy auto

### Templates Vlog
- `vlog_vertical` (9:16, captions TikTok, palette rosé/cyan/amber)
- `vlog_horizontal` (16:9, captions minimal, palette lime/cyan)

## Waves 9-17 — virando editor de podcast a sério

### Pipeline 1-click (`POST /podcast-pipeline`)
Transcribe → diarize → chapters → silence cut → filler removal →
LUT/loudnorm → per-speaker level → social copy, num único job
assíncrono. UI mostra barra de progresso ao vivo via SSE.

### Saídas prontas pra publicar

| Asset | Endpoint |
|---|---|
| **MP4 final brandado** (intro/legendas/lower-thirds/logo/CTAs) | `POST /render` |
| **Rough cut do roteiro** | `POST /roughcut` |
| **N shorts 9:16** com brand aplicado per-clip | `POST /shorts/batch` |
| **Highlights reel** de 30s | `POST /highlights` |
| **Hook 4s** pro opening de Reel | `POST /hook` |
| **MP4 com legenda queimada** (TikTok/podcast/minimal style) | `POST /export/burn-captions` |
| **SRT / VTT / ASS karaoke** | `POST /export/captions` |
| **MP3 com chapters markers + ID3** | `POST /export/podcast-mp3` |
| **RSS XML pra Apple/Spotify** | `POST /export/podcast-rss` |
| **FCPXML / xmeml multitrack** (V1 A-roll + V2 B-roll inserts) | `POST /export/fcpxml` / `/export/premiere` |
| **Thumbnail YouTube 1280x720** com text overlay | `POST /yt-thumbnail` |
| **Bundle .zip** com tudo | `POST /export/bundle` |
| **Publishing bundle** (título/caption/hashtags/chapters/URLs num só JSON) | `GET /publishing-bundle` |

### Brand book vira visual

`BrandBook` aplica:
- Paleta + tipografia em intro/outro/captions/chapter cards
- 3 caption styles: minimal, tiktok (uppercase + scale-on-hot), podcast (chip)
- **Logo watermark** num dos 4 cantos
- **Lower-thirds** (`speakers["A"] = {name, role, color}`) pinada por turno
- **CTA cards** em tempos definidos
- Cores diferentes de legenda por speaker (auto ou via brand.speakers)

### Workflows técnicos por baixo

- **Diarização**: pyannote (se HF_TOKEN) → MFCC k-means (numpy/scipy) → adaptive gap
- **Multicam sync**: cross-correlation FFT entre source e cada ângulo, offsets em segundos vão pro FCPXML multicam
- **Qualidade B-roll**: OpenCV (Laplacian variance, mean brightness, inter-frame absdiff). Roda automático em uploads. Match contextual pula clips ruins.
- **Nivelamento por speaker**: ffmpeg volumedetect mede dBFS por turno; gain por speaker pra hit -18 LUFS; filter chain `volume=enable='between(t,a,b)':volume=NdB`
- **Repetições**: detecta "X X X" + n-gram repeated dentro de uma janela
- **Job queue assíncrono** com SSE pra progresso ao vivo + cancellation

## Waves 9-11 — virando editor de podcast a sério

Quando pediram pra fechar o gap com o Eddie pra **podcast**, o sistema ganhou:

**Diarização real** (`POST /speakers`)
- 3 backends, escolhidos por ordem de disponibilidade:
  1. `pyannote.audio` (set `HF_TOKEN` + `pip install pyannote.audio`)
  2. MFCC + k-means fallback (numpy/scipy)
  3. Heurística adaptativa por P90 dos gaps
- Retorna `{backend, turns, stats: {speaker_count, by_speaker: {A: {talk_time, share, words}, B: {...}}}}`

**Audio sync multicam** (`POST /multicam-sync`)
- Cross-correlation FFT entre source + cada ângulo, detecta offset em segundos
- O offset vai pro FCPXML multicam automaticamente

**Capítulos por mudança de tópico** (`POST /chapters`)
- Linear, não destrutivo, devolve `youtube_markdown` pronto pra colar no YouTube

**Legendas por speaker** (`BrandBook.speakers = {"A": {color: "#fbbf24"}, "B": {color: "#06b6d4"}}`)
- Cada linha vira `data-speaker="A|B"`, recebe cor específica, e opcionalmente um chip `[Host]`

**Normalização por speaker** (`POST /speaker-levels`)
- Mede `mean_volume` por turno via `volumedetect`, calcula gain pra hit -18 dBFS,
  aplica `volume=enable='between(t,a,b)':volume=NdB` por turno num só pass

**Detecção de qualidade OpenCV** (`POST /angles/{i}/assess-quality`)
- Laplacian variance (blur), mean brightness (escuro), inter-frame absdiff (shake)
- Roda automaticamente em todo angle uploadado
- B-roll matching pula clips com `quality != ok`

**Job queue assíncrona** (`POST /render/async`, `/roughcut/async`)
- In-process, persiste em `jobs.json`, eventos via SSE, `/jobs/{id}/cancel`

**Pipeline 1-click pro podcast** (`POST /podcast-pipeline`)
- Transcribe → diarize → chapters → silences + fillers → apply (LUT + loudnorm) → speaker levels → social copy num único job
- UI tem barra de progresso ao vivo + idioma override

**YouTube description bundle** (`POST /export/youtube-description`)
- Junta `social_copy.youtube_description`, hashtags, chapters em `mm:ss Name`,
  e share de fala por speaker num único `.txt`

**Templates Podcast**
- `podcast_horizontal` (16:9, chapter cards, speaker colors Host/Convidado)
- `podcast_clip_vertical` (9:16, captions TikTok, A/B amarelo/violeta)

## Wave 8 — música com ducking

Suba uma trilha sonora e o sistema mixa com a voz aplicando ducking
(sidechaincompress) — a música abaixa quando o speaker fala.

```bash
curl -X POST -F "file=@trilha.mp3" \
  localhost:8765/api/projects/$PID/music/upload

curl -X POST localhost:8765/api/projects/$PID/music/mix \
  -H content-type:application/json \
  -d '{"source":"roughcut","music_db":-8}'
```

A música é auto-loopeada quando mais curta que o vídeo, atenuada pra
`music_db` (default -8 dB), e abaixada via sidechain quando a voz passa de
threshold. Saída como `*-with-music.mp4`.

## Limitações conhecidas

- Whisper é chamado uma vez por projeto, sem chunking — vídeos > 25MB precisam
  ser fatiados antes (próximo passo).
- Sem fila assíncrona — cada `POST` bloqueia até o ffmpeg/render terminar.
  Vídeos longos vão derrubar o timeout do navegador. SSE/jobs são o próximo passo.
- A reframagem 9:16 ↔ 16:9 hoje é letterbox; smart-crop (rosto centralizado)
  ficou pra depois.

## Composition de demonstração

A composition original `index.html` na raiz é a demo cinematográfica
"Jessica, eu te amo!" — independente do pipeline, serve como referência
de como uma composition Hyperframes manual fica.
