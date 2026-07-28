from baritone_client.automator.actions import ActionResult
from baritone_client.common.tasks import (
    ActionTask,
    MockClient,
    SequentialTask,
    normalize_task_result,
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
