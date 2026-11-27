[English](README.md) · **Deutsch**

# Nuki Lock (nuki_ng)

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Lizenz: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Eine sichere und zuverlässige Community-Integration für Nuki-Geräte in Home Assistant.
Smart Locks und Opener, lokaler Push über die Nuki Bridge, optional die Nuki Web-API.

> **Wenn Home Assistant sagt, die Tür wurde geöffnet, dann wurde sie geöffnet.
> Und sie öffnet nie doppelt oder verspätet.**

> [!NOTE]
> Kostenloses, inoffizielles Hobbyprojekt – nicht mit der Nuki Home Solutions GmbH verbunden, nicht von ihr unterstützt oder empfohlen.
> Name und Logo von Nuki © Nuki Home Solutions GmbH, siehe [Rechtlicher Hinweis](#rechtlicher-hinweis).

## Warum nuki_ng?

Verglichen mit der in Home Assistant eingebauten **Core**-Integration und mit **kvj**, dem Projekt, auf dem nuki_ng basiert:

| Sicherheit | Core | kvj | **nuki_ng** |
|---|:-:|:-:|:-:|
| Verschlüsseltes Token | ☑️ ¹ | ❌ | ✅ |
| Push nur von der Bridge | ❌ | ❌ | ✅ |
| Push-Daten geprüft | ❌ | ❌ | ✅ |
| Geheime Webhook-ID | ❌ | ❌ | ✅ |
| Kein „alle öffnen“ | ➖ | ❌ | ✅ |
| Wartung nur für Admins | ➖ | ❌ | ✅ |

| Zuverlässigkeit | Core | kvj | **nuki_ng** |
|---|:-:|:-:|:-:|
| Prüft Ergebnis der Aktion | ❌ | ❌ | ✅ |
| Wiederholt bei Bridge-Ausfall | ❌ | ❌ | ✅ |
| Nie doppelt/verspätet öffnen | ➖ ³ | ➖ ³ | ✅ |
| Ergebnis für Skripte | ❌ | ❌ | ✅ |
| Kommt mit belegter Bridge klar | ❌ | ❌ | ✅ |
| Übersteht Bridge-Neustart | ❌ | ❌ | ✅ |
| Ignoriert veraltete Zustände | ❌ | ❌ | ✅ |
| Lässt fremde Callbacks in Ruhe | ✅ | ❌ | ✅ |
| Vollständig asynchron | ❌ | ❌ | ✅ |

| Funktionen | Core | kvj | **nuki_ng** |
|---|:-:|:-:|:-:|
| Web-API / Hybrid | ❌ | ✅ | ✅ |
| Klingel-Event | ❌ | ❌ | ✅ |
| Bridge-Zustandssensoren | ❌ | ❌ | ✅ |
| Neustart / FW-Update | ❌ | ✅ | ✅ |
| RSSI, Keypad-Batterie | ❌ | ✅ | ✅ |
| Erneute Anmeldung | ✅ | ❌ | ✅ |
| Diagnose-Download | ❌ | ❌ | ✅ |
| DHCP-Erkennung | ✅ | ❌ | ❌ ² |

✅ ja · ☑️ teilweise · ❌ nein · ➖ nicht zutreffend

<sub>¹ optional, standardmäßig aus · ² Erkennung stattdessen über Nuki-Server · ³ wiederholt gar nicht<br>
Core = Home Assistant 2026.11 (pynuki 1.6.3), kvj = kvj/hass_nuki_ng 0.5.5, am Quellcode geprüft.</sub>

**Umstieg von kvj ohne Aufwand:** gleiche Domain, gleiche Entitäts-IDs.

> [!TIP]
> **Danke, Konstantin!** nuki_ng begann als [kvj/hass_nuki_ng](https://github.com/kvj/hass_nuki_ng) von Konstantin ([kvj](https://github.com/kvj)).
> Seine Arbeit hat das Fundament gelegt – Bridge-Callbacks, Web-API-Anbindung und das Entitätsmodell – und vielen Nuki-Nutzern seit 2021 gute Dienste geleistet. Danke für diese tolle Arbeit!
> Dieses Projekt ist eine verbesserte, eigenständig weiterentwickelte Fortführung davon (seit 0.7.0), veröffentlicht unter derselben MIT-Lizenz.
> Der ursprüngliche Copyright- und Lizenzhinweis bleibt in [LICENSE](LICENSE) erhalten.

## Was den Unterschied macht

### 🔒 Sicherheit

- **Verschlüsseltes Token** (Bridge-API 1.13): Das Token selbst geht nie über das Netz, jede Anfrage gilt nur 60 s. Ältere Firmware nutzt das Hash-Token. Das Klartext-Token muss man ausdrücklich wählen.
- **Abgesicherter Push:** geheime, zufällige Webhook-ID, nur die IP der Bridge wird angenommen, nur bekannte und gültige Felder werden übernommen.
- **Kein versehentliches Öffnen:** „Öffnen“ für die ganze Bridge oder mehrere Geräte wird abgelehnt. Dauermodus (öffnet die Haustür für jeden) und Berechtigungs-Schalter sind standardmäßig aus.
- **Keine Geheimnisse** in Logs, Attributen oder Diagnosedaten.

### 🚪 Zuverlässige Türaktionen

- **Bestätigt:** Nur ein bestätigter Erfolg der Bridge zählt. Fehler werden gemeldet, nicht verschluckt.
- **Sichere Wiederholung:** Nach einem Bridge-Neustart wird Auf-/Zusperren wiederholt – aber erst nach der Prüfung, ob der Befehl nicht schon gewirkt hat.
- **Nie doppelt, nie verspätet:** „Öffnen“ wird nur wiederholt, wenn es die Tür sicher nicht erreicht hat, und nie nach 35 s. Eine verlorene Antwort wird als `unknown` gemeldet, statt Minuten später erneut zu summen.

### 🛡️ Stabilität

- Immer nur eine Anfrage an die Bridge – mehr verkraftet sie nicht.
- Kurze Ausfälle machen Entitäten nicht unverfügbar.
- Veraltete Zustände nach einem Bridge-Neustart überschreiben keine neueren.
- Unbekannte Werte erscheinen als *unbekannt*, statt Entitäten lahmzulegen.

## Installation

[![In HACS öffnen](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=kewe87&repository=hass_nuki_ng&category=integration)

1. In HACS nach **Nuki Lock** (von kewe87) suchen und installieren
2. Home Assistant neu starten

Falls es noch nicht auftaucht: HACS → ⋮ → *Benutzerdefinierte Repositories*, `https://github.com/kewe87/hass_nuki_ng` mit Typ *Integration* hinzufügen, dann **Nuki Lock** installieren.

**Umstieg von kvj:** altes Repository in HACS entfernen (Integration behalten), dieses installieren, neu starten.

Benötigt Home Assistant 2024.12 oder neuer.

## Einrichtung

[![Integration hinzufügen](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=nuki_ng)

Nach **Nuki Lock** suchen (nicht *Nuki* – das ist die eingebaute). Die Bridge wird automatisch gefunden.

- **Bridge-Token:** Nuki-App → *Meine Geräte verwalten* → *Bridge* → HTTP-API aktivieren, Token festlegen.
- **Web-API-Token** (optional): [web.nuki.io](https://web.nuki.io/) → *API* → *API-Token generieren*.

Nur Bridge, nur Web-API (Smart Lock 3.0 Pro und neuer) oder beides.

### Optionen

- **Callback-URL** – standardmäßig automatisch. Muss HTTP sein, die Bridge kann kein HTTPS.
- **Abfrageintervall** – 30 s. Zustände kommen normalerweise per Push.
- **Wiederholungen** – 3, siehe *Zuverlässige Türaktionen*.
- **Anmeldung** – automatisch: verschlüsseltes Token (Firmware ≥ 2.14.0 / 1.22.1), sonst Hash. *Klartext* sendet das Token unverschlüsselt.
- **Diagnose** (eingeklappter Abschnitt, jeder Teil lässt sich abschalten):
  - *Bridge-Log überwachen* – an. Zählt Bluetooth-Abbrüche, Datenfehler und Neustarts.
  - *Log-Stufe für Probleme* – Warnung, Info oder Debug.
  - *Logbuch-Einträge* – an. Jeder Befehl und jedes Bluetooth-Problem im Logbuch.
  - *Reparaturhinweis* – an. Erscheint, solange die Bridge deutlich instabil ist.

## Aktionen

### Schloss-Entität

- `lock.lock` – Smart Lock: zusperren · Opener: Ring to Open aus
- `lock.unlock` – Smart Lock: aufsperren · Opener: Ring to Open an
- `lock.open` – Smart Lock: entriegeln · Opener: Tür summen

### `nuki_ng.execute_action`

Führt eine Bridge-Aktion auf **einem** Schloss oder Opener aus und liefert das Ergebnis.

Aktionen: `unlock`, `lock`, `open`, `lock_n_go`, `lock_n_go_open`, `activate_rto`, `deactivate_rto`, `electric_strike_actuation`, `activate_continuous_mode`, `deactivate_continuous_mode`

```yaml
- action: nuki_ng.execute_action
  target:
    entity_id: lock.nuki_haustur_lock
  data:
    action: electric_strike_actuation
  response_variable: ergebnis
  continue_on_error: true
- if: >
    {{ not ergebnis.success and
       ergebnis.results[0].outcome == 'unknown' }}
  then:
    - action: notify.notify
      data:
        message: Haustür evtl. geöffnet.
```

Ergebnis je Gerät:

- `success` – true / false
- `outcome` – `success`, `not_executed` oder `unknown`
- `attempts` – Anzahl Versuche
- `verified` – false bei der Web-API (nur angenommen)
- `error` – Fehlertext oder null

### `nuki_ng.checkup`

Prüft die Bridge und alle Geräte und liefert einen Bericht (`status`: `ok`, `warning` oder `problem`). Mit `notify: true` erscheint er zusätzlich als Benachrichtigung – wie bei der Taste *Check-up*. Siehe [Diagnose](#diagnose).

### Bridge-Wartung (nur Admins)

`nuki_ng.bridge_reboot`, `nuki_ng.bridge_fwupdate`, `nuki_ng.bridge_delete_callback`

## Entitäten

Gerätenamen richten sich nach der Nuki-App, Entitätsnamen nach der Sprache von Home Assistant (Deutsch und Englisch enthalten). Die Entitäts-IDs ändern sich mit der Sprache nicht.

**Je Schloss / Opener**
- Schloss mit Zustand *offen* / *öffnet*
- Klingel-Event und Ring-Aktion (Opener)
- Türsensor: offen, Zustand, Sicherheitszustand
- Batterie, Batterie kritisch, lädt, Keypad-Batterie
- Zustand, RSSI, Firmware
- Bluetooth-Abbrüche (24 h), Dauer des letzten Befehls
- Dauermodus (Opener, **standardmäßig aus**)

**Bridge**
- Verbunden, Callback gesetzt, Callback erreichbar
- Gesundheit (`ok` / `warning` / `problem`), Datenfehler (24 h)
- Letzter Neustart, fehlgeschlagene Aktionen
- Tasten für Check-up, Neustart und Firmware-Update

**Web-API**
- Aktivitätsprotokoll, zuletzt aufgesperrt von
- Auto-Lock, Taste, Klingelunterdrückung
- Berechtigungen (**standardmäßig aus**)

### Tipp: Öffnen-Taste mit Rückfrage

```yaml
type: button
entity: lock.nuki_haustur_lock
name: Öffnen
tap_action:
  action: perform-action
  perform_action: lock.open
  target:
    entity_id: lock.nuki_haustur_lock
  confirmation:
    text: Tür öffnen?
```

## Diagnose

Befehle langsam, Tür geht nicht auf, Bridge startet neu? Am Gerät *Nuki Bridge* auf **Check-up** drücken. Nach ein paar Sekunden zeigt eine Benachrichtigung, was in Ordnung ist und was nicht, jeweils mit Hinweis.

Geprüft werden: Bridge erreichbar und wie schnell, Uhrzeit, Token, Push-Updates, Signal je Gerät, Bluetooth-Abbrüche, Datenfehler (oft das Netzteil), Neustarts, Befehle mit Wiederholungen, Batterien.

Probleme landen außerdem im Home-Assistant-Log (Stufe einstellbar) und, wenn eingeschaltet, im Logbuch. 📖 **[Handbuch Fehlersuche](docs/troubleshooting.de.md)** – jede Prüfung erklärt, typische Ursachen, Sensoren für Verläufe und Automationen.

## Gut zu wissen

- *Callback erreichbar* heißt: Home Assistant stellt die URL bereit. Ob die Bridge durchkommt (Firewall, VLAN), zeigt `last_push` nach der nächsten Änderung.
- Die Bridge hält max. 3 Callbacks. nuki_ng fasst nur den eigenen an.
- Keine sehr kurzen Abfrageintervalle – jede Anfrage blockiert die Bridge.

## Gefällt dir nuki_ng?

Dann freue ich mich über einen ⭐ auf [GitHub](https://github.com/kewe87/hass_nuki_ng). Das hilft anderen, das Projekt zu finden. Fehler und Ideen gerne als [Issue](https://github.com/kewe87/hass_nuki_ng/issues).

## Änderungen

[CHANGELOG.de.md](CHANGELOG.de.md) · [Releases](https://github.com/kewe87/hass_nuki_ng/releases)

## Lizenz

MIT – siehe [LICENSE](LICENSE).
Ursprüngliches Werk © 2021 Konstantin ([kvj/hass_nuki_ng](https://github.com/kvj/hass_nuki_ng)), Änderungen © 2026 kewe87.

## Rechtlicher Hinweis

nuki_ng ist ein kostenloses Hobbyprojekt, von Nuki-Fans für Nuki-Fans: Nuki baut großartige smarte Schlösser, und dieses Projekt möchte, dass sie sich auch in Home Assistant zuhause fühlen.

Es ist **kein** offizielles Nuki-Produkt und steht in keiner Verbindung zur Nuki Home Solutions GmbH; es wird von ihr weder unterstützt noch empfohlen.
Der Name *Nuki* und das Nuki-Logo sind Marken der Nuki Home Solutions GmbH; alle Rechte daran (auch das Urheberrecht am Logo) liegen bei Nuki. Sie dienen hier ausschließlich als Hinweis, mit welchen Geräten diese Integration funktioniert.
Wünscht Nuki die Entfernung des Logos, genügt ein [Issue](https://github.com/kewe87/hass_nuki_ng/issues) – es wird umgehend entfernt.

Bereitgestellt „wie besehen“, ohne Gewährleistung.
