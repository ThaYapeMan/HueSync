# HueSync Terminology & Domain Model Recommendations

## 1. Doel

De huidige HueSync-terminologie maakt met name de verhouding tussen `Scene`, `Crossfader` en `Coupling` onnodig moeilijk te begrijpen. De belangrijkste oorzaak is dat de namen niet volledig overeenkomen met wat de objecten functioneel doen.

Dit document legt de aanbevolen terminologie en migratie vast. De centrale keuzes zijn:

- **`Scene` → `Effect`**
- Het huidige technische effect-algoritme → **`EffectType`**
- **`Crossfader` → `EnergyProfile`**
- `Coupling` voorlopig behouden

Het doel is een domeinmodel dat voor gebruikers begrijpelijk is, technisch correct blijft en ruimte biedt voor toekomstige uitbreiding.

---

## 2. Aanbevolen conceptueel model

Het nieuwe model wordt:

```text
Player ──────┐
Zone ────────┤
Analyser ────┼── Coupling ── Energy Profile
             │                    │
             │                    ├── Low-energy Effect
             │                    └── High-energy Effect
             │
             └── audio bepaalt de actuele energie
```

Een **Effect** definieert hoe de verlichting op audio reageert.

Een **Energy Profile** bepaalt hoe HueSync op basis van de actuele muziekenergie automatisch tussen twee Effects mengt.

Een **Coupling** verbindt de audio/player, analyser, Hue-zone en het Energy Profile tot één actieve synchronisatieconfiguratie.

Een korte gebruikersuitleg kan daarom worden:

> **An Effect defines how the lights react to music. An Energy Profile automatically blends between a low-energy and high-energy Effect as the music changes. A Coupling connects that behaviour to an audio source and Hue zone.**

---

## 3. Scene vervangen door Effect

### 3.1 Waarom `Scene` niet optimaal is

In professionele lichtsoftware en Philips Hue wordt een *scene* doorgaans geassocieerd met een opgeslagen lichtbeeld of toestand.

Een HueSync `Scene` is echter geen statische toestand. Het object bevat onder andere:

- effect-algoritme;
- effect speed;
- effect decay;
- sensitivity;
- brightness-instellingen;
- audio-bandinstellingen;
- onset response;
- overige parameters die bepalen hoe de verlichting continu op audio reageert.

Het object beschrijft dus vooral **gedrag**.

### 3.2 Nieuwe naam: `Effect`

Voor de gebruiker sluit `Effect` veel beter aan op de functie.

Voorbeelden:

```text
Effects

Relaxed Swirl
Party Fireworks
Deep Spectrum
Warm Pulses
```

Een gebruiker kan dan natuurlijk zeggen:

> Bij lage energie gebruik ik Relaxed Swirl en bij hoge energie Party Fireworks.

### 3.3 Bestaand technisch `Effect` hernoemen

HueSync gebruikt `Effect` momenteel ook voor het onderliggende effect-algoritme, bijvoorbeeld:

```text
swirl
fireworks
pulses
spectrum
```

Na `Scene → Effect` mogen deze twee concepten niet dezelfde naam houden.

Aanbevolen onderscheid:

```text
EffectType
    ↓
Effect
```

`EffectType` is het algoritme/de implementatie.

`Effect` is een opgeslagen, volledig geconfigureerde instantie daarvan.

Voorbeeld:

```text
Effect
  Name: Relaxed Swirl
  Type: Swirl
  Speed: 25%
  Decay: 70%
  Sensitivity: 60%
  ...
```

Aanbevolen Python-model:

```python
class EffectType(Enum):
    SWIRL = "swirl"
    FIREWORKS = "fireworks"
    PULSES = "pulses"
    SPECTRUM = "spectrum"

@dataclass
class Effect:
    name: str
    effect_type: EffectType
    # configured behaviour parameters
```

Alternatieve interne naam: `EffectDefinition` of `EffectEngine`. `EffectType` heeft de voorkeur omdat het kort en duidelijk is.

---

## 4. Crossfader vervangen door Energy Profile

### 4.1 Waarom `Crossfader` niet goed genoeg is

Een crossfader heeft in audio-, video- en lichtsoftware normaal een vrij specifieke betekenis: een bediening of mechanisme waarmee tussen A en B wordt gemengd.

Conceptueel:

```text
A ───────── [ crossfader ] ───────── B
```

De huidige HueSync `Crossfader` is veel meer dan dat. Het bevat onder andere:

- de configuratie voor rustige passages;
- de configuratie voor energieke passages;
- een lage energiedrempel;
- een hoge energiedrempel;
- smoothing/response van de blend;
- automatische aansturing door de gemeten audio-energie.

De gebruiker bedient dus geen crossfader. HueSync bepaalt continu automatisch de blend.

### 4.2 Nieuwe naam: `EnergyProfile`

`EnergyProfile` beschrijft het domeinconcept beter:

```text
Energy Profile: Party

Low-energy Effect
    Relaxed Swirl

High-energy Effect
    Party Fireworks

Music energy
Low                                      High
 |                                         |
 Relaxed Swirl ─────── blend ─────── Party Fireworks
```

Het Energy Profile definieert daarmee de relatie:

```text
music energy → effect blend
```

### 4.3 Waarom niet `Transition`

`Transition` impliceert een overgang van toestand A naar toestand B die uiteindelijk wordt voltooid.

HueSync kan daarentegen langere tijd bijvoorbeeld op:

```text
40% Low-energy Effect
60% High-energy Effect
```

blijven staan en vervolgens weer teruggaan.

Dit is een continue blend, geen klassieke transition.

### 4.4 Waarom niet `Mixer`

Intern is `LayerMixer` een goede naam voor het mechanisme dat twee outputs combineert.

Maar het gebruikersobject configureert niet alleen de mixer. Het bepaalt ook **wanneer en waarom** de mengverhouding verandert.

Daarom:

```text
User/domain concept:  EnergyProfile
Runtime mechanism:    LayerMixer
```

### 4.5 Waarom `EnergyProfile` toekomstvast is

`Crossfader` impliceert twee kanten: A en B.

Een toekomstig Energy Profile kan zonder terminologische problemen worden uitgebreid naar bijvoorbeeld:

```text
0.0 ───── 0.3 ───── 0.7 ───── 1.0
 |          |          |
Ambient    Swirl    Fireworks
```

of:

```text
Low energy      → Ambient Effect
Medium energy   → Swirl Effect
High energy     → Fireworks Effect
Extreme onset   → Flash behaviour
```

`EnergyProfile` blijft dan correct, terwijl `Crossfader` niet meer past.

---

## 5. Active/Mellow vervangen

De huidige termen `Active Scene` en `Mellow Scene` moeten eveneens worden vervangen.

`Active` is problematisch omdat het in software ook betekent dat een object momenteel geselecteerd, enabled of actief is.

Aanbevolen:

```text
mellow_scene  → low_energy_effect
active_scene  → high_energy_effect
```

User-facing:

```text
Low-energy Effect
High-energy Effect
```

Deze namen beschrijven exact de dimensie waarop HueSync de keuze maakt: gemeten audio-energie.

`Calm Effect` en `Energetic Effect` zijn menselijker alternatieven, maar interpreteren de muziek semantisch. `Low-energy` en `High-energy` zijn daarom technisch zuiverder.

---

## 6. Thresholds hernoemen

Huidig:

```text
low_threshold
high_threshold
```

Aanbevolen domain/API-termen:

```text
blend_start
blend_end
```

Betekenis:

- **Blend start**: onder dit energieniveau wordt volledig het Low-energy Effect gebruikt.
- **Blend end**: boven dit energieniveau wordt volledig het High-energy Effect gebruikt.
- Tussen beide waarden wordt proportioneel gemengd.

In de UI bij voorkeur visueel presenteren:

```text
Music energy

Low                                                    High
|                                                       |
0%        30%                           70%            100%
|─────────|═════════════════════════════|────────────────|
          ↑                             ↑
       Blend start                   Blend end

Low-energy Effect       BLEND          High-energy Effect
```

Hierdoor hoeft de gebruiker het onderliggende algoritme niet te begrijpen.

---

## 7. `fade_speed` hernoemen

De huidige `fade_speed` is feitelijk geen klassieke fade-duration, maar smoothing van de blend weight.

Daarom is `fade_speed` misleidend.

Aanbevolen code/domain-term:

```text
blend_response
```

Als de implementatie later expliciet tijdconstant-gebaseerd wordt, kan intern een technische parameter zoals:

```text
blend_tau_s
```

worden gebruikt.

User-facing hoeft de numerieke EMA- of tau-waarde niet zichtbaar te zijn.

Gebruik bijvoorbeeld:

```text
Response

Smooth  ─────────●──────── Fast
```

met uitleg:

> Controls how quickly the lighting mix responds to changes in music energy.

---

## 8. Aanbevolen terminologiemapping

| Huidige term | Nieuwe term | Opmerking |
|---|---|---|
| `Scene` | `Effect` | Definitieve keuze |
| huidig technisch `Effect` | `EffectType` | Algoritme/implementatie |
| `scene_id` | `effect_id` | |
| `Crossfader` | `EnergyProfile` | Automatische energy→blend-configuratie |
| `crossfader_id` | `energy_profile_id` | |
| `mellow_scene_id` | `low_energy_effect_id` | |
| `active_scene_id` | `high_energy_effect_id` | Vermijdt ambiguïteit van “active” |
| `low_threshold` | `blend_start` | |
| `high_threshold` | `blend_end` | |
| `fade_speed` | `blend_response` | Geen klassieke fade |
| `LayerMixer` | `LayerMixer` | Goede interne runtime-term; behouden |
| `mix` | `blend` | Waar mogelijk consistent maken |
| `target_mix` | `target_blend` | Waar mogelijk consistent maken |
| `Coupling` | `Coupling` | Voorlopig behouden |
| `Analyser` | `Analyser` | Voorlopig behouden |
| `Zone` | `Zone` | Behouden |

---

## 9. Gewenste hiërarchie

De belangrijkste gebruikersentiteiten worden:

```text
Effect
Energy Profile
Coupling
```

Hun verhouding:

```text
                       ENERGY PROFILE
                            │
               ┌────────────┴────────────┐
               │                         │
               ▼                         ▼
      LOW-ENERGY EFFECT          HIGH-ENERGY EFFECT
               │                         │
               ▼                         ▼
          EffectType                EffectType
             Swirl                   Fireworks
```

Een concreet voorbeeld:

```text
Energy Profile: Dynamic Party

Low-energy Effect
    Relaxed Swirl
        Effect Type: Swirl

High-energy Effect
    Party Fireworks
        Effect Type: Fireworks
```

---

## 10. Coupling in het model

`Coupling` kan voorlopig blijven bestaan als het configuratie-object dat de benodigde onderdelen aan elkaar koppelt.

Conceptueel:

```text
Player ──────────┐
                 │
Analyser ────────┤
                 ├── Coupling ── Energy Profile
Hue Zone ────────┤
                 │
Controller ──────┘
```

Een Coupling zegt daarmee in essentie:

> Gebruik deze audiobron en analyser om dit Energy Profile op deze Hue-zone uit te voeren.

De term `Coupling` kan later afzonderlijk worden geëvalueerd. Deze migratie hoeft daar niet op te wachten.

---

## 11. UX-aanbeveling

De UI moet gebruikers niet verplichten eerst het volledige entity-model te begrijpen.

Vermijd een workflow die voelt als:

```text
1. Create Scene
2. Create another Scene
3. Create Crossfader
4. Select both Scenes
5. Create Coupling
6. Select Crossfader
```

Presenteer in een Coupling of vergelijkbare hoofdconfiguratie liever direct:

```text
Living Room
────────────────────────────────

Audio source
Living Room Player

Hue zone
Living Room

Analysis
Default

Lighting
Dynamic Party                         Edit >

    Low energy
    Relaxed Swirl

    ─────────── 30% ───── 70% ───────────

    High energy
    Party Fireworks
```

`Dynamic Party` is hier het Energy Profile.

De gebruiker kan via **Edit** de onderliggende Effects en blend-instellingen wijzigen.

---

## 12. Effect-editor

Een Effect-editor kan bijvoorbeeld worden opgebouwd als:

```text
EDIT EFFECT
────────────────────────────────

Name
Relaxed Swirl

Effect Type
Swirl

Speed
────●────

Decay
──────●──

Sensitivity
───●─────

Brightness
...

Audio response
...
```

Hierdoor is voor de gebruiker vanzelf duidelijk dat `Swirl` het soort effect is en `Relaxed Swirl` zijn geconfigureerde Effect.

---

## 13. Energy Profile-editor

Aanbevolen concept:

```text
ENERGY PROFILE
────────────────────────────────

Name
Dynamic Party

Low-energy Effect
Relaxed Swirl

High-energy Effect
Party Fireworks

Music energy
Low                                      High
|                                          |
|───────●════════════════════════●─────────|
        30%                      70%
     Blend start              Blend end

Response
Smooth ─────────●──────── Fast
```

De essentie is visueel direct zichtbaar: de muziekenergie beweegt de output tussen twee Effects.

---

## 14. Scheiding tussen domein en runtime

Niet ieder intern technisch begrip hoeft user-facing te zijn.

Aanbevolen scheiding:

```text
DOMAIN / UI                       RUNTIME / DSP
────────────────────────────────────────────────
Effect                            EffectType implementation
EnergyProfile                     energy calculation
Blend start/end                   target_blend
Response                          blend smoothing
                                 LayerMixer
```

`Blend` blijft daarmee de correcte naam voor het feitelijke mengproces.

`EnergyProfile` is de configuratie/policy die bepaalt hoe die blend wordt aangestuurd.

Dit voorkomt dat een implementatiedetail (`Crossfader`, `LayerMixer`) als gebruikersconcept wordt gepresenteerd.

---

## 15. Migratiestrategie

### Fase 1 — Domain model

Introduceer de nieuwe namen in models en interne interfaces:

```text
Scene             → Effect
Crossfader        → EnergyProfile
mellow_scene_id   → low_energy_effect_id
active_scene_id   → high_energy_effect_id
low_threshold     → blend_start
high_threshold    → blend_end
fade_speed        → blend_response
```

Hernoem tegelijkertijd het bestaande technische effectconcept naar `EffectType` of een vergelijkbare ondubbelzinnige naam.

### Fase 2 — Storage/API compatibility

Bestaande configuraties mogen bij voorkeur niet kapotgaan.

Mogelijke aanpak:

1. lees tijdelijk zowel oude als nieuwe veldnamen;
2. converteer naar het nieuwe interne model;
3. schrijf alleen het nieuwe formaat;
4. verhoog indien aanwezig de config/schema-versie;
5. voeg migratietests toe voor bestaande configuratiebestanden.

### Fase 3 — REST API

Nieuwe resources bijvoorbeeld:

```text
/api/effects
/api/energy-profiles
/api/couplings
```

Vervang oude identifiers in request/response-modellen door de nieuwe namen.

Indien backward compatibility belangrijk is, kunnen oude endpoints tijdelijk deprecated aliases zijn.

### Fase 4 — UI

Pas labels, navigatie en editors aan naar:

```text
Effects
Energy Profiles
Couplings
```

Gebruik `Effect Type` alleen waar de gebruiker daadwerkelijk het algoritme kiest.

### Fase 5 — documentatie

Werk README, architectuurdiagrammen, API-documentatie en voorbeelden bij.

Vervang vooral uitleg zoals:

```text
Crossfader links an Active Scene and Mellow Scene
```

met:

> An Energy Profile automatically blends between a low-energy and high-energy Effect according to music energy.

---

## 16. Tests bij de migratie

Voeg minimaal tests toe voor:

- oude `Scene`-configuratie → nieuw `Effect`;
- oude `Crossfader` → nieuw `EnergyProfile`;
- behoud van IDs/referenties tijdens migratie;
- `low_energy_effect_id` en `high_energy_effect_id` resolutie;
- correcte blend onder `blend_start`;
- correcte blend boven `blend_end`;
- interpolatie tussen beide grenzen;
- `blend_response`-gedrag;
- serialization/deserialization van het nieuwe model;
- API backward compatibility indien die tijdelijk wordt ondersteund.

De terminologiemigratie mag geen verandering van het feitelijke lichtgedrag veroorzaken. Gedragswijzigingen kunnen beter in aparte commits worden uitgevoerd.

---

## 17. Aanbevolen commit-opdeling

Houd naming-refactoring en gedragswijzigingen uit elkaar.

Bijvoorbeeld:

```text
1. refactor: rename internal effect implementation to EffectType
2. refactor: rename Scene domain model to Effect
3. refactor: replace Crossfader with EnergyProfile
4. refactor: rename energy-profile fields
5. migrate: support legacy Scene/Crossfader configuration
6. api: expose Effects and Energy Profiles
7. ui: adopt Effect/Energy Profile terminology
8. docs: update domain model and terminology
```

Dit maakt regressies en reviews veel eenvoudiger.

---

## 18. Terminologie die niet wordt aanbevolen

### `Scene Blend`

Beter dan `Crossfader`, maar beschrijft voornamelijk het mengmechanisme en niet de volledige energy→behaviour policy.

### `Transition`

Niet gebruiken. HueSync voert geen eenmalige A→B-overgang uit; de blend kan continu heen en weer bewegen.

### `Mixer`

Geschikt als runtime-concept (`LayerMixer`), niet als hoofdentiteit voor gebruikers.

### `Mood`

Te subjectief. HueSync meet audio-eigenschappen zoals energie, niet de emotionele betekenis van muziek.

### `Lighting Style`

Een goede kandidaat en duidelijker dan `Scene`, maar `Effect` is korter, natuurlijker voor HueSync en sluit beter aan bij hoe gebruikers het gedrag waarschijnlijk beschrijven.

### `Active Effect`

Vermijden voor de energieke kant. `Active` kan ook betekenen dat het Effect momenteel actief/enabled is.

---

## 19. Definitieve aanbeveling

Gebruik het volgende domeinmodel:

```text
EffectType
    ↓
Effect
    ↓
EnergyProfile
    ↓
Coupling
```

Waarbij:

### EffectType
Het technische effect-algoritme, bijvoorbeeld `Swirl`, `Fireworks`, `Pulses` of `Spectrum`.

### Effect
Een opgeslagen configuratie die bepaalt hoe de lampen reageren, bijvoorbeeld `Relaxed Swirl` of `Party Fireworks`.

### EnergyProfile
Een configuratie die op basis van muziekenergie automatisch tussen Effects mengt.

```text
EnergyProfile
 ├── low_energy_effect_id
 ├── high_energy_effect_id
 ├── blend_start
 ├── blend_end
 └── blend_response
```

### Coupling
Verbindt bron/player, analyser, Energy Profile en Hue-zone voor daadwerkelijke uitvoering.

De kernrelatie kan uiteindelijk overal in HueSync worden uitgelegd als:

> **An Effect defines how the lights react to music. An Energy Profile automatically blends between a low-energy and high-energy Effect as the music changes. A Coupling connects that behaviour to an audio source and Hue zone.**

---

## 20. Eindbeeld

```text
                         COUPLING
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
      Player             Analyser             Zone
                            │
                            ▼
                     MUSIC ENERGY
                            │
                            ▼
                     ENERGY PROFILE
                            │
              ┌─────────────┴─────────────┐
              │                           │
              ▼                           ▼
      LOW-ENERGY EFFECT           HIGH-ENERGY EFFECT
       "Relaxed Swirl"            "Party Fireworks"
              │                           │
              ▼                           ▼
        EffectType                   EffectType
           Swirl                       Fireworks
              │                           │
              └──────────┬────────────────┘
                         ▼
                     LayerMixer
                         │
                         ▼
                    Hue output
```

Dit model maakt onderscheid tussen:

- **wat** het lichtgedrag is (`Effect`);
- **welk algoritme** daarvoor wordt gebruikt (`EffectType`);
- **hoe muziekenergie Effects selecteert/mengt** (`EnergyProfile`);
- **waar en voor welke bron het wordt uitgevoerd** (`Coupling`);
- **hoe de daadwerkelijke menging technisch gebeurt** (`LayerMixer`).

Daarmee verdwijnt de ambiguïteit van zowel `Scene` als `Crossfader`, terwijl het model tegelijkertijd beter uitbreidbaar wordt.
