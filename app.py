import os, re, io, sqlite3, hashlib, secrets
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import requests
from bs4 import BeautifulSoup
import streamlit as st
from apscheduler.schedulers.background import BackgroundScheduler

st.set_page_config(page_title="Provider Fax Verify", page_icon="📠", layout="centered")
DATA_DIR=Path(os.getenv("DATA_DIR", ".")); DATA_DIR.mkdir(parents=True, exist_ok=True)
DB=DATA_DIR/"provider_fax.db"
ADMIN_PASSWORD=os.getenv("ADMIN_PASSWORD", "change-me")
FAX_RE=re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?([2-9]\d{2})\)?[ .-]?(\d{3})[ .-]?(\d{4})(?!\d)")

def db():
    c=sqlite3.connect(DB, check_same_thread=False); c.row_factory=sqlite3.Row; return c

def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
def digits(v): return re.sub(r"\D", "", str(v or ""))[-10:]
def fmt(v):
    d=digits(v); return f"{d[:3]}-{d[3:6]}-{d[6:]}" if len(d)==10 else str(v or "")
def norm(v): return re.sub(r"[^a-z0-9]", "", str(v or "").lower())

def init_db():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS practices(id INTEGER PRIMARY KEY, name TEXT NOT NULL, city TEXT, state TEXT, phone TEXT, fax TEXT, source_url TEXT, active INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS providers(id INTEGER PRIMARY KEY, provider_name TEXT NOT NULL, credentials TEXT, practice_id INTEGER, npi TEXT, epic_status TEXT DEFAULT 'unknown', epic_fax TEXT, correct_fax TEXT NOT NULL, notes TEXT, active INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT, FOREIGN KEY(practice_id) REFERENCES practices(id));
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, entity_type TEXT, entity_id INTEGER, action TEXT, old_value TEXT, new_value TEXT, actor TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS review_queue(id INTEGER PRIMARY KEY, provider_id INTEGER, found_fax TEXT, current_fax TEXT, source TEXT, source_url TEXT, status TEXT DEFAULT 'pending', checked_at TEXT, resolved_at TEXT, resolution TEXT, FOREIGN KEY(provider_id) REFERENCES providers(id));
        CREATE INDEX IF NOT EXISTS idx_provider_name ON providers(provider_name);
        CREATE INDEX IF NOT EXISTS idx_practice_name ON practices(name);
        ''')
        n=c.execute("SELECT COUNT(*) n FROM providers").fetchone()[0]
        if n==0:
            seed=[("Cumberland Pediatrics","Marietta","GA","7709515400","7707025627",None,"Peter Strauss","MD","incorrect","7709515400","7707025627"),("Cumberland Pediatrics","Marietta","GA","7709515400","7707025627",None,"Alice Wilson","MD","incorrect","","7707025627"),("East Cobb Pediatrics","Marietta","GA","7709770094","7705099463",None,"Karen Thrower","MD","missing","","7705099463")]
            for practice,city,state,phone,pfax,url,pname,cred,status,epic,correct in seed:
                pid=upsert_practice(c,practice,city,state,phone,pfax,url)
                c.execute("INSERT INTO providers(provider_name,credentials,practice_id,epic_status,epic_fax,correct_fax,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(pname,cred,pid,status,epic,correct,now(),now()))

def upsert_practice(c,name,city='',state='',phone='',fax='',source_url=''):
    row=c.execute("SELECT id FROM practices WHERE lower(name)=lower(?) AND coalesce(city,'')=coalesce(?, '')",(name,city)).fetchone()
    if row:
        c.execute("UPDATE practices SET state=coalesce(nullif(?,''),state),phone=coalesce(nullif(?,''),phone),fax=coalesce(nullif(?,''),fax),source_url=coalesce(nullif(?,''),source_url),updated_at=? WHERE id=?",(state,digits(phone),digits(fax),source_url,now(),row[0])); return row[0]
    cur=c.execute("INSERT INTO practices(name,city,state,phone,fax,source_url,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(name,city,state,digits(phone),digits(fax),source_url,now(),now())); return cur.lastrowid

def add_provider(data, actor="user"):
    with db() as c:
        pid=upsert_practice(c,data['practice_name'],data.get('city',''),data.get('state',''),data.get('phone',''),data.get('correct_fax',''),data.get('source_url',''))
        cur=c.execute("INSERT INTO providers(provider_name,credentials,practice_id,npi,epic_status,epic_fax,correct_fax,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(data['provider_name'],data.get('credentials',''),pid,digits(data.get('npi','')),data.get('epic_status','unknown'),digits(data.get('epic_fax','')),digits(data['correct_fax']),data.get('notes',''),now(),now()))
        c.execute("INSERT INTO audit(entity_type,entity_id,action,new_value,actor,created_at) VALUES('provider',?,'created',?,?,?)",(cur.lastrowid,data['correct_fax'],actor,now()))

def search(q):
    key=f"%{q.strip()}%"; d=digits(q)
    with db() as c:
        return c.execute('''SELECT p.*,pr.name practice_name,pr.city,pr.state,pr.phone practice_phone,pr.fax practice_fax,pr.source_url FROM providers p JOIN practices pr ON pr.id=p.practice_id WHERE p.active=1 AND pr.active=1 AND (p.provider_name LIKE ? OR pr.name LIKE ? OR pr.city LIKE ? OR p.npi LIKE ? OR p.correct_fax LIKE ? OR pr.fax LIKE ?) ORDER BY CASE WHEN lower(p.provider_name)=lower(?) THEN 0 WHEN lower(pr.name)=lower(?) THEN 1 ELSE 2 END,pr.name,p.provider_name LIMIT 40''',(key,key,key,key,f"%{d}%",f"%{d}%",q.strip(),q.strip())).fetchall()

def extract_faxes(text): return sorted({''.join(m) for m in FAX_RE.findall(text or '')})
def check_provider(row):
    found=[]; sources=[]
    if row['npi']:
        try:
            r=requests.get("https://npiregistry.cms.hhs.gov/api/",params={"version":"2.1","number":row['npi']},timeout=12); r.raise_for_status()
            for result in r.json().get('results',[]):
                for ep in result.get('endpoints',[]) or []:
                    for k,v in ep.items():
                        if 'fax' in k.lower(): found += extract_faxes(str(v))
                for a in result.get('addresses',[]) or []:
                    if a.get('fax_number'): found.append(digits(a['fax_number']))
            sources.append("NPPES")
        except Exception: pass
    if row['source_url'] and str(row['source_url']).startswith(('http://','https://')):
        try:
            r=requests.get(row['source_url'],timeout=12,headers={'User-Agent':'Mozilla/5.0'}); r.raise_for_status(); found += extract_faxes(BeautifulSoup(r.text,'html.parser').get_text(' ')); sources.append("Official source URL")
        except Exception: pass
    found={digits(x) for x in found if len(digits(x))==10}; current=digits(row['correct_fax'])
    alternatives=sorted(found-{current})
    if alternatives and current not in found:
        with db() as c:
            exists=c.execute("SELECT 1 FROM review_queue WHERE provider_id=? AND found_fax=? AND status='pending'",(row['id'],alternatives[0])).fetchone()
            if not exists: c.execute("INSERT INTO review_queue(provider_id,found_fax,current_fax,source,source_url,checked_at) VALUES(?,?,?,?,?,?)",(row['id'],alternatives[0],current,', '.join(sources),row['source_url'],now()))
    return bool(found), alternatives

def weekly_check():
    with db() as c: rows=c.execute("SELECT p.*,pr.source_url FROM providers p JOIN practices pr ON pr.id=p.practice_id WHERE p.active=1").fetchall()
    for r in rows: check_provider(r)

@st.cache_resource
def scheduler():
    s=BackgroundScheduler(daemon=True); s.add_job(weekly_check,'cron',day_of_week='sun',hour=3,minute=0,id='weekly',replace_existing=True); s.start(); return s

init_db(); scheduler()
st.markdown('''<style>
.block-container{max-width:800px;padding-top:2rem}.faxbox{border-radius:18px;padding:24px;margin:12px 0;background:#fff;border:1px solid #dde3ea;box-shadow:0 4px 16px rgba(0,0,0,.06)}
.green{border-left:12px solid #1d8f4e}.red{border-left:12px solid #cc3340}.yellow{border-left:12px solid #e1a900}.fax{font-size:42px;font-weight:800;letter-spacing:1px;margin:8px 0}.muted{color:#5f6b76}.status{font-weight:800;font-size:18px}.stButton button{min-height:48px;font-weight:700}
</style>''',unsafe_allow_html=True)

st.title("📠 Provider Fax Verify")
st.caption("Search by provider, practice, city, fax number, or NPI.")
q=st.text_input("Search",placeholder="Provider or practice name",label_visibility="collapsed")
if q.strip():
    rows=search(q)
    if not rows: st.warning("Provider or practice not found.")
    else:
        grouped={}
        for r in rows: grouped.setdefault(r['practice_name'],[]).append(r)
        for practice,items in grouped.items():
            if len(items)>1 and norm(q) in norm(practice):
                fax=next((x['practice_fax'] or x['correct_fax'] for x in items if x['practice_fax'] or x['correct_fax']),"")
                st.markdown(f'''<div class="faxbox green"><div class="status">{practice}</div><div class="fax">{fmt(fax)}</div><div class="muted">{', '.join(x['provider_name'] for x in items)}</div></div>''',unsafe_allow_html=True)
                st.code(fmt(fax),language=None)
            else:
                for r in items:
                    bad=r['epic_status'] in ('incorrect','missing'); cls='red' if bad else 'green'; label='EPIC NUMBER INCORRECT — USE THIS FAX' if r['epic_status']=='incorrect' else ('FAX MISSING IN EPIC — USE THIS FAX' if r['epic_status']=='missing' else 'VERIFIED FAX')
                    st.markdown(f'''<div class="faxbox {cls}"><div class="status">{'🔴' if bad else '✅'} {label}</div><h3>{r['provider_name']}{', '+r['credentials'] if r['credentials'] else ''}</h3><div class="muted">{r['practice_name']}</div><div class="fax">{fmt(r['correct_fax'])}</div></div>''',unsafe_allow_html=True)
                    st.code(fmt(r['correct_fax']),language=None)
                    if st.button("Copy fax",key=f"copy{r['id']}",use_container_width=True):
                        st.components.v1.html(f"<script>navigator.clipboard.writeText('{fmt(r['correct_fax'])}');</script>",height=0); st.success("Fax copied")

with st.expander("Add a provider not on the list"):
    with st.form("add"):
        c1,c2=st.columns(2); pname=c1.text_input("Provider name*"); cred=c2.text_input("Credentials"); practice=st.text_input("Practice name*"); fax=st.text_input("Correct fax*"); c3,c4=st.columns(2); city=c3.text_input("City"); state=c4.text_input("State",value="GA"); source=st.text_input("Official source URL, if known"); submitted=st.form_submit_button("Add provider",use_container_width=True)
        if submitted:
            if not pname or not practice or len(digits(fax))!=10: st.error("Provider, practice, and a 10-digit fax are required.")
            else: add_provider({'provider_name':pname,'credentials':cred,'practice_name':practice,'correct_fax':fax,'city':city,'state':state,'source_url':source,'epic_status':'unknown'}); st.success("Provider added and ready to search."); st.rerun()

with st.sidebar:
    st.header("Admin")
    password=st.text_input("Admin password",type="password")
    if secrets.compare_digest(password,ADMIN_PASSWORD):
        st.success("Admin unlocked")
        tab1,tab2,tab3=st.tabs(["Import","Review","Export"])
        with tab1:
            up=st.file_uploader("Import CSV or XLSX",type=['csv','xlsx'])
            if up:
                df=pd.read_csv(up,dtype=str).fillna('') if up.name.endswith('.csv') else pd.read_excel(up,dtype=str).fillna('')
                st.dataframe(df.head(10),use_container_width=True)
                if st.button("Import rows"):
                    need={'provider_name','practice_name','correct_fax'}
                    if not need.issubset(df.columns): st.error("Missing required columns: "+', '.join(sorted(need-set(df.columns))))
                    else:
                        count=0
                        for _,x in df.iterrows():
                            if x['provider_name'] and x['practice_name'] and len(digits(x['correct_fax']))==10: add_provider(x.to_dict(),"admin import"); count+=1
                        st.success(f"Imported {count} providers")
            if st.button("Run source check now",use_container_width=True): weekly_check(); st.success("Source comparison completed. Review any differences in the Review tab.")
        with tab2:
            with db() as c: review=c.execute('''SELECT q.*,p.provider_name,pr.name practice_name FROM review_queue q JOIN providers p ON p.id=q.provider_id JOIN practices pr ON pr.id=p.practice_id WHERE q.status='pending' ORDER BY q.checked_at DESC''').fetchall()
            if not review: st.info("No pending possible changes.")
            for x in review:
                st.write(f"**{x['provider_name']} — {x['practice_name']}**"); st.write(f"Current: {fmt(x['current_fax'])} | Found: {fmt(x['found_fax'])} | Source: {x['source']}")
                a,b=st.columns(2)
                if a.button("Accept",key=f"a{x['id']}"):
                    with db() as c: c.execute("UPDATE providers SET correct_fax=?,updated_at=? WHERE id=?",(x['found_fax'],now(),x['provider_id'])); c.execute("UPDATE review_queue SET status='resolved',resolved_at=?,resolution='accepted' WHERE id=?",(now(),x['id']))
                    st.rerun()
                if b.button("Reject",key=f"r{x['id']}"):
                    with db() as c: c.execute("UPDATE review_queue SET status='resolved',resolved_at=?,resolution='rejected' WHERE id=?",(now(),x['id']))
                    st.rerun()
        with tab3:
            with db() as c: out=pd.read_sql_query('''SELECT p.provider_name,p.credentials,pr.name practice_name,pr.city,pr.state,p.npi,p.epic_status,p.correct_fax,p.epic_fax,pr.phone,pr.source_url,p.notes FROM providers p JOIN practices pr ON pr.id=p.practice_id WHERE p.active=1 ORDER BY pr.name,p.provider_name''',c)
            st.download_button("Download current directory",out.to_csv(index=False).encode(),"provider_fax_directory.csv","text/csv",use_container_width=True)
    elif password: st.error("Incorrect password")
