from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


app = FastAPI(
    title="OpenShift Diagnostic Copilot API",
    description="Backend API for OpenShift/Kubernetes diagnostic copilot",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class DiagnoseRequest(BaseModel):
    application: str
    namespace: str
    time_window: str
    question: str


class DiagnoseResponse(BaseModel):
    status: str
    probable_cause: str
    confidence: str
    explanation: str
    recommended_actions: list[str]


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


@app.post("/diagnose", response_model=DiagnoseResponse)
def diagnose(request: DiagnoseRequest):
    return DiagnoseResponse(
        status="Application unavailable",
        probable_cause="Pod crash or service routing issue",
        confidence="Medium",
        explanation=(
            f"The application '{request.application}' in namespace "
            f"'{request.namespace}' appears to be unavailable during "
            f"the selected time window: {request.time_window}. "
            "This first version uses mock diagnostic data. "
            "Later, this endpoint will be connected to OpenShift, "
            "Prometheus and Alertmanager."
        ),
        recommended_actions=[
            "Check pod status in the selected namespace",
            "Inspect recent pod logs",
            "Verify service and route configuration",
            "Check recent alerts from Alertmanager",
            "Validate resource limits and restart count"
        ]
    )