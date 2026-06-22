from pathlib import Path

import typer

from acoustic_orchestrator.pipeline.render_pipeline import (
    RenderSummary,
    RenderStaticRunError,
    generate_static_manifests,
    render_static_scenes,
    run_clarity_handoff,
)


app = typer.Typer(help="CLI del orquestador acústico")


@app.callback()
def callback() -> None:
    """CLI principal."""


@app.command("generate-manifests")
def generate_manifests(config: Path) -> None:
    manifest_paths = generate_static_manifests(config)
    typer.echo(f"Generados {len(manifest_paths)} manifiestos en {manifest_paths[0].parent if manifest_paths else config}")


@app.command("render-static")
def render_static(config: Path) -> None:
    try:
        manifest_paths, summary = render_static_scenes(config)
    except RenderStaticRunError as exc:
        _echo_render_summary(exc.manifest_paths, exc.summary, config)
        raise typer.Exit(code=1) from exc

    _echo_render_summary(manifest_paths, summary, config)


@app.command("clarity-handoff")
def clarity_handoff(
    config: Path,
    submit: bool = typer.Option(False, "--submit", help="Ejecuta el backend de Clarity después de preparar el manifiesto"),
) -> None:
    summary = run_clarity_handoff(config, submit=submit)
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
                *clarity_bits,
            ]
        )
    )
