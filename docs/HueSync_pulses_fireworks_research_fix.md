Bugreport + onderzoeksonderbouwing voor de Pulses- en Fireworks-effecten uit
de effect-catalogus. Eerder gemeld: Pulses toont alleen wit licht (geen
spectrumkleur), Fireworks toont een constant zwak oranje/geel gloeien
zonder te knipperen/exploderen. Grondig onderzocht op zowel algemene
techniek als academische/institutionele bron, zoals gevraagd.

---

## Onderzoeksbevindingen (bronnen, geen code overgenomen)

### 1. Particle system — Reeves (1983)

W.T. Reeves, "Particle Systems—A Technique for Modeling a Class of Fuzzy
Objects", Computer Graphics 17(3), 1983. Het grondleggende, publieke
academische model achter vrijwel elke fireworks/vuur/deeltjes-simulatie
sindsdien (bevestigd via meerdere latere IEEE/ResearchGate-papers die dit
als basis citeren). Standaard computer-graphics-leerstof.

### 2. Exponentiële attack/decay-envelope — audio-DSP-theorie

Klassieke ADSR-envelope-techniek uit synthesizer-/audio-DSP-theorie,
bevestigd toegepast op lichtintensiteit in Signify-octrooi US11057671
("Rendering a dynamic light scene based on audio visual content"): expliciet
genoemd om "distracting flicker caused by overly-dynamic light effects" te
voorkomen door attack/decay-tijdconstantes te gebruiken in plaats van
instant aan/uit-schakelingen.

### 3. Vermijd harde, strikt beat-gesynchroniseerde effecten — bevestigd
   institutioneel onderzoek

Signify-octrooi US10609794 ("Enriching audio with lighting") stelt
expliciet: *"Such 'harsh' light effects can be counterproductive... in
general the light dynamic does not have to be (exactly) aligned with the
music to have a positive effect."* Dit sluit direct aan bij de eerdere
klacht dat Multiband/Fireworks "te hard, onaangenaam" aanvoelden.

### 4. Institutionele context (TU Eindhoven ↔ Signify/Philips)

Bevestigd: een officieel gezamenlijk PhD-programma "Intelligent Lighting"
tussen TU/e en Philips Lighting B.V. (proefschrift Van Lith, 2017, vrij
toegankelijk via pure.tue.nl), en een actief lopend TU/e "Lighting and IoT
Lab" met expliciet genoemde langdurige samenwerking met Signify op
"computer-interpretable control algorithms" voor lichteffecten. Dit
bevestigt dat dit onderzoeksveld inderdaad zijn oorsprong mede bij
TU Eindhoven/Philips NatLab heeft, zoals vermoed — de specifieke onderlinge
algoritmes zijn niet los als open-access paper gevonden (vermoedelijk
grotendeels vastgelegd in de octrooien zelf), maar de octrooien citeren en
bouwen aantoonbaar voort op deze onderzoekslijn.

---

## Concrete fix-opdracht

### Pulses
Herschrijf met een exponentiële attack/decay-envelope voor HELDERHEID:
- Bij onset-trigger: snelle attack naar piek-intensiteit
- Daarna: exponentiële decay naar rust (tijdconstante gekoppeld aan
  effect_decay, veld bestaat al)
- Formule (standaard envelope follower): gain += attack_coef * (target -
  gain) als target > gain, anders gain += decay_coef * (target - gain)
- KLEUR = de actuele spectrumkleur (spectrum_rgb-berekening), NOOIT
  hardcoded wit — dit was de kernbug

### Fireworks
Herschrijf met het Reeves-particle-model, vertaald naar HueSync's discrete
lichtposities:
- Elke onset spawnt een klein aantal "particles" vanaf het triggerpunt
- Elk particle: positie (start bij trigger), snelheid (naar buiten,
  spreiding), kleur (uit spectrum/palet), lifetime
- Per frame: positie-update, lifetime neemt af, helderheid/alpha decayt
  naarmate lifetime opraakt (particle "sterft" bij lifetime=0)
- Gebruik LightChannel.position (bestaande capaciteit) zodat de explosie
  zich zichtbaar verspreidt vanaf het triggerpunt, niet willekeurig overal
  tegelijk
- effect_speed = uitdijsnelheid van de particles, effect_decay =
  fade-snelheid (beide velden bestaan al)

### Algemene ontwerprichtlijn (uit bevinding 3, geldt voor ALLE Active-
    effecten, niet alleen Pulses/Fireworks)
Vermijd instant, harde aan/uit-schakelingen zonder envelope — dit is
aantoonbaar "counterproductive" volgens het gevonden onderzoek. Elk effect
dat op een onset reageert, moet een vloeiende attack/decay-curve gebruiken,
nooit een binaire flits. Controleer of dit principe ook al correct is
toegepast op Flashes en Splotches (eerder gemeld als wél werkend, maar
controleer of ze deze envelope-aanpak al gebruiken of toevallig goed
aanvoelen zonder dat het principe expliciet is doorgevoerd).

### Belangrijke scope-afbakening: uitvoer, niet analyse

Deze envelope-richtlijn hoort UITSLUITEND bij de rendering-laag (de
effecten: Pulses, Fireworks, Flashes, Splotches, etc.), NIET bij de
onset-detectoren zelf (combined, multiband, superflux). Die laatste zijn
detectie-algoritmes met als enige taak "is er nu een onset, ja/nee" — een
meetopdracht, geen visuele beslissing. Ze moeten zo scherp en accuraat
mogelijk blijven; pas NIETS aan hun detectielogica aan naar aanleiding van
deze opdracht.

Expliciet niet meegenomen in deze ronde (bewust, op verzoek): een
cooldown/debounce-periode tussen onset-triggers op AnalysisConfig-niveau
(om de triggerFREQUENTIE te temperen, los van hoe hard elke individuele
trigger visueel reageert) — dat is een apart, mogelijk toekomstig punt en
hoort niet bij deze fix.

---

## Tests

- **Pulses**: verifieer dat de output-kleur de spectrumkleur volgt (nooit
  puur wit tenzij de spectrumkleur dat toevallig is), EN dat de helderheid
  een aantoonbare attack-piek-decay-curve volgt na een gesimuleerde onset
  (meerdere frames na de trigger vergelijken, dalende trend verwachten)
- **Fireworks**: verifieer dat na een onset de output over meerdere
  lichtposities verschilt (ruimtelijke spreiding vanuit het triggerpunt),
  EN dat de helderheid per positie afneemt over tijd (decay aantoonbaar)
- Beide: geen instant binaire aan/uit-sprongen tussen frames — verifieer
  met een test die de frame-op-frame-verandering meet en een maximale
  "sprong" per frame afdwingt (geen abrupte discontinuïteit)

Test visueel op de LXC na deploy, niet alleen via de unit tests — bevestig
zelf dat het er goed uitziet vóór je het als afgerond rapporteert.
