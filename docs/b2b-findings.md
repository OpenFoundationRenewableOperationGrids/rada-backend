# BE-01 findings: OpenEMS Backend-to-Backend (B2B) websocket

*[Français ci-dessous](#constats-be-01--websocket-backend-to-backend-b2b-dopenems)*

Story: BE-01 (#29). Tested on 7 November 2026 against OpenEMS Backend `2026.1.0` on the VPS, with the simulated edge `edge0` on the DGX Spark (PV 1.5 MW, load 1 MW, battery 4 MW / 8 MWh, reacting grid meter) and `edge1` on the VPS, offline on purpose.

## Decision

**RADA reads OpenEMS through the Backend's B2B websocket.** The risk carried since the kickoff is closed: subscriptions work on `2026.1.0`. Polling (`getEdgesChannelsValues` over B2B REST) remains the fallback; the bridge's `ChannelSource` interface allows the switch without touching translation or posting. RADA never connects to an edge directly.

## Setting it up

- The Backend ships the component but doesn't enable it: in Felix, create **`Backend2Backend.Websocket`** (defaults: port **8076**, 10 threads).
- Publish the port **on the Tailscale address only** (`100.88.150.125:8076:8076` in Compose), like Felix (8079) and the edge websocket (8081). It gives read access to every edge, so it must never be public.
- The configuration is kept in the `openems-backend-conf` volume and survives a container recreation.
- The `Backend2Backend.Rest` component (the polling fallback) exists too; it was left disabled.

## Protocol

| Item | Finding |
|---|---|
| Transport | Plain websocket `ws://`; TLS is unnecessary inside Tailscale |
| Authentication | HTTP Basic Auth on the handshake (`Authorization: Basic …`). With `Metadata.File`, **`admin:admin` is accepted** |
| Messages | JSON-RPC 2.0; `id` must be a UUID |
| `getEdgesStatus` | Requires `{"edgeIds": [...]}` (no "all edges" default: error 5007 otherwise). Returns `{"edge0":{"online":true}}` |
| `subscribeEdgesChannels` | `{"count": n, "ids": [...], "channels": [...]}`; `count` numbers the subscription, a higher one replaces it. Answers `"result":{}` |
| Notifications | `edgesCurrentData` **every 2 seconds**, subscribed channels only |
| Timestamps | **None in the notification.** The bridge stamps each value in UTC on arrival |
| Missing values | Sent as `null` (e.g. energy counters on an edge without Timedata) |
| Other methods | Through the general handler: `getEdgesChannelsValues`, and `edgeRpc` wrapping `getEdgeConfig` (asset discovery, BE-05) and `queryHistoricTimeseriesData` (needs a Backend timedata service, not configured here: not tested) |

## Behaviour

- **Offline edge:** `getEdgesStatus` reports `"online": false`, without an error. The bridge logs it and still subscribes, so values flow as soon as the edge reconnects.
- **Backend restart:** edges reconnect to the Backend by themselves. The bridge reconnects with backoff (1 s doubling to 60 s, reset when data flows) and resubscribes. The Backend then reads each subscription from its cache of every edge's latest values, which is empty until the edge has reconnected and sent them (all values on reconnection, then changes each cycle, plus a full resend every 5 minutes). Until then values arrive as `null` and the bridge posts nothing rather than empty data. Measured on 7 Nov: bridge back in **8 s**, values back after **2 min 52 s**.
- **Several clients:** each B2B connection has its own subscription, so preprod and production bridges can share one Backend (see Security and operations).

## Data observed (for BE-02)

- Units: power in W (integer), energy in Wh (cumulative, `Wh_Σ`), SoC in %.
- Signs: grid **+ import / − export**; ESS **+ discharge / − charge**; production and consumption always positive. Confirmed live with a balancing controller at grid setpoints −1 MW, 0 and +0.2 MW.
- **Energy counters need a Timedata component on the edge** (e.g. `Timedata.Rrd4j`). Without one, every counter stays empty.
- The energy-flow counters (`ProductionToEssEnergy` and the others) are OpenEMS's **allocation rule** (PV to the battery first), not a measurement, and drifted about 3 % against the measured totals in 6 minutes. Stored at most as optional metrics; no KPI is based on them.
- `_sum` has no reactive power total; reactive power is an asset-level metric.

## Security and operations

- `Metadata.File` accepts `admin:admin` on B2B and on the public web UI. **A real Metadata provider, or at least Traefik authentication on the UI, is needed before any real site connects.**
- One Backend shared by preprod and production is fine while RADA is read-only. Split it before upgrading OpenEMS (to test the upgrade in preprod first), when a real customer site connects, or when RADA starts sending commands.
- Give each environment its own B2B user once real users exist, so one can be revoked without touching the other.

## Verification (story acceptance)

| Check | Result |
|---|---|
| Live values from `edge0` received over B2B | ✔ 7 Nov, by hand (websocat), then by the bridge |
| Posted to preprod and stored | ✔ 7 Nov 15:17–16:32 from the T14: every post `201 Created`, 2 assets every 60 s, none skipped |
| Bridge as its own service on the VPS | ✔ 7 Nov 17:45 UTC: `rada-bridge-preprod`, same image as the API (`command: python -m bridge`), reaching the Backend and the API by container name on `cb_network` |
| Backend restart: rows resume within 5 minutes with no action | ✔ 7 Nov: Backend restarted 17:48:30 UTC, bridge reconnected 17:48:38, posts resumed 17:51:22 (**2 min 52 s**) |
| 24 hours of rows | ✔ 7 Nov 17:46 → 8 Nov 17:03 UTC (**23 h 17 min**, stopped early on purpose): **1,394 rows per asset** (49 and 50) out of 1,398 possible rounds. The 4 missing rounds are two 3-minute gaps on 7 Nov: 17:48–17:51 (the Backend restart test above) and 18:36–18:39 (most likely the redeploy of `rada-app-preprod` with the simulator fix). No round was missed in the 22 h 24 min after that |
| Redeploy with the review fixes | ✔ 8 Nov 17:22 UTC, commit `9113643`: connected and subscribed in under 0.1 s, then 2 assets posted every 60 s, none skipped |

---

# Constats BE-01 : websocket Backend-to-Backend (B2B) d'OpenEMS

Story : BE-01 (#29). Testé le 7 novembre 2026 avec le Backend OpenEMS `2026.1.0` du VPS, l'edge simulé `edge0` sur le DGX Spark (PV 1,5 MW, consommation 1 MW, batterie 4 MW / 8 MWh, compteur réseau réactif) et `edge1` sur le VPS, volontairement hors ligne.

## Décision

**RADA lit OpenEMS via le websocket B2B du Backend.** Le risque porté depuis le lancement est levé : les abonnements fonctionnent en `2026.1.0`. Le polling (`getEdgesChannelsValues` via B2B REST) reste la solution de repli ; l'interface `ChannelSource` du bridge permet de basculer sans toucher à la traduction ni à l'envoi. RADA ne se connecte jamais directement à un edge.

## Mise en place

- Le Backend fournit le composant sans l'activer : dans Felix, créer **`Backend2Backend.Websocket`** (valeurs par défaut : port **8076**, 10 threads).
- Publier le port **sur l'adresse Tailscale uniquement** (`100.88.150.125:8076:8076` dans Compose), comme Felix (8079) et le websocket des edges (8081). Il donne accès en lecture à tous les edges : il ne doit jamais être public.
- La configuration est conservée dans le volume `openems-backend-conf` et survit à la recréation du conteneur.
- Le composant `Backend2Backend.Rest` (le repli en polling) existe aussi ; il est resté désactivé.

## Protocole

| Élément | Constat |
|---|---|
| Transport | Websocket simple `ws://` ; TLS inutile à l'intérieur de Tailscale |
| Authentification | HTTP Basic Auth à l'ouverture (`Authorization: Basic …`). Avec `Metadata.File`, **`admin:admin` est accepté** |
| Messages | JSON-RPC 2.0 ; `id` doit être un UUID |
| `getEdgesStatus` | Exige `{"edgeIds": [...]}` (pas de « tous les edges » par défaut : erreur 5007 sinon). Renvoie `{"edge0":{"online":true}}` |
| `subscribeEdgesChannels` | `{"count": n, "ids": [...], "channels": [...]}` ; `count` numérote l'abonnement, un numéro plus élevé le remplace. Répond `"result":{}` |
| Notifications | `edgesCurrentData` **toutes les 2 secondes**, pour les seuls channels abonnés |
| Horodatage | **Aucun dans la notification.** Le bridge horodate chaque valeur en UTC à la réception |
| Valeurs manquantes | Envoyées à `null` (par ex. compteurs d'énergie sur un edge sans Timedata) |
| Autres méthodes | Via le gestionnaire général : `getEdgesChannelsValues`, et `edgeRpc` enveloppant `getEdgeConfig` (découverte des actifs, BE-05) et `queryHistoricTimeseriesData` (exige un service timedata sur le Backend, non configuré ici : non testé) |

## Comportement

- **Edge hors ligne :** `getEdgesStatus` renvoie `"online": false`, sans erreur. Le bridge le journalise et s'abonne quand même ; les valeurs arrivent dès que l'edge se reconnecte.
- **Redémarrage du Backend :** les edges se reconnectent seuls au Backend. Le bridge se reconnecte avec un délai croissant (1 s, doublé jusqu'à 60 s, remis à 1 s dès que les données circulent) et se réabonne. Le Backend lit ensuite chaque abonnement dans son cache des dernières valeurs de chaque edge, vide tant que l'edge ne s'est pas reconnecté et ne les a pas envoyées (toutes les valeurs à la reconnexion, puis les changements à chaque cycle, plus un renvoi complet toutes les 5 minutes). D'ici là les valeurs arrivent à `null` et le bridge n'envoie rien plutôt que des données vides. Mesuré le 7 nov. : bridge reconnecté en **8 s**, valeurs revenues après **2 min 52 s**.
- **Plusieurs clients :** chaque connexion B2B a son propre abonnement ; les bridges de préprod et de production peuvent partager un Backend (voir Sécurité et exploitation).

## Données observées (pour BE-02)

- Unités : puissance en W (entier), énergie en Wh (cumulée, `Wh_Σ`), SoC en %.
- Signes : réseau **+ soutirage / − injection** ; ESS **+ décharge / − charge** ; production et consommation toujours positives. Confirmé en direct avec un contrôleur d'équilibrage à des consignes réseau de −1 MW, 0 et +0,2 MW.
- **Les compteurs d'énergie exigent un composant Timedata sur l'edge** (par ex. `Timedata.Rrd4j`). Sans lui, tous les compteurs restent vides.
- Les compteurs de flux d'énergie (`ProductionToEssEnergy` et les autres) relèvent d'une **règle d'affectation** d'OpenEMS (le PV d'abord vers la batterie), pas d'une mesure, et ont dérivé d'environ 3 % par rapport aux totaux mesurés en 6 minutes. Stockés au plus comme métriques optionnelles ; aucun KPI ne s'appuie dessus.
- `_sum` n'a pas de total de puissance réactive ; la puissance réactive est une métrique au niveau actif.

## Sécurité et exploitation

- `Metadata.File` accepte `admin:admin` en B2B et sur l'interface web publique. **Un vrai fournisseur Metadata, ou au minimum une authentification Traefik devant l'interface, est nécessaire avant de raccorder un vrai site.**
- Un Backend partagé entre préprod et production convient tant que RADA est en lecture seule. Le séparer avant une mise à jour d'OpenEMS (pour la tester d'abord en préprod), quand un vrai site client se raccorde, ou quand RADA commencera à envoyer des commandes.
- Donner à chaque environnement son propre utilisateur B2B dès qu'il existera de vrais utilisateurs, pour pouvoir révoquer l'un sans toucher l'autre.

## Vérification (critères d'acceptation)

| Contrôle | Résultat |
|---|---|
| Valeurs en direct de `edge0` reçues via B2B | ✔ 7 nov., à la main (websocat), puis par le bridge |
| Envoyées à la préprod et stockées | ✔ 7 nov. 15h17–16h32 depuis le T14 : chaque envoi `201 Created`, 2 actifs toutes les 60 s, aucun ignoré |
| Bridge comme service à part sur le VPS | ✔ 7 nov. 17h45 UTC : `rada-bridge-preprod`, même image que l'API (`command: python -m bridge`), joignant le Backend et l'API par nom de conteneur sur `cb_network` |
| Redémarrage du Backend : les lignes reprennent en moins de 5 minutes sans intervention | ✔ 7 nov. : Backend redémarré à 17h48:30 UTC, bridge reconnecté à 17h48:38, envois repris à 17h51:22 (**2 min 52 s**) |
| 24 heures de lignes | ✔ du 7 nov. 17h46 au 8 nov. 17h03 UTC (**23 h 17 min**, arrêt volontairement anticipé) : **1 394 lignes par actif** (49 et 50) sur 1 398 cycles possibles. Les 4 cycles manquants correspondent à deux trous de 3 minutes le 7 nov. : 17h48–17h51 (le test de redémarrage du Backend ci-dessus) et 18h36–18h39 (très probablement le redéploiement de `rada-app-preprod` avec le correctif du simulateur). Aucun cycle manqué pendant les 22 h 24 min suivantes |
| Redéploiement avec les correctifs de la revue | ✔ 8 nov. 17h22 UTC, commit `9113643` : connecté et abonné en moins de 0,1 s, puis 2 actifs envoyés toutes les 60 s, aucun ignoré |
