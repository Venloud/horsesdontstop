"""Synthetic executions only; no broker/network/database access or release."""
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from types import SimpleNamespace

import pytest

from bot.paper_accounting import LedgerEvent, LedgerOrder, LedgerPosition, reconcile_posttrade_ledger
from bot.paper_equity_risk import ObservedPaperEquity
from bot.paper_safety import TradeBlocked


def fixture():
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=1)
    account = SimpleNamespace(id='paper-only-fixture', created_at=start-timedelta(days=1), currency='USD', cash='483.5', equity='527.5')
    def event(i,kind,minutes,**kw):
        return LedgerEvent(i,(start+timedelta(minutes=minutes)).isoformat(),kind,True,**kw)
    orders = [LedgerOrder('entry','F','buy','5','client-entry','client-entry','filled','12',broker_verified=True,journal_verified=True),
              LedgerOrder('stop','F','sell','1','client-stop','client-entry','filled',parent_order_id='entry',broker_verified=True,journal_verified=True)]
    events = [event('fund','deposit',0,amount='500'), event('fill1','fill',1,order_id='entry',quantity='2',price='10'),
              event('fill2','fill',2,order_id='entry',quantity='3',price='12'),
              event('deposit','deposit',3,amount='50'),event('withdrawal','withdrawal',4,amount='-25'),
              event('exit','fill',5,order_id='stop',quantity='1',price='15'),event('fee','fee',6,amount='-0.5')]
    return dict(account=account, events=events, orders=orders,
        positions=[LedgerPosition('F','4','11',now.isoformat(),True)],
        expected_account_hash=hashlib.sha256(account.id.encode()).hexdigest(),observed_at=now.isoformat(),
        activities_complete=True,journal_complete=True)


def test_partial_fills_linked_exit_fees_and_future_flows_conserve_every_dollar():
    r=reconcile_posttrade_ledger(**fixture())
    assert Decimal(r['cash'])==Decimal('483.5') and Decimal(r['equity'])==Decimal('527.5')
    assert Decimal(r['net_external_funding'])==525 and Decimal(r['realized_gross_pnl'])==5
    assert Decimal(r['realized_net_pnl'])==Decimal('4.5') and Decimal(r['unrealized_pnl'])==-2
    assert Decimal(r['open_cost_basis'])==46 and r['positions']=={'F':'4'}
    assert not r['risk_history_complete'] and r['daily_loss_pct'] is None
    assert not r['can_submit_orders']


@pytest.mark.parametrize('case',['missing_history','missing_journal','duplicate','unknown_fee','positive_fee',
    'unlinked_exit','forged_client','forged_order','oversold','cash','equity','extra_shares','missing_shares',
    'missing_execution','status','unverified_position','stale_position','unresolved_reservation','future_event'])
def test_missing_contradictory_or_unapproved_evidence_fails_closed(case):
    x=fixture()
    if case=='missing_history':x['activities_complete']=False
    if case=='missing_journal':x['journal_complete']=False
    if case=='duplicate':x['events'].append(x['events'][-1])
    if case=='unknown_fee':x['events'][-1]=replace(x['events'][-1],kind='unexplained_adjustment')
    if case=='positive_fee':x['events'][-1]=replace(x['events'][-1],amount='0.5')
    if case=='unlinked_exit':x['orders'][1]=replace(x['orders'][1],parent_order_id='other-entry')
    if case=='forged_client':x['orders'][0]=replace(x['orders'][0],client_order_id='substituted')
    if case=='forged_order':x['orders'][0]=replace(x['orders'][0],journal_verified=False)
    if case=='oversold':
        x['orders'][1]=replace(x['orders'][1],quantity='6')
        x['events'][-2]=replace(x['events'][-2],quantity='6')
    if case=='cash':x['account'].cash='484'
    if case=='equity':x['account'].equity='528'
    if case=='extra_shares':x['positions'][0]=replace(x['positions'][0],quantity='5')
    if case=='missing_shares':x['positions'][0]=replace(x['positions'][0],quantity='1')
    if case=='missing_execution':del x['events'][2]
    if case=='status':x['orders'][0]=replace(x['orders'][0],status='new')
    if case=='unverified_position':x['positions'][0]=replace(x['positions'][0],broker_verified=False)
    if case=='stale_position':x['positions'][0]=replace(x['positions'][0],at=(datetime.now(timezone.utc)-timedelta(minutes=3)).isoformat())
    if case=='unresolved_reservation':x['pending_client_order_ids']=['missing-broker-order']
    if case=='future_event':x['events'][-1]=replace(x['events'][-1],at=(datetime.now(timezone.utc)+timedelta(minutes=1)).isoformat())
    with pytest.raises(TradeBlocked):reconcile_posttrade_ledger(**x)


def test_restart_replays_persisted_evidence_without_double_counting(tmp_path):
    x=fixture();original=reconcile_posttrade_ledger(**x)
    path=tmp_path/'durable-accounting-evidence.json'
    path.write_text(json.dumps({k:[asdict(e) for e in x[k]] for k in ['events','orders','positions']}))
    stored=json.loads(path.read_text())
    for k,cls in [('events',LedgerEvent),('orders',LedgerOrder),('positions',LedgerPosition)]:
        x[k]=[cls(**row) for row in stored[k]]
    assert reconcile_posttrade_ledger(**x)==original


def test_partially_filled_entry_reserves_only_remaining_approved_notional():
    x=fixture();x['orders']=x['orders'][:1]
    x['orders'][0]=replace(x['orders'][0],status='partially_filled')
    x['events']=x['events'][:2];x['account'].cash='480';x['account'].equity='502'
    x['positions'][0]=replace(x['positions'][0],quantity='2')
    r=reconcile_posttrade_ledger(**x)
    assert Decimal(r['open_entry_reserved_notional'])==36 and Decimal(r['unrealized_pnl'])==2


def cash_history_case(monkeypatch):
    import bot.paper_accounting as accounting
    # Fix only the inspection clock; no broker/calendar evidence is fabricated
    # outside this synthetic scenario. Monday opening and previous Friday.
    now=datetime(2026,10,12,14,tzinfo=timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return now
    monkeypatch.setattr(accounting,'datetime',Clock)
    account=SimpleNamespace(id='paper-only-fixture',created_at=now-timedelta(days=4),currency='USD',cash='485',equity='485')
    times=[datetime(2026,10,9,13,31,tzinfo=timezone.utc),datetime(2026,10,12,13,31,tzinfo=timezone.utc),now]
    events=[LedgerEvent('fund',(times[0]-timedelta(minutes=1)).isoformat(),'deposit',True,amount='500'),
            LedgerEvent('fee',(now-timedelta(minutes=1)).isoformat(),'fee',True,amount='-15')]
    samples=[ObservedPaperEquity(t,Decimal(v),Decimal(v)) for t,v in zip(times,['500','500','485'])]
    return dict(account=account,events=events,orders=[],positions=[],
        expected_account_hash=hashlib.sha256(account.id.encode()).hexdigest(),observed_at=now.isoformat(),
        activities_complete=True,journal_complete=True,equity_observations=samples,
        all_equity_samples_accounted_for=True,exchange_calendar_verified=True)


def test_verified_sampled_history_applies_existing_daily_and_peak_risk_limits(monkeypatch):
    r=reconcile_posttrade_ledger(**cash_history_case(monkeypatch))
    assert r['risk_history_complete'] and Decimal(r['daily_loss_pct'])==3
    assert r['daily_loss_limit_breached'] is True and Decimal(r['drawdown_pct'])==3
    assert not r['can_submit_orders']


def test_flat_historical_equity_cannot_fabricate_a_peak(monkeypatch):
    x=cash_history_case(monkeypatch)
    x['equity_observations'][0]=replace(x['equity_observations'][0],equity_usd=Decimal('1000'))
    with pytest.raises(TradeBlocked,match='historical equity/positions'):
        reconcile_posttrade_ledger(**x)


@pytest.mark.parametrize('bad',['missing','quantity','unverified','valuation','timestamp',None])
def test_nonflat_historical_samples_require_independent_matching_position_marks(monkeypatch,bad):
    x=cash_history_case(monkeypatch);now=datetime.fromisoformat(x['observed_at']);old=now-timedelta(minutes=10)
    x['events']=x['events'][:1]+[LedgerEvent('fill',(now-timedelta(minutes=20)).isoformat(),'fill',True,order_id='entry',quantity='1',price='10')]
    x['orders']=[LedgerOrder('entry','F','buy','1','client','client','filled','10',broker_verified=True,journal_verified=True)]
    x['positions']=[LedgerPosition('F','1','11',now.isoformat(),True)]
    x['account'].cash='490';x['account'].equity='501'
    x['equity_observations']=x['equity_observations'][:2]+[ObservedPaperEquity(old,Decimal('502'),Decimal('490')),ObservedPaperEquity(now,Decimal('501'),Decimal('490'))]
    mark=LedgerPosition('F','1','12',old.isoformat(),True)
    if bad=='quantity':mark=replace(mark,quantity='2')
    if bad=='unverified':mark=replace(mark,broker_verified=False)
    if bad=='valuation':mark=replace(mark,market_price='999')
    if bad=='timestamp':mark=replace(mark,at=(old-timedelta(seconds=1)).isoformat())
    if bad!='missing':x['historical_positions']={old.isoformat():[mark]}
    if bad is not None:
        with pytest.raises(TradeBlocked):reconcile_posttrade_ledger(**x)
    else:
        r=reconcile_posttrade_ledger(**x)
        assert r['risk_history_complete'] and Decimal(r['verified_sampled_peak'])==502


def test_changed_mark_and_equity_change_evidence_digest():
    x=fixture();first=reconcile_posttrade_ledger(**x)
    x['positions'][0]=replace(x['positions'][0],market_price='12');x['account'].equity='531.5'
    second=reconcile_posttrade_ledger(**x)
    assert first['evidence_sha256']!=second['evidence_sha256']


def test_overlapping_entry_parents_refuse_ambiguous_fifo_attribution():
    x=fixture()
    x['orders'].append(replace(x['orders'][0],order_id='entry2',quantity='1',limit_price='8',client_order_id='entry2',journal_client_order_id='entry2'))
    at=datetime.fromisoformat(x['events'][2].at)+timedelta(seconds=30)
    x['events'].insert(3,LedgerEvent('overlap',at.isoformat(),'fill',True,order_id='entry2',quantity='1',price='8'))
    x['account'].cash='475.5';x['account'].equity='530.5';x['positions'][0]=replace(x['positions'][0],quantity='5')
    with pytest.raises(TradeBlocked,match='overlapping entry'):
        reconcile_posttrade_ledger(**x)


def test_closed_entry_can_reenter_without_reusing_its_old_cost_basis():
    x=fixture();at=datetime.fromisoformat(x['events'][0].at)
    first=replace(x['orders'][0],quantity='1',limit_price='10')
    second=replace(first,order_id='entry2',client_order_id='entry2',journal_client_order_id='entry2',limit_price='12')
    x['orders']=[first,x['orders'][1],second]
    x['events']=[x['events'][0],LedgerEvent('buy1',(at+timedelta(minutes=1)).isoformat(),'fill',True,order_id='entry',quantity='1',price='10'),
        LedgerEvent('exit1',(at+timedelta(minutes=2)).isoformat(),'fill',True,order_id='stop',quantity='1',price='15'),
        LedgerEvent('buy2',(at+timedelta(minutes=3)).isoformat(),'fill',True,order_id='entry2',quantity='1',price='12')]
    x['positions'][0]=replace(x['positions'][0],quantity='1',market_price='15')
    x['account'].cash='493';x['account'].equity='508'
    r=reconcile_posttrade_ledger(**x)
    assert Decimal(r['realized_gross_pnl'])==5 and Decimal(r['unrealized_pnl'])==3
    assert Decimal(r['open_cost_basis'])==12
