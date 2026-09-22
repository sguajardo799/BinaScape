# PRD - simplificacion de metadatos de salida render
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