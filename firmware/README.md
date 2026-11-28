# Firmware (Phase 3 — not started)

STM32U585 side of the Arduino UNO Q (Arduino C++, App Lab sketch).
Decided: 3S pack, 12 V fan on the pack via a logic-level MOSFET, 5-minute
stored interval (sample fast, send the 5-min average), night shutdown.
Still open: CN3791 output check, 5 V buck choice, power-switch circuit for the
DS3231 wake alarm, final pin map (docs/pin-mapping.md).

Message contract already fixed and tested on the Linux side:
see `docs/architecture.md` → "STM32 → Linux message (schema v1)".
