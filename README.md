# EMS Energy Manager — Home Assistant Add-on

Een volledig energiebeheersysteem als Home Assistant add-on.

---

## 🚀 Installeren via GitHub

### Stap 1 — Repository aanmaken op GitHub

1. Ga naar [github.com/new](https://github.com/new)
2. Naam: `ha-ems-addon`
3. Zet op **Public** (verplicht voor HA)
4. Klik **Create repository**

### Stap 2 — Bestanden uploaden

Upload de volgende mapstructuur **exact zo** naar je repository:

```
ha-ems-addon/                          ← root van je GitHub repo
├── repository.json                    ← verplicht voor HA add-on store
└── ems-energy-manager/                ← de add-on map
    ├── config.yaml
    ├── Dockerfile
    ├── build.yaml
    └── rootfs/
        ├── app/
        │   ├── backend.py
        │   └── static/
        │       └── index.html
        ├── etc/
        │   └── nginx/
        │       └── http.d/
        │           └── ems.conf
        └── usr/
            └── bin/
                └── run.sh
```

> **Tip**: Sleep de uitgepakte map gewoon naar de GitHub upload-pagina
> (github.com → jouw repo → "uploading an existing file")

### Stap 3 — Toevoegen aan Home Assistant

1. Ga in HA naar **Instellingen → Add-ons → Add-on Store**
2. Klik rechtsboven op **⋮ (drie puntjes) → Aangepaste opslagplaatsen**
3. Plak je GitHub URL: `https://github.com/JOUW_NAAM/ha-ems-addon`
4. Klik **Toevoegen** → sluit het venster
5. Ververs de pagina — scroll naar beneden naar **"EMS Energy Manager"**
6. Klik **Installeren** (duurt 2-5 min, bouwt de container)
7. Na installatie: klik **Starten**
8. Schakel **"Tonen in zijbalk"** in
9. Klik op **EMS** in de HA zijbalk → dashboard opent!

---

## ⚙️ Configuratie (optioneel)

Pas aan via **Add-on → Configuratie**:

| Optie | Standaard | Beschrijving |
|---|---|---|
| `mqtt_host` | `homeassistant` | MQTT broker adres |
| `mqtt_port` | `1883` | MQTT poort |
| `mqtt_username` | `` | MQTT gebruiker |
| `mqtt_password` | `` | MQTT wachtwoord |
| `scan_interval` | `10` | Polling interval (sec) |
| `energy_tariff_import` | `0.28` | Importtarief €/kWh |
| `energy_tariff_export` | `0.08` | Exporttarief €/kWh |
| `max_grid_power` | `10000` | Max. net vermogen (W) |
| `log_level` | `info` | Log niveau |

---

## 📡 Ondersteunde apparaten

| Type | Protocol | Merken |
|---|---|---|
| Thuisbatterij | Modbus TCP | Huawei, BYD, Victron, Solax |
| Zonnepanelen | Modbus TCP | SolarEdge, Fronius, Growatt, Huawei |
| Laadpaal (EV) | OCPP 1.6 | Alfen, Wallbox, ABB, EVBox, Easee |
| Warmtepomp | Modbus TCP | Daikin, Nibe, Bosch, Vaillant |
| Boiler | Modbus/MQTT | Bosch, Vaillant, Wolf |
| Slimme meter | RS485/MQTT | Eastron, Kamstrup, DSMR P1 |
| Omvormer | SunSpec | Universeel |
| HA Entiteit | HA API | Elke sensor/number entiteit |

---

## 🧠 EMS Strategieën

| Strategie | Beschrijving |
|---|---|
| Eigen verbruik | Zon → huis → batterij → laadpaal, minimaal netgebruik |
| Slimme laadpaal | EV laden alleen bij ≥1.4kW zonne-overschot |
| Dynamische tarieven | Optimaliseer op ENTSO-E spotprijzen |
| Piekbeveiliging | Begrens netafname voor capaciteitstarief |
| Warmtepomp sturing | WP activeren tijdens zonne-uren |
| V2G | Auto-energie terugleveren aan net |

---

## 💾 Data opslag

Alle data wordt bewaard in `/config/ems/` op je HA systeem:
- `devices.json` — apparaatconfiguraties
- `settings.json` — EMS instellingen
- `history.json` — vermogenshistorie (24h)

---

## 🐛 Problemen?

**Add-on niet zichtbaar na toevoegen URL:**
→ Ververs de pagina, controleer of repo **Public** is

**Installatie mislukt:**
→ Controleer de add-on log (Instellingen → Add-ons → EMS → Log)

**Dashboard niet bereikbaar:**
→ Zorg dat "Tonen in zijbalk" aanstaat en de add-on draait

**Apparaat offline na toevoegen:**
→ Controleer IP, poort en of Modbus TCP actief is op het apparaat
→ Huawei: poort 6607, slave 0 | SolarEdge: poort 502, slave 1
