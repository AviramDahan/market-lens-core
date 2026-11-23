import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('code_guard', Path(__file__).resolve().parents[1]/'scripts/check_code_only_repository.py')
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


def test_runtime_and_credentials_are_refused():
    for name in ('agent_results/run.jsonl', 'agent_tracker/live.xlsx', '.runtime/state.json',
                 'deploy/host.env', 'deploy/runtime.env', 'data/setups.sqlite'):
        assert guard.violations([(name, 1)])


def test_source_and_example_configuration_are_allowed():
    assert not guard.violations([('app/main.py', 500), ('.env.example', 500), ('tests/test_archive.py', 500)])


def test_binary_growth_is_bounded():
    assert guard.violations([('docs/oversized.zip', guard.MAX_FILE_BYTES+1)])
    assert guard.violations([(f'docs/{i}.txt', guard.MAX_FILE_BYTES) for i in range(6)])


def test_legacy_runtime_workflows_are_fail_closed():
    import yaml
    root = Path(__file__).resolve().parents[1] / '.github/workflows'
    for path in root.glob('*.yml'):
        if path.name == 'market-lens-qa.yml':
            continue
        payload = yaml.safe_load(path.read_text())
        assert all(job.get('if') == '${{ false }}' for job in payload['jobs'].values()), path.name
