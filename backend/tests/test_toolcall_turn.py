"""Slice 2 tests: the tool-call loop around the structured turn.

The provider is scripted (AsyncMock) so these tests run with no model:
a canned tool-calls reply, then a canned TurnResponse, and the loop
must execute the tool for real, feed the JSON result back into the
next provider call, and return the validated turn plus the record of
what it called.
"""
import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
os.environ["GEMINI_API_KEY"] = "test_key"

sys.modules["google"] = __import__("unittest.mock").mock.MagicMock()
sys.modules["google.genai"] = __import__("unittest.mock").mock.MagicMock()
sys.modules["google.genai.types"] = __import__("unittest.mock").mock.MagicMock()

from backend.game_state_manager import GameStateManager
from backend.game_world import initialize_game_world
from backend.gemini_service import get_structured_turn_with_tools


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _turn(dialogue, **extra):
    out = {"dialogue": dialogue}
    out.update(extra)
    return json.dumps(out)


class TestToolCallTurn(unittest.TestCase):
    def setUp(self):
        initialize_game_world("Elodia")
        self.gsm = GameStateManager()
        self.session_id = "test_session_toolcall_turn"
        self.gsm.create_session(self.session_id, "TestPlayer")
        self.history = [{"role": "user", "parts": ["Where is Brom?"]}]
        self.system = "You are Elara. Test system instruction."

    def test_tool_call_then_answer(self):
        replies = [
            '{"tool_calls": [{"tool": "locate_entity", "args": {"name": "Brom"}}]}',
            _turn("Brom is here in Oakhaven, at the smithy."),
        ]
        with patch("backend.gemini_service.generate_llm_response",
                   new=AsyncMock(side_effect=replies)) as mock_generate:
            turn, calls = _run(get_structured_turn_with_tools(
                self.history, system_instruction=self.system,
                session_id=self.session_id))
        self.assertEqual(turn.dialogue, "Brom is here in Oakhaven, at the smithy.")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["tool"], "locate_entity")
        self.assertTrue(calls[0]["result"]["found"])
        self.assertEqual(calls[0]["result"]["name"], "Brom")
        # The tool result was fed back into the second provider call.
        self.assertEqual(mock_generate.call_count, 2)
        second_history = mock_generate.call_args_list[1].args[0]
        fed_back = json.dumps(second_history)
        self.assertIn("[Tool results]", fed_back)
        self.assertIn("Brom", fed_back)

    def test_direct_answer_makes_no_tool_call(self):
        replies = [_turn("Brom is right here.")]
        with patch("backend.gemini_service.generate_llm_response",
                   new=AsyncMock(side_effect=replies)) as mock_generate:
            turn, calls = _run(get_structured_turn_with_tools(
                self.history, system_instruction=self.system,
                session_id=self.session_id))
        self.assertEqual(turn.dialogue, "Brom is right here.")
        self.assertEqual(calls, [])
        self.assertEqual(mock_generate.call_count, 1)

    def test_cap_is_enforced_and_turn_still_answers(self):
        tool_reply = ('{"tool_calls": [{"tool": "describe_here", "args": {}}]}')
        replies = [tool_reply, tool_reply, tool_reply,
                   _turn("Oakhaven is a bustling town.")]
        with patch("backend.gemini_service.generate_llm_response",
                   new=AsyncMock(side_effect=replies)) as mock_generate:
            turn, calls = _run(get_structured_turn_with_tools(
                self.history, system_instruction=self.system,
                session_id=self.session_id))
        # Only two tool executions despite three tool-call replies.
        self.assertEqual(len(calls), 2)
        self.assertEqual(turn.dialogue, "Oakhaven is a bustling town.")
        # The third tool-call reply met the spent-budget notice, and the
        # provider was asked once more for the final answer.
        self.assertEqual(mock_generate.call_count, 4)
        fourth_history = mock_generate.call_args_list[3].args[0]
        self.assertIn("used all your tool calls", json.dumps(fourth_history))

    def test_miss_result_is_fed_back(self):
        replies = [
            '{"tool_calls": [{"tool": "locate_entity", "args": {"name": "wizard\'s tower"}}]}',
            _turn("I know of no wizard's tower in Elodia."),
        ]
        with patch("backend.gemini_service.generate_llm_response",
                   new=AsyncMock(side_effect=replies)):
            turn, calls = _run(get_structured_turn_with_tools(
                self.history, system_instruction=self.system,
                session_id=self.session_id))
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0]["result"]["found"])
        self.assertEqual(calls[0]["result"]["near_matches"], [])
        self.assertEqual(turn.dialogue, "I know of no wizard's tower in Elodia.")

    def test_history_is_not_mutated_with_tool_exchanges(self):
        replies = [
            '{"tool_calls": [{"tool": "locate_entity", "args": {"name": "Brom"}}]}',
            _turn("Brom is here."),
        ]
        with patch("backend.gemini_service.generate_llm_response",
                   new=AsyncMock(side_effect=replies)):
            _run(get_structured_turn_with_tools(
                self.history, system_instruction=self.system,
                session_id=self.session_id))
        self.assertEqual(self.history,
                         [{"role": "user", "parts": ["Where is Brom?"]}])


if __name__ == "__main__":
    unittest.main()
