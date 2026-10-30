import { timingSafeEqual } from 'node:crypto';

const COOKIE = '__Host-ao_demo';
const SESSION_SECONDS = 8 * 60 * 60;
const encoder = new TextEncoder();
const readRoutes = [
  /^\/api\/ontology(?:\/(?:graph|concepts(?:\/[^/]+)?))?$/,
  /^\/api\/sources$/,
  /^\/api\/exports(?:\/.*)?$/,
  /^\/api\/playground\/(?:index|sparql\/examples)$/,
];
const queryRoutes = new Set(['/api/playground/chat', '/api/playground/sql', '/api/playground/sparql']);

function safeEqual(a, b) {
  const left = encoder.encode(a), right = encoder.encode(b);
  return left.length === right.length && timingSafeEqual(left, right);
}
function base64url(bytes) {
  return btoa(String.fromCharCode(...new Uint8Array(bytes))).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '');
}
function decode(value) {
  return Uint8Array.from(atob(value.replaceAll('-', '+').replaceAll('_', '/')), c => c.charCodeAt(0));
}
async function key(secret) {
  return crypto.subtle.importKey('raw', encoder.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign', 'verify']);
}
async function session(env, audience) {
  const payload = base64url(encoder.encode(JSON.stringify({
    exp: Math.floor(Date.now() / 1000) + SESSION_SECONDS, sid: crypto.randomUUID(), aud: audience,
  })));
  const signature = await crypto.subtle.sign('HMAC', await key(env.SESSION_SECRET), encoder.encode(payload));
  return `${payload}.${base64url(signature)}`;
}
async function identity(request, env) {
  const token = (request.headers.get('cookie') || '').split(';').map(x => x.trim()).find(x => x.startsWith(COOKIE + '='))?.slice(COOKIE.length + 1);
  if (!token || token.length > 2048) return null;
  try {
    const [payload, signature, extra] = token.split('.');
    if (extra || base64url(decode(signature)) !== signature || !await crypto.subtle.verify('HMAC', await key(env.SESSION_SECRET), decode(signature), encoder.encode(payload))) return null;
    const value = JSON.parse(new TextDecoder().decode(decode(payload)));
    if (!Number.isFinite(value.exp) || value.exp <= Date.now() / 1000 || value.exp > Date.now() / 1000 + SESSION_SECONDS + 60 || value.aud !== new URL(request.url).origin || typeof value.sid !== 'string') return null;
    return value;
  } catch { return null; }
}
function headers(extra = {}) {
  return {
    'cache-control': 'private, no-store', 'x-content-type-options': 'nosniff',
    'referrer-policy': 'same-origin', 'x-frame-options': 'DENY',
    'x-robots-tag': 'noindex, nofollow', ...extra,
  };
}
function json(error, detail, status) {
  return Response.json({ error, detail }, { status, headers: headers() });
}
function page(message = '', status = 200, full = false) {
  return new Response(`<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${full ? 'AutoOntology' : 'AutoOntology 체험'}</title>
<style>*{box-sizing:border-box}body{margin:0;background:#f6f7f9;color:#18252c;font-family:system-ui,sans-serif;display:grid;place-items:center;min-height:100svh;padding:24px}main{background:white;border:1px solid #e1e6e9;border-radius:16px;padding:40px;max-width:420px;width:100%;box-shadow:0 12px 40px #18252c08}small{color:#51756b;font-weight:700}h1{font-size:25px;margin:12px 0}p{color:#637079;font-size:15px;line-height:1.65}label{display:block;font-size:14px;margin-top:28px}input,button{width:100%;font:inherit;border-radius:8px;padding:12px;margin-top:8px}input{border:1px solid #b7c3ca}button{border:0;background:#234f43;color:white;cursor:pointer;margin-top:16px}input:focus,button:focus-visible{outline:3px solid #8bd9b6;outline-offset:2px}.error{color:#a32b28;font-size:14px}</style>
<main><small>AutoOntology</small><h1>데이터와 문서를 하나의 지식으로</h1><p>공유받은 비밀번호를 입력해 주세요.<br>${full ? '데이터와 문서를 추가하고 온톨로지를 구축할 수 있어요.' : '샘플 데이터의 개념과 관계를 살펴보고 질문할 수 있어요.'}</p><form method="post" action="/__demo/login"><label for="password">비밀번호</label><input id="password" name="password" type="password" autocomplete="current-password" required maxlength="256" autofocus><button type="submit">${full ? '접속하기' : '체험 시작하기'}</button>${message ? `<p role="alert" class="error">${message}</p>` : ''}</form></main></html>`, {
    status, headers: headers({ 'referrer-policy': 'same-origin', 'content-type': 'text/html; charset=utf-8', 'content-security-policy': "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'" }),
  });
}
function sameOrigin(request) {
  return request.headers.get('origin') === new URL(request.url).origin && request.headers.get('sec-fetch-site') !== 'cross-site';
}
async function limited(binding, value) {
  if (!binding) throw new Error('missing rate limiter');
  return !(await binding.limit({ key: value })).success;
}
async function boundedText(request, maxBytes) {
  if (!request.body) return '';
  const reader = request.body.getReader(), decoder = new TextDecoder();
  let size = 0, output = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return output + decoder.decode();
    size += value.byteLength;
    if (size > maxBytes) { await reader.cancel(); return null; }
    output += decoder.decode(value, { stream: true });
  }
}
async function handle(request, env) {
  const url = new URL(request.url), path = url.pathname;
  const full = env.ACCESS_MODE === 'full';
  const loginPage = (message = '', status = 200) => page(message, status, full);
  if (!env.SESSION_SECRET || !env.ACCESS_PASSWORD_SHA256 || !env.ORIGIN_SECRET || !env.ORIGIN_URL) return json('not_configured', '체험 공간을 준비하고 있습니다.', 503);
  if (path === '/robots.txt') return new Response('User-agent: *\nDisallow: /\n', { headers: headers() });
  if (path === '/__demo/login') {
    if (request.method === 'GET') return loginPage();
    if (request.method !== 'POST') return json('method_not_allowed', '허용되지 않는 요청입니다.', 405);
    if (!sameOrigin(request)) return json('cross_origin', '이 페이지에서 다시 시도해 주세요.', 403);
    if (await limited(env.LOGIN_LIMIT, request.headers.get('cf-connecting-ip') || 'unknown')) return loginPage('잠시 후 다시 시도해 주세요.', 429);
    const type = request.headers.get('content-type') || '';
    if (!type.startsWith('application/x-www-form-urlencoded') || Number(request.headers.get('content-length') || 0) > 2048) return loginPage('입력 내용을 확인해 주세요.', 400);
    const raw = await boundedText(request, 2048);
    if (raw === null) return loginPage('입력 내용을 확인해 주세요.', 400);
    const password = new URLSearchParams(raw).get('password') || '';
    const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', encoder.encode(password))), x => x.toString(16).padStart(2, '0')).join('');
    if (!safeEqual(env.ACCESS_PASSWORD_SHA256, digest)) return loginPage('비밀번호를 확인해 주세요.', 401);
    return new Response(null, { status: 303, headers: headers({
      location: full ? '/ui/?lang=ko' : '/ui/ontology?lang=ko',
      'set-cookie': `${COOKIE}=${await session(env, url.origin)}; Path=/; Max-Age=${SESSION_SECONDS}; Secure; HttpOnly; SameSite=Lax`,
    }) });
  }
  const viewer = await identity(request, env);
  if (!viewer) {
    if (path.startsWith('/api/') || path.startsWith('/v1/') || request.method !== 'GET') return json('authentication_required', '비밀번호를 입력한 뒤 이용해 주세요.', 401);
    return new Response(null, { status: 303, headers: headers({ location: '/__demo/login' }) });
  }
  if (path === '/__demo/logout' && request.method === 'POST') {
    if (!sameOrigin(request)) return json('cross_origin', '이 페이지에서 다시 시도해 주세요.', 403);
    return new Response(null, { status: 303, headers: headers({ location: '/__demo/login', 'set-cookie': `${COOKIE}=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Lax` }) });
  }
  if (path === '/') return new Response(null, { status: 303, headers: headers({ location: full ? '/ui/?lang=ko' : '/ui/ontology?lang=ko' }) });
  if (!full && path === '/api/playground/conversations' && request.method === 'GET') return Response.json({ conversations: [] }, { headers: headers() });
  const read = ['GET', 'HEAD'].includes(request.method) && (readRoutes.some(x => x.test(path)) || path === '/ui' || path.startsWith('/ui/'));
  const query = request.method === 'POST' && queryRoutes.has(path);
  const fullRoute = path.startsWith('/api/') || path.startsWith('/v1/') || path === '/ingest' || path.startsWith('/ingest/') || ['/docs', '/redoc', '/openapi.json', '/health/live', '/health/ready'].includes(path);
  if (!(full && fullRoute) && !read && !query) return json('sample_demo_only', '샘플 탐색과 질문을 위한 체험 공간입니다. 데이터 추가·빌드·설정 변경은 로컬 설치 후 이용해 주세요.', 403);
  if ((query || (full && !['GET', 'HEAD', 'OPTIONS'].includes(request.method))) && !sameOrigin(request)) return json('cross_origin', '이 페이지에서 다시 시도해 주세요.', 403);
  if (query && await limited(env.QUERY_LIMIT, viewer.sid)) return json('rate_limit', '질문이 많습니다. 잠시 후 다시 시도해 주세요.', 429);
  const origin = new URL(env.ORIGIN_URL);
  if (origin.protocol !== 'https:' || origin.username || origin.password) throw new Error('invalid origin');
  origin.pathname = path; origin.search = url.search;
  const forwarded = new Headers();
  for (const name of ['accept', 'content-type', 'range', 'if-none-match', 'last-event-id']) {
    const value = request.headers.get(name); if (value) forwarded.set(name, value);
  }
  forwarded.set('x-autoontology-gateway', env.ORIGIN_SECRET);
  let body;
  if (full && !['GET', 'HEAD'].includes(request.method)) {
    const length = request.headers.get('content-length');
    if (length !== null && (!/^\d+$/.test(length) || Number(length) > 100000000)) return json('too_large', '업로드는 한 번에 90 MB까지 가능합니다.', 413);
    body = request.body;
  } else if (query) {
    if (Number(request.headers.get('content-length') || 0) > 64 * 1024) return json('too_large', '질문을 조금 줄여 주세요.', 413);
    const raw = await boundedText(request, 64 * 1024);
    if (raw === null) return json('too_large', '질문을 조금 줄여 주세요.', 413);
    try {
      const input = JSON.parse(raw);
      // Shared workspaces must never expose another visitor's saved conversation.
      delete input.conversation_id;
      body = JSON.stringify(input);
      forwarded.set('content-type', 'application/json');
    } catch { return json('invalid_request', '질문 형식을 확인해 주세요.', 400); }
  }
  const upstream = await (env.FETCH || fetch)(new Request(origin, { method: request.method, headers: forwarded, body, duplex: 'half', redirect: 'manual' }));
  const output = new Headers(upstream.headers);
  for (const [name, value] of Object.entries(headers())) output.set(name, value);
  output.delete('set-cookie');
  output.delete('access-control-allow-origin');
  if (output.has('location')) {
    const target = new URL(output.get('location'), origin);
    if (target.origin !== origin.origin) return json('origin_redirect', '페이지를 열 수 없습니다.', 502);
    output.set('location', target.pathname + target.search);
  }
  const response = new Response(upstream.body, { status: upstream.status, headers: output });
  if ((output.get('content-type') || '').includes('text/html')) {
    return new HTMLRewriter().on('head', {
      element(element) { element.append(`<meta name="autoontology-demo" content="${full ? 'full' : 'sample'}">`, { html: true }); },
    }).transform(response);
  }
  return response;
}
export default {
  async fetch(request, env) {
    try { return await handle(request, env); }
    catch (error) {
      console.error(JSON.stringify({ event: 'demo_gateway_error', type: error?.name || 'Error' }));
      return json('service_unavailable', '체험 서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.', 503);
    }
  },
};
