const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const root = require('node:path').resolve(__dirname, '../scripts');
const html = fs.readFileSync(root + '/interval_analysis.html', 'utf8');
const nodes = new Map(), requests = [], recorderCalls = [], events = [], revoked = [];
const createdURLs = new Map(), pendingTakes = new Map();
let cancelChoose = false, nextFailure = null, noAnswer = false, nextUploadReply = null;
let server, failMic = false, pendingMic = null, rejectPlayAt = 0, playCount = 0;
let nextURL = 0, nextTake = 0, streams = [], lastRecorder;
class Element extends EventTarget {
  constructor(tag = 'div') { super(); this.tag = tag; this.children = []; this.attrs = {}; this.value = ''; this.hidden = false; this.textContent = ''; this.disabled = false; this.currentTime = 0; }
  get src() { return this.source; }
  set src(value) { (this.sources ||= []).push(value); this.source = value; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  get childNodes() { return this.children; }
  setAttribute(key, value) { this.attrs[key] = String(value); }
  removeAttribute(key) { delete this.attrs[key]; if (key === 'src') this.src = ''; }
  querySelectorAll(tag) { return this.children.flatMap(node => node instanceof Element ? [...(node.tag === tag ? [node] : []), ...node.querySelectorAll(tag)] : []); }
  checkValidity() { return Number.isFinite(Number(this.value)) && Math.abs(Number(this.value)) <= 5; }
  load() { if (this.src) queueMicrotask(() => this.dispatchEvent(new Event('canplaythrough'))); }
  pause() { events.push('pause'); }
  play() {
    events.push('play'); playCount++;
    if (playCount === rejectPlayAt) return Promise.reject(new DOMException('Playback needs a gesture', 'NotAllowedError'));
    queueMicrotask(() => this.dispatchEvent(new Event('playing')));
    return Promise.resolve();
  }
}
for (const match of html.matchAll(/id="([^"]+)"/g)) nodes.set(match[1], new Element(match[1].endsWith('-audio') ? 'audio' : 'div'));
nodes.get('alignment-auto').checked = true;
nodes.get('offset-seconds').value = '0';
const makeStream = () => {
  const track = new EventTarget(); track.stopped = false; track.stop = () => { track.stopped = true; };
  const stream = { getTracks: () => [track] }; streams.push(stream); return stream;
};
class Recorder {
  static isTypeSupported(type) { return type === 'audio/mp4'; }
  constructor(stream, options) { this.stream = stream; this.mimeType = options.mimeType; this.state = 'inactive'; this.target = new EventTarget(); lastRecorder = this; }
  addEventListener(...args) { this.target.addEventListener(...args); }
  removeEventListener(...args) { this.target.removeEventListener(...args); }
  start(milliseconds) { recorderCalls.push(milliseconds); this.state = 'recording'; queueMicrotask(() => this.target.dispatchEvent(new Event('start'))); }
  stop() { this.state = 'inactive'; queueMicrotask(() => { this.ondataavailable?.({ data: new Blob(['synthetic data'], { type: this.mimeType }) }); this.onstop?.(); }); }
}
const recording = kind => ({ kind, file: `${kind}-${++nextTake}.m4a`, mime: 'audio/mp4', duration: 2, size_bytes: 14, playback_delay: 0, audio_track: 0 });
function initial() { return { title: 'Interval Analysis', takes: { reference: null, comparison: null }, analysis: null, loaded_session: null, sessions: [], limits: { bytes: 67108864, seconds: 600, title: 120, notes: 5000, max_offset: 0.75 }, kinds: ['reference', 'comparison'], alignment_modes: ['auto', 'manual', 'hint'] }; }
function delayTake(kind) {
  let release;
  pendingTakes.set(`/take/${kind}`, new Promise(resolve => { release = resolve; }));
  return release;
}
function analysisFixture({ alignment = 'auto', offset_seconds = 0.04 } = {}) {
  const third = { semitones: 4, name: 'Major third', short: 'M3', direction: 'above' };
  const compound = { semitones: 16, name: 'Octave + major third', short: 'P8+M3', direction: 'above' };
  const points = [
    { time: 0, reference_hz: 220, comparison_hz: 277.2, cents: 400.1, smoothed_cents: 399.5, semitones: 4 },
    { time: 0.5, reference_hz: null, comparison_hz: null, cents: null, smoothed_cents: null, semitones: null },
    { time: 1, reference_hz: 220, comparison_hz: 277.2, cents: 400.1, smoothed_cents: 399.5, semitones: 4 },
    { time: 1.5, reference_hz: 220, comparison_hz: 554.4, cents: 1600.1, smoothed_cents: 1600.1, semitones: 16 },
  ];
  return {
    version: 1,
    alignment: { mode: alignment, offset_seconds, hint_seconds: 0, confidence: 0.8, warning: null },
    overlap_seconds: noAnswer ? 0 : 1.4977,
    reference: { duration: 2, voiced_seconds: noAnswer ? 0 : 1.4977 },
    comparison: { duration: 2, voiced_seconds: noAnswer ? 0 : 1.4977 },
    interval: noAnswer ? null : { ...third, simple: 4, octaves: 0, agreement: 0.6667, cents_median: -0.5 },
    simple_interval: noAnswer ? null : { ...third, simple: 4, octaves: 0, agreement: 1 },
    histogram: noAnswer ? [] : [{ ...third, frames: 86, share: 0.6667 }, { ...compound, frames: 43, share: 0.3333 }],
    simple_histogram: noAnswer ? [] : [{ ...third, frames: 129, share: 1 }],
    segments: noAnswer ? [] : [{ ...third, start: 0, end: 0.5 }, { ...third, start: 1, end: 1.5 }, { ...compound, start: 1.5, end: 2 }],
    timeline: noAnswer ? points.map(point => ({ ...point, reference_hz: null, comparison_hz: null, cents: null, smoothed_cents: null, semitones: null })) : points,
    warnings: ['Less than 2 s of overlapping singing.'],
    method: 'pYIN fundamental frequency; voicing-landmark alignment; running median and hysteresis labels.',
    parameters: { sample_rate: 22050, hop: 256, frame_length: 2048, fmin: 65, fmax: 1100, stabilize_seconds: 0.25, hysteresis_cents: 60, min_label_seconds: 0.1, max_offset_seconds: 0.75 },
  };
}
async function fakeFetch(route, options = {}) {
  requests.push({ route, options }); assert.equal(options.headers['X-Editor-Token'], 'test-token');
  const failure = nextFailure?.route === route ? nextFailure : null;
  if (failure) nextFailure = null;
  if (failure && !failure.committed) {
    if (failure.status) return { ok: false, status: failure.status, json: async () => ({ error: 'Synthetic response failure' }) };
    throw Error('Synthetic response failure');
  }
  const value = typeof options.body === 'string' ? JSON.parse(options.body) : {};
  let result = server;
  if (route === '/choose' && !cancelChoose) {
    server.takes[value.kind] = recording(value.kind); server.analysis = server.loaded_session = null;
  }
  if (route.startsWith('/upload/')) {
    const kind = route.split('/')[2]; server.takes[kind] = recording(kind); server.analysis = server.loaded_session = null;
    result = nextUploadReply || server; nextUploadReply = null;
  }
  if (route === '/analyze') { server.analysis = analysisFixture(value); server.loaded_session = null; }
  if (route === '/load') {
    server.loaded_session = { id: value.id, title: 'Loaded title', notes: 'Loaded notes' };
    server.takes.reference = recording('reference'); server.takes.comparison = recording('comparison');
    server.analysis = analysisFixture({ alignment: 'manual', offset_seconds: 0.12 });
  }
  if (route === '/save' || route === '/update') {
    server.loaded_session = { id: value.id || 'saved', title: value.title, notes: value.notes };
    const summary = { ...server.loaded_session, created: '2026-10-13T00:00:00Z', duration: 2, interval: server.analysis.interval };
    server.sessions = [...server.sessions.filter(session => session.id !== summary.id), summary];
  }
  if (route === '/reveal') result = { revealed: true };
  if (route === '/close') result = { closed: true };
  if (failure) throw Error('Synthetic response failure');
  if (route.startsWith('/take/')) {
    const blob = new Blob([server.takes[route.split('/')[2]].file], { type: 'audio/wav' });
    const pending = pendingTakes.get(route); pendingTakes.delete(route);
    if (pending) await pending;
    return { ok: true, blob: async () => blob };
  }
  const snapshot = structuredClone(result);
  return { ok: true, json: async () => snapshot };
}
server = initial();
server.takes.reference = recording('reference');
server.sessions = [{ id: 'sample', title: 'Saved session', created: '2026-10-13T00:00:00Z', duration: 2 }];
const startupFile = server.takes.reference.file, releaseStartupTake = delayTake('reference');
const context = vm.createContext({ console, Blob, Event, DOMException, performance, AbortController,
  document: { getElementById: id => { assert(nodes.has(id), id); return nodes.get(id); }, createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag), createTextNode: text => text },
  location: { hash: '#test-token', pathname: '/' }, sessionStorage: { getItem: () => null, setItem() {} }, history: { replaceState() {} },
  navigator: { mediaDevices: { getUserMedia: async options => {
    events.push('microphone'); assert.deepEqual(JSON.parse(JSON.stringify(options)), { audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
    if (failMic) throw new DOMException('Microphone denied', 'NotAllowedError');
    if (pendingMic) return pendingMic;
    return makeStream();
  } } }, MediaRecorder: Recorder,
  setTimeout: (fn, ms) => setTimeout(fn, ms === 1000 ? 1 : ms), clearTimeout, setInterval, clearInterval,
  requestAnimationFrame: () => 0, cancelAnimationFrame() {},
  URL: {
    createObjectURL: blob => { const url = `blob:derivative-${++nextURL}`; createdURLs.set(url, blob); return url; },
    revokeObjectURL: url => revoked.push(url),
  },
  fetch: fakeFetch,
});
context.window = context; context.addEventListener = () => {};
vm.runInContext(fs.readFileSync(root + '/interval_charts.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(root + '/interval_analysis.js', 'utf8'), context);
const run = source => vm.runInContext(source, context);
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(check) { for (let i = 0; i < 200; i++) { if (check()) return; await wait(2); } throw Error('Timed out'); }
const idle = () => until(() => run('!active && !busy'));
const requestRoutes = start => requests.slice(start).map(request => request.route);
async function click(id) {
  assert(!nodes.get(id).disabled, `${id} must be enabled`);
  nodes.get(id).dispatchEvent(new Event('click'));
  await idle();
}
async function startupTakeRace() {
  await until(() => requests.some(request => request.route === '/take/reference'));
  assert(run('busy'), 'startup must remain busy while its listening copy is pending');
  for (const id of ['reference-record', 'comparison-record', 'reference-choose', 'comparison-choose',
    'analyze', 'save', 'session-title', 'session-notes', 'close']) assert(nodes.get(id).disabled, id);
  const load = nodes.get('sessions').querySelectorAll('button')[0];
  assert(load.disabled, 'Load must be disabled during startup');
  load.dispatchEvent(new Event('click'));
  assert(!requests.some(request => request.route === '/load'), 'busy also guards the load handler');

  // Supersede the snapshot directly to exercise stale-response protection despite the UI lock.
  await run('api("/load", {id: "sample"}).then(applySnapshot)');
  const audio = nodes.get('reference-audio'), currentURL = audio.src;
  assert.equal(await createdURLs.get(currentURL).text(), server.takes.reference.file);
  releaseStartupTake();
  await idle();
  const staleURL = [...createdURLs.keys()].at(-1);
  assert.equal(await createdURLs.get(staleURL).text(), startupFile);
  assert.notEqual(staleURL, currentURL);
  assert.equal(audio.src, currentURL, 'late startup response must not replace the loaded session');
  assert(!audio.sources.includes(staleURL), 'stale audio must never be installed');
  assert(revoked.includes(staleURL), 'the discarded startup blob URL must be revoked');
  assert(!revoked.includes(currentURL), 'the loaded session must keep its playback URL');
  assert.equal(nodes.get('session-title').value, 'Loaded title');
  assert(!nodes.get('reference-choose').disabled, 'startup completion restores controls');
  console.log('PASS: startup blocks controls through take fetches and revokes a late take after a newer load');
}
async function overlappingTakeResponses() {
  for (const kind of ['reference', 'comparison']) {
    const route = `/take/${kind}`, before = requests.filter(request => request.route === route).length;
    const release = delayTake(kind), pending = run(`fetchTake("${kind}")`);
    await until(() => requests.filter(request => request.route === route).length > before);
    await run(`fetchTake("${kind}")`);
    const audio = nodes.get(`${kind}-audio`), currentURL = audio.src;
    release(); await pending;
    const staleURL = [...createdURLs.keys()].at(-1);
    assert.notEqual(staleURL, currentURL);
    assert.equal(audio.src, currentURL, 'older requests with the same signature must not win');
    assert(!audio.sources.includes(staleURL));
    assert(revoked.includes(staleURL));
    assert(!revoked.includes(currentURL));
  }
  console.log('PASS: per-kind request generations reject older responses even with unchanged take signatures');
}
async function changedTakeSignatureResponse() {
  const comparisonCount = requests.filter(request => request.route === '/take/comparison').length;
  const releaseComparison = delayTake('comparison'), pending = run('fetchTake("comparison")');
  await until(() => requests.filter(request => request.route === '/take/comparison').length > comparisonCount);
  const referenceCount = requests.filter(request => request.route === '/take/reference').length;
  const releaseReference = delayTake('reference'), releaseReplacement = delayTake('comparison');
  const loading = run('api("/load", {id: "sample"}).then(applySnapshot)');
  await until(() => requests.filter(request => request.route === '/take/reference').length > referenceCount);
  await until(() => requests.filter(request => request.route === '/take/comparison').length > comparisonCount + 1);

  releaseComparison(); await pending;
  const staleURL = [...createdURLs.keys()].at(-1), audio = nodes.get('comparison-audio');
  assert(!audio.sources.includes(staleURL), 'changed take identities must invalidate pending audio');
  assert(revoked.includes(staleURL));
  releaseReference(); releaseReplacement(); await loading;
  assert.equal(await createdURLs.get(audio.src).text(), server.takes.comparison.file);
  console.log('PASS: changed take signatures reject stale audio while replacement copies are pending');
}
async function changedWireRecord() {
  const referenceURL = nodes.get('reference-audio').src, comparisonURL = nodes.get('comparison-audio').src;
  const filename = server.takes.reference.file;
  server.takes.reference.duration = 3;
  const start = requests.length;
  await run('refresh()');
  assert.deepEqual(requestRoutes(start), ['/state', '/take/reference']);
  assert.equal(server.takes.reference.file, filename);
  assert.notEqual(nodes.get('reference-audio').src, referenceURL, 'identity includes the full wire record, not only its filename');
  assert(revoked.includes(referenceURL));
  assert.equal(nodes.get('comparison-audio').src, comparisonURL);
  assert(!revoked.includes(comparisonURL));
  console.log('PASS: take identity compares the entire wire record and refreshes only the changed take');
}
async function cancelledChooseKeepsMetadata() {
  const title = nodes.get('session-title'), notes = nodes.get('session-notes');
  const previousTitle = title.value, previousNotes = notes.value;
  title.value = 'Typed title'; notes.value = 'Unsaved edits\nKeep these notes.';
  cancelChoose = true;
  try {
    for (const kind of ['reference', 'comparison']) {
      const before = JSON.stringify(server), start = requests.length;
      const previousURL = nodes.get(`${kind}-audio`).src;
      await click(`${kind}-choose`);
      assert.equal(JSON.stringify(server), before, 'a cancelled picker returns an unchanged snapshot');
      assert.equal(title.value, 'Typed title');
      assert.equal(notes.value, 'Unsaved edits\nKeep these notes.');
      assert.deepEqual(requestRoutes(start), ['/choose'], 'cancelled choose must not fetch state or either take');
      assert.equal(nodes.get(`${kind}-audio`).src, previousURL);
      assert(!revoked.includes(previousURL), 'an unchanged playback copy stays alive');
    }
  } finally {
    cancelChoose = false;
    title.value = previousTitle; notes.value = previousNotes;
  }
  console.log(`PASS: cancelled picker preserves ${server.loaded_session ? 'edited saved' : 'draft'} metadata and playback copies without refetching`);
}
async function recordingFlow() {
  server = initial(); await run('refresh()');
  await wait(10);
  assert(nodes.get('comparison-record').disabled);
  const choosing = requests.length;
  await click('reference-choose');
  assert.deepEqual(requestRoutes(choosing), ['/choose', '/take/reference']);
  assert(!nodes.get('comparison-record').disabled);
  assert.match(nodes.get('reference-audio').src, /^blob:derivative-/);
  events.length = 0;
  nodes.get('comparison-record').dispatchEvent(new Event('click'));
  assert(events.indexOf('play') < events.indexOf('microphone'), 'play unlock must be synchronous before requesting mic');
  assert.equal(events[events.indexOf('play') + 1], 'pause');
  await until(() => run('active?.playbackStart !== null && active?.playbackStart !== undefined'));
  assert.equal(recorderCalls.at(-1), 250);
  const uploading = requests.length;
  await click('comparison-stop');
  assert.deepEqual(requestRoutes(uploading), ['/upload/comparison', '/take/comparison']);
  const upload = requests.find(request => request.route === '/upload/comparison');
  assert(upload); assert.equal(upload.options.headers['Content-Type'], 'audio/mp4');
  assert(Number(upload.options.headers['X-Playback-Delay']) >= 0);
  assert(streams.at(-1).getTracks()[0].stopped);
  assert.match(nodes.get('comparison-audio').src, /^blob:derivative-/);
  console.log('PASS: synchronous unlock, MIME fallback, processing constraints, countdown, start stamps, upload headers, derivative playback and track release');

}
async function deniedAndCancelledCapture() {
  rejectPlayAt = playCount + 2;
  run('startComparison()'); await idle();
  assert(nodes.get('playback-retry').hidden === false);
  assert(streams.at(-1).getTracks()[0].stopped);
  assert.match(nodes.get('comparison-status').textContent, /gesture/);
  rejectPlayAt = 0;
  failMic = true; run('startComparison()'); await idle(); failMic = false;
  assert.match(nodes.get('comparison-status').textContent, /denied/);
  let resolveMic; pendingMic = new Promise(resolve => { resolveMic = resolve; });
  run('startComparison(); stopCapture()');
  const delayedStream = makeStream(); resolveMic(delayedStream); pendingMic = null;
  await wait(5); assert(delayedStream.getTracks()[0].stopped);
  console.log('PASS: blocked playback retry, denied microphone and cancellation during pending permission release resources');

}
async function stoppedCaptureLimits() {
  run('startComparison()');
  await until(() => run('active?.playbackStart != null'));
  const uploadedBeforeError = requests.filter(r => r.route.startsWith('/upload/')).length;
  lastRecorder.onerror({ error: Error('Synthetic recorder failure') }); await idle();
  assert(streams.at(-1).getTracks()[0].stopped);
  assert.equal(requests.filter(r => r.route.startsWith('/upload/')).length, uploadedBeforeError);
  run('startReference()'); await until(() => lastRecorder.state === 'recording');
  run('data.limits.bytes = 1');
  lastRecorder.ondataavailable({ data: new Blob(['too large']) }); await idle();
  assert.match(nodes.get('reference-status').textContent, /size limit/);
  run('data.limits.bytes = 67108864');
  run('startComparison()'); await until(() => run('active?.playbackStart != null'));
  const tail = requests.length;
  nodes.get('reference-audio').dispatchEvent(new Event('ended')); await idle();
  assert.deepEqual(requestRoutes(tail), ['/upload/comparison', '/take/comparison']);
  assert(streams.at(-1).getTracks()[0].stopped);
  const budget = requests.length;
  run('data.limits.seconds = 2.02; startReference()'); await idle();
  assert.deepEqual(requestRoutes(budget), ['/upload/reference', '/take/reference']);
  assert(streams.at(-1).getTracks()[0].stopped);
  console.log('PASS: recorder failures and byte limits discard takes; playback-end tail and time budget stop and upload');

}
function analysisRendered() {
  assert.equal(nodes.get('analysis-result').hidden, false);
  assert.equal(nodes.get('result-interval').textContent, 'Major third above');
  assert.match(nodes.get('result-detail').textContent, /agreement 67 %/);
  assert.match(nodes.get('result-simple').textContent, /Ignoring octaves: Major third above \(100 %\)/);
  assert.equal(nodes.get('segments').children.length, 3);
  assert.match(nodes.get('analysis-method').textContent, /pYIN/);
  assert.equal(nodes.get('analysis-warnings').children.length, 1);
  const histogram = nodes.get('histogram'), timeline = nodes.get('timeline');
  assert.equal(histogram.querySelectorAll('svg').length, 1);
  assert.equal(histogram.querySelectorAll('g').length, 2);
  histogram.querySelectorAll('g')[0].dispatchEvent(new Event('focus'));
  assert.match(histogram.children.find(node => node.className === 'chart-readout').textContent, /86 frames/);
  const traces = timeline.querySelectorAll('path');
  assert.equal(traces.length, 4);
  for (const trace of traces) assert.equal(trace.attrs.d.match(/M/g).length, 2, 'unvoiced gaps break each trace');
  const slider = timeline.querySelectorAll('rect').find(node => node.attrs.role === 'slider');
  slider.dispatchEvent(new Event('focus'));
  assert.match(slider.attrs['aria-valuetext'], /220.0 Hz/);
  const right = new Event('keydown'); right.key = 'ArrowRight'; slider.dispatchEvent(right);
  assert.match(slider.attrs['aria-valuetext'], /reference unvoiced Hz/);
  for (const container of [histogram, timeline]) {
    const table = container.querySelectorAll('details')[0]; table.open = true;
    table.dispatchEvent(new Event('toggle'));
    assert(container.querySelectorAll('table').length === 1);
  }
}
async function metadataSnapshots() {
  await cancelledChooseKeepsMetadata();
  let start = requests.length;
  await run('mutate("save", "/load", {id: "sample"})');
  assert.deepEqual(requestRoutes(start), ['/load', '/take/reference', '/take/comparison']);
  assert.equal(nodes.get('session-title').value, 'Loaded title');
  assert.equal(nodes.get('session-notes').value, 'Loaded notes');
  assert.equal(nodes.get('save').textContent, 'Update notes');
  assert(nodes.get('alignment-manual').checked);
  assert.equal(nodes.get('offset-seconds').value, '0.12');
  analysisRendered();
  await cancelledChooseKeepsMetadata();
  nodes.get('session-title').value = 'Re-analysis title';
  nodes.get('session-notes').value = 'Re-analysis notes';
  start = requests.length;
  await click('analyze');
  assert.deepEqual(requestRoutes(start), ['/analyze']);
  assert.equal(nodes.get('session-title').value, 'Re-analysis title');
  assert.equal(nodes.get('session-notes').value, 'Re-analysis notes');
  assert.equal(nodes.get('save').textContent, 'Save');
  analysisRendered();
  start = requests.length;
  await click('save');
  assert.deepEqual(requestRoutes(start), ['/save'], 'save applies its snapshot without GET /state or take fetches');
  assert.equal(nodes.get('save').textContent, 'Update notes');
  assert.equal(nodes.get('session-title').value, server.loaded_session.title);
  assert.equal(nodes.get('session-notes').value, server.loaded_session.notes);
  assert.equal(nodes.get('sessions').querySelectorAll('button').length, 1);
  nodes.get('session-title').value = 'Updated title'; nodes.get('session-notes').value = 'Updated notes';
  start = requests.length;
  await click('save');
  assert.deepEqual(requestRoutes(start), ['/update']);
  assert.equal(server.loaded_session.title, 'Updated title');
  assert.equal(nodes.get('session-title').value, 'Updated title');
  assert.equal(nodes.get('session-notes').value, 'Updated notes');
  nodes.get('session-title').value = 'Unsaved edits';
  start = requests.length;
  nodes.get('sessions').querySelectorAll('button')[0].dispatchEvent(new Event('click')); await idle();
  assert.deepEqual(requestRoutes(start), ['/load', '/take/reference', '/take/comparison']);
  assert.equal(nodes.get('session-title').value, 'Loaded title');
  assert.equal(nodes.get('session-notes').value, 'Loaded notes');
  start = requests.length;
  await click('reveal');
  assert.deepEqual(requestRoutes(start), ['/reveal', '/state'], 'non-snapshot replies reconcile once');
  console.log('PASS: save, load, update and analyze consume snapshots directly; analysis, charts and metadata render from them');
}
async function noAnswerSnapshot() {
  noAnswer = true;
  const start = requests.length;
  try { await click('analyze'); } finally { noAnswer = false; }
  assert.deepEqual(requestRoutes(start), ['/analyze']);
  assert.equal(nodes.get('result-interval').textContent, 'Not enough overlapping singing');
  assert.match(nodes.get('result-detail').textContent, /0.00 s overlapping singing/);
  assert.equal(nodes.get('histogram').querySelectorAll('svg').length, 0);
  assert.equal(nodes.get('histogram').children[0].textContent, 'No jointly voiced frames to count.');
  assert.equal(nodes.get('segments').children[0].textContent, 'No sustained interval segments.');
  assert(nodes.get('timeline').querySelectorAll('path').every(path => path.attrs.d === ''));
  console.log('PASS: no-answer snapshots render empty histograms, unvoiced timelines and segment guidance');
}
async function failedUploadReconciliation() {
  for (const committed of [false, true]) {
    const originalURL = nodes.get('reference-audio').src;
    nodes.get('session-title').value = 'Draft title'; nodes.get('session-notes').value = 'Draft notes';
    nextFailure = { route: '/upload/reference', committed };
    nodes.get('reference-record').dispatchEvent(new Event('click'));
    await until(() => lastRecorder.state === 'recording');
    const start = requests.length;
    await click('reference-stop');
    assert.deepEqual(requestRoutes(start), ['/upload/reference', '/state', ...(committed ? ['/take/reference'] : [])]);
    assert.match(nodes.get('reference-status').textContent, /Synthetic response failure/);
    assert(streams.at(-1).getTracks()[0].stopped);
    assert.equal(nodes.get('session-title').value, committed ? '' : 'Draft title');
    assert.equal(nodes.get('session-notes').value, committed ? '' : 'Draft notes');
    assert.equal(nodes.get('reference-audio').src === originalURL, !committed);
    assert.equal(revoked.includes(originalURL), committed);
  }
  console.log('PASS: failed uploads reconcile exactly once whether or not the take was committed');
}
async function nonSnapshotUploadReconciliation() {
  nextUploadReply = { uploaded: true };
  nodes.get('reference-record').dispatchEvent(new Event('click'));
  await until(() => lastRecorder.state === 'recording');
  const start = requests.length;
  await click('reference-stop');
  assert.deepEqual(requestRoutes(start), ['/upload/reference', '/state', '/take/reference']);
  assert.equal(nodes.get('reference-status').textContent, 'Take ready.');
  assert.equal(await createdURLs.get(nodes.get('reference-audio').src).text(), server.takes.reference.file);
  assert(!nodes.get('comparison-record').disabled);
  assert(streams.at(-1).getTracks()[0].stopped);
  console.log('PASS: a non-snapshot upload response reconciles exactly once');
}
async function failedTakeRecovery() {
  for (const route of ['/state', '/analyze', '/choose']) {
    const oldURL = nodes.get('reference-audio').src, comparisonURL = nodes.get('comparison-audio').src;
    nextFailure = { route: '/take/reference', status: 500 };
    const failing = requests.length;
    await click('reference-choose');
    assert.deepEqual(requestRoutes(failing), ['/choose', '/take/reference']);
    assert.match(nodes.get('reference-status').textContent, /Synthetic response failure/);
    assert.equal(nodes.get('reference-audio').src, '');
    assert(nodes.get('comparison-record').disabled);
    assert.equal(revoked.filter(url => url === oldURL).length, 1);
    const take = structuredClone(server.takes.reference);
    nodes.get('session-title').value = 'Recovery title'; nodes.get('session-notes').value = 'Recovery notes';
    const recovering = requests.length;
    if (route === '/state') {
      nextFailure = { route: '/choose', committed: false };
      await click('reference-choose');
      assert.deepEqual(requestRoutes(recovering), ['/choose', '/state', '/take/reference']);
    } else {
      cancelChoose = route === '/choose';
      try { await click(route === '/choose' ? 'reference-choose' : 'analyze'); }
      finally { cancelChoose = false; }
      assert.deepEqual(requestRoutes(recovering), [route, '/take/reference']);
    }
    const recoveredURL = nodes.get('reference-audio').src;
    assert.match(recoveredURL, /^blob:derivative-/);
    assert.equal(await createdURLs.get(recoveredURL).text(), take.file);
    assert.deepEqual(server.takes.reference, take, `${route} recovery must not replace the recording`);
    assert(!nodes.get('comparison-record').disabled, `${route} recovery restores comparison recording`);
    assert.equal(nodes.get('comparison-audio').src, comparisonURL);
    assert(!revoked.includes(comparisonURL));
    assert.equal(nodes.get('session-title').value, 'Recovery title');
    assert.equal(nodes.get('session-notes').value, 'Recovery notes');
    const healthy = requests.length;
    await run('refresh()');
    assert.deepEqual(requestRoutes(healthy), ['/state'], 'an installed unchanged derivative is not refetched');
    assert.equal(nodes.get('reference-audio').src, recoveredURL);
    assert(!revoked.includes(recoveredURL));
  }
  console.log('PASS: failed derivative GETs recover through reconciliation, analysis and cancelled choose without replacing takes');
}
async function replacementAndClose() {
  nodes.get('session-title').value = 'Draft title'; nodes.get('session-notes').value = 'Draft notes';
  const referenceURL = nodes.get('reference-audio').src, comparisonURL = nodes.get('comparison-audio').src;
  const start = requests.length;
  await click('reference-choose');
  assert.deepEqual(requestRoutes(start), ['/choose', '/take/reference']);
  assert.equal(nodes.get('session-title').value, ''); assert.equal(nodes.get('session-notes').value, '');
  assert.equal(nodes.get('save').textContent, 'Save'); assert(revoked.includes(referenceURL));
  assert.equal(nodes.get('comparison-audio').src, comparisonURL);
  assert(!revoked.includes(comparisonURL));
  nextFailure = { route: '/take/reference', committed: false };
  const playbackFailure = requests.length;
  await click('reference-choose');
  assert.deepEqual(requestRoutes(playbackFailure), ['/choose', '/take/reference']);
  assert.match(nodes.get('reference-status').textContent, /Synthetic response failure/);
  nextFailure = { route: '/choose', committed: true };
  const failed = requests.length;
  await click('reference-choose');
  assert.deepEqual(requestRoutes(failed), ['/choose', '/state', '/take/reference']);
  assert.match(nodes.get('reference-status').textContent, /Synthetic response failure/);
  const closing = requests.length;
  await click('close'); assert.match(nodes.get('session-status').textContent, /^Session closed/);
  assert.deepEqual(requestRoutes(closing), ['/close']);
  assert.deepEqual([...createdURLs.keys()].sort(), [...revoked].sort(), 'every playback URL is revoked exactly once');
  console.log('PASS: replacements clear metadata, failed mutations reconcile, every URL is revoked once and close needs no state fetch');
}
async function main() {
  await startupTakeRace();
  await overlappingTakeResponses();
  await changedTakeSignatureResponse();
  await changedWireRecord();
  await recordingFlow();
  await deniedAndCancelledCapture();
  await stoppedCaptureLimits();
  await metadataSnapshots();
  await noAnswerSnapshot();
  await failedUploadReconciliation();
  await nonSnapshotUploadReconciliation();
  await failedTakeRecovery();
  await replacementAndClose();
}
main().catch(error => { console.error(error); process.exitCode = 1; run('if (active) failCapture(active, Error("Test stopped"))'); });
