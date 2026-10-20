---
name: container-hw-acceleration
description: Aktiveras när användaren vill optimera Frigate eller andra containrar för hårdvaruacceleration (Intel iGPU, Nvidia, Google Coral).
---

# Hårdvaruacceleration för Containrar
När du konfigurerar hårdvaruacceleration i docker-compose.yml för Frigate:
1. **För Google Coral (USB):** Se till att `devices` inkluderar `- /dev/bus/usb:/dev/bus/usb`.
2. **För Intel QuickSync (iGPU):** Se till att `devices` inkluderar `- /dev/dri/renderD128:/dev/dri/renderD128` och att rätt miljövariabler för drivrutiner är satta.
3. **För Nvidia GPU:** Lägg till hela `deploy.resources.reservations.devices` blocket med `capabilities: [gpu]`.
4. Påminn alltid användaren om att kontrollera att drivrutinerna är laddade på *värddatorn* (hosten) först.
