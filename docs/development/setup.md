# Entorno y desarrollo

[Índice](../README.md) · [Pruebas](testing.md)

Ejecutar comandos desde la raíz salvo indicación. Los tres proyectos Python requieren `>=3.12,<3.13`; la raíz declara orchestrator, clarity-backend y output-analyzer en el workspace uv y el lockfile incluye los tres. MATLAB requiere su instalación separada.

## Python

```sh
uv sync --all-packages --all-extras
uv run --project apps/orchestrator acoustic-orchestrator --help
uv run --project apps/clarity-backend clarity-backend --help
uv run --project apps/output-analyzer output-analyzer --help
```

Para trabajo local por app, se mantienen `uv sync --project apps/orchestrator --extra dev`, `uv sync --project apps/clarity-backend` y `uv sync --project apps/output-analyzer --extra dev`. Los extras dev de orchestrator/analyzer incluyen pytest, Ruff y mypy; matplotlib pertenece al dev del orquestador para inspección geométrica. Clarity no declara extra dev propio: las pruebas usan pytest disponible en el entorno compartido.

Estas instrucciones preparan un entorno; no indican que se haya instalado ni actualizado ninguna dependencia en la auditoría. Consulte las [guías locales](../README.md#guías-locales) para invocaciones y opciones específicas.

## Render y assets

`render-static` necesita `matlab` en PATH, MATLAB con ITA Toolbox/RAVEN configurados y todos los WAV, materiales, directividades, HRTF/DAFF y RPF referenciados. MATLAB estático exige 44100 Hz. `prepare_raven_project.m` usa actualmente la ruta Windows local `C:\ITASoftware\Raven\RavenInput` para proyectos; no asumir portabilidad sin revisar el entorno.

Los paths YAML relativos se interpretan desde el directorio del config, y los jobs relativos de Clarity desde el directorio de trabajo del backend. Inspeccione [ejemplos](../../configs/experiments/), [catálogos auditivos](../../configs/hearing/) y [entradas MATLAB](../../apps/matlab/example/) antes de ejecutar. No son datasets incluidos ni garantizan que cada asset exista en un checkout nuevo.

`generate-manifests` evita invocar MATLAB pero escribe artefactos y puede necesitar los assets usados al muestrear. `output-analyzer analyze` necesita solo los metadatos existentes y un destino escribible. No abrirá WAV ni sus assets referenciados.

## Referencias para mantenimiento

Los contratos implementados están en [contratos](../architecture/contracts.md) y sus evidencias en la [auditoría](../maintenance/documentation-audit.md). Las reglas de agentes quedan en sus AGENTS originales. El registro de skills oculto del orquestador conserva rutas absolutas heredadas; no se actualiza el flujo de agentes en esta entrega.
