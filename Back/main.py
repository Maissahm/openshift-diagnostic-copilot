from fastapi import FastAPI

app = FastAPI(
    title="OpenShift Diagnostic Copilot API",
    description="Backend API for OpenShift/Kubernetes diagnostic copilot",
    version="1.0.0"
)


@app.get("/")
def root():
    return {
        "message": "OpenShift Diagnostic Copilot API is running"
    }


@app.get("/health")
def health_check():
    return {
        "status": "healthy"
    }