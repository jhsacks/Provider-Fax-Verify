import os, re, json, secrets, uuid, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import streamlit as st
from drive_store import DriveStore, normalize

st.set_page_config(page_title="Provider Fax Verify", page_icon="📠", layout="centered")
ADMIN_PASSWORD=os.getenv("ADMIN_PASSWORD","").strip(); ROOT=Path(__file__).resolve().parent; LEGACY=ROOT/"legacy_providers.csv"
SERVICENOW_URL="https://wellstar.service-now.com/esc?id=sc_cat_item&sys_id=ba15cfea839647d4b80fc0d6feaad316&table=sc_cat_item&searchTerm=update%20fax%20number%20provider"
NPI_SEARCH_URL="https://npiregistry.cms.hhs.gov/search"; NPI_API_URL="https://npiregistry.cms.hhs.gov/api/"

def digits(v): return re.sub(r"\D","",str(v or ""))[-10:]
def fmt(v):
 d=digits(v); return f"{d[:3]}-{d[3:6]}-{d[6:]}" if len(d)==10 else ""
def clean(v): return re.sub(r"[^a-z0-9]","",str(v or "").lower())
def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
def status(v):
 v=clean(v)
 return "EPIC_CORRECT" if v in ("epiccorrect","correct","green") else ("EPIC_INCORRECT" if v in ("epicincorrect","incorrect","missing","red") else "VERIFY_NEEDED")
@st.cache_resource
def store(): return DriveStore()
@st.cache_data(ttl=300,show_spinner=False)
def load_data(): return store().load()
try: STORE=store(); DATA=load_data(); OK=True; ERR=""
except Exception as e: STORE=None; DATA=normalize({}); OK=False; ERR=str(e)

def rebuild():
 global PRACTICES,PROVIDERS
 PRACTICES={x.get("id"):x for x in DATA.get("practices",[])}; PROVIDERS={x.get("id"):x for x in DATA.get("providers",[])}
rebuild()
def practice(pid): return PRACTICES.get(pid,{})
def persist(action,entity=""):
 global DATA
 DATA.setdefault("audit",[]).append({"at":now(),"action":action,"entity":entity}); DATA=STORE.save(DATA); load_data.clear(); rebuild()
def pkey(name,pname): return clean(name)+"|"+clean(pname)

def add_memory(row):
 name=str(row.get("provider_name","")).strip(); pname=str(row.get("practice_name","")).strip() or "Practice not recorded"; city=str(row.get("city","")).strip()
 if not name:return "skipped"
 pr=next((x for x in DATA["practices"] if clean(x.get("name"))==clean(pname) and (not city or clean(x.get("city"))==clean(city))),None)
 if not pr:
  pr={"id":str(uuid.uuid4()),"name":pname,"city":city,"state":str(row.get("state","")).strip(),"fax":digits(row.get("correct_fax")),"phone":digits(row.get("phone")),"active":True}; DATA["practices"].append(pr); rebuild()
 existing=next((x for x in DATA["providers"] if pkey(x.get("provider_name"),practice(x.get("practice_id")).get("name"))==pkey(name,pname)),None)
 payload={"provider_name":name,"credentials":str(row.get("credentials","")).strip(),"practice_id":pr["id"],"npi":digits(row.get("npi")),"epic_status":status(row.get("epic_status")),"epic_fax":digits(row.get("epic_fax")),"correct_fax":digits(row.get("correct_fax")),"source_url":str(row.get("source_url","")).strip(),"notes":str(row.get("notes","")).strip(),"active":True}
 if existing:
  for k,v in payload.items():
   if v not in ("",None):existing[k]=v
  rebuild(); return "updated"
 payload["id"]=str(uuid.uuid4()); DATA["providers"].append(payload); rebuild(); return "added"
def import_df(df,label):
 counts={"added":0,"updated":0,"skipped":0}
 for _,row in df.fillna("").iterrows():counts[add_memory(row.to_dict())]+=1
 persist(label,json.dumps(counts)); return counts

def split_name(name):
 base=re.sub(r",?\s*(MD|DO|NP|APRN|PA-C|PA|FNP-C|FNP|CPNP|DNP|FAAP)\b.*$","",str(name or ""),flags=re.I).strip()
 if "," in base:
  last,first=[x.strip() for x in base.split(",",1)]; return (first.split()[0] if first else "",last)
 parts=base.split(); return ((parts[0],parts[-1]) if len(parts)>1 else (base,""))
def npi_lookup(p):
 first,last=split_name(p.get("provider_name")); pr=practice(p.get("practice_id")); params={"version":"2.1","limit":"10","enumeration_type":"NPI-1"}
 if p.get("npi"):params["number"]=p["npi"]
 else:
  if first:params["first_name"]=first
  if last:params["last_name"]=last
  if pr.get("state"):params["state"]=pr["state"]
 req=urllib.request.Request(NPI_API_URL+"?"+urllib.parse.urlencode(params),headers={"User-Agent":"ProviderFaxVerify/1.0"})
 with urllib.request.urlopen(req,timeout=15) as response: raw=json.loads(response.read().decode())
 out=[]
 for result in raw.get("results",[]):
  for addr in result.get("addresses",[]) or []:out.append({"npi":str(result.get("number","")),"fax":digits(addr.get("fax_number")),"city":addr.get("city", ""),"state":addr.get("state","")})
 return out
def verify(p):
 found=npi_lookup(p); stored=digits(p.get("correct_fax")); faxes=sorted({x["fax"] for x in found if x.get("fax")}); npis={x["npi"] for x in found if x.get("npi")}
 p.update({"last_verified":now(),"verification_source":"NPPES API 2.1","registry_faxes":faxes})
 if not p.get("npi") and len(npis)==1:p["npi"]=next(iter(npis))
 outcome="FAX_MATCH" if stored and stored in faxes else ("REVIEW_NEEDED" if faxes else "NO_FAX_FOUND"); p["verification_result"]=outcome
 if outcome=="REVIEW_NEEDED":
  for fax in faxes:
   if not any(x.get("provider_id")==p.get("id") and x.get("found_fax")==fax and x.get("status","pending")=="pending" for x in DATA.setdefault("review_queue",[])):
    DATA["review_queue"].append({"id":str(uuid.uuid4()),"provider_id":p.get("id"),"current_fax":stored,"found_fax":fax,"source":"NPPES API 2.1","status":"pending","checked_at":now()})
 persist("npi_verification",p.get("id")); return outcome

def search(q,limit=12):
 n=clean(q)
 if len(n)<2:return []
 out=[]
 for p in DATA.get("providers",[]):
  if not p.get("active",True):continue
  pr=practice(p.get("practice_id")); pname=p.get("provider_name",""); practice_name=pr.get("name",""); hay=clean(" ".join([pname,practice_name,p.get("npi",""),p.get("correct_fax","")]))
  if n in hay:out.append((0 if clean(pname).startswith(n) else 1,f"{pname}"+(f" — {practice_name}" if practice_name!="Practice not recorded" else ""),"provider",p.get("id")))
 for pr in DATA.get("practices",[]):
  if pr.get("name")!="Practice not recorded" and n in clean(pr.get("name")):out.append((0 if clean(pr.get("name")).startswith(n) else 1,f"Practice: {pr.get('name')}","practice",pr.get("id")))
 return sorted(out,key=lambda x:(x[0],clean(x[1])))[:limit]

def card(p):
 pr=practice(p.get("practice_id")); s=status(p.get("epic_status")); display=p.get("provider_name","")+(f", {p.get('credentials')}" if p.get("credentials") else ""); pn=pr.get("name","") if pr.get("name")!="Practice not recorded" else ""
 if s=="EPIC_CORRECT":st.markdown(f"<div class='card green'><div class='status'>✅ FAX IN EPIC CORRECT</div><h2>{display}</h2><div class='muted'>{pn}</div><div class='route'>Route normally through Epic.</div></div>",unsafe_allow_html=True)
 elif s=="EPIC_INCORRECT":
  fax=fmt(p.get("correct_fax")); st.markdown(f"<div class='card red'><div class='status'>🔴 FAX IN EPIC INCORRECT</div><h2>{display}</h2><div class='muted'>{pn}</div><div class='use'>USE THIS FAX</div><div class='fax'>{fax or 'FAX NEEDED'}</div></div>",unsafe_allow_html=True)
  if fax:st.code(fax,language=None)
 else:st.markdown(f"<div class='card yellow'><div class='status'>🟡 EPIC FAX NEEDS VERIFICATION</div><h2>{display}</h2><div class='muted'>{pn}</div></div>",unsafe_allow_html=True)
 a,b=st.columns(2); a.link_button("Submit Epic fax update",SERVICENOW_URL,use_container_width=True); b.link_button("Open NPI Registry",NPI_SEARCH_URL,use_container_width=True)
 if st.button("Verify with NPI Registry now",key=f"verify_{p.get('id')}",use_container_width=True):
  try:
   with st.spinner("Checking the NPI Registry..."):result=verify(p)
   if result=="FAX_MATCH":st.success("The stored fax matches a fax returned by the NPI Registry.")
   elif result=="REVIEW_NEEDED":st.warning("A different registry fax was found and added to Admin Review. Epic status was not changed.")
   else:st.info("The NPI Registry did not return a fax. No record was changed.")
  except Exception as e:st.error(f"NPI verification could not be completed: {e}")
 if p.get("last_verified"):st.caption(f"Last NPI check: {p.get('last_verified')} • {p.get('verification_result','')}")

st.markdown("""<style>.block-container{max-width:860px;padding-top:2rem}.card{border:1px solid #dce2e8;border-radius:18px;padding:25px;margin:14px 0;background:#fff;box-shadow:0 3px 14px #0000000d}.red{border-left:14px solid #c93545}.green{border-left:14px solid #20894e}.yellow{border-left:14px solid #d99b00}.fax{font-size:44px;font-weight:900}.status{font-size:25px;font-weight:900}.route{font-size:20px;font-weight:700;margin-top:14px}.use{font-size:18px;font-weight:850;margin-top:14px}.muted{color:#66727f}.stButton button{min-height:44px;font-weight:750;text-align:left}</style>""",unsafe_allow_html=True)
st.title("📠 Provider Fax Verify"); st.caption("☁️ Google Drive saving is on" if OK else "")
if not OK:st.error("Saving is not connected. "+ERR)
for key in ("selected_kind","selected_id"):
 if key not in st.session_state:st.session_state[key]=""
q=st.text_input("Search provider or practice",key="provider_search",placeholder="Start typing a provider or practice...")
if q:
 matches=search(q)
 if not matches:
  st.warning("No matching provider or practice found."); a,b=st.columns(2); a.link_button("Submit provider/fax request",SERVICENOW_URL,use_container_width=True); b.link_button("Search NPI Registry",NPI_SEARCH_URL,use_container_width=True)
 for i,(_,label,kind,item_id) in enumerate(matches):
  if st.button(label,key=f"m_{kind}_{item_id}_{i}",use_container_width=True):st.session_state.selected_kind=kind;st.session_state.selected_id=item_id;st.rerun()
kind,item_id=st.session_state.selected_kind,st.session_state.selected_id
if kind=="provider" and item_id in PROVIDERS:card(PROVIDERS[item_id])
elif kind=="practice" and item_id in PRACTICES:
 st.subheader(PRACTICES[item_id].get("name")); [card(p) for p in sorted([x for x in DATA["providers"] if x.get("practice_id")==item_id],key=lambda x:clean(x.get("provider_name")))]
if q or item_id:
 if st.button("Clear search",use_container_width=True):st.session_state.provider_search="";st.session_state.selected_kind="";st.session_state.selected_id="";st.rerun()
if "notice" in st.session_state:st.success(st.session_state.pop("notice"))
with st.expander("Add a provider not on the list"):
 with st.form("add",clear_on_submit=True):
  a,b=st.columns(2); provider_name=a.text_input("Provider name*"); credentials=b.text_input("Credentials"); practice_name=st.text_input("Practice name*"); epic_status=st.radio("Epic fax status",["EPIC_CORRECT","EPIC_INCORRECT","VERIFY_NEEDED"],horizontal=True); correct_fax=st.text_input("Correct fax (required if Epic is incorrect)"); c,d=st.columns(2); city=c.text_input("City");state=d.text_input("State",value="GA")
  if st.form_submit_button("Add provider",use_container_width=True,disabled=not OK):
   if not provider_name or not practice_name:st.error("Provider and practice are required.")
   elif epic_status=="EPIC_INCORRECT" and len(digits(correct_fax))!=10:st.error("A 10-digit correct fax is required.")
   else:add_memory(locals());persist("provider_added",provider_name);st.session_state.notice="Provider saved. The form was cleared.";st.rerun()
with st.sidebar:
 st.header("Admin"); password=st.text_input("Admin password",type="password")
 if ADMIN_PASSWORD and secrets.compare_digest(password,ADMIN_PASSWORD):
  st.success("Admin unlocked")
  if LEGACY.exists() and st.button("Load built-in provider directory",use_container_width=True,disabled=not OK):counts=import_df(pd.read_csv(LEGACY,dtype=str),"legacy_import");st.success(str(counts));st.rerun()
  up=st.file_uploader("Import CSV or XLSX",type=["csv","xlsx"])
  if up:
   df=(pd.read_csv(up,dtype=str) if up.name.lower().endswith(".csv") else pd.read_excel(up,dtype=str)).fillna("");st.dataframe(df.head(8),use_container_width=True)
   if st.button("Import uploaded file",use_container_width=True):counts=import_df(df,"uploaded_import");st.success(str(counts));st.rerun()
  st.subheader("NPI verification"); batch=st.number_input("Providers to check",1,50,10)
  if st.button("Auto-check next providers",use_container_width=True):
   items=sorted([x for x in DATA["providers"] if x.get("active",True)],key=lambda x:x.get("last_verified",""))[:int(batch)];counts={"FAX_MATCH":0,"REVIEW_NEEDED":0,"NO_FAX_FOUND":0,"ERROR":0}
   for p in items:
    try:counts[verify(p)]+=1
    except Exception:counts["ERROR"]+=1
   st.success(f"Checked {len(items)}: {counts}");st.rerun()
  pending=[x for x in DATA.get("review_queue",[]) if x.get("status","pending")=="pending"];st.subheader(f"Verification review ({len(pending)})")
  for item in pending[:50]:
   p=PROVIDERS.get(item.get("provider_id"),{});st.write(f"**{p.get('provider_name','Provider')}**");st.write(f"Stored: {fmt(item.get('current_fax')) or 'none'} | Registry: {fmt(item.get('found_fax'))}");a,b=st.columns(2)
   if a.button("Accept registry fax",key=f"a_{item['id']}"):p["correct_fax"]=digits(item["found_fax"]);item["status"]="accepted";persist("fax_accepted",item["id"]);st.rerun()
   if b.button("Keep stored fax",key=f"r_{item['id']}"):item["status"]="rejected";persist("fax_rejected",item["id"]);st.rerun()
  rows=[]
  for p in DATA["providers"]:
   pr=practice(p.get("practice_id"));rows.append({"provider_name":p.get("provider_name"),"credentials":p.get("credentials"),"practice_name":pr.get("name"),"city":pr.get("city"),"state":pr.get("state"),"npi":p.get("npi"),"epic_status":status(p.get("epic_status")),"correct_fax":fmt(p.get("correct_fax")),"epic_fax":fmt(p.get("epic_fax")),"phone":fmt(pr.get("phone")),"last_verified":p.get("last_verified","")})
  export=pd.DataFrame(rows);st.metric("Providers",len(export));st.download_button("Download directory",export.to_csv(index=False).encode(),"provider_fax_directory.csv","text/csv",use_container_width=True);st.download_button("Download JSON backup",json.dumps(DATA,indent=2).encode(),"provider_fax_data.json","application/json",use_container_width=True)
 elif password:st.error("Incorrect password")
