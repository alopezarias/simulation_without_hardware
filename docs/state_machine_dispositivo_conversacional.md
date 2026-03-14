# Máquina de Estados del Dispositivo Conversacional

## 1. Propósito del documento

Este documento define la máquina de estados del dispositivo
conversacional del proyecto.

Describe:

-   los estados posibles del sistema
-   los eventos de interacción del usuario
-   las transiciones entre estados
-   las acciones asociadas a cada transición

Este documento forma parte de la **configuración base del proyecto** y
sirve como referencia para:

-   implementación del firmware
-   lógica de control del botón
-   interfaz de usuario
-   comportamiento general del sistema

------------------------------------------------------------------------

# 2. Eventos de entrada

El dispositivo dispone de **un único botón físico**, capaz de generar
tres tipos de eventos.

## 2.1 Press (P)

Pulsación corta.

Se produce cuando:

-   el botón se presiona
-   se libera rápidamente

Uso típico:

-   acción principal
-   navegación en menús

## 2.2 Double Press (D)

Doble pulsación.

Se produce cuando:

-   se realizan dos pulsaciones cortas consecutivas
-   dentro de una ventana de tiempo definida.

Uso típico:

-   cancelar
-   salir de un modo

## 2.3 Long Press (L)

Pulsación larga.

Se produce cuando:

-   el botón permanece presionado durante un tiempo prolongado
-   superior al umbral definido.

Uso típico:

-   confirmaciones
-   cambio de modo
-   acciones estructurales (bloqueo, selección de agente, etc).

------------------------------------------------------------------------

# 3. Estados del sistema

El dispositivo puede encontrarse en los siguientes estados:

  Estado   Descripción
  -------- -------------------------------------
  LOCKED   Dispositivo bloqueado
  READY    Estado principal del dispositivo
  LISTEN   Estado de escucha del usuario
  MENU     Menú principal de configuración
  MODE     Selección de modo de funcionamiento
  AGENTS   Selección de agente conversacional

El estado **READY** actúa como **estado central del sistema**.

------------------------------------------------------------------------

# 4. Descripción detallada de estados

## 4.1 Estado: LOCKED

### Descripción

Estado de seguridad del dispositivo.

En este estado:

-   el dispositivo permanece bloqueado
-   no se aceptan acciones funcionales
-   no se puede iniciar conversación.

### Acciones permitidas

  Evento             Resultado
  ------------------ -------------------------
  Press (P)          No ocurre nada
  Double Press (D)   No ocurre nada
  Long Press (L)     Desbloquear dispositivo

### Transición

LOCKED --(Long Press)--\> READY

------------------------------------------------------------------------

## 4.2 Estado: READY

### Descripción

Estado principal del sistema.

En este estado el dispositivo:

-   está listo para iniciar conversación
-   espera interacción del usuario.

Es el **estado de retorno de la mayoría de las operaciones**.

### Acciones permitidas

  Evento             Resultado
  ------------------ ----------------------
  Press (P)          Iniciar escucha
  Double Press (D)   Abrir menú
  Long Press (L)     Bloquear dispositivo

### Transiciones

READY --(Press)--\> LISTEN\
READY --(Double Press)--\> MENU\
READY --(Long Press)--\> LOCKED

------------------------------------------------------------------------

## 4.3 Estado: LISTEN

### Descripción

Estado en el que el dispositivo **está escuchando al usuario**.

En este estado:

-   el micrófono está activo
-   se captura audio del usuario
-   se espera una finalización manual o cancelación.

Entrar en este estado implica comenzar una interacción conversacional.

### Acciones permitidas

  Evento             Resultado
  ------------------ -------------------
  Press (P)          Finalizar escucha
  Double Press (D)   Cancelar escucha
  Long Press (L)     Cambiar agente

### Transiciones

LISTEN --(Press)--\> READY

Finaliza la captura de audio.

LISTEN --(Double Press)--\> READY

La captura se cancela y el audio se descarta.

LISTEN --(Long Press)--\> AGENTS

Comportamiento:

1.  se detiene la escucha
2.  se abandona el estado LISTEN
3.  se entra en el selector de agentes

Importante:

El cambio de agente **no ocurre dentro del estado LISTEN**.\
La pulsación larga provoca **una transición al estado AGENTS**.

------------------------------------------------------------------------

## 4.4 Estado: MENU

### Descripción

Menú principal del dispositivo.

Desde este estado el usuario puede navegar entre diferentes opciones de
configuración.

### Acciones permitidas

  Evento             Resultado
  ------------------ ------------------
  Press (P)          Siguiente opción
  Double Press (D)   Cancelar menú
  Long Press (L)     Entrar en opción

### Transiciones

MENU --(Press)--\> MENU (siguiente opción)\
MENU --(Double Press)--\> READY\
MENU --(Long Press)--\> MODE

------------------------------------------------------------------------

## 4.5 Estado: MODE

### Descripción

Submenú de selección de modo del dispositivo.

Permite cambiar el modo operativo del sistema.

Ejemplos de modos posibles:

-   conversación
-   asistente
-   otros modos del sistema.

### Acciones permitidas

  Evento             Resultado
  ------------------ ---------------------
  Press (P)          Siguiente modo
  Double Press (D)   Cancelar
  Long Press (L)     Confirmar selección

### Transiciones

MODE --(Press)--\> MODE (siguiente modo)\
MODE --(Double Press)--\> READY\
MODE --(Long Press)--\> READY (modo confirmado)

------------------------------------------------------------------------

## 4.6 Estado: AGENTS

### Descripción

Selector de agentes conversacionales.

Permite elegir el agente activo con el que el dispositivo interactuará.

### Acciones permitidas

  Evento             Resultado
  ------------------ --------------------
  Press (P)          Siguiente agente
  Double Press (D)   Cancelar selección
  Long Press (L)     Confirmar agente

### Transiciones

AGENTS --(Press)--\> AGENTS (siguiente agente)\
AGENTS --(Double Press)--\> READY\
AGENTS --(Long Press)--\> READY (agente confirmado)

### Activación del agente

Cuando se confirma:

1.  el agente seleccionado pasa a ser el agente activo
2.  el sistema retorna al estado READY.

------------------------------------------------------------------------

# 5. Flujo principal del sistema

El flujo típico de uso es:

LOCKED → Long Press → READY → Press → LISTEN → Press → READY

Desde READY también se puede:

READY → MENU\
READY → LOCKED\
READY → LISTEN

------------------------------------------------------------------------

# 6. Principios de diseño de interacción

El sistema sigue una semántica consistente:

  Acción         Significado general
  -------------- ------------------------------
  Press          Acción principal / avanzar
  Double Press   Cancelar / salir
  Long Press     Confirmar / cambiar contexto

Esto permite que el usuario:

-   aprenda el sistema rápidamente
-   tenga comportamientos consistentes entre estados.
