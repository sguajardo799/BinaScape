# Design

## Context

`reconcile-static` reconcilia interrupciones como `confirmed_interruption` y programa otro intento: no acredita éxito. Aquí el caso es el inverso: el proceso sí terminó y el render es válido, pero el orquestador perdió la lectura de la evidencia de terminación. Volver a renderizar no es posible sin `overwrite` porque las salidas válidas ya existen y la cadena es terminal.

## Decisions

- **La evidencia de terminación es el resultado publicado por el pool, no la ausencia de procesos.** Solo `exit_code` 0 en `results/<attempt_id>.json` con el mismo `attempt_id` habilita la operación; un exit distinto de 0 no se reconcilia (no hay render que aceptar).
- **Aceptar exige la misma evidencia funcional que una aceptación normal, o más:** `completion.json` válido (obligatorio aquí, aunque una aceptación con exit 0 lo tolere ausente), sin `failure.json`, y `validate_render` con perfil `normal` válido y sin rechazo acústico. Un rechazo acústico queda fuera de alcance y bloquea.
- **Reconciliar a `accepted`, no a un reintento.** El resultado final equivale a lo que habría ocurrido si la lectura hubiera tenido éxito; la cadena sigue `not_retryable` y no se consume ni renueva presupuesto.
- **Registro + preimágenes + verificación en cada reanudación**, siguiendo el patrón de `interruption_recovery`: el outcome reconciliado debe ser exactamente la proyección del registro, y el resultado del pool debe conservar la huella registrada. Se reutilizan `_encoded`, `_exclusive` y `active_render_writers`.
- **Se actualiza la fila del índice** porque `resume_evidence` rechaza filas con `decision=abort`; la fila previa queda en el registro.
- **Búsqueda de la sesión** cuando el outcome no la registró: el runner solo guarda `pool_session_id` al leer el resultado con éxito. Se exige un único resultado entre las sesiones del lote.

## Risks / Trade-offs

- [Modifica metadata persistida de un lote] → Inspección por defecto, confirmaciones explícitas, preimágenes exactas y verificación en cada reanudación.
- [Un resultado del pool manipulado podría acreditar un render] → También se exigen recibo de finalización válido y validación del render; la huella del resultado queda fijada en el registro.
- [Verificar en cada reanudación relee el resultado del pool] → Coste despreciable (un JSON por intento reconciliado); si la sesión se borra, la reanudación se bloquea en vez de aceptar sin evidencia.
