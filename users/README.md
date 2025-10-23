# Modulo de Usuarios

## Campos Principales
- `cedula`: identificador unico y usado como nombre de usuario.
- `nombres`, `apellidos`: nombres completos del usuario.
- `telefono`: numero principal de contacto (unico).
- `email`, `direccion`: datos opcionales.
- `contacto_nombre`, `contacto_telefono`: contacto de respaldo.
- `roles`: relacion muchos a muchos con `Role`.

## Roles Base
- `GALPONERO`
- `CLASIFICADOR`
- `ADMINISTRADOR`
- `SUPERVISOR`

## Permisos Asociados
| Rol | Permisos |
| --- | --- |
| GALPONERO | `view_users` |
| CLASIFICADOR | `view_users` |
| ADMINISTRADOR | `view_users`, `manage_users`, `view_roles`, `manage_roles` |
| SUPERVISOR | `view_users`, `view_roles` |

## Panel Administrativo
- Creacion y edicion de usuarios con formularios personalizados.
- Acciones masivas: activar, desactivar y restablecer clave aleatoria.
- Roles gestionados con permisos asociados en linea.
- Campos agrupados en secciones: credenciales, informacion personal, contacto y roles.

