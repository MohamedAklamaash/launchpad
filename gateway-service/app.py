from fastapi import FastAPI
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

app.include_router(api_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.ALLOWED_HOSTS, port=settings.PORT)
