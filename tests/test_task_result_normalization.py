from baritone_client.automator.actions import ActionResult
from baritone_client.common.tasks import (
    ActionTask,
    IncrementalProgressRequired,
    MockClient,
    PacingHoldRequired,
    ProgressRecoveryRequired,
    SequentialTask,
    normalize_task_result,
)


def test_action_task_propagates_lifecycle_signals():
    """Lifecycle signals must escape ActionTask.run as exceptions, not be
    normalized into a failed TaskResult. PhaseExecutor depends on catching
    these to yield (pacing hold) or bank progress (incremental) rather than
    treating a healthy bot as a hard phase failure.
    """
    for signal in (
        PacingHoldRequired,
        IncrementalProgressRequired,
        ProgressRecoveryRequired,
    ):
        def _raise(_client, sig=signal):
            raise sig("signal propagation check")

        task = ActionTask(f"raise-{signal.__name__}", _raise)
        try:
            task.run(MockClient())
        except signal:
            # Expected: the specific signal observed.
            pass
        except Exception as exc:  # pragma: no cover - assertion clarity
            raise AssertionError(
                f"{signal.__name__} was swallowed into {type(exc).__name__}"
            )
        else:
            raise AssertionError(
                f"{signal.__name__} did not propagate out of ActionTask.run"
            )


def test_sequential_task_accepts_message_based_action_failure():
    sequence = SequentialTask(
        "initial gathering",
        [ActionTask("gather wood", lambda _client: ActionResult.fail("no wood"))],
    )

    result = sequence.run(MockClient())

    assert not result.success
    assert result.reason == "Sequential task failed at gather wood: no wood"


def test_unsupported_none_result_fails_closed():
    result = normalize_task_result(None)

    assert not result.success
    assert "unsupported result type" in result.reason
