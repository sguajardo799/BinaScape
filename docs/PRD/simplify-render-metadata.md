# PRD - simplificacion de metadatos de salida render

> Nota editorial (13-10-2026): **CLOSED / IMPLEMENTED** por decision del responsable. Los estados de borrador, Hoy/Despues, criterios futuros y siguientes diffs del texto original son antecedentes historicos, no backlog vigente. Consulte [el indice PRD](README.md) y [la auditoria](../maintenance/documentation-audit.md) para contratos actuales, evidencias y diferencias entre revisiones. El contenido historico se preserva.
Estado: borrador
Creado: 22.09.2026
Alcance: Simplificar metadatos de salida de RAVEN o similar. No tocar Clarity

# RESUMEN
Hoy: Se tienen metadatos redundates tanto en summary como en batch. Producto de esto su revisión es tediosa y no se logra diferenciar el summary con batch.
Despues: Los metadatos generados permiten una revisión rapida y no se observan datos repetidos innecesariamente, permitiendo un debug mas rapido e intuitivo.

## Historia
Antes: Un usuario no técnico no entiende los metadatos y no sabe el porque muchos campos estan repetidos, siente que algo puede estar mal configurado. Además usuarios mas técnicos sienten que los metadatos son muy verbosos y mencionan multiples veces la misma información.
Desspues: Ambos usuarios pueden ver y entender la separación entre summary y batch, para el primero puede observar parametros de simulación relevantes y en el caso del segundo no ve información innecesaria.

## Objetivos / No-Objetivos
O1. Todo audio renderizado tiene sus metadatos ordenados y no redundantes
NO1. Eliminación de campos arbitrariamente

## Como funciona Hoy -> Como debiese funcionar
HOY                                             Despues
metadato_render contiene campos duplicados      metadato_render es claro y conciso|