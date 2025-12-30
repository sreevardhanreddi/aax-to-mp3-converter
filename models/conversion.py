import threading
from datetime import datetime
from typing import List, Optional

from sqlmodel import Field, Session, SQLModel, UniqueConstraint, create_engine, select

from config import logger


class Conversion(SQLModel, table=True):
    """SQLModel table for tracking conversion progress"""

    __tablename__ = "conversions"
    __table_args__ = (
        UniqueConstraint("filename", "conversion_type", name="unique_filename_type"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    filename: str = Field(index=True)
    conversion_type: str = Field(default="m4b", index=True)  # "m4b" or "mp3_chapters"
    status: str = Field(index=True)
    progress: float = Field(default=0.0)
    error_message: Optional[str] = Field(default=None)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = Field(default=None)
    result_path: Optional[str] = Field(default=None)  # Path to converted file or zip


class ConversionTracker:
    def __init__(self, db_path="sqlite:///sqlite.db"):
        self.engine = create_engine(db_path)
        self.lock = threading.Lock()
        self.init_database()

    def init_database(self):
        """Initialize the database with required tables"""
        # Check if the table already exists with the correct schema
        try:
            with Session(self.engine) as session:
                # Try to query the table to see if it exists
                session.exec(select(Conversion).limit(1))
                logger.info("Database table already exists, skipping creation")
        except Exception:
            # Table doesn't exist or has wrong schema, create it
            SQLModel.metadata.create_all(self.engine)
            logger.info("Database initialized successfully")

    def start_conversion(self, filename: str, conversion_type: str = "m4b"):
        """Mark conversion as started"""
        with self.lock:
            with Session(self.engine) as session:
                # Check if conversion already exists for this type
                conversion_id = f"{filename}_{conversion_type}"
                existing = session.exec(
                    select(Conversion).where(
                        Conversion.filename == filename,
                        Conversion.conversion_type == conversion_type,
                    )
                ).first()

                if existing:
                    # Update existing record
                    existing.status = "starting"
                    existing.progress = 0.0
                    existing.started_at = datetime.utcnow()
                    existing.updated_at = datetime.utcnow()
                    existing.error_message = None
                    existing.completed_at = None
                    existing.result_path = None
                else:
                    # Create new record
                    conversion = Conversion(
                        filename=filename,
                        conversion_type=conversion_type,
                        status="starting",
                        progress=0.0,
                    )
                    session.add(conversion)

                session.commit()
                logger.info(
                    f"Started tracking {conversion_type} conversion for {filename}"
                )

    def update_progress(
        self,
        filename: str,
        progress: float,
        status: str = "converting",
        conversion_type: str = "m4b",
    ):
        """Update conversion progress"""
        with self.lock:
            with Session(self.engine) as session:
                conversion = session.exec(
                    select(Conversion).where(
                        Conversion.filename == filename,
                        Conversion.conversion_type == conversion_type,
                    )
                ).first()

                if conversion:
                    conversion.progress = progress
                    conversion.status = status
                    conversion.updated_at = datetime.utcnow()
                    session.commit()

    def complete_conversion(
        self,
        filename: str,
        success: bool = True,
        error_message: Optional[str] = None,
        result_path: Optional[str] = None,
        conversion_type: str = "m4b",
    ):
        """Mark conversion as completed or failed"""
        with self.lock:
            with Session(self.engine) as session:
                conversion = session.exec(
                    select(Conversion).where(
                        Conversion.filename == filename,
                        Conversion.conversion_type == conversion_type,
                    )
                ).first()

                if conversion:
                    if success:
                        conversion.status = "completed"
                        conversion.progress = 100.0
                        conversion.completed_at = datetime.utcnow()
                        conversion.result_path = result_path
                    else:
                        conversion.status = "error"
                        conversion.error_message = error_message

                    conversion.updated_at = datetime.utcnow()
                    session.commit()
                    logger.info(
                        f"{conversion_type} conversion completed for {filename}: {'success' if success else 'failed'}"
                    )

    def get_progress(self, filename: str, conversion_type: str = "m4b") -> dict:
        """Get current progress for a file"""
        with Session(self.engine) as session:
            conversion = session.exec(
                select(Conversion).where(
                    Conversion.filename == filename,
                    Conversion.conversion_type == conversion_type,
                )
            ).first()

            if conversion:
                result = {
                    "status": conversion.status,
                    "progress": conversion.progress,
                    "conversion_type": conversion.conversion_type,
                    "started_at": conversion.started_at.isoformat(),
                    "updated_at": conversion.updated_at.isoformat(),
                }
                if conversion.error_message:
                    result["error"] = conversion.error_message
                if conversion.completed_at:
                    result["completed_at"] = conversion.completed_at.isoformat()
                if conversion.result_path:
                    result["result_path"] = conversion.result_path
                return result
            else:
                return {
                    "status": "not_started",
                    "progress": 0,
                    "conversion_type": conversion_type,
                }

    def is_conversion_active(self, filename: str, conversion_type: str = "m4b") -> bool:
        """Check if conversion is currently active"""
        progress_data = self.get_progress(filename, conversion_type)
        return progress_data["status"] in ["starting", "converting"]

    def cleanup_old_records(self, days: int = 7):
        """Clean up old conversion records"""
        from datetime import timedelta

        cutoff_date = datetime.utcnow() - timedelta(days=days)

        with self.lock:
            with Session(self.engine) as session:
                old_conversions = session.exec(
                    select(Conversion).where(Conversion.updated_at < cutoff_date)
                ).all()

                deleted_count = len(old_conversions)
                for conversion in old_conversions:
                    session.delete(conversion)

                session.commit()
                if deleted_count > 0:
                    logger.info(f"Cleaned up {deleted_count} old conversion records")

    def get_all_active_conversions(self) -> List[dict]:
        """Get all currently active conversions"""
        with Session(self.engine) as session:
            active_conversions = session.exec(
                select(Conversion)
                .where(Conversion.status.in_(["starting", "converting"]))
                .order_by(Conversion.started_at.desc())
            ).all()

            return [
                {
                    "filename": conversion.filename,
                    "status": conversion.status,
                    "progress": conversion.progress,
                    "started_at": conversion.started_at.isoformat(),
                }
                for conversion in active_conversions
            ]

    def reset_stuck_conversions(self):
        """Reset conversions that were interrupted during server shutdown"""
        from datetime import timedelta

        # Find conversions that have been 'starting' or 'converting' for too long
        stale_threshold = datetime.utcnow() - timedelta(minutes=5)

        with self.lock:
            with Session(self.engine) as session:
                stuck_conversions = session.exec(
                    select(Conversion).where(
                        Conversion.status.in_(["starting", "converting"]),
                        Conversion.updated_at < stale_threshold,
                    )
                ).all()

                reset_count = 0
                for conversion in stuck_conversions:
                    conversion.status = "error"
                    conversion.error_message = (
                        "Conversion interrupted - server restarted"
                    )
                    conversion.updated_at = datetime.utcnow()
                    reset_count += 1

                session.commit()
                if reset_count > 0:
                    logger.info(f"Reset {reset_count} stuck conversions")
