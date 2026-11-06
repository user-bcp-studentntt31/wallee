"""Tests for confirmation-gated QA suite execution."""

import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import main
from crew.qa_execution import append_execution_evidence, run_qa_tests


COMMAND = "python tools/wallee.py test"


def create_test_tool(root: Path) -> None:
    tool = root / "tools" / "wallee.py"
    tool.parent.mkdir(parents=True)
    tool.write_text("raise SystemExit(0)\n", encoding="utf-8")


class QaExecutionTests(unittest.TestCase):
    def test_runs_only_the_configured_test_command_without_a_shell(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir)
            create_test_tool(target)
            completed = subprocess.CompletedProcess([], 0, "4 passed\n", "")
            with patch("crew.qa_execution.subprocess.run", return_value=completed) as run:
                result = run_qa_tests(target, COMMAND)

        self.assertEqual(result.status, "passed")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(run.call_args.args[0][1:], ["tools/wallee.py", "test"])
        self.assertEqual(run.call_args.kwargs["cwd"], target.resolve())
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(run.call_args.kwargs["timeout"], 900)

    def test_nonzero_exit_is_reported_as_failed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir)
            create_test_tool(target)
            completed = subprocess.CompletedProcess([], 1, "1 failed\n", "assertion failed")
            with patch("crew.qa_execution.subprocess.run", return_value=completed):
                result = run_qa_tests(target, COMMAND)

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.returncode, 1)
        self.assertIn("assertion failed", result.report_text())

    def test_timeout_keeps_partial_output_and_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir)
            create_test_tool(target)
            timeout = subprocess.TimeoutExpired([], 900, output=b"started", stderr=b"still running")
            with patch("crew.qa_execution.subprocess.run", side_effect=timeout):
                result = run_qa_tests(target, COMMAND)

        self.assertEqual(result.status, "timeout")
        self.assertIsNone(result.returncode)
        self.assertIn("started", result.report_text())
        self.assertFalse(result.passed)

    def test_unapproved_command_and_missing_runner_are_not_executed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir)
            with patch("crew.qa_execution.subprocess.run") as run:
                invalid = run_qa_tests(target, "python -c print(1)")
            self.assertEqual(invalid.status, "blocked")
            self.assertIn("lista permitida", invalid.error)

            create_test_tool(target)
            missing = run_qa_tests(target / "missing", COMMAND)
            self.assertEqual(missing.status, "not_run")
            run.assert_not_called()

    def test_evidence_records_real_status_and_redacts_sensitive_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence_path = Path(temp_dir) / "test" / "evidence.md"
            with patch("crew.qa_execution.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess(
                    [], 1, "Authorization: Bearer fake-test-credential\ncelular 987654321", ""
                )
                target = Path(temp_dir)
                create_test_tool(target)
                result = run_qa_tests(target, COMMAND)
            append_execution_evidence(evidence_path, result)
            evidence = evidence_path.read_text(encoding="utf-8")

        self.assertIn("**FALLÓ** (`failed`)", evidence)
        self.assertIn("Código de salida: `1`", evidence)
        self.assertNotIn("fake-test-credential", evidence)
        self.assertNotIn("987654321", evidence)
        run.assert_called_once()

    def test_confirmation_requires_an_explicit_yes(self):
        self.assertTrue(main.confirm_qa_test_execution(lambda _: "s"))
        self.assertTrue(main.confirm_qa_test_execution(lambda _: "sí"))
        self.assertFalse(main.confirm_qa_test_execution(lambda _: "n"))

    def test_confirmation_reprompts_on_invalid_input(self):
        answers = iter(["quizás", "s"])
        with patch.object(main.ui, "error"):
            self.assertTrue(main.confirm_qa_test_execution(lambda _: next(answers)))

    def test_confirmation_skips_execution_when_input_is_unavailable(self):
        def no_input(_):
            raise EOFError

        with patch("builtins.print"):
            self.assertFalse(main.confirm_qa_test_execution(no_input))


if __name__ == "__main__":
    unittest.main()
