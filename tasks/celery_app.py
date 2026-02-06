import os

from celery import Celery


def _build_celery_app() -> Celery:
    app = Celery(
        "aax_converter",
        broker=os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0"),
        backend=os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/1"),
    )
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        broker_connection_retry_on_startup=True,
        imports=("tasks.conversion_tasks",),
    )
    return app


celery_app = _build_celery_app()
