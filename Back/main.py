from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from kubernetes import client, config
from kubernetes.client.rest import ApiException


app = FastAPI(
    title="OpenShift Diagnostic Copilot API",
    description="FastAPI backend for OpenShift application diagnostics",
    version="1.0.0"
)

app.add_middleware(
CORSMiddleware,
allow_origins=[
"http://ai-copilot-frontend-ai-copilot.apps.sno.fedora.test",
"http://localhost:8080",
"http://127.0.0.1:8080",
],
allow_credentials=False,
allow_methods=["GET", "POST", "OPTIONS"],
allow_headers=["Content-Type"],
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
    evidence: list[str]


def load_kubernetes_config():
    try:
        config.load_incluster_config()
        return "in-cluster"
    except config.ConfigException:
        config.load_kube_config()
        return "local-kubeconfig"



@app.get("/")
def root():
    return {
        "message": "OpenShift Diagnostic Copilot API is running"
    }


@app.get("/health")
def health():
    return {
        "status": "healthy"
    }


@app.post("/diagnose", response_model=DiagnoseResponse)
def diagnose(request: DiagnoseRequest):
    evidence = []
    recommended_actions = []


      
