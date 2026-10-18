/* =========================================================
   Auditor frontend
   Talks to the backend (audit_engine.py) over HTTP.
   Change CONFIG below if your teammate's API differs.
   ========================================================= */

const CONFIG = {
  API_BASE: "http://localhost:8000",   // backend address
  AUDIT_ENDPOINT: "/api/audit",        // POST: multipart/form-data
  ZIP_FIELD: "file",                   // form field name for the zip
  REPO_FIELD: "repo_url",              // form field name for the GitHub link
  TIMEOUT_MS: 10 * 60 * 1000,          // audits can be slow
};

/*
  Expected JSON response (see schemas.py). Missing fields are handled safely.
  {
    "project_name": "sample_project",
    "baseline_passed": true,
    "duration_seconds": 14.2,
    "ai_summary": "text from the local model",
    "mutants": [
      {
        "id": 1, "file": "calculator.py", "line": 3,
        "operator": "Arithmetic swap",
        "original": "return a + b", "mutated": "return a - b",
        "status": "survived" | "killed" | "error",
        "suggestion": "Add a test that checks add(2, 3) == 5"
      }
    ]
  }
*/

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
  const form = new FormData();

  if (state.mode === "zip") {
    if (!state.file) return showError("Choose a project .zip file first.");
    form.append(CONFIG.ZIP_FIELD, state.file);
  } else {
    const url = els.repoUrl.value.trim();
    if (!/^https:\/\/github\.com\/[^/\s]+\/[^/\s]+/i.test(url)) {
      return showError("Enter a GitHub link like https://github.com/name/project");
    }
    form.append(CONFIG.REPO_FIELD, url);
  }

  showProgress();
  startSteps();

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), CONFIG.TIMEOUT_MS);

  try {
    const res = await fetch(CONFIG.API_BASE + CONFIG.AUDIT_ENDPOINT, {
      method: "POST", body: form, signal: controller.signal,
    });
    if (!res.ok) {
      let detail = "";
      try { const j = await res.json(); detail = j.detail || j.error || ""; } catch (_) {}
      throw new Error(detail || `Server returned ${res.status}`);
    }
    const data = await res.json();
    finishSteps();
    setTimeout(() => renderReport(data, false), 450);
  } catch (err) {
    stopSteps();
    hideProgress();
    const offline = err.name === "TypeError";
    showError(
      err.name === "AbortError" ? "The audit took too long and was stopped. Try a smaller project."
      : offline ? "Cannot reach the backend. Start it, check CONFIG.API_BASE in app.js, or click “See a sample report”."
      : `Audit failed: ${err.message}`
    );
  } finally {
    clearTimeout(timer);
  }
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

function normalise(raw) {
  const mutants = (raw.mutants || []).map((m, i) => ({
    id: m.id ?? i + 1,
    file: m.file || "unknown file",
    line: m.line ?? "?",
    operator: m.operator || m.type || "Code change",
    original: m.original ?? "",
    mutated: m.mutated ?? "",
    status: ["killed", "survived", "error"].includes(m.status) ? m.status : "error",
    suggestion: m.suggestion || "",
  }));
  const killed = mutants.filter((m) => m.status === "killed").length;
  const survived = mutants.filter((m) => m.status === "survived").length;
  const errors = mutants.filter((m) => m.status === "error").length;
  const judged = killed + survived;
  const score = typeof raw.score === "number"
    ? Math.round(raw.score)
    : judged ? Math.round((killed / judged) * 100) : 0;
  return {
    project: raw.project_name || "",
    duration: raw.duration_seconds != null ? Number(raw.duration_seconds).toFixed(1) : "",
    aiSummary: raw.ai_summary || "",
    baselineFailed: raw.baseline_passed === false,
    mutants,
    counts: { killed, survived, errors, total: mutants.length, score },
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
  els.repoUrl.value = "";
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
