"""Consistency check of the specs against each other and the schemas (run from anywhere; exits non-zero on any problem)."""
import re,glob,os,json,tomllib,yaml,subprocess,sys
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)),"..","docs","specs"))
from jsonschema import Draft202012Validator as V
R="../../"; ok=True
def bad(*a):
    global ok; ok=False; print("PROBLEM:",*a)
files=sorted(glob.glob("SPEC-*.adoc")); ids=[re.match(r"SPEC-(\d\d)",f)[1] for f in files]
if ids!=[f"{i:02d}" for i in range(13)]: bad("ids not contiguous",ids)
allt={}
for pat in ("**/*",R+"schemas/*",R+"examples/*",R+"README.md"):
    for f in glob.glob(pat,recursive=True):
        if os.path.isfile(f) and not f.endswith(".pyc"):
            try: allt[f]=open(f).read()
            except UnicodeDecodeError: pass
for f,t in allt.items():
    for m in re.finditer(r"SPEC-(\d\d)",t):
        if m[1] not in ids: bad("unknown spec id",f,m[0])
heads={re.match(r"SPEC-(\d\d)",f)[1]:{m[1] for m in re.finditer(r"^=+ (\d+(?:\.\d+)*)\.? ",open(f).read(),re.M)} for f in files}
for f,t in allt.items():
    for m in re.finditer(r"`SPEC-(\d\d)`\s*(?:section |§)(\d+(?:\.\d+)*)",t):
        if m[2] not in heads[m[1]]: bad("xref",f,m[0])
for f in glob.glob("*.adoc"):
    for inc in re.findall(r"^include::(\S+?)\[",open(f).read(),re.M):
        if not os.path.exists(inc): bad("include",f,inc)
idx=open("INDEX.adoc").read()
rows=list(re.finditer(r"\| `(SPEC-\d\d)`\n\| link:(\S+?)\[(.*?)\]\n\| (\w+) \| ([\d.]+)",idx))
if [r[1][5:] for r in rows]!=ids: bad("index rows vs files")
for r in rows:
    sid,f,title,st,ver=r.groups(); t=open(f).read() if os.path.exists(f) else ""
    if not (t.startswith("= "+sid) and f"\n:status: {st}\n" in t and f"\n:version: {ver}\n" in t): bad("index row",sid)
P=r"ERR_[A-Z]{2,3}_[0-9]{3}"
used={c for f in files for c in re.findall(P,open(f).read())}
t4=open("SPEC-04-cli-toolchain.adoc").read(); reg=set(re.findall(r"^\| `("+P+")`",t4,re.M))
if used-reg: bad("unregistered",used-reg)
for c in reg:
    if not any(c in open(f).read() for f in files if not f.startswith("SPEC-04")): bad("uncited",c)
for line in t4.split("\n"):
    m=re.match(r"\| `("+P+r")` \|.* \| ((?:\d\d)(?:, \d\d)*)$",line)
    if m:
        for n in m[2].split(", "):
            if n not in ids: bad("registry spec col",m[1],n)
            elif m[1] not in open(glob.glob(f"SPEC-{n}-*.adoc")[0]).read(): bad("registry lists spec",n,"which never cites",m[1])
sch=json.load(open(R+"schemas/node_manifest.schema.json")); V.check_schema(sch); V.check_schema(json.load(open(R+"schemas/node_ir.schema.json"))); v=V(sch)
for f in sorted(glob.glob(R+"examples/*.toml")):
    e=[x.message[:70] for x in v.iter_errors(tomllib.load(open(f,"rb")))]
    if e: bad("example",f,e)
n=0
for f in files:
    for m in re.finditer(r"\[source,toml\]\n----\n(.*?)\n----",open(f).read(),re.S):
        if m.group(1).startswith("include::"): continue
        d=tomllib.loads(m.group(1))
        if "node" in d:
            n+=1; e=[x.message[:70] for x in v.iter_errors(d)]
            if e: bad("embedded",f,e)
    for m in re.finditer(r"\[source,(yaml|json)\]\n----\n(.*?)\n----",open(f).read(),re.S):
        if m.group(2).startswith("include::"): continue
        try: (yaml.safe_load if m.group(1)=="yaml" else json.loads)(m.group(2))
        except Exception as ex: bad("parse",f,str(ex)[:50])
owner={"lifecycle":("LifecycleExtension","05"),"shared_memory":("SharedMemoryExtension","06"),"security":("SecurityExtension","08"),"telemetry":("TelemetryExtension","09"),"realtime":("RealtimeExtension","10"),"simulation":("SimulationExtension","11"),"concurrency":("ConcurrencyExtension","12")}
t1=open("SPEC-01-user-schema-syntax.adoc").read()
for blk,(d,sid) in owner.items():
    txt=open(glob.glob(f"SPEC-{sid}-*.adoc")[0]).read()
    for k in sch["$defs"][d]["properties"]:
        if not re.search(r"\b"+k+r"\b",txt): bad("key not in spec",d,k)
    for name,t2 in (("SPEC-01",t1),("INDEX",idx)):
        if not re.search(r"\| `"+blk+r"` \| `SPEC-"+sid+"`",t2): bad("owner mismatch",name,blk,sid)
    if f"`$defs/{d}`" not in txt: bad("spec does not cite its $defs",sid,d)
print("specs 00..%s | embedded manifests: %d | codes: %d | examples: %d"%(ids[-1],n,len(reg),len(glob.glob(R+"examples/*.toml"))))
print("ALL CONSISTENT" if ok else "ISSUES FOUND")
sys.exit(0 if ok else 1)
