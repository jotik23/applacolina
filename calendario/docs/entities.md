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
| `valid_from` / `valid_until` | Fecha | Sí / No | Ventana de vigencia. |
| `is_active` | Booleano | No | Activa la posición para el motor de asignación. |

**Impacto:** una posición activa y vigente aparece en la generación del calendario. El turno se infiere automáticamente según la categoría seleccionada (día, noche o mixto), por lo que no se edita manualmente.

---

## UserProfile (Colaborador) — campos relevantes

Aunque el modelo `UserProfile` vive en la app `users`, el calendario depende de algunos atributos:

| Campo | Tipo | Obligatorio | Descripción |
| --- | --- | --- | --- |
| `cedula`, `nombres`, `apellidos`, `telefono`, `email` | Texto | Sí / No | Información base del colaborador. |
| `roles` | Relación múltiple | No | Se usan para filtrar en UI y validar reglas. |
| `suggested_positions` | Relación (múltiple) | No | Posiciones recomendadas para priorizar asignaciones y filtros en la UI. |

**Impacto:** las posiciones sugeridas se utilizan para construir la lista de colaboradores disponibles y como referencia al momento de asignar turnos manuales o automáticos.

---

## PositionCategory (Categoría de posición)

Agrupa posiciones según su naturaleza operativa y concentra los parámetros base de descanso y sobrecarga.

| Campo | Tipo | Descripción |
| --- | --- | --- |
| `code` | Texto | Identificador interno (ej. `GALPONERO_PRODUCCION_DIA`). |
| `shift_type` | Enumeración | Turno predominante (`day`, `night`, `mixed`). |
| `rest_max_consecutive_days` | Entero | Máximo de días consecutivos de trabajo antes de aplicar bloqueos. |
| `rest_post_shift_days` | Entero | Descanso obligatorio luego de turnos especiales (ej. nocturnos). |
| `rest_monthly_days` | Entero | Días de descanso esperados en el mes. |
| `is_active` | Booleano | Permite ocultar categorías en desuso. |

**Impacto:** todas las posiciones referencian una categoría; la UI usa el `code` para derivar nombres visibles y aplicar límites básicos de descanso.

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
            <span class="inline-flex items-center rounded-full bg-amber-100 px-2.5 py-1 text-xs font-medium text-amber-700">Cobertura pendiente de autorización</span>
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
2. Cargar capacidades y preferencias de los operarios según categoría y granjas.
3. Registrar reglas de descanso por rol (diurno/nocturno) y límites de sobrecarga.
4. Generar un calendario desde `/api/calendars/generate/` (invocado por la UI) para producir un borrador editable.
5. Revisar alertas (emergencias, sobrecargas, huecos) y ajustar manualmente; toda modificación queda auditada.
6. Utilizar los formularios rápidos en la vista de detalle para reasignar o cubrir huecos. El sistema valida la autorización por categoría y la disponibilidad diaria; las reglas de descanso/sobrecarga deben revisarse antes de aprobar.
7. Aprobar la versión final para bloquear solapamientos y registrar la trazabilidad del periodo.

## Fixtures de ejemplo

El archivo `calendario/fixtures/initial_calendario.json` ofrece un dataset mínimo (granjas, operarios, posiciones y un calendario de muestra) para explorar la interfaz. Todas las cuentas usan la contraseña `calendario123` y deben emplearse únicamente en entornos de desarrollo.

```bash
python manage.py loaddata calendario/fixtures/initial_calendario.json
```

Este documento puede extenderse con instrucciones específicas de cada granja a medida que se afinen los flujos operativos.
