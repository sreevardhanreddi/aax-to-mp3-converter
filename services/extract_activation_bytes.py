import subprocess
import re
import os
import json
import sys
import zipfile
import tempfile
from pathlib import Path
from config import logger
import requests
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TALB, TCON, APIC, TRCK, TPE2
import concurrent.futures
import threading
from typing import Dict, Any, Optional, Callable


class AAXProcessor:
    def __init__(self, tables_path="/app/audible_rainbow_tables"):
        """
        Initialize AAX processor.

        Args:
            tables_path (str): Path to the directory containing rainbow tables (*.rtc files)
        """
        self.tables_path = Path(tables_path)
        self.rcrack_binary = self.find_rcrack_binary()

    def find_rcrack_binary(self):
        """Find the rcrack binary executable."""
        # Check common locations
        possible_paths = [
            "./rcrack",
            "./run/rcrack",
            "rcrack",
            "/app/audible_rainbow_tables/rcrack",
        ]

        for path in possible_paths:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path

        # Check if rcrack is in PATH
        try:
            subprocess.run(["rcrack", "--help"], capture_output=True, check=False)
            return "rcrack"
        except FileNotFoundError:
            pass

        raise FileNotFoundError(
            "rcrack binary not found. Please ensure it's installed or in the correct path."
        )

    def extract_sha1_checksum(self, aax_file):
        """
        Extract SHA1 checksum from AAX file using ffprobe.

        Args:
            aax_file (str): Path to the AAX file

        Returns:
            str: SHA1 checksum if found, None otherwise
        """
        try:
            # Run ffprobe to get file information
            result = subprocess.run(
                ["ffprobe", aax_file],
                capture_output=True,
                text=True,
                # stderr=subprocess.STDOUT,
            )

            output = result.stdout + result.stderr

            # Search for the checksum pattern
            checksum_pattern = r"\[aax\] file checksum == ([a-fA-F0-9]+)"
            match = re.search(checksum_pattern, output)

            if match:
                checksum = match.group(1)
                logger.info(f"Found SHA1 checksum: {checksum}")
                return checksum
            else:
                logger.info("No checksum found in ffprobe output")
                logger.info("Output:", output)
                return None

        except FileNotFoundError:
            raise FileNotFoundError("ffprobe not found. Please install FFmpeg.")
        except subprocess.CalledProcessError as e:
            logger.error(f"Error running ffprobe: {e}")
            return None

    def recover_activation_bytes(self, checksum):
        """
        Recover activation bytes using rainbow tables.

        Args:
            checksum (str): SHA1 checksum to crack

        Returns:
            str: Activation bytes if found, None otherwise
        """
        try:
            logger.info(f"Attempting to crack checksum: {checksum}")
            logger.info(f"Using rainbow tables in: {self.tables_path}")

            orginal_dir = os.getcwd()
            os.chdir(self.tables_path)
            # Run rcrack with the checksum
            result = subprocess.run(
                [self.rcrack_binary, str(self.tables_path), "-h", checksum],
                capture_output=True,
                text=True,
            )
            logger.info(" ".join(result.args))

            output = result.stdout + result.stderr
            logger.info("RainbowCrack output:")
            logger.info(output)

            # Parse the output to find activation bytes
            # Look for patterns like "plaintext of [hash] is [bytes]"
            activation_pattern = r"hex:([a-fA-F0-9]+)"
            match = re.search(activation_pattern, output)

            if match:
                activation_bytes = match.group(1)
                logger.info(
                    f"Successfully recovered activation bytes: {activation_bytes}"
                )
                return activation_bytes
            else:
                logger.info("Could not find activation bytes in output")
                return None

        except subprocess.CalledProcessError as e:
            logger.error(f"Error running rcrack: {e}")
            return None
        finally:
            os.chdir(orginal_dir)

    def process_aax_file(self, aax_file):
        """
        Complete process: extract checksum and recover activation bytes.

        Args:
            aax_file (str): Path to the AAX file

        Returns:
            dict: Dictionary containing checksum and activation_bytes
        """
        if not os.path.isfile(aax_file):
            raise FileNotFoundError(f"AAX file not found: {aax_file}")

        logger.info(f"Processing AAX file: {aax_file}")

        # Step 1: Extract SHA1 checksum
        checksum = self.extract_sha1_checksum(aax_file)

        if not checksum:
            return {"error": "Could not extract SHA1 checksum"}

        with open("activation_bytes.json", "r+") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = {}

            if checksum in data:
                logger.info(f"Activation bytes already found for checksum: {checksum}")
                return {"checksum": checksum, "activation_bytes": data[checksum]}
            else:
                logger.info(f"Activation bytes not found for checksum: {checksum}")

        # Step 2: Recover activation bytes
        activation_bytes = self.recover_activation_bytes(checksum)
        if not activation_bytes:
            return {"checksum": checksum, "error": "Could not recover activation bytes"}

        # if activation bytes is found store it in a json file
        with open("activation_bytes.json", "w+") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = {}

            data[checksum] = activation_bytes
            json.dump(data, f, indent=4)

        return {"checksum": checksum, "activation_bytes": activation_bytes}

    def get_activation_bytes_via_http_api(self, checksum):
        url = "https://aaxactivationserviceapi.azurewebsites.net/api/v1/activation/{}".format(
            checksum
        )
        logger.info(f"Getting activation bytes via HTTP API: {url}")
        response = requests.get(url)
        if response.status_code == 200:
            logger.info(f"Activation bytes: {response.text}")
            return response.text
        else:
            logger.error(f"Failed to get activation bytes: {response.status_code}")
            return None

    def get_activation_bytes(self, aax_file):
        checksum = self.extract_sha1_checksum(aax_file)
        if not checksum:
            return {"checksum": checksum, "error": "Could not extract SHA1 checksum"}
        # check if activation bytes is already in the json file
        with open("activation_bytes.json", "r") as f:
            data = json.load(f)
            if checksum in data:
                logger.info(
                    f"Activation bytes already found for checksum {checksum} in json file"
                )
                return {"checksum": checksum, "activation_bytes": data[checksum]}
            else:
                logger.info(
                    f"Activation bytes not found for checksum {checksum} in json file"
                )

        activation_bytes = self.get_activation_bytes_via_http_api(checksum)
        if not activation_bytes:
            logger.info("Could not get activation bytes")
            logger.info("Trying to recover activation bytes using rainbow tables")
            activation_bytes = self.recover_activation_bytes(checksum)
            if not activation_bytes:
                return {"checksum": checksum, "error": "Could not get activation bytes"}

                return {"checksum": checksum, "activation_bytes": activation_bytes}

    def get_duration(self, aax_file):
        """Get duration of AAX file in seconds using ffprobe."""
        try:
            cmd = [
                "ffprobe",
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                aax_file,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            duration = float(result.stdout.strip())
            return duration
        except (subprocess.CalledProcessError, ValueError):
            logger.error("Could not get duration from AAX file")
            return None

    def convert_to_m4a(
        self, aax_file, output_path, activation_bytes, progress_callback=None
    ):
        """
        Convert AAX file to M4A format using ffmpeg with progress tracking.

        Args:
            aax_file (str): Path to the AAX file
            output_path (str): Path for the output M4A file
            activation_bytes (str): Activation bytes for decryption
            progress_callback (callable): Function to call with progress updates

        Returns:
            bool: True if conversion successful, False otherwise
        """
        try:
            logger.info(f"Converting {aax_file} to {output_path}")

            # Get total duration for progress calculation
            total_duration = self.get_duration(aax_file)
            if not total_duration:
                logger.warning("Could not get duration, progress will be estimated")

            # FFmpeg command to convert AAX to M4A
            cmd = [
                "ffmpeg",
                "-activation_bytes",
                activation_bytes,
                "-i",
                aax_file,
                "-c",
                "copy",
                "-progress",
                "pipe:1",  # Output progress to stdout
                "-y",  # Overwrite output file if it exists
                output_path,
            ]

            # Start ffmpeg process
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )

            # Monitor progress
            progress_data = {}
            while True:
                output = process.stdout.readline()
                if output == "" and process.poll() is not None:
                    break

                if output:
                    line = output.strip()
                    if "=" in line:
                        key, value = line.split("=", 1)
                        progress_data[key] = value

                        # Check for time progress
                        if (
                            key == "out_time_ms"
                            and total_duration
                            and progress_callback
                        ):
                            try:
                                # out_time_ms is in microseconds
                                current_time = (
                                    int(value) / 1000000.0
                                )  # Convert to seconds
                                progress_percent = min(
                                    100, (current_time / total_duration) * 100
                                )
                                # Check if callback returns False (cancellation signal)
                                callback_result = progress_callback(progress_percent)
                                if callback_result is False:
                                    logger.info(
                                        "Conversion cancelled by progress callback"
                                    )
                                    process.terminate()
                                    return False
                            except (ValueError, ZeroDivisionError):
                                pass

                        # Alternative progress tracking using 'time' field
                        elif key == "time" and total_duration and progress_callback:
                            try:
                                # Parse time format HH:MM:SS.ss
                                time_parts = value.split(":")
                                if len(time_parts) == 3:
                                    hours = float(time_parts[0])
                                    minutes = float(time_parts[1])
                                    seconds = float(time_parts[2])
                                    current_time = hours * 3600 + minutes * 60 + seconds
                                    progress_percent = min(
                                        100, (current_time / total_duration) * 100
                                    )
                                    # Check if callback returns False (cancellation signal)
                                    callback_result = progress_callback(
                                        progress_percent
                                    )
                                    if callback_result is False:
                                        logger.info(
                                            "Conversion cancelled by progress callback"
                                        )
                                        process.terminate()
                                        return False
                            except (ValueError, IndexError, ZeroDivisionError):
                                pass

            # Wait for process to complete
            process.wait()

            if process.returncode == 0:
                logger.info(f"Successfully converted to {output_path}")
                if progress_callback:
                    progress_callback(100)  # Ensure 100% completion
                return True
            else:
                stderr_output = process.stderr.read()
                logger.error(
                    f"FFmpeg conversion failed with return code {process.returncode}"
                )
                logger.error(f"FFmpeg stderr: {stderr_output}")
                return False

        except Exception as e:
            logger.error(f"Error converting AAX to M4A: {e}")
            return False

    def convert_to_mp3_chapters(
        self, aax_file, output_dir, activation_bytes, progress_callback=None
    ):
        """
        Convert AAX file to multiple MP3 files (one per chapter) with metadata and create a zip file.

        Args:
            aax_file (str): Path to the AAX file
            output_dir (str): Directory to store the MP3 files and zip
            activation_bytes (str): Activation bytes for decryption
            progress_callback (callable): Function to call with progress updates

        Returns:
            dict: Result containing success status and zip file path
        """
        try:
            logger.info(f"Converting {aax_file} to MP3 chapters in {output_dir}")

            # First, extract metadata and chapters using ffprobe
            metadata_cmd = [
                "ffprobe",
                "-activation_bytes",
                activation_bytes,
                "-i",
                aax_file,
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

            # Extract general metadata
            format_info = metadata.get("format", {})
            tags = format_info.get("tags", {})
            chapters = metadata.get("chapters", [])

            if not chapters:
                return {"success": False, "error": "No chapters found in AAX file"}

            # Extract album art using the more reliable method
            album_art_data = None
            try:
                with tempfile.NamedTemporaryFile(
                    suffix=".jpg", delete=False
                ) as temp_art:
                    art_cmd = [
                        "ffmpeg",
                        "-y",  # Overwrite output file if exists
                        "-activation_bytes",
                        activation_bytes,
                        "-i",
                        aax_file,
                        "-an",  # No audio
                        "-vcodec",
                        "copy",
                        "-map",
                        "0:v:0",  # Map first video stream (album art)
                        temp_art.name,
                    ]
                    result = subprocess.run(art_cmd, capture_output=True, check=True)

                    # Read the extracted album art
                    with open(temp_art.name, "rb") as f:
                        album_art_data = f.read()

                    # Clean up temporary file
                    os.unlink(temp_art.name)
                    logger.info(
                        f"Successfully extracted album art ({len(album_art_data)} bytes)"
                    )

            except subprocess.CalledProcessError as e:
                logger.warning(f"Primary album art extraction failed: {e}")
                # Try alternative method without -map parameter
                try:
                    with tempfile.NamedTemporaryFile(
                        suffix=".jpg", delete=False
                    ) as temp_art:
                        alt_art_cmd = [
                            "ffmpeg",
                            "-y",
                            "-activation_bytes",
                            activation_bytes,
                            "-i",
                            aax_file,
                            "-an",
                            "-vcodec",
                            "copy",
                            temp_art.name,
                        ]
                        subprocess.run(alt_art_cmd, capture_output=True, check=True)

                        with open(temp_art.name, "rb") as f:
                            album_art_data = f.read()

                        os.unlink(temp_art.name)
                        logger.info(
                            f"Successfully extracted album art with alternative method ({len(album_art_data)} bytes)"
                        )

                except Exception as alt_e:
                    logger.warning(
                        f"Alternative album art extraction also failed: {alt_e}"
                    )
                    album_art_data = None

            except Exception as e:
                logger.error(f"Error extracting album art: {e}")
                album_art_data = None

            # Create temporary directory for MP3 files
            temp_dir = tempfile.mkdtemp()
            mp3_files = []

            total_chapters = len(chapters)

            for i, chapter in enumerate(chapters):
                chapter_start = float(chapter.get("start_time", 0))
                chapter_end = float(chapter.get("end_time", 0))
                duration = chapter_end - chapter_start

                # Get chapter title
                chapter_tags = chapter.get("tags", {})
                chapter_title = (
                    chapter_tags.get("title")
                    or chapter_tags.get("Title")
                    or f"Chapter {i + 1:02d}"
                )

                # Clean filename
                safe_title = "".join(
                    c for c in chapter_title if c.isalnum() or c in (" ", "-", "_")
                ).rstrip()
                mp3_filename = f"{i + 1:02d} - {safe_title}.mp3"
                mp3_path = os.path.join(temp_dir, mp3_filename)

                # Convert chapter to MP3
                chapter_cmd = [
                    "ffmpeg",
                    "-activation_bytes",
                    activation_bytes,
                    "-i",
                    aax_file,
                    "-ss",
                    str(chapter_start),
                    "-t",
                    str(duration),
                    "-acodec",
                    "libmp3lame",
                    "-ab",
                    "128k",
                    "-ar",
                    "44100",
                    "-y",
                    mp3_path,
                ]

                subprocess.run(chapter_cmd, capture_output=True, check=True)

                # Add metadata to MP3 file
                if os.path.exists(mp3_path):
                    try:
                        audio = MP3(mp3_path, ID3=ID3)

                        # Add ID3 tag if not present
                        if audio.tags is None:
                            audio.add_tags()

                        # Clear any existing tags to start fresh
                        audio.tags.clear()

                        # Add metadata
                        audio.tags.add(TIT2(encoding=3, text=chapter_title))

                        # Author/Artist
                        author = (
                            tags.get("artist")
                            or tags.get("narrator")
                            or tags.get("ARTIST")
                            or "Unknown Author"
                        )
                        audio.tags.add(TPE1(encoding=3, text=author))
                        audio.tags.add(TPE2(encoding=3, text=author))  # Album artist

                        # Album/Book title
                        album = (
                            tags.get("title") or tags.get("TITLE") or "Unknown Album"
                        )
                        audio.tags.add(TALB(encoding=3, text=album))

                        # Genre
                        genre = tags.get("genre") or tags.get("GENRE") or "Audiobook"
                        audio.tags.add(TCON(encoding=3, text=genre))

                        # Track number
                        audio.tags.add(
                            TRCK(encoding=3, text=f"{i + 1}/{total_chapters}")
                        )

                        # Add album art if available
                        if album_art_data:
                            try:
                                audio.tags.add(
                                    APIC(
                                        encoding=3,
                                        mime="image/jpeg",
                                        type=3,  # Cover (front)
                                        desc="Cover",
                                        data=album_art_data,
                                    )
                                )
                                logger.info(
                                    f"Added album art to {chapter_title} ({len(album_art_data)} bytes)"
                                )
                            except Exception as art_error:
                                logger.error(
                                    f"Error adding album art to {mp3_path}: {art_error}"
                                )
                        else:
                            logger.warning(
                                f"No album art data available for {chapter_title}"
                            )

                        # Save all changes
                        audio.save(v2_version=3)  # Use ID3v2.3 for better compatibility
                        logger.info(f"Successfully processed chapter: {chapter_title}")
                        mp3_files.append(mp3_path)

                    except Exception as e:
                        logger.error(f"Error adding metadata to {mp3_path}: {e}")
                        # Still add to list even if metadata fails
                        mp3_files.append(mp3_path)

                # Update progress
                if progress_callback:
                    progress = (
                        (i + 1) / total_chapters
                    ) * 90  # Reserve 10% for zipping
                    progress_callback(progress)

            # Create zip file
            base_name = os.path.splitext(os.path.basename(aax_file))[0]
            zip_filename = f"{base_name}_chapters.zip"
            zip_path = os.path.join(output_dir, zip_filename)

            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                for mp3_file in mp3_files:
                    if os.path.exists(mp3_file):
                        arcname = os.path.basename(mp3_file)
                        zipf.write(mp3_file, arcname)

            # Cleanup temporary files
            for mp3_file in mp3_files:
                if os.path.exists(mp3_file):
                    os.remove(mp3_file)
            os.rmdir(temp_dir)

            if progress_callback:
                progress_callback(100)

            logger.info(f"Successfully created MP3 chapters zip: {zip_path}")
            return {
                "success": True,
                "zip_path": zip_path,
                "zip_filename": zip_filename,
                "chapter_count": len(mp3_files),
            }

        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg error during MP3 conversion: {e}")
            return {"success": False, "error": f"FFmpeg error: {e}"}
        except Exception as e:
            logger.error(f"Error converting AAX to MP3 chapters: {e}")
            return {"success": False, "error": str(e)}

    def _convert_single_chapter(
        self,
        chapter_data: Dict[str, Any],
        aax_file: str,
        activation_bytes: str,
        temp_dir: str,
        album_art_data: Optional[bytes],
        total_chapters: int,
        progress_lock: threading.Lock,
        processed_count: list,  # Use list to make it mutable for threading
        progress_callback: Optional[Callable] = None,
    ) -> Optional[str]:
        """
        Convert a single chapter to MP3 with metadata.

        Args:
            chapter_data: Dictionary containing chapter info and metadata
            aax_file: Path to the AAX file
            activation_bytes: Activation bytes for decryption
            temp_dir: Temporary directory for output
            album_art_data: Album art bytes to embed
            total_chapters: Total number of chapters
            progress_lock: Threading lock for progress updates
            processed_count: List containing count of processed chapters
            progress_callback: Function to call with progress updates

        Returns:
            str: Path to the created MP3 file, or None if failed
        """
        try:
            i = chapter_data["index"]
            chapter = chapter_data["chapter"]
            tags = chapter_data["tags"]

            chapter_start = float(chapter.get("start_time", 0))
            chapter_end = float(chapter.get("end_time", 0))
            duration = chapter_end - chapter_start

            # Get chapter title
            chapter_tags = chapter.get("tags", {})
            chapter_title = (
                chapter_tags.get("title")
                or chapter_tags.get("Title")
                or f"Chapter {i + 1:02d}"
            )

            # Clean filename
            safe_title = "".join(
                c for c in chapter_title if c.isalnum() or c in (" ", "-", "_")
            ).rstrip()
            mp3_filename = f"{i + 1:02d} - {safe_title}.mp3"
            mp3_path = os.path.join(temp_dir, mp3_filename)

            # Convert chapter to MP3
            chapter_cmd = [
                "ffmpeg",
                "-activation_bytes",
                activation_bytes,
                "-i",
                aax_file,
                "-ss",
                str(chapter_start),
                "-t",
                str(duration),
                "-acodec",
                "libmp3lame",
                "-ab",
                "128k",
                "-ar",
                "44100",
                "-y",
                mp3_path,
            ]

            subprocess.run(chapter_cmd, capture_output=True, check=True)

            # Add metadata to MP3 file
            if os.path.exists(mp3_path):
                try:
                    audio = MP3(mp3_path, ID3=ID3)

                    # Add ID3 tag if not present
                    if audio.tags is None:
                        audio.add_tags()

                    # Clear any existing tags to start fresh
                    audio.tags.clear()

                    # Add metadata
                    audio.tags.add(TIT2(encoding=3, text=chapter_title))

                    # Author/Artist
                    author = (
                        tags.get("artist")
                        or tags.get("narrator")
                        or tags.get("ARTIST")
                        or "Unknown Author"
                    )
                    audio.tags.add(TPE1(encoding=3, text=author))
                    audio.tags.add(TPE2(encoding=3, text=author))  # Album artist

                    # Album/Book title
                    album = tags.get("title") or tags.get("TITLE") or "Unknown Album"
                    audio.tags.add(TALB(encoding=3, text=album))

                    # Genre
                    genre = tags.get("genre") or tags.get("GENRE") or "Audiobook"
                    audio.tags.add(TCON(encoding=3, text=genre))

                    # Track number
                    audio.tags.add(TRCK(encoding=3, text=f"{i + 1}/{total_chapters}"))

                    # Add album art if available
                    if album_art_data:
                        try:
                            audio.tags.add(
                                APIC(
                                    encoding=3,
                                    mime="image/jpeg",
                                    type=3,  # Cover (front)
                                    desc="Cover",
                                    data=album_art_data,
                                )
                            )
                            logger.info(
                                f"Added album art to {chapter_title} ({len(album_art_data)} bytes)"
                            )
                        except Exception as art_error:
                            logger.error(
                                f"Error adding album art to {mp3_path}: {art_error}"
                            )

                    # Save all changes
                    audio.save(v2_version=3)  # Use ID3v2.3 for better compatibility
                    logger.info(f"Successfully processed chapter: {chapter_title}")

                except Exception as e:
                    logger.error(f"Error adding metadata to {mp3_path}: {e}")
                    # Continue even if metadata fails

                # Update progress with thread safety
                with progress_lock:
                    processed_count[0] += 1
                    if progress_callback:
                        progress = (
                            processed_count[0] / total_chapters
                        ) * 90  # Reserve 10% for zipping
                        callback_result = progress_callback(progress)
                        if callback_result is False:
                            logger.info(
                                "Chapter conversion cancelled by progress callback"
                            )
                            return None

                return mp3_path

        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg error for chapter {i + 1}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error converting chapter {i + 1}: {e}")
            return None

    def convert_to_mp3_chapters_parallel(
        self,
        aax_file: str,
        output_dir: str,
        activation_bytes: str,
        progress_callback: Optional[Callable] = None,
        max_workers: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Convert AAX file to multiple MP3 files (one per chapter) with parallel processing.

        Args:
            aax_file (str): Path to the AAX file
            output_dir (str): Directory to store the MP3 files and zip
            activation_bytes (str): Activation bytes for decryption
            progress_callback (callable): Function to call with progress updates
            max_workers (int): Maximum number of parallel workers (defaults to CPU count)

        Returns:
            dict: Result containing success status and zip file path
        """
        try:
            logger.info(
                f"Converting {aax_file} to MP3 chapters (parallel) in {output_dir}"
            )

            # Set default max_workers to CPU count or 4, whichever is smaller
            if max_workers is None:
                max_workers = min(
                    os.cpu_count() or 4, 4
                )  # Limit to 4 to avoid overwhelming system

            logger.info(f"Using {max_workers} parallel workers for chapter conversion")

            # First, extract metadata and chapters using ffprobe
            metadata_cmd = [
                "ffprobe",
                "-activation_bytes",
                activation_bytes,
                "-i",
                aax_file,
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

            # Extract general metadata
            format_info = metadata.get("format", {})
            tags = format_info.get("tags", {})
            chapters = metadata.get("chapters", [])

            if not chapters:
                return {"success": False, "error": "No chapters found in AAX file"}

            # Extract album art using the more reliable method
            album_art_data = None
            try:
                with tempfile.NamedTemporaryFile(
                    suffix=".jpg", delete=False
                ) as temp_art:
                    art_cmd = [
                        "ffmpeg",
                        "-y",  # Overwrite output file if exists
                        "-activation_bytes",
                        activation_bytes,
                        "-i",
                        aax_file,
                        "-an",  # No audio
                        "-vcodec",
                        "copy",
                        "-map",
                        "0:v:0",  # Map first video stream (album art)
                        temp_art.name,
                    ]
                    result = subprocess.run(art_cmd, capture_output=True, check=True)

                    # Read the extracted album art
                    with open(temp_art.name, "rb") as f:
                        album_art_data = f.read()

                    # Clean up temporary file
                    os.unlink(temp_art.name)
                    logger.info(
                        f"Successfully extracted album art ({len(album_art_data)} bytes)"
                    )

            except subprocess.CalledProcessError as e:
                logger.warning(f"Primary album art extraction failed: {e}")
                # Try alternative method without -map parameter
                try:
                    with tempfile.NamedTemporaryFile(
                        suffix=".jpg", delete=False
                    ) as temp_art:
                        alt_art_cmd = [
                            "ffmpeg",
                            "-y",
                            "-activation_bytes",
                            activation_bytes,
                            "-i",
                            aax_file,
                            "-an",
                            "-vcodec",
                            "copy",
                            temp_art.name,
                        ]
                        subprocess.run(alt_art_cmd, capture_output=True, check=True)

                        with open(temp_art.name, "rb") as f:
                            album_art_data = f.read()

                        os.unlink(temp_art.name)
                        logger.info(
                            f"Successfully extracted album art with alternative method ({len(album_art_data)} bytes)"
                        )

                except Exception as alt_e:
                    logger.warning(
                        f"Alternative album art extraction also failed: {alt_e}"
                    )
                    album_art_data = None

            except Exception as e:
                logger.error(f"Error extracting album art: {e}")
                album_art_data = None

            # Create temporary directory for MP3 files
            temp_dir = tempfile.mkdtemp()
            total_chapters = len(chapters)

            # Prepare chapter data for parallel processing
            chapter_tasks = []
            for i, chapter in enumerate(chapters):
                chapter_data = {"index": i, "chapter": chapter, "tags": tags}
                chapter_tasks.append(chapter_data)

            # Thread-safe progress tracking
            progress_lock = threading.Lock()
            processed_count = [0]  # Use list to make it mutable for threading

            # Process chapters in parallel
            mp3_files = []
            failed_chapters = []

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=max_workers
            ) as executor:
                # Submit all chapter conversion tasks
                future_to_chapter = {
                    executor.submit(
                        self._convert_single_chapter,
                        chapter_data,
                        aax_file,
                        activation_bytes,
                        temp_dir,
                        album_art_data,
                        total_chapters,
                        progress_lock,
                        processed_count,
                        progress_callback,
                    ): chapter_data["index"]
                    for chapter_data in chapter_tasks
                }

                # Collect results as they complete
                for future in concurrent.futures.as_completed(future_to_chapter):
                    chapter_index = future_to_chapter[future]
                    try:
                        mp3_path = future.result()
                        if mp3_path and os.path.exists(mp3_path):
                            mp3_files.append(mp3_path)
                        else:
                            failed_chapters.append(chapter_index + 1)
                    except Exception as exc:
                        logger.error(
                            f"Chapter {chapter_index + 1} generated an exception: {exc}"
                        )
                        failed_chapters.append(chapter_index + 1)

            # Log any failed chapters
            if failed_chapters:
                logger.warning(f"Failed to convert chapters: {failed_chapters}")

            # Sort MP3 files by filename to ensure correct order
            mp3_files.sort()

            # Create zip file
            base_name = os.path.splitext(os.path.basename(aax_file))[0]
            zip_filename = f"{base_name}_chapters.zip"
            zip_path = os.path.join(output_dir, zip_filename)

            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                for mp3_file in mp3_files:
                    if os.path.exists(mp3_file):
                        arcname = os.path.basename(mp3_file)
                        zipf.write(mp3_file, arcname)

            # Cleanup temporary files
            for mp3_file in mp3_files:
                if os.path.exists(mp3_file):
                    os.remove(mp3_file)
            os.rmdir(temp_dir)

            if progress_callback:
                progress_callback(100)

            successful_chapters = len(mp3_files)
            logger.info(
                f"Successfully created MP3 chapters zip: {zip_path} "
                f"({successful_chapters}/{total_chapters} chapters converted)"
            )

            return {
                "success": True,
                "zip_path": zip_path,
                "zip_filename": zip_filename,
                "chapter_count": successful_chapters,
                "failed_chapters": failed_chapters,
                "total_chapters": total_chapters,
            }

        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg error during parallel MP3 conversion: {e}")
            return {"success": False, "error": f"FFmpeg error: {e}"}
        except Exception as e:
            logger.error(f"Error converting AAX to MP3 chapters (parallel): {e}")
            return {"success": False, "error": str(e)}
