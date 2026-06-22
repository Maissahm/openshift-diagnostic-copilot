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
    time_window_minutes: Number(timeWindow),
    question: question
  };

  setStatus("Analyzing...", "loading");
  diagnoseBtn.disabled = true;

  try {
    /*
      For now, we use mock data because the backend is not connected yet.
      Later, i will replace the mockDiagnosis(requestData) line with:

      const apiResponse = await fetch("http://localhost:8000/diagnose", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(requestData)
      });

      const response = await apiResponse.json();
    */

    const response = await mockDiagnosis(requestData);

    displayDiagnosis(response);
    setStatus("Completed", "completed");

  } catch (error) {
    console.error(error);
    setStatus("Error", "error");
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
  document.getElementById("summary").textContent = data.summary || "-";
  document.getElementById("rootCause").textContent = data.probable_root_cause || "-";
  document.getElementById("confidence").textContent = data.confidence || "-";
  document.getElementById("impact").textContent = data.impact || "-";

  const evidenceList = document.getElementById("evidenceList");
  evidenceList.innerHTML = "";

  if (Array.isArray(data.evidence)) {
    data.evidence.forEach(item => {
      const li = document.createElement("li");
      li.textContent = item;
      evidenceList.appendChild(li);
    });
  }

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

function mockDiagnosis(requestData) {
  console.log("Mock request sent:", requestData);

  return new Promise(resolve => {
    setTimeout(() => {
      resolve({
        summary: "The application is unavailable because it cannot connect to the database.",
        probable_root_cause: "Database service unavailable",
        confidence: "High",
        impact: "Backend requests requiring database access are failing.",
        evidence: [
          "Route is healthy",
          "Service has endpoints",
          "Pods are running and ready",
          "Database connection failures started at 14:33 UTC",
          "DatabaseUnavailable alert is firing"
        ],
        recommended_actions: [
          "Check database pod status",
          "Check database service endpoints",
          "Check database logs",
          "Verify DB_HOST and DB_PORT"
        ]
      });
    }, 900);
  });
}
