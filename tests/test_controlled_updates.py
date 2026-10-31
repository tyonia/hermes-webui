"""A customized checkout must refuse both update paths before touching runs/Git."""
import json

import pytest

from api import updates


@pytest.mark.parametrize("apply", [updates.apply_update, updates.apply_force_update])
@pytest.mark.parametrize("contents", [json.dumps({"update_policy": "controlled"}), "broken json"])
def test_custom_checkout_cannot_self_update(tmp_path, monkeypatch, apply, contents):
    (tmp_path / "custom-build.json").write_text(contents)
    monkeypatch.setattr(updates, "REPO_ROOT", tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("controlled update reached Git, restart, or run cancellation")

    monkeypatch.setattr(updates, "_run_git", forbidden)
    monkeypatch.setattr(updates, "_restart_blocker_snapshot", forbidden)
    result = apply("webui", channel="stable", force_active_runs=True)
    assert result["ok"] is False
    assert result["reason"] == "controlled_update"
    assert not result.get("conflict")


def test_agent_and_unmanaged_checkout_keep_existing_update_path(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(updates, "_restart_blocker_snapshot", lambda: {})
    from api import run_drain
    monkeypatch.setattr(run_drain, "hold_run_admission", lambda *a, **k: "test")
    monkeypatch.setattr(run_drain, "release_run_admission_hold", lambda *a: None)
    assert updates._run_fenced_update("webui", False, lambda: {"ok": True})["ok"]
    (tmp_path / "custom-build.json").write_text('{"update_policy":"controlled"}')
    assert updates._run_fenced_update("agent", False, lambda: {"ok": True})["ok"]


def test_cached_status_disables_only_webui_apply(tmp_path, monkeypatch):
    (tmp_path / "custom-build.json").write_text('{"update_policy":"controlled"}')
    monkeypatch.setattr(updates, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(updates, "_update_cache", {
        "webui": {"behind": 2}, "agent": {"behind": 1},
        "channel": "stable", "include_agent": True,
    })
    result = updates.cached_update_status(channel="stable")
    assert result["webui"]["manual_update"] is True
    assert result["webui"]["update_policy"] == "controlled"
    assert "manual_update" not in result["agent"]
    assert "manual_update" not in updates._update_cache["webui"]
