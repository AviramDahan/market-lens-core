from datetime import datetime,timezone

from deploy.hetzner import image_retention as images

NOW = datetime(2026,11,23,tzinfo=timezone.utc)


def row(id, tags, day=1):
    return {'Id':id,'RepoTags':tags,'Created':f'2026-10-{day:02d}T00:00:00.000000000Z','RootFS':{'Layers':['layer']}}


def test_latest_two_rollbacks_and_any_container_image_are_protected():
    rows = [row(str(i),[f'market-lens-runtime-web:old-{i}'],i) for i in range(1,6)]
    rows += [row('active',['market-lens-old-web:v1'])]
    selected = images.candidates(rows,{'active'},now=NOW)
    assert {r['id'] for r in selected} == {'1','2'}


def test_other_projects_mixed_tags_and_latest_images_are_never_selected():
    rows = [row('other',['other-project:old']),
            row('mixed',['market-lens-web:old','other-project:old']),
            row('latest',['market-lens-worker:latest'])]
    assert images.candidates(rows,set(),now=NOW) == []


def test_recent_images_stay_local():
    r=row('new',['market-lens-test:v1']);r['Created']='2026-11-20T00:00:00.000000000Z'
    assert images.candidates([r],set(),now=NOW) == []


def test_market_window_defers_without_any_docker_mutation(monkeypatch):
    monkeypatch.setattr(images,'env_value',lambda _: 'true')
    monkeypatch.setattr(images,'docker',lambda *_: (_ for _ in ()).throw(AssertionError('No Docker call')))
    assert images.retention_once(now=datetime(2026,11,23,18,tzinfo=timezone.utc))['status']=='DEFERRED_MARKET_WINDOW'
