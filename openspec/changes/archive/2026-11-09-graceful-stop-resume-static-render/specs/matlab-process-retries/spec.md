# Spec Delta

## ADDED Requirements

### Requirement: Parada cooperativa del render estático
El orquestador SHALL interpretar `Ctrl+C` durante `render-static` como solicitud de parada controlada: no despachar nuevas invocaciones y permitir terminar las ya iniciadas. SHALL evaluar y persistir todos los resultados observados antes de salir, manteniendo exclusión de escritor durante el drenaje. La solicitud SHALL NOT matar procesos de render ni acreditar su terminación.

#### Scenario: Canónicas paralelas activas
- **WHEN** se solicita parar con dos canónicas en ejecución y otras pendientes
- **THEN** las dos terminan y se resuelven sin despachar una tercera, aun si sus resultados llegan fuera de orden

#### Scenario: Render secuencial y variantes adicionales
- **WHEN** se solicita parar durante una invocación secuencial o una variante no canónica
- **THEN** se persiste ese resultado y no se inicia la siguiente variante ni se envía Clarity

#### Scenario: Solicitudes repetidas
- **WHEN** el operador vuelve a pulsar Ctrl+C durante el drenaje
- **THEN** la solicitud sigue siendo idempotente y no interrumpe publicaciones ni se convierte en cancelación inmediata

#### Scenario: Señal de consola y backend
- **WHEN** el padre recibe Ctrl+C en Windows o POSIX mientras ejecuta RAVEN o Pyroomacoustics
- **THEN** la parada no se propaga incidentalmente al render activo y el padre observa y conserva su resultado real

### Requirement: Continuaciones diferidas tras parada
Una solicitud de parada SHALL impedir lanzar reintentos de proceso y nuevas realizaciones T30 en esa llamada. El orquestador SHALL conservar la continuación autorizada para el próximo relanzamiento, con presupuesto, espera, semilla e historial existentes. SHALL NOT registrar los trabajos no iniciados como invocaciones ni abrir otro presupuesto por haber parado.

#### Scenario: Fallo reintentable durante drenaje
- **WHEN** un proceso drenado falla y su política permite otro intento
- **THEN** queda programado para la siguiente llamada, sin ejecutar el nuevo UUID durante la parada ni omitir su espera pendiente

#### Scenario: Rechazo acústico durante drenaje
- **WHEN** una canónica drenada recibe rechazo T30 con remuestreo aún permitido
- **THEN** se conserva su rechazo y la continuación acústica para el próximo comando, sin aceptarla ni remuestrear durante el drenaje

#### Scenario: Parada durante planificación de continuaciones T30
- **WHEN** se solicita parar mientras el relanzamiento calcula en memoria la continuación T30 de un participante
- **THEN** el cálculo ya iniciado puede terminar, pero se comprueba la solicitud antes de muestrear cada participante siguiente y antes de publicar; no se inicia otro muestreo ni se modifican archivos protegidos, y el próximo relanzamiento conserva las intenciones pendientes

#### Scenario: Presupuesto o circuito agotado
- **WHEN** el resultado drenado agota la cadena o abre el circuito
- **THEN** el relanzamiento mantiene ese bloqueo y la parada no renueva límites ni reinicia rachas
