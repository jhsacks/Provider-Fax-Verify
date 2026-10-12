import os,re,json,secrets,uuid,urllib.parse,urllib.request
from datetime import datetime,timezone
from pathlib import Path
import pandas as pd
import streamlit as st
from drive_store import DriveStore,normalize
st.set_page_config(page_title='Provider Fax Verify',page_icon='📠',layout='centered')
ADMIN_PASSWORD=os.getenv('ADMIN_PASSWORD','').strip();ROOT=Path(__file__).parent;LEGACY=ROOT/'legacy_providers.csv'
SN='https://wellstar.service-now.com/esc?id=sc_cat_item&sys_id=ba15cfea839647d4b80fc0d6feaad316&table=sc_cat_item&searchTerm=update%20fax%20number%20provider';NPI='https://npiregistry.cms.hhs.gov/search';API='https://npiregistry.cms.hhs.gov/api/'
def digits(v):return re.sub(r'\D','',str(v or ''))[-10:]
def fmt(v):
 d=digits(v);return f'{d[:3]}-{d[3:6]}-{d[6:]}' if len(d)==10 else ''
def clean(v):return re.sub(r'[^a-z0-9]','',str(v or '').lower())
def now():return datetime.now(timezone.utc).isoformat(timespec='seconds')
def stat(v):
 v=clean(v);return 'EPIC_CORRECT' if v in ('epiccorrect','correct','green') else ('EPIC_INCORRECT' if v in ('epicincorrect','incorrect','missing','red') else 'VERIFY_NEEDED')
@st.cache_resource
def get_store():return DriveStore()
@st.cache_data(ttl=300,show_spinner=False)
def load():return get_store().load()
try:STORE=get_store();DATA=load();OK=True;ERR=''
except Exception as e:STORE=None;DATA=normalize({});OK=False;ERR=str(e)
def index():
 global PR,PV
 PR={x.get('id'):x for x in DATA.get('practices',[])};PV={x.get('id'):x for x in DATA.get('providers',[])}
index()
def practice(i):return PR.get(i,{})
def save(action,entity=''):
 global DATA
 DATA.setdefault('audit',[]).append({'at':now(),'action':action,'entity':entity});DATA=STORE.save(DATA);load.clear();index()
def key(n,p):return clean(n)+'|'+clean(p)
def add_mem(r):
 n=str(r.get('provider_name','')).strip();pn=str(r.get('practice_name','')).strip() or 'Practice not recorded';city=str(r.get('city','')).strip()
 if not n:return 'skipped'
 pr=next((x for x in DATA['practices'] if clean(x.get('name'))==clean(pn) and (not city or clean(x.get('city'))==clean(city))),None)
 if not pr:pr={'id':str(uuid.uuid4()),'name':pn,'city':city,'state':str(r.get('state','')).strip(),'fax':digits(r.get('correct_fax')),'phone':digits(r.get('phone')),'active':True};DATA['practices'].append(pr);index()
 old=next((x for x in DATA['providers'] if key(x.get('provider_name'),practice(x.get('practice_id')).get('name'))==key(n,pn)),None)
 val={'provider_name':n,'credentials':str(r.get('credentials','')).strip(),'practice_id':pr['id'],'npi':digits(r.get('npi')),'epic_status':stat(r.get('epic_status')),'epic_fax':digits(r.get('epic_fax')),'correct_fax':digits(r.get('correct_fax')),'notes':str(r.get('notes','')).strip(),'active':True}
 if old:
  for k,v in val.items():
   if v not in ('',None):old[k]=v
  index();return 'updated'
 val['id']=str(uuid.uuid4());DATA['providers'].append(val);index();return 'added'
def import_df(df,label):
 c={'added':0,'updated':0,'skipped':0}
 for _,r in df.fillna('').iterrows():c[add_mem(r.to_dict())]+=1
 save(label,json.dumps(c));return c
def search(q):
 n=clean(q);out=[]
 if len(n)<2:return out
 for p in DATA.get('providers',[]):
  pr=practice(p.get('practice_id'));name=p.get('provider_name','');pn=pr.get('name','');hay=clean(' '.join([name,pn,p.get('npi',''),p.get('correct_fax','')]))
  if p.get('active',True) and n in hay:out.append((0 if clean(name).startswith(n) else 1,name+(f' — {pn}' if pn!='Practice not recorded' else ''),'p',p['id']))
 for pr in DATA.get('practices',[]):
  if pr.get('name')!='Practice not recorded' and n in clean(pr.get('name')):out.append((0 if clean(pr.get('name')).startswith(n) else 1,'Practice: '+pr.get('name'),'r',pr['id']))
 return sorted(out,key=lambda x:(x[0],clean(x[1])))[:12]
def ticket_label(v):return {'NOT_SUBMITTED':'Not submitted','SUBMITTED':'Submitted with ticket','IN_PROGRESS':'In progress','AWAITING_EPIC_UPDATE':'Awaiting Epic update','CORRECTED':'Epic corrected','CLOSED':'Closed'}.get(v or 'NOT_SUBMITTED','Not submitted')
def outstanding(p):return stat(p.get('epic_status'))=='EPIC_INCORRECT' and p.get('correction_status','NOT_SUBMITTED') not in ('CORRECTED','CLOSED')
def split_name(n):
 b=re.sub(r',?\s*(MD|DO|NP|APRN|PA-C|PA|FNP-C|FNP|CPNP|DNP|FAAP)\b.*$','',str(n),flags=re.I).strip();parts=b.split();return (parts[0],parts[-1]) if len(parts)>1 else (b,'')
def verify(p):
 first,last=split_name(p.get('provider_name'));pr=practice(p.get('practice_id'));params={'version':'2.1','limit':'10','enumeration_type':'NPI-1'}
 if p.get('npi'):params['number']=p['npi']
 else:params.update({'first_name':first,'last_name':last});params.update({'state':pr['state']} if pr.get('state') else {})
 req=urllib.request.Request(API+'?'+urllib.parse.urlencode(params),headers={'User-Agent':'ProviderFaxVerify/1.0'})
 with urllib.request.urlopen(req,timeout=15) as x:raw=json.loads(x.read().decode())
 faxes=sorted({digits(a.get('fax_number')) for r in raw.get('results',[]) for a in (r.get('addresses',[]) or []) if digits(a.get('fax_number'))});stored=digits(p.get('correct_fax'));result='FAX_MATCH' if stored and stored in faxes else ('REVIEW_NEEDED' if faxes else 'NO_FAX_FOUND')
 p.update({'last_verified':now(),'verification_result':result,'registry_faxes':faxes,'verification_source':'NPPES API 2.1'})
 if result=='REVIEW_NEEDED':
  for f in faxes:
   if not any(x.get('provider_id')==p['id'] and x.get('found_fax')==f and x.get('status','pending')=='pending' for x in DATA.setdefault('review_queue',[])):DATA['review_queue'].append({'id':str(uuid.uuid4()),'provider_id':p['id'],'current_fax':stored,'found_fax':f,'status':'pending','checked_at':now(),'source':'NPPES API 2.1'})
 save('npi_verification',p['id']);return result
def card(p):
 pr=practice(p.get('practice_id'));s=stat(p.get('epic_status'));name=p.get('provider_name','')+(f", {p.get('credentials')}" if p.get('credentials') else '');pn='' if pr.get('name')=='Practice not recorded' else pr.get('name','')
 if s=='EPIC_CORRECT':st.markdown(f"<div class='card green'><div class='status'>✅ FAX IN EPIC CORRECT</div><h2>{name}</h2><div>{pn}</div><h3>Route normally through Epic.</h3></div>",unsafe_allow_html=True)
 elif s=='EPIC_INCORRECT':
  f=fmt(p.get('correct_fax'));st.markdown(f"<div class='card red'><div class='status'>🔴 FAX IN EPIC INCORRECT</div><h2>{name}</h2><div>{pn}</div><b>USE THIS FAX</b><div class='fax'>{f or 'FAX NEEDED'}</div></div>",unsafe_allow_html=True)
  if f:st.code(f,language=None)
  st.write(f"**Correction status:** {ticket_label(p.get('correction_status'))}");st.write((f"**Ticket:** {p.get('ticket_number')}  " if p.get('ticket_number') else '')+(f"**Submitted:** {p.get('ticket_submitted_date')}  " if p.get('ticket_submitted_date') else '')+(f"**Update:** {p.get('ticket_note')}" if p.get('ticket_note') else ''))
  with st.expander('Update correction ticket'):
   with st.form('ticket_'+p['id']):
    opts=['NOT_SUBMITTED','SUBMITTED','IN_PROGRESS','AWAITING_EPIC_UPDATE','CORRECTED','CLOSED'];current=p.get('correction_status','NOT_SUBMITTED');cs=st.selectbox('Status',opts,index=opts.index(current) if current in opts else 0);tn=st.text_input('ServiceNow ticket number',p.get('ticket_number',''));td=st.text_input('Submitted date',p.get('ticket_submitted_date',''),placeholder='YYYY-MM-DD');note=st.text_input('Status update',p.get('ticket_note',''))
    if st.form_submit_button('Save ticket status',use_container_width=True):p.update({'correction_status':cs,'ticket_number':tn.strip(),'ticket_submitted_date':td.strip(),'ticket_note':note.strip(),'ticket_updated_at':now()});save('ticket_updated',p['id']);st.session_state.notice='Ticket status saved.';st.rerun()
 else:st.markdown(f"<div class='card yellow'><div class='status'>🟡 EPIC FAX NEEDS VERIFICATION</div><h2>{name}</h2><div>{pn}</div></div>",unsafe_allow_html=True)
st.markdown("""<style>.block-container{max-width:860px;padding-top:2rem}.card{border:1px solid #ddd;border-radius:18px;padding:25px;margin:14px 0;background:white}.green{border-left:14px solid #20894e}.red{border-left:14px solid #c93545}.yellow{border-left:14px solid #d99b00}.status{font-size:25px;font-weight:900}.fax{font-size:44px;font-weight:900}.stButton button{min-height:44px;font-weight:750;text-align:left}</style>""",unsafe_allow_html=True)
st.title('📠 Provider Fax Verify');st.caption('☁️ Google Drive saving is on' if OK else '')
if not OK:st.error('Saving is not connected. '+ERR)
for k in ('kind','pick'):
 if k not in st.session_state:st.session_state[k]=''
q=st.text_input('Search provider or practice',key='q',placeholder='Start typing a provider or practice...')
if q:
 m=search(q)
 if not m:st.warning('No matching provider or practice found.')
 for i,(_,label,k,pid) in enumerate(m):
  if st.button(label,key=f'm{i}{pid}',use_container_width=True):st.session_state.kind=k;st.session_state.pick=pid;st.rerun()
if st.session_state.kind=='p' and st.session_state.pick in PV:card(PV[st.session_state.pick])
elif st.session_state.kind=='r' and st.session_state.pick in PR:
 for p in sorted([x for x in DATA['providers'] if x.get('practice_id')==st.session_state.pick],key=lambda x:clean(x.get('provider_name'))):card(p)
if q or st.session_state.pick:
 if st.button('Clear search',use_container_width=True):st.session_state.q='';st.session_state.kind='';st.session_state.pick='';st.rerun()
if 'notice' in st.session_state:st.success(st.session_state.pop('notice'))
with st.expander('Add a provider not on the list'):
 a,b=st.columns(2);a.link_button('Submit Epic fax update',SN,use_container_width=True);b.link_button('Search NPI Registry',NPI,use_container_width=True)
 with st.form('add',clear_on_submit=True):
  c,d=st.columns(2);provider_name=c.text_input('Provider name*');credentials=d.text_input('Credentials');practice_name=st.text_input('Practice name*');epic_status=st.radio('Epic fax status',['EPIC_CORRECT','EPIC_INCORRECT','VERIFY_NEEDED'],horizontal=True);correct_fax=st.text_input('Correct fax');city=st.text_input('City');state=st.text_input('State',value='GA')
  if st.form_submit_button('Add provider',use_container_width=True,disabled=not OK):
   if not provider_name or not practice_name:st.error('Provider and practice are required.')
   elif epic_status=='EPIC_INCORRECT' and len(digits(correct_fax))!=10:st.error('A 10-digit correct fax is required.')
   else:add_mem(locals());save('provider_added',provider_name);st.session_state.notice='Provider saved. The form was cleared.';st.rerun()
with st.sidebar:
 st.header('Admin');pw=st.text_input('Admin password',type='password')
 if ADMIN_PASSWORD and secrets.compare_digest(pw,ADMIN_PASSWORD):
  st.success('Admin unlocked')
  outs=sorted([p for p in DATA['providers'] if outstanding(p)],key=lambda p:clean(p.get('provider_name')));st.subheader(f'Outstanding Epic correction tickets ({len(outs)})')
  if not outs:st.caption('No outstanding Epic correction tickets.')
  for p in outs:
   pr=practice(p.get('practice_id'));st.markdown(f"**{p.get('provider_name')}**"+(f" — {pr.get('name')}" if pr.get('name')!='Practice not recorded' else ''));st.write(f"{ticket_label(p.get('correction_status'))} | Ticket: {p.get('ticket_number') or 'Not entered'} | Fax: {fmt(p.get('correct_fax')) or 'Not entered'}")
  if LEGACY.exists() and st.button('Load built-in provider directory',use_container_width=True):c=import_df(pd.read_csv(LEGACY,dtype=str),'legacy_import');st.success(str(c));st.rerun()
  st.subheader('NPI verification');batch=st.number_input('Providers to check',1,50,10)
  if st.button('Auto-check next providers',use_container_width=True):
   items=sorted(DATA['providers'],key=lambda x:x.get('last_verified',''))[:int(batch)]
   for p in items:
    try:verify(p)
    except Exception:pass
   st.rerun()
  pending=[x for x in DATA.get('review_queue',[]) if x.get('status','pending')=='pending'];st.subheader(f'Verification review ({len(pending)})')
  for x in pending[:50]:
   p=PV.get(x.get('provider_id'),{});st.write(f"**{p.get('provider_name','Provider')}** Stored: {fmt(x.get('current_fax')) or 'none'} | Registry: {fmt(x.get('found_fax'))}");a,b=st.columns(2)
   if a.button('Accept registry fax',key='a'+x['id']):p['correct_fax']=digits(x['found_fax']);x['status']='accepted';save('fax_accepted',x['id']);st.rerun()
   if b.button('Keep stored fax',key='r'+x['id']):x['status']='rejected';save('fax_rejected',x['id']);st.rerun()
  rows=[]
  for p in DATA['providers']:
   pr=practice(p.get('practice_id'));rows.append({'provider_name':p.get('provider_name'),'practice_name':pr.get('name'),'epic_status':stat(p.get('epic_status')),'correct_fax':fmt(p.get('correct_fax')),'correction_status':p.get('correction_status','NOT_SUBMITTED'),'ticket_number':p.get('ticket_number',''),'ticket_submitted_date':p.get('ticket_submitted_date',''),'ticket_note':p.get('ticket_note','')})
  exp=pd.DataFrame(rows);st.download_button('Download directory and tickets',exp.to_csv(index=False).encode(),'provider_fax_directory.csv','text/csv',use_container_width=True)
 elif pw:st.error('Incorrect password')
