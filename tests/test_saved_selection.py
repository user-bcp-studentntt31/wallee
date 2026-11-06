"""La selección de rol, OpenSpec, repo destino y proyecto se guarda y solo se reconfirma; el feature se elige siempre."""

import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import main as app
from crew import session_state as st
from crew.roles import discover_roles, saved_roles

FEATURE = {"title": "Transfer funds", "description": "d", "acceptance_criteria": ["c"]}
PROJECTS = [{"id": "P1", "title": "Uno"}, {"id": "P2", "title": "Dos"}]
BACKLOG = [{"id": "I1", "number": 1, "title": "Primero", "status": "In Progress"},
           {"id": "I2", "number": 2, "title": "Segundo", "status": "Todo"}]
ROOT = Path(__file__).resolve().parent.parent


class StateModuleTests(unittest.TestCase):
    def test_roundtrip_ignores_unknown_keys_and_bad_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.json"
            self.assertEqual(st.load_state(path), {})
            st.save_state(path, {"role": "all", "item": {"id": "I1"}})
            self.assertEqual(st.load_state(path), {"role": "all"})  # el feature no se guarda
            path.write_text('{"role": "qa_security", "item": {"id": "x"}}', encoding="utf-8")
            self.assertEqual(st.load_state(path), {"role": "qa_security"})
            for bad in ("{roto", "[1]"):
                path.write_text(bad, encoding="utf-8")
                self.assertEqual(st.load_state(path), {})

    def test_confirm_options(self):
        state = {"role": "qa_security", "role_label": "QA & Security", "openspec_change": None,
                 "target_repo": "C:/repo", "project": {"id": "P2", "title": "Dos"}}
        with contextlib.redirect_stdout(io.StringIO()):
            for answer, expected in (("", st.USE), ("S", st.USE), ("c", st.CHANGE), ("n", st.CHANGE)):
                self.assertEqual(st.confirm_saved(state, ask=lambda _: answer), expected)
            replies = iter(["r", "?", ""])  # ya no existe «r»: se vuelve a preguntar
            self.assertEqual(st.confirm_saved(state, ask=lambda _: next(replies)), st.USE)
        text = "\n".join(st.describe(state))
        self.assertIn("QA & Security", text)
        self.assertIn("Cambio OpenSpec: ninguno", text)
        self.assertNotIn("Feature", text)

    def test_lookups_and_saved_roles(self):
        self.assertEqual(st.find_project(PROJECTS, {"id": "P2"})["title"], "Dos")
        self.assertIsNone(st.find_project(PROJECTS, {"id": "gone"}))
        self.assertFalse(st.has_selection({}))
        roles = discover_roles()
        self.assertEqual(saved_roles(roles, False, "qa_security")[0][0].KEY, "qa_security")
        everyone, run_all = saved_roles(roles, False, "all")
        self.assertTrue(run_all)
        self.assertNotIn("data", [r.KEY for r in everyone])
        self.assertIsNone(saved_roles(roles, False, "ghost"))


class SavedSelectionRunTests(unittest.TestCase):
    def run_main(self, state_file, target, answers, asked, output=None, trace=None):
        """Ejecuta main() una vez. `asked` recoge qué se preguntó; `trace` el orden de los pasos; devuelve lo impreso."""
        qa = next(r for r in discover_roles() if r.KEY == "qa_security")
        replies = iter(answers)
        printed = io.StringIO()
        trace = [] if trace is None else trace

        def ask_input(prompt=""):
            asked.append("FEATURE" if "feature" in prompt else "PROJECT" if "proyecto" in prompt else "CONFIRM")
            answer = next(replies)
            if answer == "INTERRUPT":
                raise KeyboardInterrupt
            return answer

        def tagged(tag, value):
            return lambda *a, **k: (asked.append(tag), value)[1]

        def traced(name, value=None):
            return lambda *a, **k: (trace.append((name, a)), value)[1]

        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            out = Path(output) if output else Path(tmp)
            stack.enter_context(patch.object(app, "OUTPUT_DIR", out))
            stack.enter_context(patch.object(app, "CHECKPOINT_FILE", out / ".checkpoint.json"))
            stack.enter_context(patch.object(app, "STATE_FILE", state_file))
            stack.enter_context(patch.object(app, "choose_roles", side_effect=tagged("ROLE", ((qa,), False))))
            stack.enter_context(patch.object(app, "select_change", side_effect=tagged("CHANGE", None)))
            stack.enter_context(patch.object(app, "select_target_repo", side_effect=tagged("TARGET", target)))
            stack.enter_context(patch.object(app, "proj_tools", return_value=[]))
            stack.enter_context(patch.object(app, "list_projects", return_value=PROJECTS))
            stack.enter_context(patch.object(app, "list_project_items", return_value=BACKLOG))
            stack.enter_context(patch.object(app, "get_feature_from_item",
                                             side_effect=lambda item: {**FEATURE, "title": item["title"]}))
            stack.enter_context(patch.object(app, "run_step", side_effect=traced("run", "Informe QA")))
            stack.enter_context(patch.object(app, "hitl_approve", return_value=("s", "")))
            stack.enter_context(patch.object(app, "confirm_qa_test_execution", return_value=False))
            stack.enter_context(patch.object(app, "run_qa_tests"))
            stack.enter_context(patch.object(app, "publish_output", side_effect=traced("publish")))
            stack.enter_context(patch.object(app, "start_item", side_effect=traced("start")))
            stack.enter_context(patch.object(app, "finish_item", side_effect=traced("finish")))
            stack.enter_context(patch("builtins.input", side_effect=ask_input))
            stack.enter_context(contextlib.redirect_stdout(printed))
            app.main()
        return printed.getvalue()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_file, self.target = Path(self.tmp.name) / "state.json", Path(self.tmp.name)

    def selection_questions(self, asked):
        return [a for a in asked if a in ("ROLE", "CHANGE", "TARGET", "PROJECT", "CONFIRM")]

    def test_first_run_asks_everything_and_saves_without_the_feature(self):
        asked = []
        printed = self.run_main(self.state_file, self.target, ["2", "2"], asked)
        self.assertEqual(self.selection_questions(asked), ["ROLE", "CHANGE", "TARGET", "PROJECT"])
        saved = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual((saved["role"], saved["project"]["id"], saved["target_repo"]), ("qa_security", "P2", str(self.target)))
        self.assertIsNone(saved["openspec_change"])
        self.assertNotIn("item", saved)
        self.assertIn("In Progress", printed)  # el backlog muestra el estado de cada item
        self.assertIn("Todo", printed)

    def test_next_run_only_reconfirms_and_still_asks_for_the_feature(self):
        self.run_main(self.state_file, self.target, ["2", "2"], [])
        asked = []
        self.run_main(self.state_file, self.target, ["", "1"], asked)
        self.assertEqual(self.selection_questions(asked), ["CONFIRM"])
        self.assertEqual(asked.count("FEATURE"), 1)

    def test_changing_asks_everything_again_and_updates_the_state(self):
        self.run_main(self.state_file, self.target, ["2", "2"], [])
        asked = []
        self.run_main(self.state_file, self.target, ["c", "1", "1"], asked)
        self.assertEqual(self.selection_questions(asked), ["CONFIRM", "ROLE", "CHANGE", "TARGET", "PROJECT"])
        self.assertEqual(json.loads(self.state_file.read_text(encoding="utf-8"))["project"]["id"], "P1")

    def test_interrupting_at_the_feature_prompt_keeps_the_selection(self):
        with self.assertRaises(KeyboardInterrupt):
            self.run_main(self.state_file, self.target, ["2", "INTERRUPT"], [])
        saved = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual((saved["role"], saved["project"]["id"]), ("qa_security", "P2"))
        asked = []
        self.run_main(self.state_file, self.target, ["", "1"], asked)
        self.assertEqual(self.selection_questions(asked), ["CONFIRM"])

    def test_item_lifecycle_runs_around_the_flow_with_the_chosen_item(self):
        trace = []
        self.run_main(self.state_file, self.target, ["2", "2"], [], trace=trace)
        self.assertEqual([name for name, _ in trace], ["start", "run", "publish"])
        for name, args in trace:
            if name == "start":
                self.assertEqual((args[0]["id"], args[1]["id"]), ("P2", "I2"))  # proyecto y feature elegidos

    def test_stale_saved_pieces_are_asked_again(self):
        self.state_file.write_text(json.dumps({
            "role": "ghost", "role_label": "x", "openspec_change": None,
            "target_repo": str(Path(self.tmp.name) / "borrado"), "project": {"id": "gone", "title": "x"}}), encoding="utf-8")
        asked = []
        self.run_main(self.state_file, self.target, ["", "1", "1"], asked)
        self.assertEqual(self.selection_questions(asked), ["CONFIRM", "TARGET", "PROJECT", "ROLE"])
        saved = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual((saved["role"], saved["project"]["id"]), ("qa_security", "P1"))


class PrefetchAndPromptTests(unittest.TestCase):
    def test_prefetch_runs_in_background_and_falls_back_to_a_direct_call(self):
        from crew.prefetch import Prefetch
        pf = Prefetch()
        pf.start("a", lambda x: x * 2, 21)
        self.assertEqual(pf.get("a", lambda x: self.fail("no debía llamarse"), 21), 42)
        self.assertEqual(pf.get("a", lambda x: x + 1, 1), 2)  # ya consumido: llamada directa
        self.assertEqual(pf.get("never", lambda: "directo"), "directo")

    def test_prefetch_reraises_the_error_where_it_is_collected(self):
        from crew.prefetch import Prefetch
        pf = Prefetch()
        pf.start("boom", lambda: 1 / 0)
        with self.assertRaises(ZeroDivisionError):
            pf.get("boom", lambda: None)

    def test_prompt_keeps_square_brackets_literal(self):
        from crew import ui
        with patch("builtins.input", return_value=""), contextlib.redirect_stdout(io.StringIO()) as out:
            ui.prompt("[Enter] continuar · [c] cambiarla: ")
        self.assertIn("[c]", out.getvalue())


class StartupTests(unittest.TestCase):
    def test_importing_main_does_not_load_crewai(self):
        code = "import sys, main; sys.exit(1 if 'crewai' in sys.modules else 0)"
        done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, "crewai debe cargarse en segundo plano, no al importar main")


if __name__ == "__main__":
    unittest.main()
