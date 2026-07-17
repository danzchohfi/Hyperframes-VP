# Relatório de Edição — Reel ComparaCar "Carro por assinatura"

**Fonte:** https://www.instagram.com/reel/Da2hh5mgJ9t/ (@comparacar_oficial)
**Vídeo:** 1080×1920 (9:16), 30fps, 109,25s · legendas originais queimadas mantidas
**Saída:** `output/comparacar_reel_editado.mp4` (112s — inclui outro card de 3s)

## O que foi adicionado

O vídeo original já tinha legendas queimadas, então a edição foca em camadas
complementares (nada de legenda duplicada):

| Tempo | Camada | Conteúdo |
| --- | --- | --- |
| 0,0–2,3s | Intro frame | Borda laranja desenhando + fade |
| 1,0–104,6s | Chip de canal | Pill `@comparacar_oficial` no topo |
| 0–109,25s | Barra de progresso | Topo, gradiente laranja, linear |
| 6,1 / 16,8 / 42,3 / 64,6 / 88,2 / 93,0s | Zoom punches | Punch-in sutil no vídeo (scale 1.06–1.07) sincronizado com frases de ênfase |
| 26,0–29,8s | Keyword chip | 💰 "OPERAÇÃO FINANCEIRA" |
| 32,6–40,35s | **Cutaway C1** | Card "VOCÊ PAGOU:" — itens IPVA → SEGURO → MANUTENÇÃO → +DESPESAS entrando em sincronia com a fala (33,0 / 33,4 / 34,1 / 35,0s) + banner "VENDEU POR BEM MENOS" (37,0s) |
| 40,85–44,85s | Keyword chip | 📉 "DEPRECIAÇÃO" |
| 45,8–48,7s | **Cutaway C2** | Card "FAÇA A CONTA" — somatória + "= QUANTO TE CUSTOU?" |
| 58,7–64,5s | **Cutaway C3** | Split screen COMPRAR × ASSINAR com badge VS (no "versus" falado, 59,3s) + chips comparativos + pill "MESMO PERÍODO: 36 MESES" (62,8s) |
| 74,85–77,75s | **Freeze-frame F1** | Still do apontar pra câmera, tratamento duotone laranja + halftone, "O QUE É MAIS VANTAJOSO / PRO SEU BOLSO" |
| 85,3–87,95s | Keyword chip | 🏦 "FINANCIOU? É DO BANCO" |
| 96,5–100,7s | **Cutaway C4** | Stat gigante "70%" (flicker de contagem 23→51→70 no momento em que fala "70%", 97,2s) + "A ASSINATURA É MAIS ECONÔMICA E VANTAJOSA" |
| 101,3–104,8s | Keyword chip | 📈 "RECORDE TODOS OS MESES" |
| 105,3–108,85s | Follow pill | Card estilo Instagram com botão "Seguir" |
| 108,8–112s | **Outro card** | Logo COMPARACAR + "Tem gente fazendo conta." + handle |
| 0–112s | Grain | Film grain sutil (0.10) sobre tudo |

Todos os tempos foram sincronizados com timestamps **palavra a palavra** da
transcrição Whisper large-v3 (`transcript_raw.json`).

## Identidade visual

- Laranja da marca amostrado do próprio vídeo (`#E06830` → paleta `#F0762F` /
  `#FF8A3C` / `#C2551C`)
- Tipografia: Archivo Black + Archivo (auto-hospedadas em `assets/fonts/`,
  subset latin — sem dependência de rede no render)
- Ícones: SVG inline (sem emoji — determinístico no render headless)

## Como re-renderizar

```bash
npm install                       # requer ONNXRUNTIME_NODE_INSTALL_CUDA=skip em ambientes sem rede p/ CUDA
HYPERFRAMES_BROWSER_PATH=<chrome> npx hyperframes render -o output/comparacar_reel_editado.mp4 -q high
```

## Notas técnicas

- Vídeo `muted` + `<audio>` separado (`assets/reel_audio.m4a`), padrão do framework
- Visibilidade dos cutaways controlada explicitamente via GSAP (`tl.set` de
  opacity nas janelas) — o runtime não esconde `.clip` fora da janela em todos
  os modos, e os cards têm fundo opaco
- Contador do "70%" usa flicker de dígitos via `tl.set` (callbacks `onUpdate`
  são suprimidos no seek do runtime)
- `npm run check`: 0 erros (2 warnings estilísticos aceitos: arquivo único
  grande / 4 chips na mesma track — mantém os timings sincronizados num lugar só)
