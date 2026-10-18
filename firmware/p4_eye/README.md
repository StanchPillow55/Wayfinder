# P4X-EYE

Optional perception node in [ADR-001](../../docs/adr/0001-mac-orchestrated-usb-topology.md).

The Mac accepts `vision.event` values `user_present`, `scene_changed`, and `scene_summary`. A summary is a short untrusted string. Image, frame, and audio fields are rejected. The Mac does not turn a camera event into a browser action.

Use the USB port marked Debug for flashing and for this channel. The board is not assumed to provide LiDAR or time-of-flight. Local vision firmware is not in this repo yet; the Mac simulator covers the contract.

Set `WAYFINDER_P4_PORT` when that firmware exists. The MLH kit includes one USB-C data cable, so BOX-3 and P4X-EYE cannot be attached at the same time as a USB-C DevKit unless another data cable is already on the laptop.
