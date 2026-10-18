/* =========================================================
   Auditor frontend
   Talks to backend/main.py (FastAPI) over HTTP.
   Flow: POST /audit/upload (zip) -> audit_id -> GET /audit/{audit_id}
   ========================================================= */

const CONFIG = {
  // Always talk to port 8000 on the current hostname (e.g. 148.100.108.240:8000 or localhost:8000)
  API_BASE: location.protocol.startsWith("http")
    ? `${location.protocol}//${location.hostname}:8000`
    : "http://localhost:8000",
  UPLOAD_ENDPOINT: "/audit/upload",    // POST multipart/form-data
  RESULT_ENDPOINT: "/audit/",          // GET + audit_id
  ZIP_FIELD: "archive",                // form field name the backend expects
  TIMEOUT_MS: 10 * 60 * 1000,          // audits can be slow
};

const $ = (id) => document.getElementById(id);
const els = {
  tabs: document.querySelectorAll(".tab"),
  bodyZip: $("body-zip"), bodyRepo: $("body-repo"),
  dropzone: $("dropzone"), fileInput: $("file-input"),
  dropTitle: $("drop-title"), dropSub: $("drop-sub"),
  repoUrl: $("repo-url"), formError: $("form-error"),
  runBtn: $("run-btn"), demoBtn: $("demo-btn"),
  auditPanel: $("audit-panel"), progressPanel: $("progress-panel"),
  steps: document.querySelectorAll("#steps li"),
  results: $("results"), resultsMeta: $("results-meta"),
  scoreCard: $("score-card"), gaugeFill: $("gauge-fill"), scoreNum: $("score-num"),
  verdictTitle: $("verdict-title"), verdictText: $("verdict-text"),
  stTotal: $("st-total"), stKilled: $("st-killed"), stSurvived: $("st-survived"), stErrors: $("st-errors"),
  aiPanel: $("ai-panel"), aiText: $("ai-text"),
  filters: document.querySelectorAll(".filter"),
  mutants: $("mutants"), empty: $("empty"),
  downloadBtn: $("download-btn"), againBtn: $("again-btn"),
  toast: $("toast"),
};

const state = { mode: "zip", file: null, report: null, filter: "all", stepTimer: null };

/* ---------- Input handling ---------- */
els.tabs.forEach((tab) => tab.addEventListener("click", () => setMode(tab.dataset.tab)));

function setMode(mode) {
  state.mode = mode;
  els.tabs.forEach((t) => {
    const on = t.dataset.tab === mode;
    t.classList.toggle("active", on);
    t.setAttribute("aria-selected", String(on));
  });
  els.bodyZip.classList.toggle("hidden", mode !== "zip");
  els.bodyRepo.classList.toggle("hidden", mode !== "repo");
  showError("");
}

els.fileInput.addEventListener("change", () => pickFile(els.fileInput.files[0]));
["dragenter", "dragover"].forEach((ev) =>
  els.dropzone.addEventListener(ev, (e) => { e.preventDefault(); els.dropzone.classList.add("drag"); })
);
["dragleave", "drop"].forEach((ev) =>
  els.dropzone.addEventListener(ev, (e) => { e.preventDefault(); els.dropzone.classList.remove("drag"); })
);
els.dropzone.addEventListener("drop", (e) => pickFile(e.dataTransfer.files[0]));

function pickFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".zip")) {
    showError("That is not a .zip file. Compress your project folder into a .zip and try again.");
    return;
  }
  state.file = file;
  showError("");
  els.dropzone.classList.add("has-file");
  els.dropTitle.textContent = file.name;
  els.dropSub.textContent = `${(file.size / 1024).toFixed(1)} KB · click to change`;
}

function showError(msg) {
  els.formError.textContent = msg;
  els.formError.hidden = !msg;
}

/* ---------- Run audit ---------- */
els.runBtn.addEventListener("click", runAudit);
els.demoBtn.addEventListener("click", () => {
  showProgress();
  runStepsFor(2400, () => renderReport(DEMO_REPORT, true));
});
els.againBtn.addEventListener("click", resetToStart);
els.downloadBtn.addEventListener("click", downloadReport);

async function runAudit() {
  showError("");
  if (!state.file) return showError("Choose a project .zip file first.");
  const form = new FormData();
  form.append(CONFIG.ZIP_FIELD, state.file);

  showProgress();
  startSteps();

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), CONFIG.TIMEOUT_MS);

  try {
    // 1) upload the zip; the backend runs the audit and returns an audit_id
    const res = await fetch(CONFIG.API_BASE + CONFIG.UPLOAD_ENDPOINT, {
      method: "POST", body: form, signal: controller.signal,
    });
    const job = await readJson(res);

    // 2) fetch the full report for that audit_id
    const full = await fetch(
      CONFIG.API_BASE + CONFIG.RESULT_ENDPOINT + encodeURIComponent(job.audit_id),
      { signal: controller.signal }
    );
    const data = await readJson(full);

    finishSteps();
    setTimeout(() => renderReport(data, false), 450);
  } catch (err) {
    stopSteps();
    hideProgress();
    showError(
      err.name === "AbortError" ? "The audit took too long and was stopped. Try a smaller project."
      : err.name === "TypeError" ? "Cannot reach the backend. Check it is running and that CORS allows this page, or click \u201cSee a sample report\u201d."
      : `Audit failed: ${err.message}`
    );
  } finally {
    clearTimeout(timer);
  }
}

async function readJson(res) {
  if (!res.ok) {
    let detail = "";
    try { const j = await res.json(); if (typeof j.detail === "string") detail = j.detail; } catch (_) {}
    throw new Error(detail || `Server returned ${res.status}`);
  }
  return res.json();
}

/* ---------- Progress steps ---------- */
function showProgress() {
  els.auditPanel.classList.add("hidden");
  els.results.classList.add("hidden");
  els.progressPanel.classList.remove("hidden");
  els.steps.forEach((s) => s.classList.remove("active", "done"));
  els.progressPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}
function hideProgress() {
  els.progressPanel.classList.add("hidden");
  els.auditPanel.classList.remove("hidden");
}
// Real runs: advance slowly and hold on the last-but-one step until the server answers.
function startSteps() {
  let i = 0;
  setStep(i);
  stopSteps();
  state.stepTimer = setInterval(() => {
    if (i < els.steps.length - 2) setStep(++i);
  }, 3500);
}
// Demo: spread all steps across a fixed time.
function runStepsFor(totalMs, done) {
  stopSteps();
  let i = 0;
  setStep(i);
  const gap = totalMs / els.steps.length;
  state.stepTimer = setInterval(() => {
    i++;
    if (i < els.steps.length) return setStep(i);
    stopSteps(); finishSteps(); setTimeout(done, 400);
  }, gap);
}
function setStep(index) {
  els.steps.forEach((s, n) => {
    s.classList.toggle("done", n < index);
    s.classList.toggle("active", n === index);
  });
}
function finishSteps() {
  stopSteps();
  els.steps.forEach((s) => { s.classList.remove("active"); s.classList.add("done"); });
}
function stopSteps() {
  if (state.stepTimer) clearInterval(state.stepTimer);
  state.stepTimer = null;
}

/* ---------- Report rendering ---------- */
function renderReport(raw, isDemo) {
  const report = normalise(raw);
  state.report = report;
  state.filter = "all";

  els.progressPanel.classList.add("hidden");
  els.results.classList.remove("hidden");

  // Baseline failure: tests were already red, so mutation results are meaningless.
  const meta = [];
  if (report.project) meta.push(report.project);
  if (report.duration) meta.push(`${report.duration}s`);
  if (isDemo) meta.push("sample data");
  els.resultsMeta.textContent = meta.join(" · ");

  const { killed, survived, errors, total, score } = report.counts;
  els.stTotal.textContent = total;
  els.stKilled.textContent = killed;
  els.stSurvived.textContent = survived;
  els.stErrors.textContent = errors;

  const tone = score >= 80 ? "good" : score >= 50 ? "mid" : "bad";
  els.scoreCard.className = `score-card tone-${tone}`;
  const v = verdictFor(score, report.baselineFailed);
  if (report.baselineFailed && report.errorMessage) v.text = report.errorMessage;
  els.verdictTitle.textContent = v.title;
  els.verdictText.textContent = v.text;
  animateScore(score);

  els.aiPanel.hidden = !report.aiSummary;
  els.aiText.textContent = report.aiSummary;

  els.filters.forEach((f) => f.classList.toggle("active", f.dataset.filter === "all"));
  els.filters.forEach((f) => (f.onclick = () => setFilter(f.dataset.filter)));
  drawMutants();

  els.results.scrollIntoView({ behavior: "smooth", block: "start" });
}

// Reads the first field that exists, so small naming differences in the backend do not break the page.
function pick(obj, keys, fallback) {
  for (const k of keys) {
    if (obj && obj[k] !== undefined && obj[k] !== null && obj[k] !== "") return obj[k];
  }
  return fallback;
}

function normStatus(s) {
  const v = String(s || "").toLowerCase();
  if (["survived", "alive", "missed", "not_killed"].includes(v)) return "survived";
  if (["killed", "caught", "detected"].includes(v)) return "killed";
  return "error";
}

function normalise(raw) {
  // Extract mutants: check top-level mutants first, then flatten file_results
  let list = pick(raw, ["mutants", "mutations", "mutation_results", "results"], null);
  if (!list && raw.file_results && Array.isArray(raw.file_results)) {
    list = raw.file_results.flatMap((f) =>
      (f.mutants || []).map((m) => ({ ...m, file: m.file_path || f.file_path }))
    );
  }
  list = list || [];

  const mutants = list.map((m0, i) => {
    const m = (m0 && typeof m0.mutation === "object") ? { ...m0.mutation, ...m0 } : m0;
    return {
      id: pick(m, ["id", "mutant_id"], i + 1),
      file: pick(m, ["file", "file_path", "filename", "source_file"], "unknown file"),
      line: pick(m, ["line", "line_number", "lineno"], "?"),
      operator: pick(m, ["operator", "mutation_type", "type", "description", "mutation_description"], "Code change"),
      original: pick(m, ["original", "original_code", "original_line"], ""),
      mutated: pick(m, ["mutated", "mutated_code", "mutated_line"], ""),
      status: normStatus(pick(m, ["status", "result", "outcome"], "")),
      suggestion: pick(m, ["suggestion", "recommendation", "fix", "ai_suggestion"], ""),
    };
  });

  const killed = raw.total_killed !== undefined ? raw.total_killed : mutants.filter((m) => m.status === "killed").length;
  const survived = raw.total_survived !== undefined ? raw.total_survived : mutants.filter((m) => m.status === "survived").length;
  const errors = mutants.filter((m) => m.status === "error").length;
  const total = raw.total_mutants !== undefined ? raw.total_mutants : mutants.length;
  const judged = killed + survived;
  const score = raw.overall_mutation_score !== undefined
    ? Math.round(raw.overall_mutation_score)
    : (judged ? Math.round((killed / judged) * 100) : 0);

  let ai = pick(raw, ["ai_summary", "ai_analysis", "summary", "analysis"], "");
  if (Array.isArray(ai)) ai = ai.join("\n");
  else if (ai && typeof ai === "object") ai = Object.values(ai).flat().filter((v) => typeof v === "string").join("\n");
  if (typeof ai !== "string") ai = "";

  if (raw.recommendations && Array.isArray(raw.recommendations) && raw.recommendations.length > 0) {
    const recsText = raw.recommendations.map((r) => `• ${r}`).join("\n");
    ai = ai ? `${ai}\n\nRecommendations:\n${recsText}` : recsText;
  }

  const duration = pick(raw, ["duration_seconds", "duration"], null);
  const failed = String(raw.status || "").toLowerCase() === "failed";
  return {
    project: pick(raw, ["project_name"], ""),
    duration: duration != null && !isNaN(duration) ? Number(duration).toFixed(1) : "",
    aiSummary: ai,
    baselineFailed: raw.baseline_passed === false || failed,
    errorMessage: raw.error_message || "",
    mutants,
    counts: { killed, survived, errors, total, score },
    raw,
  };
}

function verdictFor(score, baselineFailed) {
  if (baselineFailed) {
    return { title: "Tests already failing", text: "Fix the failing tests first. Auditor needs a green test suite to compare against." };
  }
  if (score >= 80) return { title: "Strong tests", text: "Your tests catch almost every bug we injected. Check the few that slipped through." };
  if (score >= 50) return { title: "Needs work", text: "Your tests catch some bugs but miss too many. Start with the missed bugs below." };
  return { title: "Weak tests", text: "Most injected bugs passed unnoticed. These tests look fine but would not warn you in a real failure." };
}

function animateScore(score) {
  const C = 2 * Math.PI * 52;
  els.gaugeFill.style.strokeDasharray = C;
  els.gaugeFill.style.strokeDashoffset = C;
  requestAnimationFrame(() => requestAnimationFrame(() => {
    els.gaugeFill.style.strokeDashoffset = C * (1 - score / 100);
  }));
  const start = performance.now();
  const dur = 1000;
  (function tick(now) {
    const t = Math.min(1, (now - start) / dur);
    els.scoreNum.textContent = Math.round(score * t);
    if (t < 1) requestAnimationFrame(tick);
  })(start);
}

function setFilter(f) {
  state.filter = f;
  els.filters.forEach((b) => b.classList.toggle("active", b.dataset.filter === f));
  drawMutants();
}

const STATUS_LABEL = { survived: "Missed", killed: "Caught", error: "Error" };

function drawMutants() {
  const list = state.report.mutants
    .filter((m) => state.filter === "all" || m.status === state.filter)
    // missed bugs first: they are the ones that matter
    .sort((a, b) => rank(a.status) - rank(b.status));
  els.mutants.replaceChildren();
  els.empty.classList.toggle("hidden", list.length > 0);

  list.forEach((m) => {
    const d = document.createElement("details");
    d.className = "mutant";
    d.dataset.status = m.status;
    if (m.status === "survived" && list.indexOf(m) === 0) d.open = true;

    const s = document.createElement("summary");
    s.append(
      el("span", `badge ${m.status}`, STATUS_LABEL[m.status]),
      el("span", "m-where", `${m.file}:${m.line}`),
      el("span", "m-what", m.operator)
    );

    const body = document.createElement("div");
    body.className = "mutant-body";

    const diff = document.createElement("div");
    diff.className = "diff";
    diff.append(el("div", "del", `− ${m.original}`), el("div", "add", `+ ${m.mutated}`));
    body.append(diff);

    const note = m.status === "survived"
      ? ["What to do", m.suggestion || "Add a test that fails when this line changes."]
      : m.status === "killed"
      ? ["Good news", "At least one test failed on this change, so it would catch this bug."]
      : ["Could not run", "This change broke the code so badly it could not be tested (for example, a syntax error)."];
    const fix = document.createElement("div");
    fix.className = "fix";
    fix.append(el("strong", "", note[0]), document.createTextNode(note[1]));
    body.append(fix);

    d.append(s, body);
    els.mutants.append(d);
  });
}

const rank = (s) => ({ survived: 0, error: 1, killed: 2 }[s] ?? 3);

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  n.textContent = text; // textContent keeps uploaded code from being treated as HTML
  return n;
}

/* ---------- Misc ---------- */
function downloadReport() {
  if (!state.report) return;
  const blob = new Blob([JSON.stringify(state.report.raw, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "auditor-report.json";
  a.click();
  URL.revokeObjectURL(a.href);
  toast("Report downloaded");
}

function resetToStart() {
  state.file = null; state.report = null;
  els.fileInput.value = "";
  els.dropzone.classList.remove("has-file");
  els.dropTitle.textContent = "Drop your project .zip here";
  els.dropSub.textContent = "or click to choose a file";
  if (els.repoUrl) els.repoUrl.value = "";
  showError("");
  els.results.classList.add("hidden");
  els.auditPanel.classList.remove("hidden");
  els.auditPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

let toastTimer;
function toast(msg) {
  els.toast.textContent = msg;
  els.toast.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => els.toast.classList.add("hidden"), 2200);
}

/* ---------- Sample data (matches sample_project/calculator.py) ---------- */
const DEMO_REPORT = {
  project_name: "sample_project",
  baseline_passed: true,
  duration_seconds: 6.8,
  ai_summary:
    "Your tests check that add() returns a number but never check the value. " +
    "Changing + to - goes unnoticed.\n" +
    "Add assertions with exact expected results, and test the divide-by-zero path.",
  mutants: [
    { id: 1, file: "calculator.py", line: 2, operator: "Arithmetic swap (+ to -)",
      original: "return a + b", mutated: "return a - b", status: "survived",
      suggestion: "Add assert add(2, 3) == 5. The current test only checks the result is an int." },
    { id: 2, file: "calculator.py", line: 11, operator: "Condition flip (== to !=)",
      original: "if b == 0:", mutated: "if b != 0:", status: "survived",
      suggestion: "Test divide(10, 0) raises ValueError, and divide(10, 2) returns 5." },
    { id: 3, file: "calculator.py", line: 5, operator: "Arithmetic swap (- to +)",
      original: "return a - b", mutated: "return a + b", status: "killed" },
    { id: 4, file: "calculator.py", line: 8, operator: "Arithmetic swap (* to /)",
      original: "return a * b", mutated: "return a / b", status: "killed" },
    { id: 5, file: "calculator.py", line: 14, operator: "Return value change",
      original: "return a / b", mutated: "return a // b", status: "survived",
      suggestion: "Test divide(7, 2) == 3.5 to catch integer division." },
    { id: 6, file: "calculator.py", line: 3, operator: "Constant change",
      original: "return a * 1", mutated: "return a * ", status: "error" },
  ],
};
