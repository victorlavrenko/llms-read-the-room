import importlib.util
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('rr',ROOT/'readroom_strengthen.py')
rr=importlib.util.module_from_spec(spec); sys.modules['rr']=rr; spec.loader.exec_module(rr)

def test_pair_orientation():
    p={'pairId':1,'firstTweet':'a','secondTweet':'b','goldSource':'first','xSource':'second','robotPredictions':{},'robotCloseCalls':{}}
    r=rr.pair_to_record(p)
    assert r['x']=='b' and r['y']=='a' and r['gold']=='Y'
    assert rr.canonical_source('X','frozen',r)=='second'
    assert rr.canonical_source('X','swapped',r)=='first'

def test_parse():
    pred,close,conf,method=rr.parse_output('Prediction: Y\nClose call: No')
    assert (pred,close,conf)==('Y','No',None)
    pred,close,conf,method=rr.parse_output('Prediction: X\nConfidence: 83')
    assert pred=='X' and conf==83

def test_wilson():
    lo,hi=rr.wilson(75,100)
    assert 0.65 < lo < 0.75 < hi < 0.85

def test_snapshot_explicit_path(tmp_path, monkeypatch):
    data=b'{"experimentVersion":"test","pairs":[]}'
    source=tmp_path/'source.json'
    source.write_bytes(data)
    monkeypatch.setattr(rr,'SNAPSHOT_SHA256',rr.sha256_bytes(data))
    dest=tmp_path/'cache'/'retweet_experiment_v1.private.json'
    obj=rr.download_snapshot(dest,str(source))
    assert obj['experimentVersion']=='test'
    assert dest.read_bytes()==data

def test_focused_defaults_and_job_count():
    assert rr.parse_models(None, rr.FOCUSED_MODELS) == rr.FOCUSED_MODELS
    assert rr.parse_models('all', rr.FOCUSED_MODELS) == rr.PAPER_MODELS
    assert rr.parse_experiments('focused') == ['prompt_robustness','orientation_swap','confidence']
    assert rr.parse_prompt_variants(None) == ['paper_exact','concise']
    sample=[]
    for i in range(2):
        sample.append({'pair_id':str(i+1),'x':'x','y':'y','gold':'X','x_source':'first','gold_source':'first'})
    jobs=rr.make_historical_jobs(sample, rr.FOCUSED_MODELS, 1, 3,
                                 rr.parse_experiments('focused'), rr.parse_prompt_variants(None))
    # per model/pair: 2 prompt variants + 1 swap + 1 confidence = 4
    assert len(jobs) == 3*2*4
    assert not any(j.experiment=='stability' for j in jobs)

def test_orientation_only_adds_exact_reference():
    sample=[{'pair_id':'1','x':'x','y':'y','gold':'X','x_source':'first','gold_source':'first'}]
    jobs=rr.make_historical_jobs(sample, ['m'], 0, 3, ['orientation_swap'], ['paper_exact','concise'])
    assert {(j.experiment,j.orientation,j.prompt_variant) for j in jobs} == {
        ('prompt_robustness','frozen','paper_exact'),
        ('orientation_swap','swapped','paper_exact'),
    }
