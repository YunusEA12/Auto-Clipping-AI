# Facecam-Quellenschutz

Anlass: TikTok 7702120159506337046 zeigt oben einen vergroesserten Ausschnitt der unten wiederholten Interview-Szene. Die dazugehoerige Quellaufnahme und Server-Metadaten lagen bei der Korrektur nicht lokal vor; die Regression ist mit simulierten Erkennungsboxen abgesichert, nicht als erfolgreiche Neuberechnung dieses konkreten Videos ausgegeben.

Ein erkanntes Gesicht beweist keine separate Streamer-Kamera. Vor jedem Split-Screen wird deshalb facecam_source geprueft. Automatische Kandidaten benoetigen mindestens fuenf lesbare Quellstichproben und mindestens 80 Prozent Persistenz. Die rohe Gesichtsbox muss klein (maximal 35 Prozent Bildbreite, 45 Prozent Bildhoehe) und randnah sein. Zentrale oder grosse Interviewgesichter, fehlende Scans und ungueltige Bereiche fuehren zum unbeschnittenen Vollbild mit unscharfem Hintergrund. Diese geometrische Naeherung ist keine biometrische Identifikation; auch ein kleines randnahes Inhaltsgesicht kann aehnlich aussehen. Ungewoehnliche echte Kamerapositionen koennen vorsorglich im Vollbild erscheinen.

Eine konfigurierte Facecam-Region ist der explizite Kameraanker: Im Split-Screen wird genau dieser Bereich verwendet, ohne ihn durch ein staerkeres global erkanntes Gesicht oder einen persistenten Interviewteilnehmer zu ersetzen. Die bestehende zeitliche Pruefung (vier von fuenf Gesichtstreffern im Ausschnitt) bleibt aktiv. Eine falsch konfigurierte Region muss weiter anhand der Quelle berichtigt werden.

facecam_source sowie layout_validation dokumentieren die Quelle und den Fallback. auto_qc.checks.facecam_source dokumentiert, ob eine separate Facecam zugesichert wird; beim sicheren Vollbild ist dies nicht anwendbar. Archiv-Metadaten bewahren die Nachweise. Der Guard gilt auch fuer erzwungenes Split-Screen. Eigenstaendige Ganzbild-/Multi-Cam-Modi werden durch diese Korrektur nicht als identifizierte Streamer-Kamera zertifiziert.

Bestehende veroeffentlichte Videos werden nicht geloescht, ersetzt oder erneut gepostet. Ein Code-Push allein ist kein verifiziertes Produktionsdeployment.
