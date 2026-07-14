# 📋 Spécification — Le QUOI

> Ce document décrit **le besoin métier**, indépendamment de la technique.
> Pour le COMMENT, voir [`architecture.md`](architecture.md).

## 1. Vision

Offrir un agent conversationnel capable de répondre aux demandes de support
client de façon fiable, contextualisée et traçable — réutilisable sur
n'importe quel projet et avec n'importe quel LLM.

## 2. Personas

- **Client final** — pose une question, veut une réponse claire et rapide.
- **Équipe support (humain)** — reçoit les cas escaladés, supervise.
- **Intégrateur (toi)** — branche l'agent sur un projet et un LLM donnés.

## 3. Cas d'usage (construits par phases)

| # | Cas d'usage | Phase ROADMAP |
|---|---|---|
| U1 | Répondre à une question à partir de la FAQ | 4 |
| U2 | Tenir le fil d'une conversation (contexte) | 3 |
| U3 | Se souvenir d'un client d'une session à l'autre | 5 |
| U4 | Router intelligemment la demande | 6 |
| U5 | Escalader vers un humain si nécessaire | 7 |
| U6 | Effectuer une action (statut commande, ticket…) | 8 |

## 4. Exigences fonctionnelles

- **RF1** — L'agent répond en langage naturel, dans la langue du client.
- **RF2** — Les réponses factuelles s'appuient sur la base FAQ (pas d'invention).
- **RF3** — L'agent cite/traçe la source utilisée quand il répond via la FAQ.
- **RF4** — L'agent conserve le contexte de la conversation en cours.
- **RF5** — L'agent peut mémoriser des informations durables sur un utilisateur.
- **RF6** — L'agent sait dire « je ne sais pas » et escalader.

## 5. Exigences non-fonctionnelles

- **RNF1 — Agnosticisme LLM** : changer de fournisseur sans modifier le code métier.
- **RNF2 — Agnosticisme projet** : la base FAQ et la config sont injectées, pas codées en dur.
- **RNF3 — Observabilité** : chaque interaction est traçable (LangSmith).
- **RNF4 — Confidentialité** : aucun secret dans le code ou les logs.
- **RNF5 — Testabilité** : le comportement est évaluable automatiquement (Phase 9).

## 6. Hors périmètre (pour l'instant)

- Interface graphique / front-end.
- Authentification des utilisateurs finaux.
- Multi-canal (téléphone, e-mail) — on se concentre sur le chat.

## 7. Critères de succès

- On peut poser une question FAQ et obtenir une réponse correcte et sourcée.
- On change de provider LLM en éditant `.env`, sans casser l'agent.
- Une conversation garde son contexte ; un utilisateur connu est reconnu.
