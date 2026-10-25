from pathlib import Path

import typer
from acoustic_orchestrator.terminal_progress import TerminalProgress

from acoustic_orchestrator.pipeline.render_pipeline import (
    RenderSummary,
    RenderStaticRunError,
    generate_static_manifests_with_summary,
    render_static_scenes,
    run_clarity_handoff,
)
from acoustic_orchestrator.pipeline.interruption_recovery import reconcile_interrupted_chain
from acoustic_orchestrator.pipeline.process_retries import RetryBlockedError
from acoustic_orchestrator.pipeline.static_plan import StaticPlanConflict


app = typer.Typer(help="CLI del orquestador acústico")


@app.callback()
def callback() -> None:
    """CLI principal."""


@app.command("generate-manifests")
def generate_manifests(
    config: Path,
    no_progress: bool = typer.Option(False, "--no-progress", help="Desactiva el progreso en stderr"),
) -> None:
    with TerminalProgress(enabled=not no_progress) as terminal:
        manifest_paths, sampling = generate_static_manifests_with_summary(
            config, progress=terminal if not no_progress else None,
        )
    typer.echo(
        " ".join(
            [
                f"Generados {len(manifest_paths)} manifiestos en {manifest_paths[0].parent if manifest_paths else config}.",
                f"muestreo_solicitadas={sampling['requested_scenes']}",
                f"muestreo_generadas={sampling['generated_scenes']}",
                f"fallidos={sampling['failed_scene_indices']}",
                f"diagnóstico={sampling['failures_path']}",
            ]
        )
    )
    if sampling["status"] == "partial":
        raise typer.Exit(code=2)
    if sampling["status"] == "failed":
        raise typer.Exit(code=1)


@app.command("render-static")
def render_static(
    config: Path,
    no_progress: bool = typer.Option(False, "--no-progress", help="Desactiva el progreso en stderr"),
) -> None:
    try:
        with TerminalProgress(enabled=not no_progress) as terminal:
            manifest_paths, summary = render_static_scenes(config, progress=terminal if not no_progress else None)
    except RenderStaticRunError as exc:
        _echo_render_summary(exc.manifest_paths, exc.summary, config)
        raise typer.Exit(code=1) from exc

    _echo_render_summary(manifest_paths, summary, config)
    if summary["sampling"]["status"] == "partial":
        raise typer.Exit(code=2)
    if summary["sampling"]["status"] == "failed":
        raise typer.Exit(code=1)


@app.command("clarity-handoff")
def clarity_handoff(
    config: Path,
    submit: bool = typer.Option(False, "--submit", help="Ejecuta el backend de Clarity después de preparar el manifiesto"),
    no_progress: bool = typer.Option(False, "--no-progress", help="Desactiva el progreso en stderr, incluido Clarity"),
) -> None:
    with TerminalProgress(enabled=not no_progress) as terminal:
        summary = run_clarity_handoff(config, submit=submit, progress=terminal if not no_progress else None)
    typer.echo(
        " ".join(
            [
                f"Clarity handoff: total={summary['total_jobs']}",
                f"planificados={summary['planned_jobs']}",
                f"completados={summary['completed_jobs']}",
                f"bloqueados={summary['blocked_jobs']}",
                f"omitidos={summary['skipped_jobs']}",
                f"manifest={summary['manifest_path']}",
                f"index={summary['index_path']}",
                f"mensaje={summary['message']}",
            ]
        )
    )


@app.command("reconcile-static")
def reconcile_static(
    run_root: Path,
    chain: str = typer.Option(..., "--chain", help="UUID de la cadena interrumpida"),
    attempt: str | None = typer.Option(None, "--attempt", help="UUID exacto del intento que se autoriza reconciliar"),
    apply: bool = typer.Option(False, "--apply", help="Publica la reconciliación; por defecto solo inspecciona"),
    confirm_stopped: bool = typer.Option(False, "--confirm-stopped", help="Confirma que no quedan escritores asociados"),
    reason: str = typer.Option("", "--reason", help="Motivo de la intervención manual, obligatorio al aplicar"),
) -> None:
    """Reconcilia una interrupción sin inventar un código de salida ni renderizar."""
    try:
        result = reconcile_interrupted_chain(
            run_root, chain, apply=apply, confirm_stopped=confirm_stopped,
            expected_attempt=attempt, reason=reason,
        )
    except (RetryBlockedError, StaticPlanConflict, OSError, ValueError) as exc:
        typer.echo(f"Reconciliación bloqueada: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"{'Reconciliado' if apply else 'Solo lectura'}: escena={result['scene_id']} "
        f"cadena={chain} intento={result['attempt_id']} consumidos={result['consumed_attempts']} "
        f"restantes={result['remaining_attempts']} siguiente_estado={result['next_state']} "
        "exit_code=null termination_observed=false"
    )
    if apply:
        typer.echo(f"Evidencia: {result['reconciliation_path']}. No se ha lanzado MATLAB; reanude render-static por separado.")
    else:
        typer.echo("No se modificaron archivos. Para aplicar indique --apply --attempt <UUID> --confirm-stopped --reason <motivo>.")


def main() -> None:
    app()


def _echo_render_summary(manifest_paths: list[Path], summary: RenderSummary, config: Path) -> None:
    clarity_bits: list[str] = []
    if summary["clarity"] is not None:
        clarity = summary["clarity"]
        clarity_bits = [
            "Clarity "
            f"total={clarity['total_jobs']} "
            f"planificados={clarity['planned_jobs']} "
            f"enviados={clarity['submitted_jobs']} "
            f"completados={clarity['completed_jobs']} "
            f"bloqueados={clarity['blocked_jobs']} "
            f"omitidos={clarity['skipped_jobs']} "
            f"auto_submit={clarity['auto_submit']} "
            f"submitted={clarity['submitted']} "
            f"manifest={clarity['manifest_path']} "
            f"mensaje={clarity['message']}"
        ]

    typer.echo(
        " ".join(
            [
                f"Generados {len(manifest_paths)} manifiestos y ejecutados {summary['render_jobs']} render(s) de MATLAB",
                f"desde {manifest_paths[0].parent if manifest_paths else config}.",
                f"Índice: {summary['index_path']}.",
                (
                    "Variantes "
                    f"totales={summary['total_variants']} "
                    f"planificadas={summary['planned_variants']} "
                    f"completadas={summary['completed_variants']} "
                    f"parciales={summary['partial_variants']} "
                    f"fallidas={summary['failed_variants']} "
                    f"reanudadas={summary['resumed_variants']} "
                    f"inconsistencias={summary['inconsistent_variants']}"
                ),
                (
                    "MATLAB "
                    f"procesos_fallidos={summary.get('failed_matlab_processes', 0)} "
                    f"recuperados={summary.get('recovered_matlab_processes', 0)} "
                    f"recuperaciones_reanudadas={summary.get('resumed_recovered_variants', 0)}"
                ),
                (
                    "Proceso "
                    f"invocaciones={summary.get('matlab_invocations', summary['render_jobs'])} "
                    f"reintentos={summary.get('process_retries', 0)} "
                    f"variantes_tras_reintento={summary.get('variants_completed_after_retry', 0)} "
                    f"corte={summary.get('circuit_open', False)}"
                ),
                (
                    "Muestreo "
                    f"solicitadas={summary['sampling']['requested_scenes']} "
                    f"muestreo_generadas={summary['sampling']['generated_scenes']} "
                    f"muestreo_fallidos={summary['sampling']['failed_scene_indices']} "
                    f"diagnóstico={summary['sampling']['failures_path']}"
                ),
                *clarity_bits,
            ]
        )
    )
