Breid de onset-detectie uit met twee extra methodes, naast de bestaande. Maak het
een keuze per profiel: `onset_method` met waarden `combined` (huidig gedrag,
blijft de default), `multiband` en `superflux`.

De bestaande Dixon-piekselectie (drie voorwaarden: lokaal maximum, asymmetrisch
lokaal gemiddelde plus delta, afnemende drempel g_alpha) blijft in alle drie de
methodes gebruikt. Wat verschilt is *hoe de detectiefunctie wordt berekend*, niet
hoe er pieken uit gekozen worden.

**Belangrijk, aanvulling t.o.v. de oorspronkelijke opzet: de PCM-tap is inmiddels
klaar (100Hz STFT-data via `PcmStft`, aangesloten op de bestaande
`combined`-detector als regressie-check). Deze nieuwe multiband/superflux-methodes
moeten dus op DIE 100Hz-databron draaien, niet op cava's 30Hz bars — dat was het
hele punt van de PCM-tap eerst bouwen.**

---

## 1. `multiband` — flux per band

Nu wordt de flux over alle bars opgeteld tot één waarde, dus `AudioFeatures.onset`
is één boolean zonder informatie over welk deel van het spectrum de aanslag
veroorzaakte. Voor de effect-engine is dat onderscheid essentieel: een kick moet
Pulses aansturen en een hi-hat Flashes, niet allebei tegelijk.

- Bereken de flux apart per band (bass/mid/treble), met dezelfde bandgrenzen die
  `_band_average` gebruikt
- Pas Dixon's drie voorwaarden per band toe, elk met eigen toestand (rollend
  gemiddelde, `g_alpha`, lokaal maximum)
- `AudioFeatures` krijgt `onset_bass`, `onset_mid`, `onset_treble` als booleans
  plus de bijbehorende strengths
- Het bestaande gecombineerde `onset`-veld blijft bestaan (waar als één van de
  drie waar is), zodat niets breekt

---

## 2. `superflux` — flux met maximum-filter

Böcks SuperFlux (2013) is spectral flux met één toevoeging: een **maximum-filter
over de frequentie-as** toegepast op het vorige frame, vóór het verschil wordt
genomen.

Waarom dat helpt: bij vibrato of een lichte toonhoogtebeweging schuift energie
tussen naburige bins. Gewone flux ziet dat als een reeks onsets — een zangstem of
strijker levert dan valse triggers. Het maximum-filter laat die kleine
verschuivingen toe zonder ze als nieuwe energie te tellen.

```
X_max(n, k) = max( X(n, k-mu) ... X(n, k+mu) )       # maximum over mu naburige bins
SuperFlux(n) = Σ_k  H( X(n, k) − X_max(n-1, k) )     # H = halve gelijkrichter
```

Böck gebruikt `mu = 3` bins bij een mel-filterbank, en neemt het verschil met
frame `n-2` in plaats van `n-1` om trage aanzetten beter te vangen. Maak beide
instelbaar (`superflux_mu`, `superflux_lag`) met die waarden als default.

Aandachtspunt: dit is ontworpen voor een mel-filterbank met een fijnere verdeling
dan cava's bars. Op de nieuwe 100Hz PCM-tap-data (raw FFT-bins uit `PcmStft`,
geen mel-filterbank) is de bin-resolutie fijner dan cava's 24-60 bars, dus
`mu = 3` heeft daar minder relatieve impact dan bij cava — documenteer dat als
comment, en overweeg `mu` instelbaar te maken relatief aan het aantal FFT-bins
in plaats van hardcoded.

**Licentie:** SuperFlux is een gepubliceerd algoritme (Böck & Widmer, 2013).
Essentia's implementatie is AGPL en mag dus niet overgenomen worden — schrijf het
zelf uit het paper. Het algoritme zelf is vrij.

---

## Wat er in de GUI bij moet

- `onset_method` als keuzelijst in de profiel-editor, met de extra parameters die
  alleen zichtbaar zijn bij de gekozen methode
- Bij het spectrum in Now Playing: **per band een indicator** die oplicht bij een
  onset, in de bandkleuren. Dan is te beoordelen of het onderscheid klopt vóórdat
  er effecten op gebouwd worden.

## Aandachtspunt bij de framerate

Bij ~30 Hz (cava) is één frame 33 ms. Dixon's `w=3` venster beslaat dan ±100 ms,
wat grof is voor het scheiden van snelle hi-hats. Op de nieuwe 100Hz PCM-tap-data
is één frame 10 ms, dus `w=3` beslaat ±30 ms — een aanzienlijke verbetering voor
het scheiden van snel op elkaar volgende aanslagen. Dit is precies waarom de
PCM-tap eerst gebouwd is; multiband en superflux profiteren hier direct van.

## Tests

- Een gesimuleerde reeks waarin alleen de bas een piek heeft: `onset_bass` waar,
  de andere twee niet
- Voor SuperFlux: een reeks die een langzaam schuivende piek over naburige bins
  simuleert (vibrato). Gewone flux moet daar onsets geven, SuperFlux niet — dat
  is precies het verschil dat het algoritme moet aantonen
- Beide methodes moeten getest worden tegen de 100Hz PCM-tap-databron
  (`PcmStft`/`StftOnsetPipeline`), niet tegen cava's bars — een test die per
  ongeluk nog op cava-data draait, test niet wat gebouwd is

---

Zoals steeds: losse commits per methode, GUI-werk hoort erbij als het bij deze
functionaliteit hoort, en rapporteer aan het eind wat er klaar is en wat de
volgende sub-stap is.
