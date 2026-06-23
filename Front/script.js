const diagnoseBtn = document.getElementById("diagnoseBtn");

diagnoseBtn.addEventListener("click", async () => {
  const application = document.getElementById("application").value.trim();
  const namespace = document.getElementById("namespace").value.trim();
  const timeWindow = document.getElementById("timeWindow").value;
  const question = document.getElementById("question").value.trim();

  if (!application || !namespace || !question) {
    setStatus("Please fill all required fields", "error");
    return;
  }

  const requestData = {
    application: application,
    namespace: namespace,
    time_window: `${timeWindow} minutes`,
    question: question
  };

  setStatus("Analyzing...", "loading");
  diagnoseBtn.disabled = true;

  try {
    const response = await fetch("http://127.0.0.1:8000/diagnose", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(requestData)
    });

    if (!response.ok) {
      throw new Error("Backend request failed");
    }

    const data = await response.json();

    displayDiagnosis(data);
    setStatus("Completed", "completed");

  } catch (error) {
    console.error(error);
    setStatus("Error: backend not reachable", "error");
  } finally {
    diagnoseBtn.disabled = false;
  }
});

function setStatus(text, type) {
  const statusBadge = document.getElementById("statusBadge");
  statusBadge.textContent = text;
  statusBadge.className = "badge";

  if (type === "loading") statusBadge.classList.add("badge-loading");
  else if (type === "completed") statusBadge.classList.add("badge-completed");
  else if (type === "error") statusBadge.classList.add("badge-error");
  else statusBadge.classList.add("badge-idle");
}

function displayDiagnosis(data) {
  document.getElementById("summary").textContent = data.explanation || "-";
  document.getElementById("rootCause").textContent = data.probable_cause || "-";
  document.getElementById("confidence").textContent = data.confidence || "-";
  document.getElementById("impact").textContent = data.status || "-";

  const evidenceList = document.getElementById("evidenceList");
  evidenceList.innerHTML = "";

  const evidence = [
    "The backend received the diagnostic request successfully",
    "The diagnosis is currently based on mock data",
    "OpenShift, Prometheus and Alertmanager integration will be added later"
  ];

  evidence.forEach(item => {
    const li = document.createElement("li");
    li.textContent = item;
    evidenceList.appendChild(li);
  });

  const actionsList = document.getElementById("actionsList");
  actionsList.innerHTML = "";

  if (Array.isArray(data.recommended_actions)) {
    data.recommended_actions.forEach(item => {
      const li = document.createElement("li");
      li.textContent = item;
      actionsList.appendChild(li);
    });
  }
}