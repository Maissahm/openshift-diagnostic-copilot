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
    recommended_actions = []

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
                    "Check if the application name is correct.",
                    "Check if the pods have the label app=<application>."
                ],
                evidence=[
                    f"No pods found with label app={request.application} or matching pod name."
                ]
            )

        crash_loop_detected = False
        image_pull_issue_detected = False
        pod_not_ready_detected = False
        high_restart_detected = False

        for pod in pods:
            pod_name = pod.metadata.name
            pod_phase = pod.status.phase

            evidence.append(f"Pod found: {pod_name}")
            evidence.append(f"Pod phase: {pod_phase}")

            ready_status = "Unknown"

            for condition in pod.status.conditions or []:
                if condition.type == "Ready":
                    ready_status = condition.status

            evidence.append(f"Pod ready status: {ready_status}")

            if pod_phase != "Running":
                pod_not_ready_detected = True
                evidence.append(f"Pod {pod_name} is not running.")

            if ready_status != "True":
                pod_not_ready_detected = True
                evidence.append(f"Pod {pod_name} is not ready.")

            for container_status in pod.status.container_statuses or []:
                container_name = container_status.name
                restart_count = container_status.restart_count

                evidence.append(
                    f"Container {container_name} restart count: {restart_count}"
                )

                if restart_count >= 3:
                    high_restart_detected = True
                    evidence.append(
                        f"Container {container_name} has a high restart count."
                    )

                state = container_status.state

                if state and state.waiting:
                    reason = state.waiting.reason
                    message = state.waiting.message or ""

                    evidence.append(
                        f"Container {container_name} waiting reason: {reason}"
                    )

                    if reason == "CrashLoopBackOff":
                        crash_loop_detected = True
                        evidence.append(message)

                    if reason in ["ImagePullBackOff", "ErrImagePull"]:
                        image_pull_issue_detected = True
                        evidence.append(message)

        if image_pull_issue_detected:
            return DiagnoseResponse(
                status="Application unavailable",
                probable_cause="Container image pull failure",
                confidence="High",
                explanation=(
                    "OpenShift found pods for the application, but at least one container "
                    "has an image pull problem. This usually means that the image name, "
                    "tag, registry access, or image pull permissions are incorrect."
                ),
                recommended_actions=[
                    "Check the image name and tag.",
                    "Check if the image exists in Docker Hub or the configured registry.",
                    "Check imagePullSecrets if the registry is private.",
                    f"Run: oc describe pod <pod-name> -n {request.namespace}"
                ],
                evidence=evidence
            )

        if crash_loop_detected:
            return DiagnoseResponse(
                status="Application unavailable",
                probable_cause="Container is crashing repeatedly",
                confidence="High",
                explanation=(
                    "OpenShift found pods for the application, but at least one container "
                    "is in CrashLoopBackOff. This means the container starts and then crashes repeatedly."
                ),
                recommended_actions=[
                    f"Run: oc logs <pod-name> -n {request.namespace}",
                    f"Run: oc describe pod <pod-name> -n {request.namespace}",
                    "Check application startup errors.",
                    "Check environment variables and configuration."
                ],
                evidence=evidence
            )

        if pod_not_ready_detected:
            return DiagnoseResponse(
                status="Application degraded",
                probable_cause="Pod is not ready",
                confidence="Medium",
                explanation=(
                    "The application pod exists, but it is not fully ready. "
                    "This may indicate a readiness probe issue, startup delay, "
                    "or application-level problem."
                ),
                recommended_actions=[
                    f"Run: oc get pods -n {request.namespace}",
                    f"Run: oc describe pod <pod-name> -n {request.namespace}",
                    "Check readiness probe configuration.",
                    "Check application logs."
                ],
                evidence=evidence
            )

        if high_restart_detected:
            return DiagnoseResponse(
                status="Application unstable",
                probable_cause="High container restart count",
                confidence="Medium",
                explanation=(
                    "The application pod is running, but one or more containers have restarted several times. "
                    "This may indicate instability, memory issues, or application errors."
                ),
                recommended_actions=[
                    f"Run: oc logs <pod-name> -n {request.namespace}",
                    f"Run: oc describe pod <pod-name> -n {request.namespace}",
                    "Check CPU and memory limits.",
                    "Check recent application errors."
                ],
                evidence=evidence
            )

        return DiagnoseResponse(
            status="No critical pod issue detected",
            probable_cause="Pods appear to be running and ready",
            confidence="Medium",
            explanation=(
                f"Real OpenShift pod data was collected for application "
                f"'{request.application}' in namespace '{request.namespace}'. "
                "The pods appear to be running and ready."
            ),
            recommended_actions=[
                "Check services and routes if the application is still unreachable.",
                "Check application logs if the issue is not visible at pod level.",
                "Next diagnostic step: verify services, endpoints and routes."
            ],
            evidence=evidence
        )

    except ApiException as api_error:
        return DiagnoseResponse(
            status="Diagnostic failed",
            probable_cause="OpenShift API access error",
            confidence="High",
            explanation=(
                "The backend could not access the OpenShift API. "
                "This is usually caused by missing service account permissions."
            ),
            recommended_actions=[
                "Check backend service account permissions.",
                f"Run: oc auth can-i get pods -n {request.namespace}",
                "Give the backend service account view permissions on the namespace.",
                "Check backend pod logs."
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
                "Verify that the kubernetes package is installed.",
                "Verify that the backend is running inside OpenShift."
            ],
            evidence=[str(error)]
        )