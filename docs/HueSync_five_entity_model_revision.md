Stop en herzie het datamodel vóór je verder bouwt — het huidige vier-
entiteiten-ontwerp (Player/Controller/LightProvider/Preset) verbergt een
belangrijk onderscheid dat nu alsnog scherp is geworden. "Preset" deed drie
dingen tegelijk (analysekeuze + renderinstellingen + de koppeling zelf), en
dat moet uit elkaar getrokken worden tot VIJF entiteiten.

## Herzien datamodel (vijf entiteiten, niet vier)

1. **Player** — ongewijzigd t.o.v. het huidige ontwerp: lms_host, lms_port,
   player_name, player_mac, alsa_device. Volledig dom, geen analyse.

2. **Controller** — ongewijzigd: hernoemd van Bridge, met `type`-veld
   (`hue` nu, `wled` later), bevat credentials per type.

3. **LightProvider** — ongewijzigd: een uitvoerdoel binnen een Controller
   (voor Hue: bridge_id, entertainment_area_id, entertainment_area_name,
   light_count).

4. **Analyser** (NIEUW, losgetrokken uit wat "Preset" was) — bevat
   ALLEEN de analysekeuze en -parameters: onset_method, onset_delta,
   onset_alpha, superflux_mu, superflux_lag, bars, lower_cutoff_freq,
   higher_cutoff_freq. Dit is HERBRUIKBAAR: meerdere Couplings kunnen naar
   dezelfde Analyser verwijzen, en delen dan effectief dezelfde
   analyse-instellingen (al draait, per het eerdere besluit, elke Coupling
   nog steeds zijn eigen cava-proces — het gaat om gedeelde CONFIGURATIE,
   niet een gedeeld proces).

5. **RenderConfig** (NIEUW, losgetrokken uit wat "Preset" was) — bevat
   ALLEEN de puur visuele/output-instellingen: colour_mode, sensitivity,
   brightness_floor, bass_hz, mid_hz, exertion_clip. Providerneutraal —
   bepaalt de abstracte Scene (zoals de bestaande Scene/LightColorCommand-
   scheiding in hue_output.py al doet), niet iets Hue- of WLED-specifieks.
   Dit maakt het mogelijk dat dezelfde Player+Analyser tegelijk een
   Hue-area en een WLED-strip aanstuurt, elk met eigen sensitivity/kleur-
   instellingen, zonder duplicatie van de analyse-instellingen.

6. **Coupling** (hernoemd van "Preset" — vervangt die naam volledig, want
   die was te generiek) — de daadwerkelijke koppeling: player_id (FK),
   analyser_id (FK), light_provider_id (FK), render_config_id (FK),
   plus name en enabled. Dit is waar activatie plaatsvindt. Bevat zelf GEEN
   analyse- of render-velden meer — puur de vier foreign keys plus
   identiteit/status.

## Voorbeeld van hoe dit samenkomt

```
Player (Zitkamer)
  └─ Analyser (combined, cava 50-12000Hz)
       ├─ Coupling 1 → LightProvider: Hue Zitkamer AE
       │              → RenderConfig: sensitivity 1.0, spectrum_rgb
       └─ Coupling 2 → LightProvider: WLED Strip
                      → RenderConfig: sensitivity 0.6, mono_pulse
```

Eén Player, één Analyser, twee Couplings met elk hun eigen
LightProvider + RenderConfig.

## Wat dit betekent voor wat je al gebouwd hebt

- Als je al modellen/migratie/routes voor "Preset" hebt gebouwd: herstructureer
  die naar Analyser + RenderConfig + Coupling. Dit is een uitbreiding
  van dezelfde richting, geen volledige herstart — Player, Controller en
  LightProvider blijven ongewijzigd.
- De migratiefunctie moet nu per bestaand profiel EEN Analyser, EEN
  RenderConfig, EN EEN Coupling aanmaken (in plaats van één Preset) — check
  of dedupliceren hier zinvol is (waarschijnlijk niet nodig voor de eerste
  migratie-ronde: elk oud profiel krijgt gewoon zijn eigen, niet-gedeelde
  Analyser/RenderConfig; delen kan de gebruiker later zelf instellen
  via de UI door twee Couplings naar dezelfde Analyser te laten
  wijzen).
- De veldcategorisatie uit Phase 1 (player/cava/pcm/render) blijft
  functioneel identiek van toepassing, alleen zitten "cava" en "pcm"-velden
  nu op Analyser, en "render"-velden op RenderConfig, in plaats van
  allebei op één Preset.
- UI: Coupling-tab (was Presets-tab) wordt het scherm waar je een Player +
  Analyser + LightProvider + RenderConfig samenbrengt — mogelijk via
  dropdowns naar bestaande Analysers/RenderConfigs, met een optie om
  een nieuwe aan te maken vanuit hetzelfde scherm (UX-detail, zelf invullen
  wat het handigst werkt).

Rapporteer, vóórdat je verder bouwt: hoeveel van het al gebouwde werk kan
hergebruikt worden met deze aanpassing, en wat moet opnieuw. Geef een kort
bijgewerkt plan, dan pas doorbouwen.
