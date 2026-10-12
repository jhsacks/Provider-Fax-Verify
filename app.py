import os, re, json, secrets, uuid
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import streamlit as st
from drive_store import DriveStore, normalize

st.set_page_config(page_title='Provider Fax Verify', page_icon='📠', layout='centered')
ADMIN_PASSWORD=os.getenv('ADMIN_PASSWORD','').strip()
ROOT=Path(__file__).resolve().parent
LEGACY=ROOT/'legacy_providers.csv'

def digits(v): return re.sub(r'\D','',str(v or ''))[-10:]
def fmt(v):
 d=digits(v); return f'{d[:3]}-{d[3:6]}-{d[6:]}' if len(d)==10 else ''
def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def clean(v): return re.sub(r'[^a-z0-9]','',str(v or '').lower())
def canonical_status(v):
 v=clean(v)
 if v in ('epiccorrect','correct','green'): return 'EPIC_CORRECT'
 if v in ('epicincorrect','incorrect','missing','red'): return 'EPIC_INCORRECT'
 return 'VERIFY_NEEDED'

@st.cache_resource
def get_store(): return DriveStore()
try:
 STORE=get_store(); DATA=STORE.load(); STORAGE_OK=True; STORAGE_ERROR=''
except Exception as e:
 STORE=None; DATA=normalize({}); STORAGE_OK=False; STORAGE_ERROR=str(e)

def persist(action='',entity=''):
 global DATA
 DATA.setdefault('audit',[]).append({'at':now(),'action':action,'entity':entity})
 DATA=STORE.save(DATA) if STORE else DATA

def practice(pid): return next((x for x in DATA['practices'] if x.get('id')==pid),{})
def provider_key(name,practice_name): return clean(name)+'|'+clean(practice_name)

def add_provider_to_memory(row):
 name=str(row.get('provider_name','')).strip(); pname=str(row.get('practice_name','')).strip() or 'Practice not recorded'; city=str(row.get('city','')).strip()
 if not name: return 'skipped'
 existing_pr=next((x for x in DATA['practices'] if clean(x.get('name'))==clean(pname) and (not city or clean(x.get('city'))==clean(city))),None)
 if not existing_pr:
  existing_pr={'id':str(uuid.uuid4()),'name':pname,'city':city,'state':str(row.get('state','')).strip(),'fax':digits(row.get('correct_fax')),'phone':digits(row.get('phone')),'active':True}; DATA['practices'].append(existing_pr)
 existing=next((x for x in DATA['providers'] if provider_key(x.get('provider_name'),practice(x.get('practice_id')).get('name'))==provider_key(name,pname)),None)
 payload={'provider_name':name,'credentials':str(row.get('credentials','')).strip(),'practice_id':existing_pr['id'],'npi':digits(row.get('npi')),'epic_status':canonical_status(row.get('epic_status')),'epic_fax':digits(row.get('epic_fax')),'correct_fax':digits(row.get('correct_fax')),'source_url':str(row.get('source_url','')).strip(),'notes':str(row.get('notes','')).strip(),'active':True}
 if existing:
  # Preserve manually entered nonblank values while upgrading status/data.
  for k,v in payload.items():
   if v not in ('',None): existing[k]=v
  return 'updated'
 payload['id']=str(uuid.uuid4()); DATA['providers'].append(payload); return 'added'

def import_df(df,action):
 counts={'added':0,'updated':0,'skipped':0}
 for _,row in df.fillna('').iterrows(): counts[add_provider_to_memory(row.to_dict())]+=1
 persist(action,json.dumps(counts)); return counts

def suggestion_rows():
 opts=[]
 for p in DATA['providers']:
  if not p.get('active',True): continue
  pr=practice(p.get('practice_id')); label=f"Provider • {p.get('provider_name','')}"
  if pr.get('name') and pr.get('name')!='Practice not recorded': label+=f" — {pr['name']}"
  opts.append((label,'provider',p.get('id')))
 for pr in DATA['practices']:
  if pr.get('active',True) and pr.get('name')!='Practice not recorded': opts.append((f"Practice • {pr.get('name','')}",'practice',pr.get('id')))
 return sorted(opts,key=lambda x:clean(x[0]))

def provider_card(p):
 pr=practice(p.get('practice_id')); status=canonical_status(p.get('epic_status')); name=p.get('provider_name',''); creds=p.get('credentials',''); pname=pr.get('name','')
 if status=='EPIC_CORRECT':
  st.markdown(f"<div class='card green'><div class='status'>✅ FAX IN EPIC CORRECT</div><h2>{name}{', '+creds if creds else ''}</h2><div class='muted'>{pname if pname!='Practice not recorded' else ''}</div><div class='route'>Route normally through Epic.</div></div>",unsafe_allow_html=True)
 elif status=='EPIC_INCORRECT':
  fax=fmt(p.get('correct_fax'))
  st.markdown(f"<div class='card red'><div class='status'>🔴 FAX IN EPIC INCORRECT</div><h2>{name}{', '+creds if creds else ''}</h2><div class='muted'>{pname if pname!='Practice not recorded' else ''}</div><div class='use'>USE THIS FAX</div><div class='fax'>{fax or 'FAX NEEDED'}</div></div>",unsafe_allow_html=True)
  if fax: st.code(fax,language=None)
 else:
  st.markdown(f"<div class='card yellow'><div class='status'>🟡 EPIC FAX NEEDS VERIFICATION</div><h2>{name}{', '+creds if creds else ''}</h2><div class='muted'>{pname if pname!='Practice not recorded' else ''}</div></div>",unsafe_allow_html=True)

st.markdown('''<style>.block-container{max-width:860px;padding-top:2rem}.card{border:1px solid #dce2e8;border-radius:18px;padding:25px;margin:14px 0;background:#fff;box-shadow:0 3px 14px #0000000d}.red{border-left:14px solid #c93545}.green{border-left:14px solid #20894e}.yellow{border-left:14px solid #d99b00}.fax{font-size:44px;font-weight:900;letter-spacing:1px;margin:5px 0}.status{font-size:25px;font-weight:900}.route{font-size:20px;font-weight:700;margin-top:14px}.use{font-size:18px;font-weight:850;margin-top:14px}.muted{color:#66727f}.stButton button{min-height:48px;font-weight:750}</style>''',unsafe_allow_html=True)
st.title('📠 Provider Fax Verify')
if STORAGE_OK: st.caption('☁️ Google Drive saving is on')
else: st.error('Saving is not connected. '+STORAGE_ERROR)

options=suggestion_rows(); labels=['']+[x[0] for x in options]
choice=st.selectbox('Search provider or practice',labels,index=0,placeholder='Start typing a provider or practice...',help='Type any part of a provider or practice name.')
if choice:
 _,kind,item_id=next(x for x in options if x[0]==choice)
 if kind=='provider': provider_card(next(p for p in DATA['providers'] if p.get('id')==item_id))
 else:
  pr=next(x for x in DATA['practices'] if x.get('id')==item_id); st.subheader(pr.get('name'))
  members=[p for p in DATA['providers'] if p.get('practice_id')==item_id and p.get('active',True)]
  for p in sorted(members,key=lambda x:clean(x.get('provider_name'))): provider_card(p)

if 'add_notice' in st.session_state:
 st.success(st.session_state.pop('add_notice'))
with st.expander('Add a provider not on the list'):
 if not STORAGE_OK: st.warning('Connect Google Drive before adding permanent records.')
 with st.form('add',clear_on_submit=True):
  a,b=st.columns(2); provider_name=a.text_input('Provider name*'); credentials=b.text_input('Credentials'); practice_name=st.text_input('Practice name*'); epic_status=st.radio('Epic fax status',['EPIC_CORRECT','EPIC_INCORRECT','VERIFY_NEEDED'],horizontal=True); correct_fax=st.text_input('Correct fax (required if Epic is incorrect)'); c,d=st.columns(2); city=c.text_input('City'); state=d.text_input('State',value='GA'); submitted=st.form_submit_button('Add provider',use_container_width=True,disabled=not STORAGE_OK)
  if submitted:
   if not provider_name or not practice_name: st.error('Provider and practice are required.')
   elif epic_status=='EPIC_INCORRECT' and len(digits(correct_fax))!=10: st.error('A 10-digit correct fax is required when Epic is incorrect.')
   else:
    result=add_provider_to_memory({'provider_name':provider_name,'credentials':credentials,'practice_name':practice_name,'epic_status':epic_status,'correct_fax':correct_fax,'city':city,'state':state}); persist('provider_added',provider_name); st.session_state['add_notice']='Provider saved to Google Drive. The form was cleared.'; st.rerun()

with st.sidebar:
 st.header('Admin'); password=st.text_input('Admin password',type='password')
 if ADMIN_PASSWORD and secrets.compare_digest(password,ADMIN_PASSWORD):
  st.success('Admin unlocked')
  if LEGACY.exists() and st.button('Load built-in provider directory',use_container_width=True,disabled=not STORAGE_OK):
   counts=import_df(pd.read_csv(LEGACY,dtype=str),'legacy_directory_import'); st.success(f"Added {counts['added']}, updated {counts['updated']}, skipped {counts['skipped']}"); st.rerun()
  up=st.file_uploader('Import CSV or XLSX',type=['csv','xlsx'])
  if up:
   df=pd.read_csv(up,dtype=str).fillna('') if up.name.lower().endswith('.csv') else pd.read_excel(up,dtype=str).fillna(''); st.dataframe(df.head(8),use_container_width=True)
   if st.button('Import uploaded file',use_container_width=True,disabled=not STORAGE_OK):
    need={'provider_name','practice_name','epic_status'}
    if not need.issubset(df.columns): st.error('Missing: '+', '.join(sorted(need-set(df.columns))))
    else:
     counts=import_df(df,'uploaded_directory_import'); st.success(f"Added {counts['added']}, updated {counts['updated']}, skipped {counts['skipped']}"); st.rerun()
  rows=[]
  for p in DATA['providers']:
   pr=practice(p.get('practice_id')); rows.append({'provider_name':p.get('provider_name'),'credentials':p.get('credentials'),'practice_name':pr.get('name'),'city':pr.get('city'),'state':pr.get('state'),'npi':p.get('npi'),'epic_status':canonical_status(p.get('epic_status')),'correct_fax':fmt(p.get('correct_fax')),'epic_fax':fmt(p.get('epic_fax')),'phone':fmt(pr.get('phone')),'source_url':p.get('source_url',''),'notes':p.get('notes','')})
  out=pd.DataFrame(rows); st.metric('Providers',len(out)); st.download_button('Download directory',out.to_csv(index=False).encode(),'provider_fax_directory.csv','text/csv',use_container_width=True); st.download_button('Download JSON backup',json.dumps(DATA,indent=2).encode(),'provider_fax_data.json','application/json',use_container_width=True)
 elif password: st.error('Incorrect password')
