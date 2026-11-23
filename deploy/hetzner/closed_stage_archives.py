"""Archive inactive scanner evidence byte-exactly; never prune live results or SQLite."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone, time as wall_time
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import time
from zoneinfo import ZoneInfo

from . import cold_backups as cold
from .github_archive import GitHubArchive
from .notify_ops import env_value

STATE = Path('/home/trader/market-lens-runtime-state')
STAGES = STATE / 'scanner-staging'
EXPORTS = Path('/home/trader/market-lens-backups/closed-stages')
ALLOWED = {'agent_results', 'agent_tracker', 'runtime', 'rollback'}


def inventory(root: Path) -> dict:
    if root.is_symlink() or not root.is_dir() or not re.fullmatch('[a-f0-9]{32}', root.name):
        raise ValueError('Unrecognized closed scanner stage')
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('Linked stage evidence must remain local')
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if relative.parts[0] not in ALLOWED:
            raise ValueError('Unrecognized stage evidence path')
        if path.suffix.lower() in {'.sqlite', '.sqlite3', '.db'}:
            raise ValueError('Database persistence is outside stage archival scope')
        cold.safe_file(root, path)
        stat = path.stat()
        result[relative.as_posix()] = {'bytes': stat.st_size, 'sha256': cold.sha256(path)}
    if not result or sum(v['bytes'] for v in result.values()) > 512 * 1024**2:
        raise ValueError('Closed stage inventory is empty or exceeds its bound')
    return result


def mounted_stages():
    ids = subprocess.check_output(['docker', 'ps', '-q'], text=True, timeout=15).split()
    if not ids:
        return []
    raw = subprocess.check_output(['docker', 'inspect', '--format', '{{json .Mounts}}', *ids],
                                  text=True, timeout=20)
    return [Path(m['Source']) for line in raw.splitlines() for m in json.loads(line)
            if m.get('Type') == 'bind']


def is_mounted(stage: Path, sources) -> bool:
    # Ignore the broad read-only runtime mount; protect every dedicated stage mount.
    return any(p.resolve().is_relative_to(stage.resolve()) for p in sources)


def offload_stage(stage: Path, store, *, stages=STAGES, exports=EXPORTS,
                  records=None, mounts=None, retire=True) -> dict:
    stage, stages = Path(stage), Path(stages)
    if (stage.parent.resolve() != stages.resolve() or stages.is_symlink()
            or stage.resolve() != stage or stage.is_symlink()):
        raise ValueError('Stage is outside the approved root')
    state = stages.parent
    if (state/'checkpoints/pending.json').exists() or (state/'checkpoints/local_write.json').exists():
        raise ValueError('Unresolved live transaction; no stage retirement')
    mounts = mounted_stages if mounts is None else mounts
    if is_mounted(stage, mounts()):
        raise ValueError('Stage is still mounted by a worker')
    records = Path(records) if records else state/'maintenance/closed-stage-archives'
    records.mkdir(parents=True, exist_ok=True)
    exports.mkdir(parents=True, exist_ok=True)
    record_path = records/(stage.name+'.json')
    archive = exports/(stage.name+'.tar.gz')
    pointer = archive.with_name(archive.name+'.archive.json')
    if record_path.exists():
        record = json.loads(record_path.read_text())
        if (record.get('version') != 'closed_scanner_stage_v1' or record.get('stage_id') != stage.name
                or record.get('original_root') != str(stage)):
            raise ValueError('Stage record identity mismatch')
        expected = record['files']
    else:
        expected = inventory(stage)
        record = {'version': 'closed_scanner_stage_v1', 'stage_id': stage.name,
                  'original_root': str(stage), 'files': expected,
                  'status': 'PREPARED', 'created_at': datetime.now(timezone.utc).isoformat()}
        cold.atomic_json(record_path, record)
    current = inventory(stage)
    # A partially completed cleanup can resume only for unchanged remaining files.
    if any(name not in expected or row != expected[name] for name, row in current.items()):
        raise ValueError('Stage evidence changed; retain it locally')
    if not pointer.exists():
        if current != expected:
            raise ValueError('Evidence missing before remote verification')
        raw_size = sum(v['bytes'] for v in expected.values())
        if shutil.disk_usage(exports).free < 12*1024**3 + raw_size + 256*1024**2:
            raise ValueError('Insufficient archive preparation headroom')
        if archive.exists():
            if (record.get('frozen_sha256') != cold.sha256(archive)
                    or record.get('frozen_bytes') != archive.stat().st_size):
                raise ValueError('Unverified prior archive requires review')
        else:
            temporary = archive.with_name(archive.name+'.building')
            if temporary.exists():
                raise ValueError('Incomplete prior archive requires review')
            prefix = f'agent_results/closed_stage_evidence/{stage.name}'
            with tarfile.open(temporary, 'x:gz', compresslevel=6) as bundle:
                for name in expected:
                    bundle.add(stage/name, arcname=f'{prefix}/{name}', recursive=False)
                bundle.add(record_path, arcname=f'{prefix}/stage_manifest.json', recursive=False)
            if inventory(stage) != expected:
                raise ValueError('Stage changed during packing; retain original')
            cold.validate_backup(temporary)
            temporary.replace(archive)
            record.update(frozen_sha256=cold.sha256(archive), frozen_bytes=archive.stat().st_size)
            cold.atomic_json(record_path, record)
        # Validation and complete private round-trip happen before any source retirement.
        receipt = cold.offload_file(exports, archive, store)
    else:
        receipt = json.loads(pointer.read_text())
        if receipt.get('remote_verified') is not True or receipt.get('repository') != store.repository:
            raise ValueError('Untrusted stage archive receipt')
        if receipt.get('sha256') != record.get('frozen_sha256') or receipt.get('bytes') != record.get('frozen_bytes'):
            raise ValueError('Receipt does not match the frozen stage')
        metadata = {k: receipt[k] for k in ('schema_version', 'kind', 'relative_path', 'bytes', 'sha256')}
        expected_tag = 'cold-backup-v1-' + hashlib.sha256(cold.encoded(metadata)).hexdigest()
        if (metadata['relative_path'] != archive.relative_to(exports).as_posix()
                or receipt.get('release_tag') != expected_tag):
            raise ValueError('Stage receipt points to different evidence')
        store.require_private()
        cold.verify_asset(store, {'id': receipt['asset_id'], 'state': 'uploaded', 'size': receipt['bytes']},
                          receipt['sha256'], receipt['bytes'])
        cold.verify_asset(store, {'id': receipt['metadata_asset_id'], 'state': 'uploaded', 'size': len(cold.encoded(metadata))},
                          hashlib.sha256(cold.encoded(metadata)).hexdigest(), len(cold.encoded(metadata)))
    record.update(status='VERIFIED_NOT_RETIRED', receipt=receipt)
    cold.atomic_json(record_path, record)
    if not retire:
        return record
    if (state/'checkpoints/pending.json').exists() or (state/'checkpoints/local_write.json').exists():
        raise ValueError('A transaction started; verified stage remains local')
    if is_mounted(stage, mounts()):
        raise ValueError('A worker mounted the stage; retain it locally')
    remaining = inventory(stage)
    if any(name not in expected or row != expected[name] for name, row in remaining.items()):
        raise ValueError('Stage changed after verification; retain it locally')
    shutil.rmtree(stage)  # All bytes remain in the private, fully verified immutable archive.
    cold.sync_directory(stages)
    record.update(status='ARCHIVED', retired_at=datetime.now(timezone.utc).isoformat())
    cold.atomic_json(record_path, record)
    return record


def archive_once(*, stages=STAGES, exports=EXPORTS, now=None, store=None,
                 target_free=20*1024**3, max_raw_bytes=512*1024**2) -> dict:
    if env_value('MARKET_LENS_CLOSED_STAGE_ARCHIVE_ENABLED').lower() != 'true':
        return {'status': 'DISABLED', 'archived_bytes': 0}
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo('America/New_York'))
    if local.weekday() < 5 and wall_time(9) <= local.time() <= wall_time(16, 30):
        return {'status': 'DEFERRED_MARKET_WINDOW', 'archived_bytes': 0}
    if not stages.is_dir() or stages.is_symlink():
        return {'status': 'NO_STAGE_ROOT', 'archived_bytes': 0}
    from .build_cache_maintenance import busy_reason
    busy = busy_reason()
    if busy:
        return {'status': 'SKIPPED_BUSY', 'reason': busy, 'archived_bytes': 0}
    if cold.fcntl is None:
        return {'status': 'UNSUPPORTED_HOST', 'archived_bytes': 0}
    records = stages.parent/'maintenance/closed-stage-archives'
    records.mkdir(parents=True, exist_ok=True)
    with (records/'archive.lock').open('a') as lock:
        try:
            cold.fcntl.flock(lock, cold.fcntl.LOCK_EX | cold.fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'SKIPPED_CONCURRENT', 'archived_bytes': 0}
        paths = sorted((p for p in stages.iterdir() if p.is_dir() and not p.is_symlink()),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        protected = set(paths[:2]); moved = 0; receipts = []; retained = []
        deadline = time.monotonic() + 180
        store = store or GitHubArchive(env_value('MARKET_LENS_ARCHIVE_REPOSITORY'), env_value('MARKET_LENS_ARCHIVE_TOKEN'))
        for path in reversed(paths):
            if shutil.disk_usage(stages).free >= target_free or time.monotonic() >= deadline:
                break
            if path in protected or datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) > now-timedelta(hours=24):
                continue
            try:
                files = inventory(path)
                size = sum(v['bytes'] for v in files.values())
                if moved + size > max_raw_bytes:
                    continue
                result = offload_stage(path, store, stages=stages, exports=exports, records=records)
            except (ValueError, cold.UnsafeBackup, PermissionError) as exc:
                retained.append({'stage': path.name, 'reason': type(exc).__name__})
                continue
            moved += size
            receipts.append({'stage': path.name, 'release_tag': result['receipt']['release_tag'], 'bytes': size})
        return {'status': 'ARCHIVED' if receipts else 'RETAINED', 'archived_bytes': moved,
                'receipts': receipts, 'retained': retained}
