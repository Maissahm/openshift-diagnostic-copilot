const diagnoseBtn = document.getElementById("diagnoseBtn");

const API_URL = "http://ai-copilot-backend-ai-copilot.apps.sno.fedora.test";

const autoFixButton = document.getElementById("autoFixButton");
const resolutionFlag = document.getElementById("resolutionFlag");
const autoFixReason = document.getElementById("autoFixReason");
const autoFixResult = document.getElementById("autoFixResult");

let currentAutoFix = null;
let currentApplication = null;
let currentNamespace = null;

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

  currentApplication = application;
  currentNamespace = namespace;
  currentAutoFix = null;

  resetAutoFixSection();

  setStatus("Analyzing...", "loading");
  diagnoseBtn.disabled = true;

  try {
    const response = await fetch(`${API_URL}/diagnose`, {
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
    renderAutoFix(data.auto_fix);

    setStatus("Completed", "completed");

  } catch (error) {
    console.error(error);
    setStatus("Error: backend not reachable", "error");

    autoFixReason.textContent = "Automatic correction cannot be evaluated because the backend is not reachable.";
    autoFixButton.disabled = true;
    autoFixButton.textContent = "Automatic Fix Unavailable";
    setResolutionFlag("manual_required", "Manual intervention required");

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

  if (Array.isArray(data.evidence) && data.evidence.length > 0) {
    data.evidence.forEach(item => {
      const li = document.createElement("li");
      li.textContent = item;
      evidenceList.appendChild(li);
    });
  } else {
    const li = document.createElement("li");
    li.textContent = "No evidence returned by the backend.";
    evidenceList.appendChild(li);
  }

  const actionsList = document.getElementById("actionsList");
  actionsList.innerHTML = "";

  if (Array.isArray(data.recommended_actions) && data.recommended_actions.length > 0) {
    data.recommended_actions.forEach(item => {
      const li = document.createElement("li");
      li.textContent = item;
      actionsList.appendChild(li);
    });
  } else {
    const li = document.createElement("li");
    li.textContent = "No recommended actions returned by the backend.";
    actionsList.appendChild(li);
  }
}

function resetAutoFixSection() {
  if (!autoFixButton || !resolutionFlag || !autoFixReason || !autoFixResult) {
    return;
  }

  autoFixButton.disabled = true;
  autoFixButton.textContent = "Automatic Fix Unavailable";
  autoFixReason.textContent = "Analyzing whether automatic correction is available...";
  autoFixResult.textContent = "";
  autoFixResult.className = "auto-fix-result";

  setResolutionFlag("manual_required", "Manual intervention required");
}

function renderAutoFix(autoFix) {
  if (!autoFixButton || !resolutionFlag || !autoFixReason || !autoFixResult) {
    return;
  }

  if (!autoFix) {
    currentAutoFix = null;

    autoFixButton.disabled = true;
    autoFixButton.textContent = "Automatic Fix Unavailable";
    autoFixReason.textContent = "Automatic correction information was not returned by the backend.";
    autoFixResult.textContent = "";

    setResolutionFlag("manual_required", "Manual intervention required");
    return;
  }

  currentAutoFix = autoFix;

  autoFixButton.textContent = autoFix.button_label || "Automatic Fix";
  autoFixReason.textContent = autoFix.reason || "No automatic correction reason returned.";
  autoFixResult.textContent = "";
  autoFixResult.className = "auto-fix-result";

  setResolutionFlag(
    autoFix.resolution_status || "manual_required",
    autoFix.flag_label || "Manual intervention required"
  );

  if (autoFix.available === true) {
    autoFixButton.disabled = false;
  } else {
    autoFixButton.disabled = true;
  }
}

function setResolutionFlag(status, label) {
  if (!resolutionFlag) {
    return;
  }

  resolutionFlag.className = "resolution-flag";

  if (status === "unresolved") {
    resolutionFlag.classList.add("unresolved");
    resolutionFlag.textContent = label || "Unresolved";
  } else if (status === "fixing") {
    resolutionFlag.classList.add("fixing");
    resolutionFlag.textContent = label || "Fixing in progress";
  } else if (status === "resolved") {
    resolutionFlag.classList.add("resolved");
    resolutionFlag.textContent = label || "Resolved";
  } else {
    resolutionFlag.classList.add("manual");
    resolutionFlag.textContent = label || "Manual intervention required";
  }
}

if (autoFixButton) {
  autoFixButton.addEventListener("click", async () => {
    if (!currentAutoFix || currentAutoFix.available !== true) {
      return;
    }

    if (!currentApplication || !currentNamespace) {
      autoFixResult.textContent = "Application or namespace is missing. Run a diagnosis again.";
      autoFixResult.className = "auto-fix-result error";
      return;
    }

    autoFixButton.disabled = true;
    autoFixButton.textContent = "Fixing...";
    autoFixResult.textContent = "The Copilot is applying a safe automatic correction...";
    autoFixResult.className = "auto-fix-result";

    setResolutionFlag("fixing", "Fixing in progress");

    const requestData = {
      application: currentApplication,
      namespace: currentNamespace,
      action: currentAutoFix.action,
      target_kind: currentAutoFix.target_kind,
      target_name: currentAutoFix.target_name
    };

    try {
      const response = await fetch(`${API_URL}/auto-fix`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(requestData)
      });

      const result = await response.json();

      if (!response.ok) {
        throw new Error(result.message || "Automatic fix request failed");
      }

      displayAutoFixResult(result);

      setResolutionFlag(
        result.resolution_status || "unresolved",
        result.flag_label || "Unresolved"
      );

      if (result.resolution_status === "resolved") {
        autoFixButton.textContent = "Fixed";
        autoFixButton.disabled = true;
        autoFixResult.classList.add("success");
      } else {
        autoFixButton.textContent = "Retry Automatic Fix";
        autoFixButton.disabled = false;
        autoFixResult.classList.add("error");
      }

    } catch (error) {
      console.error(error);

      setResolutionFlag("unresolved", "Unresolved");

      autoFixButton.textContent = "Retry Automatic Fix";
      autoFixButton.disabled = false;

      autoFixResult.textContent = "Automatic fix failed: " + error.message;
      autoFixResult.className = "auto-fix-result error";
    }
  });
}

function displayAutoFixResult(result) {
  autoFixResult.innerHTML = "";

  const message = document.createElement("p");
  message.innerHTML = `<strong>${result.message || "Automatic fix finished."}</strong>`;
  autoFixResult.appendChild(message);

  const details = document.createElement("p");
  details.innerHTML = `
    <strong>Executed action:</strong> ${result.executed_action || "-"}<br>
    <strong>Target:</strong> ${result.target_kind || "-"}/${result.target_name || "-"}
  `;
  autoFixResult.appendChild(details);

  const evidenceTitle = document.createElement("p");
  evidenceTitle.innerHTML = "<strong>Verification evidence:</strong>";
  autoFixResult.appendChild(evidenceTitle);

  const evidenceUl = document.createElement("ul");

  if (Array.isArray(result.evidence) && result.evidence.length > 0) {
    result.evidence.forEach(item => {
      const li = document.createElement("li");
      li.textContent = item;
      evidenceUl.appendChild(li);
    });
  } else {
    const li = document.createElement("li");
    li.textContent = "No verification evidence returned.";
    evidenceUl.appendChild(li);
  }

  autoFixResult.appendChild(evidenceUl);
}