"""Video renderer — ffmpeg-based composer for vertical short-form video.

Inputs the VideoAgent's beats[] (each {ts_start, ts_end, narration, on_screen_text,
broll_prompt, sfx}), generates per-beat b-roll images (DesignerAgent → image gen),
synthesizes TTS, then composes everything with ffmpeg into a 1080x1920 MP4 with
burned-in captions.

This runs on the `video` queue which uses Dockerfile.video (has ffmpeg installed).
"""
from __future__ import annotations

import asyncio
import subprocess
import tempfile
import uuid
from pathlib import Path

import ffmpeg
from sqlalchemy import select

from app.core.config import settings
from app.core.logging import log
from app.db.session import SessionLocal
from app.integrations.image_gen import generate_image
from app.integrations.tts import synthesize
from app.models.content import MediaAsset, VideoRender
from app.utils.storage import s3
from app.workers.celery_app import celery_app


def enqueue_render(video_render_id: str) -> str:
    sig = celery_app.signature(
        "app.integrations.video_render.run_render",
        args=[video_render_id],
        queue="video",
    )
    return sig.apply_async().id


@celery_app.task(name="app.integrations.video_render.run_render",
                 bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def run_render(self, video_render_id: str) -> dict:
    return asyncio.run(_render(video_render_id))


async def _render(video_render_id: str) -> dict:
    async with SessionLocal() as db:
        vr = (await db.execute(select(VideoRender).where(VideoRender.id == video_render_id))).scalar_one()
        vr.status = "rendering"
        await db.commit()

    try:
        script = vr.script_json or {}
        beats = script.get("beats") or []
        if not beats:
            raise RuntimeError("script has no beats")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # 1. TTS the full narration as a single file (lower latency than per-beat).
            full_narration = " ".join(b.get("narration", "") for b in beats)
            audio_key, _ = synthesize(full_narration, str(vr.brand_id), str(vr.asset_id))
            audio_obj = s3().get_object(Bucket=settings.s3_bucket, Key=audio_key)
            audio_local = tmp_path / "voice.mp3"
            audio_local.write_bytes(audio_obj["Body"].read())

            # 2. Per-beat b-roll image.
            image_locals: list[Path] = []
            for i, b in enumerate(beats):
                prompt = b.get("broll_prompt") or f"cinematic visual for: {b.get('narration', '')[:200]}"
                key, _ = generate_image(prompt, str(vr.brand_id), str(vr.asset_id),
                                        slot=f"broll-{i}", size="1024x1792")
                obj = s3().get_object(Bucket=settings.s3_bucket, Key=key)
                local = tmp_path / f"broll-{i}.png"
                local.write_bytes(obj["Body"].read())
                image_locals.append(local)

            # 3. Build per-beat clips with on-screen captions.
            clip_paths: list[Path] = []
            for i, (b, img) in enumerate(zip(beats, image_locals, strict=False)):
                dur = max(1.0, float(b.get("ts_end", i + 2)) - float(b.get("ts_start", i)))
                cap = (b.get("on_screen_text") or b.get("narration") or "").replace("'", "")[:140]
                out = tmp_path / f"clip-{i}.mp4"
                (
                    ffmpeg.input(str(img), loop=1, t=dur, framerate=30)
                    .filter("scale", 1080, 1920, force_original_aspect_ratio="increase")
                    .filter("crop", 1080, 1920)
                    .drawtext(
                        text=cap,
                        fontcolor="white",
                        fontsize=64,
                        x="(w-text_w)/2",
                        y="h-300",
                        box=1, boxcolor="black@0.55", boxborderw=20,
                    )
                    .output(str(out), vcodec="libx264", pix_fmt="yuv420p", r=30, preset="veryfast")
                    .overwrite_output()
                    .run(quiet=True)
                )
                clip_paths.append(out)

            # 4. Concat clips
            concat_list = tmp_path / "list.txt"
            concat_list.write_text("\n".join(f"file '{p}'" for p in clip_paths))
            video_no_audio = tmp_path / "video-noaudio.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
                 "-c", "copy", str(video_no_audio)],
                check=True, capture_output=True,
            )

            # 5. Mux audio
            final = tmp_path / "final.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(video_no_audio), "-i", str(audio_local),
                 "-c:v", "copy", "-c:a", "aac", "-shortest", str(final)],
                check=True, capture_output=True,
            )

            # 6. Upload to S3
            key = f"media/{vr.brand_id}/{vr.asset_id}/video-{uuid.uuid4().hex}.mp4"
            with open(final, "rb") as fh:
                s3().put_object(Bucket=settings.s3_bucket, Key=key, Body=fh,
                                ContentType="video/mp4")

        async with SessionLocal() as db:
            from app.models.content import ContentAsset
            vr = (await db.execute(select(VideoRender).where(VideoRender.id == video_render_id))).scalar_one()
            asset = (await db.execute(select(ContentAsset).where(ContentAsset.id == vr.asset_id))).scalar_one()
            vr.status = "done"
            vr.storage_key = key
            db.add(MediaAsset(
                account_id=asset.account_id,
                brand_id=vr.brand_id,
                asset_id=vr.asset_id,
                kind="video",
                storage_key=key,
                mime_type="video/mp4",
                provider="ffmpeg",
                meta={"video_render_id": str(vr.id)},
                created_at=vr.created_at,
            ))
            await db.commit()
        log.info("video_render_done", id=video_render_id, key=key)
        return {"video_render_id": video_render_id, "key": key, "status": "done"}
    except Exception as exc:
        async with SessionLocal() as db:
            vr = (await db.execute(select(VideoRender).where(VideoRender.id == video_render_id))).scalar_one()
            vr.status = "failed"
            vr.error = str(exc)[:1000]
            await db.commit()
        raise


