from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from run_agent import AIAgent


def _mock_response(content: str = "original"):
    msg = SimpleNamespace(content=content, tool_calls=None)
    choice = SimpleNamespace(message=msg, finish_reason="stop")
    return SimpleNamespace(
        choices=[choice],
        model="test/model",
        usage=SimpleNamespace(
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
        ),
    )


def test_run_conversation_applies_final_response_transform(monkeypatch):
    seen = {}

    def fake_transform(**kwargs):
        seen.update(kwargs)
        return {
            "final_response": "transformed",
            "completed": False,
            "partial": True,
            "metadata": {"hook": "transform_final_response"},
        }

    monkeypatch.setattr(
        "hermes_cli.plugins.apply_final_response_transforms",
        fake_transform,
    )

    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
        patch("hermes_logging.setup_logging"),
    ):
        agent = AIAgent(
            api_key="test-key",
            base_url="https://example.test/v1",
            model="test-model",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            session_id="session-1",
            platform="cli",
        )

    agent.client = MagicMock()
    agent.client.chat.completions.create.return_value = _mock_response()
    agent._cleanup_task_resources = lambda task_id: None
    agent._persist_session = lambda messages, history=None: None
    agent._save_trajectory = lambda messages, user_message, completed: None

    result = agent.run_conversation("hello", task_id="task-1")

    assert result["final_response"] == "transformed"
    assert result["completed"] is False
    assert result["partial"] is True
    assert result["metadata"] == {"hook": "transform_final_response"}
    assert seen["session_id"] == "session-1"
    assert seen["task_id"] == "task-1"
    assert seen["final_response"] == "original"
    assert seen["completed"] is True
    assert seen["partial"] is False
    assert seen["interrupted"] is False
    assert seen["model"] == "test-model"
    assert seen["platform"] == "cli"
    assert isinstance(seen["conversation_history"], list)


def test_run_conversation_applies_final_response_transform_on_api_error_return(monkeypatch):
    seen = {}

    def fake_transform(**kwargs):
        seen.update(kwargs)
        return {
            "final_response": "transformed error",
            "completed": False,
            "partial": kwargs["partial"],
        }

    monkeypatch.setattr(
        "hermes_cli.plugins.apply_final_response_transforms",
        fake_transform,
    )

    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
        patch("hermes_logging.setup_logging"),
    ):
        agent = AIAgent(
            api_key="test-key",
            base_url="https://example.test/v1",
            model="test-model",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            session_id="session-1",
            platform="cli",
        )

    agent._api_max_retries = 1
    agent.client = MagicMock()
    agent.client.chat.completions.create.side_effect = RuntimeError("boom")
    agent._cleanup_task_resources = lambda task_id: None
    agent._persist_session = lambda messages, history=None: None
    agent._save_trajectory = lambda messages, user_message, completed: None

    result = agent.run_conversation("hello", task_id="task-1")

    assert result["final_response"] == "transformed error"
    assert result["completed"] is False
    assert result["failed"] is True
    assert seen["task_id"] == "task-1"
    assert seen["final_response"].startswith("API call failed after 1 retries:")
    assert seen["completed"] is False


def test_final_response_transform_synthesizes_response_from_error(monkeypatch):
    seen = {}

    def fake_transform(**kwargs):
        seen.update(kwargs)
        return {
            "final_response": f"report: {kwargs['final_response']}",
            "completed": False,
            "partial": True,
            "metadata": {"hook": "transform_final_response"},
        }

    monkeypatch.setattr(
        "hermes_cli.plugins.apply_final_response_transforms",
        fake_transform,
    )

    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
        patch("hermes_logging.setup_logging"),
    ):
        agent = AIAgent(
            api_key="test-key",
            base_url="https://example.test/v1",
            model="test-model",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            session_id="session-1",
            platform="cli",
        )

    result = agent._apply_final_response_transforms(
        {
            "messages": [],
            "completed": False,
            "api_calls": 1,
            "error": "Context length exceeded: max compression attempts reached.",
            "partial": True,
            "failed": True,
            "compression_exhausted": True,
        },
        task_id="task-1",
    )

    assert result["final_response"] == (
        "report: Context length exceeded: max compression attempts reached."
    )
    assert result["error"] == "Context length exceeded: max compression attempts reached."
    assert result["failed"] is True
    assert result["compression_exhausted"] is True
    assert result["metadata"] == {"hook": "transform_final_response"}
    assert seen["final_response"] == "Context length exceeded: max compression attempts reached."
