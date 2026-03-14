from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from app.api.router import api_router
from app.core.config import settings
from fastapi.responses import JSONResponse
import logging
from constants import EXEMPT_PATHS

app = FastAPI(
    title="Gateway Service",
    description="API Gateway for Launchpad Services",
    version="1.0.0",
    debug=settings.DEBUG
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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


from app.core.rate_limiter import RateLimiter

rate_limiter = RateLimiter()

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.url.path not in EXEMPT_PATHS:
        try:
            await rate_limiter.check_rate_limit(request)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"message": exc.detail},
            )
    response = await call_next(request)
    return response

app.include_router(api_router, prefix="/api")

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"message": exc.detail},
        )
    logging.error(f"Global exception caught: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"message": "Internal Server Error", "details": str(exc)},
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.ALLOWED_HOSTS, port=settings.PORT)
