"""
padb_compress.py -- shrink the big JSON data constants inside a built self-contained page.

The interactive pages embed their data as single-line JS constants, e.g.
``var DATA=[{...},...];`` -- JSON text that is mostly digits and repeated keys, so it
deflates 7-16x (a real 313 MB boxplot -> 29 MB).  This module rewrites each very large
constant in place as ``var DATA=PADB_unz("<deflated base64>",<rawLen>);`` and adds a small
SYNCHRONOUS inflate routine to <head>.

Why this shape (and not DecompressionStream / deferring scripts): the page's own scripts
run at parse time and read these constants immediately, and several register
DOMContentLoaded/onload handlers.  An in-place synchronous replacement keeps every script's
order and timing exactly as before -- only the bytes on the wire (the network share) shrink.

Plot-transparent: the decoded text is byte-identical to what was removed, so the
constant parses to the same value.  A constant is only rewritten when it is provably JSON
(parsed at build time up to MAX_VALIDATE bytes, else a strict lexical screen) -- a payload
JS would accept but JSON.parse would not (NaN/Infinity, calls, etc.) is left alone.
"""
from __future__ import annotations

import base64
import json
import re
import zlib
from pathlib import Path

MIN_PAYLOAD_BYTES = 256 * 1024          # only constants at least this big are worth it
MAX_RAW_BYTES = 400 * 1024 * 1024       # keep the decoded text inside V8's string limit
MAX_VALIDATE = 200 * 1024 * 1024        # full json.loads up to this size, lexical screen above
MARKER = "/*padb-unz*/"                 # idempotence: a page that already has it is skipped

# Synchronous raw-DEFLATE (RFC 1951) decoder -> UTF-8 -> JSON.parse.  Canonical-Huffman
# decode (cost ~ number of compressed bits), preallocated output (raw length is passed in).
UNZ_JS = r"""/*padb-unz*/
function PADB_unz(b64,n){
var s=atob(b64),L=s.length,src=new Uint8Array(L),i;
for(i=0;i<L;i++)src[i]=s.charCodeAt(i);
var out=new Uint8Array(n),op=0,ip=0,bb=0,bc=0;
function bits(k){while(bc<k){bb|=src[ip++]<<bc;bc+=8;}var v=bb&((1<<k)-1);bb>>>=k;bc-=k;return v;}
function build(len,m){var c=new Uint16Array(16),sy=new Uint16Array(m),of=new Uint16Array(16),j;
for(j=0;j<m;j++)c[len[j]]++;c[0]=0;for(j=1;j<15;j++)of[j+1]=of[j]+c[j];
for(j=0;j<m;j++)if(len[j])sy[of[len[j]]++]=j;return {c:c,s:sy};}
function dec(h){var code=0,first=0,idx=0,l,cnt;
for(l=1;l<16;l++){while(bc<1){bb|=src[ip++]<<bc;bc+=8;}code|=bb&1;bb>>>=1;bc--;cnt=h.c[l];
if(code-cnt<first)return h.s[idx+(code-first)];idx+=cnt;first+=cnt;first<<=1;code<<=1;}
throw new Error("padb-unz: bad code");}
var LB=[3,4,5,6,7,8,9,10,11,13,15,17,19,23,27,31,35,43,51,59,67,83,99,115,131,163,195,227,258];
var LE=[0,0,0,0,0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,0];
var DB=[1,2,3,4,5,7,9,13,17,25,33,49,65,97,129,193,257,385,513,769,1025,1537,2049,3073,4097,6145,8193,12289,16385,24577];
var DE=[0,0,0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10,10,11,11,12,12,13,13];
var CO=[16,17,18,0,8,7,9,6,10,5,11,4,12,3,13,2,14,1,15];
var fl=null,fd=null;
function codes(lc,dc){var sym,len,dist;
for(;;){sym=dec(lc);
if(sym<256){out[op++]=sym;}
else if(sym===256)return;
else{sym-=257;len=LB[sym]+bits(LE[sym]);sym=dec(dc);dist=DB[sym]+bits(DE[sym]);
for(;len>0;len--){out[op]=out[op-dist];op++;}}}}
var last,type,j,t;
do{last=bits(1);type=bits(2);
if(type===0){bb=0;bc=0;var ln=src[ip]|(src[ip+1]<<8);ip+=4;
for(j=0;j<ln;j++)out[op++]=src[ip++];}
else if(type===1){
if(!fl){var fx=new Uint8Array(288);for(j=0;j<144;j++)fx[j]=8;for(;j<256;j++)fx[j]=9;for(;j<280;j++)fx[j]=7;for(;j<288;j++)fx[j]=8;
fl=build(fx,288);var fy=new Uint8Array(30);for(j=0;j<30;j++)fy[j]=5;fd=build(fy,30);}
codes(fl,fd);}
else if(type===2){
var nl=bits(5)+257,nd=bits(5)+1,nc=bits(4)+4,lens=new Uint8Array(320);
for(j=0;j<nc;j++)lens[CO[j]]=bits(3);
var ch=build(lens.subarray(0,19),19);lens=new Uint8Array(320);j=0;
while(j<nl+nd){t=dec(ch);
if(t<16)lens[j++]=t;
else{var rep,v=0;if(t===16){v=lens[j-1];rep=3+bits(2);}else if(t===17)rep=3+bits(3);else rep=11+bits(7);
while(rep--)lens[j++]=v;}}
codes(build(lens.subarray(0,nl),nl),build(lens.subarray(nl,nl+nd),nd));}
else throw new Error("padb-unz: bad block");
}while(!last);
if(op!==n)throw new Error("padb-unz: length mismatch "+op+" vs "+n);
return JSON.parse(new TextDecoder("utf-8").decode(out));}
"""

_ASSIGN = re.compile(r"(?m)^([ \t]*)var ([A-Za-z_$][\w$]*)=(?=[\[{])")
_NOT_JSON_TOKENS = re.compile(r"\bNaN\b|\bInfinity\b|\bundefined\b|\bfunction\b|=>|\bnew\s")
_STMT_BOUNDARY = re.compile(r"[\]}];\s*(?:var|let|const|function)\b")


def _is_json(payload: str) -> bool:
    """True only when `payload` is a JSON text JS would JSON.parse identically."""
    if payload[0] not in "[{" or payload[-1] not in "]}":
        return False
    if len(payload) <= MAX_VALIDATE:
        try:
            json.loads(payload, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
            return True
        except (ValueError, RecursionError, MemoryError):
            return False
    # very large: strict lexical screen (it came from json.dumps of plain data)
    return not (_NOT_JSON_TOKENS.search(payload) or _STMT_BOUNDARY.search(payload))


def compress_html(path, min_payload_bytes: int = MIN_PAYLOAD_BYTES, level: int = 6) -> dict:
    """Rewrite the very large `var NAME=<json>;` constants of the page at `path` in place.
    Returns {"changed": bool, "before": bytes, "after": bytes, "n": constants rewritten}."""
    p = Path(path)
    with open(p, "r", encoding="utf-8", newline="") as fh:
        text = fh.read()
    before = len(text.encode("utf-8")) if len(text) < 1 << 28 else p.stat().st_size
    if MARKER in text[:200_000]:
        return {"changed": False, "before": before, "after": before, "n": 0, "names": []}

    out, pos, n, names = [], 0, 0, []
    for m in _ASSIGN.finditer(text):
        if m.start() < pos:
            continue
        eol = text.find("\n", m.end())
        eol = len(text) if eol < 0 else eol
        line_end = eol - 1 if eol > m.end() and text[eol - 1] == "\r" else eol
        if line_end - m.end() < min_payload_bytes or text[line_end - 1] != ";":
            continue
        payload = text[m.end():line_end - 1]
        raw = payload.encode("utf-8")
        if len(raw) > MAX_RAW_BYTES or not _is_json(payload):
            continue
        co = zlib.compressobj(level, zlib.DEFLATED, -15)
        z = co.compress(raw) + co.flush()
        b64 = base64.b64encode(z).decode("ascii")
        if len(b64) >= len(payload) * 0.7:           # not worth it (already dense)
            continue
        out.append(text[pos:m.end()])
        out.append('PADB_unz("%s",%d);' % (b64, len(raw)))
        pos = line_end
        n += 1
        names.append(m.group(2))
    if not n:
        return {"changed": False, "before": before, "after": before, "n": 0, "names": []}
    out.append(text[pos:])
    new = "".join(out)
    head = new.find("<head>")
    ins = "<script>" + UNZ_JS + "</script>"
    new = (new[:head + 6] + ins + new[head + 6:]) if head >= 0 else (ins + new)
    tmp = p.with_name(p.name + ".part")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(new)
    tmp.replace(p)
    return {"changed": True, "before": before, "after": p.stat().st_size, "n": n, "names": names}
