/**
 * Lightweight regression check for ZnwdProgressTimeline phase resolution.
 * Run: node scripts/test-znwd-progress-phase.mjs
 */
import assert from 'node:assert/strict';

const PHASE_ORDER = ['routing', 'retrieval', 'guard', 'generating'];

function phaseIndex(type) {
  const i = PHASE_ORDER.indexOf(type);
  return i >= 0 ? i : -1;
}

function resolveCurrentPhase(steps, streamPhase) {
  if (streamPhase === 'answer') return PHASE_ORDER.length;
  if ((steps || []).some((s) => s.type === 'generating' && s.status === 'done')) {
    return PHASE_ORDER.length;
  }
  let max = 0;
  for (const s of steps || []) {
    const idx = phaseIndex(s.type);
    if (idx < 0) continue;
    if (s.status === 'loading') return idx;
    if (s.status === 'done' && idx >= max) max = Math.min(idx + 1, PHASE_ORDER.length - 1);
  }
  return max;
}

const doneSteps = [
  { type: 'routing', status: 'done' },
  { type: 'retrieval', status: 'done' },
  { type: 'guard', status: 'done' },
  { type: 'generating', status: 'done' },
];

assert.equal(resolveCurrentPhase(doneSteps, 'answer'), PHASE_ORDER.length);
assert.equal(resolveCurrentPhase(doneSteps, 'steps'), PHASE_ORDER.length);

const loadingGen = [
  { type: 'routing', status: 'done' },
  { type: 'retrieval', status: 'done' },
  { type: 'guard', status: 'done' },
  { type: 'generating', status: 'loading' },
];
assert.equal(resolveCurrentPhase(loadingGen, 'steps'), 3);

console.log('znwd progress phase tests passed');
