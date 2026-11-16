import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {canonicalHttpFetch} from './canonical-http.mjs';

test('actual loopback HTTP retains canonical Host, Origin and bodies without following redirects',async()=>{
 let calls=0;const server=createServer(async(req,res)=>{calls++;const chunks=[];for await(const c of req)chunks.push(c);res.writeHead(req.url==='/redirect'?303:200,{'content-type':'application/json',location:'/other'});res.end(JSON.stringify({host:req.headers.host,origin:req.headers.origin??null,method:req.method,body:Buffer.concat(chunks).toString()}));});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const origin='http://127.0.0.1:'+server.address().port;
 try{
  const response=await canonicalHttpFetch(origin+'/mcp',{method:'POST',headers:{Host:'pilot.ci.invalid',Origin:'https://pilot.ci.invalid'},body:'controlled-body'});
  assert.deepEqual(await response.json(),{host:'pilot.ci.invalid',origin:'https://pilot.ci.invalid',method:'POST',body:'controlled-body'});
  const redirected=await canonicalHttpFetch(origin+'/redirect');assert.equal(redirected.status,303);assert.equal(calls,2);
  await assert.rejects(canonicalHttpFetch('https://example.com/'),/ci_loopback_transport_only/);assert.equal(calls,2);
 }finally{server.closeAllConnections();await new Promise(r=>server.close(r));}
});
