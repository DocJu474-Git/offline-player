# Tape Volume - Pine-Port für TradingView

Port des thinkorswim-Studies „Tape Volume" v1.1 (Logan Crist / BaconTrading, 2018)
nach Pine Script v6. Datei: `tape_volume.pine`.

## Installation

1. TradingView öffnen, unten „Pine Editor".
2. Inhalt von `tape_volume.pine` einfügen, „Speichern", „Zum Chart hinzufügen".
3. Der Indikator läuft als Unterfenster. Werte sind auf -1 bis +1 normiert, Nulllinie = neutral.

## Datenquelle und Plan

| Chart | Plan | Qualität |
|---|---|---|
| Tick-Chart 1T/10T/100T/1000T | Expert oder Ultimate | wie Original: jeder Bar = ein Tick |
| Zeit-Chart + 1S-Intrabars (Standard) | Premium oder höher | Näherung: ein 1S-Bar = ein „Tick" |
| Zeit-Chart ohne Intrabars | jeder Plan | grob: ein Chart-Bar = ein „Tick" |

- Mit **Premium** läuft der Indikator im Modus „Intrabars 1S". Die Infobox zeigt den aktiven Modus.
- Für echte Tick-Charts einen Monat **Expert** buchen und einen 1T- oder 10T-Chart öffnen. Der Indikator
  erkennt Tick-Charts automatisch (Input „Datenquelle" = Auto).
- Intrabar-Historie ist bei TradingView begrenzt. Ältere Bars ohne Intrabar-Daten werden im Auto-Modus
  als ein Tick verarbeitet, der Übergang ist in der Linie sichtbar.
- Bar Replay und Deep Backtesting funktionieren laut TradingView nicht auf Tick-Charts.

## Was portiert ist

| Original | Port | Treue |
|---|---|---|
| Cash Flow (7A) | Range-VWAP, BreakEffect, VWAPDiff | 1:1 |
| Order Accumulation (7C) | zwei EMA-RSI (50 / 200 Ticks), Volumen-Score | 1:1 |
| Supply & Demand (7D) | Halbzyklus 100 Ticks, Summe 300 Ticks | 1:1, Zyklus als Input |
| Market Orders (7F) | Volumen bei Range-Wechsel / Gap | 1:1 |
| Order Speed (7G) | Zeit auf Up- minus Down-Seite | 1:1 |
| Price Inertia (7H) | Up/Down-Sum, Reverse-Option | 1:1 |
| Volume Momentum (7B) | Box-VWAP um Wendepunkte | Näherung: Wendepunkt kausal statt per Rückwärts-Indexer |
| Transaction Sentiment (7E) | Preis- vs. Daten-Pivots | Näherung: Pivot einen Tick später erkannt |
| Ausreißer-Filter (Abschnitt 10) | max. Schritt = 3x mittlerer Schritt | laufender statt chartweiter Mittelwert |
| Skalierung | „Letzte Bars" (900) oder „Alle Bars" | kausal (kein Blick in die Zukunft) |
| Eigene Volume-/Time-Bars, Uhr-Label | entfällt | TradingView zeigt den Preis im Hauptchart |

Pine Script arbeitet nur vorwärts. Alle Stellen, an denen das Original in die Zukunft schaut
(`C[-1]`, `HighestAll`, negative `GetValue`-Offsets), sind kausal ersetzt. Dadurch
unterscheidet sich der Linienverlauf im Detail vom thinkorswim-Bild, die Richtung der
Akkumulation bleibt gleich.

## Inputs

- Algorithmus 1 / 2: einer der acht Algorithmen oder „Aus".
- Datenquelle: Auto, Chart-Bars als Ticks, Intrabars.
- Intrabar-Timeframe: Standard 1S, nur auf Zeit-Charts relevant.
- Skalierung und sichtbare Bars entsprechen „LineScaling" und „VisualLength" im Original.
- Alerts: Algorithmus 1 kreuzt die Nulllinie.

## Grenzen

- Kein Bid/Ask-Aggressor, weder in thinkorswim noch in TradingView. Beide nutzen die Tick-Richtung.
- Die Zeitgewichtung in Supply & Demand (Volumen mal Millisekunden) ist aus dem Original übernommen.
- Der Code ist ohne Pine-Compiler geschrieben. Beim ersten Laden Fehlermeldungen des Editors prüfen.
