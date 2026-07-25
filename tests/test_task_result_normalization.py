from baritone_client.automator.actions import ActionResult
from baritone_client.common.tasks import ActionTask, MockClient, SequentialTask


def test_sequential_task_accepts_message_based_action_failure():
    sequence = SequentialTask(
        "initial gathering",
        [ActionTask("gather wood", lambda _client: ActionResult.fail("no wood"))],
    )

    result = sequence.run(MockClient())

    assert not result.success
    assert result.reason == "Sequential task failed at gather wood: no wood"
