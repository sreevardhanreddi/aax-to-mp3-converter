import threading
import signal
import sys
import atexit
from typing import List, Callable, Optional
from config import logger


class ThreadManager:
    """Manages application threads with graceful shutdown support"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        """Singleton pattern for thread manager"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return

        self._active_threads: List[threading.Thread] = []
        self._shutdown_event = threading.Event()
        self._thread_lock = threading.Lock()
        self._cleanup_callbacks: List[Callable] = []
        self._initialized = True

        # Register signal handlers
        self._register_signal_handlers()
        atexit.register(self._cleanup_on_exit)

    def _register_signal_handlers(self):
        """Register signal handlers for graceful shutdown"""
        try:
            signal.signal(signal.SIGTERM, self._signal_handler)
            signal.signal(signal.SIGINT, self._signal_handler)
            logger.info("Signal handlers registered for graceful shutdown")
        except Exception as e:
            logger.warning(f"Could not register signal handlers: {e}")

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        self.shutdown()
        sys.exit(0)

    def _cleanup_on_exit(self):
        """Cleanup function called on process exit"""
        self.shutdown()

    def add_cleanup_callback(self, callback: Callable):
        """Add a cleanup callback to be called during shutdown"""
        with self._thread_lock:
            self._cleanup_callbacks.append(callback)

    def create_thread(
        self, target: Callable, args: tuple = (), kwargs: dict = None, name: str = None
    ) -> threading.Thread:
        """Create and track a new thread"""
        if kwargs is None:
            kwargs = {}

        thread = threading.Thread(
            target=self._thread_wrapper, args=(target, args, kwargs), name=name
        )
        thread.daemon = True

        with self._thread_lock:
            self._active_threads.append(thread)

        return thread

    def _thread_wrapper(self, target: Callable, args: tuple, kwargs: dict):
        """Wrapper for thread execution with cleanup"""
        try:
            target(*args, **kwargs)
        except Exception as e:
            logger.error(f"Thread {threading.current_thread().name} failed: {e}")
        finally:
            # Remove thread from active list when it completes
            with self._thread_lock:
                try:
                    self._active_threads.remove(threading.current_thread())
                except ValueError:
                    pass  # Thread already removed

    def start_thread(
        self, target: Callable, args: tuple = (), kwargs: dict = None, name: str = None
    ) -> threading.Thread:
        """Create and start a new thread"""
        thread = self.create_thread(target, args, kwargs, name)
        thread.start()
        logger.info(f"Started thread: {thread.name}")
        return thread

    def is_shutdown_requested(self) -> bool:
        """Check if shutdown has been requested"""
        return self._shutdown_event.is_set()

    def shutdown(self, timeout: float = 5.0):
        """Initiate graceful shutdown of all threads"""
        logger.info("Initiating graceful shutdown...")

        # Set shutdown event
        self._shutdown_event.set()

        # Call cleanup callbacks
        for callback in self._cleanup_callbacks:
            try:
                callback()
            except Exception as e:
                logger.error(f"Error in cleanup callback: {e}")

        # Wait for threads to finish
        with self._thread_lock:
            active_threads = list(self._active_threads)

        for thread in active_threads:
            if thread.is_alive():
                logger.info(f"Waiting for thread {thread.name} to finish...")
                thread.join(timeout=timeout)
                if thread.is_alive():
                    logger.warning(
                        f"Thread {thread.name} did not finish within {timeout}s"
                    )

        logger.info("Shutdown complete")

    def get_active_thread_count(self) -> int:
        """Get the number of active threads"""
        with self._thread_lock:
            return len([t for t in self._active_threads if t.is_alive()])

    def get_active_thread_names(self) -> List[str]:
        """Get names of all active threads"""
        with self._thread_lock:
            return [t.name for t in self._active_threads if t.is_alive()]


# Global instance
thread_manager = ThreadManager()
