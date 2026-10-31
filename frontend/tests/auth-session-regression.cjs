/* Run: node tests/auth-session-regression.cjs
 * Executes the actual TypeScript modules in isolated, deterministic runtimes.
 * React hooks are a minimal lifecycle harness, not a browser rendering test.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');

function load(relative, globals = {}, imports = {}) {
  const source = fs.readFileSync(path.join(root, relative), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX,
    },
  });
  const context = {
    exports: {}, console, setTimeout, clearTimeout, queueMicrotask,
    AbortController, AbortSignal, DOMException, URLSearchParams, atob,
    process: { env: { NEXT_PUBLIC_AUTH_DEV_BOOTSTRAP: 'false' } },
    require: (name) => {
      assert.ok(Object.hasOwn(imports, name), `Unexpected import: ${name}`);
      return imports[name];
    },
    ...globals,
  };
  vm.runInNewContext(outputText, context, { filename: relative });
  return context.exports;
}

function apiRuntime(fetch) {
  const store = new Map([
    ['hsdeep_auth_token', 'existing-access'],
    ['hsdeep_refresh_token', 'existing-refresh'],
  ]);
  const api = load('src/lib/api.ts', {
    window: {}, fetch,
    localStorage: {
      getItem: (key) => store.get(key) ?? null,
      setItem: (key, value) => store.set(key, value),
      removeItem: (key) => store.delete(key),
    },
  });
  return api;
}

function providerRuntime(api) {
  const effects = [];
  const changes = [];
  const states = [];
  let stateIndex = 0;
  let value;
  const react = {
    createContext: () => ({ Provider: 'Provider' }),
    useContext: () => value,
    useCallback: (callback) => callback,
    useRef: (current) => ({ current }),
    useState: (initial) => {
      const index = stateIndex++;
      states[index] = initial;
      return [initial, (next) => { states[index] = next; changes.push(index); }];
    },
    useEffect: (effect) => effects.push(effect),
  };
  const auth = load('src/lib/auth-context.tsx', {}, {
    react,
    'react/jsx-runtime': { jsx: (_type, props) => { value = props.value; return null; } },
    '@tanstack/react-query': { useQueryClient: () => ({ clear() {} }) },
    '@/lib/api': api,
  });
  auth.AuthProvider({ children: null });
  return { effects, changes, states };
}
const settle = () => new Promise((resolve) => setImmediate(resolve));

(async () => {
  let completeRefresh;
  let calls = 0;
  const concurrent = apiRuntime(() => {
    calls++;
    return new Promise((resolve) => { completeRefresh = resolve; });
  });
  const first = concurrent.refreshAuthToken();
  const second = concurrent.refreshAuthToken();
  assert.equal(first, second, 'Concurrent refresh must return the same promise');
  assert.equal(calls, 1);
  completeRefresh(new Response(JSON.stringify({ access_token: 'rotated-access', refresh_token: 'rotated-refresh' })));
  await Promise.all([first, second]);
  assert.equal(concurrent.getAuthToken(), 'rotated-access');
  assert.equal(concurrent.getRefreshToken(), 'rotated-refresh');
  console.log('PASS concurrent refresh shares one promise and persists rotated tokens');

  for (const kind of ['success', '401', '503', 'network']) {
    let requests = 0;
    const api = apiRuntime(async () => {
      requests++;
      if (kind === 'network') throw new TypeError('Network unavailable');
      if (kind === '401') return new Response('{"detail":"Unauthorized"}', { status: 401 });
      if (kind === '503') return new Response('{"detail":"Unavailable"}', { status: 503 });
      return new Response('{"tenant_id":"real-tenant"}');
    });
    const harness = providerRuntime(api);
    assert.equal(harness.changes.length, 0);
    const cleanup = harness.effects[0]();
    assert.equal(harness.changes.length, 0, 'Initial effect must not synchronously set loading');
    assert.equal(requests, 0, 'Initial session check must be deferred');
    await settle();
    await settle();
    assert.equal(harness.states[1], false, 'Loading must finish');
    if (kind === '401') {
      assert.equal(api.getAuthToken(), null);
      assert.equal(api.getRefreshToken(), null);
    } else {
      assert.equal(api.getAuthToken(), 'existing-access');
      assert.equal(api.getRefreshToken(), 'existing-refresh');
    }
    if (kind === 'success') assert.equal(harness.states[0].tenant_id, 'real-tenant');
    if (kind === '503' || kind === 'network') assert.ok(harness.states[2]);
    cleanup();
    console.log(`PASS deferred initial session check: ${kind}; credentials ${kind === '401' ? 'cleared' : 'preserved'}`);
  }
  console.log('PASS all auth-session regression assertions');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
