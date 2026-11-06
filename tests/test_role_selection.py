"""Selección de rol, carpetas de salida declaradas en el prompt y contexto OpenSpec."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import main as app
from crew import roles as roles_mod
from crew.code_paths import resolve_destination
from crew.openspec_changes import Change, change_context, list_changes, role_tasks, select_change
from crew.qa_execution import TestRunResult

FEATURE = {"title": "Transfer funds", "description": "Move money", "acceptance_criteria": ["Reject empty"]}
TASKS = (
    "# Tasks\n\n- [x] 1.1 [rol: Architect] Andamiaje\n"
    "- [ ] 2.1 [rol: AI Engineer 1; prueba: QA & Security] Backend\n- [ ] 3.1 Sin rol\n"
)


class RoleDiscoveryTests(unittest.TestCase):
    def test_roles_come_from_agents_folder_in_flow_order(self):
        keys = [role.KEY for role in roles_mod.discover_roles()]
        on_disk = {p.stem for p in Path(roles_mod.agents.__path__[0]).glob("*.py")} - {"__init__"}
        self.assertEqual(keys, [key for key in roles_mod.CANONICAL_ORDER if key in on_disk])
        self.assertEqual(set(keys), on_disk)

    def test_output_folders_are_read_from_each_prompt(self):
        by_key = {role.KEY: role for role in roles_mod.discover_roles()}
        self.assertEqual(roles_mod.output_folders(by_key["qa_security"]), ("test", "security"))
        for key in ("ai_engineer_1", "ai_engineer_2"):
            self.assertEqual(set(roles_mod.output_folders(by_key[key])), {"backend-nestjs", "frontend-react"})
        self.assertEqual(roles_mod.report_folder(by_key["qa_security"]), "specs")

    def test_editing_the_prompt_changes_the_folders(self):
        role = SimpleNamespace(INSTRUCTIONS="x\n- Carpetas de salida en /output: `docs`, audit.\n"
                                            "Carpeta del informe en /output: audit")
        self.assertEqual(roles_mod.output_folders(role), ("docs", "audit"))
        self.assertEqual(roles_mod.report_folder(role), "audit")
        self.assertIsNone(roles_mod.output_folders(SimpleNamespace(INSTRUCTIONS="sin carpetas")))


class ChooseRolesTests(unittest.TestCase):
    def setUp(self):
        self.all = roles_mod.discover_roles()
        self.quiet = contextlib.redirect_stdout(io.StringIO())
        self.quiet.__enter__()

    def tearDown(self):
        self.quiet.__exit__(None, None, None)

    def test_number_selects_one_role_and_last_option_selects_all(self):
        qa_number = [r.KEY for r in self.all].index("qa_security") + 1
        picked, run_all = roles_mod.choose_roles(self.all, False, ask=lambda _: str(qa_number))
        self.assertEqual([r.KEY for r in picked], ["qa_security"])
        self.assertFalse(run_all)
        picked, run_all = roles_mod.choose_roles(self.all, False, ask=lambda _: str(len(self.all) + 1))
        self.assertTrue(run_all)
        self.assertNotIn("data", [r.KEY for r in picked])

    def test_invalid_input_asks_again(self):
        answers = iter(["x", "99", "1"])
        picked, _ = roles_mod.choose_roles(self.all, False, ask=lambda _: next(answers))
        self.assertEqual(picked[0].KEY, "product_owner")

    def test_env_override(self):
        with patch.dict("os.environ", {"WALLEE_ROLE": "qa_security"}):
            self.assertEqual(roles_mod.choose_roles(self.all, False)[0][0].KEY, "qa_security")
        with patch.dict("os.environ", {"WALLEE_ROLE": "all"}):
            self.assertTrue(roles_mod.choose_roles(self.all, False)[1])
        with patch.dict("os.environ", {"WALLEE_ROLE": "nope"}):
            with self.assertRaises(SystemExit):
                roles_mod.choose_roles(self.all, False)


class FolderRoutingTests(unittest.TestCase):
    def test_qa_files_land_in_test_and_security(self):
        folders = ("test", "security")
        nest = "import { Test } from '@nestjs/testing';"
        self.assertEqual(resolve_destination("test/a.e2e-spec.ts", nest, "x", folders), "test/a.e2e-spec.ts")
        self.assertEqual(resolve_destination("security/review.md", "", "x", folders), "security/review.md")
        self.assertEqual(resolve_destination("a.spec.ts", nest, "wallet", folders),
                         "test/backend-nestjs/src/wallet/a.spec.ts")
        self.assertEqual(resolve_destination("security-checks.ts", nest, "wallet", folders),
                         "security/backend-nestjs/src/wallet/security-checks.ts")
        self.assertIsNone(resolve_destination("README.md", "", "x", folders))
        self.assertIsNone(resolve_destination("../test/a.ts", nest, "x", folders))

    def test_ai_engineer_keeps_its_projects_and_without_folders_nothing_changes(self):
        folders = ("backend-nestjs", "frontend-react")
        self.assertEqual(resolve_destination("backend-nestjs/src/a.ts", "", "x", folders), "backend-nestjs/src/a.ts")
        self.assertEqual(resolve_destination("src/Login.tsx", "", "x", folders), "frontend-react/src/Login.tsx")
        self.assertEqual(resolve_destination("schema.sql", "", "x"), "db/schema.sql")
        self.assertIsNone(resolve_destination("README.md", "", "x"))


class OpenSpecTests(unittest.TestCase):
    def make_repo(self, root: Path) -> Path:
        change = root / "openspec" / "changes" / "w1-demo"
        (change / "specs" / "onboarding").mkdir(parents=True)
        (change / "tasks.md").write_text(TASKS, encoding="utf-8")
        (change / "proposal.md").write_text("# Por qué\nOnboarding guiado", encoding="utf-8")
        (change / "specs" / "onboarding" / "spec.md").write_text("### Requirement: pasos", encoding="utf-8")
        (root / "openspec" / "changes" / "archive" / "old").mkdir(parents=True)
        return change

    def test_list_counts_tasks_and_skips_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_repo(Path(tmp))
            changes = list_changes(Path(tmp))
            self.assertEqual([(c.name, c.done, c.total) for c in changes], [("w1-demo", 1, 3)])

    def test_select_change(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            self.make_repo(Path(tmp))
            self.assertEqual(select_change(Path(tmp), ask=lambda _: "1").name, "w1-demo")
            self.assertIsNone(select_change(Path(tmp), ask=lambda _: "2"))
            with patch.dict("os.environ", {"WALLEE_OPENSPEC_CHANGE": "w1-demo"}):
                self.assertEqual(select_change(Path(tmp)).name, "w1-demo")
            with patch.dict("os.environ", {"WALLEE_OPENSPEC_CHANGE": "none"}):
                self.assertIsNone(select_change(Path(tmp)))
            self.assertIsNone(select_change(Path(tmp) / "missing"))

    def test_context_gives_each_role_its_own_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            change = list_changes(Path(self.make_repo(Path(tmp)).parents[2]))[0]
            by_key = {role.KEY: role for role in roles_mod.discover_roles()}
            qa = change_context(change, by_key["qa_security"])
            self.assertIn("Onboarding guiado", qa)
            self.assertIn("Requirement: pasos", qa)
            self.assertIn("2.1 [rol: AI Engineer 1; prueba: QA & Security]", qa)
            self.assertNotIn("1.1 [rol: Architect]", qa)
            self.assertEqual([t[0] for t in role_tasks(change, ("Architect", "architect"))], ["1.1"])
            self.assertEqual(role_tasks(change, ("AI Engineer 2",)), [])
            self.assertEqual(change_context(None, by_key["architect"]), "")


class SingleRoleRunTests(unittest.TestCase):
    def test_qa_only_writes_test_and_security_and_no_scaffolding(self):
        qa = next(r for r in roles_mod.discover_roles() if r.KEY == "qa_security")
        prompts = []

        def run(agent, task):
            prompts.append(task.description)
            return ("Informe QA. NO EJECUTADO.\n```typescript\n// test/transfer.e2e-spec.ts\n"
                    "import { Test } from '@nestjs/testing';\n```\n"
                    "```markdown\n// security/security-review.md\nSEC-01 pendiente\n```")

        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            output, target = Path(tmp) / "output", Path(tmp) / "target"
            output.mkdir()
            target.mkdir()
            repo = Path(tmp) / "repo"
            (repo / "openspec" / "changes" / "w1-demo").mkdir(parents=True)
            (repo / "openspec" / "changes" / "w1-demo" / "tasks.md").write_text(TASKS, encoding="utf-8")
            change = Change("w1-demo", repo / "openspec" / "changes" / "w1-demo", 1, 3)
            stack.enter_context(patch.object(app, "OUTPUT_DIR", output))
            stack.enter_context(patch.object(app, "CHECKPOINT_FILE", output / ".checkpoint.json"))
            stack.enter_context(patch.object(app, "choose_roles", return_value=((qa,), False)))
            stack.enter_context(patch.object(app, "select_change", return_value=change))
            stack.enter_context(patch.object(app, "select_target_repo", return_value=target))
            stack.enter_context(patch.object(app, "proj_tools", return_value=[]))
            stack.enter_context(patch.object(app, "list_projects", return_value=[{"id": "P", "title": "T"}]))
            stack.enter_context(patch.object(app, "list_project_items", return_value=[{"title": "x"}]))
            stack.enter_context(patch.object(app, "get_feature_from_item", return_value=FEATURE))
            stack.enter_context(patch.object(app, "run_step", side_effect=run))
            approval_mock = stack.enter_context(patch.object(app, "hitl_approve", return_value=("s", "")))
            execution_prompt = stack.enter_context(
                patch.object(app, "confirm_qa_test_execution", return_value=False)
            )
            test_runner = stack.enter_context(patch.object(app, "run_qa_tests"))
            publish_mock = stack.enter_context(patch.object(app, "publish_output"))
            stack.enter_context(patch.object(app, "start_item"))
            finish_mock = stack.enter_context(patch.object(app, "finish_item"))
            stack.enter_context(patch.object(app, "STATE_FILE", output / ".state.json"))
            stack.enter_context(patch("builtins.input", return_value="1"))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            app.main()

            self.assertEqual(len(prompts), 1)
            approval_mock.assert_not_called()
            execution_prompt.assert_called_once()
            test_runner.assert_not_called()
            publish_mock.assert_called_once()
            finish_mock.assert_not_called()
            published_target, published_files, _, labels, change_name, approved = publish_mock.call_args.args
            self.assertEqual((published_target, labels, change_name, approved),
                             (target, ["QA & Security"], "w1-demo", ["QA & Security"]))
            self.assertIn(Path("test/transfer.e2e-spec.ts"), published_files)
            self.assertIn("Cambio OpenSpec activo: w1-demo", prompts[0])
            self.assertTrue((output / "test" / "transfer.e2e-spec.ts").is_file())
            self.assertTrue((output / "security" / "security-review.md").is_file())
            self.assertTrue((output / "specs" / "qa_security_report.md").is_file())
            self.assertTrue((target / "test" / "transfer.e2e-spec.ts").is_file())
            self.assertEqual(sorted(p.name for p in output.iterdir() if p.is_dir()), ["security", "specs", "test"])
            checkpoint = json.loads((output / ".checkpoint.json").read_text())
            self.assertEqual(checkpoint["openspec_change"], "w1-demo")
            self.assertEqual(list(checkpoint["steps"]), ["qa_security"])
            self.assertNotIn("workflow_mode", checkpoint)

    def test_qa_yes_runs_the_copied_tests_then_generates_final_report(self):
        qa = next(r for r in roles_mod.discover_roles() if r.KEY == "qa_security")
        generated = ("Informe preliminar. NO EJECUTADO.\n```typescript\n// test/transfer.e2e-spec.ts\n"
                     "describe('transfer', () => {});\n```")
        final_report = "Informe final: la suite FALLÓ con salida real."

        def run(agent, task):
            if "RESULTADO REAL DEL EJECUTOR" in task.description:
                self.assertIn("Estado: FALLÓ", task.description)
                return final_report
            return generated

        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            output, target = Path(tmp) / "output", Path(tmp) / "target"
            output.mkdir()
            target.mkdir()
            repo = Path(tmp) / "repo"
            change_dir = repo / "openspec" / "changes" / "w1-demo"
            change_dir.mkdir(parents=True)
            (change_dir / "tasks.md").write_text(TASKS, encoding="utf-8")
            change = Change("w1-demo", change_dir, 1, 3)

            stack.enter_context(patch.dict("os.environ", {"CREWAI_INCLUDE_DATA": "false"}))
            stack.enter_context(patch.object(app, "OUTPUT_DIR", output))
            stack.enter_context(patch.object(app, "CHECKPOINT_FILE", output / ".checkpoint.json"))
            stack.enter_context(patch.object(app, "choose_roles", return_value=((qa,), False)))
            stack.enter_context(patch.object(app, "select_change", return_value=change))
            stack.enter_context(patch.object(app, "select_target_repo", return_value=target))
            stack.enter_context(patch.object(app, "proj_tools", return_value=[]))
            stack.enter_context(patch.object(app, "list_projects", return_value=[{"id": "P", "title": "T"}]))
            stack.enter_context(patch.object(app, "list_project_items", return_value=[{"title": "x"}]))
            stack.enter_context(patch.object(app, "get_feature_from_item", return_value=FEATURE))
            stack.enter_context(patch.object(app, "run_step", side_effect=run))
            approval_mock = stack.enter_context(patch.object(app, "hitl_approve"))
            stack.enter_context(patch.object(app, "confirm_qa_test_execution", return_value=True))

            def execute_tests(test_target, command):
                self.assertTrue((test_target / "test" / "transfer.e2e-spec.ts").is_file())
                return TestRunResult(command, command, "failed", 1, 0.1, "1 failed", "")

            test_runner = stack.enter_context(patch.object(app, "run_qa_tests", side_effect=execute_tests))
            publish_mock = stack.enter_context(patch.object(app, "publish_output"))
            stack.enter_context(patch.object(app, "start_item"))
            finish_mock = stack.enter_context(patch.object(app, "finish_item"))
            stack.enter_context(patch.object(app, "STATE_FILE", output / ".state.json"))
            stack.enter_context(patch("builtins.input", return_value="1"))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

            app.main()

            approval_mock.assert_not_called()
            test_runner.assert_called_once_with(target, "python tools/wallee.py test")
            self.assertEqual((output / "specs" / "qa_security_report.md").read_text(encoding="utf-8"),
                             final_report)
            self.assertIn("**FALLÓ**", (target / "test" / "evidence.md").read_text(encoding="utf-8"))
            self.assertEqual((target / "specs" / "qa_security_report.md").read_text(encoding="utf-8"),
                             final_report)
            self.assertTrue(publish_mock.call_args.args[1])
            finish_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
