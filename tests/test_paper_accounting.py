import copy,hashlib
from decimal import Decimal
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
import pytest
from bot.paper_accounting import reconcile_cash_lifetime,verify_fresh_baseline
from bot.paper_safety import TradeBlocked


def evidence():
 now=datetime.now(timezone.utc);created=now-timedelta(days=3);funded=created+timedelta(minutes=2)
 account=SimpleNamespace(id='paper-account',created_at=created,currency='USD',equity='500',cash='500')
 acts=[dict(id='initial-journal',activity_type='JNLC',created_at=funded.isoformat(),net_amount='500',status='executed',currency='USD')]
 hist=dict(timestamp=[int((funded+timedelta(hours=1)).timestamp())],equity=[500],profit_loss=[0],cashflow={'JNLC':[500]})
 return dict(account=account,activities=acts,history=hist,positions=[],orders=[],expected_account_hash=hashlib.sha256(b'paper-account').hexdigest(),observed_at=now.isoformat(),activities_complete=True)


def test_authentic_cash_only_lifetime_explains_funding_not_trading_profit():
 r=reconcile_cash_lifetime(**evidence())
 assert Decimal(r['net_external_funding'])==500 and r['realized_pnl']=='0'
 assert Decimal(r['nominal_peak_equity'])==500 and r['daily_trading_loss_pct']=='0'
 assert not r['can_submit_orders']


@pytest.mark.parametrize('case',['partial','identity','exposure','fill','journal','cashflow','pnl','equity','stale'])
def test_unexplained_risk_evidence_fails_closed(case):
 x=evidence()
 if case=='partial':x['activities_complete']=False
 if case=='identity':x['expected_account_hash']='wrong'
 if case=='exposure':x['positions']=[{'symbol':'F','qty':'1'}]
 if case=='fill':x['activities'][0]['activity_type']='FILL'
 if case=='journal':x['activities'].append(dict(x['activities'][0],id='extra'))
 if case=='cashflow':x['history']['cashflow']['JNLC']=[499]
 if case=='pnl':x['history']['profit_loss']=[1]
 if case=='equity':x['account'].equity='499'
 if case=='stale':x['observed_at']=(datetime.now(timezone.utc)-timedelta(minutes=3)).isoformat()
 with pytest.raises(TradeBlocked):reconcile_cash_lifetime(**x)


def witnesses():
 w=dict(principal='monitor',authenticated=True,account_id_sha256='a'*64,equity='500',cash='500',positions=0,open_orders=0,unresolved_intents=0,at=datetime.now(timezone.utc).isoformat())
 return [w,dict(w,principal='reconciler')]


def test_fresh_baseline_preserves_unknown_prehistory_and_cannot_self_activate():
 r=verify_fresh_baseline(witnesses(),authorization_reference='owner directed fresh baseline',prior_history_digest='b'*64)
 assert r['pre_scope_pnl']=='UNKNOWN' and r['pre_scope_peak']=='UNKNOWN'
 assert not r['activated'] and not r['can_submit_orders']


@pytest.mark.parametrize('case',['same_principal','unverified','exposure','disagreement','stale'])
def test_fresh_baseline_needs_independent_current_flat_witnesses(case):
 w=witnesses()
 if case=='same_principal':w[1]['principal']='monitor'
 if case=='unverified':w[1]['authenticated']=False
 if case=='exposure':w[1]['positions']=1
 if case=='disagreement':w[1]['equity']='499'
 if case=='stale':w[1]['at']=(datetime.now(timezone.utc)-timedelta(minutes=3)).isoformat()
 with pytest.raises(TradeBlocked):verify_fresh_baseline(w,authorization_reference='owner',prior_history_digest='b'*64)
