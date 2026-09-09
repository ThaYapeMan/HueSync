Twee gecombineerde uitbreidingen op de net gebouwde two-layer mixer — beide
tegelijk, want ze lossen samen het probleem op dat de mixer nu weinig nut
heeft (te weinig onderscheidende opties, en gedeelde instellingen tussen
lagen maken het verschil nog kleiner).

Plan eerst, geef een kort overzicht terug voordat je bouwt — dit is een
substantiële uitbreiding op wat gisteren is gebouwd.

---

## Deel 1 — Elke laag krijgt zijn eigen volledige RenderConfig

Op dit moment deelt Mellow/Active alles behalve colour_mode (sensitivity,
brightness_floor, band boundaries, exertion_clip, onset_flash_intensity
zijn gedeeld). Dat maakt het onderscheid tussen de twee lagen te beperkt.

Wijzig het datamodel: een Coupling verwijst naar TWEE losse RenderConfigs
(bijvoorbeeld `render_config_id` voor Active, `mellow_render_config_id` voor
Mellow — of een duidelijkere naamgeving naar keuze), in plaats van één
RenderConfig met een extra mellow_colour_mode-veld erbovenop. De
mix-parameters zelf (mix_low_threshold, mix_high_threshold, mix_ema_alpha)
horen bij de Coupling of blijven op één van de twee RenderConfigs — kies
zelf de meest logische plek, maar niet gedupliceerd op beide.

Dit hergebruikt bestaande infrastructuur: de gebruiker kan met de bestaande
Clone-knop een RenderConfig dupliceren en dan de kloon aanpassen voor de
andere laag — dus twee volledig onafhankelijke configuraties, elk met eigen
sensitivity/boundaries/etc.

Migratie: bestaande Couplings met alleen colour_mode + mellow_colour_mode
moeten omgezet worden naar twee losse RenderConfigs (de bestaande blijft
Active, een nieuwe wordt aangemaakt voor Mellow met dezelfde waarden behalve
colour_mode).

---

## Deel 2 — Effect-catalogus (Light DJ-geïnspireerd + branchebrede domeinkennis)

Lees docs/EFFECT_ENGINE.md voor de bestaande specificatie, en
docs/HueSync_analysis_and_player_architecture.md voor de bestaande Groove
Wave-beschrijving.

### Onderzoeksresultaat (publiek beschikbaar, geen code, alleen gedrag)

Uit officiële App Store / Google Play-beschrijvingen van Light DJ (marketing
copy, geen code):

**Active effects** (triggeren tijdens luide passages):
- Splotches
- Fireworks
- Pulses
- Flashes
- Special Mix (optioneel, waarschijnlijk een combinatie van de andere vier —
  niet prioritair)

**Mellow effects** (actief tijdens zachte passages):
- Swirl
- Wave
- Solid
- None (uitgeschakeld — mellow-laag toont dan simpelweg niets/blijft
  ongewijzigd)

Aanvullend bevestigd via LedFx's publieke documentatie (docs.ledfx.app/effects
— GPL-3, dus gedrag lezen en citeren mag, code NIET overnemen): vergelijkbare
concepten (Radial, Concentric, Flame, Smoke, Soap, Waterfall, Digital Rain,
Spotlight) bestaan al lang als gedeelde, generieke domeinkennis in de
audio-reactive-lighting-branche (LedFx, WLED, Resolume, MADRIX gebruiken
allemaal vergelijkbare concepten met eigen namen). Dit bevestigt: dit zijn
gangbare, branchebrede concepten, geen uniek intellectueel eigendom van één
app. Je mag tijdens het ontwerpen ook LedFx's publieke effect-documentatie
en WLED's publieke effectenlijst (permissieve licentie) raadplegen, zolang
je zelf schrijft op basis van het BESCHREVEN gedrag en nooit code overneemt
— precies zoals al eerder gedaan bij de kleuranalyse-fase ("Read their docs
and approach, don't copy code").

### Interpretatie-richtlijn (eigen ontwerp, geen namaak)

Voor elk effect: baseer je implementatie op wat de naam logischerwijs
betekent in audio-reactive lighting, niet op enige aanname over hoe een
specifieke app het intern bouwt:

- **Splotches**: organische, onregelmatige kleurvlekken die verschijnen en
  vervagen op willekeurige posities
- **Fireworks**: uitbarstingen vanuit een punt die naar buiten uitdijen en
  vervagen, getriggerd op onset
- **Pulses**: ritmisch, synchroon oplichten/dimmen van alle lichten samen,
  op het tempo van de muziek
- **Flashes**: korte, felle, willekeurige lichtflitsen op individuele
  lichten bij een onset
- **Swirl**: langzaam roterende/kolkende kleurovergang tussen lichten
- **Wave**: een golf van kleur die geleidelijk over de lichtpositie reist
  (gebruikt LightChannel.position, net als Groove Wave, maar trager/zachter
  voor mellow-context)
- **Solid**: één vaste, langzaam driftende kleur over alle lichten (kan deels
  voortbouwen op het huidige spectrum_rgb-gedrag)
- **Groove Wave**: zoals al beschreven in
  docs/HueSync_analysis_and_player_architecture.md — spatiale kleurgolven
  via LightChannel.position

### Wat te bouwen

1. Nieuw `effect`-concept (naast of in plaats van colour_mode — beargumenteer
   welke aanpak het schoonst aansluit bij het bestaande model) op elke
   RenderConfig (Active en Mellow apart, dankzij Deel 1), met de effecten
   hierboven als opties.
2. Implementatie per effect in de render-pipeline (sync_engine.py /
   hue_output.py), voortbouwend op de bestaande Scene/LightColorCommand-
   scheiding zodat dit ook voor toekomstige WLED/Nanoleaf-drivers werkt.
3. UI: effect-keuze in de RenderConfig-editor, met eventuele
   effect-specifieke parameters (bv. Fireworks-uitdijsnelheid,
   Wave-golfsnelheid) alleen zichtbaar bij het gekozen effect.

### Tests

- Per effect: een test die aantoonbaar ander signaal-/kleurgedrag verifieert
  bij dezelfde input (bv. Fireworks geeft een expanderend patroon vanaf een
  punt, Pulses geeft synchrone helderheidswisseling over alle lichten,
  Splotches geeft ongelijke kleur per lichtpositie op hetzelfde moment)
- Niet alleen dat de code draait — daadwerkelijk bewijs dat elk effect visueel
  onderscheidend is van de andere

---

## Deel 3 — UX-patronen (inspiratie, geen visuele kloon)

Onderzoek naar UX-PATRONEN (niet visueel ontwerp) van Light DJ en Hue
Essentials, op basis van publiek zichtbare App Store-screenshots. Dit is
GEEN opdracht om het specifieke visuele ontwerp (iconen, kleurenschema,
exacte layout) van die apps te kopiëren — dat is auteursrechtelijk
beschermd "look and feel" van een commercieel product, en dat namaken is
niet legitiem, ongeacht de reden. Gebruik dit puur als inspiratie voor
functionele UX-PATRONEN, gebouwd met HueSync's eigen shadcn/ui-stijl en
design-tokens.

Patronen die opvallen in de screenshots, en waarom ze HueSync's huidige UI
zouden kunnen verbeteren:

1. **Effect-kiezer als grid/lijst met visuele previews, niet een platte
   dropdown.** Voor de nieuwe effect-catalogus (Splotches/Fireworks/Pulses/
   Flashes/Swirl/Wave/Solid) is een dropdown zoals nu bij colour_mode
   waarschijnlijk te weinig informatief — een grid met korte, eigen
   beschrijvingen/mini-animaties (of gewoon duidelijke labels) per effect
   sluit beter aan bij hoe een gebruiker een effect kiest op gevoel, niet op
   naam alleen.

2. **Mellow en Active als twee duidelijk gescheiden, naast elkaar zichtbare
   secties**, in plaats van (zoals nu) los onder elkaar in één lang
   formulier. Dit sluit ook aan bij Deel 1 hierboven (elke laag krijgt zijn
   eigen RenderConfig) — de UI zou dat onderscheid nu ook visueel moeten
   weerspiegelen, bijvoorbeeld twee kolommen of tabs binnen de
   Coupling-editor.

3. **Live-preview blijft prominent bovenaan** — dit heeft HueSync al (Live
   Preview-kaart op Now Playing), geen wijziging nodig, bevestigt alleen dat
   de bestaande aanpak overeenkomt met wat gangbaar is.

Ontwerp dit als EIGEN, HueSync-stijl interface (shadcn/ui, bestaande
design-tokens, donker thema zoals nu) — niet een visuele kloon van Light
DJ/Hue Essentials. Focus op de FUNCTIONELE indeling (grid i.p.v. dropdown,
twee-koloms Mellow/Active-scheiding), niet op specifieke kleuren, iconen, of
merkelementen van die apps.

Dit is de manier waarop Deel 1 en Deel 2 in de interface gepresenteerd
worden, geen losse, aparte stap.

---

## Volgorde en oplevering

1. Deel 1 eerst (datamodel + migratie + UI voor twee losse RenderConfigs per
   Coupling) — dit is de fundering.
2. Deel 2 daarna (nieuwe effecten), zodat ze meteen bruikbaar zijn binnen de
   al-onafhankelijke twee-lagen-structuur.
3. Losse commits per onderdeel, CI groen (inclusief de frontend-build-check
   en de git-diff-bundle-check) vóór elke rapportage.
4. GUI-werk hoort bij deze functionaliteit en moet gewoon netjes worden
   afgemaakt, niet als losstaand polish-project.

Rapporteer na Deel 1 met een tussenrapportage vóór je aan Deel 2 begint.
