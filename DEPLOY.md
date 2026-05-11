# Deploy — pra subir o app numa URL pública

Duas opções testadas. **Railway é o mais rápido** (GitHub → click → tem URL em ~5 min). **Fly.io é mais barato** quando você for armazenar muitos vídeos.

---

## ⚠ Antes de qualquer coisa

O app é uma porta-de-entrada pra sua chave da OpenAI. Se você subir sem
proteção, qualquer um que descobrir a URL pode rodar Whisper e GPT-4o na
sua conta. **Configure `HFVP_AUTH_PASSWORD`** — o middleware embutido vai
exigir Basic auth em TUDO exceto `/api/health`.

Vídeos longos consomem RAM e disco. Numa máquina pequena (1 vCPU / 1 GB),
rendere clipes curtos (< 5 min) e use renders horizontais simples.

---

## Railway (recomendado pra começar)

1. **Crie a conta**: <https://railway.app> (login com GitHub).
2. **New Project → Deploy from GitHub repo** → escolha `danzchohfi/Hyperframes-VP`.
3. Em **Settings → Source**:
   - Branch: `claude/setup-hyperframes-DRZSw` (ou faça merge pra `main` primeiro)
4. Em **Variables**, adicione:
   ```
   OPENAI_API_KEY=sk-...
   HFVP_AUTH_PASSWORD=alguma-senha-forte
   HFVP_AUTH_USER=admin            # opcional, default "admin"
   ```
5. Em **Settings → Volumes → Add Volume**:
   - Mount path: `/data`
   - Size: 10 GB (você pode aumentar depois)
6. Em **Settings → Networking → Generate Domain** → ganha um `*.up.railway.app`.
7. Espera o primeiro build (~3-5 min). Quando ficar verde, abre a URL.

A primeira visita pede usuário/senha (Basic auth). Depois disso o app
funciona igualzinho ao local.

**Custo**: o plano Hobby ($5/mês) cobre o app + 0.25 USD/GB/mês de volume.
Pra 10 GB de projetos = ~$2.50/mês a mais.

---

## Fly.io (mais barato + escolhe a região)

Pré-requisito: <https://fly.io/docs/hands-on/install-flyctl/>

```bash
cd ~/Hyperframes-VP
fly launch --no-deploy        # gera fly.toml customizado (o nosso já está bom)
fly secrets set OPENAI_API_KEY=sk-...
fly secrets set HFVP_AUTH_PASSWORD=alguma-senha-forte
fly volumes create hfvp_data --size 10 --region gru
fly deploy
```

`fly launch --no-deploy` vai detectar o `fly.toml` e o `Dockerfile` que já
estão no repo. Se ele perguntar pra sobrescrever, responda **não**.

A URL pública sai como `https://<nome-do-app>.fly.dev`.

**Custo**: shared-cpu-2x + 2 GB RAM auto-stop fica ~$3/mês quando idle,
mais $0.15/GB/mês de volume. Apple Podcasts ingestion etc. funciona porque
o app está sempre acessível, só que cold-starts levam ~5 s.

---

## Variáveis de ambiente disponíveis

| Variável | Default | Pra quê |
|---|---|---|
| `OPENAI_API_KEY` | — | Obrigatória pra Whisper + LLMs |
| `HFVP_AUTH_PASSWORD` | (vazio = sem auth) | Senha do Basic auth |
| `HFVP_AUTH_USER` | `admin` | Usuário do Basic auth |
| `HFVP_PROJECTS_DIR` | `server/projects/` | Diretório persistente (use `/data/projects` em volume) |
| `EPIDEMIC_SOUND_TOKEN` | — | Habilita busca de música via API parceira |
| `HF_TOKEN` | — | Habilita diarização real via pyannote.audio |
| `PORT` | `8765` | Porta de escuta (Railway/Fly injetam sozinhos) |

---

## Pós-deploy: dia-a-dia

- **Acessar**: `https://<seu-app>.up.railway.app` (Basic auth, depois UI normal)
- **Atualizar**: push pra branch → Railway auto-redeploy. No Fly: `fly deploy`.
- **Logs**: Railway tab "Deployments" → "View Logs". Fly: `fly logs`.
- **Backup**: `fly ssh sftp` ou Railway "Volumes → Download snapshot".

---

## Local continua funcionando

A configuração via env não quebra o `./run.sh` local — se as variáveis
não estiverem setadas, o app continua usando `server/projects/` e sem auth,
exatamente como antes.
