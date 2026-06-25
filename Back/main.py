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

def find_application_pods(v1_api, namespace: str, application: str):
   
    pods = v1_api.list_namespaced_pod(
        namespace=namespace,
        label_selector=f"app={application}"
    ).items

    if pods:
        return pods

    all_pods = v1_api.list_namespaced_pod(namespace=namespace).items

    matching_pods = [
        pod for pod in all_pods
        if application.lower() in pod.metadata.name.lower()
    ]

    return matching_pods


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

    try:
        config_mode = load_kubernetes_config()
        evidence.append(f"Kubernetes configuration loaded using: {config_mode}")

        v1 = client.CoreV1Api()

        pods = find_application_pods(
            v1_api=v1,
            namespace=request.namespace,
            application=request.application
        )

        if not pods:
            return DiagnoseResponse(
                status="Application unavailable",
                probable_cause="No pods found for the application",
                confidence="High",
                explanation=(
                    f"No pods were found for application '{request.application}' "
                    f"in namespace '{request.namespace}'. This may mean that the "
                    "application is not deployed, the application name is incorrect, "
                    "or the pod labels do not match the application name."
                ),
                recommended_actions=[
                    f"Run: oc get pods -n {request.namespace}",
                    f"Run: oc get deployment -n {request.namespace}",
                    "Verify that the application is deployed in the selected namespace.",
                    "Check if the pods have the label app=<application>.",
                    "If the deployment exists but replicas are set to 0, scale the deployment up."
                ],
                evidence=[
                    f"No pods found with label app={request.application} or matching pod name."
                ]
            )

        return DiagnoseResponse(
            status="Application found",
            probable_cause="Pods were found for the application",
            confidence="Medium",
            explanation=(
                f"Pods were found for application '{request.application}' "
                f"in namespace '{request.namespace}'. Further diagnostic rules "
                "will be added in the next commits."
            ),
            recommended_actions=[
                "Continue with pod status, readiness and container state diagnostics."
            ],
            evidence=evidence
        )

    except ApiException as api_error:
        return DiagnoseResponse(
            status="Diagnostic failed",
            probable_cause="OpenShift API access error",
            confidence="High",
            explanation="The backend could not access the OpenShift API.",
            recommended_actions=[
                "Check backend service account permissions.",
                f"Run: oc auth can-i get pods -n {request.namespace}",
                "Give the backend service account view permissions on the namespace."
            ],
            evidence=[str(api_error)]
        )

    except Exception as error:
        return DiagnoseResponse(
            status="Diagnostic failed",
            probable_cause="Unexpected backend error",
            confidence="Low",
            explanation="An unexpected error occurred during the diagnostic process.",
            recommended_actions=[
                "Check backend logs.",
                "Verify that the Kubernetes package is installed.",
                "Verify that the backend is running correctly."
            ],
            evidence=[str(error)]
        )