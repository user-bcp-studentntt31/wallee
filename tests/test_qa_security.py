"""QA & Security agent: contract, searched (never assumed) context and ISTQB test-design rules."""

from pathlib import Path
import tempfile
import unittest

from agents import qa_security
from crew.workflow import workflow_steps

FEATURE = {
    "title": "Payment idempotency protection",
    "description": "Repeated requests with the same key return the original result",
    "acceptance_criteria": ["W4-AC2 same key returns the original result"],
}


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class QaSecurityContractTests(unittest.TestCase):
    def test_workflow_contract_is_unchanged(self):
        step = next(s for s in workflow_steps() if s[0] == "qa_security")
        self.assertEqual(step[1:], ("QA & Security", qa_security.agent, "qa_security_report.md", True))

    def test_instructions_keep_honesty_hitl_and_path_rules(self):
        text = qa_security.INSTRUCTIONS
        for expected in ("NO EJECUTADO", "SPEC PRINCIPAL", "DISCOVERY", "DECISIÓN REQUERIDA", "PROPUESTO",
                         "Wx-ACn-Tm", "MAPA DEL PROYECTO", "NO preguntes por nada que el", "es ilegible o"):
            self.assertIn(expected, text)

    def test_instructions_apply_istqb_black_box_techniques(self):
        text = qa_security.INSTRUCTIONS
        for expected in ("ISTQB", "EP (partición de equivalencia)", "BVA (valores límite", "DT (tabla de decisión)",
                         "ST (transición de estados)", "EG (adivinar errores", "NO exhaustivo",
                         "499.99 / 500.00 / 500.01", "trazabilidad bidireccional",
                         "Alcance del sprint: descúbrelo", "FUERA DEL ALCANCE DEL SPRINT"):
            self.assertIn(expected, text)

    def test_instructions_are_autonomous_and_say_whom_to_ask(self):
        text = qa_security.INSTRUCTIONS
        for expected in ("Autonomía primero", "Pregunta SOLO ante una duda real", "NO te detengas",
                         "DECISIONES REQUERIDAS", "A quién preguntar", "Product Owner humano", "Architect"):
            self.assertIn(expected, text)

    def test_instructions_read_openspec_as_the_primary_spec(self):
        text = qa_security.INSTRUCTIONS
        for expected in ("openspec/changes/<cambio>/", "Scenario", "tasks.md", "Fuera de alcance", "archive"):
            self.assertIn(expected, text)

    def test_instructions_use_po_approvals_and_only_own_tasks(self):
        text = qa_security.INSTRUCTIONS
        for expected in ("DEC-04 y DEC-05", "PA-01 a PA-10 resueltas", "[rol: ...; prueba: QA & Security]",
                         "no las cierres", "pendiente» o «bloqueada por"):
            self.assertIn(expected, text)
        self.assertNotIn("están PROPUESTO hasta que el humano los", text)

    def test_task_embeds_spec_discovery_and_paths(self):
        task = qa_security.build_task(FEATURE)
        self.assertIs(task.agent, qa_security.agent)
        for section in ("## SPEC PRINCIPAL", "## DISCOVERY (secundaria", "## MAPA DEL PROYECTO"):
            self.assertIn(section, task.description)
        self.assertIn("challenge/docs/discovery/06-handoff-por-rol/qa-security.md", task.description)
        self.assertIn("NO EJECUTADO", task.expected_output)
        self.assertIn("BVA", task.expected_output)

    def test_execution_report_task_uses_results_without_regenerating_tests(self):
        task = qa_security.build_execution_report_task(FEATURE, "QA draft", "Estado: failed")
        self.assertIs(task.agent, qa_security.agent)
        self.assertIn("QA draft", task.description)
        self.assertIn("Estado: failed", task.description)
        self.assertIn("No vuelvas a generar ni modificar archivos de prueba", task.description)
        self.assertIn("sin regenerar tests", task.expected_output)


class QaSecurityContextTests(unittest.TestCase):
    def test_spec_is_found_wherever_it_is_and_cited_with_its_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "docs/otra/product_owner_report.md", "---\nid: x\n---\nCA-1 spec del PO")
            write(root, "output/specs/qa_security_report.md", "INFORME PROPIO")
            context = qa_security.build_context(FEATURE, root)
        self.assertIn("### docs/otra/product_owner_report.md", context)
        self.assertIn("CA-1 spec del PO", context)
        self.assertNotIn("id: x", context)
        self.assertNotIn("INFORME PROPIO", context)
        self.assertNotIn("SPEC PRINCIPAL NO ENCONTRADO", context)

    def test_missing_spec_is_not_assumed_and_asks(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = qa_security.build_context(FEATURE, Path(tmp))
        self.assertIn("SPEC PRINCIPAL NO ENCONTRADO", context)
        self.assertIn("DISCOVERY NO ENCONTRADO", context)
        self.assertIn("DECISIÓN REQUERIDA", context)
        self.assertIn("MAPA DEL PROYECTO NO ENCONTRADO", context)

    def test_duplicate_spec_locations_are_ambiguous(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "output/specs/product_owner_report.md", "version A")
            write(root, "copia/product_owner_report.md", "version B")
            context = qa_security.build_context(FEATURE, root)
        self.assertIn("product_owner_report.md AMBIGUO", context)
        self.assertNotIn("version A", context)
        self.assertNotIn("version B", context)

    def test_discovery_is_searched_and_ignored_dirs_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = "algun/lugar/discovery"
            write(root, base + "/06-handoff-por-rol/qa-security.md", "HANDOFF QA")
            write(root, base + "/02-producto/historias/W4-idempotencia.md", "HISTORIA W4 T2")
            write(root, "node_modules/otro/discovery/06-handoff-por-rol/qa-security.md", "ignorado")
            context = qa_security.build_context(FEATURE, root)
        self.assertIn("### algun/lugar/discovery/06-handoff-por-rol/qa-security.md", context)
        self.assertIn("HISTORIA W4 T2", context)
        self.assertIn("historias del alcance: W4", context)
        self.assertNotIn("DISCOVERY AMBIGUO", context)

    def test_two_discovery_folders_are_ambiguous(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "a/06-handoff-por-rol/qa-security.md", "A")
            write(root, "b/06-handoff-por-rol/qa-security.md", "B")
            context = qa_security.build_context(FEATURE, root)
        self.assertIn("DISCOVERY AMBIGUO", context)

    def test_real_repository_context(self):
        context = qa_security.build_context(FEATURE)
        self.assertIn("challenge/docs/discovery/06-handoff-por-rol/qa-security.md", context)
        self.assertIn("W4-AC2-T2", context)
        self.assertIn("RN-01", context)

    def test_long_documents_are_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "output/specs/architect_report.md", "x" * (qa_security.SPEC_CHARS * 2))
            context = qa_security.build_context(FEATURE, root)
        self.assertIn("(truncado)", context)
        self.assertLess(len(context), qa_security.SPEC_CHARS + 2500)

    def test_story_scope_comes_from_the_spec_first(self):
        known = {"W1": Path("W1-x.md"), "W2": Path("W2-x.md"), "W4": Path("W4-x.md")}
        stories, origin = qa_security.scope(FEATURE, "Criterios W1-AC1 y W2-AC2 del sprint", known)
        self.assertEqual((stories, origin), (["W1", "W2"], "spec"))

    def test_scope_uses_any_hu_prefix_found_in_the_spec(self):
        stories, origin = qa_security.scope({"title": "x"}, "H7-AC1 y H8-AC3", {})
        self.assertEqual((stories, origin), (["H7", "H8"], "spec"))

    def test_scope_falls_back_to_feature_then_keywords_then_none(self):
        known = {"W1": Path("a"), "W4": Path("b")}
        self.assertEqual(qa_security.scope({"title": "W1 alta"}, "", known), (["W1"], "feature"))
        stories, origin = qa_security.scope({"title": "Payment idempotency protection"}, "", known)
        self.assertEqual(stories, ["W4"])
        self.assertIn("heurística", origin)
        self.assertEqual(qa_security.scope({"title": "Transfer funds"}, "", known), ([], "ninguna"))

    def test_spec_with_one_story_leaves_the_rest_out_of_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "output/specs/product_owner_report.md", "Alcance del sprint: W2-AC1, W2-AC2")
            base = "challenge/docs/discovery"
            write(root, base + "/06-handoff-por-rol/qa-security.md", "HANDOFF")
            write(root, base + "/02-producto/historias/W2-pago-contacto.md", "TEXTO HISTORIA W2")
            write(root, base + "/02-producto/historias/W4-idempotencia.md", "TEXTO HISTORIA W4")
            context = qa_security.build_context({"title": "Payment idempotency protection"}, root)
        self.assertIn("HU del sprint a probar: W2 (origen: spec)", context)
        self.assertIn("Fuera del alcance del sprint (no probar): W4", context)
        self.assertIn("TEXTO HISTORIA W2", context)
        self.assertNotIn("TEXTO HISTORIA W4", context)

    def test_undetermined_scope_asks_the_po(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "challenge/docs/discovery/06-handoff-por-rol/qa-security.md", "HANDOFF")
            write(root, "challenge/docs/discovery/02-producto/historias/W1-onboarding.md", "H1")
            context = qa_security.build_context({"title": "Transfer funds"}, root)
        self.assertIn("NO DETERMINADAS", context)
        self.assertIn("DECISIÓN REQUERIDA", context)

    def test_mention_without_ac_does_not_pull_a_story_into_the_sprint(self):
        known = {"W1": Path("a"), "W2": Path("b")}
        stories, _ = qa_security.scope({"title": "x"}, "W2 depende de W1. Cubre W1-AC1 y W1-AC2.", known)
        self.assertEqual(stories, ["W1"])


def openspec_fixture(root: Path) -> None:
    base = "openspec"
    write(root, base + "/config.yaml", "schema: spec-driven")
    write(root, base + "/changes/w2-pago/proposal.md", "Cubre W2-AC1 y W2-AC2. W4 depende de W2.\n\nFuera de alcance: KPI")
    write(root, base + "/changes/w2-pago/specs/pago/spec.md",
          "### Requirement: Monto maximo\nX\n\n#### Scenario: Monto 500\n- **WHEN** a\n- **THEN** b\n"
          "#### Scenario: Monto 501\n- **WHEN** c\n- **THEN** d\n")
    write(root, base + "/changes/w2-pago/tasks.md", "- [x] 1.1 hecha\n- [ ] 1.2 pendiente (W2-AC1-T1)\n- [ ] 1.3 otra")
    write(root, base + "/changes/w2-pago/design.md", "DISENO W2")
    write(root, base + "/changes/archive/w1-viejo/proposal.md", "CAMBIO ARCHIVADO W1-AC1")
    write(root, base + "/specs/onboarding/spec.md", "SPEC VIGENTE ONBOARDING")
    discovery = "challenge/docs/discovery"
    write(root, discovery + "/06-handoff-por-rol/qa-security.md", "HANDOFF")
    for story in ("W1-onboarding", "W2-pago", "W4-idempotencia"):
        write(root, discovery + "/02-producto/historias/" + story + ".md", "HISTORIA " + story[:2])


class QaSecurityOpenSpecTests(unittest.TestCase):
    def test_active_changes_are_the_primary_spec_and_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            openspec_fixture(root)
            context = qa_security.build_context(FEATURE, root)
        self.assertIn("### openspec/changes/w2-pago/proposal.md", context)
        self.assertIn("### openspec/changes/w2-pago/specs/pago/spec.md", context)
        self.assertIn("### openspec/changes/w2-pago/tasks.md", context)
        self.assertIn("DISENO W2", context)
        self.assertIn("HU del sprint a probar: W2 (origen: OpenSpec: cambios activos)", context)
        self.assertIn("1 requirements, 2 scenarios, tareas 1 hechas / 2 pendientes", context)
        self.assertIn("HISTORIA W2", context)
        self.assertNotIn("HISTORIA W4", context)
        self.assertIn("Fuera del alcance del sprint (no probar): W1, W4", context)

    def test_archived_changes_are_only_listed_for_minimal_regression(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            openspec_fixture(root)
            context = qa_security.build_context(FEATURE, root)
        self.assertIn("Cambios archivados (sprints cerrados, solo regresión mínima): w1-viejo", context)
        self.assertNotIn("CAMBIO ARCHIVADO", context)
        self.assertIn("SPEC VIGENTE ONBOARDING", context)
        self.assertIn("spec vigente, solo regresión mínima", context)

    def test_new_active_change_extends_the_scope_sprint_by_sprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            openspec_fixture(root)
            write(root, "openspec/changes/w4-idem/proposal.md", "Cubre W4-AC1 a W4-AC3")
            context = qa_security.build_context(FEATURE, root)
        self.assertIn("HU del sprint a probar: W2, W4 (origen: OpenSpec", context)
        self.assertIn("Cambio activo `w4-idem`", context)

    def test_openspec_without_active_changes_asks_the_po(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "openspec/config.yaml", "schema: spec-driven")
            write(root, "openspec/changes/archive/w1/proposal.md", "viejo")
            context = qa_security.build_context({"title": "Transfer funds"}, root)
        self.assertIn("OpenSpec no tiene cambios activos", context)
        self.assertIn("SPEC PRINCIPAL NO ENCONTRADO", context)

    def test_two_openspec_folders_are_ambiguous(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "a/openspec/config.yaml", "x")
            write(root, "b/openspec/config.yaml", "y")
            context = qa_security.build_context(FEATURE, root)
        self.assertIn("OPENSPEC AMBIGUO", context)

    def test_unrelated_openspec_named_folder_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "docs/openspec/notas.md", "no es un workspace de OpenSpec")
            context = qa_security.build_context(FEATURE, root)
        self.assertNotIn("OPENSPEC AMBIGUO", context)
        self.assertNotIn("notas.md", context)

    def test_real_repository_reads_the_openspec_change(self):
        context = qa_security.build_context(FEATURE)
        self.assertIn("### openspec/changes/w1-onboarding-guiado/tasks.md", context)
        self.assertIn("origen: OpenSpec: cambios activos", context)


class QaSecurityProjectMapTests(unittest.TestCase):
    MAP = {
        "app": {"repo_url": "https://github.com/org/mi-app", "local_path": "../mi-app", "backend_dir": "backend",
                "frontend_dir": "frontend", "contract_file": "AGENTS.md"},
        "run_targets": {"preference": ["url", "mcp"], "api_base_url": "http://localhost:3000", "mcp": None},
        "qa": {"branch": "qa-security",
               "tests": {"backend_e2e": "backend/test/<f>.e2e-spec.ts", "backend_unit": "backend/src/<f>/<n>.spec.ts",
                         "frontend": "frontend/src/<f>/<N>.test.tsx"},
               "run_tests_command": "python tools/wallee.py test", "test_plan": "backend/test/p.md",
               "evidence": "backend/test/evidence.md", "security_review": "backend/security/s.md",
               "decisions": "backend/test/d.md"},
    }

    def build(self, tmp: str, with_app: bool = True, app_dir: str = "mi-app", branch: str = "qa-security"):
        import json

        root = Path(tmp) / "wallee"
        write(root, "agents/qa-project-map.json", json.dumps(self.MAP))
        if with_app:
            app = Path(tmp) / app_dir
            write(app, "backend/src/health/health.controller.ts", "x")
            write(app, "backend/src/wallet/wallet.module.ts", "x")
            write(app, "backend/test/app.e2e-spec.ts", "x")
            write(app, "frontend/src/api/health.ts", "x")
            write(app, "AGENTS.md", "CONTRATO: cobertura 85%")
            write(app, ".git/HEAD", "ref: refs/heads/" + branch + "\n")
        return root

    def test_map_gives_app_paths_url_and_where_tests_go(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = qa_security.build_context(FEATURE, self.build(tmp))
        self.assertIn("## MAPA DEL PROYECTO", context)
        self.assertIn("App detectada en disco", context)
        self.assertIn("Rama actual de la app: qa-security", context)
        self.assertIn("health, wallet", context)
        self.assertIn("app.e2e-spec.ts", context)
        self.assertIn("CONTRATO: cobertura 85%", context)
        self.assertIn("URL base http://localhost:3000", context)
        self.assertIn("El equipo de la app la levanta; tú no", context)
        self.assertIn("va a la rama «qa-security» del repositorio de la app (https://github.com/org/mi-app)", context)
        self.assertIn("NO a wallee", context)
        self.assertIn("backend/test/<f>.e2e-spec.ts", context)
        self.assertIn("evidencia backend/test/evidence.md", context)
        self.assertIn("Comando de pruebas: python tools/wallee.py test", context)

    def test_app_on_another_branch_is_reported_to_the_person_not_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = qa_security.build_context(FEATURE, self.build(tmp, branch="main"))
        self.assertIn("Rama actual de la app: main", context)
        self.assertIn("las pruebas van a la rama «qa-security»", context)
        self.assertIn("sin preguntar", context)

    def test_missing_app_is_declared_without_asking_for_the_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = qa_security.build_context(FEATURE, self.build(tmp, with_app=False))
        self.assertIn("APP NO ENCONTRADA en disco", context)
        self.assertIn("https://github.com/org/mi-app", context)
        self.assertIn("No preguntes por la ruta", context)

    def test_app_is_found_as_a_sibling_named_like_the_repository(self):
        import json

        with tempfile.TemporaryDirectory() as tmp:
            root = self.build(tmp, app_dir="mi-app")
            moved = {**self.MAP, "app": {**self.MAP["app"], "local_path": "../otro-lugar"}}
            write(root, "agents/qa-project-map.json", json.dumps(moved))
            context = qa_security.build_context(FEATURE, root)
        self.assertIn("App detectada en disco", context)

    def test_missing_map_asks_instead_of_assuming(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = qa_security.build_context(FEATURE, Path(tmp))
        self.assertIn("MAPA DEL PROYECTO NO ENCONTRADO", context)
        self.assertIn("DECISIÓN REQUERIDA", context)

    def test_two_or_broken_maps_are_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "a/qa-project-map.json", "{}")
            write(root, "b/qa-project-map.json", "{}")
            self.assertIn("MAPA DEL PROYECTO AMBIGUO", qa_security.build_context(FEATURE, root))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "agents/qa-project-map.json", "{no es json")
            self.assertIn("MAPA DEL PROYECTO ILEGIBLE", qa_security.build_context(FEATURE, root))

    def test_real_map_is_aligned_with_the_repository_and_the_app_repo(self):
        import json

        repo = Path(qa_security.ROOT)
        maps = qa_security.scan(repo)["map"]
        self.assertEqual([p.relative_to(repo).as_posix() for p in maps], ["agents/qa-project-map.json"])
        data = json.loads(maps[0].read_text(encoding="utf-8"))
        self.assertEqual(data["qa"]["branch"], "qa-security")
        self.assertEqual(data["app"]["repo_url"], "https://github.com/user-bcp-student09/wallee-app")
        self.assertTrue((repo / data["real_vs_mock"]["definition"]).is_file())
        self.assertTrue((repo / data["real_vs_mock"]["mock_convention"]).is_file())
        for path in data["qa"]["tests"].values():
            self.assertTrue(path.startswith((data["app"]["backend_dir"] + "/", data["app"]["frontend_dir"] + "/")))
        target = data["qa"]["output_target_dir"]
        self.assertEqual(target, data["app"]["backend_dir"])
        for key in ("test_plan", "evidence", "security_review", "decisions"):
            self.assertTrue(data["qa"][key].startswith(target + "/"))
            self.assertIn(data["qa"][key][len(target) + 1:], qa_security.INSTRUCTIONS)
        for folder in ("test", "security"):
            self.assertTrue(any(data["qa"][key].startswith(target + "/" + folder + "/")
                                for key in ("test_plan", "evidence", "security_review", "decisions")))
        for path in data["qa"]["tests"].values():
            self.assertIn(path.split("<")[0], qa_security.INSTRUCTIONS)

    def test_declared_output_folders_follow_the_team_convention(self):
        from crew.roles import output_folders, report_folder

        self.assertEqual(output_folders(qa_security), ("test", "security"))
        self.assertEqual(report_folder(qa_security), "specs")

    def test_qa_suites_live_in_the_app_repo_not_in_wallee(self):
        self.assertFalse((Path(qa_security.ROOT) / "tests" / "qa").exists())
        self.assertIn("nunca propongas archivos de prueba dentro de wallee", qa_security.INSTRUCTIONS)

    def test_real_context_includes_map_and_real_vs_mock_definition(self):
        context = qa_security.build_context(FEATURE)
        self.assertIn("Fuente: agents/qa-project-map.json", context)
        self.assertIn("va a la rama «qa-security»", context)
        self.assertIn("challenge/docs/discovery/03-alcance-hackathon/04-real-vs-mock.md", context)


if __name__ == "__main__":
    unittest.main()
