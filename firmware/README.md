# Firmware (Phase 3 — not started)

STM32U585 side of the Arduino UNO Q (Arduino C++, App Lab sketch).
Blocked on hardware decisions: power topology (1S vs 3S after CN3791 output
check), logic-level MOSFET for the fan, final pin map (docs/pin-mapping.md).

Message contract already fixed and tested on the Linux side:
see `docs/architecture.md` → "STM32 → Linux message (schema v1)".
