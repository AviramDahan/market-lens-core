"""Retire only old unreferenced Market Lens build images, keeping two rollbacks."""
from datetime import datetime, timedelta, timezone, time as wall_time
import json
import subprocess
from zoneinfo import ZoneInfo

from .cold_backups import atomic_json
from .notify_ops import env_value
from .build_cache_maintenance import STATE


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True, timeout=45)


def candidates(images, container_images, *, now):
    keep = set(container_images)
    for role in ('market-lens-runtime-web', 'market-lens-runtime-worker'):
        related = [r for r in images if any(t.split(':')[0] == role for t in (r.get('RepoTags') or []))]
        keep.update(r['Id'] for r in sorted(related, key=lambda r:r['Created'], reverse=True)[:3])
    for row in images:
        if any(tag.endswith(':latest') for tag in (row.get('RepoTags') or [])):
            keep.add(row['Id'])
    result = []
    for row in images:
        tags = row.get('RepoTags') or []
        created = datetime.fromisoformat(row['Created'][:26]+'+00:00')
        if (not tags or row['Id'] in keep or any(not tag.startswith('market-lens-') for tag in tags)
                or created > now-timedelta(days=14)):
            continue
        result.append({'id':row['Id'], 'tags':tags, 'created':row['Created'],
                       'layers':row.get('RootFS',{}).get('Layers',[])})
    return result


def referenced_images():
    ids = docker('ps','-aq').split()
    if not ids:
        return set()
    return set(docker('inspect','--format','{{.Image}}',*ids).split())


def retention_once(*, now=None, state=STATE):
    if env_value('MARKET_LENS_IMAGE_RETENTION_ENABLED').lower() != 'true':
        return {'status':'DISABLED','removed':[]}
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo('America/New_York'))
    if local.weekday()<5 and wall_time(9)<=local.time()<=wall_time(16,30):
        return {'status':'DEFERRED_MARKET_WINDOW','removed':[]}
    from .build_cache_maintenance import busy_reason
    if busy_reason():
        return {'status':'SKIPPED_BUSY','removed':[]}
    ids = sorted(set(docker('image','ls','-q','--no-trunc','--filter','reference=market-lens*').split()))
    images = json.loads(docker('image','inspect',*ids)) if ids else []
    selected = candidates(images, referenced_images(), now=now)[:4]
    result = {'status':'COMPLETE','at':now.isoformat(),'candidates':selected,'removed':[]}
    atomic_json(state/'image-retention-last.json',result)
    for row in selected:
        if busy_reason():
            result['status']='DEFERRED_BUSY';break
        current = json.loads(docker('image','inspect',row['id']))[0]
        if row['id'] in referenced_images() or sorted(current.get('RepoTags') or []) != sorted(row['tags']):
            continue
        # No force, no system prune, no stopped-container or volume deletion.
        docker('image','rm',*row['tags'])
        result['removed'].append(row['id'])
        atomic_json(state/'image-retention-last.json',result)
    return result
