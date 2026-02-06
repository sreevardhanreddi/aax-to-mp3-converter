import base64
import json
import os
import subprocess
import tempfile
import zipfile

import redis
from celery import chord

from config import logger
from services import AAXProcessor, conversion_orchestrator, conversion_service

from .celery_app import celery_app


@celery_app.task(name="tasks.convert_m4b")
def convert_m4b_task(
    filename: str, aax_file_path: str, m4b_file_path: str, activation_bytes: str
):
    logger.info(f"Running Celery M4B conversion task for {filename}")
    conversion_orchestrator.convert_file_background(
        filename=filename,
        aax_file_path=aax_file_path,
        m4b_file_path=m4b_file_path,
        activation_bytes=activation_bytes,
        start_tracking=False,
    )


@celery_app.task(name="tasks.convert_mp3_chapters")
def convert_mp3_chapters_task(
    filename: str,
    aax_file_path: str,
    output_dir: str,
    activation_bytes: str,
):
    logger.info(f"Preparing MP3 chapter tasks for {filename}")

    try:
        metadata_cmd = [
            "ffprobe",
            "-activation_bytes",
            activation_bytes,
            "-i",
            aax_file_path,
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-show_chapters",
            "-v",
            "quiet",
        ]
        metadata_result = subprocess.run(
            metadata_cmd, capture_output=True, text=True, check=True
        )
        metadata = json.loads(metadata_result.stdout)
        format_info = metadata.get("format", {})
        tags = format_info.get("tags", {})
        chapters = metadata.get("chapters", [])

        if not chapters:
            conversion_service.complete_conversion(
                filename=filename,
                success=False,
                error_message="No chapters found in AAX file",
                conversion_type="mp3_chapters",
            )
            return

        album_art_data = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as temp_art:
                art_cmd = [
                    "ffmpeg",
                    "-y",
                    "-activation_bytes",
                    activation_bytes,
                    "-i",
                    aax_file_path,
                    "-an",
                    "-vf",
                    "scale='min(500,iw)':'min(500,ih)':force_original_aspect_ratio=decrease",
                    "-map",
                    "0:v:0",
                    temp_art.name,
                ]
                subprocess.run(art_cmd, capture_output=True, check=True)
                with open(temp_art.name, "rb") as art_file:
                    album_art_data = art_file.read()
                os.unlink(temp_art.name)
        except Exception:
            album_art_data = None

        base_name = os.path.splitext(os.path.basename(aax_file_path))[0]
        temp_dir = tempfile.mkdtemp(prefix=f"{base_name}_mp3_")
        total_chapters = len(chapters)
        conversion_service.update_progress(filename, 0, "converting", "mp3_chapters")

        progress_client = redis.Redis.from_url(
            os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0"),
            decode_responses=True,
        )
        progress_key = f"mp3_progress:{filename}"
        progress_client.set(progress_key, 0, ex=60 * 60 * 12)

        album_art_b64 = (
            base64.b64encode(album_art_data).decode("ascii") if album_art_data else None
        )

        chapter_sigs = [
            convert_mp3_chapter_task.s(
                filename=filename,
                aax_file_path=aax_file_path,
                activation_bytes=activation_bytes,
                chapter_index=i,
                chapter=chapter,
                tags=tags,
                temp_dir=temp_dir,
                total_chapters=total_chapters,
                album_art_b64=album_art_b64,
                progress_key=progress_key,
            )
            for i, chapter in enumerate(chapters)
        ]

        chord(chapter_sigs)(
            finalize_mp3_chapters_task.s(
                filename=filename,
                output_dir=output_dir,
                temp_dir=temp_dir,
                total_chapters=total_chapters,
                progress_key=progress_key,
            )
        )
    except Exception as task_error:
        conversion_service.complete_conversion(
            filename=filename,
            success=False,
            error_message=f"MP3 task orchestration failed: {task_error}",
            conversion_type="mp3_chapters",
        )


@celery_app.task(name="tasks.convert_mp3_chapter")
def convert_mp3_chapter_task(
    filename: str,
    aax_file_path: str,
    activation_bytes: str,
    chapter_index: int,
    chapter: dict,
    tags: dict,
    temp_dir: str,
    total_chapters: int,
    album_art_b64: str | None,
    progress_key: str,
):
    try:
        processor = AAXProcessor()
        album_art_data = (
            base64.b64decode(album_art_b64.encode("ascii")) if album_art_b64 else None
        )
        mp3_path = processor.convert_single_chapter_for_task(
            chapter_index=chapter_index,
            chapter=chapter,
            tags=tags,
            aax_file=aax_file_path,
            activation_bytes=activation_bytes,
            temp_dir=temp_dir,
            album_art_data=album_art_data,
            total_chapters=total_chapters,
        )

        if not mp3_path:
            return {"success": False, "chapter_index": chapter_index}

        progress_client = redis.Redis.from_url(
            os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0"),
            decode_responses=True,
        )
        completed = int(progress_client.incr(progress_key))
        progress = round((completed / total_chapters) * 90, 1)
        conversion_service.update_progress(
            filename=filename,
            progress=progress,
            status="converting",
            conversion_type="mp3_chapters",
        )

        return {"success": True, "chapter_index": chapter_index, "mp3_path": mp3_path}
    except Exception as chapter_error:
        logger.error(
            f"MP3 chapter task failed for {filename} chapter {chapter_index + 1}: {chapter_error}"
        )
        return {"success": False, "chapter_index": chapter_index}


@celery_app.task(name="tasks.finalize_mp3_chapters")
def finalize_mp3_chapters_task(
    chapter_results: list,
    filename: str,
    output_dir: str,
    temp_dir: str,
    total_chapters: int,
    progress_key: str,
):
    try:
        successful_paths = []
        failed_chapters = []

        for result in chapter_results:
            if result.get("success") and result.get("mp3_path"):
                successful_paths.append(result["mp3_path"])
            else:
                failed_chapters.append((result.get("chapter_index", -1) + 1))

        progress_client = redis.Redis.from_url(
            os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0"),
            decode_responses=True,
        )
        progress_client.delete(progress_key)

        if failed_chapters or not successful_paths:
            for mp3_path in successful_paths:
                if os.path.exists(mp3_path):
                    os.remove(mp3_path)
            if os.path.exists(temp_dir):
                for tmp_name in os.listdir(temp_dir):
                    tmp_path = os.path.join(temp_dir, tmp_name)
                    if os.path.isfile(tmp_path):
                        os.remove(tmp_path)
                os.rmdir(temp_dir)
            conversion_service.complete_conversion(
                filename=filename,
                success=False,
                error_message=(
                    f"Failed chapters: {failed_chapters}"
                    if failed_chapters
                    else "No chapters were converted"
                ),
                conversion_type="mp3_chapters",
            )
            return

        base_name = os.path.splitext(os.path.basename(filename))[0]
        zip_filename = f"{base_name}_chapters.zip"
        zip_path = os.path.join(output_dir, zip_filename)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for mp3_path in successful_paths:
                if os.path.exists(mp3_path):
                    zipf.write(mp3_path, os.path.basename(mp3_path))

        for mp3_path in successful_paths:
            if os.path.exists(mp3_path):
                os.remove(mp3_path)
        if os.path.exists(temp_dir):
            os.rmdir(temp_dir)

        conversion_service.complete_conversion(
            filename=filename,
            success=True,
            result_path=zip_path,
            conversion_type="mp3_chapters",
        )
    except Exception as finalize_error:
        conversion_service.complete_conversion(
            filename=filename,
            success=False,
            error_message=f"MP3 finalization failed: {finalize_error}",
            conversion_type="mp3_chapters",
        )


def mark_queue_failure(filename: str, conversion_type: str, error_message: str):
    conversion_service.complete_conversion(
        filename=filename,
        success=False,
        error_message=error_message,
        conversion_type=conversion_type,
    )
