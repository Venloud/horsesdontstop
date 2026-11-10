"""Select by training only, then describe the untouched chronological test split."""
import json
from pathlib import Path
R=Path(__file__).resolve().parent
rows=json.loads((R/'results.json').read_text())['results'];selected=[]
for strategy in sorted({r['config']['strategy'] for r in rows}):
 train=[r for r in rows if r['split']=='train' and r['config']['strategy']==strategy and r['config']['spread_bps']==5]
 best=max(train,key=lambda r:r['sharpe_daily'] if r['sharpe_daily'] is not None else -999)
 test=next(r for r in rows if r['split']=='test' and r['config']==best['config'])
 stress=next(r for r in rows if r['split']=='test' and dict(r['config'],spread_bps=5,slippage_bps=5)==test['config'] and r['config']['spread_bps']==20)
 clean=lambda r:{k:v for k,v in r.items() if k not in ['ledger','daily_equity','config']}
 quarters={};symbols={}
 for t in test['ledger']:
  q=t['day'][:4]+'Q'+str((int(t['day'][5:7])-1)//3+1);quarters[q]=quarters.get(q,0)+t['pnl'];symbols[t['symbol']]=symbols.get(t['symbol'],0)+t['pnl']
 selected.append({'strategy':strategy,'selection':'2024 training daily Sharpe only; four configurations tested per strategy','config':best['config'],'train':clean(best),'test':clean(test),'stress':clean(stress),'test_quarter_pnl':quarters,'test_symbol_pnl':symbols})
(R/'selected.json').write_text(json.dumps(selected,indent=2,allow_nan=False))
lines=['| Candidate | Train final | Test final | Test trades | Win % | PF | Expectancy $ | Max DD % | Daily Sharpe | Stress final |','|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
for r in sorted(selected,key=lambda r:r['test']['sharpe_daily'] or -999,reverse=True):
 t=r['test'];lines.append(f"| {r['strategy']} | {r['train']['final']:.2f} | {t['final']:.2f} | {t['trades']} | {100*(t['win_rate'] or 0):.1f} | {(t['profit_factor'] or 0):.3f} | {(t['expectancy'] or 0):.3f} | {100*t['max_drawdown']:.2f} | {(t['sharpe_daily'] or 0):.3f} | {r['stress']['final']:.2f} |")
(R/'comparison.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))
