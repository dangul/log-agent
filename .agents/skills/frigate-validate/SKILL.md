---
name: frigate-validate
description: Används när användaren ber dig lägga till, ta bort eller ändra en kamera eller zon i Frigate-konfigurationen.
---

# Validering av Frigate-konfiguration
När du redigerar config.yml för Frigate, följ alltid dessa steg:
1. Kontrollera att det valda RTMP/RTSP-strömformatet matchar kamerans specifikationer.
2. Säkerställ att `detect`-upplösningen och FPS är optimerade (rekommenderat: 1280x720 @ 5fps för detektering).
3. **Viktigt:** Kör alltid YAML-syntaxvalidering lokalt.
4. Om användaren har tillgång till terminalen och Frigate körs, rekommendera att köra Frigates interna config-check-kommando via Docker innan containern startas om:
   `docker exec -it frigate python3 -m frigate.config`
