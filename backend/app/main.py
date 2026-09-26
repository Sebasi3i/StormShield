from fastapi import FastAPI

app = FastAPI(
    title="Weather Risk API",
    description="Severe weather risk intelligence API for property insurance",
    version="0.1.0",
)


@app.get("/")
def root():
    return {"message": "Weather Risk API is running"}


@app.get("/health")
def health():
    return {"status": "healthy"}