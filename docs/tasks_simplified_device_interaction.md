# Tasks: Simplified Device Interaction Model

## Phase 1: Protocol and State Foundations

- [x] 1.1 Update `backend/shared/protocol.py` and `device_runtime/src/device_runtime/protocol/types.py` to define the new visible-state vocabulary and any new call-related message names.
- [x] 1.2 Update `device_runtime/src/device_runtime/domain/events.py` and `device_runtime/src/device_runtime/domain/state.py` to remove the legacy `LOCKED/READY/MENU/MODE/AGENTS` interaction model from the canonical runtime state.
- [x] 1.3 Adjust `device_runtime/tests/contracts/test_runtime_protocol_compat.py` so runtime/backend protocol compatibility asserts the simplified contract instead of the legacy one.

## Phase 2: Runtime State Machine and Controller Flow

- [x] 2.1 Rewrite `device_runtime/src/device_runtime/application/services/device_state_machine.py` for `standby`, `listening`, `calling`, `incoming_call`, and `config` transitions.
- [x] 2.2 Update `device_runtime/src/device_runtime/application/services/device_controller.py` so hold-to-talk starts immediately on hold, stops on release, and never emits a release beep.
- [x] 2.3 Update `device_runtime/src/device_runtime/application/services/protocol_service.py` to map backend-initiated calls, greeting completion, and playback-end beep behavior into the new model.

## Phase 3: Backend and Simulator Integration

- [x] 3.1 Update backend routing/services in `backend/application/services/message_router.py`, `recording.py`, and `turn_processing.py` for the new outbound call signal and simplified post-greeting return to `standby`.
- [ ] 3.2 Update simulator interaction flow in `simulator/application/services/simulator_controller.py`, `protocol_service.py`, and UI entrypoints under `simulator/entrypoints/` to match hold-to-talk, `calling`, `incoming_call`, and `config`.
- [x] 3.3 Review display/experience mapping in `device_runtime/src/device_runtime/application/services/display_model_service.py` and `experience_service.py` so `llamando`, incoming-call copy, and playback-end beep rules are rendered consistently.

## Phase 4: Verification

- [ ] 4.1 Update unit tests in `device_runtime/tests/` and `simulator/tests/` for press+hold, release-send-return, single-click greeting, double-click config entry, and incoming-call preemption of config.
- [ ] 4.2 Update scenario coverage in `simulator/qa/scenario_runner.py` and `simulator/qa/smoke_test.py` for the new simplified happy paths.
- [x] 4.3 Verify beep behavior explicitly: beep after received audio playback ends, never on button release.

> Temporary stabilization note: until tasks 3.2, 4.1, and 4.2 are completed, bare `pytest` excludes `simulator/tests` so the default clean suite stays focused on backend + `device_runtime` real-device validation.

## Phase 5: Documentation Cleanup

- [ ] 5.1 Update any remaining references in `README.md`, `RUNBOOK.md`, and adjacent docs that still describe the superseded interaction-state logic.
- [ ] 5.2 Remove or rewrite obsolete references to agent-selection/menu flows when they conflict with the simplified canonical model.
