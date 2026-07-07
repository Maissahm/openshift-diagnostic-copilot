from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from datetime import datetime, timezone
from typing import Optional, Literal
import time
import os
import re
import requests


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


class AutoFixInfo(BaseModel):
    available: bool
    action: Optional[str] = None
    target_kind: Optional[str] = None
    target_name: Optional[str] = None
    button_label: str
    resolution_status: Literal[
        "unresolved",
        "resolved",
        "fixing",
        "manual_required"
    ]
    flag_label: str
    reason: str


def manual_auto_fix(
    reason: str = "This issue requires manual DevOps intervention."
) -> AutoFixInfo:
    return AutoFixInfo(
        available=False,
        action=None,
        target_kind=None,
        target_name=None,
        button_label="Automatic Fix Unavailable",
        resolution_status="manual_required",
        flag_label="Manual intervention required",
        reason=reason
    )


def available_auto_fix(
    action: str,
    target_kind: str,
    target_name: str,
    reason: str
) -> AutoFixInfo:
    return AutoFixInfo(
        available=True,
        action=action,
        target_kind=target_kind,
        target_name=target_name,
        button_label="Automatic Fix",
        resolution_status="unresolved",
        flag_label="Unresolved",
        reason=reason
    )


class DiagnoseResponse(BaseModel):
    status: str
    probable_cause: str
    confidence: str
    explanation: str
    recommended_actions: list[str]
    evidence: list[str]
    auto_fix: AutoFixInfo = Field(default_factory=manual_auto_fix)


class AutoFixRequest(BaseModel):
    application: str
    namespace: str
    action: str
    target_kind: str
    target_name: str


class AutoFixResponse(BaseModel):
    status: str
    resolution_status: Literal["resolved", "unresolved"]
    flag_label: str
    message: str
    executed_action: str
    target_kind: str
    target_name: str
    evidence: list[str]


PROMETHEUS_URL = os.getenv(
    "PROMETHEUS_URL",
    "https://thanos-querier-openshift-monitoring.apps.sno.fedora.test"
)

SERVICE_ACCOUNT_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"


def validate_k8s_name(value: str, field_name: str) -> str:
    pattern = r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$"

    if not value or not re.match(pattern, value):
        raise ValueError(
            f"Invalid {field_name}: '{value}'. "
            "Only lowercase letters, numbers and hyphens are allowed."
        )

    return value


def load_kubernetes_config():
    try:
        config.load_incluster_config()
        return "in-cluster"
    except Exception:
        config.load_kube_config()
        return "local-kubeconfig"


def labels_match_selector(pod_labels: dict, selector: dict) -> bool:
    if not pod_labels or not selector:
        return False

    for key, value in selector.items():
        if pod_labels.get(key) != value:
            return False

    return True


def labels_match_match_expressions(pod_labels: dict, match_expressions: list) -> bool:
    if not match_expressions:
        return True

    for expression in match_expressions:
        key = expression.key
        operator = expression.operator
        values = expression.values or []

        if operator == "In":
            if pod_labels.get(key) not in values:
                return False

        elif operator == "NotIn":
            if pod_labels.get(key) in values:
                return False

        elif operator == "Exists":
            if key not in pod_labels:
                return False

        elif operator == "DoesNotExist":
            if key in pod_labels:
                return False

    return True


def deployment_matches_pods(deployment, pods) -> bool:
    selector = deployment.spec.selector

    match_labels = selector.match_labels or {}
    match_expressions = selector.match_expressions or []

    for pod in pods:
        pod_labels = pod.metadata.labels or {}

        if labels_match_selector(
            pod_labels,
            match_labels
        ) and labels_match_match_expressions(
            pod_labels,
            match_expressions
        ):
            return True

    return False


def find_application_deployments(apps_api, namespace: str, application: str, pods=None):
    deployments = apps_api.list_namespaced_deployment(namespace=namespace).items
    pods = pods or []

    matching_deployments = []

    for deployment in deployments:
        deployment_name = deployment.metadata.name
        deployment_labels = deployment.metadata.labels or {}

        name_matches = application.lower() in deployment_name.lower()
        label_matches = deployment_labels.get("app") == application
        selector_matches_pods = deployment_matches_pods(deployment, pods) if pods else False

        if name_matches or label_matches or selector_matches_pods:
            matching_deployments.append(deployment)

    return matching_deployments


def get_first_matching_deployment_name(
    apps_api,
    namespace: str,
    application: str,
    pods=None
) -> Optional[str]:
    deployments = find_application_deployments(
        apps_api=apps_api,
        namespace=namespace,
        application=application,
        pods=pods or []
    )

    if not deployments:
        return None

    return deployments[0].metadata.name


def verify_deployment_recovered(
    apps_api,
    v1_api,
    namespace: str,
    deployment_name: str,
    timeout_seconds: int = 90
):
    evidence = []
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        deployment = apps_api.read_namespaced_deployment(
            name=deployment_name,
            namespace=namespace
        )

        desired_replicas = deployment.spec.replicas or 0
        available_replicas = deployment.status.available_replicas or 0
        ready_replicas = deployment.status.ready_replicas or 0

        evidence.append(
            f"Deployment {deployment_name}: desired={desired_replicas}, "
            f"ready={ready_replicas}, available={available_replicas}"
        )

        if desired_replicas > 0 and available_replicas >= 1 and ready_replicas >= 1:
            return True, evidence

        time.sleep(5)

    return False, evidence


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


def service_selector_matches_pods(service, pods) -> bool:
    selector = service.spec.selector or {}

    if not selector:
        return False

    for pod in pods:
        pod_labels = pod.metadata.labels or {}

        if labels_match_selector(pod_labels, selector):
            return True

    return False


def get_service_endpoint_count(v1_api, namespace: str, service_name: str) -> int:
    try:
        endpoints = v1_api.read_namespaced_endpoints(
            name=service_name,
            namespace=namespace
        )
    except ApiException as error:
        if error.status == 404:
            return 0
        raise

    endpoint_count = 0

    for subset in endpoints.subsets or []:
        endpoint_count += len(subset.addresses or [])

    return endpoint_count


def find_application_routes(custom_api, namespace: str, application: str, services):
    service_names = [service.metadata.name for service in services]

    routes_response = custom_api.list_namespaced_custom_object(
        group="route.openshift.io",
        version="v1",
        namespace=namespace,
        plural="routes"
    )

    routes = routes_response.get("items", [])
    matching_routes = []

    for route in routes:
        route_name = route.get("metadata", {}).get("name", "")
        target_service = route.get("spec", {}).get("to", {}).get("name", "")

        route_name_matches = application.lower() in route_name.lower()
        route_targets_application_service = target_service in service_names

        if route_name_matches or route_targets_application_service:
            matching_routes.append(route)

    return matching_routes


def get_service_account_token() -> str:
    with open(SERVICE_ACCOUNT_TOKEN_PATH, "r", encoding="utf-8") as token_file:
        return token_file.read().strip()


def query_prometheus(query: str) -> dict:
    token = get_service_account_token()

    response = requests.get(
        f"{PROMETHEUS_URL}/api/v1/query",
        headers={"Authorization": f"Bearer {token}"},
        params={"query": query},
        verify=False,
        timeout=10
    )

    response.raise_for_status()
    return response.json()


def convert_time_window_to_prometheus(time_window: str) -> str:
    match = re.search(r"\d+", time_window)

    if not match:
        return "30m"

    minutes = int(match.group())

    if minutes <= 0:
        return "30m"

    return f"{minutes}m"


def format_metric_value(value: str) -> str:
    try:
        numeric_value = float(value)

        if numeric_value.is_integer():
            return str(int(numeric_value))

        return f"{numeric_value:.2f}"
    except Exception:
        return value


def collect_prometheus_evidence(namespace: str, pods: list, time_window: str) -> list[str]:
    prometheus_evidence = []

    if not pods:
        return prometheus_evidence

    pod_names = [
        pod.metadata.name
        for pod in pods
        if pod.metadata and pod.metadata.name
    ]

    if not pod_names:
        return prometheus_evidence

    pod_regex = "|".join(pod_names)
    prometheus_window = convert_time_window_to_prometheus(time_window)

    try:
        restart_query = (
            f'sum by (pod) (kube_pod_container_status_restarts_total'
            f'{{namespace="{namespace}",pod=~"{pod_regex}"}})'
        )

        restart_data = query_prometheus(restart_query)
        restart_results = restart_data.get("data", {}).get("result", [])

        if restart_results:
            for result in restart_results:
                pod_name = result.get("metric", {}).get("pod", "unknown-pod")
                value = result.get("value", [None, "0"])[1]
                value = format_metric_value(value)

                prometheus_evidence.append(
                    f"Prometheus: total restart count for pod {pod_name} is {value}."
                )
        else:
            prometheus_evidence.append(
                "Prometheus: no restart metric found for the application pods."
            )

        restart_increase_query = (
            f'sum by (pod) (increase(kube_pod_container_status_restarts_total'
            f'{{namespace="{namespace}",pod=~"{pod_regex}"}}[{prometheus_window}]))'
        )

        restart_increase_data = query_prometheus(restart_increase_query)
        increase_results = restart_increase_data.get("data", {}).get("result", [])

        if increase_results:
            for result in increase_results:
                pod_name = result.get("metric", {}).get("pod", "unknown-pod")
                value = result.get("value", [None, "0"])[1]
                value = format_metric_value(value)

                prometheus_evidence.append(
                    f"Prometheus: restarts increased by {value} for pod {pod_name} "
                    f"during the last {prometheus_window}."
                )
        else:
            prometheus_evidence.append(
                f"Prometheus: no restart increase detected during the last {prometheus_window}."
            )

        readiness_query = (
            f'kube_pod_status_ready'
            f'{{namespace="{namespace}",pod=~"{pod_regex}",condition="true"}}'
        )

        readiness_data = query_prometheus(readiness_query)
        readiness_results = readiness_data.get("data", {}).get("result", [])

        if readiness_results:
            for result in readiness_results:
                pod_name = result.get("metric", {}).get("pod", "unknown-pod")
                value = result.get("value", [None, "0"])[1]

                readiness_status = "ready" if value == "1" else "not ready"

                prometheus_evidence.append(
                    f"Prometheus: pod {pod_name} readiness metric indicates {readiness_status}."
                )
        else:
            prometheus_evidence.append(
                "Prometheus: no readiness metric found for the application pods."
            )

        cpu_query = (
            f'sum by (pod) (rate(container_cpu_usage_seconds_total'
            f'{{namespace="{namespace}",pod=~"{pod_regex}",container!="",image!=""}}[5m]))'
        )

        cpu_data = query_prometheus(cpu_query)
        cpu_results = cpu_data.get("data", {}).get("result", [])

        if cpu_results:
            for result in cpu_results:
                pod_name = result.get("metric", {}).get("pod", "unknown-pod")
                value = result.get("value", [None, "0"])[1]

                try:
                    cpu_millicores = float(value) * 1000
                    cpu_text = f"{cpu_millicores:.2f} millicores"
                except Exception:
                    cpu_text = value

                prometheus_evidence.append(
                    f"Prometheus: CPU usage for pod {pod_name} is {cpu_text}."
                )
        else:
            prometheus_evidence.append(
                "Prometheus: no CPU usage metric found for the application pods."
            )

        memory_query = (
            f'sum by (pod) (container_memory_working_set_bytes'
            f'{{namespace="{namespace}",pod=~"{pod_regex}",container!="",image!=""}}) / 1024 / 1024'
        )

        memory_data = query_prometheus(memory_query)
        memory_results = memory_data.get("data", {}).get("result", [])

        if memory_results:
            for result in memory_results:
                pod_name = result.get("metric", {}).get("pod", "unknown-pod")
                value = result.get("value", [None, "0"])[1]

                try:
                    memory_mib = float(value)
                    memory_text = f"{memory_mib:.2f} MiB"
                except Exception:
                    memory_text = value

                prometheus_evidence.append(
                    f"Prometheus: memory usage for pod {pod_name} is {memory_text}."
                )
        else:
            prometheus_evidence.append(
                "Prometheus: no memory usage metric found for the application pods."
            )

    except Exception as error:
        prometheus_evidence.append(
            f"Prometheus: metrics could not be collected. Error: {str(error)}"
        )

    return prometheus_evidence


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
        request.application = validate_k8s_name(request.application, "application")
        request.namespace = validate_k8s_name(request.namespace, "namespace")

        config_mode = load_kubernetes_config()
        evidence.append(f"Kubernetes configuration loaded using: {config_mode}")

        v1 = client.CoreV1Api()
        apps_v1 = client.AppsV1Api()
        custom_api = client.CustomObjectsApi()

        pods = find_application_pods(
            v1_api=v1,
            namespace=request.namespace,
            application=request.application
        )

        if not pods:
            deployments = find_application_deployments(
                apps_api=apps_v1,
                namespace=request.namespace,
                application=request.application,
                pods=[]
            )

            scaled_to_zero_deployment = None

            for deployment in deployments:
                replicas = deployment.spec.replicas or 0

                evidence.append(
                    f"Deployment found: {deployment.metadata.name}, replicas={replicas}"
                )

                if replicas == 0:
                    scaled_to_zero_deployment = deployment
                    break

            if scaled_to_zero_deployment:
                deployment_name = scaled_to_zero_deployment.metadata.name

                return DiagnoseResponse(
                    status="Application unavailable",
                    probable_cause="Deployment is scaled to zero",
                    confidence="High",
                    explanation=(
                        f"No pods were found for application '{request.application}', "
                        f"but a matching deployment '{deployment_name}' exists with replicas set to 0. "
                        "This means the application is deployed but currently scaled down."
                    ),
                    recommended_actions=[
                        "Scale the deployment back to one replica.",
                        f"Run: oc scale deployment/{deployment_name} --replicas=1 -n {request.namespace}",
                        f"Validate the fix with: oc get pods -n {request.namespace}"
                    ],
                    evidence=evidence + [
                        f"No pods found for application {request.application}.",
                        f"Deployment {deployment_name} has replicas=0."
                    ],
                    auto_fix=available_auto_fix(
                        action="scale_deployment_to_one",
                        target_kind="deployment",
                        target_name=deployment_name,
                        reason=(
                            "The application has a matching deployment with zero replicas. "
                            "This is a simple issue that the Copilot can correct automatically."
                        )
                    )
                )

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
                evidence=evidence + [
                    f"No pods found with label app={request.application} or matching pod name."
                ],
                auto_fix=manual_auto_fix(
                    "No matching deployment scaled to zero was found. Automatic correction is not safe."
                )
            )

        crash_loop_detected = False
        image_pull_issue_detected = False
        pod_not_running_detected = False
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
                pod_not_running_detected = True
                evidence.append(
                    f"Pod {pod_name} is not running. Current phase: {pod_phase}."
                )

            if pod_phase == "Running" and ready_status != "True":
                pod_not_ready_detected = True
                evidence.append(
                    f"Pod {pod_name} is running but not ready."
                )

            for container_status in pod.status.container_statuses or []:
                container_name = container_status.name
                restart_count = container_status.restart_count

                evidence.append(
                    f"Container {container_name} restart count: {restart_count}"
                )

                if restart_count >= 3 and ready_status != "True":
                    crash_loop_detected = True
                    evidence.append(
                        f"Container {container_name} restarted {restart_count} times "
                        "and the pod is not ready. This suggests repeated crashes."
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

        prometheus_evidence = collect_prometheus_evidence(
            namespace=request.namespace,
            pods=pods,
            time_window=request.time_window
        )

        evidence.extend(prometheus_evidence)

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
                    "is crashing repeatedly. This can appear as CrashLoopBackOff, or as "
                    "a high restart count while the pod is still not ready."
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

        if pod_not_running_detected:
            return DiagnoseResponse(
                status="Application unavailable",
                probable_cause="Pod is not running",
                confidence="High",
                explanation=(
                    "At least one application pod exists, but its phase is not Running. "
                    "This means the pod is blocked, failed, pending, or cannot start correctly. "
                    "The problem may be related to scheduling, resources, volumes, image configuration, "
                    "or node availability."
                ),
                recommended_actions=[
                    f"Run: oc describe pod <pod-name> -n {request.namespace}",
                    f"Run: oc get events -n {request.namespace}",
                    "Identify the blocking reason from the pod events.",
                    "If the pod is Pending, check scheduling, node availability and resource requests.",
                    "If the pod has a volume issue, fix the PVC or volume configuration.",
                    "If the pod has an image issue, correct the image name, tag or registry access.",
                    f"Validate the fix with: oc get pods -n {request.namespace}"
                ],
                evidence=evidence
            )

        if pod_not_ready_detected:
            deployment_name = get_first_matching_deployment_name(
                apps_api=apps_v1,
                namespace=request.namespace,
                application=request.application,
                pods=pods
            )

            if deployment_name:
                auto_fix = available_auto_fix(
                    action="restart_deployment",
                    target_kind="deployment",
                    target_name=deployment_name,
                    reason=(
                        "The pod is running but not ready, and a matching deployment was found. "
                        "The Copilot can try a safe deployment restart without changing configuration."
                    )
                )
            else:
                auto_fix = manual_auto_fix(
                    "The pod is not ready, but no matching deployment was found. Automatic correction is not safe."
                )

            return DiagnoseResponse(
                status="Application degraded",
                probable_cause="Pod is running but not ready",
                confidence="Medium",
                explanation=(
                    "The application pod is Running, but it is not Ready. "
                    "This means the container has started, but OpenShift does not consider "
                    "the application ready to receive traffic. This may indicate a readiness "
                    "probe issue, startup delay, wrong port configuration, wrong health endpoint, "
                    "or an application-level problem."
                ),
                recommended_actions=[
                    f"Run: oc describe pod <pod-name> -n {request.namespace}",
                    "Fix the readiness probe path, port or initial delay if it is misconfigured.",
                    "Verify that the application exposes the expected health endpoint.",
                    "Verify that the application listens on the same port configured in the service and readiness probe.",
                    "Check application logs to identify internal errors.",
                    f"Validate the fix with: oc get pods -n {request.namespace}"
                ],
                evidence=evidence,
                auto_fix=auto_fix
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

        selector_mismatch_detected = False

        for service in services:
            selector = service.spec.selector or {}

            evidence.append(
                f"Service {service.metadata.name} selector: {selector}"
            )

            if not service_selector_matches_pods(service, pods):
                selector_mismatch_detected = True
                evidence.append(
                    f"Service {service.metadata.name} selector does not match the application pod labels."
                )

        if selector_mismatch_detected:
            return DiagnoseResponse(
                status="Application unavailable",
                probable_cause="Service selector does not match pod labels",
                confidence="High",
                explanation=(
                    "A service was found for the application, but its selector does not match "
                    "the labels of the application pods. In this situation, the service exists, "
                    "but it cannot correctly forward traffic to the application pods."
                ),
                recommended_actions=[
                    "Update the service selector so it matches the labels of the application pods.",
                    "Or update the pod labels so they match the service selector.",
                    f"Run: oc get pods --show-labels -n {request.namespace}",
                    f"Run: oc describe svc <service-name> -n {request.namespace}",
                    f"Validate the fix with: oc get endpoints -n {request.namespace}"
                ],
                evidence=evidence
            )

        service_without_endpoints = []

        for service in services:
            endpoint_count = get_service_endpoint_count(
                v1_api=v1,
                namespace=request.namespace,
                service_name=service.metadata.name
            )

            evidence.append(
                f"Service {service.metadata.name} endpoint count: {endpoint_count}"
            )

            if endpoint_count == 0:
                service_without_endpoints.append(service.metadata.name)

        if service_without_endpoints:
            return DiagnoseResponse(
                status="Application unavailable",
                probable_cause="Service has no endpoints",
                confidence="High",
                explanation=(
                    "The service exists and its selector matches the application pods, "
                    "but the service has no endpoints. This means the service is not "
                    "connected to any Ready pod, so traffic cannot reach the application."
                ),
                recommended_actions=[
                    "Fix pod readiness if the pods are not Ready.",
                    "Verify that the service targetPort matches the container port.",
                    "Verify that the application container is listening on the expected port.",
                    f"Run: oc get endpoints -n {request.namespace}",
                    f"Run: oc describe svc <service-name> -n {request.namespace}",
                    "Validate the fix by checking that the service endpoints are no longer empty."
                ],
                evidence=evidence
            )

        routes = find_application_routes(
            custom_api=custom_api,
            namespace=request.namespace,
            application=request.application,
            services=services
        )

        if not routes:
            return DiagnoseResponse(
                status="Application unavailable",
                probable_cause="No route found for the application",
                confidence="Medium",
                explanation=(
                    "The application pods, service and endpoints appear to be available, "
                    "but no OpenShift route was found. If this application must be accessible "
                    "from outside the cluster, a route is required."
                ),
                recommended_actions=[
                    "Create an OpenShift route for the correct service if external access is required.",
                    "Make sure the route targets the service connected to the application pods.",
                    f"Run: oc get route -n {request.namespace}",
                    "Validate the fix by opening the generated route URL in a browser."
                ],
                evidence=evidence + [
                    f"No route found for application {request.application}."
                ]
            )

        evidence.append(
            "Routes found: " + ", ".join(
                route.get("metadata", {}).get("name", "")
                for route in routes
            )
        )

        service_names = [service.metadata.name for service in services]
        wrong_route_targets = []

        for route in routes:
            route_name = route.get("metadata", {}).get("name", "")
            target_service = route.get("spec", {}).get("to", {}).get("name", "")

            evidence.append(
                f"Route {route_name} target service: {target_service}"
            )

            if target_service not in service_names:
                wrong_route_targets.append(
                    f"Route {route_name} points to service {target_service}, "
                    f"but expected one of: {service_names}"
                )

        if wrong_route_targets:
            return DiagnoseResponse(
                status="Application unavailable",
                probable_cause="Route points to the wrong service",
                confidence="High",
                explanation=(
                    "An OpenShift route was found for the application, but it does not point "
                    "to the service associated with the application pods. This means the route "
                    "may send external traffic to the wrong backend service."
                ),
                recommended_actions=[
                    "Update the route so it targets the correct application service.",
                    "Or create a new route for the correct service.",
                    f"Run: oc get route -n {request.namespace}",
                    f"Run: oc describe route <route-name> -n {request.namespace}",
                    "Validate the fix by opening the route URL again."
                ],
                evidence=evidence + wrong_route_targets
            )

        return DiagnoseResponse(
            status="No critical OpenShift issue detected",
            probable_cause="Pods, service, endpoints and route appear to be correctly configured",
            confidence="Medium",
            explanation=(
                f"Real OpenShift data was collected for application "
                f"'{request.application}' in namespace '{request.namespace}'. "
                "The pods are running and ready, a service was found, the service has endpoints, "
                "and the OpenShift route points to the expected service."
            ),
            recommended_actions=[
                "If the application is still unavailable, check application-level logs.",
                "Check Prometheus metrics for CPU, memory or availability issues.",
                "Check Alertmanager for active alerts.",
                "Check Loki logs for recent application errors."
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
                f"Run: oc auth can-i get endpoints -n {request.namespace}",
                f"Run: oc auth can-i get routes.route.openshift.io -n {request.namespace}",
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
                "Verify that the backend is running inside OpenShift.",
                "Verify that Prometheus/Thanos URL and permissions are correctly configured."
            ],
            evidence=[str(error)]
        )


@app.post("/auto-fix", response_model=AutoFixResponse)
def auto_fix(request: AutoFixRequest):
    evidence = []

    allowed_actions = [
        "restart_deployment",
        "scale_deployment_to_one"
    ]

    try:
        request.application = validate_k8s_name(request.application, "application")
        request.namespace = validate_k8s_name(request.namespace, "namespace")
        request.target_name = validate_k8s_name(request.target_name, "target_name")
    except Exception as error:
        return AutoFixResponse(
            status="Rejected",
            resolution_status="unresolved",
            flag_label="Unresolved",
            message=str(error),
            executed_action=request.action,
            target_kind=request.target_kind,
            target_name=request.target_name,
            evidence=[str(error)]
        )

    if request.action not in allowed_actions:
        return AutoFixResponse(
            status="Rejected",
            resolution_status="unresolved",
            flag_label="Unresolved",
            message="This action is not allowed for automatic correction.",
            executed_action=request.action,
            target_kind=request.target_kind,
            target_name=request.target_name,
            evidence=[f"Rejected action: {request.action}"]
        )

    if request.target_kind != "deployment":
        return AutoFixResponse(
            status="Rejected",
            resolution_status="unresolved",
            flag_label="Unresolved",
            message="Automatic correction is only allowed for deployments.",
            executed_action=request.action,
            target_kind=request.target_kind,
            target_name=request.target_name,
            evidence=[f"Rejected target kind: {request.target_kind}"]
        )

    try:
        config_mode = load_kubernetes_config()
        evidence.append(f"Kubernetes configuration loaded using: {config_mode}")

        v1 = client.CoreV1Api()
        apps_v1 = client.AppsV1Api()

        pods = find_application_pods(
            v1_api=v1,
            namespace=request.namespace,
            application=request.application
        )

        matching_deployments = find_application_deployments(
            apps_api=apps_v1,
            namespace=request.namespace,
            application=request.application,
            pods=pods
        )

        matching_deployment_names = [
            deployment.metadata.name
            for deployment in matching_deployments
        ]

        if request.target_name not in matching_deployment_names:
            return AutoFixResponse(
                status="Rejected",
                resolution_status="unresolved",
                flag_label="Unresolved",
                message=(
                    "The requested deployment does not match the diagnosed application. "
                    "Automatic correction was rejected for safety."
                ),
                executed_action=request.action,
                target_kind=request.target_kind,
                target_name=request.target_name,
                evidence=evidence + [
                    f"Requested target: {request.target_name}",
                    f"Matching deployments for application {request.application}: {matching_deployment_names}"
                ]
            )

        deployment = apps_v1.read_namespaced_deployment(
            name=request.target_name,
            namespace=request.namespace
        )

        evidence.append(
            f"Deployment found before correction: {deployment.metadata.name}"
        )

        if request.action == "scale_deployment_to_one":
            current_replicas = deployment.spec.replicas or 0

            evidence.append(
                f"Current replica count for deployment {request.target_name}: {current_replicas}"
            )

            if current_replicas != 0:
                return AutoFixResponse(
                    status="Skipped",
                    resolution_status="unresolved",
                    flag_label="Unresolved",
                    message=(
                        "The deployment is not scaled to zero anymore. "
                        "Automatic scale correction was skipped."
                    ),
                    executed_action=request.action,
                    target_kind=request.target_kind,
                    target_name=request.target_name,
                    evidence=evidence
                )

            apps_v1.patch_namespaced_deployment_scale(
                name=request.target_name,
                namespace=request.namespace,
                body={
                    "spec": {
                        "replicas": 1
                    }
                }
            )

            evidence.append(
                f"Executed correction: scaled deployment/{request.target_name} to 1 replica."
            )

        elif request.action == "restart_deployment":
            desired_replicas = deployment.spec.replicas or 0

            evidence.append(
                f"Current desired replicas for deployment {request.target_name}: {desired_replicas}"
            )

            if desired_replicas == 0:
                return AutoFixResponse(
                    status="Rejected",
                    resolution_status="unresolved",
                    flag_label="Unresolved",
                    message=(
                        "The deployment has zero replicas. Restart is not useful. "
                        "Use scale correction instead."
                    ),
                    executed_action=request.action,
                    target_kind=request.target_kind,
                    target_name=request.target_name,
                    evidence=evidence
                )

            restarted_at = datetime.now(timezone.utc).isoformat()

            patch_body = {
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "ai-copilot/restarted-at": restarted_at
                            }
                        }
                    }
                }
            }

            apps_v1.patch_namespaced_deployment(
                name=request.target_name,
                namespace=request.namespace,
                body=patch_body
            )

            evidence.append(
                f"Executed correction: restarted deployment/{request.target_name} "
                f"using annotation ai-copilot/restarted-at={restarted_at}."
            )

        recovered, verification_evidence = verify_deployment_recovered(
            apps_api=apps_v1,
            v1_api=v1,
            namespace=request.namespace,
            deployment_name=request.target_name,
            timeout_seconds=90
        )

        evidence.extend(verification_evidence)

        if recovered:
            return AutoFixResponse(
                status="Auto-fix completed",
                resolution_status="resolved",
                flag_label="Resolved",
                message="Automatic correction completed successfully. The deployment is now healthy.",
                executed_action=request.action,
                target_kind=request.target_kind,
                target_name=request.target_name,
                evidence=evidence
            )

        return AutoFixResponse(
            status="Auto-fix executed but not resolved",
            resolution_status="unresolved",
            flag_label="Unresolved",
            message=(
                "The automatic correction was executed, but the deployment did not become healthy "
                "within the verification timeout."
            ),
            executed_action=request.action,
            target_kind=request.target_kind,
            target_name=request.target_name,
            evidence=evidence
        )

    except ApiException as api_error:
        return AutoFixResponse(
            status="Auto-fix failed",
            resolution_status="unresolved",
            flag_label="Unresolved",
            message="OpenShift API error during automatic correction.",
            executed_action=request.action,
            target_kind=request.target_kind,
            target_name=request.target_name,
            evidence=[str(api_error)]
        )

    except Exception as error:
        return AutoFixResponse(
            status="Auto-fix failed",
            resolution_status="unresolved",
            flag_label="Unresolved",
            message="Unexpected backend error during automatic correction.",
            executed_action=request.action,
            target_kind=request.target_kind,
            target_name=request.target_name,
            evidence=[str(error)]
        )