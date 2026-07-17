#!/usr/bin/env python3
"""Gera FCPXML 1.11 da edição do reel ComparaCar para refino no Final Cut Pro.

- Vídeo-fonte como clip primário com zoom punches em keyframes de escala
- Textos abertos (Basic Title) com fonte/tamanho/cor/posição
- Solids (Custom generator) como placeholder dos fundos dos cutaways
- Chapter markers por seção + to-do markers com a intenção de cada animação

Uso: python3 scripts/generate_fcpxml.py [saida.fcpxml]
Depois: FCP > File > Import > XML. Relink de mídia: assets/reel.mp4.
"""
import sys
from xml.sax.saxutils import escape

FPS = 30
ORANGE = "1 0.882 0.302 1"      # #ffe14d (amarelo claro da marca)
ORANGE_MID = "0.984 0.831 0.039 1"  # #fbd40a (amarelo da marca, sampleado #F8D000)
WHITE = "1 1 1 1"
INK = "0.047 0.047 0.063"

def fr(sec):
    """segundos -> rational alinhado ao frame (1/30) em base /3000s"""
    frames = round(sec * FPS)
    return f"{frames * 100}/3000s"

VIDEO_DUR = 109.2333  # 3277 frames
TOTAL = 112.0

# ---------------- punches: keyframes de escala no clip primário ----------------
PUNCHES = [(6.14, 1.062), (16.77, 1.07), (42.25, 1.062), (64.63, 1.07), (88.22, 1.062), (93.04, 1.055)]

def punch_keyframes():
    kfs = []
    for t, s in PUNCHES:
        kfs.append((t, 1.0))
        kfs.append((t + 0.3333, s))
        kfs.append((t + 0.6333, s))
        kfs.append((t + 2.2333, 1.0))
    return "\n".join(
        f'                            <keyframe time="{fr(t)}" value="{v} {v}"/>' for t, v in kfs
    )

# ---------------- markers (tempo-fonte do clip primário) ----------------
CHAPTERS = [
    (0.0, "HOOK — Como assim assinar um carro?"),
    (8.9667, "Contexto — todo dia o mesmo comentário"),
    (16.7667, "Em NENHUM cenário o carro é seu"),
    (25.9667, "Operação financeira"),
    (32.6, "CUTAWAY C1 — Custos do carro próprio"),
    (40.7667, "Depreciação"),
    (45.8333, "CUTAWAY C2 — Faça a conta"),
    (58.7333, "CUTAWAY C3 — COMPRAR × ASSINAR"),
    (64.5, "Em qualquer modalidade não é seu"),
    (74.8667, "FREEZE F1 — Pro seu bolso"),
    (85.2333, "Financiado = do banco"),
    (95.1333, "CUTAWAY C4 — 70% dos casos"),
    (100.6667, "Recorde todos os meses"),
    (105.0, "Fechamento — tem gente fazendo conta"),
]
TODOS = [
    (26.0, "Chip 'OPERAÇÃO FINANCEIRA': pop com back-ease (scale 0.82->1, y +70->0), sai 29.4s"),
    (32.6, "C1 entra com slide da direita; itens IPVA/SEGURO/MANUTENCAO/+DESPESAS entram em 33.0/33.4/34.1/35.0 sincronizados com a fala; checks pop"),
    (37.0, "Banner laranja 'VENDEU POR BEM MENOS' sobe com back-ease e leve rotacao -2deg"),
    (40.8667, "Chip 'DEPRECIACAO' pop; sai 44.5s"),
    (45.8333, "C2 zoom-out sutil (scale 1.12->1); termos em stagger 0.3s; '= QUANTO TE CUSTOU?' pop 47.35"),
    (58.7333, "C3 paineis deslizam das laterais; badge VS gira 360 e pop em 59.3 ('versus' falado); chips em stagger; pill 36 MESES pop 62.83"),
    (74.8667, "F1: freeze frame com duotone laranja + halftone; flash branco 0.25s; textos slam (scale 1.7->1). Still pronto em assets/still_75.png"),
    (85.3, "Chip 'FINANCIOU? E DO BANCO' pop; sai 87.6s"),
    (96.5, "C4: contador 23->51->70 em 96.78/97.0/97.2 (fala '70%' em 97.17); pop de escala no numero; linhas entram 98.6/98.85"),
    (101.3, "Chip 'RECORDE TODOS OS MESES' pop; sai 104.4s"),
    (105.3, "Follow pill estilo IG sobe com back-ease; botao Seguir pulsa 106.4 e 107.5"),
    (32.0, "SFX: whoosh nas entradas de cutaway (32.6/45.8/58.7/74.85/96.5/108.8), pops nos itens e chips, ticks+ding no contador 70%, impacts nos banners/VS/freeze — arquivos em assets/sfx/"),
    (108.8, "Video fade-out para outro card; wordmark/linha/tagline/handle em cascata; fade final para preto 111.55"),
]

def markers():
    out = []
    for t, name in CHAPTERS:
        out.append(f'                    <chapter-marker start="{fr(t)}" duration="100/3000s" value="{escape(name)}"/>')
    for t, note in TODOS:
        out.append(f'                    <marker start="{fr(t)}" duration="100/3000s" value="{escape(note[:60])}" completed="0" note="{escape(note)}"/>')
    return "\n".join(out)

# ---------------- solids de fundo (placeholders dos cutaways) ----------------
SOLIDS = [
    ("BG C1 — custos (ink)", 32.6, 7.7333),
    ("BG C2 — somatoria (ink)", 45.8333, 2.9),
    ("BG C3 — versus (ink; metade direita laranja no original)", 58.7333, 5.7667),
    ("BG F1 — freeze duotone (usar still assets/still_75.png)", 74.8667, 2.9),
    ("BG C4 — 70% (ink)", 96.5, 4.2),
]

# ---------------- titulos ----------------
# (texto, start, dur, size, color, pos_x, pos_y, align, font)
AB = "Nunito"
AR = "Nunito"
TITLES = [
    ("comparacar_oficial", 1.0, 103.6, 30, WHITE, 0, 815, "center", AR),
    ("OPERAÇÃO FINANCEIRA", 26.0, 3.8, 44, WHITE, 0, 501, "center", AB),
    ("DEPRECIAÇÃO", 40.8667, 4.0, 44, WHITE, 0, 501, "center", AB),
    ("FINANCIOU? É DO BANCO", 85.3, 2.6667, 44, WHITE, 0, 501, "center", AB),
    ("RECORDE TODOS OS MESES", 101.3, 3.5, 44, WHITE, 0, 501, "center", AB),
    # C1
    ("CARRO PRÓPRIO · NA PONTA DO LÁPIS", 32.6, 7.7333, 30, ORANGE, 0, 590, "center", AR),
    ("VOCÊ PAGOU:", 32.6, 7.7333, 88, WHITE, 0, 470, "center", AB),
    ("IPVA\nSEGURO\nMANUTENÇÃO\n+ DESPESAS", 33.0, 7.3333, 66, WHITE, 0, 60, "center", AB),
    ("E NO FINAL…\nVENDEU POR BEM MENOS DO QUE PAGOU", 37.0, 3.3333, 56, WHITE, 0, -560, "center", AB),
    # C2
    ("FAÇA A CONTA", 45.8333, 2.9, 30, ORANGE, 0, 330, "center", AR),
    ("+ DINHEIRO PARADO NO CARRO\n+ IPVA · SEGURO · MANUTENÇÃO\n+ DEPRECIAÇÃO NA REVENDA", 45.9667, 2.7667, 54, WHITE, 0, 60, "center", AB),
    ("= QUANTO TE CUSTOU?", 47.3667, 1.3667, 76, WHITE, 0, -280, "center", AB),
    # C3
    ("COMPRAR", 58.7333, 5.7667, 78, WHITE, -270, 295, "center", AB),
    ("ASSINAR", 58.7333, 5.7667, 78, WHITE, 270, 295, "center", AB),
    ("VS", 59.3, 5.2, 92, "0.047 0.047 0.063 1", 0, 0, "center", AB),
    ("ENTRADA + PARCELAS\nIPVA · SEGURO\nREVENDA POR MENOS", 59.8667, 4.6333, 33, WHITE, -270, -190, "center", AR),
    ("PARCELA ÚNICA\nTUDO INCLUSO\nZERO REVENDA", 60.5667, 3.9333, 33, WHITE, 270, -190, "center", AR),
    ("MESMO PERÍODO: 36 MESES", 62.8333, 1.6667, 40, WHITE, 0, -685, "center", AR),
    # F1
    ("O QUE É MAIS VANTAJOSO", 74.8667, 2.9, 54, WHITE, 0, 700, "center", AR),
    ("PRO SEU BOLSO", 75.1667, 2.6, 118, WHITE, 0, 570, "center", AB),
    # C4
    ("MAIS DE", 96.5, 4.2, 30, ORANGE, 0, 520, "center", AR),
    ("70%", 96.7667, 3.9333, 300, ORANGE, 0, 150, "center", AB),
    ("DOS CASOS", 96.5, 4.2, 46, WHITE, 0, -175, "center", AR),
    ("A ASSINATURA É\nMAIS ECONÔMICA E VANTAJOSA", 98.6, 2.1, 78, WHITE, 0, -440, "center", AB),
    # follow
    ("ComparaCar · @comparacar_oficial · SEGUIR", 105.3, 3.5333, 32, WHITE, 0, -230, "center", AR),
]
OUTRO_TITLES = [  # ancorados no gap do outro (offset absoluto)
    ("COMPARACAR", 109.2333, 2.7667, 116, WHITE, 0, 80, "center", AB),
    ("Tem gente fazendo conta.", 109.8667, 2.1333, 44, WHITE, 0, -120, "center", AR),
    ("@comparacar_oficial", 110.1667, 1.8333, 36, ORANGE, 0, -300, "center", AR),
]

def title_el(idx, ref, text, start, dur, size, color, px, py, align, font, lane):
    tsid = f"ts{idx}"
    lines = text.split("\n")
    body = "".join(
        f'<text-style ref="{tsid}">{escape(l)}{"" if i == len(lines)-1 else chr(10)}</text-style>'
        for i, l in enumerate(lines)
    )
    return f"""                <title ref="{ref}" lane="{lane}" offset="{fr(start)}" name="{escape(text.split(chr(10))[0][:40])}" start="0s" duration="{fr(dur)}">
                    <text>{body}</text>
                    <text-style-def id="{tsid}">
                        <text-style font="{font}" fontSize="{size}" fontFace="Black" fontColor="{color}" bold="1" alignment="{align}"/>
                    </text-style-def>
                    <adjust-transform position="{px} {py}"/>
                </title>"""

def solid_el(name, start, dur, lane):
    return f"""                <video ref="r5" lane="{lane}" offset="{fr(start)}" name="{escape(name)}" start="0s" duration="{fr(dur)}">
                    <param name="Color" key="9999/10003/10004/2/113" value="{INK}"/>
                </video>"""

def build():
    titles = "\n".join(
        title_el(i, "r4", *t, lane=2) for i, t in enumerate(TITLES)
    )
    solids = "\n".join(solid_el(n, s, d, lane=1) for n, s, d in SOLIDS)
    outro_titles = "\n".join(
        title_el(100 + i, "r4", *t, lane=2) for i, t in enumerate(OUTRO_TITLES)
    )
    outro_offset = fr(VIDEO_DUR)
    outro_dur = fr(TOTAL - VIDEO_DUR)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.11">
    <resources>
        <format id="r1" name="FFVideoFormatRateUndefined" frameDuration="100/3000s" width="1080" height="1920" colorSpace="1-1-1 (Rec. 709)"/>
        <asset id="r2" name="reel" uid="COMPARACAR_REEL_SRC" start="0s" duration="327750/3000s" hasVideo="1" format="r1" hasAudio="1" audioSources="1" audioChannels="2" audioRate="48000">
            <media-rep kind="original-media" src="file:///RELINK/assets/reel.mp4"/>
        </asset>
        <asset id="r3" name="comparacar_reel_editado (referencia renderizada)" uid="COMPARACAR_REEL_RENDER" start="0s" duration="336000/3000s" hasVideo="1" format="r1" hasAudio="1" audioSources="1" audioChannels="2" audioRate="48000">
            <media-rep kind="original-media" src="file:///RELINK/output/comparacar_reel_editado.mp4"/>
        </asset>
        <effect id="r4" name="Basic Title" uid=".../Titles.localized/Basic Text.localized/Basic Title.localized/Basic Title.moti"/>
        <effect id="r5" name="Custom" uid=".../Generators.localized/Solids.localized/Custom.localized/Custom.motn"/>
    </resources>
    <library>
        <event name="ComparaCar — Reel assinatura">
            <asset-clip ref="r3" name="Referência renderizada (HyperFrames)" duration="336000/3000s" format="r1" tcFormat="NDF" audioRole="dialogue"/>
            <project name="ComparaCar — Carro por assinatura (edit)">
                <sequence format="r1" duration="{fr(TOTAL)}" tcStart="0s" tcFormat="NDF" audioLayout="stereo" audioRate="48k">
                    <spine>
                        <asset-clip ref="r2" offset="0s" name="Reel ComparaCar (fonte)" start="0s" duration="{fr(VIDEO_DUR)}" format="r1" tcFormat="NDF" audioRole="dialogue">
                            <adjust-transform>
                                <param name="scale">
                                    <keyframeAnimation>
{punch_keyframes()}
                                    </keyframeAnimation>
                                </param>
                            </adjust-transform>
{solids}
{titles}
{markers()}
                        </asset-clip>
                        <gap name="Outro" offset="{outro_offset}" start="0s" duration="{outro_dur}">
{outro_titles}
                        </gap>
                    </spine>
                </sequence>
            </project>
        </event>
    </library>
</fcpxml>
"""

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "export/ComparaCar_reel_assinatura.fcpxml"
    xml = build()
    import xml.etree.ElementTree as ET
    ET.fromstring(xml)  # valida bem-formado
    import os
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w").write(xml)
    n_titles = len(TITLES) + len(OUTRO_TITLES)
    print(f"OK: {out} — {n_titles} títulos, {len(SOLIDS)} solids, {len(CHAPTERS)} capítulos, {len(TODOS)} to-dos")
