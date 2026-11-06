"""S1 CrewAI — Generador BCP con HITL y checkpoint por paso.

Flujo:
  0. Si hay una selección guardada (.wallee-state.json: rol, OpenSpec, repo destino, proyecto) la muestra y solo pide
     reconfirmarla; si se cambia, vuelve a preguntar. El feature se elige siempre. crewai se carga en segundo plano.
     Pregunta qué rol ejecutar (los de agents/ o «Todos») y qué cambio OpenSpec de openspec/changes/ usar.
     Las carpetas de /output de cada rol salen de las líneas «Carpetas de salida en /output:» de su prompt.
  1. Conecta al MCP Projects → lista proyectos → elige proyecto → lista backlog → elige feature
  2. Por cada agente elegido, corre un mini-Crew de un solo paso.
  3. QA genera y guarda sus pruebas automáticamente; después pregunta antes de ejecutarlas.
     Los demás roles conservan la aprobación [s / n / r].
  4. Al aprobar, guarda artefactos y registra el checkpoint en output/.checkpoint.json.
  5. Al volver a ejecutar, carga el checkpoint y omite los pasos ya aprobados.
  6. Al terminar, copia output/ al repo destino, hace commit y push en una rama y, tras una
     preaprobación humana, abre el Pull Request con `gh`. WALLEE_PUBLISH=false omite la publicación.

Estructura generada en output/ (según el rol; cada prompt declara sus carpetas):
  .checkpoint.json        ← estado de cada paso
  specs/
    product_owner_report.md
    architect_report.md
    ai_engineer_1_report.md
    ai_engineer_2_report.md
    qa_security_report.md
    presenter_report.md
  backend-nestjs/src/<feature>/...
  frontend-react/src/<feature>/...
  test/  security/        ← QA & Security
  db/schema.sql  seed.sql
"""
import sys
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import os
import logging

# ---------------------------------------------------------------------------
# Telemetría configurable por entorno
# ---------------------------------------------------------------------------
os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ["OTEL_LOG_LEVEL"] = "error"                # No ensucia la terminal con logs informativos
os.environ["OTEL_BSP_EXPORT_TIMEOUT"] = "2000"        # Reduce timeout de exportación a 2s (evita cuelgues al salir)

# Silenciar loggers que puedan imprimir advertencias de red/telemetría en stderr
for _log in ("opentelemetry", "crewai.telemetry", "urllib3", "requests"):
    logging.getLogger(_log).setLevel(logging.ERROR)


import re
import json
import time
import threading
import textwrap
from pathlib import Path

# crewai y los agentes (lo más lento del arranque) se cargan en segundo plano: ver crew.roles.prewarm()
from crew.workflow import build_tasks, include_data, workflow_steps
from crew.roles import choose_roles, discover_roles, output_folders, prewarm, report_folder, saved_roles
from crew.openspec_changes import change_context, find_change, select_change
from crew.session_state import USE, STATE_NAME, confirm_saved, find_project, has_selection, load_state, save_state
from crew.lifecycle import describe_item, finish_item, start_item
from crew.scaffold import BACKEND_DIR, FRONTEND_DIR, ensure_scaffolding
from crew.target_repo import copy_output, select_target_repo
from crew.publish import publish_output
from crew.prefetch import Prefetch
from crew import ui
from crew.code_paths import FILENAME_PATTERN, feature_slug, guess_filename, resolve_destination
from crew.qa_execution import append_execution_evidence, run_qa_tests

# ── MCP clients ────────────────────────────────────────────────────
from mcp_projects import (
    list_tools as proj_tools,
    list_projects,
    list_project_items,
    get_feature_from_item,
)
from hex_archetype import HEX_PROMPT_PREFIX

# ── Flag de verbosidad MCP ─────────────────────────────────────────
# True  → muestra el detalle de cada herramienta al arrancar
# False → solo confirma "Herramientas cargadas correctamente."
VERBOSE_MCP_TOOLS = False

# ── Rutas base ─────────────────────────────────────────────────────
OUTPUT_DIR      = Path(__file__).parent / "output"
CHECKPOINT_FILE = OUTPUT_DIR / ".checkpoint.json"
STATE_FILE      = Path(__file__).parent / STATE_NAME   # última selección (rol, OpenSpec, destino, proyecto, feature)
OUTPUT_DIR.mkdir(exist_ok=True)   # lo necesita save_checkpoint()
# ensure_scaffolding() NO se llama aquí: escribiría en output/ con sólo importar
# el módulo, antes del MCP, del feature elegido y del primer agente.

# Carpeta del feature para archivos sin ruta explícita; main() la fija según el feature elegido.
FEATURE_SLUG = "feature"

# ── Checkpoint ─────────────────────────────────────────────────────

def load_checkpoint() -> dict:
    """Carga el checkpoint si existe, o retorna estado vacío."""
    if CHECKPOINT_FILE.exists():
        try:
            return json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"steps": {}}


def save_checkpoint(data: dict) -> None:
    """Persiste el checkpoint en disco de forma segura."""
    try:
        clean_data = {}
        for k, v in data.items():
            if k == "steps" and isinstance(v, dict):
                clean_steps = {}
                for sk, sv in v.items():
                    if isinstance(sv, dict):
                        clean_steps[sk] = {
                            "status": sv.get("status"),
                            "output": str(getattr(sv.get("output"), "raw", sv.get("output")) or "")
                        }
                    else:
                        clean_steps[sk] = str(sv)
                clean_data[k] = clean_steps
            else:
                clean_data[k] = v
        CHECKPOINT_FILE.write_text(json.dumps(clean_data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"  ⚠ Advertencia al guardar checkpoint: {e}")


# ── Output helpers ─────────────────────────────────────────────────

def extract_and_save_code(text, folders=None) -> list[str]:
    """Extrae bloques de código del output del LLM y los guarda en las carpetas de salida del rol."""
    text_str = str(getattr(text, "raw", text) or "")
    saved = []
    seen_paths = set()

    # Patrón para bloques ``` con o sin lenguaje
    code_block_pattern = re.compile(
        r"(?:(?:archivo|file|fichero|clase|class|componente)?[:\s*#/-]*[`\"]?(" + FILENAME_PATTERN + r")[`\"]?[^\n]*\n)?"
        r"```(?:[a-zA-Z0-9_\-]+)?\n(.*?)```",
        re.DOTALL | re.IGNORECASE,
    )
    header_pattern = re.compile(
        r"^\s*(?:(?:\/\/|\/\*|#|<!--|--)\s*)?(?:(?:file|archivo|path|ruta)\s*:\s*)?(" + FILENAME_PATTERN + r")",
        re.IGNORECASE,
    )

    for m in code_block_pattern.finditer(text_str):
        name = m.group(1)
        code = m.group(2).strip()
        if not code:
            continue

        target_name = name

        # 1. Buscar la ruta o el nombre en las primeras líneas del bloque
        if not target_name:
            for line in code.split("\n")[:5]:
                m_line = header_pattern.match(line)
                if m_line:
                    target_name = m_line.group(1)
                    break

        # 2. Deducir el nombre desde el contenido si el bloque no lo declara
        if not target_name:
            target_name = guess_filename(code)

        if not target_name:
            continue

        dest_rel = resolve_destination(target_name, code, FEATURE_SLUG, folders)

        if dest_rel and dest_rel not in seen_paths:
            seen_paths.add(dest_rel)

            dest = OUTPUT_DIR / dest_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(code, encoding="utf-8")
            saved.append(dest_rel)
            print(f"  💾 Guardado: {dest_rel}")

    if not saved:
        print("  ⚠ Advertencia: no se detectaron archivos de código fuente para extraer en este paso.")
    else:
        print(f"  📦 Total archivos de código generados en este paso: {len(saved)}")

    # Asegurar el andamiaje base solo si el rol escribe en los proyectos NestJS/React
    if needs_scaffolding(folders):
        ensure_scaffolding(OUTPUT_DIR)

    return saved


def needs_scaffolding(folders) -> bool:
    return folders is None or bool({BACKEND_DIR, FRONTEND_DIR} & set(folders))


def save_spec(filename: str, content, folder: str = "specs") -> None:
    specs_dir = OUTPUT_DIR / folder
    specs_dir.mkdir(parents=True, exist_ok=True)
    text = str(getattr(content, "raw", content) or "")
    (specs_dir / filename).write_text(text, encoding="utf-8")
    print(f"  ✅ specs/{filename}")


# ── Mini-Crew: corre un único agente/tarea ─────────────────────────

class StepLoadingSpinner:
    """Indicador de carga interactivo con spinner y tiempo transcurrido en tiempo real."""
    def __init__(self, message: str = "Agente procesando con el LLM..."):
        self.message = message
        self.stop_event = threading.Event()
        self.thread = None

    def _spin(self):
        chars = ["|", "/", "-", "\\"]
        idx = 0
        t0 = time.time()
        # Pequeña pausa inicial para que CrewAI imprima sus recuadros iniciales
        time.sleep(1.0)
        while not self.stop_event.is_set():
            elapsed = int(time.time() - t0)
            mins, secs = divmod(elapsed, 60)
            time_str = f"{mins:02d}:{secs:02d}"
            char = chars[idx % len(chars)]
            msg = f"\r  ⏳ [{char}] {self.message} ({time_str} transcurrido)   "
            try:
                sys.stdout.write(msg)
                sys.stdout.flush()
            except Exception:
                pass
            idx += 1
            time.sleep(0.2)
        try:
            sys.stdout.write("\r" + " " * 75 + "\r")
            sys.stdout.flush()
        except Exception:
            pass

    def __enter__(self):
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._spin, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=1.0)


def run_step(agent, task) -> str:
    """Ejecuta un mini-Crew de un solo agente y retorna el output en texto."""
    from crewai import Crew, Process   # import pesado: solo cuando se ejecuta un agente
    mini_crew = Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        verbose=True,
        tracing=False,
    )
    with StepLoadingSpinner(f"Generando con {agent.llm.model}..."):
        result = mini_crew.kickoff()

    # Pausa para permitir que los callbacks y paneles asíncronos de CrewAI terminen de pintar en consola
    time.sleep(1.2)

    if hasattr(result, "raw") and result.raw:
        return str(result.raw)
    if hasattr(task, "output") and task.output:
        return str(getattr(task.output, "raw", task.output))
    return str(result)


# ── HITL: aprobación humana ────────────────────────────────────────

def _flush_input_buffer():
    """Limpia caracteres tecleados accidentalmente mientras el LLM generaba."""
    try:
        import msvcrt
        while msvcrt.kbhit():
            msvcrt.getch()
    except Exception:
        pass


def print_formatted_preview(step_name: str, output: str, max_lines: int = 45) -> None:
    """Muestra el preview con formato enriquecido (tablas redondeadas alineadas, colores, preservando saltos de línea)."""
    output_str = str(getattr(output, "raw", output) or "").strip()
    if not output_str:
        print("  (Sin contenido generado)")
        return

    try:
        from rich.console import Console
        from rich.table import Table
        from rich.box import ROUNDED

        console = Console()
        console.print(f"\n[bold cyan]{'─'*65}[/bold cyan]")
        console.print(f"[bold cyan]▶ OUTPUT GENERADO — {step_name}[/bold cyan]")
        console.print(f"[bold cyan]{'─'*65}[/bold cyan]\n")

        all_lines = output_str.splitlines()
        lines = all_lines[:max_lines]
        hidden_count = max(0, len(all_lines) - max_lines)

        table_lines = []
        in_table = False
        non_table_buffer = []

        def flush_buf():
            if non_table_buffer:
                console.print("\n".join(non_table_buffer))
                non_table_buffer.clear()

        def render_table(tbl_lines):
            if len(tbl_lines) < 2:
                for tl in tbl_lines:
                    console.print(tl)
                return
            headers = [c.strip() for c in tbl_lines[0].split("|")[1:-1]]
            if not headers:
                for tl in tbl_lines:
                    console.print(tl)
                return
            table = Table(show_header=True, header_style="bold magenta", box=ROUNDED, expand=True)
            for i, h in enumerate(headers):
                if i == 0:
                    table.add_column(h, style="cyan", ratio=3)
                elif i == 1 and any(k in h.lower() for k in ("estado", "status", "resultado")):
                    table.add_column(h, justify="center", ratio=1)
                else:
                    table.add_column(h, style="white", ratio=4)

            # Las filas de datos comienzan tras el separador | --- |
            for r in tbl_lines[2:]:
                cols = [c.strip() for c in r.split("|")[1:-1]]
                if len(cols) == len(headers):
                    table.add_row(*cols)
                elif cols:
                    while len(cols) < len(headers):
                        cols.append("")
                    table.add_row(*cols[:len(headers)])
            console.print(table)

        for line in lines:
            st = line.strip()
            if st.startswith("|") and st.endswith("|") and st.count("|") >= 2:
                if not in_table:
                    flush_buf()
                    in_table = True
                table_lines.append(st)
            else:
                if in_table:
                    render_table(table_lines)
                    table_lines.clear()
                    in_table = False
                non_table_buffer.append(line)

        if in_table:
            render_table(table_lines)
        flush_buf()

        if hidden_count > 0:
            console.print(f"\n[dim italic]... [{hidden_count} líneas adicionales guardadas en specs/ y código][/dim italic]")

        console.print(f"\n[bold cyan]{'─'*65}[/bold cyan]")

    except Exception:
        # Fallback sin formato especial si falla Rich
        sep = "─" * 65
        print(f"\n{sep}\n▶ OUTPUT GENERADO — {step_name}\n{sep}")
        print("\n".join(output_str.splitlines()[:max_lines]))
        print(sep)


def hitl_approve(step_name: str, output: str) -> tuple[str, str]:
    """
    Muestra un preview del output y pide aprobación de forma clara e interactiva.

    Retorna:
        ("s", "")         → aprobado
        ("n", "")         → rechazado (abortar)
        ("r", feedback)   → reintentar el paso (con feedback opcional del usuario)
    """
    import time
    time.sleep(0.3)

    print_formatted_preview(step_name, output)

    box_line = "=" * 65
    banner = f"""
{box_line}
 🔔 ACCIÓN REQUERIDA — Aprobación humana para: {step_name}
{box_line}
 [s] = Aprobar y continuar al siguiente paso
 [r] = Reintentar este mismo paso (permite ingresar feedback opcional)
 [n] = Rechazar y detener ejecución
{box_line}"""
    print(banner)

    while True:
        _flush_input_buffer()
        try:
            raw = input("\n👉 Ingresa tu opción [s/r/n] y presiona Enter: ").strip().lower()
        except EOFError:
            return "s", ""
        except KeyboardInterrupt:
            print("\n❌ Interrupción por teclado.")
            return "n", ""

        tokens = raw.split()
        choice = tokens[-1] if tokens else ""

        if not choice:
            continue

        if choice in ("s", "si", "sí", "y", "yes", "1") or choice.endswith("s"):
            return "s", ""
        if choice in ("n", "no", "0") or choice.endswith("n"):
            return "n", ""
        if choice in ("r", "retry", "reintentar") or choice.endswith("r"):
            print("\n💬 [Feedback Opcional] Escribe instrucciones adicionales para el agente")
            feedback = input("   (o presiona Enter para reintentar sin cambios): ").strip()
            return "r", feedback

        print("\n  ✗ Opción inválida. Por favor ingresa 's' para sí, 'r' para reintentar o 'n' para rechazar.")


def confirm_qa_test_execution(ask=ui.prompt) -> bool:
    """Ask before running generated QA tests and creating the execution report."""
    while True:
        try:
            answer = ask(
                "\nLas pruebas QA ya fueron generadas y copiadas. "
                "¿Procedo a ejecutarlas y generar el informe con los resultados reales? [s/n]: "
            ).strip().lower()
        except EOFError:
            print("No se recibió confirmación; se omite la ejecución de QA.")
            return False
        if answer in ("s", "si", "sí", "y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        ui.error("Respuesta inválida. Ingresa s o n.")



# ── Contexto acumulado ─────────────────────────────────────────────

def _ctx_snippet(text: str, max_chars: int = 8000) -> str:
    """Recorta el output a max_chars para inyectarlo como contexto."""
    text_clean = str(text or "").strip()
    if len(text_clean) <= max_chars:
        return text_clean
    return text_clean[:max_chars] + "\n...(truncado para contexto)"


def build_context_note(approved_outputs: dict) -> str:
    """Construye una nota de contexto con los outputs aprobados anteriores."""
    parts = []
    labels = {key: label for key, label, *_ in workflow_steps(True)}
    for key, label in labels.items():
        if key in approved_outputs:
            val = str(getattr(approved_outputs[key], "raw", approved_outputs[key]) or "")
            parts.append(f"### {label}\n{_ctx_snippet(val)}")
    if not parts:
        return ""
    return "\n\n---\n## Contexto de pasos anteriores\n\n" + "\n\n".join(parts)



# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def _wait_for_agents(loader):
    """Espera la carga en segundo plano de crewai y los agentes (avisa si aún no terminó)."""
    if loader.is_alive():
        print("  ⏳ Cargando agentes (primera vez en esta sesión)...")
    return discover_roles()


def main():
    with_data = include_data()
    loader = prewarm()   # importar crewai y los agentes tarda; se hace mientras el usuario responde
    ui.banner()
    root = Path(__file__).resolve().parent
    # ── Selección guardada: solo se reconfirma; si cambia, se vuelve a elegir ──
    state = load_state(STATE_FILE)
    hint_id = (state.get("project") or {}).get("id")
    prefetch = Prefetch()   # la red del MCP es lenta: se pide mientras el usuario responde
    prefetch.start("projects", list_projects)
    if hint_id:
        prefetch.start("items", list_project_items, hint_id)
    use_saved = has_selection(state) and confirm_saved(state) == USE
    if not use_saved:
        state = {k: state[k] for k in ("pr_repo",) if k in state}   # el repo del PR se conserva

    # ── Rol a ejecutar (carpeta agents/ o «Todos») ──
    selected_roles = run_all = None
    if not (use_saved and state.get("role") and not os.getenv("WALLEE_ROLE")):
        selected_roles, run_all = choose_roles(_wait_for_agents(loader), with_data)
        state["role"] = "all" if run_all else selected_roles[0].KEY
        state["role_label"] = "Todos" if run_all else selected_roles[0].LABEL

    # ── Cambio OpenSpec de trabajo ──
    if use_saved and "openspec_change" in state and not os.getenv("WALLEE_OPENSPEC_CHANGE"):
        selected_change = find_change(root, state["openspec_change"])
        if selected_change is None and state["openspec_change"]:
            ui.error(f"El cambio OpenSpec guardado '{state['openspec_change']}' ya no existe.")
            selected_change = select_change(root)
    else:
        selected_change = select_change(root)
    state["openspec_change"] = selected_change.name if selected_change else None
    if selected_change:
        ui.success(f"Cambio OpenSpec: {selected_change.name} ({selected_change.done}/{selected_change.total} tasks)")

    # ── Repositorio de destino donde se copiará output/ al terminar ──
    saved_target = Path(state["target_repo"]) if use_saved and state.get("target_repo") else None
    if saved_target and saved_target.is_dir() and not os.getenv("WALLEE_TARGET_REPO"):
        target_repo = saved_target
        ui.success(f"Repositorio de salida: {target_repo}")
    else:
        target_repo = select_target_repo(root)
    state["target_repo"] = str(target_repo)
    save_state(STATE_FILE, state)   # se guarda ya: si interrumpes más adelante, no se vuelve a preguntar

    # ── MCP Proyectos ─────────────────────────────────────────────────
    print("\n[MCP Proyectos] Autenticando...")
    if VERBOSE_MCP_TOOLS:
        print("[MCP Proyectos] Herramientas disponibles:")
        for t in proj_tools():
            print(f"  · {t['name']}: {t['description']}")
    else:
        print("[MCP Proyectos] Conectado.")

    # ── Selección de proyecto ─────────────────────────────────────────
    projects = prefetch.get("projects", list_projects)
    if not projects:
        print("[MCP Proyectos] ✗ No se encontraron proyectos.")
        raise SystemExit(1)

    if len(projects) == 1:
        selected_project = projects[0]
        print(f"\n[MCP Proyectos] Proyecto asignado: {selected_project['title']}")
    elif use_saved and find_project(projects, state.get("project")):
        selected_project = find_project(projects, state["project"])
        print(f"\n[MCP Proyectos] Proyecto guardado: {selected_project['title']}")
    else:
        print("\n[MCP Proyectos] Proyectos disponibles:")
        for i, proj in enumerate(projects, 1):
            icon = "⚫" if proj.get("closed") else "🟢"
            print(f"  {i}. {icon} {proj.get('title', '(sin título)')}")
        while True:
            sel = input(f"\nSelecciona el número de proyecto [1-{len(projects)}]: ").strip()
            if sel.isdigit() and 1 <= int(sel) <= len(projects):
                selected_project = projects[int(sel) - 1]
                break
            print(f"  ✗ Entrada inválida.")
    state["project"] = {"id": selected_project.get("id"), "title": selected_project.get("title")}
    save_state(STATE_FILE, state)

    # ── Selección de feature del backlog (siempre se elige: cambia en cada ejecución) ──
    # Si es el proyecto que ya se pidió en segundo plano, se reutiliza ese backlog
    backlog = (prefetch.get("items", list_project_items, hint_id) if selected_project.get("id") == hint_id
               else list_project_items(selected_project["id"]))
    if not backlog:
        print("[MCP Proyectos] ✗ El proyecto no tiene items en el backlog.")
        raise SystemExit(1)

    print(f"\n[MCP Proyectos] Backlog de '{selected_project['title']}':")
    for i, item in enumerate(backlog, 1):
        num = f"#{item['number']} — " if item.get("number") else ""
        print(f"  {i:>2}. {describe_item(item)} {num}{item.get('title', '(sin título)')}")

    while True:
        sel = input(f"\nSelecciona el número de feature [1-{len(backlog)}]: ").strip()
        if sel.isdigit() and 1 <= int(sel) <= len(backlog):
            selected_item = backlog[int(sel) - 1]
            break
        print(f"  ✗ Entrada inválida.")

    feature = get_feature_from_item(selected_item)
    print(f"\n[MCP Proyectos] Feature cargado: {feature['title']}")
    print(f"[MCP Proyectos] Criterios de aceptación: {feature['acceptance_criteria']}")

    # ── Roles ya cargados (en segundo plano) ──
    all_roles = _wait_for_agents(loader)
    role_by_key = {role.KEY: role for role in all_roles}
    if selected_roles is None:
        picked = saved_roles(all_roles, with_data, state["role"])
        if picked is None:
            ui.error(f"El rol guardado '{state['role']}' ya no existe.")
            picked = choose_roles(all_roles, with_data)
            state["role"] = "all" if picked[1] else picked[0][0].KEY
            state["role_label"] = "Todos" if picked[1] else picked[0][0].LABEL
            save_state(STATE_FILE, state)
        selected_roles, run_all = picked
    steps = workflow_steps(with_data, roles=selected_roles)
    global FEATURE_SLUG
    FEATURE_SLUG = feature_slug(feature["title"])

    # ── Cargar checkpoint ─────────────────────────────────────────────
    checkpoint  = load_checkpoint()
    steps_state = checkpoint.get("steps", {})

    # Verificar si el checkpoint corresponde a este feature (por título)
    ckpt_feature = checkpoint.get("feature_title")
    if ckpt_feature and ckpt_feature != feature["title"]:
        print(f"\n⚠  El checkpoint existente es para '{ckpt_feature}'.")
        ans = input("¿Ignorar checkpoint y empezar desde cero? [s/n]: ").strip().lower()
        if ans == "s":
            steps_state = {}
            checkpoint  = {"steps": {}}
        else:
            print("Abortando. Cambia de feature o elimina output/.checkpoint.json para reiniciar.")
            raise SystemExit(0)

    mode = "with-data" if with_data else "standard"
    previous_mode = checkpoint.get("workflow_mode", "standard")
    if run_all:
        if steps_state and previous_mode != mode:
            print("El checkpoint pertenece a otro flujo. Conserva output/ moviéndolo a otra carpeta antes de cambiar CREWAI_INCLUDE_DATA.")
            raise SystemExit(1)
        checkpoint["workflow_mode"] = mode
    checkpoint["feature_title"] = feature["title"]
    checkpoint["openspec_change"] = selected_change.name if selected_change else None

    # ── Recuperar outputs aprobados del checkpoint ────────────────────
    approved_outputs: dict = {}
    for step_key, step_data in steps_state.items():
        if step_data.get("status") == "approved":
            approved_outputs[step_key] = step_data.get("output", "")

    # Un solo rol: los informes de los demás roles, si existen en output/, sirven de contexto
    if not run_all:
        for other in all_roles:
            report = OUTPUT_DIR / report_folder(other) / other.SPEC_FILE
            if other not in selected_roles and other.KEY not in approved_outputs and report.is_file():
                approved_outputs[other.KEY] = report.read_text(encoding="utf-8")

    # ── Enriquecer backstory del Architect con brief del feature ──────
    role_by_key["architect"].agent.backstory += HEX_PROMPT_PREFIX + (
        f"\n\n## Feature brief\n"
        f"**{feature['title']}**\n\n{feature['description']}\n\n"
        f"**Criterios de aceptación:** {', '.join(feature['acceptance_criteria'])}"
    )

    # ── Definición de pasos ───────────────────────────────────────────
    # (key, label, agent, spec_filename, extraer_código)


    # Andamiaje de build: en este punto, con proyecto y feature ya elegidos.
    # Solo si algún rol seleccionado escribe en backend-nestjs/frontend-react.
    if any(needs_scaffolding(output_folders(role)) for role in selected_roles):
        ensure_scaffolding(OUTPUT_DIR)

    print("\n" + "=" * 60)
    print(f"S1 CrewAI — HITL — Feature: {feature['title']}")
    print("Rol: " + ("Todos" if run_all else selected_roles[0].LABEL)
          + (f" · OpenSpec: {selected_change.name}" if selected_change else ""))
    print("Pasos: " + " → ".join(step[1] for step in steps))
    print("=" * 60)

    # Ciclo de vida: el item pasa de Todo a In Progress al empezar a trabajarlo
    start_item(selected_project, selected_item)

    # ── Loop HITL ─────────────────────────────────────────────────────
    for step_key, step_label, agent, spec_file, do_code in steps:
        role = role_by_key[step_key]
        folders = output_folders(role)

        # Un solo rol ya aprobado: permite repetirlo en vez de omitirlo
        if (not run_all and step_key != "qa_security"
                and steps_state.get(step_key, {}).get("status") == "approved"):
            ans = input(f"\n{step_label} ya fue aprobado. ¿Volver a ejecutarlo? [s/n]: ").strip().lower()
            if ans == "s":
                steps_state.pop(step_key)
                approved_outputs.pop(step_key, None)

        # ── Ya aprobado en ejecución anterior → omitir ───────────────
        if steps_state.get(step_key, {}).get("status") == "approved":
            print(f"\n✅ [{step_label}] — ya aprobado (checkpoint). Omitiendo.")
            if do_code:
                saved_out = steps_state[step_key].get("output", "")
                if saved_out:
                    extract_and_save_code(saved_out, folders)
            continue

        print(f"\n{'─'*60}")
        print(f"▶ PASO: {step_label}")
        print(f"{'─'*60}")

        # ── Bucle de reintentos ───────────────────────────────────────
        user_feedback = ""
        while True:
            # Rebuild tasks fresh en cada intento
            task_map = build_tasks(feature, with_data, selected_roles)
            task = task_map[step_key]

            if step_key == "qa_security":
                task.description += (
                    "\n\nFASE AUTOMÁTICA DE GENERACIÓN: genera los archivos de pruebas y el borrador QA, "
                    "pero marca todos los resultados NO EJECUTADO. main.py copiará los archivos y pedirá "
                    "confirmación; no existe informe final hasta que se ejecute la segunda tarea con salida real."
                )

            # Inyectar contexto de pasos anteriores en la descripción
            ctx_note = build_context_note(approved_outputs)
            if ctx_note:
                task.description = task.description + ctx_note
            task.description += change_context(selected_change, role)

            if with_data and step_key == "ai_engineer_1":
                task.description += "\nUsa el diseño de persistencia aprobado de Datos y persistencia. No regeneres sus archivos SQL; implementa el acceso a datos del backend NestJS según ese contrato."
            if with_data and step_key == "qa_security":
                task.description += "\nRevisa también el modelo, migración, integridad y compatibilidad del contrato aprobado de Datos y persistencia con el backend NestJS."

            # Si el usuario ingresó feedback opcional en un reintento, inyectarlo como directiva prioritaria
            if user_feedback:
                task.description += (
                    f"\n\n---\n## ⚠️ INSTRUCCIONES ESPECÍFICAS DE CORRECCIÓN (FEEDBACK HUMANO):\n"
                    f"{user_feedback}\n"
                    f"Por favor atiende y corrige con máxima prioridad las observaciones del usuario indicadas arriba.\n"
                )

            # Ejecutar mini-Crew
            output = run_step(agent, task)

            # QA test generation is automatic; execution has its own confirmation after files are copied.
            if step_key == "qa_security":
                print("\n✅ Pruebas y documentos QA generados. Se guardarán sin ejecutar todavía.")
                decision, user_feedback = "s", ""
            else:
                decision, user_feedback = hitl_approve(step_label, output)

            if decision == "s":
                # APROBADO
                print(f"\n✅ {step_label} aprobado. Guardando artefactos...")
                save_spec(spec_file, output, report_folder(role))
                if do_code:
                    extract_and_save_code(output, folders)
                approved_outputs[step_key] = output
                steps_state[step_key] = {"status": "approved", "output": output}
                checkpoint["steps"] = steps_state
                save_checkpoint(checkpoint)
                break

            elif decision == "n":
                # RECHAZADO
                print(f"\n❌ {step_label} rechazado. Abortando ejecución.")
                steps_state[step_key] = {"status": "rejected", "output": output}
                checkpoint["steps"] = steps_state
                save_checkpoint(checkpoint)
                raise SystemExit(0)

            else:
                # REINTENTAR
                if user_feedback:
                    print(f"\n🔄 Reintentando {step_label} incorporando tu feedback: \"{user_feedback}\"...")
                else:
                    print(f"\n🔄 Reintentando {step_label}...")
                # el while True vuelve a ejecutar el paso

    # ── Todos los pasos aprobados ─────────────────────────────────────
    presenter_text = str(getattr(approved_outputs.get("presenter", ""), "raw", approved_outputs.get("presenter", "")) or "")

    print("\n" + "=" * 60)
    print("✅ TODOS LOS PASOS APROBADOS" if run_all else f"✅ {selected_roles[0].LABEL} APROBADO")
    print("=" * 60)
    for role in selected_roles:
        print(f"  output/{report_folder(role)}/{role.SPEC_FILE}")
        for folder in output_folders(role) or ():
            print(f"  output/{folder}/   ← {role.LABEL}")
    if selected_change:
        print(f"  OpenSpec: revisa y marca las tareas en openspec/changes/{selected_change.name}/tasks.md")
    print("=" * 60)
    copied = copy_output(OUTPUT_DIR, target_repo)
    ui.success(f"{len(copied)} archivos copiados de output/ a {target_repo}")
    qa_role = next((role for role in selected_roles if role.KEY == "qa_security"), None)
    qa_test_result = None
    if qa_role:
        if confirm_qa_test_execution():
            qa_map_path = Path(__file__).resolve().parent / "agents" / "qa-project-map.json"
            qa_map = json.loads(qa_map_path.read_text(encoding="utf-8"))
            test_command = qa_map.get("qa", {}).get("run_tests_command")
            if not isinstance(test_command, str) or not test_command.strip():
                raise ValueError(f"Falta qa.run_tests_command en {qa_map_path}")

            qa_test_result = run_qa_tests(target_repo, test_command)
            append_execution_evidence(OUTPUT_DIR / "test" / "evidence.md", qa_test_result)
            ui.success(f"Pruebas QA: {qa_test_result.status}")
            copied = copy_output(OUTPUT_DIR, target_repo)
            ui.success(f"Evidencia real copiada a {target_repo}")

            previous_qa_report = str(approved_outputs.get(qa_role.KEY, ""))
            report_task = qa_role.build_execution_report_task(
                feature, previous_qa_report, qa_test_result.report_text()
            )
            final_qa_report = run_step(qa_role.agent, report_task)
            save_spec(qa_role.SPEC_FILE, final_qa_report, report_folder(qa_role))

            copied = copy_output(OUTPUT_DIR, target_repo)
            ui.success(f"{len(copied)} archivos QA finales copiados a {target_repo}")
        else:
            print("Ejecución omitida. Los casos permanecen NO EJECUTADO y no se genera informe final.")

    approved_labels = [step[1] for step in steps if steps_state.get(step[0], {}).get("status") == "approved"]
    def remember_pr_repo(repo):
        state["pr_repo"] = repo
        save_state(STATE_FILE, state)

    publish_output(target_repo, copied, feature, [role.LABEL for role in selected_roles],
                   selected_change.name if selected_change else None, approved_labels,
                   pr_repo=state.get("pr_repo"), on_pr_repo=remember_pr_repo)
    if qa_role and (qa_test_result is None or not qa_test_result.passed):
        ui.error("El item permanece In Progress porque la suite QA no fue ejecutada o no pasó.")
    else:
        # Solo se ofrece pasar el item a Done después de que la suite QA haya pasado.
        finish_item(selected_project, selected_item)
    if presenter_text and any(role.KEY == "presenter" for role in selected_roles):
        print("\nInforme del Presenter (extracto):")
        print(presenter_text[-500:] if len(presenter_text) > 500 else presenter_text)


if __name__ == "__main__":
    main()
