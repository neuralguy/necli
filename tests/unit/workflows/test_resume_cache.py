"""workflows/runner.py — кеш/resume переиспользует только успешные результаты."""

import json
import os

from workflows.runner import WorkflowRunner


def _write_state(run_dir, agents):
    os.makedirs(run_dir, exist_ok=True)
    state = {
        "id": os.path.basename(run_dir),
        "name": "t",
        "status": "completed",
        "phases": [{"id": "p1", "title": "P", "status": "done", "agents": agents}],
    }
    with open(os.path.join(run_dir, "state.json"), "w", encoding="utf-8") as fh:
        json.dump(state, fh)


class TestResumeCache:
    def test_loads_only_done_agents(self, tmp_path):
        # done → в кеш; failed/running (в т.ч. budget/iter-стоп) → НЕ в кеш,
        # перезапустятся. Это правильно: незавершённую работу нельзя кешировать.
        run_id = "prev-run"
        run_dir = os.path.join(str(tmp_path), ".data", "workflow_runs", run_id)
        _write_state(run_dir, [
            {"label": "a", "status": "done", "cache_key": "K_DONE", "result": {"ok": 1}},
            {"label": "b", "status": "failed", "cache_key": "K_FAIL", "result": {"error": "x"}},
            {"label": "c", "status": "running", "cache_key": "K_RUN", "result": {}},
        ])

        r = WorkflowRunner(model="m", working_dir=str(tmp_path), isolate=False,
                           cache=True, resume_from_run_id=run_id)
        r._load_resume_cache()

        assert "K_DONE" in r._cache_by_key
        assert "K_FAIL" not in r._cache_by_key
        assert "K_RUN" not in r._cache_by_key
        assert r._cache_by_key["K_DONE"] == {"ok": 1}

    def test_no_resume_id_loads_nothing(self, tmp_path):
        r = WorkflowRunner(model="m", working_dir=str(tmp_path), isolate=False, cache=True)
        r._load_resume_cache()
        assert r._cache_by_key == {}

    def test_cache_key_sensitive_to_prompt(self, tmp_path):
        r = WorkflowRunner(model="m", working_dir=str(tmp_path), isolate=False)
        k1 = r._agent_cache_key("P", "do X", {})
        k2 = r._agent_cache_key("P", "do Y", {})
        k3 = r._agent_cache_key("P", "do X", {})
        assert k1 != k2          # разный prompt → разный ключ
        assert k1 == k3          # тот же prompt → стабильный ключ

    def test_cache_key_sensitive_to_opts_and_model(self, tmp_path):
        r = WorkflowRunner(model="m1", working_dir=str(tmp_path), isolate=False)
        r2 = WorkflowRunner(model="m2", working_dir=str(tmp_path), isolate=False)
        same = r._agent_cache_key("P", "X", {"role": "coder"})
        diff_opts = r._agent_cache_key("P", "X", {"role": "reviewer"})
        diff_model = r2._agent_cache_key("P", "X", {"role": "coder"})
        assert same != diff_opts
        assert same != diff_model
