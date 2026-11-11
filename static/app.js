"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
));
const fmt = (n) => Number(n || 0).toLocaleString("ru-RU");

const state = { upload: null, textOk: false };

async function api(url, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.method = init.method || "POST";
    init.headers = { "Content-Type": "application/json; charset=utf-8" };
    init.body = JSON.stringify(opts.json);
  }
  const resp = await fetch(url, init);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || `Ошибка ${resp.status}`);
  return data;
}

// ---------- конфиг ----------
api("/api/config").then((c) => {
  $("cfg").textContent = `${c.push_base_url} · режим ${c.push_mode} · потоков ${c.concurrency}`;
}).catch(() => {});

// ---------- текст ----------
const msg = $("message");

document.querySelectorAll(".letter").forEach((btn) => {
  btn.addEventListener("click", () => {
    const ch = btn.dataset.ch;
    const { selectionStart: s, selectionEnd: e, value } = msg;
    msg.value = value.slice(0, s) + ch + value.slice(e);
    msg.selectionStart = msg.selectionEnd = s + ch.length;
    msg.focus();
    scheduleCheck();
  });
});

let checkTimer = null;
function scheduleCheck() {
  clearTimeout(checkTimer);
  checkTimer = setTimeout(checkText, 300);
}
msg.addEventListener("input", scheduleCheck);

async function checkText() {
  const text = msg.value;
  if (!text.trim()) {
    $("textStats").textContent = "0 символов";
    $("textWarnings").innerHTML = "";
    $("fixBtn").classList.add("hidden");
    state.textOk = false;
    updateStart();
    return;
  }
  try {
    const r = await api("/api/text/check", { json: { message: text } });
    $("textStats").textContent =
      `${fmt(r.length)} символов · ${fmt(r.bytes_utf8)} байт UTF-8` +
      (r.has_kyrgyz ? " · есть кыргызские буквы ✓" : "");
    $("textWarnings").innerHTML =
      r.errors.map((w) => `<li class="error">${esc(w)}</li>`).join("") +
      r.warnings.map((w) => `<li>${esc(w)}</li>`).join("");
    $("fixBtn").classList.toggle("hidden", !r.fixable);
    state.textOk = r.errors.length === 0;
  } catch (e) {
    $("textWarnings").innerHTML = `<li class="error">${esc(e.message)}</li>`;
    state.textOk = false;
  }
  updateStart();
}

$("fixBtn").addEventListener("click", async () => {
  try {
    const r = await api("/api/text/fix", { json: { message: msg.value } });
    msg.value = r.message;
    if (r.changes.length) alert("Исправлено:\n" + r.changes.join("\n"));
    checkText();
  } catch (e) { alert(e.message); }
});

// ---------- файл ----------
const drop = $("drop");
const fileInput = $("file");
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => {
  e.preventDefault(); drop.classList.add("over");
}));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => {
  e.preventDefault(); drop.classList.remove("over");
}));
drop.addEventListener("drop", (e) => {
  if (e.dataTransfer.files.length) uploadFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener("change", () => {
  if (fileInput.files.length) uploadFile(fileInput.files[0]);
  fileInput.value = "";
});

async function uploadFile(file) {
  const info = $("uploadInfo");
  info.classList.remove("hidden");
  info.innerHTML = `Загрузка и проверка «${esc(file.name)}»…`;
  state.upload = null;
  updateStart();
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await api("/api/uploads", { method: "POST", body: fd });
    state.upload = r;
    let html =
      `<div>Файл: <b>${esc(r.source)}</b> <span class="muted">(${esc(r.encoding)}, колонка: ${esc(r.column)}` +
      (r.sheets > 1 ? `, листов: ${r.sheets}` : "") + `)</span></div>` +
      `<div>Получателей: <b class="ok">${fmt(r.count)}</b>` +
      ` · строк в файле: ${fmt(r.rows)} · дубликатов: ${fmt(r.duplicates)}` +
      ` · некорректных: <span class="${r.invalid ? "err" : ""}">${fmt(r.invalid)}</span></div>` +
      `<div class="muted">Пример: ${r.sample.map(esc).join(", ")}</div>`;
    if (r.invalid_samples.length) {
      html += `<details><summary class="muted">Некорректные строки (первые ${r.invalid_samples.length})</summary><ul>` +
        r.invalid_samples.map((s) =>
          `<li>${s.sheet ? esc(s.sheet) + ", " : ""}строка ${s.row}: «${esc(s.value)}»</li>`).join("") +
        `</ul></details>`;
    }
    info.innerHTML = html;
  } catch (e) {
    info.innerHTML = `<span class="err">${esc(e.message)}</span>`;
  }
  updateStart();
}

// ---------- тест и запуск ----------
function updateStart() {
  const ready = state.textOk && state.upload;
  $("startBtn").disabled = !ready;
  $("startHint").textContent = ready
    ? `Будет отправлено ${fmt(state.upload.count)} уведомлений.`
    : "Введите текст и загрузите файл с получателями.";
}

$("testBtn").addEventListener("click", async () => {
  const out = $("testResult");
  out.textContent = "Отправка…";
  out.className = "muted";
  try {
    const r = await api("/api/test-send", { json: { login: $("testLogin").value, message: msg.value } });
    out.textContent = r.status === "ok" ? `✓ Отправлено на ${r.login}` : `✗ ${r.status}: ${r.detail}`;
    out.className = r.status === "ok" ? "ok" : "err";
  } catch (e) {
    out.textContent = e.message;
    out.className = "err";
  }
});

$("startBtn").addEventListener("click", async () => {
  if (!state.upload) return;
  const preview = msg.value.trim().slice(0, 200);
  if (!confirm(`Отправить уведомление ${fmt(state.upload.count)} получателям?\n\n«${preview}${msg.value.length > 200 ? "…" : ""}»`)) return;
  $("startBtn").disabled = true;
  try {
    const job = await api("/api/jobs", {
      json: { upload_id: state.upload.upload_id, message: msg.value, name: $("jobName").value, start: true },
    });
    if (job.last_error) alert(job.last_error);
    state.upload = null;
    $("uploadInfo").classList.add("hidden");
    $("jobName").value = "";
    loadJobs();
  } catch (e) {
    alert(e.message);
  }
  updateStart();
});

// ---------- список рассылок ----------
const STATUS = {
  ready: "готова", running: "идёт", pausing: "ставится на паузу", paused: "на паузе",
  cancelling: "отменяется", cancelled: "отменена", done: "завершена",
};

function jobHtml(j) {
  const pct = (n) => (j.total ? (n / j.total) * 100 : 0);
  const left = j.total - j.processed;
  const a = [];
  if (j.status === "ready" || j.status === "paused")
    a.push(`<button class="small" data-act="start">${j.status === "paused" ? "Продолжить" : "Запустить"}</button>`);
  if (j.status === "running") a.push(`<button class="small secondary" data-act="pause">Пауза</button>`);
  if (["ready", "paused", "running"].includes(j.status))
    a.push(`<button class="small danger" data-act="cancel">Отменить</button>`);
  if (j.processed) a.push(`<a href="/api/jobs/${j.id}/report"><button class="small secondary">Отчёт CSV</button></a>`);
  if (["done", "cancelled", "paused"].includes(j.status) && j.error)
    a.push(`<button class="small secondary" data-act="retry">Повторить ошибки (${fmt(j.error)})</button>`);
  if (["done", "cancelled", "paused"].includes(j.status) && j.unknown)
    a.push(`<button class="small secondary" data-act="retry-unknown">Повторить ошибки + без ответа</button>`);

  return `<div class="job" data-id="${esc(j.id)}">
    <div class="job-head">
      <span class="job-title">${esc(j.name)}</span>
      <span class="badge ${["running", "pausing", "cancelling"].includes(j.status) ? "running" : esc(j.status)}">${esc(STATUS[j.status] || j.status)}</span>
    </div>
    <div class="job-msg">${esc(j.message)}</div>
    <div class="bar">
      <span class="b-ok" style="width:${pct(j.ok)}%"></span>
      <span class="b-err" style="width:${pct(j.error)}%"></span>
      <span class="b-unk" style="width:${pct(j.unknown)}%"></span>
    </div>
    <div class="stats">
      <span>${fmt(j.processed)} / ${fmt(j.total)} (${pct(j.processed).toFixed(1)}%)</span>
      <span class="ok">✓ ${fmt(j.ok)}</span>
      <span class="err">✗ ${fmt(j.error)}</span>
      <span class="unk" title="Нет ответа от сервиса — сообщение могло дойти">? ${fmt(j.unknown)}</span>
      ${left > 0 ? `<span class="muted">осталось ${fmt(left)}</span>` : ""}
      <span class="muted">создана ${esc(j.created_at)}${j.finished_at ? " · завершена " + esc(j.finished_at) : ""}</span>
    </div>
    ${j.last_error ? `<div class="err" style="font-size:13px">${esc(j.last_error)}</div>` : ""}
    <div class="actions">${a.join("")}</div>
  </div>`;
}

async function loadJobs() {
  try {
    const jobs = await api("/api/jobs");
    $("jobs").innerHTML = jobs.length ? jobs.map(jobHtml).join("") : `<p class="muted">Пока нет рассылок.</p>`;
    const active = jobs.some((j) => ["running", "pausing", "cancelling"].includes(j.status));
    clearTimeout(loadJobs.t);
    loadJobs.t = setTimeout(loadJobs, active ? 1500 : 8000);
  } catch {
    clearTimeout(loadJobs.t);
    loadJobs.t = setTimeout(loadJobs, 5000);
  }
}

$("jobs").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const id = btn.closest(".job").dataset.id;
  const act = btn.dataset.act;
  try {
    if (act === "cancel" && !confirm("Отменить рассылку? Продолжить её потом будет нельзя.")) return;
    if (act === "retry" || act === "retry-unknown") {
      if (act === "retry-unknown" && !confirm(
        "«Без ответа» — сообщения, на которые сервис не ответил. Часть из них могла дойти, " +
        "эти абоненты получат уведомление повторно. Продолжить?")) return;
      const job = await api(`/api/jobs/${id}/retry`, { json: { include_unknown: act === "retry-unknown" } });
      if (confirm(`Создана рассылка на ${fmt(job.total)} логинов. Запустить сейчас?`))
        await api(`/api/jobs/${job.id}/start`, { method: "POST" });
    } else {
      await api(`/api/jobs/${id}/${act}`, { method: "POST" });
    }
  } catch (err) { alert(err.message); }
  loadJobs();
});

loadJobs();
