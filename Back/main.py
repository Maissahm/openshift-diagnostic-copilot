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


def labels_match_selector(pod_labels: dict, selector: dict) -> bool:
    if not pod_labels or not selector:
        return False

    for key, value in selector.items():
        if pod_labels.get(key) != value:
            return False

    return True


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


def find_application_services(v1_api, namespace: str, application: str, pods):
    all_services = v1_api.list_namespaced_service(namespace=namespace).items
    pod_labels_list = [pod.metadata.labels or {} for pod in pods]

    matching_services = []

    for service in all_services:
        service_name = service.metadata.name
        selector = service.spec.selector or {}

        service_name_matches = application.lower() in service_name.lower()
        selector_matches_app = selector.get("app") == application
        selector_matches_pods = any(
            labels_match_selector(pod_labels, selector)
            for pod_labels in pod_labels_list
        )

        if service_name_matches or selector_matches_app or selector_matches_pods:
            matching_services.append(service)

    return matching_services


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
                    "Check if the application name is correct.",
                    "Check if the pods have the label app=<application>.",
                    "If the deployment exists but replicas are set to 0, scale the deployment up."
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

                        if message:
                            evidence.append(message)

                    if reason in ["ImagePullBackOff", "ErrImagePull"]:
                        image_pull_issue_detected = True

                        if message:
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
                    "Correct the container image name or tag if it is wrong.",
                    "Push the missing image to the configured registry if it does not exist.",
                    "Configure the correct imagePullSecret if the registry is private.",
                    "Restart the deployment after fixing the image configuration.",
                    f"Validate the fix with: oc get pods -n {request.namespace}"
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
                    "Fix the startup error shown in the logs.",
                    "Check missing environment variables, ConfigMaps, Secrets or external dependencies.",
                    "Redeploy the corrected application version or rollback to a stable image.",
                    f"Validate the fix with: oc get pods -n {request.namespace}"
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
                    "wrong port configuration or an application-level problem."
                ),
                recommended_actions=[
                    f"Run: oc describe pod <pod-name> -n {request.namespace}",
                    "Fix the readiness probe path, port or initial delay if it is misconfigured.",
                    "Verify that the application exposes the expected health endpoint.",
                    "Check application logs to identify internal errors.",
                    f"Validate the fix with: oc get pods -n {request.namespace}"
                ],
                evidence=evidence
            )

        if high_restart_detected:
            return DiagnoseResponse(
                status="Application unstable",
                probable_cause="High container restart count",
                confidence="Medium",
                explanation=(
                    "The application pod is running, but one or more containers "
                    "have restarted several times. This may indicate instability, "
                    "memory issues, probe problems or application errors."
                ),
                recommended_actions=[
                    f"Run: oc logs <pod-name> -n {request.namespace}",
                    f"Run: oc describe pod <pod-name> -n {request.namespace}",
                    "Fix the cause of repeated restarts found in the logs.",
                    "Increase memory or CPU limits if the container is being killed because of resource pressure.",
                    "Adjust liveness probe settings if the probe is restarting the container too early.",
                    f"Validate the fix with: oc get pods -n {request.namespace}"
                ],
                evidence=evidence
            )

        services = find_application_services(
            v1_api=v1,
            namespace=request.namespace,
            application=request.application,
            pods=pods
        )

        if not services:
            return DiagnoseResponse(
                status="Application unavailable",
                probable_cause="No service found for the application",
                confidence="High",
                explanation=(
                    f"The application pods were found for '{request.application}' "
                    f"in namespace '{request.namespace}', but no Kubernetes/OpenShift "
                    "service was found. Without a service, traffic cannot be forwarded "
                    "correctly to the application pods."
                ),
                recommended_actions=[
                    "Create a service for the application deployment.",
                    "Expose the deployment using the correct application port.",
                    "Make sure the service selector matches the pod labels.",
                    f"Run: oc get svc -n {request.namespace}",
                    f"Validate the fix with: oc get endpoints -n {request.namespace}"
                ],
                evidence=evidence + [
                    f"No service found for application {request.application}."
                ]
            )

        evidence.append(
            "Services found: " + ", ".join(service.metadata.name for service in services)
        )

        return DiagnoseResponse(
            status="No critical pod or service issue detected",
            probable_cause="Pods appear to be running and a service was found",
            confidence="Medium",
            explanation=(
                f"Real OpenShift data was collected for application "
                f"'{request.application}' in namespace '{request.namespace}'. "
                "The pods appear to be running and ready, and at least one service "
                "was found for the application. The next diagnostic step is to verify "
                "service selectors, endpoints and routes."
            ),
            recommended_actions=[
                "Next diagnostic step: verify that the service selector matches pod labels.",
                "Next diagnostic step: verify that the service has endpoints.",
                "Next diagnostic step: verify that an OpenShift route exists."
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
                f"Run: oc auth can-i get services -n {request.namespace}",
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