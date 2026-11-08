# GeoChat

GeoChat is a GIS agent application built with Streamlit, DeepSeek, and LangGraph. Users can upload geospatial data and use natural language to create maps, run spatial analyses, manage layers, and export maps or data.

## 1. For Developers: Project Structure

The project is organized into the user interface, agent, GIS processing, map rendering, and session state:

| Path | Responsibility |
|---|---|
| `frontend.py` | Streamlit interface, file uploads, chat, and map display |
| `agent/` | LangGraph workflow, model configuration, and GIS tools |
| `core/` | Datasets, layers, and user session state |
| `gis/` | Data loading, coordinate processing, and spatial analysis |
| `maps/` | Folium map rendering and HTML, PNG, and PDF output |
| `tests/` | Automated tests and acceptance materials |

The main entry point is `frontend.py`; the agent factory is `agent.graph.create_agent_session`. Dependency lists and version constraints are in `tests/requirements.txt` and `tests/constraints.txt`, respectively.

## 2. For Users: Features and How to Use Them

### What the Application Can Do

GeoChat helps users work with geospatial data, create maps, and perform statistical analysis through natural language. Map creation is the most complete area; some data analysis and statistical analysis capabilities are still being developed or planned.

#### Part 1: Data Upload and Analysis

GeoChat supports GeoJSON, JSON, and CSV files, including the use of multiple files in one session. Some spatial analyses are available, including distance calculations, buffers, area or density calculations, and random forest spatial prediction. Data quality checks, attribute joins, complex spatial relationships, filtering, and overlay analysis are not yet fully implemented.

#### Part 2: Map Creation

Create and combine map layers from uploaded data, adjust maps, layers, and titles using natural language, and export the current map or visible layers. See “Basic Usage” below for supported map types and examples.

#### Part 3: Statistical Analysis

Planned capabilities include aggregation by area, metric calculations, area change comparisons, sorting and Top-N queries, and exporting statistical tables to CSV. These general statistical workflows are not yet fully implemented.

### Installation and Startup (Windows / PowerShell)

Run the following commands from the project root. These commands create a virtual environment with Python 3.14. If `.venv` already exists, you can skip the first command.

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r tests\requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

Set your DeepSeek API key and start the application in the same PowerShell window:

```powershell
$env:DEEPSEEK_API_KEY = "<your-api-key>"
.\.venv\Scripts\python.exe -m streamlit run frontend.py
```

You can also store the API key in a `.env` file in the project root. Do not commit real credentials to version control. PNG and PDF exports require Playwright Chromium; the browser usually only needs to be installed once.

### Basic Usage

After uploading one or more GeoJSON, JSON, or CSV files, enter a natural-language request in the chat. Replace the example field and layer names below with names from your own data.

#### Part 1: Data Upload and Analysis

| Feature | Example request | Notes | Status |
|---|---|---|---|
| Upload and combine data | Upload multiple files, then ask: “Please analyze the school, bus stop, and administrative boundary data I uploaded together.” | Multiple GeoJSON, JSON, or CSV files can be used in the same session. | Implemented |
| Inspect data | “Tell me which datasets are currently uploaded and what geometry types and attributes they contain.” | Field detection depends on the data. | Implemented |
| Check and transform coordinate reference systems | “Check the CRS of my datasets.” “Transform the school dataset to EPSG:32632.” | CRS checks are supported. A target CRS must be specified for transformation; unknown source CRS values are not guessed, and the source is preserved. | Implemented |
| Check data quality | “Check the data for missing coordinates, anomalous records, and invalid geometries.” | Reports issue counts; it does not repair data. | Implemented |
| Match semantically equivalent fields | “Find fields that may refer to the same concept, such as `name` and `Stadtteil`.” | Suggests candidates from field-name patterns and value overlap; full synonym and cross-language understanding is not supported. | Partially implemented |
| Join attribute tables | “Match the population table to the administrative boundaries using a common field.” | Creates temporary field mappings and reports matched, unmatched, and duplicate keys; it does not merge fields into a new dataset. | Implemented |
| Query distances | “Find schools with no bus stop within 500 meters.” “Calculate the distance from each school to its nearest bus stop.” | Supports within-layer pair distances, cross-layer nearest-neighbor and threshold queries, using a spatial index. Returns a text summary and distance table; it does not automatically create a filtered result layer. | Implemented |
| Query spatial relationships | “Assign each school to an administrative area.” “Check whether buildings intersect the flood area.” | Supports point-in-polygon and intersects queries, reports matches, nonmatches, and multiple matches, and creates a result layer. | Implemented |
| Filter by attributes or time | “Filter for accidents from the last three years.” “Select only one type of restaurant.” | Supports category, numeric, and date conditions. The filtered features are added as a new layer; source data is unchanged. | Implemented |
| Buffer analysis | “Create a 500-meter buffer around schools, parks, or candidate locations.” | Supports point, line, and polygon buffers, calculated in a local UTM CRS with distance and geometry checks. | Implemented |
| Spatial overlay analysis | “Find areas close to parks and far from roads.” “Compare two green-space layers.” | Supports polygon intersection, difference, union, and symmetric difference; operations can be combined in sequence. | Implemented |
| Manage derived data | “Keep analysis results as new data while preserving the source layers.” | Analyses can create session datasets or map layers. Visible vector layers can be exported as GeoJSON; a general save-and-manage workflow is not available. | Implemented |
| Spatial density and hotspot detection | “Identify high-density areas from the distribution of bicycle accidents.” | Counts points in a grid; defaults to 500-meter cells and marks hotspots at the 90th percentile of occupied cells. This is not a statistical significance test. | Implemented |

#### Part 2: Map Creation

| Feature | Example request | Notes | Status |
|---|---|---|---|
| Ask about available features | “What features do you support?” | Ask about the application's current capabilities. | Implemented |
| Get a visualization recommendation | “Based on the data I uploaded, recommend a suitable visualization.” | Recommends only supported map types and gives a brief reason. The request does not create a map by itself. | Implemented |
| Display multiple files together | “Visualize all the files I uploaded together.” | Combines multiple datasets on one map. | Implemented |
| Create a heatmap | “Show my point data as a heatmap.” | Shows the spatial density of point data. | Implemented |
| Create a choropleth | “Create a choropleth map using the `unemployment_rate` attribute.” | Shades polygon features by the specified numeric field. | Implemented |
| Create ordinary or proportional point maps | “Show my point data as an ordinary point map.” “Create a proportional point map using `visitors_daily`.” | A proportional point map represents meaningful numeric values with symbol size. | Implemented |
| Create ordinary or proportional line maps | “Show my line data as an ordinary line map.” “Create a proportional line map using `traffic_volume`.” | A proportional line map represents meaningful numeric values with line width. | Implemented |
| Create a GeoJSON layer | “Display my data as a GeoJSON map.” | Displays point, line, or polygon geometry and its attributes. | Implemented |
| Change the map type | “Change the ordinary point map to a proportional point map.” “Change the GeoJSON polygon map to a choropleth.” | Converts to a map type compatible with the data geometry and attributes. | Implemented |
| Ask which visual properties can be changed | “What visual properties of the current map can I change?” | Available properties vary by map type. | Implemented |
| Change outline color | “Change the layer outline to red.” | Applies to layers that support outline styling. | Implemented |
| Adjust line width | “Make the lines thicker.” | Changes line-map or boundary line width. | Implemented |
| Adjust opacity | “Reduce the layer opacity.” | The effect depends on the layer type. | Implemented |
| Change polygon fill color | “Change the polygon fill color to blue.” | Choropleths support separate fill and boundary outline colors. | Implemented |
| Remove a layer | “Remove the `Bahnhöfe_points` layer from the map.” | Removing a map layer does not delete its uploaded source file. | Implemented |
| Hide or show a layer | “Hide the `Buslinien_line` layer.” “Show the `Buslinien_line` layer.” | The buttons in the left layer panel can also be used. | Implemented |
| Toggle the basemap | Use the OpenStreetMap checkbox on the left side of the page. | The checkbox turns the basemap on or off. | Implemented |
| Ask about export formats | “Can I export a map? Which formats are supported?” | HTML, PNG, PDF, and GeoJSON are supported. | Implemented |
| Export a map or data | “Export the current map as HTML/PNG/PDF/GeoJSON.” | Click the download button in the chat message when the export is ready. GeoJSON does not include the basemap or styling; multiple visible layers are packaged in a ZIP file. Online basemap tiles in HTML, PNG, and PDF exports require an internet connection. | Implemented |
| Set a map title | “Set the map title to ‘Public Transport Facilities in Dresden’.” | Sets a custom title for the current map. | Implemented |
| Generate a title automatically | “Generate a title based on all currently visible layers.” | Creates a title from the visible layers. | Implemented |
| Remove the map title | “Remove the current title.” | Clears the current map title. | Implemented |

Proportional point and line maps exclude identifier fields such as house numbers and postal codes. If multiple meaningful numeric fields are available, you can specify one. Otherwise, the agent selects a field and reports which field it used and what other fields are available.

#### Part 3: Statistical Analysis

The statistical analysis module is still being developed. The table below describes the current supported scope; cross-year change comparison remains in development.

| Feature | Example request | Status |
|---|---|---|
| Count or aggregate by area | “Count the schools in each administrative area.” “Sum the population in each administrative area.” | Implemented with `aggregate_features_by_area`; returns a table and reusable result layer. |
| Calculate ratios or indicators | “Calculate the school coverage rate for each administrative area.” | Implemented with `calculate_derived_indicator` for row-wise numeric field ratios and scaled rates. |
| Compare area or change | “Calculate the green-space area in each administrative area.” “Compare the change in green-space area between two years.” | Partially implemented: exact polygon intersection area by area is available; temporal comparison is not implemented. |
| Sort and return Top-N results | “List the five administrative areas with the highest unemployment rates.” | Implemented with `rank_dataset_by_field`; returns a text table only. |
| Create and export a statistical table | “Create a statistical table by administrative area and export it as CSV.” | Implemented with `export_statistical_table`; CSV is returned as a downloadable attachment. |

---

# GeoChat (Deutsche Version)

GeoChat ist eine GIS-Agent-Anwendung auf Basis von Streamlit, DeepSeek und LangGraph. Nutzerinnen und Nutzer können Geodaten hochladen und per natürlicher Sprache Karten erstellen, räumliche Analysen durchführen, Layer verwalten sowie Karten und Daten exportieren.

## 1. Für Entwickler: Projektstruktur

Das Projekt ist in Benutzeroberfläche, Agent, GIS-Verarbeitung, Kartendarstellung und Sitzungsstatus gegliedert:

| Pfad | Aufgabe |
|---|---|
| `frontend.py` | Streamlit-Oberfläche, Datei-Upload, Chat und Kartenanzeige |
| `agent/` | LangGraph-Workflow, Modellkonfiguration und GIS-Werkzeuge |
| `core/` | Datensätze, Layer und Sitzungsstatus |
| `gis/` | Datenimport, Koordinatenverarbeitung und räumliche Analysen |
| `maps/` | Folium-Kartendarstellung sowie HTML-, PNG- und PDF-Ausgabe |
| `tests/` | Automatisierte Tests und Abnahmematerialien |

Der Haupteinstiegspunkt ist `frontend.py`; die Agent-Factory ist `agent.graph.create_agent_session`. Abhängigkeiten und Versionsvorgaben stehen in `tests/requirements.txt` beziehungsweise `tests/constraints.txt`.

## 2. Für Nutzer: Funktionen und Bedienung

GeoChat unterstützt die Arbeit mit Geodaten, die Kartenerstellung und statistische Analysen per natürlicher Sprache. Die Kartenerstellung ist am weitesten entwickelt; einige Funktionen zur Datenanalyse und Statistik befinden sich noch in Entwicklung oder Planung.

### Was die Anwendung kann

#### Teil 1: Daten-Upload und Analyse

GeoChat unterstützt GeoJSON-, JSON- und CSV-Dateien. Mehrere Dateien können innerhalb einer Sitzung verwendet werden. Einige räumliche Analysen sind verfügbar, darunter Distanzberechnungen, Puffer, Flächen- oder Dichteberechnungen sowie räumliche Vorhersagen mit Random Forest. Datenqualitätsprüfungen, Attribut-Joins, komplexe räumliche Beziehungen, Filter und Overlay-Analysen sind noch nicht vollständig implementiert.

#### Teil 2: Kartenerstellung

Erstellen und kombinieren Sie Kartenlayer aus hochgeladenen Daten, passen Sie Karten, Layer und Titel per natürlicher Sprache an und exportieren Sie die aktuelle Karte oder sichtbare Layer. Unterstützte Kartentypen und Beispiele finden Sie weiter unten unter „Grundlegende Verwendung“.

#### Teil 3: Statistische Analyse

Geplant sind Aggregationen nach Gebieten, Kennzahlenberechnungen, Vergleiche von Flächenänderungen, Sortierungen und Top-N-Abfragen sowie der Export statistischer Tabellen als CSV. Diese allgemeinen statistischen Workflows sind noch nicht vollständig implementiert.

### Installation und Start (Windows / PowerShell)

Führen Sie die folgenden Befehle im Projektverzeichnis aus. Damit wird eine virtuelle Umgebung mit Python 3.14 erstellt. Falls `.venv` bereits vorhanden ist, können Sie den ersten Befehl überspringen.

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r tests\requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

Legen Sie im selben PowerShell-Fenster den DeepSeek-API-Schlüssel fest und starten Sie die Anwendung:

```powershell
$env:DEEPSEEK_API_KEY = "<your-api-key>"
.\.venv\Scripts\python.exe -m streamlit run frontend.py
```

Alternativ können Sie den API-Schlüssel in einer `.env`-Datei im Projektverzeichnis speichern. Echte Zugangsdaten dürfen nicht in die Versionsverwaltung übernommen werden. Für PNG- und PDF-Exporte wird Playwright Chromium benötigt. Der Browser muss normalerweise nur einmal installiert werden.

### Grundlegende Verwendung

Laden Sie eine oder mehrere GeoJSON-, JSON- oder CSV-Dateien hoch und geben Sie anschließend eine Anfrage in natürlicher Sprache im Chat ein. Ersetzen Sie die Beispiel-Feld- und Layernamen durch die Namen in Ihren eigenen Daten.

#### Teil 1: Daten-Upload und Analyse

| Funktion | Beispielanfrage | Hinweise | Status |
|---|---|---|---|
| Daten hochladen und kombinieren | Laden Sie mehrere Dateien hoch und fragen Sie: „Analysiere bitte gemeinsam die hochgeladenen Schul-, Bushaltestellen- und Verwaltungsgrenzdaten.“ | Mehrere GeoJSON-, JSON- oder CSV-Dateien können in derselben Sitzung verwendet werden. | Implementiert |
| Daten prüfen | „Welche Datensätze sind aktuell hochgeladen und welche Geometrietypen und Attribute enthalten sie?“ | Die Erkennung von Feldern hängt von den Daten ab. | Implementiert |
| Koordinatenreferenzsysteme prüfen und transformieren | „Prüfe die CRS meiner Datensätze.“ „Transformiere den Schul-Layer nach EPSG:32632.“ | CRS-Prüfungen werden unterstützt. Für eine Transformation muss ein Ziel-CRS angegeben werden; unbekannte Quell-CRS werden nicht geraten, und die Quelle bleibt erhalten. | Implementiert |
| Datenqualität prüfen | „Prüfe die Daten auf fehlende Koordinaten, auffällige Datensätze und ungültige Geometrien.“ | Meldet die Anzahl der Befunde; die Daten werden nicht automatisch repariert. | Implementiert |
| Semantisch gleichbedeutende Felder erkennen | „Finde Felder mit möglicherweise gleicher Bedeutung, zum Beispiel `name` und `Stadtteil`.“ | Schlägt Kandidaten anhand von Feldnamensmustern und Wertüberschneidungen vor; vollständige Synonym- und Spracherkennung wird nicht unterstützt. | Teilweise implementiert |
| Attributtabellen verknüpfen | „Ordne die Bevölkerungstabelle über ein gemeinsames Feld den Verwaltungsgrenzen zu.“ | Erstellt temporäre Feldzuordnungen und meldet passende, nicht passende und doppelte Schlüssel; Felder werden nicht in einen neuen Datensatz übernommen. | Implementiert |
| Distanzen abfragen | „Finde Schulen ohne Bushaltestelle im Umkreis von 500 Metern.“ „Berechne die Entfernung jeder Schule zur nächsten Bushaltestelle.“ | Unterstützt paarweise Distanzen innerhalb eines Layers, layerübergreifende nächste Nachbarn und Umkreisabfragen; ein räumlicher Index beschleunigt die Suche. Ergebnis ist ein Textbericht mit Distanzangaben, kein automatisch gefilterter Layer. | Implementiert |
| Räumliche Beziehungen abfragen | „Ordne jede Schule einem Verwaltungsgebiet zu.“ „Prüfe, ob Gebäude das Überschwemmungsgebiet schneiden.“ | Unterstützt Punkt-in-Polygon und Überschneidungen, meldet Treffer, Nichttreffer und Mehrfachtreffer und erstellt einen Ergebnislayer. | Implementiert |
| Nach Attributen oder Zeit filtern | „Filtere Unfälle aus den letzten drei Jahren.“ „Wähle nur eine bestimmte Restaurantart aus.“ | Unterstützt Kategorien, Zahlen- und Datumsbedingungen. Die Treffer werden als neuer Layer hinzugefügt; die Quelldaten bleiben unverändert. | Implementiert |
| Pufferanalyse | „Erstelle einen 500-Meter-Puffer um Schulen, Parks oder mögliche Standorte.“ | Unterstützt Puffer um Punkte, Linien und Flächen; Berechnung im lokalen UTM-CRS mit Distanz- und Geometrieprüfung. | Implementiert |
| Räumliche Overlay-Analyse | „Finde Gebiete in der Nähe von Parks und weit entfernt von Straßen.“ „Vergleiche zwei Grünflächen-Layer.“ | Unterstützt Schnittmenge, Differenz, Vereinigung und symmetrische Differenz von Polygonen; Operationen können nacheinander kombiniert werden. | Implementiert |
| Abgeleitete Daten verwalten | „Speichere Analyseergebnisse als neue Daten und behalte die Quell-Layer.“ | Analysen können Sitzungsdatensätze oder Kartenlayer erstellen. Sichtbare Vektorlayer lassen sich als GeoJSON exportieren; eine allgemeine Speicher- und Verwaltungsfunktion gibt es nicht. | Implementiert |
| Räumliche Dichte und Hotspots erkennen | „Ermittle Gebiete mit hoher Dichte anhand der Verteilung von Fahrradunfällen.“ | Zählt Punkte in einem Raster; Standard sind 500-Meter-Zellen und die Markierung des 90. Perzentils belegter Zellen. Kein Test auf statistische Signifikanz. | Implementiert |

#### Teil 2: Kartenerstellung

| Funktion | Beispielanfrage | Hinweise | Status |
|---|---|---|---|
| Verfügbare Funktionen erfragen | „Welche Funktionen unterstützt du?“ | Fragen Sie nach den aktuellen Funktionen der Anwendung. | Implementiert |
| Visualisierung empfehlen lassen | „Empfiehl anhand meiner hochgeladenen Daten eine geeignete Visualisierung.“ | Es werden nur unterstützte Kartentypen mit einer kurzen Begründung empfohlen. Die Anfrage erstellt nicht automatisch eine Karte. | Implementiert |
| Mehrere Dateien gemeinsam anzeigen | „Visualisiere alle von mir hochgeladenen Dateien gemeinsam.“ | Kombiniert mehrere Datensätze auf einer Karte. | Implementiert |
| Heatmap erstellen | „Stelle meine Punktdaten als Heatmap dar.“ | Zeigt die räumliche Dichte von Punktdaten. | Implementiert |
| Choropleth erstellen | „Erstelle eine Choropleth-Karte mit dem Attribut `unemployment_rate`.“ | Färbt Polygon-Features nach dem angegebenen numerischen Feld ein. | Implementiert |
| Einfache oder proportionale Punktkarte erstellen | „Stelle meine Punktdaten als einfache Punktkarte dar.“ „Erstelle eine proportionale Punktkarte anhand von `visitors_daily`.“ | Bei proportionalen Punkten werden aussagekräftige numerische Werte durch die Symbolgröße dargestellt. | Implementiert |
| Einfache oder proportionale Linienkarte erstellen | „Stelle meine Liniendaten als einfache Linienkarte dar.“ „Erstelle eine proportionale Linienkarte anhand von `traffic_volume`.“ | Bei proportionalen Linien werden aussagekräftige numerische Werte durch die Linienbreite dargestellt. | Implementiert |
| GeoJSON-Layer erstellen | „Stelle meine Daten als GeoJSON-Karte dar.“ | Zeigt Punkt-, Linien- oder Polygongeometrien und ihre Attribute. | Implementiert |
| Kartentyp ändern | „Ändere die einfache Punktkarte in eine proportionale Punktkarte.“ „Ändere die GeoJSON-Polygonkarte in eine Choropleth-Karte.“ | Der neue Kartentyp muss zu Geometrie und Attributen der Daten passen. | Implementiert |
| Änderbare visuelle Eigenschaften abfragen | „Welche visuellen Eigenschaften der aktuellen Karte kann ich ändern?“ | Die verfügbaren Eigenschaften unterscheiden sich je nach Kartentyp. | Implementiert |
| Umrandungsfarbe ändern | „Ändere die Umrandung des Layers in Rot.“ | Gilt für Layer, die eine Umrandungsgestaltung unterstützen. | Implementiert |
| Linienbreite anpassen | „Stelle die Linien dicker dar.“ | Ändert die Breite von Linien oder Begrenzungen. | Implementiert |
| Transparenz anpassen | „Verringere die Transparenz des Layers.“ | Die Wirkung hängt vom Layertyp ab. | Implementiert |
| Polygonfüllfarbe ändern | „Ändere die Füllfarbe der Polygone in Blau.“ | Bei Choropleth-Karten lassen sich Füll- und Umrandungsfarbe getrennt festlegen. | Implementiert |
| Layer entfernen | „Entferne den Layer `Bahnhöfe_points` von der Karte.“ | Beim Entfernen eines Kartenlayers wird die hochgeladene Quelldatei nicht gelöscht. | Implementiert |
| Layer ausblenden oder anzeigen | „Blende den Layer `Buslinien_line` aus.“ „Zeige den Layer `Buslinien_line` an.“ | Sie können auch die Schaltflächen im Layerbereich links verwenden. | Implementiert |
| Basiskarte ein- oder ausschalten | Verwenden Sie das OpenStreetMap-Kontrollkästchen auf der linken Seite. | Mit dem Kontrollkästchen wird die Basiskarte ein- oder ausgeschaltet. | Implementiert |
| Exportformate erfragen | „Kann ich eine Karte exportieren? Welche Formate werden unterstützt?“ | HTML, PNG, PDF und GeoJSON werden unterstützt. | Implementiert |
| Karte oder Daten exportieren | „Exportiere die aktuelle Karte als HTML/PNG/PDF/GeoJSON.“ | Klicken Sie nach dem Export auf die Download-Schaltfläche in der Chatnachricht. GeoJSON enthält weder Basiskarte noch Gestaltung; mehrere sichtbare Layer werden als ZIP-Datei gepackt. Online-Basiskartenkacheln in HTML-, PNG- und PDF-Exporten benötigen eine Internetverbindung. | Implementiert |
| Kartentitel festlegen | „Setze den Kartentitel auf ‚ÖPNV-Einrichtungen in Dresden‘.“ | Legt einen eigenen Titel für die aktuelle Karte fest. | Implementiert |
| Titel automatisch erstellen | „Erstelle einen Titel anhand aller aktuell sichtbaren Layer.“ | Generiert einen Titel aus den sichtbaren Layern. | Implementiert |
| Kartentitel entfernen | „Entferne den aktuellen Titel.“ | Löscht den aktuellen Kartentitel. | Implementiert |

Proportionale Punkt- und Linienkarten schließen Kennungsfelder wie Hausnummern und Postleitzahlen aus. Wenn mehrere aussagekräftige numerische Felder vorhanden sind, können Sie eines davon angeben. Andernfalls wählt der Agent ein Feld aus und nennt das verwendete Feld sowie weitere verfügbare Felder.

#### Teil 3: Statistische Analyse

Das Statistikmodul wird weiterentwickelt. Die Tabelle zeigt den aktuellen Umfang; der Vergleich von Veränderungen zwischen Jahren befindet sich noch in Entwicklung.

| Funktion | Beispielanfrage | Status |
|---|---|---|
| Nach Gebiet zählen oder aggregieren | „Zähle die Schulen in jedem Verwaltungsgebiet.“ „Summiere die Bevölkerung je Verwaltungsgebiet.“ | Implementiert: Zählung oder Summe je Polygongebiet, mit Tabelle und wiederverwendbarem Ergebnislayer. |
| Verhältnisse oder Kennzahlen berechnen | „Berechne die Schulversorgungsquote für jedes Verwaltungsgebiet.“ | Implementiert: berechnet zeilenweise Kennzahlen aus vorhandenen numerischen Feldern und einem Skalierungsfaktor. |
| Flächen oder Veränderungen vergleichen | „Berechne die Grünflächen je Verwaltungsgebiet.“ „Vergleiche die Veränderung der Grünflächen zwischen zwei Jahren.“ | Teilweise implementiert: Polygonflächen werden an Gebietsgrenzen geschnitten und je Gebiet summiert; der Jahresvergleich ist nicht implementiert. |
| Sortieren und Top-N abfragen | „Liste die fünf Verwaltungsgebiete mit der höchsten Arbeitslosenquote auf.“ | Implementiert: sortiert nach einem numerischen Feld und gibt eine Rangtabelle als Text zurück. |
| Statistische Tabelle erstellen und exportieren | „Erstelle eine Statistik nach Verwaltungsgebiet und exportiere sie als CSV.“ | Implementiert: CSV wird als herunterladbarer Anhang bereitgestellt. |

---

# GeoChat（中文版）

GeoChat 是一个基于 Streamlit、DeepSeek 和 LangGraph 的 GIS Agent 应用。用户可以上传地理数据，通过自然语言生成地图、执行空间分析、管理图层并导出地图或数据。

## 1. 开发者：项目结构

项目按界面、Agent、GIS 处理、地图渲染和会话状态划分：

| 路径 | 职责 |
|---|---|
| `frontend.py` | Streamlit 页面、文件上传、聊天和地图展示 |
| `agent/` | LangGraph 工作流、模型配置和 GIS 工具 |
| `core/` | 数据集、图层及用户会话状态 |
| `gis/` | 数据读取、坐标处理和空间分析 |
| `maps/` | Folium 地图渲染及 HTML、PNG、PDF 输出 |
| `tests/` | 自动化测试与验收资料 |

主要入口是 `frontend.py`；Agent 工厂为 `agent.graph.create_agent_session`。依赖列表和版本约束分别位于 `tests/requirements.txt` 和 `tests/constraints.txt`。

## 2. 使用者：应用功能与使用方法

### 应用能做什么

GeoChat 通过自然语言协助处理地理数据、制作地图和开展统计分析。地图制作功能较为完整；数据分析和统计分析仍有部分能力处于开发或规划阶段。

#### 第一部分：数据上传与分析

支持上传 GeoJSON、JSON 和 CSV，并在同一会话中使用多个文件。当前支持部分空间分析，包括距离计算、缓冲区、面积或密度计算，以及随机森林空间预测。数据质量检查、属性关联、复杂空间关系、筛选和叠置分析等能力尚未全部实现。

#### 第二部分：地图制作

支持根据上传数据创建和组合地图图层，并通过自然语言调整地图、图层和标题；也可以导出当前地图或可见图层。具体地图类型和操作方式见下方“基本使用”。

#### 第三部分：统计分析

计划支持按区域汇总、指标计算、面积变化比较、排序与 Top-N 查询，以及统计表格和 CSV 导出。目前这些通用统计工作流尚未完整实现。

### 安装和启动（Windows / PowerShell）

在项目根目录执行。以下命令使用 Python 3.14 创建项目虚拟环境；如果 `.venv` 已存在，可跳过创建步骤。

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r tests\requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

在同一个 PowerShell 窗口配置 DeepSeek API Key 并启动应用：

```powershell
$env:DEEPSEEK_API_KEY = "<your-api-key>"
.\.venv\Scripts\python.exe -m streamlit run frontend.py
```

也可以将 API Key 放在项目根目录的 `.env` 文件中。不要把真实密钥提交到版本库。PNG 和 PDF 导出需要 Playwright Chromium；安装浏览器通常只需执行一次。

### 基本使用

上传一个或多个 GeoJSON、JSON 或 CSV 文件后，在聊天框输入自然语言请求。以下示例中的字段名和图层名需要替换为你上传数据里的实际名称。

#### 第一部分：数据上传与分析

| 功能 | 使用示例 | 说明 | 实现状态 |
|---|---|---|---|
| 上传并组合数据 | 上传多个文件后询问：“请同时使用我上传的学校、公交站和行政区数据进行分析。” | 支持在同一会话中使用多个 GeoJSON、JSON 或 CSV 文件。 | 已实现 |
| 查看数据情况 | “请说明当前上传了哪些数据，以及每个数据包含什么几何类型和属性。” | 字段识别效果取决于数据内容。 | 已实现 |
| 坐标系检查与转换 | “检查各数据集的 CRS。”“将学校图层转换为 EPSG:32632。” | 支持检查 CRS；转换时需指定目标 CRS。源 CRS 未知时不会猜测，原数据保留。 | 已实现 |
| 数据质量检查 | “检查数据中的缺失坐标、异常记录和无效几何。” | 汇报问题数量，不自动修复数据。 | 已实现 |
| 语义字段匹配 | “找出两个文件中含义可能相同但名称不同的字段。” | 可根据字段名称格式及字段值重合度提出候选；尚不支持完整的同义词或跨语言语义识别。 | 部分实现 |
| 属性表关联 | “根据共同字段将人口统计表与行政区边界匹配。” | 建立临时字段对应关系，汇报匹配、未匹配和重复键；不会合并字段生成新数据集。 | 已实现 |
| 距离查询 | “找出 500 米内没有公交站的学校。”“计算每所学校到最近公交站的距离。” | 支持单图层两两距离、跨图层最近邻和阈值范围查询，并使用空间索引。结果以文字和距离表汇报，不自动生成筛选结果图层。 | 已实现 |
| 空间关系查询 | “判断每所学校属于哪个行政区。”“检查建筑是否与淹没区域相交。” | 支持点归属面和相交判断，汇报匹配、未匹配和多重匹配，并生成结果图层。 | 已实现 |
| 属性与时间筛选 | “筛选最近三年的事故。”“只选择某一类餐厅。” | 支持类别、数值和日期条件；筛选结果生成新图层，原数据不变。 | 已实现 |
| 缓冲区分析 | “为学校、公园或候选地址生成 500 米缓冲区。” | 支持点、线、面缓冲；采用本地 UTM 坐标系计算，并检查距离和几何。 | 已实现 |
| 空间叠置分析 | “找出靠近公园、远离道路的区域。”“比较两个绿地图层。” | 支持面图层相交、差集、并集和对称差集；Agent 可顺序组合操作。 | 已实现 |
| 派生数据管理 | “将分析结果保存为新数据，同时保留源图层。” | 多种分析会在当前会话中生成新数据集或图层；可将可见矢量图层导出为 GeoJSON，尚无通用的独立保存与管理流程。 | 已实现 |
| 空间密度与热点识别 | “根据自行车事故分布识别高密度区域。” | 支持点数据网格计数；默认 500 米网格，按占用网格的第 90 百分位标记热点。这不是统计显著性检验。 | 已实现 |

#### 第二部分：地图制作

| 功能 | 使用示例 | 说明 | 实现状态 |
|---|---|---|---|
| 了解功能 | “说说你都支持什么功能？” | 询问应用当前支持的功能。 | 已实现 |
| 获取可视化建议 | “根据我上传的数据，推荐一种合适的可视化方案。” | 只推荐已支持的地图类型并简要说明理由，不会因此自动生成地图。 | 已实现 |
| 同时显示多个文件 | “请同时可视化我上传的多个文件。” | 在同一张地图中组合多个数据文件。 | 已实现 |
| 创建热力图 | “将我的点数据显示为热力图。” | 用于展示点数据的空间密度。 | 已实现 |
| 创建 Choropleth | “根据 `unemployment_rate` 属性创建 Choropleth 地图。” | 按指定数值字段对面要素进行分级设色。 | 已实现 |
| 创建普通点图或比例点图 | “将我的点数据显示为普通点图。”“根据 `visitors_daily` 创建比例点图。” | 比例点图用符号大小表示有意义的数值。 | 已实现 |
| 创建普通线图或比例线图 | “将我的线数据显示为普通线图。”“根据 `traffic_volume` 创建比例线图。” | 比例线图用线宽表示有意义的数值。 | 已实现 |
| 创建 GeoJSON 图层 | “将我的数据显示为 GeoJSON 地图类型。” | 可显示点、线或面几何及其属性。 | 已实现 |
| 转换地图类型 | “将普通点图转换为比例点图。”“将 GeoJSON 面图转换为 Choropleth 地图。” | 转换为与数据几何和属性相适配的地图类型。 | 已实现 |
| 查看可修改属性 | “说说目前的地图都有哪些视觉属性可以修改。” | 可修改属性因地图类型而异。 | 已实现 |
| 修改轮廓颜色 | “将图层的轮廓颜色改为红色。” | 适用于支持轮廓样式的图层。 | 已实现 |
| 调整线宽 | “将线条显示得更粗一些。” | 修改线图或边界线宽。 | 已实现 |
| 调整透明度 | “降低图层的透明度。” | 具体效果取决于图层类型。 | 已实现 |
| 修改面填充色 | “将面要素的填充颜色改为蓝色。” | Choropleth 可分别设置填充色和边界轮廓色。 | 已实现 |
| 移除图层 | “从地图中移除图层 `Bahnhöfe_points`。” | 移除地图图层不会删除上传的源文件。 | 已实现 |
| 隐藏或显示图层 | “隐藏图层 `Buslinien_line`。”“显示图层 `Buslinien_line`。” | 也可以使用左侧图层面板按钮。 | 已实现 |
| 控制底图 | 使用页面左侧的 OpenStreetMap 复选框。 | 目前通过复选框开启或关闭底图。 | 已实现 |
| 查询导出格式 | “你是否支持地图导出？支持哪些地图导出格式？” | 当前支持 HTML、PNG、PDF 和 GeoJSON。 | 已实现 |
| 导出地图或数据 | “将当前地图导出为 HTML/PNG/PDF/GeoJSON 格式。” | 导出完成后点击聊天消息中的下载按钮。GeoJSON 不包含底图和样式；多个可见图层会打包为 ZIP。HTML、PNG 和 PDF 中的在线底图需要联网加载。 | 已实现 |
| 设置地图标题 | “请为地图添加一个标题‘德累斯顿公共交通设施分布’。” | 为当前地图设置指定标题。 | 已实现 |
| 自动生成标题 | “请根据目前所有已开启的图层，自动生成一个标题。” | 根据当前可见图层生成标题。 | 已实现 |
| 删除地图标题 | “请删除当前标题。” | 清除当前地图标题。 | 已实现 |

比例点图和比例线图会排除门牌号、邮政编码等标识字段。多个有意义的数值字段中，用户可以指定要展示的字段；未指定时，Agent 会选择一个并说明所用字段及其他可选字段。

#### 第三部分：统计分析

统计分析模块仍在开发中，以下表格说明当前支持范围；跨年份变化比较等能力仍在开发。

| 功能 | 请求示例 | 实现状态 |
|---|---|---|
| 按区域计数或汇总 | “统计每个行政区内的学校数量。”“汇总每个行政区的人口。” | 已实现：支持基于面区域计数或求和，返回统计表和可复用结果图层。 |
| 计算比例或指标 | “计算各行政区的学校覆盖率。” | 已实现：支持对已有数值字段逐行计算比值及按比例因子缩放的率。 |
| 比较面积或变化 | “计算各行政区的绿地面积。”“比较两个年份的绿地面积变化。” | 部分实现：可裁切并汇总各区内的多边形面积；跨年份变化比较尚未实现。 |
| 排序和 Top-N 查询 | “列出失业率最高的五个行政区。” | 已实现：按指定数值字段排序并以文本表格返回，不创建新图层。 |
| 生成并导出统计表 | “生成行政区统计表并导出为 CSV。” | 已实现：CSV 作为可下载附件返回，可选择属性字段，几何不导出。 |
