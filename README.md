# Offline Player

Ein MP3-Player, der komplett ohne Internet läuft. Importierte Musik und Playlists
werden im Browser-Speicher (IndexedDB) abgelegt und bleiben erhalten.

## Starten

**Am einfachsten (Windows):** `start.cmd` doppelklicken. Es öffnet sich
http://localhost:8765/ im Browser. Das Fenster mit dem Server muss offen bleiben,
solange der Player läuft.

**Ohne Server:** `index.html` direkt doppelklicken. Funktioniert ebenfalls offline,
nur die Installation als App (siehe unten) ist dann nicht möglich.

## Als App installieren

1. Player über `start.cmd` öffnen (Chrome oder Edge).
2. In der Adressleiste auf das Symbol „App installieren“ klicken
   (oder Menü ⋯ → „Apps“ → „Offline Player installieren“).
3. Danach startet der Player wie ein normales Programm aus dem Startmenü.
   Beim ersten Start muss der Server (`start.cmd`) laufen, danach wird die App
   auch ohne Server aus dem Cache geladen.

Auf dem Handy: https://docju474-git.github.io/offline-player/ im Browser öffnen
und „Zum Startbildschirm hinzufügen“ bzw. „App installieren“ wählen. Danach läuft
die App auch ohne Internet.

## Bedienung

- **Importieren:** Button „Importieren“ oder Dateien ins Fenster ziehen.
  MP3, M4A, AAC, OGG, OPUS, WAV, FLAC. Titel/Interpret werden aus ID3-Tags gelesen.
- **Listen:** Tab „Listen“ → „+ Neue Liste“ → „Titel hinzufügen“.
  Über ⋯ an einem Titel: abspielen, verschieben, entfernen, in andere Liste kopieren.
- **Player-Tasten:** Vorheriger Titel · −20 s · −10 s · Play/Pause · +10 s · +20 s · Nächster Titel.
  Darunter Zufall, Wiederholen (alle / ein Titel) und Lautstärke.
- Der Player merkt sich Titel und Position beim Schließen.

## Tastatur

| Taste | Aktion |
|---|---|
| Leertaste | Play / Pause |
| ← / → | 10 s zurück / vor |
| Umschalt + ← / → | 20 s zurück / vor |
| Strg + ← / → | vorheriger / nächster Titel |

Die Sperrbildschirm- und Headset-Tasten (Play, Pause, nächster Titel, ±10 s)
werden über die Media Session API unterstützt.

## Dateien

- `index.html` – die komplette App
- `manifest.json`, `sw.js`, `icon*.png`, `icon.svg` – für die Installation als App
- `start.cmd` / `start.sh` – lokaler Mini-Server (Python)
