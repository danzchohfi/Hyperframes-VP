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
