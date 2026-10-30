/* Live integration test: node tests/auth-session-live.cjs
 * Uses a separate session and in-memory browser storage; never prints secrets.
 * Requires the local backend and credentials already configured in .env.local.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const root = path.resolve(__dirname, '..');
process.loadEnvFile(path.join(root, '.env.local'));
const source = fs.readFileSync(path.join(root, 'src/lib/api.ts'), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;
const storage = new Map();
let refreshRequests = 0;
const context = {
  exports: {}, process, console, URLSearchParams, AbortController, AbortSignal,
  DOMException, setTimeout, clearTimeout, atob,
  window: {},
  localStorage: {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: (key) => storage.delete(key),
  },
  fetch: (url, init) => {
    if (String(url).endsWith('/api/auth/refresh')) refreshRequests++;
    return fetch(url, init);
  },
};
vm.runInNewContext(compiled, context, { filename: 'api.cjs' });
const api = context.exports;
async function rawRefresh(token) {
  return fetch(`${api.API_BASE_URL}/api/auth/refresh`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: token }),
    signal: AbortSignal.timeout(10000),
  });
}

(async () => {
  try {
    const login = await api.loginWithCredentials({
      email: process.env.NEXT_PUBLIC_AUTH_EMAIL,
      password: process.env.NEXT_PUBLIC_AUTH_PASSWORD,
      tenantId: process.env.NEXT_PUBLIC_AUTH_TENANT_ID,
    });
    assert.equal(api.getAuthToken(), login.access_token);
    assert.equal(api.getRefreshToken(), login.refresh_token);
    const me = await api.getMe();
    assert.ok(me.tenant_id);
    console.log('PASS login persists both tokens; /me resolves tenant');

    const first = api.refreshAuthToken();
    const second = api.refreshAuthToken();
    assert.equal(first, second);
    await Promise.all([first, second]);
    assert.equal(refreshRequests, 1);
    assert.notEqual(api.getRefreshToken(), login.refresh_token);
    assert.equal((await rawRefresh(login.refresh_token)).status, 401);
    assert.equal((await api.getMe()).tenant_id, me.tenant_id);
    console.log('PASS concurrent refresh shares one request; old token revoked');

    // Invalid access token exercises a real 401 and the frontend retry path.
    api.setAuthToken('invalid-access-token');
    assert.equal((await api.getMe()).tenant_id, me.tenant_id);
    assert.equal(refreshRequests, 2);
    console.log('PASS API 401 refreshes and retries as the same tenant');

    const cto = await api.getAiCtoStatus();
    assert.ok(['healthy', 'degraded'].includes(cto.ai_cto.status));
    console.log(`PASS /ai-cto: ${cto.ai_cto.status}`);
    for (const route of ['/api/devices', '/api/infrastructure/topology', '/api/assets/devices']) {
      try {
        await api.apiFetch(`${route}?${api.withTenant(me.tenant_id)}`);
        console.log(`DATA ${route}: OK`);
      } catch (error) {
        console.log(`DATA ${route}: ${error.status ?? 'network error'}`);
      }
    }
    const rotated = api.getRefreshToken();
    await api.logoutSession();
    assert.equal(api.getAuthToken(), null);
    assert.equal(api.getRefreshToken(), null);
    assert.equal((await rawRefresh(rotated)).status, 401);
    await assert.rejects(api.ensureAuthenticated(), (error) => error.status === 401);
    console.log('PASS logout clears storage, revokes refresh, prevents immediate bootstrap');
  } finally {
    if (api.getRefreshToken()) await api.logoutSession();
  }
})().catch((error) => {
  console.error(`FAIL ${error.name}; status=${error.status ?? 'n/a'}; path=${error.path ?? 'assertion'}`);
  process.exitCode = 1;
});
