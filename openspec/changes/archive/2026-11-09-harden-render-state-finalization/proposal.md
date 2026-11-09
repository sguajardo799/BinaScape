# Proposal

## Why

La segunda E2E confirmó que un render válido y aceptado puede detener el lote al persistir su clasificación de reintentos, dejando `outcome.json` confirmado y `chain.json` en `running`. La recuperación implementada solo cubre aceptación no persistida; además, el reemplazo de esos metadatos no tolera bloqueos transitorios de Windows.

## What Changes

- Recuperar automáticamente, en la siguiente llamada con reanudación activa, el cierre incompleto de una cadena cuyo proceso terminó de forma observada y cuyo render aceptado puede revalidarse completamente.
- Distinguir una etiqueta persistida `running` de una terminación realmente desconocida: admitir exclusivamente candidatos acreditados, sin retirar locks ni autorizar otra invocación.
- Verificar la resolución aceptada ya publicada en el circuito y conservarla sin volver a contabilizar el intento, reiniciar rachas ni alterar su orden.
- Extender la auditoría de reparación con una acción explícita de finalización de cadena, publicación idempotente y rechazo seguro por lectores incompatibles; conservar la lectura de reparaciones anteriores.
- Reintentar de forma acotada el reemplazo de metadatos mutables ante errores Windows seleccionados, sin repetir la serialización, la simulación ni transacciones completas. Mantener intacta la política de reemplazo ya existente del índice.
- Mantener causas y rutas visibles cuando se agote la espera; conservar presupuestos, validación acústica y archivos protegidos.

## Capabilities

### New Capabilities

Ninguna: se amplían capacidades existentes.

### Modified Capabilities

- `static-render-acceptance`: exigir cierre coherente de cadenas conocidas antes de reutilizar/entregar resultados y auditar la finalización sin fabricar evidencia de proceso.
- `static-render-resumption`: identificar y completar automáticamente cierres acreditados durante el preflight global, conservando los bloqueos de terminación desconocida.
- `matlab-process-retries`: añadir tolerancia acotada a reemplazos transitorios de metadatos y conservar presupuesto/circuito al completar una resolución ya acreditada.

## Impact

- Complemento de `recover-render-persistence-failures`, ya implementado pero aún no archivado; se desarrollará sobre esa implementación en `feat/recover-render-persistence-failures`. Archivar/sincronizar el cambio anterior antes de este. Estas deltas añaden requisitos con nombres distintos para no reemplazar sus deltas pendientes.
- Implementación posterior solo en `apps/orchestrator`: persistencia de intentos, cadenas/circuito y resúmenes; `persistence_recovery.py`, `persistence_journal.py`, `render_history.py`, preflight, tests y README. No cambia el algoritmo ni los contratos de los backends.
- **BREAKING para lectores de reparación anteriores:** nuevo formato de journal versión 2 para la nueva acción; outcomes reparados mantienen versión 2. Se siguen admitiendo outcomes versión 1 y journals de reparación versión 1 válidos. No se migran archivos ya correctos.
- Sin nuevos campos YAML obligatorios, cambios de nombres de audio, ejecuciones del experimento real ni edición de sus resultados durante desarrollo.
