# RIN08-Live

RIN08-Live ist eine Android-Anwendung zur Echtzeit-Erfassung und Bewertung einer Reise nach den Vorgaben der **RIN 2008** (FGSV Richtlinien für integrierte Netzgestaltung).
Die App berechnet fortlaufend die Reiseweite, Luftlinienweite, die Luftliniengeschwindigkeit und klassifiziert die erfassten Datenpunkte fortlaufend in SAQ-Stufen (A bis F).

## Features
* **Echtzeit-Tracking:** GPS-Tracking im 2-Sekunden-Intervall.
* **Live-Bewertung:** Fortlaufende Berechnung von Gesamtdistanz, Luftlinie, V-Aktuell (letzte 5 Datenpunkte) und V-Luftlinie.
* **Datenvisualisierung:** Dynamisches Live-Plotting der gefahrenen Werte gegen die SAQ-Grenzwerte via Chart.js.
* **Konfigurierbarkeit:** Anpassbare SAQ-Kurvenparameter (`a`, `b`, `c`) und Filter-Schwellenwerte für verschiedene Modi (PKW, ÖV, IÖ).
* **Lokale Persistenz:** Speicherung der Messpunkte in einer nativen SQLite-Datenbank.

## Bekannte Probleme (Known Issues)
* **Daten-Export defekt:** Aktuell schlagen alle Export-Routen (Teilen, Speichern, Zwischenablage) aufgrund von Restriktionen der nativen WebView-Bridge fehl. Die Messdaten werden sicher in der Datenbank erfasst, können momentan aber nicht aus der App exportiert werden.

## Installation & Berechtigungen

Die App wird als APK-Datei bereitgestellt und muss manuell installiert werden (Sideloading).

### 1. APK Installieren (Sideload)
1. Lade die aktuelle `RIN08-Live.apk` aus dem Bereich **[Releases](#)** herunter.
2. Öffne die Datei auf deinem Android-Gerät.
3. Falls eine Sicherheitswarnung erscheint: Erlaube die Installation aus "Unbekannten Quellen" für deinen Browser oder Dateimanager.

### 2. Hintergrund-Standort aktivieren (Zwingend erforderlich!)
Damit die GPS-Aufzeichnung nicht vom Android-System beendet wird, sobald der Bildschirm ausgeht, benötigt die App dauerhaften Zugriff auf den Standort:
1. Öffne die Android **Einstellungen** ➔ **Apps** ➔ **RIN08-Live**.
2. Tippe auf **Berechtigungen** ➔ **Standort**.
3. Wähle die Option **"Immer zulassen"** (Allow all the time). 

## Build Instructions

Das Projekt basiert auf der WebView-Umgebung [iappyxOS]. Die Architektur besteht aus einer Single-File-Application.

1. Erstelle ein neues Projekt in der iappyxOS-Umgebung.
2. Füge den Quellcode der `index.html` aus diesem Repository als Hauptdatei ein.
3. Aktiviere die Berechtigungen `ACCESS_FINE_LOCATION` und `FOREGROUND_SERVICE`.
4. Kompiliere die APK direkt über das Build-System.
