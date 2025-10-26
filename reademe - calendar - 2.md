Posiciones vigentes:

- Agregar filas de descansos

Ahora hablemos de las reglas de descanso (restrule), estas deben estar asociadas a las categorías tal como overloadallowance. Considerando que ambas tablas son una relación 1 a 1 con la categoria (positioncategory). Se deben eliminar ambas entidades (restrule y overloadallowance). Moviendo los campos relevantes a la tabla positioncategory, a saber: overloadallowance (Máximo días extra consecutivos, Puntos por día extra, Nivel de alerta (si aplica)) y restrule: ('Frecuencia mínima de descanso, Días de descanso consecutivos mínimos, Días de descanso consecutivos máximos, Descanso posterior al turno y Descanso mensual requerido'). Por tal razón, es necesario actualizar los modelos, paneles admin y UI, así como la lógica asociada de generación de calendarios. No es necesario que las categorías de posiciones se gestionen desde la UI publica, lo que significa que la vista de reglas operativas puede eliminarse así como el boton de configurar reglas.

- 
- Refactorizar, usar librería practica javascript en vez de vanilla. 