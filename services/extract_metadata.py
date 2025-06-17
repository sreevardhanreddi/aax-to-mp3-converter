import subprocess
import json
import os
import hashlib
from pathlib import Path
from datetime import datetime, timedelta
import csv
import mimetypes


class AudiobookMetadataExtractor:
    def __init__(self, input_file):
        self.input_file = Path(input_file)
        self.metadata = {}

    def format_duration(self, seconds):
        """Convert seconds to H:MM:SS format"""
        try:
            seconds = float(seconds)
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            secs = int(seconds % 60)
            return f"{hours}:{minutes:02d}:{secs:02d}"
        except (ValueError, TypeError):
            return "Unknown"

    def format_bitrate(self, bitrate):
        """Format bitrate to readable format"""
        try:
            bitrate = int(bitrate)
            return str(bitrate)
        except (ValueError, TypeError):
            return "Unknown"

    def format_file_size(self, size_bytes):
        """Format file size to MB format like AAX info"""
        try:
            size_bytes = int(size_bytes)
            size_mb = size_bytes / (1024 * 1024)
            return f"{size_mb:.2f} MB"
        except (ValueError, TypeError):
            return "Unknown"

    def get_file_checksum(self):
        """Calculate SHA1 checksum of the file"""
        try:
            sha1_hash = hashlib.sha1()
            with open(self.input_file, "rb") as f:
                # Read file in chunks to handle large files
                for chunk in iter(lambda: f.read(4096), b""):
                    sha1_hash.update(chunk)
            return sha1_hash.hexdigest()
        except Exception as e:
            print(f"Error calculating file checksum: {e}")
            return "Unknown"

    def get_mime_type(self):
        """Get MIME type of the file"""
        mime_type, _ = mimetypes.guess_type(str(self.input_file))
        if mime_type:
            return mime_type

        # Handle specific audiobook formats
        suffix = self.input_file.suffix.lower()
        mime_map = {
            ".aax": "audio/aax",
            ".m4b": "audio/mp4",
            ".m4a": "audio/mp4",
            ".mp3": "audio/mpeg",
        }
        return mime_map.get(suffix, "Unknown")

    def extract_full_metadata(self):
        """Extract all metadata using ffprobe"""
        try:
            cmd = [
                "ffprobe",
                "-i",
                str(self.input_file),
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                "-show_chapters",
                "-v",
                "quiet",
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            self.metadata = json.loads(result.stdout)
            return True

        except subprocess.CalledProcessError as e:
            print(f"Error extracting metadata: {e}")
            if "Invalid data found when processing input" in str(e):
                print("This may be due to incorrect activation bytes for AAX file")
            return False
        except json.JSONDecodeError as e:
            print(f"Error parsing metadata: {e}")
            return False

    def get_file_info(self):
        """Get comprehensive file information in AAX format"""
        try:
            file_stats = self.input_file.stat()

            # Format last modified date like AAX format
            modified_time = datetime.fromtimestamp(file_stats.st_mtime)
            formatted_modified = modified_time.strftime("%m/%d/%Y, %I:%M:%S %p")

            # Calculate file checksum
            file_checksum = self.get_file_checksum()

            # Calculate AAX checksum if we have activation bytes
            aax_checksum = "Unknown"

            return {
                "Name": self.input_file.name,
                "Size": self.format_file_size(file_stats.st_size),
                "Type": self.get_mime_type(),
                "LastModified": formatted_modified,
                "Checksum": file_checksum,
            }
        except Exception as e:
            print(f"Error getting file info: {e}")
            return {}

    def get_track_info(self):
        """Extract track information in AAX format"""
        if "format" not in self.metadata:
            return {}

        format_info = self.metadata["format"]
        tags = format_info.get("tags", {})

        # Map common tag variations to standard names
        def get_tag_value(tag_names):
            for tag in tag_names:
                value = tags.get(tag) or tags.get(tag.upper()) or tags.get(tag.lower())
                if value:
                    return value
            return "Unknown"

        track_info = {
            "Title": get_tag_value(["title", "Title", "TITLE"]),
            "Narrator": get_tag_value(
                ["narrator", "artist", "Narrator", "NARRATOR", "ARTIST"]
            ),
            "Publisher": get_tag_value(
                ["publisher", "Publisher", "PUBLISHER", "label"]
            ),
            "Description": get_tag_value(
                ["description", "comment", "Description", "DESCRIPTION", "COMMENT"]
            ),
            "Duration": self.format_duration(format_info.get("duration", 0)),
            "Genre": get_tag_value(["genre", "Genre", "GENRE"]),
            "Recorded_Date": get_tag_value(["date", "year", "Date", "YEAR", "DATE"]),
            "Copyright": get_tag_value(["copyright", "Copyright", "COPYRIGHT"]),
            "Encoded_Date": get_tag_value(
                ["encoded_date", "creation_time", "ENCODED_DATE"]
            ),
            "OverallBitRate": self.format_bitrate(format_info.get("bit_rate", 0)),
            "Format": format_info.get("format_name", "Unknown").lower(),
        }

        return track_info

    def print_file_info(self, activation_bytes=None):
        """Print file information in AAX format"""
        print("File")
        file_info = self.get_file_info(activation_bytes)

        for key, value in file_info.items():
            print(f"{key}:")
            print(f"{value}")

    def print_track_info(self):
        """Print track information in AAX format"""
        print("\nTrack Infos")
        track_info = self.get_track_info()

        for key, value in track_info.items():
            print(f"{key}:")
            print(f"{value}")

    def print_chapters_detailed(self):
        """Print detailed chapter information"""
        print("\n" + "=" * 60)
        print("DETAILED CHAPTERS")
        print("=" * 60)

        if "chapters" not in self.metadata or not self.metadata["chapters"]:
            print("No chapters found")
            return

        chapters = self.metadata["chapters"]
        print(f"Total Chapters: {len(chapters)}\n")

        for i, chapter in enumerate(chapters, 1):
            start_time = float(chapter.get("start_time", 0))
            end_time = float(chapter.get("end_time", 0))
            duration = end_time - start_time

            print(f"Chapter {i:2d}:")
            print(f"  Start Time: {self.format_duration(start_time)}")
            print(f"  End Time: {self.format_duration(end_time)}")
            print(f"  Duration: {self.format_duration(duration)}")

            # Chapter title
            title = chapter.get("tags", {}).get("title", f"Chapter {i}")
            print(f"  Title: {title}")
            print()

    def test_activation_bytes(self, activation_bytes_list):
        """Test multiple activation bytes to find the correct one"""
        print("Testing activation bytes...")

        file_checksum = self.get_file_checksum()

        for i, activation_bytes in enumerate(activation_bytes_list, 1):
            print(f"Testing #{i}: {activation_bytes}")

            # Calculate AAX checksum
            aax_checksum = self.calculate_aax_checksum(activation_bytes)

            if aax_checksum:
                print(f"  AAX Checksum: {aax_checksum}")

                # Try to extract metadata with these activation bytes
                if self.extract_full_metadata(activation_bytes):
                    print(f"  ✓ Successfully extracted metadata")
                    return activation_bytes
                else:
                    print(f"  ✗ Failed to extract metadata")
            else:
                print(f"  ✗ Failed to calculate checksum")

        return None

    def export_aax_format_json(self, output_file=None, activation_bytes=None):
        """Export metadata in AAX-like format to JSON"""
        if not output_file:
            output_file = self.input_file.stem + "_aax_metadata.json"

        try:
            export_data = {
                "File": self.get_file_info(activation_bytes),
                "Track_Infos": self.get_track_info(),
                "Chapters": [],
                "extraction_time": datetime.now().isoformat(),
            }

            # Add chapter information
            if "chapters" in self.metadata and self.metadata["chapters"]:
                for i, chapter in enumerate(self.metadata["chapters"], 1):
                    start_time = float(chapter.get("start_time", 0))
                    end_time = float(chapter.get("end_time", 0))
                    duration = end_time - start_time
                    title = chapter.get("tags", {}).get("title", f"Chapter {i}")

                    export_data["Chapters"].append(
                        {
                            "Chapter": i,
                            "Title": title,
                            "Start_Time": self.format_duration(start_time),
                            "End_Time": self.format_duration(end_time),
                            "Duration": self.format_duration(duration),
                        }
                    )

            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)

            print(f"\nAAX-format metadata exported to: {output_file}")
            return True
        except Exception as e:
            print(f"Error exporting to JSON: {e}")
            return False

    def print_all_metadata_aax_format(self, activation_bytes=None):
        """Print all metadata in AAX format"""
        print("=" * 60)

        # First, try to extract metadata
        success = self.extract_full_metadata(activation_bytes)

        # Print file info regardless of metadata extraction success
        self.print_file_info(activation_bytes)

        if success:
            self.print_track_info()
            self.print_chapters_detailed()
        else:
            print("\nCould not extract detailed metadata.")
            if self.input_file.suffix.lower() == ".aax":
                print("For AAX files, you may need correct activation bytes.")

        return success
