
Nueva app/modulo: Calendario
Contexto: Nuestra empresa tiene multiples granjas de gallinas ponedoras. Los colaboradores (galponeros, clasificadores, lideres y oficios varios) deben ser asignados a cada galpón/granja para el desempeño de las funciones operativas opciones y/o indispensables según los criterios de asignación definidos. 
Objetivos:
- Funcionalidades panel de administración django:
- - Permitir configurar desde el panel de administración django los criterios necesarios para la asignación de turnos. 
- Funcionalidades públicos usuarios regulares:
- -Ofrecer una funcionalidad que permita la generación del calendario para un periodo de tiempo definido (fecha inicio, fecha fin) considerando todos los criterios de asignación. 
- - Permitir que el calendario generado sea inicialmente un borrador (calendario sugerido, no necesariamente debe ser almacenado, pueste ser generado en memoría hasta que sea confirmado/editado) para que el usuario pueda editarlo según eventualidades/emergencias para así generar una versión formal. Esta versión formal debe ser almacenada. 
- - Se debe considerar las versiones almacenadas dentro del contexto de la generación del calendario, puesto que el historico de turnos aplica en los criterios de generación. 
- - La interfáz gráfica debe ser simple pero estética, enfocada en funcionalidad misma.


Datos:
- Colaboradores: Basado en el modelo de datos de la app users. UserProfile represente un colaborador. Estos colaboradores tiene un rol asignado (por ejemplo galponero), lo cual representa el cargo a alto nivel pero no indica puntualmente a que granja o lote de aves está asignado. 
- Debe ser posible configurar dese el panel administrativo django las diferentes posiciones relevantes en el calendario. Estas varían según el número de lotes activos, las tareas complementarias realizadas en la granja y/o las necesidades de contratación de personal. Significa que las posiciones disponibles tienen un periodo de vigencia. 

Para que esto suceda de forma realista, debe ser posible definir/configurar de forma simple pero flexible todos las posiciones disponibles, el periodo de tiempo vigente de cada uno de ellos (cambian en el tiempo), los operarios habilitados tipificados por rol y posiciones que pueden desempeñar, el grado de complejidad del rol, así como la marca de los operarios rankeados para manejar ese nivel de complejidad, los turnos de descanso semanal/quincenal permitidos por rol, los días de la semana preferibles para asignación de descansos, los días adicionales de sobrecargo/continuidad en el rol para poder lograr el match de turnos/descansos más viable y justo para todos (cuando alguien tiene que trabajar días adicionales en promedio de equidad). 

Ejemplo: 
Este es un ejemplo de calendario para su análisis y desglose. En la primera columna se encuentran los diferentes roles/posiciones (previamente definidos/habilitados por periodos de tiempo). Para cada uno de los roles disponibles se asigna un operario que los cubra según los criterios de asignación tanto del rol como del operario.

DESCRIPCIÓN         - DIAS: mart. 14	mier. 15	jue 16	vier. 17	sab. 18	domin. 19	lun. 20	mart. 21	mier. 22
Líder Colina 1      - Ledis B	Jose F	Jose F	Jose F	Jose F	Jose F	Jose F	Jose F	Jose F
Colina 1: G1        - Alex F	Alex F	Alex F	Alex F	Cesar O	Dario V	Alex F	Alex F	Alex F
Colina 1: G2/G3     - Leiner G	Leiner G	Leiner G	Leiner G	Leiner G	Leiner G	Dario V	Leiner G	Leiner G
Colina 1: G4        - Leiner G	Leiner G	Leiner G	Leiner G	Leiner G	Leiner G	Dario V	Leiner G	
Colina 1: G5        - Carlos Nuevo	Cesar O	Cesar O	Cesar O	Teofilo vidal	Cesar O	Cesar O	Cesar O	Cesar O
Colina 1: Prueba    - Jamer Sanabria	Carlos Nuevo		Teofilo vidal			Felix Ortiz 	Felix Ortiz 	Felix Ortiz 
Colina 1: Oficios varios    - 	Orlando	Orlando			Orlando	Orlando	Orlando	Orlando
Colina 1: Oficios varios    - 
Colina 1: Noche     - Willian A	Willian A	Willian A	Willian A	Willian A	Willian A	Willian A	Dario V	Dario V

Descansos           - Cesar O			Omar P	Alex F	Alex F	Leiner G	Eriberto	Eriberto
Descansos           - Jose Findlay				Orlando	Esteban A	Yina	Willian A	Willian A
Descansos           - Willian A						Jonathan	Jonathan	
Descansos           - Jonathan								Jose I

Líder Colina 2      - Eriberto	Eriberto	Eriberto	Eriberto	Eriberto	Eriberto	Eriberto	Wilder S	Wilder S
Colina 2: G1        - Esteban A	Esteban A	Esteban A	Esteban A	Esteban A	Jose I	Esteban A	Esteban A	Esteban A
Colina 2: G1        - Omar P	Omar P	Omar P	Dario V	Dario V	Omar P	Omar P	Omar P	Omar P
Colina 2: G2 Día    - Jose I	Jose I		Yina	Yina	Yina	Adiela	Adiela	Adiela
Colina 2: G2 Día    - Yina	Yina	Yina	Jose I	Jose I	Adiela	camilo	Yina	Yina
Colina 2: Prueba    - 		Jose I	Jairo Nuevo					Orlis Manuel
Colina 2: Prueba    - 
Colina 2: G2 Noche  - Wilder S	Wilder S	Wilder S	camilo	Wilder S	Wilder S	Wilder S	Wilder S	Wilder S
Colina 2: G2 Noche  - Adiela	Adiela	Adiela	Jesus trabajador	Jairo Nuevo	Jairo Nuevo	Jairo Nuevo	Jairo Nuevo	Jairo Nuevo
Colina 2: Oficios varios    - camilo	camilo	camilo						
Colina 2: Oficios varios    - 
Colina 2: Noche     - Jonathan	Jonathan	Jonathan	Jonathan	Jonathan	Jonathan	Jose I	Jose I	Jonathan

Clasificador Dia : C1   - Rafael M	Rafael M	Rafael M	Rafael M		Dina J	Dina J	Dina J	Dina J
Clasificador Dia : C1   - Rosa M	Rosa M	Rosa M	Orlando		Omar Clasif	Omar Clasif	Omar Clasif	Omar Clasif
Clasificador Noche: C2  - Dina J	Dina J	Dina J		Rafael M	Rafael M	Rafael M	Rafael M	Rafael M
Clasificador Noche: C2  - Omar Clasif	Omar Clasif	Omar Clasif		Rosa M	Rosa M	Rosa M	Rosa M	Rosa M

Explicación adicional Ejemplo Calendario:
- C1 / C2 es la abreviación de las granjas Colina 1 / La Colina 1, y Colina 2 / La Colina 2. 
- G1 / G2 / G3 / G4 / G5 / Gn representan los galpones / lotes asignados para cada operario. Est es solo contexto dado que en el modelo está implicito en la definició / configuración de la posición. 
- Los Descansos no son una posición, indican los días que los operarios descansan según los criterios de asignación. 


Criterios de asignación:
- Posición:
* Las posiciones requieren de un tipo, a continuación se listan los tipos habilitados hasta el momento: GALPONERO_PRODUCCIÓN_DÍA, GALPONERO_LEVANTE_DÍA, GALPONERO_PRODUCCIÓN_NOCHE, GALPONERO_LEVANTE_NOCHE, CLASIFICADOR_DIA, CLASIFICADOR_NOCHE, LIDER_GRANJA, SUPERVISOR, LÍDER_TECNICO, OFICIOS_VARIOS. 
* Cada posición requiere un set de habilidades especificas. Estas habilidades normalmente no son fijas sino un umbral continuo. Debe ser posible configurar este unbral para hacer match con las del operario. Las habilidades son listadas a continuación:
* *: Responsabilidad / Criticidad asistencia: Entre más crítico es la asistencia, más necesario es tener un operario responsable que lo cubra. 
* *: Experiencia: Entre más nivel de experiencia se necesita, más importante es tener un operario con calificación de experiencia alta.
* *: Cada rol tiene secuencia de días laborales continuos y días de descanso. Por ejemplo: Los clasificadores descansan todos los sabados, los lideres descansan 2 días cada 2 semanas, etc. 
* * Debe ser posible indicar si esta posición es estricta en los requimientos (lo que significa que solo puede asignar un operario que haga match con lo solictad o queda en blanco), o si es flexible y permite asignarle un operario menos capacitado como última alternativa.

- Operario:
* Cada operario tiene un set de habilidades especifico por tipo de rol. Estas habilidades normalmente no son fijas sino un umbral continuo. Por lo tanto debe ser posible indicarlas en la configuración del operario por cada tipo de rol que el operario puede realizar. Las habilidades son listadas a continuación:  
* * Responsabilidad: Calificación del nivel de responsabilidad que se le asigna al operario. 
* * Experiencia: Calificación del grado de experiencia / habilidad que se le asigna al operario por tipo de posición disponible. Significa que hay operarios que pueden ejercer en multiples posiciones, pero no en el mismo grado. 
* * Hay colaboradores con excepciones en los días de descanso, implica que pueden tener preferencias sobre que días de la semana descansan y si esto es semanal, quincenal o mensual. Debe ser posible configurarlo. Así como indicar si estas preferencias son opcionales u obligatorias.
* * Los operarios normalmente ingresan en un periodo de prueba. Debe ser posible indicar si ya pasó el periodo de prueba o no. (Se asume que un operario en periodo de prueba tiene menos habilidades y necesita una asignación compartida para su formación).
* Hay preferencias de galponeros para cada granja, puede ser por cercanía o necesidades adicionales. Debe ser posible asignar al operario a 1 granja en particular, a varias, y e alguna manera indicar el nivel de preferencia.  

- Generales:
* En los turnos de día: Se espera que por defecto cada colaborador trabaje 6 días a la semana, con un día de descanso. Cuando sea necesario extender los días continuos de trabajo dado que la posición exije un operario y no hay más opciones disponibles, estos días adicionales (más de 6) deben sumar puntos en un esquema de remuneración por puntos que se definirá en otro módulo. Los días continuos de trabajo sin descanso deben ser mínimo 5 y máximo 8. 
* En los turnos de noche: Se espera que por defecto cada colaborador trabaje 8 turnos seguidos con un turno de posturno (dado que si trabaja toda la noche no puede laborar al día siguiente), y un turno de descanso. Mínimo 6, Máximo 9. Preferible 8.
* Si un operario está laborando en turnos de noche, la siguiente rotación debe ser en turnos de día (para compensar el desgaste nocturno y permitir su recuperación). Es decir, los turnos de noche tienen rotaciones así: 8 turnos noche, 1 posturno, 1 descanso, 6 turnos día, 1 descanso, 8 turnos noche nuevamente, etc.
* Idealmente se deben asignar 5 descansos por mes, 1 semanal, y 1 descanso adicional mensual. . Se debe permitir configurar una bandera en la UI para indicar si se quiere que sea estricto en los descansos consecutivos (solo 4 por mes) o si se desea ser flexible (en el periodo de tiempo a generar e incluir el descanso mensual). La flexibilidad puede ser necesaria para lograr las rotaciones y llenar todos los turnos. En caso de asignar dos días de descanso, deben ser consecutivos.    
* Se deben considerar los días adicionales de sobrecargo/continuidad en el rol para poder lograr el match de turnos/descansos más viable y justo para todos (cuando alguien tiene que trabajar días adicionales en promedio de equidad). 
* Se debe intentar rotar los galponeros entre galpones de la misma granja en periodos de 2 semanas (buscando un equilibrio entre continuidad en el galpón y rotación (descanso, aprendizaje, complemento)).

Todas las opciones de configuración, ya sea de operarios, posiciones, habilidades, etc deben ser realizadas através del panel administrativo django. 

Solo se debe proveer la funcionalidad de generar la programación de turnos (borrador) con los filtros anteriormente descritos, editar dicha programación, permitir guardar la versión final aplicable para el periodo de tiempo definido, y permitir navegar entre las programaciones ya definidas. No debe ser posible guardar programaciones solapadas en el tiempo; puesto que finalmente se espera que este módulo pueda representar la realidad de la asignación de turnos por operarios en el presente, futuro y el pasado.  

Notas adicionales (sesión de validación 2024-XX-XX):
- Horizonte estándar de planificación: se trabaja principalmente con periodos semanales, con opción de planificar hasta dos semanas manteniendo continuidad de rotaciones.
- Estados del calendario: `Borrador` → `Aprobado`; cualquier ajuste posterior crea un estado `Modificado` que debe validarse nuevamente antes de reemplazar la versión aprobada vigente.
- Gestión de versiones: no se permiten solapamientos con calendarios aprobados; si surgen cambios, se reconfiguran únicamente las fechas futuras del calendario activo.
- Complejidad operario/posición: etiquetas `Básico`, `Intermedio`, `Avanzado`. Operarios con nivel superior pueden cubrir posiciones de menor complejidad. En emergencias, niveles inferiores pueden cubrir posiciones de mayor exigencia y deben resaltarse (ej. colores diferenciados) para seguimiento y alertas futuras.
- Reglas de descanso: mínimo un día libre cada seis días trabajados. Turnos nocturnos incluyen posturno obligatorio antes del descanso. Preferencias de descanso configurables por rol (lista fija por rol; algunos roles sin preferencia).
- Rotación y fairness: se evalúa el histórico mensual para equilibrar asignaciones y descansos, asegurando rotación entre galpones de una misma granja.
- Sobrecargos: máximo tres días adicionales consecutivos. Cada sobrecargo debe marcarse en el calendario y registrar anotación para bono compensatorio y seguimiento por supervisión.
- Flujo de aprobación: el responsable con permiso de gestión puede generar, aprobar y modificar calendarios; no se requiere workflow adicional de múltiples aprobadores.

Implementación inicial (módulo calendario):
- App `calendario` registrada con modelos para posiciones, capacidades operativas, reglas de descanso y versionamiento de calendarios (`ShiftCalendar`, `ShiftAssignment`, `AssignmentChangeLog`).
- Servicios de generación (`CalendarScheduler`) que contemplan complejidad, descansos mensuales/semanales, posturnos nocturnos y sobrecargas con alertas diferenciadas.
- API básica (`/api/calendars/…`) para listar, generar borradores y aprobar calendarios; integra el motor de asignación y devuelve huecos críticos.
- Panel administrativo con formularios avanzados e inlines para gestionar posiciones, preferencias, capacidades y seguimiento de cambios/alertas.
- Señales que registran auditoría automática (creación, actualización, eliminación) conservando trazabilidad incluso sobre modificaciones manuales.
- Documentación de entidades y flujo de datos disponible en `calendario/docs/entities.md` para apoyar el diseño de formularios HTML/Tailwind.
- Interfaz de usuarios (HTML + Tailwind) con panel de generación/listado (`/calendario/`) y vista de detalle con tabla dinámica, alertas y acción de aprobación.
- Edición rápida en la vista de detalle: formularios embebidos permiten reasignar turnos o cubrir huecos. La validación comprueba complejidad y disponibilidad diaria, pero no recalcula automáticamente reglas de descanso ni sobrecargas (el supervisor debe confirmar manualmente estas excepciones).
- Fixtures de prueba (`calendario/fixtures/initial_calendario.json`) con roles, operarios, posiciones y un calendario de ejemplo. Las credenciales preconfiguradas usan la contraseña `calendario123` y sirven únicamente para entornos de desarrollo.
