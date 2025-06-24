import os
from typing import Optional, Callable
from .thread_manager import thread_manager
from .conversion_service import conversion_service
from .extract_activation_bytes import AAXProcessor
from config import logger


class ConversionOrchestrator:
    """Orchestrates conversion operations with thread management"""

    def __init__(self):
        # Register cleanup callback with thread manager
        thread_manager.add_cleanup_callback(self._cleanup_conversions)
        self._processor = None

    @property
    def processor(self):
        """Lazy initialization of AAXProcessor"""
        if self._processor is None:
            self._processor = AAXProcessor()
        return self._processor

    def _cleanup_conversions(self):
        """Cleanup callback for thread manager"""
        conversion_service.cleanup_active_conversions()

    def _create_progress_callback(
        self, filename: str, conversion_type: str
    ) -> Callable:
        """Create a progress callback function with cancellation support"""

        def progress_callback(progress_percent: float) -> bool:
            # Check if shutdown was requested
            if thread_manager.is_shutdown_requested():
                logger.info(
                    f"Shutdown requested, cancelling {conversion_type} conversion of {filename}"
                )
                return False

            conversion_service.update_progress(
                filename, round(progress_percent, 1), "converting", conversion_type
            )
            logger.info(
                f"{conversion_type} conversion progress for {filename}: {progress_percent:.1f}%"
            )
            return True

        return progress_callback

    def convert_file_background(
        self,
        filename: str,
        aax_file_path: str,
        m4a_file_path: str,
        activation_bytes: str,
    ):
        """Background function to handle file conversion with progress tracking"""
        try:
            if os.path.exists(m4a_file_path):
                os.remove(m4a_file_path)

            # Start conversion tracking
            if not conversion_service.start_conversion(filename, "m4a"):
                logger.warning(f"Could not start conversion tracking for {filename}")
                return

            # Create progress callback
            progress_callback = self._create_progress_callback(filename, "m4a")

            # Update status to converting
            conversion_service.update_progress(filename, 0, "converting", "m4a")

            success = self.processor.convert_to_m4a(
                aax_file_path, m4a_file_path, activation_bytes, progress_callback
            )

            # Mark conversion as completed or failed
            if success and not thread_manager.is_shutdown_requested():
                conversion_service.complete_conversion(
                    filename,
                    success=True,
                    result_path=m4a_file_path,
                    conversion_type="m4a",
                )
            else:
                error_msg = (
                    "Conversion failed"
                    if not thread_manager.is_shutdown_requested()
                    else "Server interrupted"
                )
                conversion_service.complete_conversion(
                    filename,
                    success=False,
                    error_message=error_msg,
                    conversion_type="m4a",
                )

        except Exception as e:
            logger.error(f"Error in M4A conversion: {e}")
            conversion_service.complete_conversion(
                filename, success=False, error_message=str(e), conversion_type="m4a"
            )

    def convert_mp3_chapters_background(
        self,
        filename: str,
        aax_file_path: str,
        output_dir: str,
        activation_bytes: str,
        parallel: bool = False,
    ):
        """Background function to handle MP3 chapter conversion with progress tracking"""
        try:
            # Start conversion tracking
            if not conversion_service.start_conversion(filename, "mp3_chapters"):
                logger.warning(
                    f"Could not start MP3 conversion tracking for {filename}"
                )
                return

            # Create progress callback
            progress_callback = self._create_progress_callback(filename, "mp3_chapters")

            # Update status to converting
            conversion_service.update_progress(
                filename, 0, "converting", "mp3_chapters"
            )

            # Use parallel or sequential conversion based on parameter
            if parallel:
                result = self.processor.convert_to_mp3_chapters_parallel(
                    aax_file_path,
                    output_dir,
                    activation_bytes,
                    progress_callback,
                    max_workers=None,
                )
            else:
                result = self.processor.convert_to_mp3_chapters(
                    aax_file_path, output_dir, activation_bytes, progress_callback
                )

            # Mark conversion as completed or failed
            if result["success"] and not thread_manager.is_shutdown_requested():
                conversion_service.complete_conversion(
                    filename,
                    success=True,
                    result_path=result["zip_path"],
                    conversion_type="mp3_chapters",
                )
            else:
                error_msg = (
                    result.get("error", "MP3 conversion failed")
                    if not thread_manager.is_shutdown_requested()
                    else "Server interrupted"
                )
                conversion_service.complete_conversion(
                    filename,
                    success=False,
                    error_message=error_msg,
                    conversion_type="mp3_chapters",
                )

        except Exception as e:
            logger.error(f"Error in MP3 conversion: {e}")
            conversion_service.complete_conversion(
                filename,
                success=False,
                error_message=str(e),
                conversion_type="mp3_chapters",
            )

    def start_m4a_conversion(
        self,
        filename: str,
        aax_file_path: str,
        m4a_file_path: str,
        activation_bytes: str,
    ) -> bool:
        """Start M4A conversion in background thread"""
        try:
            # Check if conversion is already in progress
            if conversion_service.is_conversion_active(filename, "m4a"):
                logger.warning(f"M4A conversion already in progress for {filename}")
                return False

            # Start conversion in background thread
            thread_manager.start_thread(
                target=self.convert_file_background,
                args=(filename, aax_file_path, m4a_file_path, activation_bytes),
                name=f"m4a_conversion_{filename}",
            )

            logger.info(f"Started M4A conversion thread for {filename}")
            return True

        except Exception as e:
            logger.error(f"Error starting M4A conversion: {e}")
            return False

    def start_mp3_conversion(
        self,
        filename: str,
        aax_file_path: str,
        output_dir: str,
        activation_bytes: str,
        parallel: bool = False,
    ) -> bool:
        """Start MP3 chapters conversion in background thread"""
        try:
            # Check if conversion is already in progress
            if conversion_service.is_conversion_active(filename, "mp3_chapters"):
                logger.warning(f"MP3 conversion already in progress for {filename}")
                return False

            # Start conversion in background thread
            conversion_type = "parallel" if parallel else "sequential"
            thread_name = f"mp3_{conversion_type}_conversion_{filename}"

            thread_manager.start_thread(
                target=self.convert_mp3_chapters_background,
                args=(filename, aax_file_path, output_dir, activation_bytes, parallel),
                name=thread_name,
            )

            logger.info(
                f"Started MP3 {conversion_type} conversion thread for {filename}"
            )
            return True

        except Exception as e:
            logger.error(f"Error starting MP3 conversion: {e}")
            return False

    def get_conversion_status(self, filename: str, conversion_type: str = "m4a"):
        """Get conversion progress status"""
        progress_data = conversion_service.get_progress(filename, conversion_type)

        # If completed, add download URL
        if progress_data["status"] == "completed":
            if conversion_type == "m4a":
                progress_data["download_url"] = f"/download/{filename}"
            elif conversion_type == "mp3_chapters":
                progress_data["download_url"] = f"/download/mp3/{filename}"

        return progress_data

    def get_active_conversions(self):
        """Get all currently active conversions for monitoring"""
        active_conversions = conversion_service.get_all_active_conversions()
        thread_info = {
            "active_threads": thread_manager.get_active_thread_names(),
            "thread_count": thread_manager.get_active_thread_count(),
        }

        return {
            "active_conversions": active_conversions,
            "count": len(active_conversions),
            "thread_info": thread_info,
        }


# Global instance
conversion_orchestrator = ConversionOrchestrator()
