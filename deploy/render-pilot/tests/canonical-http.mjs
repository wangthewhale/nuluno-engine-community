import {request as httpRequest} from 'node:http';

/** CI loopback HTTP transport, retaining an explicitly supplied canonical Host.
 * Node fetch ignores a custom Host header. This adapter changes only the test
 * transport; the production gateway's HTTPS origin/Host checks stay unchanged.
 * It never follows redirects or permits a non-loopback destination.
 */
export async function canonicalHttpFetch(input, init) {
 const req = new Request(input, init), url = new URL(req.url);
 if(url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || url.username || url.password) throw Error('ci_loopback_transport_only');
 const body = req.body ? Buffer.from(await req.arrayBuffer()) : undefined;
 if(body && body.length > 2*1024*1024) throw Error('ci_request_byte_limit');
 return new Promise((resolve,reject)=>{
  const outgoing = httpRequest(url,{method:req.method,headers:Object.fromEntries(req.headers),signal:req.signal},incoming=>{
   const chunks=[];let size=0;
   incoming.on('data',chunk=>{size+=chunk.length;if(size>2*1024*1024)incoming.destroy(Error('ci_response_byte_limit'));else chunks.push(chunk);});
   incoming.on('error',reject);
   incoming.on('end',()=>{const headers=new Headers();for(let i=0;i<incoming.rawHeaders.length;i+=2)headers.append(incoming.rawHeaders[i],incoming.rawHeaders[i+1]);resolve(new Response(req.method==='HEAD'||[204,205,304].includes(incoming.statusCode)?null:Buffer.concat(chunks),{status:incoming.statusCode,headers}));});
  });
  outgoing.setTimeout(15000,()=>outgoing.destroy(Error('ci_loopback_timeout')));
  outgoing.on('error',reject);outgoing.end(body);
 });
}
