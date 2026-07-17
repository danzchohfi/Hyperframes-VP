# HyperFrames Composition Project

## Skills — USE THESE FIRST

**Always invoke the relevant skill before writing or modifying compositions.** Skills encode framework-specific patterns (e.g., `window.__timelines` registration, `data-*` attribute semantics, seek-safe animation rules) that are NOT in generic web docs. Skipping them produces broken compositions.

**Routing:** `/hyperframes` is the mandatory entry point for any request to make, edit, animate, or render a video — it resumes project state and selects the owning workflow. Before composing any animation, also load `/motion-doctrine`.

### Core domain skills

| Skill                     | Command                  | When to use                                                                                                  |
| ------------------------- | ------------------------ | ------------------------------------------------------------------------------------------------------------ |
| **hyperframes**           | `/hyperframes`           | Mandatory entry point for any video/animation request — resumes state, routes to the owning workflow          |
| **hyperframes-core**      | `/hyperframes-core`      | Composition contract: `data-*` timing, `class="clip"`, tracks, sub-compositions, Tailwind, validation          |
| **hyperframes-cli**       | `/hyperframes-cli`       | Dev-loop CLI: init, lint, check, preview, render, publish, doctor; diagnosing build/render failures            |
| **hyperframes-animation** | `/hyperframes-animation` | All motion work + the seven runtime adapters (GSAP default, Lottie, Three.js, Anime.js, CSS, WAAPI, TypeGPU)   |
| **hyperframes-keyframes** | `/hyperframes-keyframes` | Seek-safe 2D/3D keyframes, GSAP timelines, FLIP, paths, masks, SVG morph/draw, `keyframes` diagnostics         |
| **hyperframes-creative**  | `/hyperframes-creative`  | Non-animation creative direction: design specs, palettes, typography, narration, beat planning                 |
| **hyperframes-registry**  | `/hyperframes-registry`  | Installing and wiring registry blocks/components via `hyperframes add` / `hyperframes catalog`                 |
| **media-use**             | `/media-use`             | Every media need: BGM, SFX, images, icons, logos, TTS/voiceover, transcription, captions, background removal   |

### Workflow skills (input → video)

| Skill                       | Command                    | When to use                                                                                 |
| --------------------------- | -------------------------- | -------------------------------------------------------------------------------------------- |
| **product-launch-video**    | `/product-launch-video`    | Product/marketing URL, script, or brief → launch/promo video (default for commercial URLs)    |
| **faceless-explainer**      | `/faceless-explainer`      | Arbitrary text or topic → explainer with invented visuals (no site or footage to capture)     |
| **pr-to-video**             | `/pr-to-video`             | GitHub pull request → code-change explainer video built from the diff and commits             |
| **music-to-video**          | `/music-to-video`          | Music track → beat-synced lyric video, slideshow, or kinetic promo                            |
| **changelog-video**         | `/changelog-video`         | Weekly changelog `.md` → branded ~45–60s changelog video (self-contained assets)              |
| **general-video**           | `/general-video`           | Freeform or multi-scene builds when no specialized workflow fits                              |
| **motion-graphics**         | `/motion-graphics`         | Short (~10s) design-led motion graphic: kinetic type, stat count-up, logo sting, lower-third  |
| **slideshow**               | `/slideshow`               | Presentation / pitch deck — output is a navigable deck, not a rendered MP4                    |
| **talking-head-recut**      | `/talking-head-recut`      | Timed graphic overlay cards on existing talking-head / podcast footage                        |
| **embedded-captions**       | `/embedded-captions`       | Captions/subtitles on an existing talking-head video (36-style catalog, local end to end)     |
| **remotion-to-hyperframes** | `/remotion-to-hyperframes` | Explicit ask to port a Remotion (React) composition to HyperFrames HTML                       |
| **figma**                   | `/figma`                   | Import Figma designs, frames, brand tokens, or animations into a composition                  |

### Motion doctrine & technique skills

| Skill                | Command             | When to use                                                                                        |
| -------------------- | ------------------- | ---------------------------------------------------------------------------------------------------|
| **motion-doctrine**  | `/motion-doctrine`  | GATEWAY — load before composing any animation: vector law, film's current, Seam Gate, no idle wobble |
| **cut-the-curve**    | `/cut-the-curve`    | Transition catalog (velocity-matched seams, waterfall entry, nudge curve) — read before any transition |
| **seam-craft**       | `/seam-craft`       | Render-correct scene-to-scene seams on the master timeline; white-flash guard                        |
| **oversized-cursor** | `/oversized-cursor` | House-style oversized cursor for UI scenes and pointer-led actions                                   |
| **captions-overlay** | `/captions-overlay` | Caption model (drop / rail / embed); captions are overlays, never a reserved bottom band             |

> Skills live in `.agents/skills/` (symlinked from `.claude/skills/`), installed via
> `npx skills add heygen-com/hyperframes`. To refresh them: `npx hyperframes skills update`.
> If skills don't appear in the agent session, restart the session after installing.

## Commands

```bash
npm run dev          # preview in browser (studio editor)
npm run check        # lint + validate + inspect
npm run render       # render to MP4
npm run publish      # publish and get a shareable link
npx hyperframes lint --verbose  # include info-level findings
npx hyperframes lint --json     # machine-readable output for CI
npx hyperframes docs <topic> # reference docs in terminal
```

## Documentation

**For quick reference**, use the local CLI docs command (no network required):

```bash
npx hyperframes docs <topic>
```

Topics: `data-attributes`, `gsap`, `compositions`, `rendering`, `examples`, `troubleshooting`

**For full documentation**, discover pages via the machine-readable index — do NOT guess URLs:

```
https://hyperframes.heygen.com/llms.txt
```

## Project Structure

- `index.html` — main composition (root timeline)
- `compositions/` — sub-compositions referenced via `data-composition-src`
- `meta.json` — project metadata (id, name)
- `transcript.json` — whisper word-level transcript (if generated)

## Linting — ALWAYS RUN AFTER CHANGES

After creating or editing any `.html` composition, **always** run the full check before considering the task complete:

```bash
npm run check
```

Fix all errors before presenting the result. Inspect warnings should be reviewed before rendering.

## Key Rules

1. Every timed element needs `data-start`, `data-duration`, and `data-track-index`
2. Elements with timing **MUST** have `class="clip"` — the framework uses this for visibility control
3. Timelines must be paused and registered on `window.__timelines`:
   ```js
   window.__timelines = window.__timelines || {};
   window.__timelines["composition-id"] = gsap.timeline({ paused: true });
   ```
4. Videos use `muted` with a separate `<audio>` element for the audio track
5. Sub-compositions use `data-composition-src="compositions/file.html"` to reference other HTML files
6. Only deterministic logic — no `Date.now()`, no `Math.random()`, no network fetches
