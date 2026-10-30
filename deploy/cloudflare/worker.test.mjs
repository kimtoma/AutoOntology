import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { test } from 'node:test';
import worker from './worker.mjs';

const base = 'https://autoontology.example.com';
const password = 'test-password-used-only-in-tests';
const environment = () => ({
  ACCESS_PASSWORD_SHA256: createHash('sha256').update(password).digest('hex'),
  SESSION_SECRET: 'test-session-secret-used-only-in-tests',
  ORIGIN_SECRET: 'test-origin-secret-used-only-in-tests',
  ORIGIN_URL: 'https://origin.example.com',
  LOGIN_LIMIT: { limit: async () => ({ success: true }) },
  QUERY_LIMIT: { limit: async () => ({ success: true }) },
});
const request = (path, init = {}) => new Request(base + path, init);
async function login(env = environment()) {
  return worker.fetch(request('/__demo/login', {
    method: 'POST', headers: { Origin: base },
    body: new URLSearchParams({ password }),
  }), env);
}

test('unauthenticated pages and API requests never reach the origin', async () => {
  const env = environment();
  env.FETCH = () => assert.fail('origin must remain private');
  assert.equal((await worker.fetch(request('/ui/graph'), env)).status, 303);
  assert.equal((await worker.fetch(request('/api/ontology'), env)).status, 401);
});

test('the password form preserves the origin for browser POST navigation', async () => {
  const response = await worker.fetch(request('/__demo/login'), environment());
  assert.equal(response.headers.get('referrer-policy'), 'same-origin');
});

test('wrong passwords, cross-site login, and excessive attempts are rejected', async () => {
  const env = environment();
  assert.equal((await worker.fetch(request('/__demo/login', {
    method: 'POST', headers: { Origin: base },
    body: new URLSearchParams({ password: 'wrong' }),
  }), env)).status, 401);
  assert.equal((await worker.fetch(request('/__demo/login', {
    method: 'POST', headers: { Origin: 'https://other.example.com' },
    body: new URLSearchParams({ password }),
  }), env)).status, 403);
  env.LOGIN_LIMIT = { limit: async () => ({ success: false }) };
  assert.equal((await login(env)).status, 429);
});

test('login issues a secure cookie; a forged cookie is not accepted', async () => {
  const env = environment();
  const response = await login(env);
  assert.equal(response.status, 303);
  const cookie = response.headers.get('set-cookie');
  assert.match(cookie, /Secure; HttpOnly; SameSite=Lax/);
  assert.equal(response.headers.get('location'), '/ui/ontology?lang=ko');
  const token = cookie.split(';')[0];
  const dot = token.indexOf('.') + 1;
  const forged = token.slice(0, dot) + (token[dot] === 'a' ? 'b' : 'a') + token.slice(dot + 1);
  assert.equal((await worker.fetch(request('/api/ontology', {
    headers: { Cookie: forged },
  }), env)).status, 401);
});

test('signed in sample viewers cannot change settings, sources, builds, or use provider APIs', async () => {
  const env = environment();
  env.FETCH = () => assert.fail('privileged routes must not reach the origin');
  const cookie = (await login(env)).headers.get('set-cookie').split(';')[0];
  for (const path of ['/api/settings', '/api/sources', '/api/runs', '/ingest/delete', '/v1/chat/completions']) {
    const response = await worker.fetch(request(path, {
      method: 'POST', headers: { Cookie: cookie, Origin: base }, body: '{}',
    }), env);
    assert.equal(response.status, 403, path);
  }
  assert.equal((await worker.fetch(request('/api/settings', { headers: { Cookie: cookie } }), env)).status, 403);
});

test('allowed requests stream through with an origin secret and no browser credentials', async () => {
  const env = environment();
  const cookie = (await login(env)).headers.get('set-cookie').split(';')[0];
  env.FETCH = async (req) => {
    assert.equal(req.url, 'https://origin.example.com/api/ontology?revision=1');
    assert.equal(req.headers.get('x-autoontology-gateway'), env.ORIGIN_SECRET);
    assert.equal(req.headers.get('cookie'), null);
    assert.equal(req.headers.get('authorization'), null);
    return new Response('sample ontology', { headers: { 'content-type': 'application/json' } });
  };
  const response = await worker.fetch(request('/api/ontology?revision=1', {
    headers: { Cookie: cookie, Authorization: 'do-not-forward' },
  }), env);
  assert.equal(await response.text(), 'sample ontology');
  assert.equal(response.headers.get('cache-control'), 'private, no-store');
  assert.equal(response.headers.get('referrer-policy'), 'same-origin');
});

test('cross-site queries and queries above the rate limit are refused', async () => {
  const env = environment();
  const cookie = (await login(env)).headers.get('set-cookie').split(';')[0];
  assert.equal((await worker.fetch(request('/api/playground/chat', {
    method: 'POST', headers: { Cookie: cookie, Origin: 'https://other.example.com' }, body: '{}',
  }), env)).status, 403);
  env.QUERY_LIMIT = { limit: async () => ({ success: false }) };
  assert.equal((await worker.fetch(request('/api/playground/chat', {
    method: 'POST', headers: { Cookie: cookie, Origin: base }, body: '{}',
  }), env)).status, 429);
});

test('conversations remain browser-local in the shared demo', async () => {
  const env = environment();
  const cookie = (await login(env)).headers.get('set-cookie').split(';')[0];
  const response = await worker.fetch(request('/api/playground/conversations', { headers: { Cookie: cookie } }), env);
  assert.deepEqual(await response.json(), { conversations: [] });
  assert.equal((await worker.fetch(request('/api/playground/conversations/' + 'a'.repeat(32), { headers: { Cookie: cookie } }), env)).status, 403);
});

test('follow-up chat keeps explicit history without opening a saved conversation', async () => {
  const env = environment();
  const cookie = (await login(env)).headers.get('set-cookie').split(';')[0];
  env.FETCH = async (req) => {
    const body = await req.json();
    assert.equal(body.conversation_id, undefined);
    assert.deepEqual(body.history, [{ role: 'user', content: 'first question' }]);
    return new Response('event: done\ndata: {}\n\n', { headers: { 'content-type': 'text/event-stream' } });
  };
  const response = await worker.fetch(request('/api/playground/chat', {
    method: 'POST', headers: { Cookie: cookie, Origin: base, 'content-type': 'application/json' },
    body: JSON.stringify({ question: 'follow-up', conversation_id: 'a'.repeat(32), history: [{ role: 'user', content: 'first question' }] }),
  }), env);
  assert.equal(response.status, 200);
  assert.match(await response.text(), /event: done/);
});
