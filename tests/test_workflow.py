"""Exercise the five-role pipeline without calling Copilot or GitHub."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import main as app
from crew.workflow import ROLES, STEPS, DATA_ROLES, build_tasks, workflow_steps, include_data


FEATURE = {
    "title": "Transfer funds",
    "description": "Transfer funds between accounts",
    "acceptance_criteria": ["Reject a transfer with insufficient funds"],
}


class WorkflowTests(unittest.TestCase):
    def test_each_role_builds_its_own_task(self):
        tasks = build_tasks(FEATURE)
        self.assertEqual(
            list(tasks),
            ["product_owner", "architect", "ai_engineer_1", "ai_engineer_2", "qa_security", "presenter"],
        )
        for role in ROLES:
            self.assertIs(tasks[role.KEY].agent, role.agent)
            self.assertIn("Transfer funds", tasks[role.KEY].description)
            self.assertIn(FEATURE["description"], tasks[role.KEY].description)
            for criterion in FEATURE["acceptance_criteria"]:
                self.assertIn(criterion, tasks[role.KEY].description)
            self.assertTrue(Path(role.__file__).is_file())

    def test_pipeline_saves_all_roles_and_resumes(self):
        self.check_pipeline(False)

    def test_invalid_activation_is_rejected(self):
        with patch.dict("os.environ", {"CREWAI_INCLUDE_DATA": "maybe"}):
            with self.assertRaises(ValueError):
                include_data()

    def check_pipeline(self, with_data, interrupt_data=False):
        roles = DATA_ROLES if with_data else ROLES
        steps = workflow_steps(with_data)
        calls = []

        def run(agent, task):
            key = next(role.KEY for role in roles if role.agent is agent)
            calls.append((key, task.description))
            if key == "data":
                return "Approved data contract.\n```sql\n-- migration.sql\nCREATE TABLE accounts (id INTEGER PRIMARY KEY);\n```"
            if key == "ai_engineer_1":
                return (
                    "```typescript\n// backend-nestjs/src/balance/balance.service.ts\nexport class BalanceService {}\n```\n"
                    "```typescript\nimport { Module } from '@nestjs/common';\n@Module({})\nexport class TransferModule {}\n```"
                )
            if key == "ai_engineer_2":
                return "```typescript\n// frontend-react/src/balance/BalanceView.tsx\nexport default function BalanceView() { return <div />; }\n```"
            if key == "qa_security":
                return "Coverage report. NO EJECUTADO.\n```typescript\n// balance.service.spec.ts\nimport { Test } from '@nestjs/testing';\ndescribe('BalanceService', () => { it('works', () => {}); });\n```"
            return f"Approved output from {key}. No model was called."

        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict("os.environ", {"CREWAI_INCLUDE_DATA": str(with_data).lower()}))
            output = Path(tmp)
            stack.enter_context(patch.object(app, "OUTPUT_DIR", output))
            stack.enter_context(
                patch.object(app, "CHECKPOINT_FILE", output / ".checkpoint.json")
            )
            target = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            stack.enter_context(patch.object(app, "select_target_repo", return_value=target))
            stack.enter_context(patch.object(app, "proj_tools", return_value=[]))
            stack.enter_context(
                patch.object(
                    app,
                    "list_projects",
                    return_value=[{"id": "P1", "title": "Team backlog"}],
                )
            )
            stack.enter_context(
                patch.object(
                    app,
                    "list_project_items",
                    return_value=[{"title": FEATURE["title"]}],
                )
            )
            stack.enter_context(
                patch.object(app, "get_feature_from_item", return_value=FEATURE)
            )
            stack.enter_context(patch.object(app, "run_step", side_effect=run))
            data_decisions = iter([("r", "Add a uniqueness constraint"), ("n", "")])
            def approve(label, result):
                if interrupt_data and label == "Datos y persistencia":
                    decision = next(data_decisions, ("s", ""))
                    if decision[0] != "s":
                        self.assertFalse((output / "specs/data_spec.md").exists())
                        self.assertFalse((output / "db/migration.sql").exists())
                    return decision
                return "s", ""
            stack.enter_context(patch.object(app, "hitl_approve", side_effect=approve))
            stack.enter_context(patch.object(app, "choose_roles", return_value=(roles if with_data else ROLES, True)))
            stack.enter_context(patch.object(app, "select_change", return_value=None))
            stack.enter_context(patch.object(app, "confirm_qa_test_execution", return_value=False))
            stack.enter_context(patch.object(app, "run_qa_tests"))
            stack.enter_context(patch.object(app, "publish_output"))
            stack.enter_context(patch.object(app, "start_item"))
            stack.enter_context(patch.object(app, "finish_item"))
            stack.enter_context(patch.object(app, "STATE_FILE", output / ".state.json"))
            stack.enter_context(patch.object(app, "confirm_saved", return_value="use"))
            stack.enter_context(patch("builtins.input", return_value="1"))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            if interrupt_data:
                with self.assertRaises(SystemExit):
                    app.main()
                rejected = json.loads((output / ".checkpoint.json").read_text())
                self.assertEqual(rejected["steps"]["data"]["status"], "rejected")
                self.assertNotIn("ai_engineer_1", rejected["steps"])
                self.assertIn("Add a uniqueness constraint", calls[3][1])
            app.main()

            expected = [role.KEY for role in roles]
            if interrupt_data:
                expected[2:3] = ["data", "data", "data"]
            self.assertEqual([key for key, _ in calls], expected)
            for key, _, _, spec, _ in steps:
                self.assertTrue((output / "specs" / spec).is_file(), key)
            for relative in (
                "backend-nestjs/src/balance/balance.service.ts",
                "test/backend-nestjs/src/transfer-funds/balance.service.spec.ts",
                "backend-nestjs/src/transfer-funds/transfer.module.ts",
                "frontend-react/src/balance/BalanceView.tsx",
                "backend-nestjs/package.json",
                "frontend-react/package.json",
                "frontend-react/src/main.tsx",
            ):
                self.assertTrue((output / relative).is_file(), relative)
                self.assertTrue((target / relative).is_file(), relative)
            self.assertFalse((target / ".checkpoint.json").exists())
            presenter_prompt = calls[-1][1]
            self.assertIn("Coverage report", presenter_prompt)
            self.assertIn("Approved output from architect", presenter_prompt)
            checkpoint = json.loads((output / ".checkpoint.json").read_text())
            self.assertEqual(len(checkpoint["steps"]), len(roles))
            if with_data:
                self.assertEqual(
                    list(dict.fromkeys(key for key, _ in calls))[:4],
                    ["product_owner", "architect", "data", "ai_engineer_1"],
                )
                self.assertIn("Approved data contract", next(prompt for key, prompt in calls if key == "ai_engineer_1"))
                self.assertIn("Approved data contract", presenter_prompt)
                self.assertTrue((output / "db/migration.sql").is_file())
            self.assertTrue(
                all(
                    step["status"] == "approved"
                    for step in checkpoint["steps"].values()
                )
            )

            app.main()
            self.assertEqual(
                len(calls), len(expected), "Approved steps must not call the model again"
            )
            before = (output / ".checkpoint.json").read_text()
            with patch.dict("os.environ", {"CREWAI_INCLUDE_DATA": str(not with_data).lower()}):
                with self.assertRaises(SystemExit) as stopped:
                    app.main()
                self.assertEqual(stopped.exception.code, 1)
            self.assertEqual((output / ".checkpoint.json").read_text(), before)


if __name__ == "__main__":
    unittest.main()
