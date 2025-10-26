# Documentación de entidades del calendario

Esta guía resume los modelos principales del módulo `calendario`, describe sus campos y provee un ejemplo práctico de cómo deberían diligenciarse desde la interfaz HTML/TailwindCSS.

---

## PositionDefinition (Definición de posición)

Representa un rol operativo disponible en un rango de fechas determinado.

| Campo | Tipo | Obligatorio | Descripción |
| --- | --- | --- | --- |
| `name` | Texto | Sí | Nombre descriptivo visible para los usuarios. |
| `code` | Texto (único) | Sí | Código corto, se utiliza para identificar la posición en tablas/calendarios. |
| `category` | Enumeración | Sí | Tipo de posición (ej. `GALPONERO_PRODUCCION_DIA`). |
| `farm` | Relación | Sí | Granja asociada. |
| `chicken_house` | Relación | No | Galpón específico (si aplica). |
| `rooms` | Relación (múltiple) | No | Salones dentro del galpón (si aplica). |
| `complexity` | Enumeración | Sí | Nivel requerido (`basic`, `intermediate`, `advanced`). |
| `allow_lower_complexity` | Booleano | No | Permite cubrir con operarios de menor complejidad en emergencia. |
| `valid_from` / `valid_until` | Fecha | Sí / No | Ventana de vigencia. |
| `is_active` | Booleano | No | Activa la posición para el motor de asignación. |
| `notes` | Texto largo | No | Observaciones adicionales. |

**Impacto:** una posición activa y vigente aparece en la generación del calendario. Si `allow_lower_complexity` es verdadero, el motor podrá asignar operarios con menor nivel, marcando la alerta correspondiente. El turno se infiere automáticamente según la categoría seleccionada (día, noche o mixto), por lo que no se edita manualmente.

---

## OperatorCapability (Capacidad del operario)

Registra la fortaleza de un operario por categoría de posición.

| Campo | Tipo | Obligatorio | Descripción |
| --- | --- | --- | --- |
| `operator` | Relación | Sí | Usuario (colaborador) habilitado. |
| `category` | Enumeración | Sí | Debe coincidir con la categoría de la posición. |
| `skill_score` | Entero (1-10) | Sí | Nivel de habilidad (1 = en aprendizaje, 10 = experto). |

**Impacto:** el generador de turnos evalúa si el `skill_score` supera el umbral de la posición (`basic` ≥ 1, `intermediate` ≥ 3, `advanced` ≥ 8). Si el puntaje no alcanza el mínimo y la posición no permite coberturas inferiores, se marca un hueco crítico; de lo contrario se asigna con alerta (`warn` a un punto, `critical` a dos o más puntos de diferencia).

---

## UserProfile (Colaborador) — campos relevantes

Aunque el modelo `UserProfile` vive en la app `users`, el calendario depende de algunos atributos:

| Campo | Tipo | Obligatorio | Descripción |
| --- | --- | --- | --- |
| `cedula`, `nombres`, `apellidos`, `telefono`, `email` | Texto | Sí / No | Información base del colaborador. |
| `roles` | Relación múltiple | No | Se usan para filtrar en UI y validar reglas. |
| `preferred_farm` | Relación | No | Granja sugerida al motor de asignación (prioridad blanda). |

**Impacto:** si `preferred_farm` está definido, el generador intentará asignar primero en esa granja siempre que existan capacidades disponibles. Si no hay preferencia, la selección se realiza únicamente por habilidades y reglas de descanso.

---

## RestRule (Regla de descanso) y RestPreference (Preferencia de descanso)

Configuran la política de descansos por rol y turno.

| Campo | Tipo | Descripción |
| --- | --- | --- |
| `role` | Relación | Rol del operario (ej. Galponero). |
| `shift_type` | Enumeración | Diferencia entre turnos diurnos y nocturnos. |
| `min_rest_frequency` | Entero | Días máximos continuos antes de un descanso (por defecto 6). |
| `min_consecutive_days` / `max_consecutive_days` | Entero | Rango aceptable de días consecutivos de trabajo. |
| `post_shift_rest_days` | Entero | Días libres después de turnos nocturnos. |
| `monthly_rest_days` | Entero | Descansos esperados al mes. |
| `enforce_additional_rest` | Booleano | Obliga el descanso adicional mensual. |
| `active_from` / `active_until` | Fecha | Vigencia de la regla. |

Las preferencias asociadas (`RestPreference`) indican días de descanso recomendados u obligatorios.

**Impacto:** el motor bloquea asignaciones que excedan el máximo consecutivo y marca sobrecargas cuando se usan reglas de emergencia.

---

## PositionCategory (Categoría de posición)

Agrupa posiciones según su naturaleza operativa. Cada categoría define su turno natural y los límites base de sobrecarga.

| Campo | Tipo | Descripción |
| --- | --- | --- |
| `code` | Texto | Identificador interno (ej. `GALPONERO_PRODUCCION_DIA`). |
| `name` | Texto | Nombre visible de la categoría. |
| `shift_type` | Enumeración | Turno predominante (`day`, `night`, `mixed`). |
| `default_extra_day_limit` | Entero | Días extra permitidos por defecto (día ≤ 3, noche ≤ 2). |
| `default_overtime_points` | Entero | Puntos que suma cada día extra cuando no hay regla específica. |
| `is_active` | Booleano | Permite ocultar categorías en desuso. |

**Impacto:** todas las posiciones referencian una categoría. El motor usa los límites y puntos por defecto cuando no existe una regla de sobrecarga personalizada.

---

## OverloadAllowance (Regla de sobrecarga)

Especializa la sobrecarga de una categoría, tomando el turno que la propia categoría declara.

| Campo | Tipo | Descripción |
| --- | --- | --- |
| `category` | Relación | Categoría afectada. |
| `extra_day_limit` | Entero | Días extra consecutivos autorizados sobre el máximo de descanso. |
| `overtime_points` | Entero | Puntos que suma cada día extra aplazando el descanso. |
| `alert_level` | Enumeración | Nivel de alerta que se mostrará (`warn`, `critical`). |

**Impacto:** cuando el motor extiende una secuencia de trabajo más allá del descanso permitido, verifica la regla asociada a la categoría. Si hay margen, marca la asignación como sobrecarga (`is_overtime`) y acredita los puntos definidos.

---

## ShiftCalendar (Calendario de turnos)

Encapsula una generación de turnos para un rango de fechas.

| Campo | Tipo | Obligatorio | Descripción |
| --- | --- | --- | --- |
| `name` | Texto | No | Nombre amigable (ej. "Semana 42 - Colina"). |
| `start_date` / `end_date` | Fecha | Sí | Rango (no se permite solapar con aprobados). |
| `status` | Enumeración | Sí | `draft`, `approved`, `modified`. |
| `base_calendar` | Relación | No | Referencia cuando es una versión modificada. |
| `created_by` / `approved_by` | Usuario | No | Auditoría. |
| `approved_at` | DateTime | No | Registro de aprobación. |
| `notes` | Texto | No | Comentarios operativos. |

**Impacto:** una vez aprobado, solo se aceptan modificaciones para fechas futuras y deben volver a aprobarse.

---

## ShiftAssignment (Asignación de turno)

| Campo | Tipo | Descripción |
| --- | --- | --- |
| `calendar` | Relación | Calendario al que pertenece. |
| `position` | Relación | Posición a cubrir. |
| `date` | Fecha | Día específico. |
| `operator` | Relación | Operario asignado. |
| `is_auto_assigned` | Booleano | Marca si proviene del motor. |
| `alert_level` | Enumeración | `none`, `warn`, `critical`. |
| `is_overtime` | Booleano | Indica sobrecarga. |
| `overtime_points` | Entero | Puntos acreditados por la sobrecarga (0 si no aplica). |
| `notes` | Texto | Observaciones (ej. motivo de sobrecarga). |

**Impacto:** cualquier cambio crea un registro en `AssignmentChangeLog` para trazabilidad.

---

## AssignmentChangeLog y WorkloadSnapshot

- `AssignmentChangeLog` registra la historia (creación, actualización, eliminación) con operadores anterior y nuevo.
- `WorkloadSnapshot` guarda métricas mensuales por operario (turnos diurnos, nocturnos, descansos, sobrecargas y puntos acumulados) para análisis de equidad.

---

## Ejemplo práctico (UI HTML/Tailwind)

El siguiente fragmento muestra cómo podría verse un formulario para crear una posición y visualizar las implicaciones de un calendario generado. Se asume que el backend expone endpoints bajo `/api/calendars/…` y que el envío se realiza via `fetch` o formularios tradicionales.

```html
<section class="bg-white shadow rounded-lg p-6 space-y-6">
  <header>
    <h2 class="text-xl font-semibold text-slate-800">Nueva posición operativa</h2>
    <p class="text-sm text-slate-500">Configure la posición para que pueda ser tomada en cuenta durante la generación del calendario.</p>
  </header>

  <form id="position-form" class="grid grid-cols-1 md:grid-cols-2 gap-4">
    <label class="flex flex-col text-sm text-slate-700">
      Nombre
      <input name="name" type="text" required class="mt-1 rounded border-slate-300 focus:border-amber-500 focus:ring-amber-500" placeholder="Galponero producción Colina 1" />
    </label>

    <label class="flex flex-col text-sm text-slate-700">
      Código
      <input name="code" type="text" required class="mt-1 rounded border-slate-300 focus:border-amber-500 focus:ring-amber-500" placeholder="COL1-GAL-D" />
    </label>

    <label class="flex flex-col text-sm text-slate-700">
      Categoría
      <select name="category" class="mt-1 rounded border-slate-300 focus:border-amber-500 focus:ring-amber-500">
        <option value="GALPONERO_PRODUCCION_DIA">Galponero producción día</option>
        <option value="GALPONERO_PRODUCCION_NOCHE">Galponero producción noche</option>
        <!-- agregar el resto de categorías -->
      </select>
    </label>

    <label class="flex flex-col text-sm text-slate-700">
      Complejidad requerida
      <select name="complexity" class="mt-1 rounded border-slate-300 focus:border-amber-500 focus:ring-amber-500">
        <option value="basic">Básico</option>
        <option value="intermediate" selected>Intermedio</option>
        <option value="advanced">Avanzado</option>
      </select>
    </label>

    <label class="flex items-center space-x-3 text-sm text-slate-700 md:col-span-2">
      <input name="allow_lower_complexity" type="checkbox" class="h-4 w-4 text-amber-500 border-slate-300 rounded" />
      <span>Permitir cubrir con complejidad inferior (se resaltará como emergencia)</span>
    </label>

    <div class="md:col-span-2 grid grid-cols-1 md:grid-cols-3 gap-4">
      <label class="flex flex-col text-sm text-slate-700">
        Vigente desde
        <input name="valid_from" type="date" required class="mt-1 rounded border-slate-300 focus:border-amber-500 focus:ring-amber-500" />
      </label>
      <label class="flex flex-col text-sm text-slate-700">
        Vigente hasta
        <input name="valid_until" type="date" class="mt-1 rounded border-slate-300 focus:border-amber-500 focus:ring-amber-500" />
      </label>
      <label class="flex flex-col text-sm text-slate-700">
        Turno
        <select name="shift_type" class="mt-1 rounded border-slate-300 focus:border-amber-500 focus:ring-amber-500">
          <option value="day">Día</option>
          <option value="night">Noche</option>
        </select>
      </label>
    </div>
  </form>
</section>

<!-- Vista resumida de un calendario generado -->
<section class="mt-8">
  <div class="overflow-x-auto rounded-lg border border-slate-200 shadow-sm">
    <table class="min-w-full divide-y divide-slate-200 text-sm">
      <thead class="bg-slate-50">
        <tr>
          <th class="px-4 py-2 text-left font-semibold text-slate-600">Posición</th>
          <th class="px-4 py-2 text-left font-semibold text-slate-600">Operario asignado</th>
          <th class="px-4 py-2 text-left font-semibold text-slate-600">Fecha</th>
          <th class="px-4 py-2 text-left font-semibold text-slate-600">Alerta</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-slate-100 bg-white">
        <tr>
          <td class="px-4 py-2 font-medium text-slate-800">COL1-GAL-D</td>
          <td class="px-4 py-2 text-slate-700">Alex Forero</td>
          <td class="px-4 py-2 text-slate-700">2025-01-02</td>
          <td class="px-4 py-2">
            <span class="inline-flex items-center rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-medium text-emerald-700">Sin alerta</span>
          </td>
        </tr>
        <tr>
          <td class="px-4 py-2 font-medium text-slate-800">COL1-GAL-D</td>
          <td class="px-4 py-2 text-slate-700">Cesar Ortiz</td>
          <td class="px-4 py-2 text-slate-700">2025-01-08</td>
          <td class="px-4 py-2">
            <span class="inline-flex items-center rounded-full bg-amber-100 px-2.5 py-1 text-xs font-medium text-amber-700">Cobertura con menor complejidad</span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
  <p class="mt-3 text-xs text-slate-500">Las alertas amarillas indican asignaciones que requieren seguimiento y justifican anotación para el supervisor.</p>
</section>
```

**Flujo recomendado:**

1. Configurar posiciones activas con sus rangos de vigencia.
2. Cargar capacidades y preferencias de los operarios según complejidad y granjas.
3. Registrar reglas de descanso por rol (diurno/nocturno) y límites de sobrecarga.
4. Generar un calendario desde `/api/calendars/generate/` (invocado por la UI) para producir un borrador editable.
5. Revisar alertas (emergencias, sobrecargas, huecos) y ajustar manualmente; toda modificación queda auditada.
6. Utilizar los formularios rápidos en la vista de detalle para reasignar o cubrir huecos. El sistema verifica complejidad y disponibilidad diaria, pero las reglas de descanso/sobrecarga deben ser revisadas por el supervisor antes de aprobar.
7. Aprobar la versión final para bloquear solapamientos y registrar la trazabilidad del periodo.

## Fixtures de ejemplo

El archivo `calendario/fixtures/initial_calendario.json` ofrece un dataset mínimo (granjas, operarios, posiciones y un calendario de muestra) para explorar la interfaz. Todas las cuentas usan la contraseña `calendario123` y deben emplearse únicamente en entornos de desarrollo.

```bash
python manage.py loaddata calendario/fixtures/initial_calendario.json
```

Este documento puede extenderse con instrucciones específicas de cada granja a medida que se afinen los flujos operativos.
