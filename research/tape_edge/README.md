# Tape Volume Edge-Test (Python)

Prüft, ob die acht Algorithmen des thinkScript-Studies „Tape Volume" auf echten
CME-Tick-Daten einen messbaren Vorsprung haben. Ergebnis ist ein PDF-Report plus CSV.

## Dateien

| Datei | Zweck |
|---|---|
| `algos.py` | Die acht Algorithmen plus Delta-Referenz als numba-Kernel auf dem Tick-Strom |
| `data.py` | Databento-Download mit Kostencheck, Parquet-Cache, synthetische Testdaten |
| `edge_test.py` | Ereignisstudie, Zufallsbasis, Kosten, Monatsstabilität, PDF-Report |
| `requirements.txt` | Python-Pakete |

## Installation

```
cd research/tape_edge
pip install -r requirements.txt
```

## 1. Funktionstest ohne Daten

```
python edge_test.py --synthetic --synthetic-days 3 --out report_synthetic
```

Erzeugt einen Random Walk mit Bid/Ask-Bounce. Ein korrekter Test darf hier keinen
Edge finden: Ø Ticks nahe 0, |t| meist unter 2, Netto negativ.

## 2. Daten laden (Databento)

API-Key als Umgebungsvariable `DATABENTO_API_KEY` setzen. Erst Kosten ansehen:

```
python data.py --symbol ES.v.0 --start 2026-08-01 --end 2026-11-01 --schema trades
```

Dann laden (Abbruch, wenn über `--max-cost`, Standard 20 USD):

```
python data.py --symbol ES.v.0 --start 2026-08-01 --end 2026-11-01 --schema trades --confirm
```

Optional ein Monat `--schema tbbo` für echte Bid/Ask-Mids. Liegt eine TBBO-Datei vor,
nutzt der Test deren Mid, sonst die Näherung „Last minus halber Tick in Aggressor-Richtung".

## 3. Test laufen lassen

```
python edge_test.py --data data/ES.v.0_trades_2026-08-01_2026-11-01.parquet --out report
```

Wichtige Optionen:

| Option | Standard | Bedeutung |
|---|---|---|
| `--tick-value` | 12,5 | USD je Tick (ES 12,5, MES 1,25) |
| `--commission` | 2,0 | USD je Seite |
| `--window` | 2000 | Ticks für Trendbereinigung der Nullkreuzung |
| `--lookback` | 2000 | Ticks für Divergenz |
| `--div-frac` | 0,25 | Divergenz: Algo mindestens so weit vom Extrem (Anteil der Spanne) |
| `--min-gap` | 500 | Mindestabstand zwischen Ereignissen in Ticks |
| `--draws` | 300 | Zufallsziehungen für p Zufall |
| `--max-days` | 0 | nur die letzten N Handelstage (Speicher) |

Speicher: etwa 150 Byte je Trade im Peak. Drei Monate ES (ca. 30 Mio. Trades) brauchen
rund 5 GB RAM. Bei weniger RAM `--max-days 20`.

## Was der Report zeigt

- **Ø Ticks**: mittlere Mid-Bewegung in Signalrichtung nach 5, 20, 60 s und 20, 100 Ticks.
- **p Zufall**: Anteil zufälliger Einstiege gleicher Anzahl mit mindestens gleich großem Effekt.
- **Netto**: Ø Ticks minus 1 Tick Spread minus Kommission beider Seiten.
- **IC**: Rangkorrelation zwischen Algo-Änderung (500 Ticks) und 60-s-Rendite, je Tag.
- **Monate**: gleiches Vorzeichen in jedem Monat ist Pflicht für einen Edge-Kandidaten.
- **Tick-Regel vs. Flag**: Supply & Demand und Delta werden mit beiden Seitenquellen gerechnet.

Edge-Kandidat heißt: Netto positiv, p Zufall unter 0,01, alle Monate gleiches Vorzeichen,
n über 1000. Bei 22 gleichzeitig getesteten Kombinationen sind einzelne kleine p-Werte
durch Zufall zu erwarten.

## Bekannte Messfallen, die der Test vermeidet

- Last-zu-Last-Renditen messen den Bid/Ask-Bounce als Scheinertrag. Der Test misst Mid-zu-Mid.
- Ereignisse nur in der RTH (08:30-15:00 Chicago), Horizont innerhalb derselben Session.
- Keine Parameteroptimierung, daher kein Overfitting der Signale.
