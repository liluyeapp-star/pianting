import {readFileSync} from 'node:fs';
import assert from 'node:assert/strict';
const source=readFileSync(new URL('../skills/pianting/scripts/range_worker.mjs',import.meta.url),'utf8').replace('const MEDIA = {};','const MEDIA = {"/test.mp3":{"bytes":100,"etag":"\\"test\\""}};');
const worker=(await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'))).default;
const data=Uint8Array.from({length:100},(_,i)=>i);
const env={ASSETS:{fetch:async req=>{if(new URL(req.url).pathname!='/test.mp3')return new Response('missing',{status:404});assert.equal(req.headers.get('Range'),null);assert.equal(req.headers.get('Accept-Encoding'),'identity');return new Response(data);}}};
async function check(method,headers,status,expected,range){const r=await worker.fetch(new Request('https://example.com/test.mp3',{method,headers}),env);assert.equal(r.status,status);assert.deepEqual([...new Uint8Array(await r.arrayBuffer())],expected);if(range)assert.equal(r.headers.get('Content-Range'),range);return r;}
assert.equal((await check('HEAD',{},200,[])).headers.get('Content-Length'),'100');
await check('GET',{},200,[...data]);
await check('GET',{Range:'bytes=0-9'},206,[...data.slice(0,10)],'bytes 0-9/100');
await check('GET',{Range:'bytes=90-'},206,[...data.slice(90)],'bytes 90-99/100');
await check('GET',{Range:'bytes=-5'},206,[...data.slice(95)],'bytes 95-99/100');
await check('GET',{Range:'bytes=100-'},416,[],'bytes */100');
await check('GET',{Range:'bytes=0-9','If-Range':'"old"'},200,[...data]);
assert.equal((await worker.fetch(new Request('https://example.com/missing'),env)).status,404);
console.log('8 range and passthrough checks passed');
