# Contraste MVP vs especificacion final

Documento base contrastado: `/Users/user/Documents/projects/ai/ia_device/especificacion-proyecto-dispositivo-conversacional.md`.

## Cobertura buena para validar bases

- Arquitectura cliente ligero + backend remoto: `SI`.
- Transporte WebSocket bidireccional: `SI`.
- Estados principales (`idle`, `listening`, `processing`, `speaking`, `error`): `SI`.
- Semantica del boton (tap, double, long, interrupt): `SI`.
- Seleccion de agente por `agent.select`: `SI`.
- Respuesta textual en streaming (`assistant.text.partial/final`): `SI`.
- Simulacion de pantalla y LED para validar UX: `SI`.
- Logging base de protocolo y latencia de turno: `SI`.
- Auth basica de dispositivo (token + allowlist opcional): `SI`.

## Cobertura parcial (correcta para esta fase)

- Audio de entrada real en chunks: `PARCIAL`.
  - Existe `audio.chunk`, pero en este MVP se usa `debug.user_text` como entrada principal.
- Audio de salida real streaming: `PARCIAL`.
  - Existe `assistant.audio.*` fake opcional, no reproduccion real de audio.
- Integracion OpenClawd real: `PARCIAL`.
  - Adaptador listo, modo mock por defecto.

## Fuera de alcance actual (esperado)

- GPIO/boton/pantalla/LED reales del Whisplay HAT.
- Captura microfono real y playback real en hardware final.
- STT/TTS reales de produccion.
- Reconexion/watchdog endurecidos como servicio embebido.

## Conclusion

Este MVP **si sirve** para validar las bases del proyecto final: contrato de mensajes, maquina de estados, UX de boton, orquestacion por agente y ciclo conversacional completo. No sustituye las fases de audio/hardware real, pero reduce riesgo tecnico antes de integrar la Pi.
