import os, re, json, secrets, uuid
from datetime import datetime, timezone
from copy import deepcopy
import pandas as pd
import streamlit as st
from drive_store import DriveStore, normalize

st.set_page_config(page_title='Provider Fax Verify', page_icon='📠', layout='centered')
ADMIN_PASSWORD=os.getenv('ADMIN_PASSWORD','').strip()

def digits(v): return re.sub(r'\D','',str(v or ''))[-10:]
def fmt(v):
    d=digits(v); return f'{d[:3]}-{d[3:6]}-{d[6:]}' if len(d)==10 else str(v or '')
def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def clean(v): return re.sub(r'[^a-z0-9]','',str(v or '').lower())

@st.cache_resource
def get_store(): return DriveStore()

def seed():
    return normalize({'practices':[{'id':'cumberland','name':'Cumberland Pediatrics','city':'Marietta','state':'GA','fax':'7707025627','phone':'7709515400','active':True},{'id':'east-cobb','name':'East Cobb Pediatrics','city':'Marietta','state':'GA','fax':'7705099463','phone':'7709770094','active':True}], 'providers':[{'id':str(uuid.uuid4()),'provider_name':'Peter Strauss','credentials':'MD','practice_id':'cumberland','npi':'','epic_status':'incorrect','epic_fax':'7709515400','correct_fax':'7707025627','active':True},{'id':str(uuid.uuid4()),'provider_name':'Alice Wilson','credentials':'MD','practice_id':'cumberland','npi':'','epic_status':'incorrect','epic_fax':'','correct_fax':'7707025627','active':True},{'id':str(uuid.uuid4()),'provider_name':'Karen Thrower','credentials':'MD','practice_id':'east-cobb','npi':'','epic_status':'missing','epic_fax':'','correct_fax':'7705099463','active':True}]})

try:
    STORE=get_store(); DATA=STORE.load(); STORAGE_OK=True; STORAGE_ERROR=''
except Exception as e:
    STORE=None; DATA=seed(); STORAGE_OK=False; STORAGE_ERROR=str(e)

def save(action='', entity=''):
    global DATA
    DATA.setdefault('audit',[]).append({'at':now(),'action':action,'entity':entity})
    DATA=STORE.save(DATA) if STORE else DATA

def practice(pid): return next((x for x in DATA['practices'] if x['id']==pid),{})
def matches(q,p,pr):
    d=digits(q); hay=' '.join(str(x) for x in [p.get('provider_name'),p.get('npi'),p.get('correct_fax'),p.get('epic_fax'),pr.get('name'),pr.get('city'),pr.get('fax')])
    return clean(q) in clean(hay) or (len(d)>=4 and d in digits(hay))

def add_provider(row):
    pname=str(row.get('practice_name','')).strip(); city=str(row.get('city','')).strip()
    pr=next((x for x in DATA['practices'] if clean(x.get('name'))==clean(pname) and clean(x.get('city'))==clean(city)),None)
    if not pr:
        pr={'id':str(uuid.uuid4()),'name':pname,'city':city,'state':str(row.get('state','GA')),'fax':digits(row.get('correct_fax')),'phone':digits(row.get('phone')),'active':True}
        DATA['practices'].append(pr)
    item={'id':str(uuid.uuid4()),'provider_name':str(row.get('provider_name','')).strip(),'credentials':str(row.get('credentials','')).strip(),'practice_id':pr['id'],'npi':digits(row.get('npi')),'epic_status':str(row.get('epic_status','unknown')).lower(),'epic_fax':digits(row.get('epic_fax')),'correct_fax':digits(row.get('correct_fax')),'source_url':str(row.get('source_url','')).strip(),'notes':str(row.get('notes','')).strip(),'active':True}
    DATA['providers'].append(item); save('provider_added',item['id'])

st.markdown('''<style>.block-container{max-width:820px;padding-top:2rem}.card{border:1px solid #dce2e8;border-radius:18px;padding:22px;margin:12px 0;background:#fff;box-shadow:0 3px 14px #0000000d}.red{border-left:12px solid #c93545}.green{border-left:12px solid #20894e}.fax{font-size:42px;font-weight:850;letter-spacing:1px;margin:8px 0}.status{font-size:17px;font-weight:850}.muted{color:#66727f}.stButton button{min-height:48px;font-weight:750}</style>''',unsafe_allow_html=True)
st.title('📠 Provider Fax Verify')
if STORAGE_OK: st.caption('☁️ Google Drive saving is on')
else: st.error('Saving is not connected. '+STORAGE_ERROR)
q=st.text_input('Search',placeholder='Provider or practice name',label_visibility='collapsed')
if q.strip():
    results=[]
    for p in DATA['providers']:
        pr=practice(p.get('practice_id'))
        if p.get('active',True) and pr.get('active',True) and matches(q,p,pr): results.append((p,pr))
    if not results: st.warning('Provider or practice not found.')
    else:
        practice_hits={pr['id'] for _,pr in results if clean(q) in clean(pr.get('name'))}
        shown=set()
        for p,pr in results:
            if pr['id'] in practice_hits:
                if pr['id'] in shown: continue
                shown.add(pr['id']); names=[x['provider_name'] for x in DATA['providers'] if x.get('practice_id')==pr['id'] and x.get('active',True)]
                fax=pr.get('fax') or p.get('correct_fax')
                st.markdown(f"<div class='card green'><div class='status'>{pr['name']}</div><div class='fax'>{fmt(fax)}</div><div class='muted'>{', '.join(names)}</div></div>",unsafe_allow_html=True)
                st.code(fmt(fax),language=None)
            else:
                bad=p.get('epic_status') in ('incorrect','missing'); cls='red' if bad else 'green'; label='EPIC NUMBER INCORRECT — USE THIS FAX' if p.get('epic_status')=='incorrect' else ('FAX MISSING IN EPIC — USE THIS FAX' if p.get('epic_status')=='missing' else 'VERIFIED FAX')
                st.markdown(f"<div class='card {cls}'><div class='status'>{'🔴' if bad else '✅'} {label}</div><h3>{p['provider_name']}{', '+p.get('credentials','') if p.get('credentials') else ''}</h3><div class='muted'>{pr.get('name','')}</div><div class='fax'>{fmt(p.get('correct_fax'))}</div></div>",unsafe_allow_html=True)
                st.code(fmt(p.get('correct_fax')),language=None)
                st.caption('Use the copy icon in the fax box above.')

with st.expander('Add a provider not on the list'):
    if not STORAGE_OK: st.warning('Connect Google Drive before adding permanent records.')
    with st.form('add'):
        a,b=st.columns(2); provider_name=a.text_input('Provider name*'); credentials=b.text_input('Credentials'); practice_name=st.text_input('Practice name*'); correct_fax=st.text_input('Correct fax*'); c,d=st.columns(2); city=c.text_input('City'); state=d.text_input('State',value='GA'); submitted=st.form_submit_button('Add provider',use_container_width=True,disabled=not STORAGE_OK)
        if submitted:
            if not provider_name or not practice_name or len(digits(correct_fax))!=10: st.error('Provider, practice, and a 10-digit fax are required.')
            else: add_provider(locals()); st.success('Provider saved to Google Drive.'); st.rerun()

with st.sidebar:
    st.header('Admin')
    password=st.text_input('Admin password',type='password')
    if ADMIN_PASSWORD and secrets.compare_digest(password,ADMIN_PASSWORD):
        up=st.file_uploader('Import CSV or XLSX',type=['csv','xlsx'])
        if up:
            df=pd.read_csv(up,dtype=str).fillna('') if up.name.lower().endswith('.csv') else pd.read_excel(up,dtype=str).fillna('')
            st.dataframe(df.head(8),use_container_width=True)
            if st.button('Import rows',use_container_width=True,disabled=not STORAGE_OK):
                need={'provider_name','practice_name','correct_fax'}
                if not need.issubset(df.columns): st.error('Missing: '+', '.join(sorted(need-set(df.columns))))
                else:
                    count=0
                    for _,row in df.iterrows():
                        if row['provider_name'] and row['practice_name'] and len(digits(row['correct_fax']))==10: add_provider(row.to_dict()); count+=1
                    st.success(f'Imported {count} providers'); st.rerun()
        rows=[]
        for p in DATA['providers']:
            pr=practice(p.get('practice_id')); rows.append({'provider_name':p.get('provider_name'),'credentials':p.get('credentials'),'practice_name':pr.get('name'),'city':pr.get('city'),'state':pr.get('state'),'npi':p.get('npi'),'epic_status':p.get('epic_status'),'correct_fax':fmt(p.get('correct_fax')),'epic_fax':fmt(p.get('epic_fax')),'phone':fmt(pr.get('phone')),'source_url':p.get('source_url',''),'notes':p.get('notes','')})
        out=pd.DataFrame(rows)
        st.download_button('Download directory',out.to_csv(index=False).encode(),'provider_fax_directory.csv','text/csv',use_container_width=True)
        st.download_button('Download JSON backup',json.dumps(DATA,indent=2).encode(),'provider_fax_data.json','application/json',use_container_width=True)
    elif password: st.error('Incorrect password')
