"""Neutral representation shared by any client; no model or network request."""
import hashlib
import json


def compact_public_source(value, max_bytes=2500):
    if type(max_bytes) is not int or not 512<=max_bytes<=65536:raise ValueError('Explicit UTF-8 compact byte budget required')
    text=value.get('text',value.get('body',''))
    if not isinstance(text,str):text=json.dumps(text,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    if value.get('content_type')=='application/json':
        try:text=json.dumps(json.loads(text),ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
        except ValueError:pass
    raw=text.encode('utf-8')
    result={k:value[k] for k in ('url','status','content_type','body_sha256','bytes','observed_at') if k in value}
    result['representation']='compact_public_source_v1';result['text']='';result['text_truncated']=True
    result['representation_sha256']='0'*64
    encode=lambda v:json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
    if len(encode(result))>max_bytes:raise ValueError('Source metadata exceeds compact byte budget')
    lo,hi=0,len(raw)
    while lo<hi:
        middle=(lo+hi+1)//2;result['text']=raw[:middle].decode('utf-8',errors='ignore')
        if len(encode(result))<=max_bytes:lo=middle
        else:hi=middle-1
    result['text']=raw[:lo].decode('utf-8',errors='ignore')
    result['text_truncated']=bool(value.get('text_truncated')) or lo<len(raw)
    result['representation_sha256']=hashlib.sha256(result['text'].encode('utf-8')).hexdigest()
    if len(encode(result))>max_bytes:
        result['text']=result['text'][:-1];result['text_truncated']=True
        result['representation_sha256']=hashlib.sha256(result['text'].encode('utf-8')).hexdigest()
    return result
