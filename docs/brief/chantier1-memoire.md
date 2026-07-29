# Chantier 1 : mémoire

L'expert impose les exigences suivantes ; à vous d'en déduire l'architecture mémoire (aucune solution n'est imposée, seulement le résultat attendu) :

## Exigence imposée

R1 - Tenir le fil d'une conversation de 30 tours (messages).

R2 - Se souvenir, d'une session à l'autre (des jours plus tard), des faits et préférences durables d'un même utilisateur (ex. « je suis client pro », « tutoie-moi », n° de contrat).

R3 - Isolation stricte : la mémoire d'un utilisateur n'est jamais accessible à un autre.

R4 - Au-delà des 30 messages, résumer / sélectionner sans perdre l'information critique.

R5 - Droit à l'oubli (RGPD) : un utilisateur peut demander d'oublier une information (« oublie mon numéro de commande »), avec suppression effective et vérifiable.

R6 - Traçabilité : on doit pouvoir inspecter ce que l'agent a retenu d'un utilisateur.