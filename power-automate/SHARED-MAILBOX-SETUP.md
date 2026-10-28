# v5: delt postkasse, datomapper og dubletter

Importfil: **Gem-nye-fakturaer-solution-v5.zip**, version **1.0.0.5**.

Flowet tjekker `faktura@alcora.dk` hvert 15. minut. Det læser både Indbakke
og mappen med det præcise navn `Arkiv_10_2024_>`. Arkivmappen kan ligge direkte
under postkassen eller direkte under Indbakke. Hvis navnet ikke findes, eller
der er flere mapper med det navn på disse placeringer, fejler kørslen tydeligt.

Kun mails modtaget fra første initialisering og frem behandles. Gamle mails i
begge mapper springes over, også hvis de senere flyttes. En ny mail kan godt
flyttes fra Indbakke til Arkiv inden næste tjek. Mails, der flyttes ud af begge
mapper eller slettes før et tjek, kan ikke hentes af dette flow.

PDF'er gemmes under `/Fakturaer/yyyy-MM-dd/`. Datoen kommer fra mailens
modtagelsestidspunkt i dansk tidszone, inklusive sommer-/vintertid. Der er én
mappe pr. dag, ikke én pr. mail. PDF'en beholder sit originale filnavn (fx
`Faktura_9583195.pdf`). inco og Royal Unibrew navngiver dog ofte filen efter
forfaldsdatoen (fx `inco Aarhus A_S_29.12.2026.pdf`), så forskellige fakturaer kan
have samme navn. Findes navnet allerede under `Fakturaer` med andet indhold, får
den nye fil en kort tilfældig endelse (fx `..._3f9a1c2b.pdf`) i stedet for at
overskrive den gamle.

Dubletkontrollen sammenligner PDF'ernes bytes. En identisk PDF i begge mapper,
med et andet filnavn eller i en senere mail, gemmes kun én gang. To PDF'er med
samme navn og forskelligt indhold gemmes begge. PDF'er med samme fakturanummer,
men forskellig PDF-kodning, er forskellige filer for denne kontrol. Den første
gemte fil bliver liggende i sin oprindelige datomappe. Eksisterende PDF'er under
`Fakturaer`, inklusive v4-filer i selve rodmappen, indgår i kontrollen og flyttes
ikke. Direkte mappeopslag bruges i stedet for OneDrive-søgning.

De eksisterende Royal Unibrew-, inco-, Kreditnota_- og nummererede
`Faktura_<nummer>.pdf`-filtre er bevaret. Det sidste filter kræver fortsat
`fra inco` i mailens emne. Inline-filer og `Faktura_TEST.pdf` springes over.

## Import og første test

1. Sørg for, at `Fakturaer` findes i den OneDrive for Business, flowet skal bruge.
2. Slå det eksisterende fakturaflow fra, mens det opdateres. Gem gerne en eksport
   af det aktive flow, hvis det indeholder lokale ændringer.
3. Gå til **Solutions > Import solution** i det samme miljø som tidligere.
   Vælg **Gem-nye-fakturaer-solution-v5.zip**. Brug de eksisterende Outlook- og
   OneDrive-forbindelser. Outlook-kontoen skal have læseadgang til den delte
   postkasse; det er ikke tilstrækkeligt kun at kunne sende som den.
4. Hvis importen tilbyder at aktivere flows automatisk, lad det være slået fra,
   indtil indstillingerne er gennemgået.
5. Åbn **Invoice PDF automation > Gem nye fakturaer fra Outlook**. Kontroller
   forbindelserne og trinnene **Shared mailbox**, **Archive folder name** og
   **OneDrive folder**. Arkivnavnet slutter bogstaveligt med `_>` i pakken.
6. Gem flowet, ret eventuelle valideringsfejl, og slå det til. Det erstatter det
   tidligere flow med samme ID; aktiver kun én udgave mod denne destination.
7. Lad den første planlagte kørsel afslutte, eller vælg **Test > Manually** for at
   køre initialiseringen. Kontroller i kørselsoversigten, at hele kørslen lykkes,
   og at `/Fakturaer/_automation-state.json` er oprettet. Denne fil fastlægger
   starttidspunktet. En forventet 404 på **Read saved state** håndteres ved den
   første kørsel; slutresultatet skal stadig være en vellykket kørsel.
8. **Efter initialiseringen:** send selv en ny mail til postkassen med en harmløs
   PDF, fx `inco TEST.pdf`. Vent på næste tjek, eller kør testen manuelt. Bekræft,
   at PDF'en kan åbnes i dagens datomappe. Dette verificerer også oprettelse af
   datomappen via OneDrive-forbindelsen.
9. Send samme PDF igen, denne gang til Arkiv via jeres normale arbejdsgang. Kør
   igen og kontroller, at der stadig kun er én PDF. Flyt også den første mail
   til Arkiv, og gentag kontrollen. Brug til sidst en anden PDF med samme navn:
   den skal blive gemt som en ekstra fil.

## Tilstand og drift

`_automation-state.json` gemmer starttidspunktet og ID'er for behandlede mails;
den indeholder ikke selve PDF-indholdet. Behold filen ved opdateringer. Hvis den
slettes, initialiserer flowet en ny fremadrettet startdato, så mails fra perioden
mellem den gamle og nye startdato ikke længere bliver hentet. Ved ugyldig JSON
eller adgangsfejl fejler flowet i stedet for at nulstille startdatoen.

Mailen registreres først som behandlet, når dens relevante PDF'er er håndteret.
En afbrudt kørsel kan derfor gentages. Før nye uploads gennemgås de faktisk gemte
PDF'er, så et upload, der lykkedes før en senere fejl, genkendes ved genkørsel.
Sletning af en PDF genåbner ikke en allerede behandlet mail.

Kørsler og løkker er sekventielle for at undgå, at to samtidige uploads fra dette
flow opretter dubletter. Andre flows eller manuelle samtidige uploads koordineres
ikke af denne kontrol.

Mail- og vedhæftningslister gennemgås med Microsoft Graphs `@odata.nextLink`
via **Send an HTTP request** i den eksisterende Office 365 Outlook-forbindelse.
Det er GET-kald til den valgte postkasse. Mailene ændres ikke. Der tilføjes ingen
premium-connector, Azure-funktion eller ekstern tjeneste. OneDrive-lister bruger
connectorens paginering. En ufuldstændig liste eller overskredet løkkegrænse
giver en fejl frem for en vellykket, ufuldstændig dubletkontrol.

Flowet genlæser metadata for mails med vedhæftninger siden startdatoen; det
henter kun vedhæftninger for endnu ikke behandlede mail-ID'er. Dubletkontrollen
læser eksisterende PDF'er med samme byteantal som den nye PDF. Større mængder
mail/filer kan derfor øge køretid og connectorforbrug. Hold øje med kørslerne.

## Lokal verifikation og genbygning

```powershell
py -m unittest discover -v
py validate_package.py
py build_shared_solution.py
```

Kør fra `power-automate`. Den lokale scenarietest fortolker det genererede flows
WDL med syntetiske connector-responser. Den verificerer altså den udstedte
flowlogik, men erstatter ikke Microsofts importvalidering eller en rigtig mailtest.

v5 genbruger den Microsoft-genererede v4-solutioncontainer. Kun workflowets
definition og solutionens version/beskrivelse ændres; workflow-ID, publisher,
connection references, draft-status og øvrige ZIP-dele bevares. De normaliserede
v5-filer ligger i `solution-v5/`. v4-pakken og dens byggefiler er bevaret.

Live-status: **ikke importeret eller testet i tenant i denne session**.
Playwright-browseren viste Microsofts login-side.

Kilder: [Office 365 Outlook-connector](https://learn.microsoft.com/en-us/connectors/office365/),
[OneDrive for Business-connector](https://learn.microsoft.com/en-us/connectors/onedriveforbusiness/),
[Graph: list messages](https://learn.microsoft.com/en-us/graph/api/user-list-messages),
[Power Automate-variabler](https://learn.microsoft.com/en-us/power-automate/create-variable-store-values),
[workflow-handlinger](https://learn.microsoft.com/en-us/azure/logic-apps/logic-apps-workflow-actions-triggers).
