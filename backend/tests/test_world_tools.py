"""Slice 2 tests: the read-only world tools against the real loaded world.

The loaded world is Elodia as shipped: Oakhaven only, with Elara, Brom,
and the injected debug NPC The Architect; exits north to Whispering
Woods and south to Riverford, neither generated yet. The tools must
answer from exactly that — including reporting a named exit destination
as ungenerated rather than inventing it.
"""
import os
import sys
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
os.environ["GEMINI_API_KEY"] = "test_key"

sys.modules["google"] = __import__("unittest.mock").mock.MagicMock()
sys.modules["google.genai"] = __import__("unittest.mock").mock.MagicMock()
sys.modules["google.genai.types"] = __import__("unittest.mock").mock.MagicMock()

from backend.game_state_manager import GameStateManager
from backend.game_world import initialize_game_world
from backend.world_tools import (
    describe_here,
    execute_tool_call,
    list_nearby,
    locate_entity,
    parse_tool_calls,
)


class TestWorldTools(unittest.TestCase):
    def setUp(self):
        initialize_game_world("Elodia")
        self.gsm = GameStateManager()
        self.session_id = "test_session_world_tools"
        self.gsm.create_session(self.session_id, "TestPlayer")

    def test_locate_character(self):
        res = locate_entity("Brom", self.session_id)
        self.assertTrue(res["found"])
        self.assertEqual(res["kind"], "character")
        self.assertEqual(res["name"], "Brom")
        self.assertIn("Oakhaven", res["where"])
        self.assertEqual(res["occupation"], "Blacksmith")
        self.assertIn("dwarf", res["description"])

    def test_locate_feature_by_partial_name(self):
        # "smithy" is a partial name; "smith" is a token inside it.
        res = locate_entity("smith", self.session_id)
        self.assertTrue(res["found"])
        self.assertEqual(res["kind"], "feature")
        self.assertEqual(res["name"], "Smithy")
        self.assertEqual(res["where"], "Oakhaven")

    def test_locate_location(self):
        res = locate_entity("Oakhaven", self.session_id)
        self.assertTrue(res["found"])
        self.assertEqual(res["kind"], "location")
        self.assertIn("Whispering Woods", res["where"])

    def test_locate_named_exit_destination_is_ungenerated(self):
        # The exit exists; the place behind it has not been generated.
        # The tool must say exactly that, not invent a description.
        for name in ("Whispering Woods", "Riverford"):
            res = locate_entity(name, self.session_id)
            self.assertTrue(res["found"], name)
            self.assertEqual(res["kind"], "location")
            self.assertIn("not yet generated", res["where"])

    def test_locate_pure_miss(self):
        res = locate_entity("wizard's tower", self.session_id)
        self.assertFalse(res["found"])
        self.assertEqual(res["near_matches"], [])

    def test_locate_compound_miss_returns_near_matches_not_a_hit(self):
        # "Brom's Smithy" is neither Brom nor the Smithy: a miss whose
        # near matches are both. The prototype's first toolbox bug was
        # matching this as a hit on Brom.
        res = locate_entity("Brom's Smithy", self.session_id)
        self.assertFalse(res["found"])
        names = {m["name"] for m in res["near_matches"]}
        self.assertEqual(names, {"Brom", "Smithy"})

    def test_describe_here(self):
        res = describe_here(self.session_id)
        self.assertEqual(res["location"], "Oakhaven")
        self.assertIn("Market Square", res["features"])
        self.assertIn("Smithy", res["features"])
        self.assertEqual(res["exits"]["north"], "Whispering Woods")
        self.assertEqual(res["exits"]["south"], "Riverford")
        present = " ".join(res["characters_present"])
        self.assertIn("Elara", present)
        self.assertIn("Brom", present)

    def test_list_nearby_marks_ungenerated_exits(self):
        res = list_nearby(self.session_id)
        self.assertEqual(res["here"], "Oakhaven")
        self.assertIn("north to Whispering Woods (not yet generated)",
                      res["exit_destinations"])
        self.assertIn("south to Riverford (not yet generated)",
                      res["exit_destinations"])

    def test_execute_unknown_tool_returns_error_not_exception(self):
        res = execute_tool_call(self.session_id, "teleport", {"name": "Brom"})
        self.assertIn("error", res)
        self.assertIn("teleport", res["error"])

    def test_execute_locate_needs_a_name(self):
        res = execute_tool_call(self.session_id, "locate_entity", {})
        self.assertIn("error", res)

    def test_parse_tool_calls_fenced_and_raw(self):
        fenced = '```json\n{"tool_calls": [{"tool": "locate_entity", "args": {"name": "Brom"}}]}\n```'
        self.assertEqual(
            parse_tool_calls(fenced),
            [{"tool": "locate_entity", "args": {"name": "Brom"}}])
        raw = '{"tool_calls": [{"tool": "describe_here", "args": {}}]}'
        self.assertEqual(
            parse_tool_calls(raw),
            [{"tool": "describe_here", "args": {}}])

    def test_parse_tool_calls_rejects_non_tool_json(self):
        self.assertIsNone(parse_tool_calls('{"dialogue": "Hello."}'))
        self.assertIsNone(parse_tool_calls("just some prose, no json"))
        self.assertIsNone(parse_tool_calls(""))


if __name__ == "__main__":
    unittest.main()
