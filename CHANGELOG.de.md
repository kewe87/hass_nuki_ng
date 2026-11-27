[English](CHANGELOG.md) | **Deutsch**

# Änderungsprotokoll

## 0.7.5 – noch nicht veröffentlicht

- **Diagnose** für alle, die wissen wollen, warum eine Tür langsam ist oder nicht aufgeht:
  - Taste *Check-up* und Aktion `nuki_ng.checkup`: prüft Bridge, Uhrzeit, Token, Push-Updates, Signal, Bluetooth-Abbrüche, Datenfehler, Neustarts, Befehle und Batterien, mit Hinweis zu jedem Befund.
  - Liest das Bridge-Log (alle 10 Minuten in kleinen Portionen) und zählt Bluetooth-Abbrüche, Bluetooth-Ausfälle, Datenfehler und Neustarts.
  - Neue Sensoren: Gesundheit der Bridge, Datenfehler (24 h), Bluetooth-Abbrüche (24 h) und Dauer des letzten Befehls je Gerät.
  - Befehle und Bluetooth-Probleme im Logbuch, Reparaturhinweis bei instabiler Bridge, Problem-Meldungen mit einstellbarer Log-Stufe.
  - Alles lässt sich in den Optionen abschalten (Abschnitt *Diagnose*). Fehlgeschlagene Aktionen bleiben jetzt über Neustarts erhalten.
- [Handbuch Fehlersuche](docs/troubleshooting.de.md).
- **Entitätsnamen in deiner Sprache**: Die Namen folgen der Sprache von Home Assistant (Deutsch und Englisch enthalten), z. B. *Nuki Haustür Batterie*. Die Entitäts-IDs bleiben gleich.
- Der Bericht des Check-ups ist auch auf Deutsch, und während er läuft, erscheint sofort eine Benachrichtigung.
- **Firmware-Updates in der Update-Übersicht**: Schlösser und Opener zeigen neue Firmware unter *Einstellungen → Updates*, mit Release Notes aus dem Nuki-Hilfecenter (Option, standardmäßig an).
  Die Taste der Bridge heißt jetzt *Bridge-Firmware aktualisieren*: Für die Bridge gibt es keine Release Notes, darüber aktualisiert man weiterhin die Bridge selbst.
- Bluetooth-Abbrüche: Eine Serie von Nachversuchen (die Bridge schreibt je Versuch einen Eintrag, oft 3–8 in wenigen Sekunden) zählt als ein Abbruch, damit die Warngrenzen stimmen.
- Übrig gebliebene Geräte älterer Versionen (ein zweites, leeres Gerät je Schloss) werden automatisch entfernt. Geräte, die die Bridge nicht mehr meldet, lassen sich in der Oberfläche löschen.

## 0.7.4 – 17.11.2026

- Zeigt wieder das Nuki-Logo (hell und dunkel) wie das ursprüngliche nuki_ng, damit man die Integration sofort erkennt.
- README: klarer rechtlicher Hinweis – kostenloses, inoffizielles Hobbyprojekt; Name und Logo von Nuki gehören der Nuki Home Solutions GmbH.

## 0.7.3 – 17.11.2026

- Eigenes Icon (`brand/icon.png`, `brand/icon@2x.png`), damit die Integration in Home Assistant mit Symbol erscheint.

## 0.7.2 – 12.11.2026

- Schnellere Wiederholung, wenn die Bridge eine Aktion als fehlgeschlagen meldet (meist ein Bluetooth-Abbruch
  zum Gerät): der nächste Versuch startet nach 1 s statt 5 s, die Tür öffnet nach einem Aussetzer also früher.

## 0.7.1 – 11.11.2026

- README überarbeitet: Vergleich zuerst, auf dem Handy gut lesbar; README und Änderungsprotokoll auch auf Deutsch.
- Prüfung mit hassfest und der HACS-Action bei jedem Push.
- Bridge-Wartungsaktionen wählen die Bridge über ein Gerätefeld statt über ein Ziel
  (YAML mit `target: device_id` funktioniert weiter).
- Manifest-Schlüssel sortiert, `hacs.json` aufgeräumt.

## 0.7.0 – 11.11.2026

Ab dieser Version ein eigenständiges Projekt (basiert auf kvj/hass_nuki_ng, siehe [LICENSE](LICENSE)).

### Sicherheit
- Standardmäßig Anmeldung mit verschlüsseltem Token (xsalsa20poly1305, Bridge-API 1.13), automatischer
  Rückfall auf das von Nuki abgekündigte Hash-Token; Klartext-Token nur bei ausdrücklicher Auswahl.
- Zufällige, geheime Webhook-ID statt einer aus dem Konfigurationseintrag abgeleiteten.
- Push-Callbacks werden nur von der IP der Bridge angenommen (sonst HTTP 403), Größe begrenzt,
  nur bekannte Felder mit geprüften Typen, Gerätetyp muss passen, keine Zeitstempel aus Callbacks.
- Öffnende Aktionen (`open`, `lock_n_go_open`, `electric_strike_actuation`) werden für die ganze
  Bridge oder mehrere Geräte abgelehnt.
- Wartungsaktionen der Bridge (`bridge_reboot`, `bridge_fwupdate`, `bridge_delete_callback`) nur für Admins.
- Dauermodus- und Berechtigungs-Schalter standardmäßig deaktiviert.
- Tokens, Hashes, Nonces und Webhook-ID werden in Logs, Attributen und Diagnosedaten geschwärzt;
  Token-Felder sind Passwortfelder; Eingaben werden nicht mehr geloggt.

### Zuverlässigkeit
- HTTP 503 (Bridge beschäftigt, nichts ausgeführt) wird automatisch wiederholt.
- Öffnende Aktionen werden nur wiederholt, wenn sie das Gerät sicher nicht erreicht haben, und nie
  später als 35 s; eine verlorene Antwort wird als `unknown` gemeldet.
- Auf-/Zusperren prüfen vor einer Wiederholung den aktuellen Zustand, damit kein Befehl doppelt ankommt.
- `execute_action` liefert `outcome` (`success` / `not_executed` / `unknown`) und `verified` und
  prüft die Aktion.
- HTTP 401 startet die erneute Anmeldung.
- Beim Abgleich gepollter Zustände werden nur Zeitstempel der Bridge verglichen (keine Probleme durch
  abweichende Uhren).
- Laufende Aktionen stoppen und Timer werden beim Entladen beendet.
- Callbacks anderer Systeme werden nie entfernt; Reparaturhinweis, wenn alle 3 Plätze belegt sind.
- Änderungen über die Web-API lesen erst die aktuelle Konfiguration und aktualisieren den Cache erst
  nach Erfolg.

### Entitäten
- Unbekannte Zustandswerte (z. B. Opener-Boot) werden *unbekannt* statt Fehler auszulösen.
- Die Schloss-Entität zeigt *offen* / *öffnet* beim Entriegeln oder Summen.
- Fehlender Batteriestand ist *unbekannt* statt 0 %.
- Callback-URLs aus den Attributen entfernt; stattdessen letzter Push, abgelehnte Pushes und Anmeldemodus.
- Gerätemodell wird aus dem Gerätetyp abgeleitet.

### Konfiguration
- Jede Bridge kann nur einmal hinzugefügt werden (Hardware-ID).
- Erneute Anmeldung, wenn sich das Token ändert.
- Neue Option: Anmeldemodus der Bridge.
- Englische und deutsche Übersetzungen für Dialoge, Reparaturhinweise und Aktionen.

### Hinweise zum Update
- Nach dem Update wird der Bridge-Callback automatisch mit einer neuen geheimen URL registriert.
- Wer `switch.…_continuous_mode` oder die Berechtigungs-Schalter genutzt hat, aktiviert sie unter
  *Einstellungen → Entitäten* wieder.
- Skripte, die die Antwort von `nuki_ng.execute_action` auswerten, können jetzt `outcome` je Gerät lesen.

## 0.6.0 – 11.11.2026

Erste Version dieses Forks.

- Schlossaktionen werden geprüft (`success` der Bridge), Bridge-Anfragen nacheinander, kurze Ausfälle
  werden überbrückt.
- `execute_action` wirkt nur auf die gewählten Geräte und liefert Ergebnisse.
- Optionen: Callback-URL, Abfrageintervall, Wiederholungen.
- Klingel-Event, Bridge-Zustandsentitäten, Diagnose-Download.

## 0.5.5 und älter

Siehe das ursprüngliche Projekt [kvj/hass_nuki_ng](https://github.com/kvj/hass_nuki_ng).
