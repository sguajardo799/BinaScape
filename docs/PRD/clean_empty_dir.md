# PRD - limpieza y orden de directorios en salida
+ Estado: Borrador
+ Creado: 22-09-2026
+ Alcance: Limpieza de directorios de salida vacias y reorganización en una carpeta de indexes y manifests

## Resumen
Hoy: Al ejecutar el pipeline siempre se generan los directorios audio/prepared, pero rara vez se emplean. A la vez que los indexes y manifests empleados internamente y para debug quedan expuestos directamente a un usuario no técnico.
Despúes: El pipeline no genera directorios con archivos vacios, y los directorios de indexes y manifests estan unidos en un directorio que los agrupa

## Historia
Actualmente un usuario del pipeline ve en sus salidas multiples carpetas vacias, esto genera confusion y no le es intuitiva la busqueda de los resultados en el directorio. Es por esto que las personas menos tecnicas no estan empleando de forma correcta el pipeline.

## Objetivos
01. Obtener un directorio de salida mas intuitivo
02. No tener subdirectorios vacios que el usuario deba revisar

## Como funcionara
Desde el punto de vista de los directorios
Hoy -> output_dir                   Despues ->  output_dir
        |-audio                                  |-cropped_audio(solo si se necesita)
        |-indexes                                |-metadata(o nombre explicito que definas)
        |-manifests                              --output_audio
        --outputs