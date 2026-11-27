[English](troubleshooting.md) | **Deutsch**

# Handbuch Fehlersuche

Die Tür öffnet erst nach Sekunden, ein Befehl geht gar nicht durch oder die Bridge startet ständig neu? nuki_ng hilft herauszufinden, woran es liegt. Dieses Handbuch erklärt, was die Integration beobachtet, wie man den Check-up nutzt und was hinter den einzelnen Befunden meist steckt.

- [Schnellstart: Check-up](#schnellstart-check-up)
- [Was geprüft wird](#was-geprüft-wird)
- [Typische Ursachen](#typische-ursachen)
- [Sensoren für Verläufe und Automationen](#sensoren-für-verläufe-und-automationen)
- [Log, Logbuch und Reparaturhinweis](#log-logbuch-und-reparaturhinweis)
- [Einstellungen](#einstellungen)
- [Fehler melden](#fehler-melden)

## Schnellstart: Check-up

**Einstellungen → Geräte & Dienste → Nuki Lock → Gerät *Nuki Bridge* → *Check-up* drücken.**

Sofort erscheint eine Benachrichtigung „wird geprüft …“. Nach ein paar Sekunden ersetzt sie der Bericht, Probleme oben, in der Sprache von Home Assistant (Deutsch oder Englisch):

```
⚠️ Nuki Check-up: Warnung
- ⚠️ Wohnungstür: Bluetooth-Abbrüche: 8 in den letzten 24 h.
  → Mehr Abbrüche als üblich. Werden Befehle langsam, die Bridge näher stellen oder die Batterien tauschen.
- ✅ Bridge: erreichbar: Antwortet in 0.41 s.
- ✅ Haustür: Signal: -43 dBm, gut.
…
```

Dasselbe als Aktion, z. B. in einem Skript, mit dem Bericht als Antwort:

```yaml
- action: nuki_ng.checkup
  data:
    notify: false
  response_variable: report
- if: "{{ report.status != 'ok' }}"
  then:
    - action: notify.notify
      data:
        message: "Nuki: {{ report.bridges[0].checks | selectattr('status', 'ne', 'ok') | map(attribute='title') | join(', ') }}"
```

`report.status` ist `ok`, `warning` oder `problem`. `report.bridges[].checks[]` enthält je Prüfung `id`, `status`, `title`, `detail` und bei Bedarf `hint`.

Ohne Ziel prüft die Aktion alle eingerichteten Bridges, mit `device_id: <Bridge>` nur eine.

## Was geprüft wird

| Prüfung | Gut | Warnung | Problem |
|---|---|---|---|
| **Bridge erreichbar** | antwortet | antwortet langsamer als 2 s | keine Antwort |
| **Uhrzeit** | weniger als 30 s Abweichung | mehr als 30 s | – |
| **Token** | verschlüsselt | Hash oder Klartext | – |
| **Push-Updates** | Callback gesetzt und erreichbar | kein Callback oder Bridge erreicht Home Assistant nicht | – |
| **Signal** (je Gerät) | über −80 dBm | −80 bis −90 dBm | unter −90 dBm |
| **Bluetooth-Abbrüche** (je Gerät, 24 h) | bis 5 | 6–19 | ab 20 |
| **Datenfehler** (Bridge, 24 h) | 0–2 | ab 3 | – |
| **Neustarts** (Bridge, 24 h) | keine | Neustart | Neustart nach Bluetooth-Ausfall |
| **Befehle** (je Gerät, 24 h) | höchstens 2 mit Wiederholung | ab 3 mit Wiederholung | fehlgeschlagen |
| **Batterie** | – | – | kritisch |
| **Web-API** (falls eingerichtet) | antwortet | – | Fehler |

Die Grenzen sind so gesetzt, dass nur gemeldet wird, was man auch merkt. Ein paar Abbrüche am Tag, die die Bridge selbst wiederholt, sind normal und bleiben *gut*.

Die *Gesundheit* der Bridge (Sensor) zeigt laufend den schlechtesten Befund aus dem, was ohnehin bekannt ist. Der Check-up fragt zusätzlich die Bridge selbst (Antwortzeit, Uhrzeit, frisches Log) und die Web-API.

### Woher die Bluetooth-Werte kommen

Die Bridge führt ein kurzes eigenes Protokoll (`/log`). nuki_ng liest alle 10 Minuten die neuesten 100 Einträge und merkt sich, was neu ist:

- **Bluetooth-Abbruch** (`BLE-EarlyDisconnect`): Die Verbindung zum Gerät brach mitten im Befehl ab. Die Bridge versucht es meist selbst noch einmal. Das kostet Sekunden, bleibt aber sonst unsichtbar.
- **Bluetooth-Ausfall** (`BLE-RebootBLEFailed` u. a.): Die Bridge gibt Bluetooth auf und startet sich neu. Für 15–20 s geht dann kein Befehl.
- **Datenfehler** (`UartCRCError`): Gestörte Daten zwischen WLAN- und Bluetooth-Chip der Bridge. Fast immer die Stromversorgung.
- **Neustart** (`System-Startup`).

Größere Abfragen bringen die Bridge zum Hängen, deshalb liest nuki_ng bewusst wenig und selten. Läuft das Bridge-Log zwischen zwei Abfragen über, fehlen einzelne Einträge (im Debug-Log vermerkt). Die Zahlen sind dann eher zu niedrig als zu hoch.

## Typische Ursachen

| Befund | Häufige Ursache | Was hilft |
|---|---|---|
| Schwaches Signal, Bluetooth-Abbrüche | Bridge zu weit weg, Metall/Türzarge dazwischen | Bridge näher ans Gerät, ideal 1–3 m mit Sichtlinie |
| Datenfehler, Neustarts | schwaches oder defektes Netzteil, dünnes USB-Kabel | anderes Netzteil (mind. 1,5 A) und Kabel |
| Abbrüche nur bei einem Gerät | schwache Batterien | Batterien tauschen, auch wenn noch nicht „kritisch“ |
| Befehle mit Wiederholung, sonst alles grün | gelegentliche Funkstörung | beobachten; die Integration wiederholt sicher |
| Bridge antwortet langsam | zu kurzes Abfrageintervall, andere Systeme fragen dieselbe Bridge | Abfrageintervall nicht unter 30 s, andere Abfragen reduzieren |
| Keine Push-Updates | Firewall/VLAN, HTTPS-URL | HTTP-URL von Home Assistant im lokalen Netz in den Optionen eintragen |
| Uhrzeit weicht ab | Bridge ohne Internet, Home-Assistant-Uhr falsch | Zeit von Home Assistant prüfen, Bridge braucht Internet für die Zeit |
| Hash- oder Klartext-Token | alte Bridge-Firmware | Firmware-Update der Bridge (Taste *Bridge-Firmware aktualisieren*) |

**Wichtig bei Türöffnern:** Befehle, die eine Tür öffnen, wiederholt nuki_ng nur, wenn die Bridge sie sicher nicht ausgeführt hat. Eine Zeitüberschreitung bedeutet „vielleicht offen“ und wird nicht wiederholt. Viele solche Fälle zeigen sich im Check-up als fehlgeschlagene Befehle.

## Sensoren für Verläufe und Automationen

| Entität | Inhalt |
|---|---|
| `sensor.nuki_bridge_health` | `ok`, `warning` oder `problem`; Attribut `problems` listet die Befunde, `last_checkup` den letzten Check-up |
| `sensor.nuki_bridge_data_errors_24h` | Datenfehler der letzten 24 h; Attribute `restarts_24h`, `bluetooth_failures_24h` |
| `sensor.nuki_<gerät>_bluetooth_drops_24h` | Bluetooth-Abbrüche dieses Geräts in 24 h |
| `sensor.nuki_<gerät>_last_action_duration` | Dauer des letzten Befehls in s; Attribute `action`, `success`, `attempts`, `confirmed_by`, `error`, `at` und Zähler `actions_24h`, `retried_24h`, `failed_24h` |
| `sensor.nuki_bridge_failed_actions` | fehlgeschlagene Versuche insgesamt (bleibt über Neustarts) |

Beispiel: Push, wenn ein Gerät auffällig viele Abbrüche hat.

```yaml
triggers:
  - trigger: numeric_state
    entity_id: sensor.nuki_wohnungstur_bluetooth_drops_24h
    above: 5
actions:
  - action: notify.notify
    data:
      message: "Wohnungstür: {{ trigger.to_state.state }} Bluetooth-Abbrüche in 24 h. Bridge näher ans Schloss?"
```

Beispiel: Wie schnell öffnet die Haustür? Den Sensor `…_last_action_duration` in eine Verlaufskarte legen. Steigt die Dauer und `attempts` ist öfter 2 oder mehr, verdecken Wiederholungen ein Funkproblem.

## Log, Logbuch und Reparaturhinweis

**Home-Assistant-Log:** Neue Bluetooth-Probleme, wiederholte Befehle und Befunde des Check-ups werden mit der eingestellten *Log-Stufe für Probleme* geschrieben:

- **Warnung** (Standard): erscheint ohne weitere Einstellung im Log.
- **Info** oder **Debug**: erscheint nur, wenn diese Stufe für die Integration aktiv ist. Für eine Fehlersuche mit allen Details: *Einstellungen → Geräte & Dienste → Nuki Lock → ⋮ → Debug-Protokollierung aktivieren*, das Problem nachstellen, danach wieder deaktivieren. Home Assistant bietet dann die Log-Datei zum Herunterladen an.

Fehlgeschlagene Befehle meldet Home Assistant unabhängig davon immer als Fehler der Aktion.

**Logbuch:** Jeder Befehl erscheint mit Dauer und Versuchen („open: done in 1.2 s“), jedes neue Bluetooth-Problem mit Anzahl. Die Ereignisse heißen `nuki_ng_action` und `nuki_ng_bluetooth_problem` und lassen sich auch als Auslöser in Automationen nutzen.

**Reparaturhinweis:** Unter *Einstellungen → System → Reparaturen* erscheint „Nuki Bridge ist instabil“, solange es viele Bluetooth-Abbrüche, Bluetooth-Ausfälle mit Neustart oder gehäufte Datenfehler gibt. Er verschwindet von selbst, wenn sich die Lage beruhigt.

## Einstellungen

*Einstellungen → Geräte & Dienste → Nuki Lock → Konfigurieren → Abschnitt Diagnose*

| Option | Standard | Wirkung |
|---|---|---|
| Bridge-Log überwachen | an | Liest das Bridge-Log. Aus: keine Bluetooth-Zähler, keine Sensoren *Bluetooth Drops* / *Data Errors*, keine Abfrage von `/log` |
| Log-Stufe für Probleme | Warnung | siehe oben |
| Logbuch-Einträge | an | Befehle und Bluetooth-Probleme im Logbuch |
| Reparaturhinweis bei instabiler Bridge | an | siehe oben |

Der Check-up und die Befehlsstatistik sind immer verfügbar. Der Check-up fragt die Bridge nur, wenn man ihn startet.

## Fehler melden

Hilft das alles nicht weiter, bitte ein [Issue](https://github.com/kewe87/hass_nuki_ng/issues) anlegen und mitschicken:

1. den Bericht des Check-ups (Text der Benachrichtigung),
2. die Diagnosedaten: *Einstellungen → Geräte & Dienste → Nuki Lock → ⋮ → Diagnosedaten herunterladen*. Tokens und Adressen sind darin entfernt. Sie enthalten den Bluetooth-Verlauf der letzten 24 h und die Befehlsstatistik.
3. bei Bedarf ein Debug-Log (siehe oben).
