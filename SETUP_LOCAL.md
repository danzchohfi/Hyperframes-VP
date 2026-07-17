# Rodando as edições localmente (Mac)

Por que local é muito mais rápido: na nuvem o Chrome renderiza **sem GPU**
(software), cada frame custa 1-5s. No Mac o render usa GPU/Metal e o mesmo
vídeo sai em poucos minutos. E o principal: local tem **preview ao vivo**
(`npm run dev`) — você vê a composição no browser instantaneamente, sem
renderizar pra conferir cada ajuste.

## 1. Setup (uma vez só)

```bash
# dependências (Homebrew)
brew install node ffmpeg yt-dlp git

# clone o projeto (branch com a edição do ComparaCar como referência)
git clone https://github.com/danzchohfi/Hyperframes-VP.git
cd Hyperframes-VP
git checkout claude/instagram-video-editing-afbhdo

npm install
npx hyperframes doctor        # confere Chrome/FFmpeg — deve ficar tudo ✓

# instala as fontes usadas (Google Fonts, grátis): Nunito
# (a composição usa os woff2 de assets/fonts/, mas ter no sistema ajuda no FCP)

# skills oficiais do HyperFrames para o Claude Code local (recomendado)
npx hyperframes skills
```

## 2. Workflow por vídeo novo

### a) Baixar o vídeo (se for link do Instagram)

```bash
yt-dlp -o "assets/NOME.mp4" "https://www.instagram.com/reel/XXXX/"
ffmpeg -i assets/NOME.mp4 -vn -acodec copy assets/NOME_audio.m4a
```

### b) Transcrever com timestamps por palavra (pt-BR)

O CLI tem um bug com o modelo multilíngue (`--dtw large-v3` vs `large.v3`).
Caminho que funciona:

```bash
# 1ª vez: deixe o CLI baixar o modelo (vai falhar no fim — normal)
npx hyperframes transcribe assets/NOME.mp4 -m large-v3 -l pt || true

# rode o whisper-cli direto com a flag correta:
W=~/.cache/hyperframes/whisper
ffmpeg -y -i assets/NOME.mp4 -ar 16000 -ac 1 /tmp/NOME_16k.wav
$W/whisper.cpp/build/bin/whisper-cli \
  --model $W/models/ggml-large-v3.bin \
  --language pt --output-json-full --dtw large.v3 --suppress-nst \
  --output-file transcript_raw /tmp/NOME_16k.wav
```

No Apple Silicon o whisper.cpp usa Metal — a transcrição voa.

### c) Montar a edição com o Claude Code local

Abra o Claude Code **dentro da pasta do projeto** e peça a edição. O repo já
carrega todo o padrão como contexto:

- `index.html` — a composição do ComparaCar = **template do estilo** (paleta
  preto + amarelo #F8D000, Nunito, cutaways, chips, freeze duotone, punches,
  follow pill, outro card, SFX)
- `RELATORIO_EDICAO.md` — tabela de camadas/tempos e as decisões técnicas
- `transcript_raw.json` — como usar os tempos palavra a palavra
- `assets/sfx/` — os 8 efeitos sonoros prontos (whoosh, pop, tick, ding,
  impact, riser) para reusar em qualquer vídeo
- `scripts/generate_fcpxml.py` — adapte os arrays TITLES/CHAPTERS/TODOS para
  o vídeo novo e gere o XML do Final Cut

Prompt sugerido:

> Edite assets/NOME.mp4 seguindo o padrão do index.html (reel ComparaCar):
> transcreva, escolha 3-5 momentos de cutaway sincronizados por palavra,
> chips de palavra-chave, zoom punches, freeze de ênfase, outro card e SFX
> de assets/sfx/. Paleta e fonte iguais. Rode npm run check até zero erros.

### d) Iterar com preview ao vivo (o grande ganho do local)

```bash
npm run dev        # abre o studio no browser — scrub na timeline, ajuste fino
npm run check      # lint + validate + inspect — zero erros antes de renderizar
```

### e) Renderizar

```bash
npm run render     # ou:
npx hyperframes render -o output/NOME_editado.mp4 -q high
```

**Se** o render travar/EPIPE (só deve acontecer em máquina sem GPU):

```bash
PRODUCER_ENABLE_STREAMING_ENCODE=false \
FFMPEG_ENCODE_TIMEOUT_MS=3600000 FFMPEG_PROCESS_TIMEOUT_MS=3600000 \
npx hyperframes render -o output/NOME_editado.mp4 -q high -w 3
```

(Foi o que precisamos na nuvem; a causa está documentada no
`RELATORIO_EDICAO.md`.)

### f) Refinar no Final Cut (opcional)

```bash
python3 scripts/generate_fcpxml.py export/NOME.fcpxml
```

Importe com File > Import > XML e relink para `assets/NOME.mp4`. Os SFX de
`assets/sfx/` podem ser importados junto — os to-do markers dizem onde entra
cada um.

## Dicas de performance no render headless

Aprendidas na marra (valem também pra máquinas fracas):

- Evite `backdrop-filter` e animações CSS de `conic-gradient`/`@property` —
  em render por software custam segundos por frame
- Gradientes/glows grandes: estáticos, não animados
- `shine`/meteors/grain (transform pequenos) são baratos
- Todo `<audio>` de SFX **precisa de `id`** ou fica mudo no render
- Cutaways de fundo opaco: gate de visibilidade explícito por opacity no GSAP
  (`tl.set` no início/fim da janela)
