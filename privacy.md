# Política de privacidad

Última actualización: 10 de junio de 2026.

Esta política describe el tratamiento de datos asociado al GPT **Biwenger World Cup 2026 Optimizer** y a la API configurada por cada instalación.

## Responsable y contacto

El servicio es un proyecto independiente y no está afiliado con Biwenger, Diario AS, OpenAI ni la FIFA.

Para consultas de privacidad o solicitudes relacionadas con datos, utiliza el canal de contacto publicado por la instalación que presta el servicio.

No publiques claves API, credenciales ni otros datos sensibles en un Issue.

## Datos que procesa el GPT público

El perfil público utiliza una clave compartida de solo lectura y rutas sin `user_id`. La API procesa únicamente los parámetros necesarios para responder a cada consulta, por ejemplo:

- Filtros de jugadores, selecciones, posiciones, estados o partidos.
- IDs de jugadores usados en comparaciones y recomendaciones.
- Presupuesto, formación, fase, tamaño de plantilla y preferencias de optimización enviados en la petición.

Estas operaciones públicas son de cálculo o consulta y no guardan una plantilla, ratings personalizados ni configuración individual del usuario.

## Perfil privado de administración

El perfil admin es independiente del GPT público y requiere una clave distinta. Puede guardar en SQLite un `user_id` elegido por el administrador junto con:

- Presupuesto, formación y tamaño de plantilla.
- Ratings manuales de jugadores.
- Plantilla local y acciones simuladas.

El `user_id` separa espacios de trabajo, pero no es una identidad verificada ni debe contener nombre completo, correo, teléfono u otros datos personales.

## Datos que no se solicitan

El servicio no solicita ni necesita:

- Usuario o contraseña de Biwenger.
- Cookies o tokens de sesión de Biwenger.
- Datos bancarios o de pago.
- Acceso a la plantilla, saldo, mercado o transacciones reales de una cuenta Biwenger.

No envíes estos datos al GPT ni a la API.

## Registros técnicos

La infraestructura puede generar registros técnicos necesarios para seguridad, diagnóstico y disponibilidad, como fecha y hora, ruta solicitada, código HTTP, dirección IP y agente de usuario. Estos registros no se utilizan para publicidad ni para crear perfiles comerciales y se conservan solo durante el tiempo operativo razonablemente necesario.

Las claves API se usan para autorizar peticiones. No deben incluirse en mensajes, repositorios públicos ni informes de errores.

## Fuentes y terceros

El servicio interviene junto con los siguientes terceros:

- **OpenAI/ChatGPT**, que procesa la conversación conforme a sus propias condiciones y política de privacidad.
- **El proveedor HTTPS configurado por cada instalación**, utilizado para publicar y proteger el endpoint.
- **Biwenger**, cuya API pública se consulta para obtener catálogo, precios fantasy explícitos, partidos y clasificaciones disponibles.
- **GitHub**, que aloja el código, esta política y el canal público de Issues.

La información deportiva puede ser parcial, provisional o cambiar. El servicio no combina estos datos con credenciales de cuentas privadas.

## Finalidad y base del tratamiento

Los datos de cada petición se procesan para prestar la funcionalidad solicitada, proteger el servicio, prevenir abusos y diagnosticar errores. El uso del GPT y de la API es voluntario.

## Conservación y eliminación

- Las consultas públicas no crean un perfil persistente en la base de datos de la aplicación.
- Los registros técnicos se rotan o eliminan según las necesidades de operación y seguridad.
- Los datos del perfil admin permanecen hasta que el administrador los modifique o elimine.

Las solicitudes de acceso o eliminación deben identificar únicamente la información necesaria para localizar los datos. Nunca envíes una clave API en una solicitud pública.

## Seguridad

Se aplican separación de claves admin/solo lectura, HTTPS, secretos fuera del repositorio y confirmación explícita para acciones locales. Ningún sistema es completamente infalible; evita introducir información personal o sensible que no sea necesaria.

## Menores

El servicio no está diseñado para recopilar deliberadamente datos personales de menores. Si eres menor, utiliza el servicio con supervisión de una persona adulta responsable y no compartas datos personales.

## Cambios en esta política

Esta política puede actualizarse cuando cambien las funciones o proveedores. La fecha de la parte superior indicará la última revisión.
