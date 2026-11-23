"""Refuse runtime data, credentials and oversized generated payloads in code Git."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_ROOTS = {'agent_results', 'agent_tracker', 'charts', 'screenshots', 'data',
                   '.runtime', '.codex', '.venv', 'node_modules'}
FORBIDDEN_NAMES = {'.env', 'host.env', 'runtime.env', 'id_ed25519', 'id_rsa', 'setups.sqlite'}
MAX_FILE_BYTES = 10 * 1024**2
MAX_TOTAL_BYTES = 50 * 1024**2


def violations(files):
    errors = []; total = 0
    for name, size in files:
        path = Path(name)
        total += size
        if path.parts[0] in FORBIDDEN_ROOTS or path.name in FORBIDDEN_NAMES:
            errors.append(f'Runtime data or credential path: {name}')
        if size > MAX_FILE_BYTES:
            errors.append(f'Oversized source file: {name}')
    if total > MAX_TOTAL_BYTES:
        errors.append('Code-only source inventory exceeded 50 MiB')
    return errors


def main():
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    rows = [(name, (ROOT/name).stat().st_size) for name in names if name]
    errors = violations(rows)
    for error in errors:
        print(error)
    if not errors:
        print(f'Code-only guard passed: {len(rows)} files, {sum(size for _, size in rows)} bytes')
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
