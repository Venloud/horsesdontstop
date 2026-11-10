"""Offline-only, long-only 5-minute portfolio simulator. Never imports broker code."""
from dataclasses import dataclass,asdict
from datetime import datetime,time
from zoneinfo import ZoneInfo
from collections import defaultdict,deque
from math import isfinite,sqrt
from statistics import mean,stdev
import json,csv,argparse
from pathlib import Path
NY=ZoneInfo('America/New_York')
@dataclass(frozen=True)
class Config:
 strategy:str='orb_vwap_volume'
 opening_minutes:int=20
 stop_pct:float=.01
 target_r:float=1.5
 trail_r:float=0
 spread_bps:float=5
 slippage_bps:float=5
 commission_share:float=.005
 risk:float=.01

def prepare(raw):
 days=defaultdict(lambda:defaultdict(list));seen=set()
 for b in raw:
  t=datetime.fromisoformat(b['t'].replace('Z','+00:00'))
  if t.tzinfo is None:raise ValueError('Timezone required')
  t=t.astimezone(NY)
  if not time(9,30)<=t.time()<time(16):continue
  vals=[float(b[k]) for k in ('o','h','l','c','v')]
  o,h,l,c,v=vals
  if not all(isfinite(x) for x in vals) or min(o,h,l,c)<=0 or v<0 or l>min(o,c) or h<max(o,c):raise ValueError('Invalid OHLCV')
  key=(b['symbol'],t)
  if key in seen:raise ValueError('Duplicate bar')
  seen.add(key);days[t.date().isoformat()][b['symbol']].append(dict(b,dt=t,minute=t.hour*60+t.minute))
 history=defaultdict(list);volumes=defaultdict(lambda:deque(maxlen=20));result={};quality=defaultdict(int)
 expected=set(range(570,960,5))
 for day,symbols in sorted(days.items()):
  result[day]={}
  for sym,bars in sorted(symbols.items()):
   bars.sort(key=lambda b:b['dt']);mins={b['minute'] for b in bars}
   # Fail closed: incomplete IEX days and early-close days excluded, never fill forward.
   if mins!=expected:quality['excluded_symbol_days_missing_or_short_session']+=1;continue
   hist=history[sym];eligible=False
   if len(hist)>=22:
    baseline=mean(x['v'] for x in hist[-21:-1]);eligible=baseline>0 and hist[-1]['v']/baseline>=1.15 and hist[-1]['c']>hist[-22]['c']
   cv=cp=0
   for b in bars:
    k=(sym,b['minute']);past=volumes[k]
    b['rvol']=b['v']/mean(past) if len(past)>=20 and mean(past)>0 else None
    cv+=b['v'];cp+=b.get('vw',(b['h']+b['l']+b['c'])/3)*b['v'];b['vwap']=cp/cv if cv else b['c'];b['daily_momentum']=eligible
   result[day][sym]=bars
   for b in bars:volumes[(sym,b['minute'])].append(b['v'])
   hist.append({'c':bars[-1]['c'],'v':sum(b['v'] for b in bars)})
   quality['accepted_symbol_days']+=1
 return result,dict(quality)

def signal(bars,i,cfg):
 b=bars[i];prior=bars[i-1] if i else b
 if cfg.strategy=='momentum_daily_eod':return i==0 and b['daily_momentum']
 if not 600<=b['minute']<=690 or b['rvol'] is None:return False
 if cfg.strategy.startswith('orb'):
  opening=[x for x in bars if x['minute']<570+cfg.opening_minutes]
  if len(opening)!=cfg.opening_minutes//5:return False
  high=max(x['h'] for x in opening)
  if b['c']<=high or prior['c']>high:return False
  return (cfg.strategy=='orb_plain' or (b['rvol']>=1.2 and b['c']>b['vwap']))
 if cfg.strategy=='vwap_reclaim':return prior['c']<=prior['vwap'] and b['c']>b['vwap'] and b['rvol']>=1.2
 if cfg.strategy=='momentum_intraday':return i>=3 and b['c']/bars[i-3]['c']-1>=.002 and b['c']>b['vwap'] and b['rvol']>=1.2
 raise ValueError('Unknown strategy')

def run(data,cfg,start,end):
 if cfg.risk!=.01 or cfg.stop_pct<=0 or cfg.target_r<=0 or min(cfg.spread_bps,cfg.slippage_bps,cfg.commission_share)<0:raise ValueError('Invalid configuration')
 cash=500.;peak=500.;trades=[];equity=[];curve=[];skips=0;halt=False
 cost=(cfg.spread_bps/2+cfg.slippage_bps)/10000
 for day,syms in sorted(data.items()):
  if not start<=day<end:continue
  opening=cash;position=None;traded=False
  for i in range(78):
   if position:
    sym,qty,entry,stop,target,initial_r,entrytime=position;b=syms[sym][i];exitprice=None;reason=None
    if b['o']<=stop:exitprice=b['o'];reason='gap_stop'
    elif b['l']<=stop:exitprice=stop;reason='stop' # both touched => stop first
    elif b['h']>=target:exitprice=target;reason='target'
    elif b['minute']>=950:exitprice=b['o'];reason='eod'
    if exitprice is not None:
     fill=exitprice*(1-cost);pnl=qty*(fill-entry)-2*qty*cfg.commission_share;cash+=qty*fill-qty*cfg.commission_share
     trades.append({'day':day,'symbol':sym,'entry_time':entrytime,'exit_time':b['dt'].isoformat(),'qty':qty,'entry':entry,'exit':fill,'pnl':pnl,'reason':reason});position=None
    elif cfg.trail_r and b['h']>=entry+initial_r*cfg.trail_r:
     # updated stop applies only NEXT bar: no unknown intrabar ordering.
     position=(sym,qty,entry,max(stop,b['h']-initial_r),target,initial_r,entrytime)
   mark=cash+(position[1]*syms[position[0]][i]['c'] if position else 0)
   peak=max(peak,mark);curve.append(mark)
   if mark<=peak*.85:halt=True
   if position or traded or halt or mark<=opening*.97 or i==0 or i>=76:continue
   candidates=[sym for sym,bars in sorted(syms.items()) if signal(bars,i-1,cfg)]
   if not candidates:continue
   sym=candidates[0];b=syms[sym][i];entry=b['o']*(1+cost);risk=entry*cfg.stop_pct
   qty=min(int((cash-.5)/(entry+cfg.commission_share)),int(cash*.01/(risk+2*cfg.commission_share+entry*cost)))
   # cap participation to 1% of prior COMPLETED IEX bar volume; execution stress assumption, not quote depth.
   qty=min(qty,int(syms[sym][i-1]['v']*.01))
   if qty<1:skips+=1;continue
   cash-=qty*(entry+cfg.commission_share);position=(sym,qty,entry,entry-risk,entry+risk*cfg.target_r,risk,b['dt'].isoformat());traded=True
   # Stops/targets can fill on entry bar, conservatively stop-first.
   if b['l']<=entry-risk or b['h']>=entry+risk*cfg.target_r:
    px=min(b['o'],entry-risk) if b['l']<=entry-risk else entry+risk*cfg.target_r
    fill=px*(1-cost);cash+=qty*(fill-cfg.commission_share);trades.append({'day':day,'symbol':sym,'entry_time':b['dt'].isoformat(),'exit_time':b['dt'].isoformat(),'qty':qty,'entry':entry,'exit':fill,'pnl':qty*(fill-entry)-2*qty*cfg.commission_share,'reason':'entry_bar_stop' if b['l']<=entry-risk else 'entry_bar_target'});position=None
  if position:raise ValueError('Unclosed position: invalid session')
  curve.append(cash)
  equity.append(cash)
 wins=sum(t['pnl'] for t in trades if t['pnl']>0);loss=-sum(t['pnl'] for t in trades if t['pnl']<0)
 returns=[b/a-1 for a,b in zip([500.]+equity[:-1],equity)];high=500.;dd=0
 for value in curve:high=max(high,value);dd=max(dd,1-value/high)
 return {'config':asdict(cfg),'initial':500,'final':round(cash,4),'trades':len(trades),'win_rate':sum(t['pnl']>0 for t in trades)/len(trades) if trades else None,'profit_factor':wins/loss if loss else None,'expectancy':sum(t['pnl'] for t in trades)/len(trades) if trades else None,'max_drawdown':dd,'sharpe_daily':mean(returns)/stdev(returns)*sqrt(252) if len(returns)>1 and stdev(returns)>0 else None,'days':len(equity),'unaffordable_or_liquidity_skips':skips,'drawdown_halt':halt,'ledger':trades,'daily_equity':equity}

if __name__=='__main__':
 R=Path(__file__).resolve().parent;data,q=prepare(json.loads((R/'iex_5m_2024_2025.json').read_text()))
 results=[]
 for strategy in ('momentum_daily_eod','orb_plain','orb_vwap_volume','vwap_reclaim','momentum_intraday'):
  for stop,target,trail in ((.01,1.5,0),(.005,2,0),(.01,2,1),(.03,2,0)):
   for spread,slip in ((5,5),(10,15),(20,30)):
    cfg=Config(strategy=strategy,stop_pct=stop,target_r=target,trail_r=trail,spread_bps=spread,slippage_bps=slip)
    for label,start,end in (('train','2024-03-01','2025-01-01'),('test','2025-01-01','2026-01-01')):
     results.append(dict(run(data,cfg,start,end),split=label))
 out={'quality':q,'results':results,'note':'Hypotheses; $500 independent train/test; one whole-share long position and one entry/day; not production simulation or deployment approval'}
 (R/'results.json').write_text(json.dumps(out,indent=2,allow_nan=False));print(json.dumps({'quality':q,'runs':len(results)}))
