---
name: frigate-troubleshoot
description: Används när användaren säger att Frigate har kraschat, laggar eller visar felmeddelanden.
---

# Felsökningsguide för Frigate
När ett problem uppstår, utför dessa steg systematiskt:
1. Be användaren köra `docker logs frigate --tail 100` (eller gör det själv om du har tillgång till Bash-verktyget) för att leta efter specifika Python-fel eller ffmpeg-krascher.
2. Om felet är "Green screen" eller "ffmpeg krasch", kontrollera om kamerans ström har ändrat codec (Frigate kräver vanligtvis H.264).
3. Kontrollera lagringsutrymmet och skrivhastigheten där `/media/frigate` är monterat. Om disken är full, hjälp användaren att justera `retain`-inställningarna i config.yml för att spara färre dagar av inspelningar.
