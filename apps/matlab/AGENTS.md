# AGENTS.md

## Cómo funciona este repositorio

- El flujo principal hoy es el **render estático**.
- El entrypoint usable es `run_raven_static_render.m`.
- La implementación central del flujo estático está en `static/render_static_scene.m`.
- La carga y validación básica de escenas estáticas pasa por `core/load_scene_config.m` y `core/validate_static_scene_config.m`.
- `core/prepare_raven_project.m` prepara el proyecto RAVEN y depende de supuestos del entorno local.

## Advertencia sobre el flujo dinámico

- `run_raven_dynamic_render.m` **no está implementado como flujo soportado**.
- `dynamic/render_dynamic_scene.m` sigue siendo un placeholder con `TODO`.
- No describir el render dinámico como feature disponible.

## Guía de desarrollo y edición segura

- Leer el código antes de afirmar capacidades del repositorio.
- Verificar entrypoints reales y funciones efectivamente llamadas.
- Distinguir entre código funcional, scaffolding, placeholders y `TODO`.
- No asumir que la existencia de un archivo implica soporte completo.
- Mantener la separación entre flujo estático usable y flujo dinámico pendiente.

## Guía de documentación

- Escribir documentación en español, clara y práctica.
- Mantener un tono conservador y preciso.
- No sobreafirmar features.
- Marcar explícitamente lo no implementado o no soportado.
- Si cambia el estado real del flujo dinámico, actualizar `README.md` y `AGENTS.md` en el mismo cambio.

## Dependencias y contexto

- El repositorio depende de MATLAB, ITA Toolbox y RAVEN.
- Hay supuestos de entorno local y de Windows en partes del flujo.
- No asumir portabilidad o reproducibilidad completa sin verificar el entorno.
