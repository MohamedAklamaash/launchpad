from fastapi import FastAPI, Request
from app.api.router import api_router
from app.core.config import settings

app = FastAPI(
    title="Gateway Service",
    description="API Gateway for Launchpad Services",
    version="1.0.0",
    debug=settings.DEBUG
)

@app.get("/")
async def root():
    return {"message": "Gateway Service is running"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/liveness")
async def liveness():
    return {"status": "The service is live"}

@app.get("/readiness")
async def readiness():
    return {"status": "The service is ready"}

from fastapi.responses import JSONResponse
import logging

app.include_router(api_router)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logging.error(f"Global exception caught: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"message": "Internal Server Error", "details": str(exc)},
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.ALLOWED_HOSTS, port=settings.PORT)
