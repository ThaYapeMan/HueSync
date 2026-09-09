# HueSync — kritische product- en UI-review

**Repository:** https://github.com/ThaYapeMan/HueSync  
**Reviewdatum:** 9 september 2026  
**Focus:** domeinmodel, informatiearchitectuur, begrijpelijkheid, grafische interface, live-bediening en toekomstvastheid.

---

## Executive summary

De nieuwe naam **HueSync** en de migratie van **Scene → Effect** en **Crossfader → Energy Profile** zijn duidelijke verbeteringen. Het domeinmodel klopt nu veel beter met wat het systeem werkelijk doet:

```text
Audio source
    ↓
Virtual Player
    ↓
Analyser
    ↓
Energy Profile
    ├── Low-energy Effect
    └── High-energy Effect
    ↓
Zone
    ↓
Hue lights
```

De backend en README communiceren dit nieuwe model al redelijk consequent. De grootste zwakte zit nu niet meer in de architectuur, maar in de **user interface**: die presenteert een sterk visueel en dynamisch systeem nog hoofdzakelijk als een beheerapplicatie met tabellen, dropdowns, modals en numerieke invoervelden.

Dat is precies het verkeerde abstractieniveau voor HueSync. De gebruiker wil primair begrijpen:

1. **Waar komt de muziek vandaan?**
2. **Wat hoort de analyser?**
3. **Welk Effect is nu dominant?**
4. **Waarom schakelt/mengt het systeem tussen Effects?**
5. **Welke lamp reageert waarop?**
6. **Wat verandert er als ik deze instelling aanpas?**

Die vragen moeten in de interface **zichtbaar** worden gemaakt, niet alleen in tekst worden uitgelegd.

Mijn hoofdadvies is daarom:

> Maak van HueSync geen CRUD-interface voor zeven entiteiten, maar een **live audiovisuele control surface** met een visueel signaalpad, directe preview, grafische Energy Profiles, visuele Effect-cards en een ruimtelijke Zone-weergave.

De huidige pagina's `Effects`, `Energy Profiles`, `Analysers`, `Zones`, `Virtual Players` en `Couplings` mogen als beheerniveau blijven bestaan, maar moeten secundair worden. De primaire ervaring moet draaien om **Live**, **Shows/Couplings** en een grafische **Designer**.

---

# 1. Wat nu al sterk is

## 1.1 Het nieuwe domeinmodel is veel begrijpelijker

De README definieert nu zes functionele concepten rond een Coupling:

- Controller
- Virtual Player
- Zone
- Analyser
- Effect
- Energy Profile
- Coupling

Een `EnergyProfile` verwijst expliciet naar een low-energy en high-energy Effect en mengt ertussen op basis van muziekenergie. Dat is semantisch veel beter dan het oude Crossfader-model.

Bron:  
https://github.com/ThaYapeMan/HueSync#architecture--six-entity-model

De API ondersteunt inmiddels ook de nieuwe namen:

```text
/api/effects
/api/energy-profiles
```

waarbij `/api/scenes` en `/api/crossfaders` alleen nog deprecated aliases zijn.

Dat is een goede migratiestrategie: nieuwe terminologie in het product zonder bestaande clients abrupt te breken.

---

## 1.2 De DSP-architectuur is goed gescheiden van rendering

HueSync heeft een duidelijke bron → analyse → features → effect → output-keten. LMS/cava en AirPlay/PCM kunnen verschillende ingestion-routes gebruiken terwijl de rendering uiteindelijk via dezelfde concepten loopt.

Dat maakt een grafische interface juist kansrijk: je hebt al echte modules die visueel als blokken kunnen worden weergegeven.

---

## 1.3 De huidige UI heeft al live data

`NowPlaying` ontvangt onder andere:

- spectrum bars;
- kleur/output;
- channel colours;
- onset;
- bass/mid/treble onset;
- mix;
- runtime status.

Dat betekent dat de belangrijkste data voor een grafische live-interface **al beschikbaar is via WebSocket**. Er hoeft dus niet eerst een compleet nieuw backendmodel gebouwd te worden.

Bron:  
`web/src/App.tsx` en `web/src/pages/NowPlaying.tsx`

---

## 1.4 Het project heeft al goede bouwstenen voor progressive disclosure

De Effect-editor toont alleen relevante parameters wanneer een Effect Type die ondersteunt. Bijvoorbeeld speed en decay worden conditioneel getoond.

Dat is precies de juiste richting: niet ieder DSP-veld overal tonen.

---

# 2. Grootste problemen in de huidige interface

## 2.1 Het hoofdmenu weerspiegelt de database, niet de taak van de gebruiker

De huidige navigatie is:

```text
Now Playing
Couplings
Analysers
Effects
Energy Profiles
Zones
Virtual Players
Latency
```

Dit is logisch voor een ontwikkelaar die de objectstructuur kent, maar niet voor iemand die HueSync wil gebruiken.

De gebruiker denkt waarschijnlijk eerder in:

```text
Wat draait er nu?
Hoe wil ik dat mijn kamer reageert?
Welke kamer?
Welke audiobron?
Waarom reageert dit Effect zo?
```

Niet in:

```text
Ik ga nu een EnergyProfile record aanpassen.
```

### Aanbeveling

Breng de hoofdstructuur terug naar drie primaire domeinen:

```text
LIVE
SHOWS
SETUP
```

Met daaronder bijvoorbeeld:

```text
LIVE
  Now Playing

SHOWS
  Couplings / Shows
  Effects
  Energy Profiles

SETUP
  Audio Sources / Players
  Analysers
  Zones
  Bridges
  Latency
```

Nog beter is `Coupling` in de UI uiteindelijk **Show** of **Setup** te noemen, terwijl `Coupling` intern kan blijven bestaan. Zie §7.

---

## 2.2 De interface is te veel tabel + modal

Effects en Energy Profiles worden momenteel als tabellen gepresenteerd.

Een Effect wordt bijvoorbeeld gereduceerd tot:

```text
Name | Effect Type | Sensitivity | Brightness Floor | Actions
```

Een Energy Profile tot:

```text
Name | High-energy effect | Low-energy effect | Blend range | Blend response
```

Dit toont de data, maar **niet de betekenis**.

Voor HueSync zijn juist deze dingen belangrijk:

- hoe ziet het Effect eruit?
- hoe beweegt het?
- hoe fel is het?
- waar reageert het op?
- wanneer neemt low-energy over?
- wanneer wordt high-energy dominant?
- waar zit de muziekenergie nu?

Een tabel is daar bijna de slechtst mogelijke visualisatie voor.

---

## 2.3 De nieuwe terminologie is nog niet volledig gemigreerd

Dit moet eerst worden opgelost.

In `NowPlaying.tsx` staan nog:

```text
Mellow
Active
Active scene
Crossfader
```

terwijl het nieuwe model juist is:

```text
Low energy
High energy
Effect
Energy Profile
```

Dit is meer dan cosmetiek. Een gebruiker kan hierdoor denken dat `Active` betekent “het Effect dat nu actief is”, terwijl het oorspronkelijk “high-energy side” betekende.

### Direct vervangen

```text
Mellow           → Low energy
Active           → High energy
Active scene     → High-energy Effect
Crossfader       → Energy Profile
Layer mix        → Energy blend
```

---

# 3. Ontwerpprincipe: maak het signaalpad zichtbaar

De belangrijkste grafische verbetering is dat HueSync zijn eigen architectuur moet **tekenen**.

De keten is namelijk begrijpelijk zodra je hem ziet.

## Mockup 1 — primaire Live-pagina

```text
┌────────────────────────────────────────────────────────────────────────────────────┐
│ HueSync                                                     ● LIVE   Living Room    │
├────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                    │
│  AUDIO                ANALYSIS                 ENERGY                 LIGHTS         │
│                                                                                    │
│  ┌──────────────┐     ┌──────────────────┐     ┌──────────────┐      ┌───────────┐  │
│  │ Sonos Living │ ──▶ │ Spectrum         │ ──▶ │  ENERGY  62% │ ──▶  │  ●   ●    │  │
│  │ via LMS      │     │ ▁▂▄▇▆▃▂▅▇▃      │     │ ██████░░░░   │      │    ●   ●  │  │
│  │ Playing ●    │     │ kick ●           │     └──────┬───────┘      │ ●         │  │
│  └──────────────┘     │ bass ▂▅█         │            │              └───────────┘  │
│                       └──────────────────┘             ▼                           │
│                                               ┌──────────────────────┐             │
│                                               │ ENERGY PROFILE       │             │
│                                               │ Dynamic Party        │             │
│                                               │                      │             │
│                                               │ Calm Swirl       38% │             │
│                                               │ ████████░░░░░░░░░░   │             │
│                                               │ Party Fireworks  62% │             │
│                                               │ ████████████░░░░░░   │             │
│                                               └──────────────────────┘             │
│                                                                                    │
│  CURRENT OUTPUT                                                                    │
│  ┌──────────────────────────────────────────────────────────────────────────────┐  │
│  │ ● Left lamp       ● TV strip        ● Floor lamp        ● Right lamp        │  │
│  │   RGB preview       RGB preview       RGB preview          RGB preview       │  │
│  └──────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                    │
└────────────────────────────────────────────────────────────────────────────────────┘
```

### Waarom dit beter is

De gebruiker ziet in één scherm:

- bron is actief;
- analyser ontvangt audio;
- huidige energie;
- welk Energy Profile draait;
- de echte blend tussen twee Effects;
- output per lichtpositie.

Dit is **causaliteit zichtbaar maken**.

Dat is voor HueSync belangrijker dan nog meer helpteksten.

---

# 4. Energy Profile moet een grafiek/editor worden

Dit is de grootste concrete UI-kans.

Nu voert de gebruiker in:

```text
blend_start = 0.3
blend_end = 0.7
blend_response = 0.1
```

Dat zijn implementatiewaarden.

De betekenis is grafisch:

```text
low Effect ──────── blend zone ──────── high Effect
```

Dat moet letterlijk op het scherm staan.

## Mockup 2 — Energy Profile editor

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ Energy Profile                                                     ● LIVE    │
│ Dynamic Party                                                               │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│ LOW ENERGY                                              HIGH ENERGY           │
│                                                                              │
│ ┌────────────────────┐                             ┌────────────────────┐     │
│ │  🌊 Calm Swirl     │                             │  ✦ Party Fireworks│     │
│ │                    │                             │                    │     │
│ │  ~ ~ ~ ~ ~         │                             │   *   ✦  *        │     │
│ │ blue / smooth      │                             │ orange / punchy    │     │
│ │        Edit ›      │                             │          Edit ›    │     │
│ └─────────┬──────────┘                             └─────────┬──────────┘     │
│           │                                                  │                │
│           └─────────────────────┬────────────────────────────┘                │
│                                 │                                             │
│ Music energy                    ▼                                             │
│                                                                              │
│ 0%          30%                                70%                       100%   │
│ │────────────●══════════════════════════════════●──────────────────────────│   │
│ │ LOW 100%         AUTOMATIC BLEND                HIGH 100%               │   │
│                         ▲                                                    │
│                         │ current: 62%                                       │
│                         ●                                                    │
│                                                                              │
│ Response                                                                     │
│ Smooth  ────────────────●────────────────────────────── Fast                  │
│          settles over ~0.8 s                                                 │
│                                                                              │
│ Preview                                                                      │
│ [ Drag simulated music energy:  ───────────────●──────────── ]               │
│                                                                              │
│                         [ Save ]                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Belangrijke wijzigingen

#### Geen losse numerieke thresholdvelden als primaire control

Gebruik een **dual-handle range slider**:

```text
0 ───────●══════════════════●──────── 1
         start              end
```

De blendzone wordt visueel gekleurd.

Numerieke waarden mogen beschikbaar blijven in **Advanced**.

#### Toon de actuele energie live

Als het profiel op de actieve Coupling wordt gebruikt:

```text
                  ▲
               current
```

beweegt live over dezelfde balk.

Hierdoor begrijpt de gebruiker onmiddellijk waarom een bepaald Effect dominant is.

#### Maak response tijdgebaseerd

In plaats van:

```text
EMA smoothing = 0.1
```

toon:

```text
Response
Smooth ─────●──── Fast

~0.8 sec
```

Intern kan de exacte coefficient/tijdconstante nog bestaan.

---

# 5. Effects moeten visuele kaarten worden

De Effect-catalogus is bij uitstek visueel:

- Spectrum RGB
- Spatial Spectrum
- Mono Pulse
- Pulses
- Flashes
- Splotches
- Fireworks
- Swirl
- Wave
- Solid

Een dropdown of tabel doet hier veel informatie verloren gaan.

## Mockup 3 — Effects gallery

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ Effects                                                    + Create Effect   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  MY EFFECTS                                                                 │
│                                                                             │
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐ │
│  │ ≋ ≋ ≋  Calm Swirl   │  │  ✦ * Party        │  │ ███ Deep Spectrum   │ │
│  │                     │  │      Fireworks      │  │                     │ │
│  │  ● ● ● ●            │  │ *     ✦     *       │  │ ▂▅█▇▃▆             │ │
│  │                     │  │                     │  │ R  G  B             │ │
│  │ Swirl               │  │ Fireworks           │  │ Spectrum RGB        │ │
│  │ Smooth · 65% sens.  │  │ Punchy · 90% sens. │  │ Balanced            │ │
│  │                     │  │                     │  │                     │ │
│  │ [Preview]   [Edit]  │  │ [Preview]   [Edit] │  │ [Preview]   [Edit] │ │
│  └─────────────────────┘  └─────────────────────┘  └─────────────────────┘ │
│                                                                             │
│  EFFECT TYPES                                                               │
│                                                                             │
│  Spectrum        Pulses       Fireworks        Swirl        Wave            │
│  [animated]      [animated]   [animated]       [animated]   [animated]      │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Effect Type kiezen

De huidige card-picker is al beter dan een selectbox. Ga één stap verder:

- geef iedere Effect Type een mini-animatie;
- gebruik een simpele virtuele rij lampen;
- speel een gestandaardiseerde test-energy/onset af;
- laat de gebruiker **zien** wat “Pulses” versus “Fireworks” doet.

Dit hoeft geen zware WebGL-simulatie te zijn. Een Canvas/SVG-preview met 5–8 lichtpunten is genoeg.

---

# 6. Geef ieder Effect een interactieve preview

Nu moet iemand instellingen veranderen en daarna naar echte lampen kijken.

Voor parameters zoals:

- speed;
- decay;
- sensitivity;
- brightness floor;
- onset flash intensity;

zou de editor een lokale simulatie moeten hebben.

## Mockup 4 — Effect editor

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ Edit Effect — Party Fireworks                                      ● LIVE    │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│ PREVIEW                                      CONTROLS                        │
│                                                                              │
│ ┌─────────────────────────────────────┐      Effect type                     │
│ │                                     │      [ Fireworks              ▾ ]     │
│ │          ✦                          │                                      │
│ │    *            ●                   │      Sensitivity                     │
│ │             ✧         *             │      Low ─────────●────── High       │
│ │  ●                       ✦          │                                      │
│ │         *                           │      Burst speed                     │
│ │                    ●                │      Slow ───────────●──── Fast       │
│ │                                     │                                      │
│ └─────────────────────────────────────┘      Decay                           │
│                                            Long ───────●──────── Short        │
│ Audio simulation                                                              │
│ [ calm ] [ groove ] [ beat-heavy ] [ LIVE ]  Brightness floor               │
│                                            Dark ───●──────────── Bright      │
│                                                                              │
│                                            ▸ Advanced audio mapping           │
│                                                                              │
│                       [ Cancel ] [ Save ]                                     │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Belangrijk UX-principe

Gebruik **menselijke labels** aan de buitenkant van sliders:

```text
Long decay                      Short decay
Smooth response                 Fast response
Dark                            Bright
Subtle                          Reactive
```

Toon technische waardes alleen:

- in een tooltip;
- naast de slider in klein formaat;
- of in Advanced mode.

---

# 7. `Coupling` is technisch correct, maar nog steeds moeilijk voor eindgebruikers

De gebruiker moet een Coupling maken die bestaat uit:

- Virtual Player
- Zone
- Analyser
- Energy Profile

Dat is logisch in de code, maar het woord **Coupling** blijft abstract.

Ik zou twee niveaus onderscheiden:

```text
Internal/domain: Coupling
UI-facing:       Show
```

of:

```text
Internal/domain: Coupling
UI-facing:       Sync Setup
```

Mijn voorkeur is **Show** wanneer de configuratie vooral creatief wordt gebruikt.

Voorbeelden:

```text
Living Room — Party
Living Room — Relaxed
Kitchen — Dinner
Office — Focus
```

Dan zegt de gebruiker:

> Start “Living Room — Party”

in plaats van:

> Activate Coupling 2.

Als je `Coupling` wilt behouden, geef het minstens een user-facing ondertitel:

```text
Couplings
Audio-to-light setups
```

---

# 8. Maak de Coupling-editor een visuele pipeline

De huidige editor is een verticale reeks dropdowns:

```text
Virtual Player [select]
Zone           [select]
Analyser       [select]
Energy Profile [select]
```

Dit is functioneel, maar de relaties zijn onzichtbaar.

## Mockup 5 — Show/Coupling designer

```text
┌───────────────────────────────────────────────────────────────────────────────┐
│ Living Room — Party                                             ● Running    │
├───────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│  SOURCE              UNDERSTAND             REACT                 OUTPUT       │
│                                                                               │
│ ┌───────────────┐    ┌───────────────┐    ┌────────────────┐    ┌───────────┐ │
│ │ ♪ Living Room │ ─▶ │ ◫ Music       │ ─▶ │ ⚡ Dynamic     │ ─▶ │ ⌂ Living  │ │
│ │ Sonos         │    │ analyser      │    │ Party          │    │ Room      │ │
│ │               │    │               │    │                │    │ 4 lights  │ │
│ │ LMS follower  │    │ SuperFlux     │    │ Calm Swirl     │    │           │ │
│ │       Edit ›  │    │ HPSS off      │    │ ↔ Fireworks    │    │   Edit ›  │ │
│ └───────────────┘    └───────────────┘    └────────────────┘    └───────────┘ │
│                                                                               │
│                          LIVE SIGNAL                                           │
│          ▁▂▂▄▇▅▂        kick ●         energy 62%       ● ● ● ●             │
│                                                                               │
│ [ Stop ]                                  [ Duplicate ] [ Edit details ]       │
└───────────────────────────────────────────────────────────────────────────────┘
```

Dit beeld maakt de architectuur vrijwel zonder documentatie begrijpelijk.

### Interactie

Klik op een blok om het te vervangen:

```text
[Dynamic Party ▼]
```

Hover toont samenvatting.

`Edit ›` opent een side panel in plaats van een stapel modals.

---

# 9. Gebruik side panels in plaats van modals voor complex editorwerk

De huidige interface gebruikt veel Dialogs.

Voor simpele acties is dat prima, maar voor Effect/Analyser/Energy Profile-editing is een modal nadelig:

- de gebruiker verliest context;
- live preview verdwijnt achter het venster;
- geneste “+ New” dialogs maken de hiërarchie moeilijk;
- vergelijking tussen bron en resultaat is lastig.

### Aanbevolen patroon

Desktop:

```text
┌──────────────────────────────┬──────────────────────┐
│ Main visual workspace        │ Inspector            │
│                              │                      │
│ Energy Profile graph         │ Name                 │
│ Live signal                  │ Response             │
│ Preview                      │ Advanced settings    │
│                              │                      │
└──────────────────────────────┴──────────────────────┘
```

Mobile:

- dezelfde inspector als bottom sheet.

Dit patroon wordt veel gebruikt in grafische editors, DAWs en node-based tools omdat de gebruiker **object + eigenschappen tegelijk** ziet.

---

# 10. Maak Now Playing het echte hart van HueSync

De huidige `NowPlaying` is functioneel al de interessantste pagina:

- active coupling;
- live preview;
- spectrum;
- layer mix;
- band boundaries;
- frequency cutoffs;
- status.

Maar conceptueel zijn er twee soorten informatie door elkaar:

### Performance / creative

- huidige kleur;
- output per lamp;
- Energy Profile mix;
- spectrum;
- onset.

### Engineering / diagnostics

- frequency cutoffs;
- band boundaries;
- cava restart;
- statusvelden;
- DSP details.

Die moeten worden gescheiden.

## Nieuwe structuur

```text
LIVE
├── Visual output
├── Music energy
├── Energy Profile
├── Spectrum / onset
└── Quick controls

DIAGNOSTICS
├── Frequency cutoffs
├── Band boundaries
├── Cava/PCM pipeline
├── STFT/onset status
├── latency
└── raw runtime status
```

Een gebruiker moet niet tussen “lichtshow” en “cava restarts briefly” schakelen in dezelfde visuele hiërarchie.

---

# 11. Maak de Zone ruimtelijk

Philips Hue zelf gebruikt ruimtelijke positionering van lampen in een Entertainment Area. Hue beschrijft het plaatsen van lampen op een kaart ten opzichte van de ruimte/speakers als basis voor ruimtelijke audiovisuele synchronisatie.

Bronnen:

- https://www.philips-hue.com/en-us/explore-hue/blog/sync-with-music
- https://www.philips-hue.com/nl-nl/explore-hue/blog/sync-with-music

HueSync heeft al `channel positions` voor de live preview. Gebruik die veel prominenter.

## Mockup 6 — Zone / room view

```text
┌──────────────────────────────────────────────────────────────┐
│ Living Room                                       4 lights   │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│                     FRONT / TV                               │
│                                                              │
│            ● TV Left          ● TV Right                    │
│                                                              │
│                                                              │
│                                                              │
│       ● Floor Lamp                       ● Cabinet            │
│                                                              │
│                        ◉ listener                            │
│                                                              │
│                         BACK                                 │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│ Live colours                                                 │
│ ● #ff3a21   ● #f1a50c   ● #581cff   ● #00a8ff               │
└──────────────────────────────────────────────────────────────┘
```

### Bonus

Laat bij spatial effects zien hoe spectrum mapping loopt:

```text
BASS                    MID                    TREBLE
 red                    green                  blue
  ↓                      ↓                      ↓
●──────────────●──────────────●──────────────●
```

Dat maakt `spectrum_rgb_spatial` onmiddellijk begrijpelijk.

---

# 12. Voeg een “what the system hears” visualisatie toe

HueSync heeft veel geavanceerde DSP:

- spectrum;
- onset;
- multiband onset;
- SuperFlux;
- HPSS;
- harmonic/percussive energy.

De huidige configuratie noemt die dingen, maar visualiseert ze nauwelijks als concept.

Maak een compacte live analyzer:

```text
BASS             MID              TREBLE
████████         █████            ███
kick ●           onset ○          hat ●

Percussive   ████████░░  78%
Harmonic     ██░░░░░░░░  22%
```

Voor HPSS is bijvoorbeeld een simpele tweedelige balk veel begrijpelijker dan alleen:

```text
use_hpss_separation = true
```

---

# 13. Basic / Advanced mode

HueSync heeft inmiddels genoeg parameters dat één interface niet tegelijk optimaal kan zijn voor:

- iemand die gewoon mooi licht wil;
- iemand die onset detection wil tunen;
- iemand die cava/PCM wil vergelijken.

Maak daarom twee informatieniveaus.

## Basic

```text
Effect
Intensity
Speed
Smoothness
Energy blend
Brightness
```

## Advanced

```text
bars
lower_cutoff_freq
higher_cutoff_freq
onset_method
onset_delta
onset_alpha
superflux_mu
superflux_lag
bass_hz
mid_hz
HPSS
exertion_clip
...
```

Belangrijk: dit hoeft geen globale “expert mode” setting te zijn.

Gebruik lokaal:

```text
▸ Advanced
```

zodat complexe secties alleen openen wanneer nodig.

---

# 14. Maak A/B vergelijken een echte productfeature

De README noemt clone van Couplings expliciet als aanbevolen manier voor A/B-tests.

Dat is nuttig voor ontwikkeling, maar de interface kan dit veel beter ondersteunen.

## Mockup 7 — Compare mode

```text
┌──────────────────────────────────────────────────────────────────────┐
│ Compare                                                              │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│ A — Current                         B — Experiment                    │
│ Dynamic Party                       Dynamic Party — fast release      │
│                                                                      │
│ Effect: Fireworks                   Effect: Fireworks                 │
│ Response: 0.8 s                     Response: 0.5 s                   │
│ Sensitivity: 1.0                    Sensitivity: 1.0                  │
│                                                                      │
│              [ A ] ───────●──────── [ B ]                            │
│                                                                      │
│ [Switch to A]                       [Switch to B]                     │
│                                                                      │
│ Live metrics                                                         │
│ energy correlation    0.91                                             │
│ current output        preview A / preview B                          │
└──────────────────────────────────────────────────────────────────────┘
```

Je kunt dit eerst simpel maken:

- duplicate;
- toon verschillen;
- één knop om A/B te activeren.

Geen ingewikkeld experiment-framework nodig.

---

# 15. Laat status en fouten visueel in de pipeline zien

Nu staat veel status in een aparte Status-card.

Beter:

```text
Audio Source        Analyser        Energy Profile       Hue Zone
   ●                   ●                  ●                 ●
 Running             Live               Live            Streaming
```

Bij een fout:

```text
Audio Source        Analyser        Energy Profile       Hue Zone
   ●                   ⚠                  ○                 ○
 Running        No PCM frames        Waiting           Waiting
```

En klik op `⚠`:

```text
No PCM frames received for 2.4 s
Source: /run/huesync/airplay.pcm
[Open diagnostics]
```

De gebruiker ziet dan **waar in de keten** het probleem zit.

Dit is een belangrijke winst van een pipeline-UI: foutlokalisatie wordt gratis begrijpelijk.

---

# 16. Vereenvoudig latency visueel

Latency is nu een losse technische pagina.

Voor de gebruiker betekent latency simpelweg:

> lopen de lampen vóór of achter de muziek?

Maak daarom een calibratie-widget:

```text
LIGHTS
   ●
   │
   │  +1.85 s
   ▼
AUDIO

Lights currently wait 1850 ms before output.

[ -100 ms ]  [ -10 ]  1850 ms  [ +10 ] [ +100 ms ]

         [ Tap when beat and flash align ]
```

Later kan dit zelfs een tap-calibration worden.

De technische strategy (`fixed`, `none`) kan onder Advanced.

---

# 17. Navigation redesign

## Huidig

```text
Now Playing
Couplings
Analysers
Effects
Energy Profiles
Zones
Virtual Players
Latency
```

## Aanbevolen

```text
● LIVE

▣ SHOWS
  Living Room — Party
  Kitchen — Relaxed
  + New Show

✦ LIBRARY
  Effects
  Energy Profiles

⚙ SETUP
  Audio
  Zones & Bridges
  Analysis
  Latency
  Diagnostics
```

Of nog compacter:

```text
Live
Shows
Effects
Setup
```

Energy Profiles hoeven zelfs niet per se een hoofdnavigatie-item te blijven. Ze kunnen primair vanuit een Show en Effect-context worden bewerkt.

---

# 18. Gebruik kleur functioneel

Resolume laat gebruikers interface-elementen kleurcoderen om workflows herkenbaar te maken. QLC+ bouwt zijn Virtual Console eveneens rond visuele widgets en groepering.

Bronnen:

- https://resolume.com/support/en/layouts
- https://docs.qlcplus.org/v5/virtual-console

HueSync kan een vaste semantische kleurtaal gebruiken:

```text
Audio / source         blauw
Analysis               paars
Energy                 amber/geel
Effects                magenta
Hue output             groen
Warning                oranje
Error                  rood
```

Let op: kleur mag nooit het **enige** onderscheid zijn. Combineer met iconen/labels.

De live spectrumkleuren zelf moeten natuurlijk niet door deze UI-semantiek worden vervormd.

---

# 19. Inspiration: wat relevante tools goed doen

## Philips Hue

Hue houdt complexe synchronisatie voor consumenten bewust simpel:

- intensity slider;
- entertainment-area spatial layout;
- directe concepten als subtle ↔ intense;
- de ruimte staat centraal.

HueSync kan technisch veel meer, maar de eerste laag van de UI mag dezelfde eenvoud hebben.

Bron:  
https://www.philips-hue.com/en-us/explore-hue/blog/sync-with-music

---

## QLC+

QLC+ maakt expliciet onderscheid tussen:

- **Edit mode**: functies en widgets bouwen;
- **Operate mode**: de show bedienen.

Dat is een zeer relevant model voor HueSync.

HueSync heeft nu configuratie en bediening door elkaar. Overweeg dezelfde conceptual split:

```text
RUN
DESIGN
```

Bron:  
https://docs.qlcplus.org/v5/virtual-console

---

## Resolume

Resolume gebruikt voortdurend:

- live preview;
- visuele lagen;
- directe controls;
- kleurcodering;
- A/B mixing;
- contextuele property panels.

Belangrijk is vooral: het toont **media en gedrag**, niet primair records en IDs.

Bronnen:

- https://www.resolume.com/support/en/composition
- https://resolume.com/support/en/layouts

---

# 20. Concrete P0/P1/P2-roadmap

## P0 — eerst doen

### P0.1 Terminologie volledig opschonen

Verwijder user-facing resten van:

```text
Scene
Mellow
Active
Crossfader
```

en gebruik consequent:

```text
Effect
Low energy
High energy
Energy Profile
Energy blend
```

### P0.2 Maak Energy Profile grafisch

Bouw:

- Effect-cards links/rechts;
- dual-handle blend range;
- live energy marker;
- response slider met menselijke labels.

Dit geeft waarschijnlijk de grootste UX-winst per ontwikkeluur.

### P0.3 Maak Effects cards in plaats van tabel

Minimaal:

- naam;
- type;
- icoon/miniatuur;
- korte gedragsomschrijving;
- belangrijkste parameters;
- preview/edit.

### P0.4 Reorganiseer Now Playing

Boven:

- source;
- live spectrum;
- energy;
- current Effect blend;
- room output.

Onder een Diagnostics disclosure:

- cutoffs;
- band boundaries;
- raw status.

---

## P1 — daarna

### P1.1 Visuele Coupling/Show pipeline

Vier blokken:

```text
Source → Analyser → Energy Profile → Zone
```

met live status.

### P1.2 Side-panel inspector

Vervang grote editor-dialogs door een inspector naast de grafiek/preview.

### P1.3 Ruimtelijke Zone-view

Gebruik bestaande channel positions om lampen als punten in de ruimte te tekenen.

### P1.4 Effect simulator

SVG/Canvas mini-preview van ieder Effect Type.

### P1.5 Basic/Advanced disclosures

Maak de standaardinterface creatief; DSP-parameters blijven beschikbaar maar secundair.

---

## P2 — verfijning

### P2.1 A/B compare mode

Gebruik bestaande clone-functionaliteit als basis.

### P2.2 Latency calibration UX

Maak latency een hoor/zicht-synchronisatiecontrol in plaats van een technisch formulier.

### P2.3 Presets/templates

Bijvoorbeeld:

```text
Relaxed
Balanced
Party
Beat-heavy
Ambient
```

Een template maakt automatisch:

- twee Effects;
- een Energy Profile;
- redelijke defaults.

### P2.4 Onboarding wizard

```text
1. Choose audio
2. Choose room
3. Choose look
4. Test
5. Done
```

De gebruiker hoeft dan voor een eerste werkende show nooit handmatig door alle entiteiten.

---

# 21. Voorgestelde eindarchitectuur van de UI

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ HueSync                                                      ● Connected    │
├──────────────┬──────────────────────────────────────────────────────────────┤
│              │                                                              │
│ ● LIVE       │  Live workspace                                              │
│              │  source → analyser → energy → effects → room                 │
│ SHOWS        │                                                              │
│              │                                                              │
│ EFFECTS      │                                                              │
│              │                                                              │
│ SETUP        │                                                              │
│              │                                                              │
│              │                                              ┌─────────────┐ │
│              │                                              │ Inspector   │ │
│              │                                              │             │ │
│              │                                              │ Contextual  │ │
│              │                                              │ controls    │ │
│              │                                              │             │ │
│              │                                              └─────────────┘ │
└──────────────┴──────────────────────────────────────────────────────────────┘
```

Dit is een klassiek **workspace + inspector** model.

Het past veel beter bij HueSync dan een reeks beheerpagina's.

---

# 22. Eén mogelijke complete user flow

Een nieuwe gebruiker opent HueSync.

## Stap 1 — Home

```text
No show running.

[ Create your first show ]
```

## Stap 2 — Audio

```text
Where is your music playing?

○ Living Room Sonos
○ Kitchen Sonos
○ AirPlay to HueSync
```

## Stap 3 — Zone

Grafische kaart:

```text
Living Room
● ●   ●
   ●
4 Hue lights
```

## Stap 4 — Style

```text
How should it feel?

[ Relaxed ]  [ Balanced ]  [ Party ]  [ Custom ]
```

Bij Party:

```text
Low energy  → Swirl
High energy → Fireworks
```

## Stap 5 — Test

Live spectrum start.

```text
Music detected ✓

Energy 47%

Swirl      58%
Fireworks  42%
```

Lamp-preview beweegt live.

## Stap 6

```text
[ Start Show ]
```

Pas **daarna** hoeft de gebruiker eventueel Effect-, Energy Profile- of Analyserdetails te openen.

Dit is veel toegankelijker dan eerst afzonderlijk:

Controller → Zone → Player → Analyser → Effect → Energy Profile → Coupling

te moeten begrijpen.

---

# 23. Technische UI-aanbevelingen voor de bestaande React-code

De huidige stack met React, Tailwind en shadcn-achtige componenten is prima voor dit ontwerp. Een frameworkwissel is niet nodig.

Aanbevolen nieuwe componenten:

```text
SignalFlow
SignalNode
LiveSpectrum
EnergyMeter
EnergyBlendEditor
EffectCard
EffectPreview
EffectInspector
ZoneMap
LightDot
PipelineStatus
AdvancedDisclosure
ComparePanel
```

## Begin niet met een generieke node-editor

Een volledig vrij node graph-systeem zoals TouchDesigner is verleidelijk, maar voor HueSync waarschijnlijk te complex.

De topology is grotendeels vast:

```text
Source → Analysis → Energy Profile → Output
```

Gebruik dus een **gestructureerde flow**, geen onbeperkt drag-and-drop node canvas.

Dat geeft 80% van de visuele winst met veel minder complexiteit.

---

# 24. Data/API-uitbreidingen die de UI waarschijnlijk nodig heeft

Veel bestaat al, maar voor een hoogwaardige grafische UI zou ik het statusmodel uitbreiden met een compacte live-resolved state.

Bijvoorbeeld:

```json
{
  "active_show": {
    "id": "...",
    "name": "Living Room — Party"
  },
  "source": {
    "name": "Living Room Sonos",
    "state": "playing"
  },
  "analysis": {
    "energy": 0.62,
    "percussive": 0.71,
    "harmonic": 0.29,
    "onset": true
  },
  "energy_profile": {
    "name": "Dynamic Party",
    "low_effect": {
      "id": "...",
      "name": "Calm Swirl",
      "weight": 0.38
    },
    "high_effect": {
      "id": "...",
      "name": "Party Fireworks",
      "weight": 0.62
    }
  }
}
```

De frontend hoeft dan minder IDs terug te resolven via meerdere API-calls.

Dit is geen vervanging van de bestaande REST-entiteiten, maar een **read model voor de live UI**.

---

# 25. Specifieke code-/productbevindingen

## 25.1 `NowPlaying` bevat nog legacy-termen

In de huidige code:

```text
Layer mix
Mellow
Active
Active scene
Crossfader
```

Dit is inconsistent met het nieuwe model.

Bestand:  
`web/src/pages/NowPlaying.tsx`

**Prioriteit: P0.**

---

## 25.2 EnergyProfile is correct gemodelleerd, maar verkeerd gepresenteerd

De editor legt `blend_start`, `blend_end` en `blend_response` tekstueel uit, inclusief een voorbeeld 0.3 / 0.7.

Dat bewijst dat het concept extra uitleg nodig heeft.

Een goed grafisch control maakt die uitleg grotendeels overbodig.

Bestand:  
`web/src/pages/EnergyProfiles.tsx`

**Prioriteit: P0.**

---

## 25.3 Effect Type selector is al een goed begin

De Effect-editor gebruikt kaarten met naam en beschrijving in plaats van alleen een dropdown.

Dat patroon moet de basis worden voor de hele Effect library en uitgebreid worden met live mini-previews.

Bestand:  
`web/src/pages/Effects.tsx`

**Prioriteit: P0/P1.**

---

## 25.4 Coupling-editor maakt nested entities aan maar verbergt hun relatie

Het is positief dat vanuit een Coupling direct een Player, Zone, Analyser of Energy Profile kan worden aangemaakt.

Maar doordat alles als verticale selectievelden wordt weergegeven, blijft de architectuur abstract.

Maak dezelfde functionaliteit zichtbaar als vier verbonden blokken.

Bestand:  
`web/src/pages/Couplings.tsx`

**Prioriteit: P1.**

---

## 25.5 Now Playing bevat te veel engineering controls

Band boundaries en frequency cutoffs zijn nuttig, maar horen niet op hetzelfde informatieniveau als de visuele live output.

Verplaats ze naar:

```text
Diagnostics / Advanced analysis
```

**Prioriteit: P0/P1.**

---

# 26. Wat ik nadrukkelijk níet zou doen

## Geen volledige 3D room editor als eerste stap

2D is genoeg. Philips Hue zelf laat zien dat ruimtelijke positionering veel waarde heeft zonder dat een complexe 3D-engine nodig is.

## Geen generieke TouchDesigner-kloon

HueSync is geen modulaire programmeeromgeving. De grafiek moet begrip vergroten, niet nieuwe complexiteit creëren.

## Geen technische parameters verwijderen

De geavanceerde instellingen zijn juist een sterke kant van HueSync. Verberg ze achter progressive disclosure; haal ze niet weg.

## Geen animaties puur voor decoratie

Animatie moet data uitleggen:

- energy marker beweegt;
- Effect weights veranderen;
- spectrum leeft;
- lampdots tonen echte output.

Geen zinloze gradients, particle backgrounds of bewegende UI-chrome.

---

# 27. Mijn concrete doelbeeld voor HueSync

HueSync zou visueel moeten voelen als een kruising tussen:

- de **eenvoud van Philips Hue Sync**;
- de **live control surface van QLC+**;
- de **directe visuele feedback van Resolume**;
- met de eigen, veel rijkere audio-analyse van HueSync eronder.

Niet als een database-admin voor muziekverlichting.

De gebruiker moet na vijf seconden naar het scherm kijken en ongeveer dit begrijpen:

```text
Mijn Sonos speelt
        ↓
HueSync hoort vooral veel energie en een kick
        ↓
Dynamic Party zit nu voor 65% aan de Fireworks-kant
        ↓
daarom zie ik deze kleuren en bursts
        ↓
deze vier lampen krijgen dit signaal
```

Als de interface dát zichtbaar maakt, wordt het complexe systeem vanzelf begrijpelijk.

---

# 28. Aanbevolen implementatievolgorde

De meest efficiënte volgorde is:

```text
1. Terminologie-restanten opruimen
       ↓
2. EnergyBlendEditor component
       ↓
3. Effect cards + mini-preview
       ↓
4. Nieuwe Live layout
       ↓
5. Source → Analysis → Energy → Zone flow
       ↓
6. Inspector / Advanced panels
       ↓
7. Zone map
       ↓
8. Show/Coupling designer
       ↓
9. A/B compare
       ↓
10. Onboarding/presets
```

Na stap 4 zal de interface al wezenlijk anders en begrijpelijker aanvoelen.

---

# 29. Samenvatting prioriteiten

| Prioriteit | Verbetering | Impact |
|---|---|---|
| P0 | Legacy Scene/Crossfader/Mellow/Active-termen verwijderen | Hoog |
| P0 | Energy Profile grafisch maken | Zeer hoog |
| P0 | Effect library als cards/previews | Zeer hoog |
| P0 | Live pagina herstructureren rond causaliteit | Zeer hoog |
| P1 | Coupling als visuele pipeline | Zeer hoog |
| P1 | Side inspector i.p.v. complexe modals | Hoog |
| P1 | Zone als 2D ruimtelijke kaart | Hoog |
| P1 | Basic/Advanced disclosure | Hoog |
| P1 | DSP live visualisatie | Hoog |
| P2 | A/B compare mode | Middel/hoog |
| P2 | Latency calibration UX | Middel |
| P2 | Presets + onboarding | Hoog voor nieuwe gebruikers |

---

# 30. Eindconclusie

De nieuwe repository heeft inhoudelijk een veel sterker domeinmodel dan de eerdere versie. **Effect** en **Energy Profile** zijn goede keuzes en moeten behouden blijven.

De volgende grote sprong zit niet in nóg meer backendarchitectuur, maar in het zichtbaar maken van de architectuur die er al is.

Mijn belangrijkste ontwerpbeslissing zou zijn:

> **De primaire HueSync-interface wordt één live visuele signaalflow van Audio → Analysis → Energy Profile → Effects → Room.**

Daaromheen komen:

- een grafische Energy Profile editor;
- Effect cards met preview;
- een ruimtelijke Zone-view;
- contextuele inspectors;
- een Advanced/Diagnostics-laag voor DSP;
- een Show/Coupling designer voor samengestelde configuraties.

Dat model sluit beter aan bij hoe professionele licht- en VJ-software complexe realtime-systemen begrijpelijk maakt, zonder HueSync zelf onnodig tot een professionele node-editor te veranderen.

---

## Onderzochte bronnen

### HueSync repository

- https://github.com/ThaYapeMan/HueSync
- https://github.com/ThaYapeMan/HueSync/blob/master/README.md
- https://github.com/ThaYapeMan/HueSync/blob/master/web/src/App.tsx
- https://github.com/ThaYapeMan/HueSync/blob/master/web/src/pages/NowPlaying.tsx
- https://github.com/ThaYapeMan/HueSync/blob/master/web/src/pages/Couplings.tsx
- https://github.com/ThaYapeMan/HueSync/blob/master/web/src/pages/Effects.tsx
- https://github.com/ThaYapeMan/HueSync/blob/master/web/src/pages/EnergyProfiles.tsx

### Philips Hue

- https://www.philips-hue.com/en-us/explore-hue/blog/sync-with-music
- https://www.philips-hue.com/nl-nl/explore-hue/blog/sync-with-music
- https://www.philips-hue.com/en-in/explore-hue/propositions/entertainment/hue-sync

### QLC+

- https://docs.qlcplus.org/v5/virtual-console
- https://docs.qlcplus.org/v5/virtual-console/styling-and-placement
- https://docs.qlcplus.org/v5/advanced/web-interface

### Resolume

- https://www.resolume.com/support/en/composition
- https://resolume.com/support/en/layouts
- https://resolume.com/support/en/decks
