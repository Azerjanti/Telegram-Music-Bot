---
name: Shazamio on Python 3.13
description: Shazamio imports pydub, which needs the removed audioop module on Python 3.13.
---

Python 3.13 no longer ships `audioop`, while Shazamio/pydub still imports it. Keep `audioop-lts` installed alongside Shazamio.

**Why:** Without the compatibility package, importing Shazamio fails before voice recognition can start.

**How to apply:** Preserve the dependency whenever the voice-recognition feature or Python runtime is upgraded.