"""
Smart PDF Backend — Entry Point

Run with:
    python main.py                    # Development
    uvicorn app:app --host 0.0.0.0    # Production (single worker)
    gunicorn app:app -w 1 -k uvicorn.workers.UvicornWorker  # Production (gunicorn)
"""
import uvicorn
from config import settings

if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
        access_log=settings.DEBUG,
    )