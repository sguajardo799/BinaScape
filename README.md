# Binaural-Impared-Hear

El flujo soportado desde la raiz es el render estatico coordinado por `apps/orchestrator`.

## Modulos

- `apps/orchestrator`: proyecto Python que valida configs, genera manifiestos y coordina MATLAB/Clarity por subprocess y archivos.
- `apps/matlab`: modulo backend MATLAB/RAVEN.
- `apps/clarity-backend`: proyecto Python sibling que consume manifiestos de degradacion auditiva generados por el orchestrator.

## Python y workspace

El monorepo usa Python 3.12 (`>=3.12,<3.13`). La raiz declara un workspace `uv` conservador con miembros Python:

```text
apps/orchestrator
apps/clarity-backend
```

Los lockfiles por app (`apps/orchestrator/uv.lock` y `apps/clarity-backend/uv.lock`) se conservan como fuente operativa. No hay `uv.lock` raiz canonico en esta etapa.

## Prerrequisitos

- `uv` instalado.
- Python 3.12 disponible para `uv`.
- MATLAB, ITA Toolbox y RAVEN instalados localmente para ejecutar renders.
- `matlab` disponible en `PATH` para `render-static`.
- El `.rpf` configurado en `configs/experiments/static_example.yml` debe existir en el entorno local.

## Flujo estatico root-first

Instalar dependencias de los proyectos Python desde la raiz:

```sh
uv sync --project apps/orchestrator --extra dev
uv sync --project apps/clarity-backend
```

Verificar los CLIs desde la raiz:

```sh
uv run --project apps/orchestrator acoustic-orchestrator --help
uv run --project apps/clarity-backend clarity-backend --help
```

Generar manifiestos sin invocar MATLAB:

```sh
uv run --project apps/orchestrator acoustic-orchestrator generate-manifests configs/experiments/static_example.yml
```

Ejecutar el render estatico completo, con MATLAB/RAVEN estan disponibles:

```sh
uv run --project apps/orchestrator acoustic-orchestrator render-static configs/experiments/static_example.yml
```

Este comando usa `configs/experiments/static_example.yml`, delega el render a `apps/matlab/run_raven_static_render.m` y, si se habilita degradacion auditiva, delega Clarity a `apps/clarity-backend` mediante `uv run --project`.