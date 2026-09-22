# PRD - Incorporar politica de posición de fuentes determinista
Estado: borrador
Creado: 22.09.2026
Alcance: Añadir a las politicas aleatorias ya existentes una alternativa que fije la posicion de las fuentes respecto el receptor. No tocar politicas actuales.

## Resumen
Hoy: solo existen politicas pseudo aleatorias con/sin restricción de distancia al receptor
Despues: se incorpora la alternativa de posiciones deterministas respecto al receptor, generando datos utilies para aplicaciones mas simples/restingidas

## Historia
Hoy: Los usuarios solo pueden generar estimulos posicionados aleatoriamente en la escena, incorporando ciertas restricciones. Esto para generar conjuntos de datos restringidos para por ejemplos pruebas de audición no es deseable.
Despues: Se extienden las politicas actuales incorporando una de posiciones relativas al receptor, recibiendo: DoA(azimuth y elevacion en grados) y Distancias(1 o mas). De esta forma el pipeline puede generar conjuntos para pruebas de audición para casos mas restringidos y/o establecidos.

## Objetivos / No-Objetivos
O1. Incorporar una nueva politica de posicionamiento de fuentes relativo al receptor.
O2. Debe soportar listas de 1 o mas posiciones angulares y distancias
O3. Debe validarse que dada la ubicación del receptor las fuentes queden dentro de la sala.cha
NO1. Modificación de politicas actuales

## Como funciona Hoy -> Como debiese funcionar
HOY                                                     Despues
solo existen politicas aleatorias con restricciones     Se incorpora nueva politica determinista, esta debe recibir una lista de azimuths, elevaciones y distancias, donde puede ser posicionada la fuente