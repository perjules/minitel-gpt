// Exercise the trusted application function against fake DOM/fetch/timers.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const script = JSON.parse(fs.readFileSync(0, 'utf8')).script;
const elements = Object.fromEntries(
  ['testMsg', 'presetSel', 'fsystem', 'memoryUser', 'testSpin', 'testOut']
    .map(id => [id, {value: id === 'testMsg' ? '17/0' : 'test', style: {}, textContent: ''}])
);
global.document = {getElementById: id => elements[id]};
global.alert = () => {};
global.CSRF_TOKEN = "feature-test-csrf-token";
global.PRESET = "test";
global.DRAFT_SYSTEM = null;
global.performance = {now: () => 0};
vm.runInThisContext(script + '\nglobal.preview = testPreset;');
const tick = () => new Promise(resolve => setImmediate(resolve));

(async () => {
  let release;
  let calls = 0;
  global.fetch = async () => ({json: () => ++calls === 1
    ? new Promise(resolve => { release = resolve; })
    : Promise.resolve({ok: true, answer: 'RESET'})});
  const delayed = preview();
  await tick();
  await preview(true);
  release({ok: true, answer: 'LATE RESPONSE'});
  await delayed;
  assert.equal(elements.testOut.textContent, 'RESET');

  let timers = [];
  global.setTimeout = callback => {timers.push(callback);};
  calls = 0;
  global.fetch = async () => ({json: async () => ++calls === 1
    ? {ok: true, scene: ['first frame', 'LATE FRAME']}
    : {ok: true, answer: 'RESET'}});
  const playback = preview();
  await tick();
  assert.equal(elements.testOut.textContent, 'first frame\n');
  await preview(true);
  timers.shift()();
  await playback;
  assert.equal(elements.testOut.textContent, 'RESET');
  assert.equal(elements.testSpin.style.display, 'none');

  timers = [];
  global.fetch = async () => ({json: async () => ({ok: true, scene: ['first', 'second']})});
  const uninterrupted = preview();
  await tick();
  elements.testMsg.value = '';
  await preview();
  timers.shift()();
  await tick();
  timers.shift()();
  await uninterrupted;
  assert.equal(elements.testOut.textContent, 'first\nsecond\n');
})().catch(error => { console.error(error); process.exitCode = 1; });
