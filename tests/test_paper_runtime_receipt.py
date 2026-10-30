import json
import pytest
from bot.paper_runtime_receipt import write_runtime_receipt
from bot.paper_safety import TradeBlocked


def event():
 return dict(worker_id='paper-'+'a'*32,sequence=2,status='closed_flat_idle',external_ack=True)


def test_atomic_runtime_receipt_is_observation_not_trading_permission(tmp_path):
 p=tmp_path/'runtime.json';write_runtime_receipt(p,event());r=json.loads(p.read_text())
 assert r['sequence']==2 and r['external_ack'] is True and r['pid']>0
 assert r['status']=='closed_flat_idle' and 'can_trade' not in r
 assert len(list(tmp_path.iterdir()))==1


@pytest.mark.parametrize('changes',[{'external_ack':'yes'},{'sequence':True},{'status':'trading_ready'},{'token':'SECRET'}])
def test_invalid_receipt_is_rejected_before_write(tmp_path,changes):
 p=tmp_path/'r.json';r=event();r.update(changes)
 with pytest.raises(TradeBlocked):write_runtime_receipt(p,r)
 assert not p.exists()
