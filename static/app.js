const $ = (id) => document.getElementById(id);
const stages = ["analysis", "planning", "policy", "execution", "containment", "comparison"];
const STAGE_LABELS = {
  analysis: "Reviewing your Python code",
  planning: "Preparing a bounded test plan",
  policy: "Checking the execution policy",
  execution: "Running code in the restricted sandbox",
  containment: "Checking sandbox configuration",
  comparison: "Comparing predictions with available evidence",
};
const pageSize = 20;
let selectedFile = null;
let currentReport = null;
let page = 0;
let lastRuns = [];
let demoMode = false;
let demoUploadEnabled = false;

const STATUS_LABELS = {
  predicted_not_observed: "Predicted by AI — not observed during this run",
  sample_markers_ignored: "Submitted-program markers were not trusted",
  docker_engine_metadata: "Confirmed by the host",
  verified: "Verified",
  passed: "Verified",
  failed: "Failed",
  unknown: "Unknown",
  "not-started": "Waiting to run",
  not_run: "Not run",
  skipped: "Skipped",
  policy_denied: "Policy denied",
  denied: "Denied",
  pending: "Pending",
  completed: "Completed",
};

function displayStatus(value) {
  return STATUS_LABELS[value] || value || "Not available";
}

function normalizeReport(report) {
  const sandbox = report?.sandbox_execution || {};
  const containment = report?.containment_report || null;
  const evidence = report?.evidence_summary || sandbox.evidence || [];
  const comparison = report?.comparison || {};
  const executionScope = report?.execution_scope || {
    executed_sandbox_runs: report?.execution_performed ? 1 : 0,
  };
  return {
    ...report,
    evidence_summary: Array.isArray(evidence) ? evidence : [],
    execution_scope: executionScope,
    containment_report: containment,
    comparison: {
      ...comparison,
      comparisons: Array.isArray(comparison.comparisons)
        ? comparison.comparisons
        : [],
      limitations: comparison.limitations || report?.limitations || [],
    },
    limitations: report?.limitations ||
      comparison.limitations ||
      report?.ai_predictions?.limitations ||
      [],
  };
}

function showPage(pageName) {
  const active = pageName === "runs" ? "runs" : pageName === "containment" ? "containment" : pageName === "reports" ? "reports" : "analyze";
  document.querySelectorAll("[data-nav]").forEach((item) => {
    const selected = item.dataset.nav === active;
    item.classList.toggle("active", selected);
    if (selected) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  });
  const analyzeVisible = active === "analyze";
  const historyVisible = active === "runs";
  const reportVisible = active === "containment" || active === "reports";
  $("analyzeView").hidden = !analyzeVisible;
  $("historyView").hidden = !historyVisible;
  $("report").hidden = !reportVisible;
  $("analyzeView").setAttribute("aria-hidden", String(!analyzeVisible));
  $("historyView").setAttribute("aria-hidden", String(!historyVisible));
  $("report").setAttribute("aria-hidden", String(!reportVisible));
  if (active === "containment") activateTab("containment");
  if (active === "reports") activateTab("overview");
  if (active === "runs") $("historyView").scrollIntoView({ behavior: "smooth", block: "start" });
}

function clearReport() {
  currentReport = null;
  $("downloadButton").disabled = true;
  $("report").hidden = true;
  $("report").setAttribute("aria-hidden", "true");
}

function displayValue(value, fallback = "Not available") {
  if (value === null || value === undefined || value === "") return fallback;
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function statusBadge(value) {
  const badge = document.createElement("span");
  badge.className = `badge ${String(value || "unknown").toLowerCase().replace(/[^a-z0-9-]/g, "-")}`;
  badge.textContent = displayStatus(value);
  return badge;
}

function setStatus(message, isError = false) {
  $("statusLine").textContent = message;
  $("statusLine").className = isError ? "status error" : "status";
}

function updateAnalyzeButton() {
  $("analyzeButton").disabled =
    !selectedFile ||
    (demoMode
      ? !demoUploadEnabled || !$("accessCode").value.trim()
      : !$("apiKey").value.trim());
}

function renderSource(source) {
  const lines = source.split(/\r?\n/);
  const width = String(lines.length).length;
  $("sourcePreview").textContent = lines
    .map((line, index) => `${String(index + 1).padStart(width, " ")}  ${line}`)
    .join("\n");
}

function chooseFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".py")) {
    setStatus("Choose a Python .py file.", true);
    return;
  }
  if (file.size > 100 * 1024) {
    setStatus("File exceeds the 100 KB limit.", true);
    return;
  }
  selectedFile = file;
  $("fileMeta").textContent = `${file.name} · ${file.size.toLocaleString()} bytes`;
  file.text().then(renderSource).catch(() => {
    $("sourcePreview").textContent = "Source preview unavailable.";
  });
  setStatus("");
  updateAnalyzeButton();
}

function renderTrace(target, states = {}) {
  target.replaceChildren();
  stages.forEach((name) => {
    const details = states[name] || {};
    const state = details.status || "not-started";
    const row = document.createElement("div");
    row.className = `trace-row ${state}`;
    const dot = document.createElement("i");
    dot.className = "trace-dot";
    const label = document.createElement("span");
    label.className = "name";
    label.textContent = STAGE_LABELS[name] || name;
    const status = document.createElement("span");
    status.className = "state";
    status.textContent = displayStatus(state);
    row.append(dot, label, status);
    if (details.error) {
      const error = document.createElement("p");
      error.className = "trace-error";
      error.textContent = details.error;
      row.append(error);
    }
    target.append(row);
  });
}

function renderContainment(report) {
  const grid = $("controlGrid");
  grid.replaceChildren();
  if (!report) {
    $("containmentStatus").replaceChildren(statusBadge("not_run"));
    $("containmentSummary").textContent = "No sandbox configuration report was produced for this run.";
    $("containmentMetric").textContent = "Not run";
    $("containmentLimitations").textContent =
      "Containment is not claimed when policy prevents execution or verification did not run.";
    return;
  }

  $("containmentStatus").replaceChildren(statusBadge(report.overall_status));
  $("containmentSummary").textContent =
    `Verified ${displayValue(report.verified_count, "Unknown")} · ` +
    `Failed ${displayValue(report.failed_count, "Unknown")} · ` +
    `Unknown ${displayValue(report.unknown_count, "Unknown")}`;
  $("containmentMetric").textContent =
    report.overall_status === "passed"
      ? "Verified"
      : report.failed_count
        ? "Failed"
        : "Unknown";
  (report.controls || []).forEach((control) => {
    const card = document.createElement("article");
    card.className = "control";
    const head = document.createElement("div");
    head.className = "control-head";
    const title = document.createElement("span");
    title.className = "control-title";
    title.textContent = displayValue(control.control_id);
    head.append(title, statusBadge(control.status));
    const detail = document.createElement("div");
    detail.className = "detail";
    detail.textContent =
      `Expected: ${displayValue(control.expected_value)} · ` +
      `Actual: ${displayValue(control.actual_value)} · ` +
      `${displayValue(control.explanation)} · Evidence: ` +
      displayValue(control.evidence_source);
    card.append(head, detail);
    grid.append(card);
  });
  $("containmentLimitations").textContent = (report.limitations || []).join(" ");
}

function renderProbeResults(data) {
  const container = $("probeResults");
  container.replaceChildren();
  const summary = document.createElement("p");
  summary.className = "notice";
  summary.textContent = `Containment verified ${data.verified_count}/${data.total}.`;
  container.append(summary);
  const table = document.createElement("table");
  table.className = "probe-table";
  const header = document.createElement("tr");
  ["Probe", "Expected", "Actual", "Result", "Evidence source", "Trust tier"]
    .forEach((label) => {
      const cell = document.createElement("th");
      cell.scope = "col";
      cell.textContent = label;
      header.append(cell);
    });
  table.append(header);
  (data.results || []).forEach((item) => {
    const row = document.createElement("tr");
    [
      item.probe, item.expected, item.actual, displayStatus(item.status),
      item.evidence_source, item.trust_tier,
    ].forEach((value, index) => {
      const cell = document.createElement("td");
      cell.textContent = displayValue(value);
      if (index === 3) {
        cell.className = item.status === "passed"
          ? "probe-pass"
          : "probe-fail";
      }
      row.append(cell);
    });
    table.append(row);
  });
  container.append(table);
  $("containmentSummary").textContent =
    `Containment verified ${data.verified_count}/${data.total}.`;
  $("containmentStatus").replaceChildren(
    statusBadge(data.verified_count === data.total ? "passed" : "failed"),
  );
  $("containmentLimitations").textContent = (data.limitations || []).join(" ");
}

async function runProbes() {
  const button = $("runProbesButton");
  button.disabled = true;
  button.textContent = "Running probes…";
  try {
    const response = await fetch("/containment/probes", {
      method: "POST",
      headers: { "X-API-Key": $("apiKey").value },
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "Containment probes failed.");
    }
    renderProbeResults(data);
  } catch (error) {
    $("probeResults").replaceChildren();
    const failure = document.createElement("p");
    failure.className = "empty error";
    failure.textContent = error.message;
    $("probeResults").append(failure);
  } finally {
    button.disabled = false;
    button.textContent = "Run probes";
  }
}

function renderFindings(report) {
  const list = $("findingList");
  list.replaceChildren();
  const predictions = report.ai_predictions?.predictions || [];
  const comparisons = Array.isArray(report.comparison?.comparisons)
    ? report.comparison.comparisons
    : [];
  $("predictionMetric").textContent = predictions.length;
  const confirmed = comparisons.filter((item) =>
    ["verified", "passed", "stdout_captured"].includes(item.status),
  ).length;
  $("confirmedMetric").textContent = confirmed;
  const isForgery = report.sample_id === "forgery" ||
    (report.evidence_summary || []).some((item) =>
      item.trust_tier === "program_reported");
  if (isForgery) {
    const callout = document.createElement("p");
    callout.className = "notice warning-callout";
    callout.textContent =
      "Program-reported evidence is untrusted and was not host-verified.";
    list.append(callout);
  }
  if (!predictions.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No security findings were confirmed in this run.";
    list.append(empty);
    return;
  }
  predictions.forEach((prediction) => {
    const comparison = comparisons.find((item) =>
      item.operation === prediction.operation && item.target === prediction.target,
    ) || {};
    const card = document.createElement("article");
    card.className = "finding";
    const top = document.createElement("div");
    top.className = "finding-top";
    const title = document.createElement("h3");
    title.textContent = displayValue(prediction.operation);
    top.append(title, statusBadge(prediction.risk_level));
    const explanation = document.createElement("p");
    explanation.textContent = prediction.reason || "The model identified this as a behavior to review.";
    const context = document.createElement("p");
    context.className = "finding-context";
    context.textContent = `Predicted behavior: ${displayValue(prediction.operation)} targeting ${displayValue(prediction.target)}.`;
    const meta = document.createElement("div");
    meta.className = "finding-meta";
    meta.textContent =
      `AI confidence: ` +
      (prediction.confidence == null ? "Unknown" : `${Math.round(prediction.confidence * 100)}%`) +
      " · Evidence status: ";
    meta.append(statusBadge(comparison.status || "unknown"));
    const source = document.createElement("code");
    source.textContent = `Source evidence: ${displayValue(prediction.evidence)}`;
    meta.append(source);
    const next = document.createElement("p");
    next.className = "finding-next";
    next.textContent = "Next step: review the source location and confirm whether this behavior is expected.";
    card.append(top, context, explanation, meta, next);
    list.append(card);
  });

  const comparisonList = $("comparisonList");
  comparisonList.replaceChildren();
  comparisons.forEach((comparison) => {
    const card = document.createElement("article");
    card.className = "comparison";
    const title = document.createElement("h3");
    title.textContent = `${displayValue(comparison.operation)} · ${displayValue(comparison.target)}`;
    const status = document.createElement("p");
    status.className = "finding-meta";
    status.append(statusBadge(comparison.status || "unknown"));
    const outcome = document.createElement("p");
    outcome.textContent = comparison.status === "predicted_not_observed"
      ? "Not independently observed"
      : displayValue(comparison.actual_outcome);
    const reason = document.createElement("p");
    reason.className = "detail";
    reason.textContent = displayValue(comparison.reason);
    card.append(title, status, outcome, reason);
    comparisonList.append(card);
  });
  if (!comparisons.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No comparison records were returned for this run.";
    comparisonList.append(empty);
  }
}

function renderExecution(report) {
  const execution = report.sandbox_execution || {};
  const metadata = execution.host_metadata || {};
  $("executionExitCode").textContent = displayValue(
    metadata.exit_code ?? execution.exit_code,
  );
  $("executionDuration").textContent = execution.execution_duration_seconds == null
    ? "Not available"
    : `${execution.execution_duration_seconds}s`;
  $("executionOom").textContent = metadata.oom_killed ?? execution.oom_killed
    ? "Stopped after exceeding memory limit"
    : "No";
  $("executionLifecycle").textContent = displayValue(
    metadata.status || execution.status,
  );
  $("executionCleanup").textContent = displayValue(execution.cleanup);
  const scope = report.execution_scope || {};
  $("scopeValue").textContent =
    `${displayValue(scope.planned_test_cases, "0")} test cases planned · ` +
    `${displayValue(scope.executed_sandbox_runs, "0")} sandbox run executed · ` +
    `${displayValue(scope.individually_executed_test_cases, "0")} test cases individually executed`;
  const plannedCount = Array.isArray(report.agent_plan?.test_cases)
    ? report.agent_plan.test_cases.length
    : 0;
  const plannedNote = document.querySelector("[data-planned-note]");
  if (plannedNote) {
    plannedNote.textContent =
      `${plannedCount} planned cases, 1 sandbox run. Planned questions are not separate executions.`;
  }
  $("hostMetadata").textContent = Object.keys(metadata).length
    ? JSON.stringify(metadata, null, 2)
    : "Host metadata was not available.";
  const planList = $("plannedTests");
  planList.replaceChildren();
  const testCases = report.agent_plan?.test_cases || [];
  if (!testCases.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No planned test cases were returned.";
    planList.append(empty);
  } else {
    testCases.forEach((testCase, index) => {
      const item = document.createElement("li");
      item.textContent = typeof testCase === "string"
        ? testCase
        : `${index + 1}. ${displayValue(testCase.question || testCase.description || testCase)}`;
      planList.append(item);
    });
  }
}

function renderEvidence(report) {
  const list = $("evidenceList");
  list.replaceChildren();
  const evidence = report.evidence_summary || [];
  if (!evidence.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No independent evidence was collected in this run.";
    list.append(empty);
    return;
  }
  evidence.forEach((item) => {
    const card = document.createElement("article");
    card.className = "evidence";
    const head = document.createElement("div");
    head.className = "evidence-head";
    const title = document.createElement("span");
    title.className = "evidence-title";
    title.textContent = displayValue(item.evidence_type || item.source);
    head.append(title, statusBadge(item.trust_tier || "unknown"));
    const detail = document.createElement("div");
    detail.className = "detail";
    detail.textContent =
      `Source: ${displayValue(item.source)} · Context: ${displayValue(item.collected_at || item.timestamp)} · ` +
      `Trust: ${displayStatus(item.trust_tier || "unknown")}`;
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "View structured payload";
    const payload = document.createElement("pre");
    payload.className = "log";
    payload.textContent = item.payload_json || JSON.stringify(item, null, 2);
    details.append(summary, payload);
    card.append(head, detail, details);
    list.append(card);
  });
}

function displayReport(report) {
  const normalized = normalizeReport(report);
  currentReport = normalized;
  renderTrace($("reportTrace"), normalized.pipeline_stages || {});
  renderTrace($("traceList"), normalized.pipeline_stages || {});
  $("runId").textContent = displayValue(normalized.run_id, "—");
  $("overviewRunId").textContent = displayValue(normalized.run_id, "Demo result — not persisted");
  $("outcomeBadge").replaceChildren(statusBadge(normalized.analysis_status || normalized.overall_status));
  const analysis = normalized.ai_predictions || {};
  const outcome = normalized.analysis_status || normalized.overall_status;
  const execution = normalized.sandbox_execution || {};
  const containment = normalized.containment_report;
  $("summaryOutcome").textContent = displayStatus(outcome);
  $("summaryExitCode").textContent = displayValue(
    execution.host_metadata?.exit_code ?? execution.exit_code,
  );
  $("riskValue").textContent = displayValue(analysis.risk_level);
  $("riskSummary").textContent = displayValue(analysis.summary);
  $("durationMetric").textContent = normalized.sandbox_execution?.execution_duration_seconds == null
    ? "Not available"
    : `${normalized.sandbox_execution.execution_duration_seconds}s`;
  $("sourceHash").textContent = displayValue(normalized.source_sha256, "Not retained for demo results");
  $("sizeMetric").textContent = displayValue(normalized.source_size_bytes, "Available in authenticated history");
  $("objectiveValue").textContent = displayValue(normalized.analysis_objective, "Not provided");
  $("scopeValue").textContent = normalized.execution_scope?.executed_sandbox_runs === 1
    ? "One fixed sandbox run"
    : "No sandbox run";
  $("containmentMetric").textContent = containment
    ? `${displayValue(containment.verified_count, "0")}/9`
    : "Not run";
  const probeReport = normalized.behavioral_probes || normalized.probe_report;
  $("probeMetric").textContent = probeReport
    ? `${displayValue(probeReport.verified_count, "0")}/${displayValue(probeReport.total, "8")}`
    : "Not available";
  $("executionSummary").textContent = normalized.sandbox_execution
    ? `Program ${displayStatus(normalized.sandbox_execution.status)} · Exit code ${displayValue(normalized.sandbox_execution.exit_code, "not available")}${normalized.sandbox_execution.oom_killed ? " · Stopped after exceeding memory limit" : ""}.`
    : "The program did not run in the sandbox.";
  $("limitationSummary").textContent = (normalized.limitations || []).join(" ") ||
    "Results reflect one bounded sandbox run and available host evidence only.";
  renderFindings(normalized);
  renderExecution(normalized);
  renderContainment(normalized.containment_report);
  renderEvidence(normalized);
  $("stdoutOutput").textContent =
    normalized.sandbox_execution?.stdout ||
    normalized.sandbox_execution?.program_stdout ||
    "No output captured.";
  $("stderrOutput").textContent = normalized.sandbox_execution?.stderr || "No error output captured.";
  showPage("reports");
  $("downloadButton").disabled = false;
}

async function loadDemoConfig() {
  try {
    const response = await fetch("/demo/samples");
    if (!response.ok) return;
    const data = await response.json();
    demoMode = true;
    demoUploadEnabled = data.upload_enabled === true;
    $("demoPanel").hidden = false;
    $("apiKeyField").hidden = true;
    $("accessCodeField").hidden = !demoUploadEnabled;
    $("analysisForm").hidden = !demoUploadEnabled;
    $("analysisForm").querySelector(".drop").hidden = !demoUploadEnabled;
    $("fileInput").hidden = !demoUploadEnabled;
    $("fileMeta").hidden = !demoUploadEnabled;
    $("sourcePreview").hidden = !demoUploadEnabled;
    $("objective").closest(".field").hidden = !demoUploadEnabled;
    $("analyzeButton").hidden = !demoUploadEnabled;
    $("demoSample").replaceChildren();
    (data.samples || []).forEach((sample) => {
      const option = document.createElement("option");
      option.value = sample.id;
      option.textContent = sample.filename;
      $("demoSample").append(option);
    });
    updateAnalyzeButton();
  } catch (error) {
    console.error("Unable to load demo configuration.", error);
  }
}

async function runDemoSample() {
  const button = $("demoRunButton");
  button.disabled = true;
  $("demoStatus").textContent = "Running in the sandbox...";
  try {
    const response = await fetch(
      `/demo/analyze/${encodeURIComponent($("demoSample").value)}`,
      { method: "POST" },
    );
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Demo sample failed.");
    displayReport(data);
    $("demoStatus").textContent = data.execution_performed
      ? "Sample completed with a fresh sandbox result."
      : "Sample did not execute.";
  } catch (error) {
    $("demoStatus").textContent = error.message || "Demo sample failed.";
    clearReport();
  } finally {
    button.disabled = false;
  }
}

async function analyze() {
  clearReport();
  $("analyzeButton").disabled = true;
  setStatus("Running in the sandbox...");
  const form = new FormData();
  form.append("file", selectedFile);
  const objective = $("objective").value.trim();
  if (objective) form.append("objective", objective);
  try {
    const headers = demoMode
      ? { "X-Access-Code": $("accessCode").value.trim() }
      : { "X-API-Key": $("apiKey").value };
    const response = await fetch("/analyze", {
      method: "POST",
      headers,
      body: form,
    });
    const data = await response.json();
    if (!response.ok && response.status !== 422 && response.status !== 502) {
      throw new Error(data.detail || "Analysis failed.");
    }
    displayReport(data);
    setStatus(data.analysis_status === "completed"
      ? "Completed with evidence."
      : `Result returned: ${displayStatus(data.analysis_status)}. Execution did not occur.`);
    page = 0;
    await loadHistory();
  } catch (error) {
    setStatus(error.message || "Unable to complete analysis.", true);
    clearReport();
  } finally {
    updateAnalyzeButton();
  }
}

async function loadHealth() {
  $("healthDot").className = "";
  $("healthText").textContent = "Checking connection";
  try {
    const response = await fetch("/health");
    $("healthDot").className = response.ok ? "ok" : "bad";
    $("healthText").textContent = response.ok ? "Backend online" : "Backend unavailable";
  } catch {
    $("healthDot").className = "bad";
    $("healthText").textContent = "Backend unavailable";
  }
}

async function loadHistory() {
  const key = $("apiKey").value.trim();
  if (!key) {
    $("historyList").replaceChildren();
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = demoMode
      ? "History is available to the owner only. Public demo results are not saved."
      : "Shared history requires a workspace API key. Public demo results are not saved.";
    $("historyList").append(empty);
    return;
  }
  const list = $("historyList");
  list.replaceChildren();
  const loading = document.createElement("div");
  loading.className = "empty";
  loading.textContent = "Loading history…";
  list.append(loading);
  try {
    const response = await fetch(`/runs?limit=${pageSize}&offset=${page * pageSize}`, {
      headers: { "X-API-Key": key },
    });
    if (!response.ok) {
      const message = response.status === 429
        ? "Too many requests. Please wait a moment before refreshing history."
        : response.status === 503
          ? "History is temporarily busy. Please try again shortly."
          : response.status === 401
            ? "Invalid API key."
            : "History unavailable.";
      throw new Error(message);
    }
    const data = await response.json();
    lastRuns = data.runs || [];
    const query = $("historySearch").value.trim().toLowerCase();
    const runs = lastRuns.filter((run) =>
      !query || displayValue(run.filename).toLowerCase().includes(query) ||
      displayValue(run.run_id).toLowerCase().includes(query));
    list.replaceChildren();
    if (!runs.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = query ? "No matching runs." : "No saved analyses yet.";
      list.append(empty);
    }
    runs.forEach((run) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "history-item";
      const name = document.createElement("strong");
      name.textContent = displayValue(run.filename);
      const meta = document.createElement("span");
      meta.textContent = `${displayStatus(run.status)} · Risk ${displayValue(run.risk_level)} · ${displayValue(run.created_at)}`;
      const id = document.createElement("code");
      id.textContent = displayValue(run.run_id);
      button.append(name, meta, id);
      button.addEventListener("click", () => loadRun(run.run_id));
      list.append(button);
    });
    $("pageLabel").textContent = `Page ${page + 1}`;
    $("prevPage").disabled = page === 0;
    $("nextPage").disabled = lastRuns.length < pageSize;
  } catch (error) {
    list.replaceChildren();
    const failure = document.createElement("div");
    failure.className = "empty error";
    failure.textContent = error.message;
    list.append(failure);
  }
}

async function loadRun(runId) {
  const detail = $("historyDetail");
  detail.textContent = "Loading…";
  try {
    const response = await fetch(`/runs/${encodeURIComponent(runId)}`, {
      headers: { "X-API-Key": $("apiKey").value },
    });
    if (!response.ok) throw new Error("Unable to load persisted run.");
    const data = await response.json();
    $("historyDetailText").textContent = `Persisted ledger record · ${runId}`;
    const record = data.run || {};
    displayReport({
      analysis_status: record.status,
      run_id: record.run_id,
      ai_predictions: {
        summary: record.summary,
        risk_level: record.risk_level,
        predictions: (data.findings || []).map((finding) => ({
          operation: finding.operation,
          target: finding.target,
          risk_level: finding.risk_level || "unknown",
          reason: finding.reason || "Persisted comparison record.",
          confidence: finding.confidence,
          evidence: finding.evidence || "Persisted ledger record.",
        })),
      },
      comparison: {
        comparisons: (data.findings || []).map((finding) => ({
          operation: finding.operation,
          target: finding.target,
          predicted_risk: finding.risk_level,
          ai_confidence: finding.confidence,
          status: finding.comparison_status,
          reason: finding.reason,
        })),
        limitations: ["Historical view contains persisted ledger evidence only."],
      },
      pipeline_stages: Object.fromEntries(
        (data.steps || []).map((step) => [step.stage, step]),
      ),
      evidence_summary: data.evidence || [],
      containment_report: {
        controls: data.containment_checks || [],
        overall_status: record.containment_status,
      },
      execution_scope: {},
      limitations: [
        "Detailed sandbox metadata and planned test cases are not stored in the ledger.",
      ],
      sandbox_execution: {
        status: record.sandbox_status,
        exit_code: record.sandbox_exit_code,
      },
    });
    detail.textContent = `Report loaded for ${runId}.`;
  } catch (error) {
    detail.textContent = error.message;
  }
}

function activateTab(name) {
  document.querySelectorAll(".tab").forEach((tab) => {
    const selected = tab.dataset.tab === name;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", String(selected));
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    const selected = panel.dataset.panel === name;
    panel.classList.toggle("active", selected);
    panel.hidden = !selected;
  });
}

function initializeStep(name, callback) {
  try {
    callback();
  } catch (error) {
    console.error(`Initialization failed for ${name}.`, error);
  }
}

initializeStep("file dropzone", () => {
  const dropzone = $("dropzone");
  dropzone.addEventListener("dragover", (event) => { event.preventDefault(); dropzone.classList.add("drag"); });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag"));
  dropzone.addEventListener("drop", (event) => { event.preventDefault(); dropzone.classList.remove("drag"); chooseFile(event.dataTransfer.files[0]); });
  dropzone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); $("fileInput").click(); }
  });
  $("fileInput").addEventListener("change", () => chooseFile($("fileInput").files[0]));
});
initializeStep("analysis form", () => {
  $("apiKey").addEventListener("input", updateAnalyzeButton);
  $("accessCode").addEventListener("input", updateAnalyzeButton);
  $("analysisForm").addEventListener("submit", (event) => { event.preventDefault(); analyze(); });
});
initializeStep("history controls", () => {
  $("refreshHistory").addEventListener("click", () => { page = 0; loadHistory(); });
  $("historySearch").addEventListener("input", () => loadHistory());
  $("prevPage").addEventListener("click", () => { if (page) { page -= 1; loadHistory(); } });
  $("nextPage").addEventListener("click", () => { if (lastRuns.length === pageSize) { page += 1; loadHistory(); } });
});
initializeStep("containment probes", () => $("runProbesButton").addEventListener("click", runProbes));
initializeStep("demo samples", () => $("demoRunButton").addEventListener("click", runDemoSample));
initializeStep("report export", () => $("downloadButton").addEventListener("click", () => {
  if (!currentReport) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(currentReport, null, 2)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `dryrun-${displayValue(currentReport.run_id, "report")}.json`;
  link.click();
  URL.revokeObjectURL(url);
}));
initializeStep("report tabs", () => document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => activateTab(tab.dataset.tab))));
initializeStep("main navigation", () => document.querySelectorAll("[data-nav]").forEach((nav) => nav.addEventListener("click", () => showPage(nav.dataset.nav))));
initializeStep("new analysis", () => $("newButton").addEventListener("click", () => {
  currentReport = null;
  $("analysisForm").reset();
  selectedFile = null;
  $("fileMeta").textContent = "No file selected";
  $("sourcePreview").textContent = "Select a Python file to preview its source.";
  updateAnalyzeButton();
  showPage("analyze");
  window.scrollTo({ top: 0, behavior: "smooth" });
}));
initializeStep("initial trace", () => renderTrace($("traceList")));
initializeStep("initial page", () => showPage("analyze"));
initializeStep("backend health", () => loadHealth());
initializeStep("demo configuration", () => loadDemoConfig());
