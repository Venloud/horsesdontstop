"""GET-only historical market data; no broker trading client or database access."""
import os,json,hashlib,time
from pathlib import Path
import requests
R=Path(__file__).resolve().parent
if __name__=='__main__':
 s=requests.Session();s.trust_env=False
 key=os.environ.get('ALPACA_PAPER_API_KEY');secret=os.environ.get('ALPACA_PAPER_SECRET_KEY')
 if not key or not secret:raise SystemExit('Missing PAPER data credentials; values never printed')
 headers={'APCA-API-KEY-ID':key,'APCA-API-SECRET-KEY':secret}
 params={'symbols':'AAPL,AMD,F,KO,SPY,WMT','timeframe':'5Min','start':'2024-01-01T00:00:00Z','end':'2026-01-01T00:00:00Z','feed':'iex','adjustment':'all','limit':10000,'sort':'asc'}
 rows=[];pages=0
 while True:
  resp=s.get('https://data.alpaca.markets/v2/stocks/bars',headers=headers,params=params,timeout=45)
  if resp.status_code!=200:raise SystemExit(f'Data GET failed HTTP {resp.status_code}; no broker operation attempted')
  d=resp.json();pages+=1
  for symbol,bars in d.get('bars',{}).items():
   rows.extend(dict(b,symbol=symbol) for b in bars)
  token=d.get('next_page_token')
  if not token:break
  params['page_token']=token
  if pages>150:raise SystemExit('Pagination bound exceeded')
  time.sleep(.35)
 out=R/'iex_5m_2024_2025.json';out.write_text(json.dumps(rows),encoding='utf-8')
 (R/'data_manifest.json').write_text(json.dumps({'source':'Alpaca historical IEX 5Min adjusted all','start':params['start'],'end_exclusive':params['end'],'symbols':params['symbols'],'pages':pages,'rows':len(rows),'sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'broker_calls':0,'database_calls':0},indent=2))
 print(f'Saved {len(rows)} historical bars, {pages} GET pages; no orders/Neon calls')
