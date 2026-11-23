import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deploy.hetzner import closed_stage_archives as stages
from test_cold_backups import Store


@pytest.fixture
def fixture_stage(tmp_path, monkeypatch):
    root = tmp_path/'state/scanner-staging'
    stage = root/('a'*32)
    (stage/'agent_tracker').mkdir(parents=True)
    (stage/'agent_results/diagnostics').mkdir(parents=True)
    (stage/'agent_tracker/paper.xlsx').write_bytes(b'paper portfolio evidence')
    (stage/'agent_results/diagnostics/run.json').write_text('{"status":"RUN_FAILED"}')
    exports = tmp_path/'exports'
    monkeypatch.setattr(stages.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100*1024**3))
    return stage, {'stages':root, 'exports':exports, 'mounts':lambda:[]}


def test_full_private_round_trip_precedes_retirement(fixture_stage, tmp_path):
    stage, kwargs = fixture_stage
    original = stages.inventory(stage)
    store = Store()
    record = stages.offload_stage(stage, store, **kwargs)
    assert record['status'] == 'ARCHIVED'
    assert not stage.exists()
    assert record['files'] == original
    recovered = tmp_path/'restore.tar.gz'
    stages.cold.restore_receipt(record['receipt'], recovered, store)
    import tarfile
    with tarfile.open(recovered) as tar:
        prefix = 'agent_results/closed_stage_evidence/' + record['stage_id']
        for name, expected in original.items():
            data = tar.extractfile(prefix+'/'+name).read()
            import hashlib
            assert hashlib.sha256(data).hexdigest() == expected['sha256']


@pytest.mark.parametrize('private,corrupt', [(False,False),(True,True)])
def test_bad_remote_never_deletes_stage(fixture_stage, private, corrupt):
    stage, kwargs = fixture_stage
    before = stages.inventory(stage)
    with pytest.raises(ValueError):
        stages.offload_stage(stage, Store(private=private, corrupt=corrupt), **kwargs)
    assert stages.inventory(stage) == before


def test_upload_only_can_be_verified_then_retired_without_duplicate_upload(fixture_stage):
    stage, kwargs = fixture_stage
    store = Store()
    record = stages.offload_stage(stage, store, retire=False, **kwargs)
    assert stage.exists() and record['status'] == 'VERIFIED_NOT_RETIRED'
    assert store.uploads == 2
    stages.offload_stage(stage, store, **kwargs)
    assert not stage.exists() and store.uploads == 2


def test_source_mutation_after_upload_prevents_retirement(fixture_stage):
    stage, kwargs = fixture_stage
    store = Store(); request = store.request
    def mutate(*args, **kw):
        (stage/'agent_tracker/paper.xlsx').write_bytes(b'new unique evidence')
        return request(*args, **kw)
    store.request = mutate
    with pytest.raises(ValueError, match='changed after verification'):
        stages.offload_stage(stage, store, **kwargs)
    assert (stage/'agent_tracker/paper.xlsx').read_bytes() == b'new unique evidence'


@pytest.mark.parametrize('marker', ['pending.json','local_write.json'])
def test_unresolved_live_transaction_blocks_archive(fixture_stage, marker):
    stage, kwargs = fixture_stage
    checkpoint = kwargs['stages'].parent/'checkpoints'
    checkpoint.mkdir();(checkpoint/marker).write_text('{}')
    store = Store()
    with pytest.raises(ValueError, match='Unresolved live transaction'):
        stages.offload_stage(stage, store, **kwargs)
    assert stage.exists() and store.uploads == 0


def test_dedicated_stage_mount_is_protected(fixture_stage):
    stage, kwargs = fixture_stage
    kwargs['mounts'] = lambda:[stage/'agent_tracker']
    store = Store()
    with pytest.raises(ValueError, match='still mounted'):
        stages.offload_stage(stage, store, **kwargs)
    assert stage.exists() and store.uploads == 0


def test_receipt_from_different_stage_is_rejected(fixture_stage):
    stage, kwargs = fixture_stage
    store = Store();stages.offload_stage(stage, store, retire=False, **kwargs)
    pointer = kwargs['exports']/(stage.name+'.tar.gz.archive.json')
    record = json.loads(pointer.read_text());record['relative_path']='different-stage.tar.gz'
    pointer.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='different evidence'):
        stages.offload_stage(stage, store, **kwargs)
    assert stage.exists()


def test_sensitive_content_stays_local_and_is_never_uploaded(fixture_stage):
    stage, kwargs = fixture_stage
    (stage/'agent_results/diagnostics/run.json').write_text('{"password":"never-upload"}')
    store = Store()
    with pytest.raises(ValueError):
        stages.offload_stage(stage, store, **kwargs)
    assert stage.exists() and store.uploads == 0


def test_failed_delete_retries_from_verified_receipt(fixture_stage, monkeypatch):
    stage, kwargs = fixture_stage
    store = Store(); remove = stages.shutil.rmtree
    def fail_stage_only(path, *args, **options):
        if Path(path) == stage:
            raise PermissionError()
        return remove(path, *args, **options)
    monkeypatch.setattr(stages.shutil, 'rmtree', fail_stage_only)
    with pytest.raises(PermissionError):
        stages.offload_stage(stage, store, **kwargs)
    assert stage.exists() and store.uploads == 2
    monkeypatch.setattr(stages.shutil, 'rmtree', remove)
    stages.offload_stage(stage, store, **kwargs)
    assert not stage.exists() and store.uploads == 2


def test_no_retirement_in_regular_market_window(monkeypatch):
    from datetime import datetime,timezone
    monkeypatch.setattr(stages, 'env_value', lambda _: 'true')
    result = stages.archive_once(now=datetime(2026,11,23,18,tzinfo=timezone.utc))
    assert result == {'status':'DEFERRED_MARKET_WINDOW','archived_bytes':0}


def test_outside_scope_root_is_never_removed(fixture_stage, tmp_path):
    stage, kwargs = fixture_stage
    foreign = tmp_path/'other-app'/stage.name
    foreign.mkdir(parents=True)
    with pytest.raises(ValueError, match='outside the approved root'):
        stages.offload_stage(foreign, Store(), **kwargs)
    assert foreign.exists() and stage.exists()


def test_sqlite_is_explicitly_outside_scope(fixture_stage):
    stage, kwargs = fixture_stage
    db=stage/'agent_results/setups.sqlite';db.write_bytes(b'untouched database')
    store=Store()
    with pytest.raises(ValueError,match='outside stage archival scope'):
        stages.offload_stage(stage,store,**kwargs)
    assert db.read_bytes()==b'untouched database' and store.uploads==0


def test_retry_after_receipt_write_failure_reuses_frozen_bytes(fixture_stage, monkeypatch):
    stage, kwargs=fixture_stage
    store=Store();atomic=stages.cold.atomic_json
    def fail_receipt(path, value):
        if path.name.endswith('.archive.json'):
            raise OSError('Receipt storage interrupted')
        return atomic(path,value)
    monkeypatch.setattr(stages.cold,'atomic_json',fail_receipt)
    with pytest.raises(OSError):
        stages.offload_stage(stage,store,**kwargs)
    assert stage.exists() and store.uploads==2
    frozen=(kwargs['exports']/(stage.name+'.tar.gz')).read_bytes()
    monkeypatch.setattr(stages.cold,'atomic_json',atomic)
    stages.offload_stage(stage,store,**kwargs)
    assert store.uploads==2 and store.files['backup.tar']==frozen and not stage.exists()
