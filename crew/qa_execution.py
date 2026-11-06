"""Controlled execution of the QA test suite in the selected application repository."""

from dataclasses import dataclass
from datetime import datetime
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

EXPECTED_TEST_COMMAND = ["python", "tools/wallee.py", "test"]
TEST_TIMEOUT_SECONDS = 900
OUTPUT_LIMIT = 12000

_SECRET_PATTERN = re.compile(
    r"(?i)\b(authorization|access[_-]?token|api[_-]?key|password|secret|token)\b"
    r"(\s*[:=]\s*|\s+)(?:bearer\s+)?[^\s,;]+"
)
_PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?51\s?)?9\d{8}(?!\d)")


@dataclass(frozen=True)
class TestRunResult:
    configured_command: str
    executed_command: str
    status: str
    returncode: int | None
    elapsed_seconds: float
    stdout: str = ""
    stderr: str = ""
    error: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    @property
    def status_label(self) -> str:
        return {
            "passed": "PASÓ",
            "failed": "FALLÓ",
            "timeout": "TIEMPO AGOTADO",
            "not_run": "NO EJECUTADO",
            "blocked": "BLOQUEADO",
            "error": "ERROR",
        }.get(self.status, self.status.upper())

    def report_text(self, limit: int = OUTPUT_LIMIT) -> str:
        output = _redact(self.stdout + ("\n" + self.stderr if self.stderr else ""))
        error = _redact(self.error)
        if len(output) > limit:
            output = "[salida truncada; se muestra el final]\n" + output[-limit:]
        return (
            f"Estado: {self.status_label} ({self.status})\n"
            f"Comando configurado: {self.configured_command}\n"
            f"Comando ejecutado: {self.executed_command or 'ninguno'}\n"
            f"Código de salida: {self.returncode if self.returncode is not None else 'N/A'}\n"
            f"Duración: {self.elapsed_seconds:.1f} segundos\n"
            f"Error de ejecución: {error or 'ninguno'}\n"
            f"Salida real:\n{output or '(sin salida)'}"
        )

    def evidence_markdown(self) -> str:
        output = _redact(self.stdout + ("\n" + self.stderr if self.stderr else ""))
        error = _redact(self.error)
        if len(output) > OUTPUT_LIMIT:
            output = "[salida truncada; se muestra el final]\n" + output[-OUTPUT_LIMIT:]
        return (
            "\n\n## Ejecución de pruebas solicitada\n\n"
            f"- Fecha: {datetime.now().astimezone().isoformat(timespec='seconds')}\n"
            f"- Comando configurado: `{self.configured_command}`\n"
            f"- Comando ejecutado: `{self.executed_command or 'ninguno'}`\n"
            f"- Estado: **{self.status_label}** (`{self.status}`)\n"
            f"- Código de salida: `{self.returncode if self.returncode is not None else 'N/A'}`\n"
            f"- Duración: {self.elapsed_seconds:.1f} segundos\n"
            f"- Detalle: {error or 'ninguno'}\n\n"
            "<details><summary>Salida real (máximo 12 000 caracteres)</summary>\n\n"
            "```text\n" + (output or "(sin salida)") + "\n```\n\n</details>\n"
        )


def _as_text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _redact(text: str) -> str:
    text = _SECRET_PATTERN.sub(r"\1\2[REDACTADO]", text)
    return _PHONE_PATTERN.sub("[CELULAR REDACTADO]", text)


def run_qa_tests(target_repo: Path, configured_command: str) -> TestRunResult:
    """Run only the app's documented test command, without invoking a shell."""
    try:
        command_parts = shlex.split(configured_command)
    except ValueError as exc:
        return TestRunResult(configured_command, "", "blocked", None, 0, error=str(exc))

    if command_parts != EXPECTED_TEST_COMMAND:
        return TestRunResult(
            configured_command,
            "",
            "blocked",
            None,
            0,
            error="El comando no coincide con la lista permitida para QA.",
        )

    target_repo = Path(target_repo).resolve()
    test_tool = target_repo / "tools" / "wallee.py"
    if not test_tool.is_file():
        return TestRunResult(
            configured_command,
            "",
            "not_run",
            None,
            0,
            error=f"No se encontró el ejecutor de pruebas: {test_tool}",
        )

    command = [sys.executable, "tools/wallee.py", "test"]
    executed_command = subprocess.list2cmdline(command)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=target_repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TEST_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        return TestRunResult(
            configured_command,
            executed_command,
            "timeout",
            None,
            time.monotonic() - started,
            _as_text(exc.stdout),
            _as_text(exc.stderr),
            f"Se agotó el límite de {TEST_TIMEOUT_SECONDS} segundos.",
        )
    except OSError as exc:
        return TestRunResult(
            configured_command,
            executed_command,
            "error",
            None,
            time.monotonic() - started,
            error=f"No se pudo iniciar el ejecutor de pruebas: {exc}",
        )

    return TestRunResult(
        configured_command,
        executed_command,
        "passed" if completed.returncode == 0 else "failed",
        completed.returncode,
        time.monotonic() - started,
        completed.stdout,
        completed.stderr,
    )


def append_execution_evidence(evidence_path: Path, result: TestRunResult) -> None:
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    with evidence_path.open("a", encoding="utf-8") as evidence:
        evidence.write(result.evidence_markdown())
