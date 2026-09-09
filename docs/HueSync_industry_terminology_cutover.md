Cutover: hernoem meerdere HueSync-concepten naar gevestigde, professionele
lichtindustrie-terminologie. Onderzoek gebeurde in twee categorieën, en dat
onderscheid is belangrijk gebleken:

1. **Theatrale lichtconsoles** (ETC, grandMA3, algemene DMX-vakliteratuur)
   — handmatig bediende showbesturing. Bron voor: Scene, Crossfader, Fade,
   Go, Blackout, Live/Blind.
2. **Sound-to-light / audio-reactive software** (ENTTEC EMU, VenueMagic,
   Lightjams) — dit is HueSync's EIGEN categorie, geen theaterconsole.
   Bron voor: Zone (niet "Group", dat is theaterconsole-vocabulaire).
   Bevestigd dat de architectuur zelf (audio-analyse → effecten → zones)
   al overeenkomt met hoe deze categorie software werkt.

Doel: een consistente, industriestandaard-woordenschat door de hele stack
heen, gebruik makend van de EXACTE branchecategorie die bij HueSync past
(sound-to-light) waar die specifieker is dan generieke theaterconsole-
termen. Dit is een grote, samenhangende herstructurering — plan eerst, geef
een kort overzicht terug voordat je bouwt, en behandel het net zo serieus
als de eerdere Profile/Bridge-cutover.

---

## Aanleiding

De huidige structuur (uit Deel 1 van de two-layer mixer, commit 78274a5)
zette Active/Mellow-RenderConfig's en crossfade-parameters rechtstreeks op
Coupling. Dat maakte Coupling onnodig complex en de relatie tussen
Analysis config en de twee lagen onduidelijk. Onderzoek naar hoe
professionele lichtconsoles dit al decennia oplossen (Scene + Crossfader
als twee gescheiden concepten) biedt een schonere, beproefde structuur.

---

## Terminologie-tabel (bevestigd via onderzoek)

| HueSync nu | Nieuwe naam | Definitie (bevestigd) | Bron-categorie |
|---|---|---|---|
| RenderConfig | **Scene** | "predefined look or state for your lighting fixtures" | Theaterconsole |
| (nieuw concept) | **Crossfader** | "smooth transitions between active scenes... gradually raising one while lowering the other" | Theaterconsole |
| LightProvider | **Zone** | "create unlimited custom trigger zones" (ENTTEC EMU); gangbaar in sound-to-light-software voor een audio-reactieve lichtgroep | Sound-to-light (HueSync's eigen categorie — NIET "Group", dat is theaterconsole-vocabulaire) |
| mix_ema_alpha | **fade_speed** (of fade_time, zie hieronder) | "transition time between two different states of lighting" | Theaterconsole |
| "Activate"-knop | **Go** | knop die de eerstvolgende cue/look daadwerkelijk oproept | Theaterconsole |
| (nieuw UX-principe) | **Live / Blind** | "Live" = wat er nu daadwerkelijk op de output staat; "Blind" = een cue bewerken zonder de live-uitvoer te raken | Theaterconsole |

Bewust NIET hernoemd: Controller (Hue Bridge) blijft ongewijzigd —
"Universe"/"Patch" uit de DMX-wereld passen niet op Hue Entertainment's
architectuur (geen DMX-adressering), dus die analogie wordt niet
geforceerd. Player/VirtualPlayer blijft ook ongewijzigd — er is geen
lichtindustrie-equivalent voor een audio-analysebron. "Stop" blijft ook
ongewijzigd (niet hernoemd naar "Blackout" — Blackout betekent in de
industrie specifiek "alles naar 0%, systeem blijft actief", terwijl
HueSync's Stop de hele sessie afbreekt; dat is een ander concept, de
analogie zou misleidend zijn). Grand Master-fader (overkoepelende
helderheidsregeling) is bewust NIET meegenomen — nieuwe functionaliteit,
buiten scope voor deze cutover.

---

## Deel A — RenderConfig → Scene (volledige hernoeming, overal)

Geen twee namen naast elkaar voor hetzelfde concept — dit gaat overal door:

- Backend: RenderConfig-klasse in models.py → Scene, met alle gerelateerde
  namen (create_render_config → create_scene, get_render_configs →
  get_scenes, render_config_id → scene_id, enz.)
- API-routes: /api/render-configs/* → /api/scenes/*
- Storage-sleutel: "render_configs" → "scenes" (met migratie/backfill)
- Frontend: RenderConfigs.tsx → Scenes.tsx, sidebar-tab "Rendering" →
  "Scenes", alle component-/variabelenamen mee (renderConfigId → sceneId)
- Alle UI-labels die "Render config" zeggen worden "Scene"

---

## Deel B — Nieuwe entiteit: Crossfader

Vervangt de huidige, ingebedde Active/Mellow/mix-structuur op Coupling.

- **Crossfader**-entiteit: active_scene_id (FK), mellow_scene_id (FK),
  low_threshold, high_threshold, fade_speed (zie Deel D), plus name/id
  zoals de andere entiteiten.
- Coupling verliest render_config_id, mellow_render_config_id,
  mix_low_threshold, mix_high_threshold, mix_ema_alpha.
- Coupling krijgt in plaats daarvan ÉÉN veld: crossfader_id (FK).
- Coupling wordt daarmee weer symmetrisch: vier gelijkwaardige FK's
  (player_id, group_id, analysis_config_id, crossfader_id) — geen
  ingebedde rendering-complexiteit meer, geen uitleg-tekst nodig in de
  Coupling-editor omdat de verwarring die dit opriep nu verdwijnt door de
  scheiding zelf.

### API

- CRUD-routes voor Crossfader (/api/crossfaders/*), consistent met
  bestaande patronen voor AnalysisConfig/Scene.
- PATCH /api/couplings/{id} gebruikt crossfader_id i.p.v. de vier losse
  velden.
- Field-categorisatie (Phase 1-patroon) uitbreiden: beoordeel of
  crossfader_id live-updatebaar moet zijn zonder sessie-restart, consistent
  met hoe analysis_config_id dat al kan.

### UI

- Nieuwe **Crossfaders**-tab in de sidebar, met CRUD: naam, Active Scene-
  dropdown, Mellow Scene-dropdown, low/high threshold, fade_speed.
- CouplingEditor wordt weer simpel: vier dropdowns naast elkaar (Player,
  Group, Analysis config, Crossfader).
- "+ New Crossfader" quick-create optie vanuit de Coupling-editor, net als
  de andere "+ New"-knoppen.

---

## Deel C — LightProvider → Zone

Bevestigd via onderzoek naar sound-to-light-software specifiek (ENTTEC EMU:
"Create unlimited custom trigger zones"; VenueMagic: "no limit on the
number of zones you create") — dit is HueSync's EIGEN branchecategorie
(audio-reactive lighting control), niet de theaterconsole-categorie. "Zone"
is daar de gangbare term voor een audio-reactieve lichtgroep, en past
inhoudelijk exact op wat LightProvider al doet (een Hue Entertainment Area
= een groep lichten die samen audio-reactief wordt aangestuurd). Bewust
GEEN "Group" — dat is theaterconsole-vocabulaire uit een andere categorie.

- Backend: LightProvider-klasse → Zone, alle gerelateerde namen
  (get_light_providers → get_zones, light_provider_id → zone_id)
- API-routes: /api/light-providers/* → /api/zones/*
- Storage-sleutel: "light_providers" → "zones" (met migratie/backfill)
- Frontend: variabelenamen mee (lightProviderId → zoneId), sidebar-label
  "Zones"
- Documenteer kort in CLAUDE.md dat dit specifiek de Hue Entertainment
  Area/toekomstige WLED-groep betekent — niet een generieke UI-groepering
  — om verwarring te voorkomen. Vermeld ook waarom "Zone" i.p.v. "Group" is
  gekozen (sound-to-light-categorie, niet theaterconsole), zodat een
  toekomstige sessie dit niet per ongeluk terugdraait.

---

## Deel D — mix_ema_alpha → fade_speed

- Hernoem het veld op de Crossfader-entiteit. Kies zelf tussen
  `fade_speed` en `fade_time`, afhankelijk van welke term precies past:
  ema_alpha is een gladheids-/reactiesnelheid-parameter, geen letterlijke
  tijdsduur — beoordeel of "fade_speed" preciezer is dan "fade_time" om
  geen verkeerde verwachting over eenheid/gedrag te wekken.
- UI-label bijwerken: "EMA alpha (crossfade speed, 0.01–0.5)" →
  bijvoorbeeld "Fade speed (0.01–0.5)" — behoud de getallenrange-uitleg,
  vervang alleen de technische term.

---

## Deel E — Activate → Go (knoptekst)

Bevestigd: in lighting consoles is "Go" de standaardknop die de
eerstvolgende cue/look daadwerkelijk oproept ("cues are advanced by
pressing the [Go] button").

- Hernoem de "Activate"-knop naar "Go" op de Couplings-tabel en in Now
  Playing's CouplingSelector.
- Backend-routenaam (/api/couplings/{id}/activate) hoeft niet mee te
  veranderen — dit is puur een UI-labelwijziging, geen API-hernoeming
  (behoud interne consistentie met "activate" als werkwoord in code/logs,
  alleen de zichtbare knoptekst wordt "Go").

## Deel F — Live/Blind als UX-principe

Bevestigd: theaterconsoles onderscheiden expliciet "Live" (wat nu op de
output staat) van "Blind" (een cue bewerken zonder de live-uitvoer te
raken) — "blind editing permits modifications to recorded cues without
altering the live output, preserving the current stage state."

Dit is meer een ONTWERPREGEL dan een rename: HueSync heeft al een "● Live"-
badge rechtsboven in de UI. Leg vast, in CLAUDE.md en als concreet
implementatieprincipe:

- Elke bewerking van een Scene/Crossfader/AnalysisConfig/Coupling die NIET
  de actief-lopende Coupling is, mag NOOIT de live-uitvoer beïnvloeden —
  dit is "Blind" bewerken.
- Alleen wijzigingen op de daadwerkelijk actieve Coupling passen live toe
  (via de bestaande update_onset_pipeline()/update_render()-paden uit
  Phase 1), en dat gebeurt bewust, niet per ongeluk.
- Overweeg een klein visueel signaal in de UI wanneer je een Scene/
  Crossfader bewerkt die momenteel deel uitmaakt van de actieve Coupling
  (bv. "Editing this will affect the Live output" als waarschuwing) versus
  wanneer dat niet zo is (stilzwijgend Blind, geen waarschuwing nodig).

Dit voorkomt een categorie bugs zoals de eerdere "licht bevriest bij live
bewerken"-issue uit deze sessie — expliciet vastleggen wanneer iets Live
vs Blind is, in plaats van dat impliciet te laten.

---

## Migratie (één samenhangende stap)

Voer Deel A, B, C en D samen uit in dezelfde migratie-ronde — niet als
losse migraties na elkaar over dezelfde velden. Deel E en F zijn puur
UI-tekst/ontwerpprincipe, geen datamodelwijziging, en kunnen in dezelfde
commit-ronde maar hebben geen eigen migratiestap nodig. Storage.migrate()
uitbreiden zodat, voor elke bestaande Coupling met render_config_id/
mellow_render_config_id/mix_* rechtstreeks erop:

1. Bestaande RenderConfig-records worden Scene-records (hernoemd)
2. Bestaande LightProvider-records worden Zone-records (hernoemd)
3. Een nieuwe Crossfader-entiteit wordt aangemaakt die de mix-waarden
   overneemt (met fade_speed i.p.v. mix_ema_alpha)
4. coupling.crossfader_id wordt gezet naar het nieuwe Crossfader-record
5. coupling.zone_id vervangt coupling.light_provider_id

Geen dataverlies, geen handmatige actie van de gebruiker nodig.

---

## Tests

- Migratietest: bestaande Coupling (oude structuur) → na migratie correct
  omgezet naar Scene + Group + Crossfader + Coupling met crossfader_id/
  group_id, geen dataverlies. Test met een realistische fixture die het
  HUIDIGE config.json-formaat nabootst.
- CRUD-tests voor Crossfader en Group, consistent met bestaande patronen.
- Regressietest: LayerMixer-rendering geeft identieke output vóór en na
  deze herstructurering bij gelijke onderliggende waarden — dit is een
  pure datamodel-/naamgevingsherstructurering, geen gedragswijziging.

---

## Consistentie-check (verplicht vóór rapportage)

Grep-audit door de hele codebase, bevestig afwezigheid van:
- "render_config" / "RenderConfig" / "Rendering" (behalve waar het
  generiek over het renderen van de daadwerkelijke Hue-output gaat — een
  ander concept dan de opgeslagen Scene zelf; licht toe waar je die grens
  trekt als die situatie zich voordoet)
- "light_provider" / "LightProvider"
- "mix_ema_alpha"
- "Activate" als zichtbare knoptekst (mag wel als interne functie-/
  routenaam blijven bestaan, zie Deel E)

---

## Werkwijze

Losse commits per deel (A, B, C, D kunnen apart gecommit worden, maar de
migratie zelf is één samenhangende stap zoals hierboven beschreven). CI
groen (inclusief de frontend-build-check en de git-diff-bundle-check) vóór
elke rapportage. Rapporteer met de grep-bevestiging vóór je het als
afgerond meldt.
