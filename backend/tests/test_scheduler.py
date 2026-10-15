from subprocess import CompletedProcess
from unittest.mock import patch

from radar.scheduler import run_cycle


def test_scheduler_runs_dbt_only_after_successful_refresh():
    with patch("radar.scheduler.subprocess.run") as run:
        run.return_value = CompletedProcess([], 0)
        assert run_cycle() is True
        assert run.call_count == 2
        assert run.call_args_list[1].args[0][:2] == ["dbt", "build"]


def test_scheduler_surfaces_partial_refresh_and_skips_dbt():
    with patch("radar.scheduler.subprocess.run") as run:
        run.return_value = CompletedProcess([], 1)
        assert run_cycle() is False
        assert run.call_count == 1


def test_scheduler_surfaces_dbt_failure():
    with patch("radar.scheduler.subprocess.run") as run:
        run.side_effect = [CompletedProcess([], 0), CompletedProcess([], 1)]
        assert run_cycle() is False
