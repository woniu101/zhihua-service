import uvicorn

from zhihua_service.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "zhihua_service.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.environment == "development",
    )

