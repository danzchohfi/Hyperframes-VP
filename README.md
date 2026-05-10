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
