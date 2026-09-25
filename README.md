# IBB Power BI MCP Server

MCP-Server (Streamable HTTP) für Power BI: Berichte aus kuratierten Vorlagen erzeugen, Datasets
aktualisieren, eigene Berichte verwalten. Wird von `showcase-ibb-chat` aufgerufen, der
`acting_as_user` serverseitig setzt (nie das Modell). Getrennt vom SQL-Server, weil Power BI eine
eigene Sicherheitsdomäne mit eigenen Zugangsdaten ist.

## Entscheidungen zu den schwierigen Fragen

| Frage | Entscheidung und Grund |
|---|---|
| Wer darf ein Dataset nutzen? | Wer alle Impala-Objekte abfragen darf, die das Dataset im Katalog unter `requires` nennt. Geprüft wie im Doc-Server per `SELECT 1 FROM db.objekt LIMIT 0` über Impala doAs, Ranger bleibt die einzige Quelle der Rechte. Ein Dataset ohne `requires` ist ungültig, sonst wäre es öffentlich. |
| Nutzer-Token statt Service Principal? | Nicht möglich. Der Chat kennt nur die Cloudera-Identität (SSO über Knox), keinen Entra-Token, ein OBO-Tausch hat also nichts zum Tauschen. Alle Power-BI-Aufrufe laufen als ein Service Principal, aber nur für Datasets, Vorlagen und den einen Agent-Workspace aus dem Katalog. |
| Zeilenrechte (RLS)? | Der Server führt keine DAX-Abfragen aus. Execute Queries unterstützt für Service Principals weder RLS noch SSO-Datasets. Den Bericht öffnet der Nutzer mit seinem eigenen Entra-Login, dort greift RLS nativ. Die Zahlen für den Chat kommen weiter aus SQL. |
| Semantic Model lesen? | Nicht über die REST-API möglich (Execute Queries lehnt INFO-Funktionen und DMV ab). Das BI-Team beschreibt Tabellen und Measures im Katalog. |
| Visuals frei erzeugen? | Über die Fabric REST API (Create Report mit PBIR-Definition) ja, aber nur aus festen Vorlagen im Code (`pbir.py`): Karte, Balkendiagramm, optional Datumsslicer. Der Server schreibt die Dateien, das Modell wählt nur Spalten und Titel. Auf einem Pro-Workspace live bestätigt. |
| Woher kommen die Daten des Berichts? | `create_report_from_query` führt eine SELECT-Abfrage als der Nutzer aus (Impala doAs, Ranger entscheidet) und lädt genau dieses Ergebnis in ein neues Push-Dataset. Es ist eine Momentaufnahme, keine Live-Verbindung. |
| Refresh-Limit? | Auf Shared Capacity sind acht Refreshes pro Tag erlaubt, geplante eingerechnet. Der Server liest die Refresh-Historie von Power BI und lehnt ab, wenn einer läuft, der letzte weniger als 10 Minuten zurückliegt oder das Tageslimit des Datasets (Standard 4) in den letzten 24 Stunden erreicht ist. Kein eigener Zustand, gilt auch nach Neustarts. |
| Berichts-Wildwuchs? | Erzeugte Berichte liegen nur im Agent-Workspace, heißen `<Name> [agent:<nutzer>:<datum>]`, jeder Nutzer darf höchstens 10 haben und nur eigene auflisten, abfragen und löschen. `scripts/cleanup_reports.py` löscht Berichte nach N Tagen (Standard 30, ohne `--apply` nur Vorschau). |
| Fehlt die Berechtigung oder das Objekt? | Beides liefert dieselbe Antwort, damit nichts über Existenz verraten wird. |

## Tools

`create_report_from_query` (Bericht aus einer Abfrage, ohne Katalog), `list_my_reports`, `get_report_status`,
`delete_generated_report` (löscht auch das zugehörige Dataset), und die Katalog-Werkzeuge `list_powerbi_datasets`,
`get_powerbi_semantic_model`, `list_report_templates`, `create_powerbi_report`, `refresh_powerbi_dataset`.
Alle nehmen `acting_as_user`, das Modell sieht dieses Feld nicht. Das Modell kennt nur die Schlüssel
aus dem Katalog, nie Power-BI-GUIDs.

## Katalog

`catalog.json` beschreibt Agent-Workspace, Datasets (GUIDs, Beschreibung, `requires`, Tabellen und
Measures) und Vorlagen. Der eingecheckte Stand hat nur Platzhalter-GUIDs, solange die noch drin sind,
melden die Katalog-Werkzeuge "Katalog enthält noch Platzhalter". `create_report_from_query` braucht nur den echten `agent_workspace_id`. Die Struktur wird beim Laden streng geprüft.

## Voraussetzungen auf der Power-BI-Seite

1. Entra App registrieren, Client Secret anlegen, keine API-Berechtigungen hinzufügen. Tenant-ID, Client-ID
   und Secret in 1Password im Item `showcase-ibb-powerbi-sp` ablegen (`tenant-id`, `client-id`, `client-secret`).
2. Sicherheitsgruppe anlegen, den Service Principal hineinlegen.
3. Power-BI-Admin-Portal, Tenant-Einstellungen, Entwicklereinstellungen: "Allow service principals to use Power BI
   APIs" für diese Gruppe aktivieren.
4. Service Principal als Mitglied oder Admin zu Vorlagen-Workspace, Dataset-Workspaces und Agent-Workspace hinzufügen.
5. Semantic Models für die vier Datasets bauen, gebunden an die `v_*_official`-Views, und Vorlagenberichte darauf.
   Vorlage und Ziel-Dataset müssen dasselbe Modell (Tabellen, Spalten, Measures) haben, sonst brechen die Visuals.
6. RLS-Rollen im Modell so anlegen, dass sie die Ranger-Regeln abbilden. Das ist Handarbeit und die größte
   Abweichungsgefahr zwischen Ranger und Power BI.
7. Nutzer als Viewer in den Agent-Workspace, am besten über eine Entra-Gruppe.
8. GUIDs in `catalog.json` eintragen, committen.
9. Netzweg: Power BI muss Impala erreichen. Das Warehouse erlaubt nur bekannte IPs, empfohlen ist ein
   On-premises Data Gateway auf einer kleinen Maschine im selben VPC, dessen IP in
   `whitelistWorkloadAccessIpCIDRs` steht.

## Umgebungsvariablen

| Variable | Zweck |
|---|---|
| `PBI_TENANT_ID`, `PBI_CLIENT_ID`, `PBI_CLIENT_SECRET` | Service Principal |
| `IMPALA_HOST`, `IMPALA_PORT`, `IMPALA_HTTP_PATH`, `IMPALA_PROXY_USER`, `IMPALA_PROXY_PASSWORD` | Rechteprüfung, wie beim SQL-Server |
| `POWERBI_CATALOG_PATH` | Optional, Standard `catalog.json` |
| `MCP_TRANSPORT`, `APP_PORT` | Wie bei den anderen Servern |

## Deployment

Im Repo `showcase-ibb-main`: `make deploy APP=powerbi` (Secrets aus 1Password, `is_web_app: false`). Interne Adresse:
`http://showcase-ibb-powerbi-mcp-server.serving-apps.svc.cluster.local:80/mcp`.

## Bekannte Grenzen

- Nutzer im Agent-Workspace sehen dort alle Berichte mit den Namen der anderen. Wer das nicht will,
  braucht einen Workspace je Team.
- Die Rechteprüfung sagt, dass ein Nutzer die Views abfragen darf, nicht dass Power-BI-RLS dieselben Zeilen
  freigibt. Beides muss zusammenpassen.
- Ein Refresh gilt als "läuft", solange die Historie einen Eintrag mit Status Unknown ohne Endzeit zeigt.
- Auf Pro-Lizenz sind Refreshes und Kapazität knapp, XMLA und Modellautomatisierung brauchen Premium oder Fabric.
  Das Lizenzmodell ist noch nicht geklärt.

## Noch nicht live getestet

Bewusst nur mit Mock-Transport getestet, Aufrufformen laut Microsoft-Referenz (Stand 2026-09-25):
Refresh, Refresh-Historie, Clone, Berichte auflisten, Bericht löschen. Vor dem ersten echten Lauf prüfen:

- Token-Endpunkt und Scope `https://analysis.windows.net/powerbi/api/.default` (Standardfluss, nicht in den
  gelesenen Seiten wörtlich bestätigt).
- Ob ein Refresh mit Service Principal `notifyOption: NoNotification` akzeptiert. Laut Referenz ist die
  Benachrichtigung für Service Principals nicht anwendbar.
- Ob Clone Report mit `targetModelId` in einen anderen Workspace bei getrennten Dataset- und
  Vorlagen-Workspaces wie erwartet bindet (die Referenz nennt ein "shared dataset" im Ziel-Workspace).
- Ob die Refresh-Historie für Nutzer mit Member-Rolle des Service Principals lesbar ist (laut Referenz
  Schreibrecht auf das Dataset nötig).
- Die Rechteprüfung mit echtem Ranger (`DENIAL_MARKERS` in `src/powerbi_mcp_server/access.py`).

## Daten im Agent-Workspace

Jeder Bericht bringt sein eigenes Push-Dataset mit den Zeilen der Abfrage mit. Wer im Agent-Workspace Mitglied oder
Viewer ist, kann diese Datasets öffnen und sieht die Zeilen, die der Ersteller sehen durfte. Deshalb den Workspace nur
Personen mit gleichem Datenzugriff geben, oder einen Workspace je Team anlegen.
