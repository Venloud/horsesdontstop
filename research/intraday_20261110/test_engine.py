import unittest
from datetime import datetime,timedelta
from engine import prepare,run,Config,signal
class Tests(unittest.TestCase):
 def raw(self):
  t=datetime.fromisoformat('2024-06-03T13:30:00+00:00')
  return [{'symbol':'F','t':(t+timedelta(minutes=5*i)).isoformat(),'o':10.,'h':10.1,'l':9.9,'c':10.,'v':10000} for i in range(78)]
 def test_missing_session_excluded(self):self.assertEqual(prepare(self.raw()[:-1])[0]['2024-06-03'],{})
 def test_naive_timestamp(self):
  raw=self.raw();raw[0]['t']='2024-06-03T09:30:00'
  with self.assertRaises(ValueError):prepare(raw)
 def test_duplicate(self):
  with self.assertRaises(ValueError):prepare(self.raw()+self.raw()[:1])
 def test_cash_no_signal(self):
  d,_=prepare(self.raw());self.assertEqual(run(d,Config(),'2024','2025')['final'],500)
 def test_no_warmup_rvol(self):
  d,_=prepare(self.raw());self.assertTrue(all(b['rvol'] is None for b in d['2024-06-03']['F']))
 def test_next_bar_and_eod(self):
  d,_=prepare(self.raw());b=d['2024-06-03']['F'];b[0]['daily_momentum']=True
  r=run(d,Config(strategy='momentum_daily_eod',stop_pct=.03),'2024','2025');self.assertEqual(r['trades'],1);self.assertIn('09:35',r['ledger'][0]['entry_time']);self.assertEqual(r['ledger'][0]['reason'],'eod');self.assertLess(r['final'],500)
 def test_stop_first(self):
  d,_=prepare(self.raw());b=d['2024-06-03']['F'];b[0]['daily_momentum']=True;b[1].update(h=12,l=8)
  r=run(d,Config(strategy='momentum_daily_eod'),'2024','2025');self.assertEqual(r['ledger'][0]['reason'],'entry_bar_stop')
 def test_unaffordable(self):
  raw=self.raw()
  for b in raw:b.update(o=1000,h=1010,l=990,c=1000)
  d,_=prepare(raw);d['2024-06-03']['F'][0]['daily_momentum']=True
  r=run(d,Config(strategy='momentum_daily_eod'),'2024','2025');self.assertEqual(r['trades'],0)
 def test_trailing_next_bar(self):
  d,_=prepare(self.raw());b=d['2024-06-03']['F'];b[0]['daily_momentum']=True;b[1]['l']=9.99;b[2].update(h=10.15,l=9.98);b[3].update(o=9.99,h=10.1,l=9.9)
  r=run(d,Config(strategy='momentum_daily_eod',target_r=5,trail_r=1),'2024','2025');self.assertIn('09:45',r['ledger'][0]['exit_time'])
if __name__=='__main__':unittest.main()
