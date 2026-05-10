"""Orchestrates a full podcast workflow as a single async job:

  1. Transcribe (if not already)
  2. Detect speakers (diarization)
  3. Detect chapters (topic-shift)
  4. Detect silences + fillers
  5. Apply edits (cuts + LUT)
  6. Level speakers (normalize per-speaker volume)
  7. Generate social copy

Each step emits progress to the SSE bus via the job context.
"""

from __future__ import annotations

from pathlib import Path

from . import jobs as jobs_svc
from . import ffmpeg as ff
from . import whisper as whisper_svc
from . import speakers as speakers_svc
from . import chapters as chapters_svc
from . import fillers as fillers_svc
from . import silence as silence_svc
from . import speaker_levels as levels_svc
from . import social_copy as social_svc
from .. import storage


async def run(ctx: jobs_svc.JobContext, *, language: str | None = None) -> dict:
    pid = ctx.project_id
    state = storage.load(pid)
    pdir = storage.project_dir(pid)
    src = pdir / "source.mp4"
    if not src.exists():
        raise RuntimeError("no source uploaded")

    out: dict = {}

    # --- 1. transcribe ---
    if not state.has_transcript:
        ctx.progress(0.05, "Transcribing")
        ctx.log("Whisper API")
        result = await whisper_svc.transcribe(src, language=language)
        storage.write_json(pid, "transcript.json", result)
        state.has_transcript = True
        storage.save(state)
        out["transcribed_words"] = len(result.get("words") or [])
    else:
        out["transcribed_words"] = len(storage.read_json(pid, "transcript.json").get("words") or [])

    transcript = storage.read_json(pid, "transcript.json")
    ctx.check_cancel()

    # --- 2. speakers ---
    ctx.progress(0.20, "Detecting speakers")
    spk = await speakers_svc.diarize(src, transcript.get("words") or [], backend="auto")
    storage.write_json(pid, "speakers.json", spk)
    out["speaker_count"] = spk["stats"]["speaker_count"]
    out["speaker_backend"] = spk["backend"]
    ctx.check_cancel()

    # --- 3. chapters ---
    ctx.progress(0.35, "Detecting chapters")
    try:
        chap = await chapters_svc.detect(transcript)
        storage.write_json(pid, "chapters.json", {
            "chapters": [c.model_dump() for c in chap.chapters],
            "youtube_markdown": chapters_svc.to_youtube_markdown(chap.chapters),
        })
        out["chapter_count"] = len(chap.chapters)
    except Exception as e:
        ctx.log(f"chapter detection failed: {e}", level="warn")
        out["chapter_count"] = 0
    ctx.check_cancel()

    # --- 4. silences + fillers ---
    ctx.progress(0.50, "Detecting silences + fillers")
    silences = await ff.detect_silences(src, noise_db=-32.0, min_silence=0.5)
    dur = state.source_duration or (await ff.duration(src))
    keep = silence_svc.plan_keep_segments(dur, silences)
    storage.write_json(pid, "cuts.json", {
        "options": {"noise_db": -32.0, "min_silence": 0.5, "pad": 0.08},
        "source_duration": dur,
        "silences": [{"start": s, "end": e} for s, e in silences],
        "keep": [{"start": s, "end": e} for s, e in keep],
        "kept_duration": silence_svc.total_kept(keep),
    })
    state.has_cuts = True

    lang_for_fillers = language or transcript.get("language") or "auto"
    filler_ranges = fillers_svc.detect_filler_ranges(
        transcript.get("words") or [], language=lang_for_fillers,
    )
    storage.write_json(pid, "fillers.json", {
        "language": lang_for_fillers,
        "ranges": [{"start": s, "end": e} for s, e in filler_ranges],
        "stats": fillers_svc.stats(filler_ranges),
    })
    state.has_fillers = True
    state.fillers_count = len(filler_ranges)
    storage.save(state)
    out["filler_ranges"] = len(filler_ranges)
    out["silence_count"] = len(silences)
    ctx.check_cancel()

    # --- 5. apply edits ---
    ctx.progress(0.65, "Applying cuts + LUT")
    keep_after = fillers_svc.subtract_ranges(keep, filler_ranges)
    lut = pdir / "lut.cube" if state.has_lut else None
    graded = pdir / "graded.mp4"
    await ff.cut_segments(src, graded, keep_after, lut=lut, loudnorm=True)
    out["graded_duration"] = await ff.duration(graded)
    ctx.check_cancel()

    # --- 6. level speakers (on graded) ---
    ctx.progress(0.82, "Leveling speakers")
    try:
        levels = await levels_svc.measure_per_speaker(graded, spk["turns"])
        gains = levels_svc.gain_plan(levels, target_dbfs=-18.0)
        af = levels_svc.build_filter(spk["turns"], gains)
        levelled = pdir / "exports" / f"{state.name.replace(' ', '_')}-levelled.mp4"
        levelled.parent.mkdir(exist_ok=True)
        await ff.apply_audio_filter(graded, levelled, af)
        out["levels"] = levels
        out["gains"] = gains
        storage.write_json(pid, "speaker_levels.json", {
            "target_dbfs": -18.0,
            "source": "graded",
            "levels": levels,
            "gains": gains,
        })
    except Exception as e:
        ctx.log(f"level normalization skipped: {e}", level="warn")
    ctx.check_cancel()

    # --- 7. social copy ---
    ctx.progress(0.95, "Writing social copy")
    try:
        story_title = None
        story_logline = None
        story_file = pdir / "story.json"
        if story_file.exists():
            sj = storage.read_json(pid, "story.json")
            story_title = sj.get("title")
            story_logline = sj.get("logline")
        social = await social_svc.generate(
            transcript_text=transcript.get("text") or "",
            title=story_title,
            logline=story_logline,
            brand_name=state.name,
            language=language or "pt",
        )
        storage.write_json(pid, "social_copy.json", social.model_dump())
        out["social_hook"] = social.hook
    except Exception as e:
        ctx.log(f"social copy skipped: {e}", level="warn")

    ctx.progress(1.0, "Done")
    return out
