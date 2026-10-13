"use strict";
const $ = (id) => document.getElementById(id),
  token = location.hash.slice(1) || sessionStorage.getItem("editorToken") || "";
if (token) sessionStorage.setItem("editorToken", token);
history.replaceState(null, "", location.pathname);
const kinds = { reference: "Reference", comparison: "Comparison" },
  takeURLs = new Map(),
  installedTakeSignatures = new Map(),
  takeGenerations = new Map(),
  mimeChoices = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/webm", "audio/mp4", ""];
let data = null, active = null, busy = true, closed = false, referenceReady = false;

async function api(path, value) {
  const response = await fetch(path, {
    method: value === undefined ? "GET" : "POST",
    headers: { "X-Editor-Token": token, "Content-Type": "application/json" },
    body: value === undefined ? undefined : JSON.stringify(value),
  });
  const result = await response.json();
  if (!response.ok) throw Error(result.error || "Request failed");
  return result;
}
async function upload(kind, blob, delay) {
  if (!blob.size) throw Error("No audio was captured. Please record again.");
  if (blob.size > data.limits.bytes) throw Error("Recording exceeds the upload size limit.");
  const response = await fetch(`/upload/${kind}`, {
    method: "POST",
    headers: {
      "X-Editor-Token": token,
      "Content-Type": blob.type || "application/octet-stream",
      "X-Playback-Delay": String(delay),
    },
    body: blob,
  });
  const result = await response.json();
  if (!response.ok) throw Error(result.error || "Upload failed");
  return result;
}
function clearTake(kind) {
  // Clearing a take also invalidates any listening copy still in flight.
  takeGenerations.set(kind, (takeGenerations.get(kind) || 0) + 1);
  const audio = $(`${kind}-audio`);
  audio.pause();
  audio.removeAttribute("src");
  audio.load();
  if (takeURLs.has(kind)) URL.revokeObjectURL(takeURLs.get(kind));
  takeURLs.delete(kind);
  installedTakeSignatures.delete(kind);
  if (kind === "reference") referenceReady = false;
}
async function fetchTake(kind) {
  clearTake(kind);
  const generation = takeGenerations.get(kind), signature = JSON.stringify(data.takes[kind]);
  const response = await fetch(`/take/${kind}`, { headers: { "X-Editor-Token": token } });
  if (!response.ok) {
    const error = await response.json();
    throw Error(error.error || "Could not load the listening copy.");
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  if (closed || generation !== takeGenerations.get(kind) || signature !== JSON.stringify(data.takes[kind])) {
    URL.revokeObjectURL(url);
    return;
  }
  if (takeURLs.has(kind)) URL.revokeObjectURL(takeURLs.get(kind));
  takeURLs.set(kind, url);
  $(`${kind}-audio`).src = url;
  installedTakeSignatures.set(kind, signature);
  $(`${kind}-audio`).load();
}
function text(tag, value, className) {
  const node = document.createElement(tag);
  node.textContent = value;
  if (className) node.className = className;
  return node;
}
function duration(seconds, decimals = 0) {
  const factor = 10 ** decimals, rounded = Math.max(0, Math.floor(seconds * factor) / factor);
  const remainder = (rounded % 60).toFixed(decimals).padStart(decimals ? decimals + 3 : 2, "0");
  return `${Math.floor(rounded / 60)}:${remainder}`;
}
function signed(value, decimals = 0) {
  return `${value >= 0 ? "+" : ""}${Number(value).toFixed(decimals)}`;
}
function intervalLabel(interval) {
  return interval.name + (interval.direction === "unison" ? "" : ` ${interval.direction}`);
}
function status(kind, message) { $(`${kind}-status`).textContent = message; }
function captureSupported() { return !!(navigator.mediaDevices?.getUserMedia && window.MediaRecorder); }
function updateControls() {
  const blocked = !data || closed || busy || !!active;
  for (const kind of Object.keys(kinds)) {
    $(`${kind}-record`).disabled = blocked || !captureSupported() ||
      (kind === "comparison" && !referenceReady);
    $(`${kind}-choose`).disabled = blocked;
    $(`${kind}-stop`).disabled = closed || busy || active?.kind !== kind || active.stopping;
    $(`${kind}-audio`).controls = !active && !closed;
  }
  $("playback-retry").disabled = blocked || !referenceReady;
  $("analyze").disabled = blocked || !data?.takes.reference || !data?.takes.comparison;
  $("alignment-controls").disabled = blocked;
  $("offset-seconds").disabled = blocked || !$("alignment-manual").checked;
  $("save").disabled = blocked || !(data?.loaded_session || data?.analysis);
  $("save").textContent = data?.loaded_session ? "Update notes" : "Save";
  $("reveal").disabled = blocked || !data?.loaded_session;
  $("session-title").disabled = $("session-notes").disabled = blocked;
  $("close").disabled = !data || closed || busy;
  for (const button of $("sessions").querySelectorAll("button")) button.disabled = blocked;
}
function renderAnalysis() {
  const result = data.analysis;
  $("analysis-result").hidden = !result;
  if (!result) return;
  const answer = result.interval, alignment = result.alignment;
  $("result-interval").textContent = answer ? intervalLabel(answer) : "Not enough overlapping singing";
  const mode = { auto: "automatic", manual: "manual", hint: "playback hint" }[alignment.mode];
  const detail = answer ? [
    `${answer.semitones} semitones`,
    `agreement ${Math.round(answer.agreement * 100)} %`,
    `median ${signed(answer.cents_median)} cents`,
  ] : [`${result.overlap_seconds.toFixed(2)} s overlapping singing`];
  detail.push(`alignment ${signed(alignment.offset_seconds, 2)} s (${mode}, confidence ${alignment.confidence.toFixed(2)})`);
  $("result-detail").textContent = detail.join(" · ");
  const simple = result.simple_interval;
  $("result-simple").textContent = simple && answer &&
    (simple.semitones !== answer.semitones || simple.agreement !== answer.agreement)
    ? `Ignoring octaves: ${intervalLabel(simple)} (${Math.round(simple.agreement * 100)} %)` : "";
  $("analysis-warnings").replaceChildren(...result.warnings.map((warning) => text("li", warning)));
  IntervalCharts.renderHistogram($("histogram"), result.histogram);
  IntervalCharts.renderTimeline($("timeline"), result);
  $("segments").replaceChildren(...result.segments.map((segment) =>
    text("li", `${duration(segment.start, 2)}–${duration(segment.end, 2)} · ${intervalLabel(segment)}`)));
  if (!result.segments.length) $("segments").append(text("li", "No sustained interval segments."));
  $("analysis-method").textContent = result.method;
}
function renderSessions() {
  $("sessions").replaceChildren();
  for (const session of data.sessions) {
    const row = document.createElement("li");
    if (session.error) {
      row.append(text("span", `${session.id}: ${session.error}`, "note"));
    } else {
      const description = document.createElement("div");
      description.append(text("strong", session.title || "Untitled"));
      const info = [new Date(session.created).toLocaleString(), duration(session.duration)];
      if (session.interval) info.push(`${session.interval.short} ${session.interval.direction}`);
      description.append(text("div", info.join(" · "), "note"));
      const load = text("button", "Load");
      load.addEventListener("click", () => mutate("save", "/load", { id: session.id }));
      row.append(description, load);
    }
    $("sessions").append(row);
  }
  if (!data.sessions.length) $("sessions").append(text("li", "No saved sessions.", "note"));
}
async function applySnapshot(snapshot, populate = false) {
  const previous = data, playbackErrors = [];
  const changedKinds = snapshot.kinds.filter((kind) =>
    JSON.stringify(previous?.takes[kind]) !== JSON.stringify(snapshot.takes[kind]));
  data = snapshot;
  // Re-analysis detaches the saved result, but unchanged takes keep the user's metadata.
  const loadedChanged = data.loaded_session && previous?.loaded_session?.id !== data.loaded_session.id;
  if (populate || changedKinds.length || loadedChanged) {
    $("session-title").value = data.loaded_session?.title || "";
    $("session-notes").value = data.loaded_session?.notes || "";
  }
  $("session-title").maxLength = data.limits.title;
  $("session-notes").maxLength = data.limits.notes;
  $("alignment-range").textContent = `±${data.limits.max_offset} s`;
  if (data.analysis && JSON.stringify(data.analysis) !== JSON.stringify(previous?.analysis) && data.analysis.alignment.mode !== "manual") {
    $("offset-seconds").value = data.analysis.alignment.offset_seconds.toFixed(2);
  }
  if ((populate || loadedChanged) && data.loaded_session && data.analysis) {
    $("alignment-manual").checked = data.analysis.alignment.mode === "manual";
    $("alignment-auto").checked = !$("alignment-manual").checked;
    $("offset-seconds").value = data.analysis.alignment.offset_seconds.toFixed(2);
  }
  renderAnalysis();
  renderSessions();
  const playback = data.kinds.map((kind) => {
    const take = data.takes[kind];
    $(`${kind}-duration`).textContent = take
      ? `${duration(take.duration)} · ${(take.size_bytes / 1048576).toFixed(1)} MiB · audio track ${take.audio_track}`
      : `No ${kind} take yet.`;
    if (!active) $(`${kind}-clock`).textContent = `0:00 elapsed · ${duration(data.limits.seconds - 2)} remaining`;
    if (!take) { clearTake(kind); return; }
    // A snapshot can outlive a failed fetch; only installed listening copies count.
    if (installedTakeSignatures.get(kind) === JSON.stringify(take)) return;
    return fetchTake(kind).catch((error) => {
      status(kind, error.message);
      playbackErrors.push(error.message);
    });
  });
  updateControls();
  await Promise.all(playback);
  if (playbackErrors.length) throw Error(playbackErrors.join("\n"));
}
async function refresh() {
  await applySnapshot(await api("/state"));
}
async function mutationSnapshot(request) {
  let result;
  try { result = await request; }
  catch (error) {
    // A lost response does not tell us whether the server committed the mutation.
    await refresh().catch(() => {});
    throw error;
  }
  return result?.takes ? result : api("/state");
}
async function mutate(panel, route, value) {
  if (busy || active || closed) return;
  busy = true;
  updateControls();
  status(panel, route === "/analyze" ? "Analyzing pitch and alignment…" : "Working…");
  try {
    const snapshot = await mutationSnapshot(api(route, value));
    await applySnapshot(snapshot, ["/load", "/save", "/update"].includes(route));
    status(panel, route === "/analyze" ? "Analysis complete." : "Done.");
  } catch (error) {
    status(panel, error.message);
  } finally {
    busy = false;
    updateControls();
  }
}

function captureTimer(capture, callback, milliseconds) {
  const timer = setTimeout(() => {
    capture.timers.delete(timer);
    if (!capture.discarded) callback();
  }, milliseconds);
  capture.timers.add(timer);
  return timer;
}
function waitCapture(capture, milliseconds) {
  return new Promise((resolve, reject) => {
    const abort = () => reject(new DOMException("Recording cancelled", "AbortError"));
    capture.controller.signal.addEventListener("abort", abort, { once: true });
    captureTimer(capture, () => {
      capture.controller.signal.removeEventListener("abort", abort);
      resolve();
    }, milliseconds);
  });
}
function waitEvent(capture, target, name, stamp) {
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      target.removeEventListener(name, ready);
      capture.controller.signal.removeEventListener("abort", abort);
      clearTimeout(timer);
      capture.timers.delete(timer);
    };
    const ready = () => { if (stamp) stamp(); cleanup(); resolve(); };
    const abort = () => { cleanup(); reject(new DOMException("Recording cancelled", "AbortError")); };
    const timer = captureTimer(capture, () => {
      cleanup();
      reject(Error(`Timed out waiting for ${name}. Please retry.`));
    }, 10000);
    target.addEventListener(name, ready, { once: true });
    capture.controller.signal.addEventListener("abort", abort, { once: true });
  });
}
function releaseCapture(capture) {
  capture.controller.abort();
  for (const timer of capture.timers) clearTimeout(timer);
  capture.timers.clear();
  clearInterval(capture.clockTimer);
  cancelAnimationFrame(capture.levelFrame);
  capture.stream?.getTracks().forEach((track) => track.stop());
  capture.source?.disconnect();
  if (capture.context) capture.context.close().catch(() => {});
  $(`${capture.kind}-level`).value = 0;
  if (capture.recorder) {
    capture.recorder.ondataavailable = capture.recorder.onstop = capture.recorder.onerror = null;
    if (capture.recorder.state !== "inactive") {
      try { capture.recorder.stop(); } catch (_) { /* Tracks have already been released. */ }
    }
  }
  if (capture.kind === "comparison") $("reference-audio").pause();
}
function abortComparison(error, capture = active) {
  if (capture?.kind === "comparison") abortCapture(capture, error);
  else $("reference-audio").pause();
  if (error?.name === "NotAllowedError") $("playback-retry").hidden = false;
}
function abortCapture(capture, error) {
  capture.discarded = true;
  releaseCapture(capture);
  capture.chunks.length = 0;
  if (active === capture) active = null;
  status(capture.kind, error?.message || "Recording cancelled.");
  updateControls();
}
function failCapture(capture, error) {
  if (capture.discarded || active !== capture) return;
  if (capture.kind === "comparison") abortComparison(error, capture);
  else abortCapture(capture, error);
}
function newCapture(kind) {
  for (const key of Object.keys(kinds)) $(`${key}-audio`).pause();
  const capture = {
    kind, controller: new AbortController(), timers: new Set(), chunks: [],
    bytes: 0, discarded: false, stopping: false, recorderStart: null, playbackStart: null,
    delay: 0, budget: Math.max(0, data.limits.seconds - 2),
  };
  active = capture;
  $("playback-retry").hidden = true;
  status(kind, "Requesting microphone access…");
  updateControls();
  return capture;
}
async function microphone(capture) {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: {
    echoCancellation: false, noiseSuppression: false, autoGainControl: false,
  } });
  if (capture.discarded || active !== capture) {
    stream.getTracks().forEach((track) => track.stop());
    throw new DOMException("Recording cancelled", "AbortError");
  }
  capture.stream = stream;
  for (const track of stream.getTracks()) {
    track.addEventListener("ended", () => failCapture(capture, Error("Microphone disconnected. Record again.")));
  }
  startLevel(capture);
}
function startLevel(capture) {
  const Context = window.AudioContext || window.webkitAudioContext;
  if (!Context) return;
  try {
    capture.context = new Context();
    const analyser = capture.context.createAnalyser();
    analyser.fftSize = 2048;
    capture.source = capture.context.createMediaStreamSource(capture.stream);
    capture.source.connect(analyser);
    capture.context.resume().catch(() => {});
    const samples = new Float32Array(analyser.fftSize);
    const draw = () => {
      analyser.getFloatTimeDomainData(samples);
      const rms = Math.sqrt(samples.reduce((sum, sample) => sum + sample * sample, 0) / samples.length);
      $(`${capture.kind}-level`).value = Math.min(1, rms);
      capture.levelFrame = requestAnimationFrame(draw);
    };
    draw();
  } catch (_) {
    status(capture.kind, "Level meter unavailable; recording can continue.");
  }
}
function createRecorder(capture) {
  let lastError;
  for (const mimeType of mimeChoices) {
    if (mimeType && !MediaRecorder.isTypeSupported(mimeType)) continue;
    try { return new MediaRecorder(capture.stream, mimeType ? { mimeType } : {}); }
    catch (error) { lastError = error; }
  }
  throw lastError || Error("This browser cannot record audio. Choose an audio file instead.");
}
function captureClock(capture) {
  const elapsed = (performance.now() - capture.recorderStart) / 1000;
  $(`${capture.kind}-clock`).textContent =
    `${duration(elapsed)} elapsed · ${duration(Math.max(0, capture.budget - elapsed))} remaining`;
}
async function beginRecorder(capture) {
  const recorder = capture.recorder = createRecorder(capture);
  recorder.ondataavailable = (event) => {
    if (capture.discarded || !event.data.size) return;
    capture.bytes += event.data.size;
    if (capture.bytes > data.limits.bytes) {
      failCapture(capture, Error("Recording reached the size limit and was discarded. Try a shorter take."));
      return;
    }
    capture.chunks.push(event.data);
  };
  recorder.onerror = (event) => failCapture(capture, event.error || Error("Recording failed. Please retry."));
  recorder.onstop = () => finishCapture(capture);
  const started = waitEvent(capture, recorder, "start", () => {
    capture.recorderStart = performance.now();
    capture.clockTimer = setInterval(() => captureClock(capture), 250);
    captureClock(capture);
    captureTimer(capture, () => stopCapture(capture), capture.budget * 1000);
  });
  try { recorder.start(250); }
  catch (error) { failCapture(capture, error); }
  await started;
  if (capture.discarded) throw new DOMException("Recording cancelled", "AbortError");
  status(capture.kind, "Recording…");
}
async function startReference() {
  if (busy || active || closed || !data) return;
  const capture = newCapture("reference");
  try {
    await microphone(capture);
    await beginRecorder(capture);
  } catch (error) { failCapture(capture, error); }
}
function startComparison() {
  if (busy || active || closed || !referenceReady) return;
  const reference = $("reference-audio"), capture = newCapture("comparison");
  try {
    // Safari grants playback to this element only inside the initiating gesture.
    reference.pause();
    reference.currentTime = 0;
    const unlocked = reference.play();
    reference.pause();
    Promise.resolve(unlocked).catch((error) => {
      if (error.name !== "AbortError") failCapture(capture, error);
    });
    comparisonFlow(capture).catch((error) => failCapture(capture, error));
  } catch (error) { abortComparison(error, capture); }
}
async function comparisonFlow(capture) {
  await microphone(capture);
  for (let count = 3; count > 0; count--) {
    status("comparison", `Starting in ${count}…`);
    await waitCapture(capture, 1000);
  }
  const reference = $("reference-audio");
  reference.currentTime = 0;
  await beginRecorder(capture);
  const playing = waitEvent(capture, reference, "playing", () => {
    capture.playbackStart = performance.now();
    capture.delay = Math.max(0, capture.playbackStart - capture.recorderStart) / 1000;
  });
  await Promise.all([playing, Promise.resolve().then(() => reference.play())]);
  if (capture.delay > 5) throw Error("Playback took more than 5 seconds to start. Please retry.");
  status("comparison", `Recording… Playback-delay hint ${capture.delay.toFixed(2)} s.`);
}
function stopCapture(capture = active) {
  if (!capture || capture.discarded || capture.stopping) return;
  if (capture.recorder?.state !== "recording" ||
      (capture.kind === "comparison" && capture.playbackStart === null)) {
    failCapture(capture, Error("Recording cancelled."));
    return;
  }
  capture.stopping = true;
  status(capture.kind, "Finishing recording…");
  if (capture.kind === "comparison") $("reference-audio").pause();
  updateControls();
  try { capture.recorder.stop(); }
  catch (error) { failCapture(capture, error); }
}
async function finishCapture(capture) {
  if (capture.discarded || active !== capture) return;
  capture.stopping = true;
  const blob = new Blob(capture.chunks, { type: capture.recorder.mimeType });
  releaseCapture(capture);
  busy = true;
  updateControls();
  status(capture.kind, "Preparing the listening copy…");
  try {
    await applySnapshot(await mutationSnapshot(upload(capture.kind, blob, capture.delay)));
    status(capture.kind, "Take ready.");
  } catch (error) {
    failCapture(capture, error);
  } finally {
    capture.chunks.length = 0;
    if (active === capture) active = null;
    busy = false;
    updateControls();
  }
}
function analyze() {
  const manual = $("alignment-manual").checked;
  if (manual && (!$("offset-seconds").value || !$("offset-seconds").checkValidity())) {
    status("analysis", "Enter a manual offset between −5 and +5 seconds.");
    return;
  }
  const value = { alignment: manual ? "manual" : "auto" };
  if (manual) value.offset_seconds = Number($("offset-seconds").value);
  mutate("analysis", "/analyze", value);
}
function save() {
  const value = { title: $("session-title").value, notes: $("session-notes").value };
  if (data.loaded_session) value.id = data.loaded_session.id;
  mutate("save", data.loaded_session ? "/update" : "/save", value);
}
async function closeSession() {
  if (busy || closed) return;
  if (active) failCapture(active, Error("Recording discarded while closing the session."));
  busy = true;
  updateControls();
  try {
    await api("/close", {});
    closed = true;
    for (const kind of Object.keys(kinds)) clearTake(kind);
    status("session", "Session closed. You can close this tab.");
  } catch (error) { status("session", error.message); }
  finally { busy = false; updateControls(); }
}
function bindControls() {
  $("reference-record").addEventListener("click", startReference);
  $("comparison-record").addEventListener("click", startComparison);
  $("playback-retry").addEventListener("click", startComparison);
  for (const kind of Object.keys(kinds)) {
    $(`${kind}-stop`).addEventListener("click", () => stopCapture());
    $(`${kind}-choose`).addEventListener("click", () => mutate(kind, "/choose", { kind }));
    $(`${kind}-audio`).addEventListener("error", () => {
      const error = Error(`${kinds[kind]} playback failed. Choose the file again or reload the saved session.`);
      if (kind === "reference") {
        referenceReady = false;
        if (active?.kind === "comparison") abortComparison(error);
      }
      status(kind, error.message);
      updateControls();
    });
    $(`${kind}-audio`).addEventListener("play", () => {
      if (active && (active.kind !== "comparison" || kind !== "reference")) $(`${kind}-audio`).pause();
    });
  }
  $("reference-audio").addEventListener("canplaythrough", () => {
    referenceReady = !!takeURLs.get("reference");
    updateControls();
  });
  $("reference-audio").addEventListener("ended", () => {
    if (active?.kind === "comparison" && !active.stopping) {
      const capture = active;
      captureTimer(capture, () => stopCapture(capture), 1000);
    }
  });
  for (const id of ["alignment-auto", "alignment-manual"]) $(id).addEventListener("change", updateControls);
  $("analyze").addEventListener("click", analyze);
  $("save").addEventListener("click", save);
  $("reveal").addEventListener("click", () => mutate("save", "/reveal", { id: data.loaded_session.id }));
  $("close").addEventListener("click", closeSession);
  window.addEventListener("pagehide", () => {
    closed = true;
    if (active) failCapture(active, Error("Recording discarded."));
    for (const kind of Object.keys(kinds)) clearTake(kind);
  });
}
bindControls();
refresh().then(() => {
  status("session", "Unsaved takes are temporary. Save a session before closing.");
  if (!captureSupported()) {
    for (const kind of Object.keys(kinds)) status(kind, "Microphone recording is unavailable. Choose a file instead.");
  }
}).catch((error) => status("session", error.message)).finally(() => {
  busy = false;
  updateControls();
});
