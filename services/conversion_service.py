import threading
from typing import Optional, List, Dict, Any
from models import ConversionTracker
from config import logger


class ConversionService:
    """High-level service for managing conversions with thread safety"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        """Singleton pattern for conversion service"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return

        self._tracker = ConversionTracker()
        self._tracker.cleanup_old_records(days=2)
        self._tracker.reset_stuck_conversions()
        self._initialized = True
        logger.info("ConversionService initialized")

    def start_conversion(self, filename: str, conversion_type: str = "m4a") -> bool:
        """
        Start tracking a conversion

        Args:
            filename: Name of the file being converted
            conversion_type: Type of conversion ("m4a" or "mp3_chapters")

        Returns:
            bool: True if conversion started, False if already in progress
        """
        if self.is_conversion_active(filename, conversion_type):
            logger.warning(
                f"Conversion already active for {filename} ({conversion_type})"
            )
            return False

        self._tracker.start_conversion(filename, conversion_type)
        logger.info(f"Started tracking {conversion_type} conversion for {filename}")
        return True

    def update_progress(
        self,
        filename: str,
        progress: float,
        status: str = "converting",
        conversion_type: str = "m4a",
    ):
        """Update conversion progress"""
        self._tracker.update_progress(filename, progress, status, conversion_type)

    def complete_conversion(
        self,
        filename: str,
        success: bool = True,
        error_message: Optional[str] = None,
        result_path: Optional[str] = None,
        conversion_type: str = "m4a",
    ):
        """Mark conversion as completed or failed"""
        self._tracker.complete_conversion(
            filename, success, error_message, result_path, conversion_type
        )
        logger.info(
            f"Completed {conversion_type} conversion for {filename}: {'success' if success else 'failed'}"
        )

    def get_progress(
        self, filename: str, conversion_type: str = "m4a"
    ) -> Dict[str, Any]:
        """Get current progress for a file"""
        return self._tracker.get_progress(filename, conversion_type)

    def is_conversion_active(self, filename: str, conversion_type: str = "m4a") -> bool:
        """Check if conversion is currently active"""
        return self._tracker.is_conversion_active(filename, conversion_type)

    def get_all_active_conversions(self) -> List[Dict[str, Any]]:
        """Get all currently active conversions"""
        return self._tracker.get_all_active_conversions()

    def cleanup_active_conversions(self):
        """Mark all active conversions as interrupted"""
        try:
            active_conversions = self.get_all_active_conversions()

            for conv in active_conversions:
                logger.info(f"Marking conversion as interrupted: {conv['filename']}")
                self.complete_conversion(
                    conv["filename"],
                    success=False,
                    error_message="Server interrupted",
                    conversion_type=conv.get("conversion_type", "unknown"),
                )
        except Exception as e:
            logger.error(f"Error during conversion cleanup: {e}")

    def cleanup_old_records(self, days: int = 7):
        """Clean up old conversion records"""
        self._tracker.cleanup_old_records(days)

    def reset_stuck_conversions(self):
        """Reset conversions that were interrupted during server shutdown"""
        self._tracker.reset_stuck_conversions()


# Global instance
conversion_service = ConversionService()
