// This template is filled by build_feed.py at build time. Do not deploy this
// source file itself: deploy only the generated public/ directory.
const MEDIA = {};
const common = (info) => ({
  'Content-Type': 'audio/mpeg', 'Accept-Ranges': 'bytes', 'ETag': info.etag,
  'Cache-Control': 'public, max-age=0, no-transform',
  'X-Robots-Tag': 'noindex, nofollow', 'Referrer-Policy': 'no-referrer',
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Expose-Headers': 'Content-Length, Content-Range, Accept-Ranges, ETag'
});

export default {
  async fetch(request, env) {
    const info = MEDIA[new URL(request.url).pathname];
    if (!info) return env.ASSETS.fetch(request);
    const headers = common(info);
    if (request.method === 'OPTIONS') {
      return new Response(null, {status: 204, headers: {...headers,
        'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
        'Access-Control-Allow-Headers': 'Range, If-Range'}});
    }
    if (!['GET', 'HEAD'].includes(request.method)) {
      return new Response(null, {status: 405, headers: {...headers, Allow: 'GET, HEAD, OPTIONS'}});
    }
    if (request.method === 'HEAD') {
      return new Response(null, {headers: {...headers, 'Content-Length': String(info.bytes)}});
    }
    let range = request.headers.get('Range');
    if (request.headers.has('If-Range') && request.headers.get('If-Range') !== info.etag) range = null;
    let start = 0, end = info.bytes - 1, status = 200;
    if (range && !range.includes(',')) {
      const match = /^bytes=(\d*)-(\d*)$/.exec(range.trim());
      if (match && (match[1] || match[2])) {
        start = match[1] ? Number(match[1]) : Math.max(0, info.bytes - Number(match[2]));
        end = match[1] && match[2] ? Math.min(Number(match[2]), info.bytes - 1) : info.bytes - 1;
        if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start >= info.bytes || start > end || (!match[1] && Number(match[2]) === 0)) {
          return new Response(null, {status: 416, headers: {...headers, 'Content-Range': `bytes */${info.bytes}`, 'Content-Length': '0'}});
        }
        status = 206;
      } else if (range.startsWith('bytes=')) {
        return new Response(null, {status: 416, headers: {...headers, 'Content-Range': `bytes */${info.bytes}`, 'Content-Length': '0'}});
      }
    }
    const assetHeaders = new Headers(request.headers);
    for (const name of ['Range', 'If-Range', 'If-None-Match', 'If-Modified-Since', 'If-Match', 'If-Unmodified-Since']) assetHeaders.delete(name);
    assetHeaders.set('Accept-Encoding', 'identity');
    const asset = await env.ASSETS.fetch(new Request(request.url, {method: 'GET', headers: assetHeaders}));
    if (asset.status !== 200) return asset;
    const bytes = await asset.arrayBuffer();
    if (bytes.byteLength !== info.bytes) return new Response('Media size mismatch', {status: 502, headers: {'Cache-Control': 'no-store'}});
    headers['Content-Length'] = String(end - start + 1);
    if (status === 206) headers['Content-Range'] = `bytes ${start}-${end}/${info.bytes}`;
    return new Response(status === 206 ? bytes.slice(start, end + 1) : bytes, {status, headers});
  }
};
