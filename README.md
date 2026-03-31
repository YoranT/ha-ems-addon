# EMS Energy Manager v2 — Home Assistant Add-on

Slim energiebeheersysteem voor Home Assistant met **echte** device-integraties:
SolarEdge (omvormer + inline meter + batterij), Sessy batterij, Easee laadpaal,
HomeWizard P1, Sessy P1, en elke Home Assistant entiteit.

**Geen MQTT vereist** — alle communicatie gaat via Modbus TCP, lokale REST of cloud REST API.

---

## 🚀 Installeren / bijwerken

### Via GitHub (aanbevolen)

1. HA → **Instellingen → Add-ons → Add-on Store → ⋮ → Aangepaste opslagplaatsen**
2. URL: `https://github.com/YoranT/ha-ems-addon` → **Toevoegen**
3. Zoek **"EMS Energy Manager"** → **Installeren**
4. Na installatie: **Starten** → **Tonen in zijbalk** aanzetten
5. Klik **EMS** in de zijbalk

> **Bijwerken van v1 naar v2?** Stop de add-on, installeer opnieuw (of klik "Bijwerken" als beschikbaar), start opnieuw.
> Je apparaatconfiguratie in `/config/ems/devices.json` blijft bewaard.

---

## 📡 Ondersteunde apparaten

### ☀️ SolarEdge — Modbus TCP
Verbindt rechtstreeks met de **omvormer** via Modbus TCP.
Meter en batterij zijn bereikbaar via dezelfde verbinding (zelfde IP/poort).

**Vereiste instelling op omvormer:**
SetApp → Communicatie → RS485/LAN → **Modbus TCP inschakelen**

| Apparaat | Type selecteer | Poort |
|---|---|---|
| Omvormer (SE5K, SE8K, ...) | Zonnepanelen | 1502 (nieuw) / 502 (oud) |
| Inline meter | Slimme meter | zelfde als omvormer |
| Batterij (LG Chem, BYD via StorEdge) | Thuisbatterij | zelfde als omvormer |

**Meter offset:** 0 = meter 1, 1 = meter 2 (bij twee meters)

---

### 🔋 Sessy batterij — lokale REST API
De Sessy heeft een ingebouwde lokale HTTP API — **geen cloud, geen MQTT**.

**Instellen:**
- Voeg toe als **Thuisbatterij** → merk **Sessy**
- IP-adres: bijv. `192.168.1.x` of `sessy-XXXX.local`
- Gebruikersnaam + wachtwoord: **sticker op de Sessy dongle**

**EMS stuurt de Sessy automatisch:**
- Laadt bij zonne-overschot (`set_power` negatief = laden)
- Ontlaadt bij netafname (`set_power` positief = ontladen)
- Valt terug op `HOME_SMART` als EMS niet stuurt

**Handmatig setpoint** instellen kan via de knop op de apparaatkaart.

---

### 📊 Sessy P1 dongle — lokale REST API
- Voeg toe als **Slimme meter** → merk **Sessy**
- Zelfde IP en inloggegevens als de Sessy batterij

---

### 📊 HomeWizard Wi-Fi P1 — lokale HTTP API
**Vereiste instelling:**
HomeWizard Energy app → Instellingen → Meter → **Lokale API inschakelen**

- Voeg toe als **Slimme meter** → merk **HomeWizard**
- IP-adres van de P1 meter (zie HomeWizard app of router)
- Geeft import/export vermogen, T1/T2 tellers en gasverbruik

---

### 🔌 Easee laadpaal — Cloud REST API
Easee heeft geen lokale API. De EMS gebruikt de officiële Easee Cloud API.

**Instellen:**
- Voeg toe als **Laadpaal (EV)** → merk **Easee**
- E-mailadres of telefoonnummer van je Easee account
- Wachtwoord van je Easee account
- Charger ID is optioneel — wordt automatisch gevonden

**EMS stuurt Easee automatisch:**
- Start laden bij ≥1380W zonne-overschot (= minimaal 6A op 230V)
- Past laadstroom dynamisch aan op beschikbaar overschot
- Stopt of verlaagt bij onvoldoende zon

**Handmatig** start/stop via knop op de apparaatkaart.

---

### 🏠 Home Assistant Entiteit
Koppel **elke** HA sensor of number entiteit als vermogensmeter.
Handig voor: Victron VRM, Fronius via HA integratie, DSMR P1 Reader, SMA via HA, etc.

- Voeg toe als gewenst type → merk **Overig**
- Zoek de entiteit in het formulier (live zoeken in jouw HA entiteiten)

---

### ⚡ Generieke Modbus TCP
Voor andere merken die Modbus TCP ondersteunen:
Huawei SUN2000/LUNA2000, Fronius, Growatt, Victron Cerbo GX, Solax, BYD, Eastron SDM630, Nibe warmtepomp.

Standaard registeradressen per merk zijn ingebakken in de firmware.

| Merk | Poort | Slave ID |
|---|---|---|
| Huawei SUN2000 | 6607 | 0 |
| Fronius | 502 | 1 |
| Growatt | 502 | 1 |
| Victron Cerbo | 502 | 100 |
| Solax | 502 | 1 |
| Eastron SDM630 | 502 | 1 |

---

## 🧠 EMS Strategieën

| Strategie | Beschrijving |
|---|---|
| **Eigen verbruik** | Zon → huis → Sessy batterij laden → Easee EV laden |
| **Slimme laadpaal** | Easee start/stopt/regelt op basis van zonne-overschot |
| **Sessy sturing** | Laad/ontlaad setpoint automatisch via REST API |
| **Piekbeveiliging** | Waarschuwt en begrens bij overschrijden netlimiet |
| **Warmtepomp** | Activeert WP via HA entiteit op zonne-uren |

---

## ⚙️ Add-on Configuratie

| Optie | Standaard | Beschrijving |
|---|---|---|
| `scan_interval` | `10` | Polling interval in seconden (5–60) |
| `log_level` | `info` | `debug`, `info`, `warning`, `error` |
| `energy_tariff_import` | `0.28` | Importtarief €/kWh |
| `energy_tariff_export` | `0.08` | Exporttarief €/kWh |
| `max_grid_power` | `10000` | Max. netaansluiting in Watt |

---

## 💾 Data opslag

Alles wordt bewaard in `/config/ems/` op je HA host:

| Bestand | Inhoud |
|---|---|
| `devices.json` | Apparaatconfiguraties (incl. credentials) |
| `settings.json` | EMS strategie-instellingen |
| `history.json` | Vermogenshistorie (laatste 24h, 5-min intervallen) |

> ⚠️ `devices.json` bevat wachtwoorden in plaintext. Het bestand staat alleen op je eigen HA installatie.

---

## 🐛 Problemen oplossen

**Add-on start niet:**
→ Controleer de add-on log: Instellingen → Add-ons → EMS → **Log**

**SolarEdge offline:**
→ Modbus TCP aanzetten in SetApp
→ Poort: probeer 1502 én 502
→ Firewall: zorg dat HA de omvormer kan bereiken op die poort

**Sessy offline:**
→ Controleer IP: ping `sessy-XXXX.local` vanuit je netwerk
→ Gebruikersnaam/wachtwoord: precies zoals op de sticker (hoofdlettergevoelig)

**Easee offline:**
→ Controleer je inloggegevens in de Easee app
→ Telefoonnummer formaat: `+31612345678`

**HomeWizard offline:**
→ Lokale API aanzetten in de HomeWizard Energy app
→ IP-adres controleren (zie router of HomeWizard app)

**HA entiteiten niet zichtbaar in formulier:**
→ Wacht tot de add-on volledig gestart is
→ De entiteiten worden live opgehaald uit jouw HA installatie

---

## 📁 Bestandsstructuur repo

```
ha-ems-addon/
├── repository.json                        ← HA add-on store manifest
└── ems-energy-manager/
    ├── config.yaml                        ← Add-on definitie
    ├── Dockerfile                         ← Container build
    ├── build.yaml                         ← Multi-arch config
    └── rootfs/
        ├── app/
        │   ├── backend.py                 ← Hoofd Python backend
        │   ├── drivers/
        │   │   ├── solaredge.py           ← SolarEdge Modbus driver
        │   │   ├── sessy.py               ← Sessy REST driver
        │   │   ├── easee.py               ← Easee Cloud API driver
        │   │   ├── meters.py              ← HomeWizard P1, Sessy P1, HA entiteit
        │   │   ├── modbus_generic.py      ← Generieke Modbus driver
        │   │   └── registry.py            ← Driver factory
        │   └── static/
        │       └── index.html             ← Dashboard frontend
        ├── etc/nginx/http.d/ems.conf      ← Nginx reverse proxy
        └── usr/bin/run.sh                 ← Startup script
```
