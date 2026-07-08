"""Agent Velmo 2.0 : garde-fou d'entrée → mémoire → routage outils → garde-fou
de sortie → écriture mémoire.

Le routage et les outils sont fonctionnels (accès réel à la base). La mémoire,
les garde-fous de contenu et le MLOps sont les chantiers à construire ; ici ils
sont câblés via des composants par défaut (no-op).
"""

from __future__ import annotations

import re

from . import tools
from .config import db_backend
from .guardrails import GuardrailEngine
from .llm import LLM, get_llm
from .memory import MemoryContext, MemoryManager

SYSTEM_PROMPT = (
    "Tu es l'assistant de support de Velmo, boutique de maillots de foot collector. "
    "Tu traites la gestion de commandes de niveau 1 avec courtoisie et précision."
)

DEFAULT_REFUSAL = (
    "Désolé, je ne peux pas traiter cette demande. Je reste à votre disposition "
    "pour vos commandes, livraisons, retours et la FAQ Velmo."
)

ORDER_RE = re.compile(r"O-\d{4}-\d{4}")
SIZE_RE = re.compile(r"\b(XXL|XL|S|M|L)\b")
AMOUNT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:€|euros?)")
_CONFIRM = ("je confirme", "confirme", "c'est confirmé", "oui je", "vas-y")

# Cibles d'oubli reconnues (R5) : les clés de faits qu'on sait retenir + les
# cibles épisodiques usuelles. Liste de reconnaissance *prioritaire*, pas une
# whitelist : hors de cette liste, on retombe sur l'extraction libre.
# Tuple (et non set) pour un ordre de priorité déterministe quand un message
# cite plusieurs cibles (« oublie ma taille et mes clubs »).
_FORGET_TARGETS = ("taille", "clubs", "segment", "canal", "adresse", "commande")
_FORGET_FALLBACK_RE = re.compile(r"\bm(?:on|a|es)\s+([a-zàâäéèêëïîôöùûüç]+)")

# Alias conviviaux -> référence produit.
_ALIASES = {
    "om 1993": "om-1993",
    "marseille 1993": "om-1993",
    "france 98": "france-1998",
    "france 1998": "france-1998",
    "united 99": "mu-1999-treble",
    "mu 1999": "mu-1999-treble",
    "manchester 1999": "mu-1999-treble",
    "bresil 1970": "brazil-1970",
    "brésil 1970": "brazil-1970",
}

_FAQ_KEYWORDS = (
    "frais de port", "frais de livraison", "délai", "delai", "politique de retour",
    "authenticit", "certificat", "paiement", "réassort", "reassort", "rétractation",
    "retractation", "entretien", "garantie", "remboursement sous", "conditions d'échange",
)


class Agent:
    """Assistant de support adossé aux outils métier et à la FAQ."""

    def __init__(
        self,
        llm: LLM,
        memory: MemoryManager,
        guardrails: GuardrailEngine,
        session=None,
        kb=None,
    ) -> None:
        self.llm = llm
        self.memory = memory
        self.guardrails = guardrails
        self.session = session
        self.kb = kb

    def respond(self, user_id: str, message: str) -> str:
        gate_in = self.guardrails.check_input(message)
        if not gate_in.allowed:
            refusal = gate_in.refusal or DEFAULT_REFUSAL
            self.memory.write(user_id, message, refusal)
            return refusal

        ctx = self.memory.read(user_id, message)
        answer = self._handle(user_id, message, ctx)

        gate_out = self.guardrails.check_output(answer)
        if not gate_out.allowed:
            answer = gate_out.refusal or DEFAULT_REFUSAL

        self.memory.write(user_id, message, answer)
        return answer

    # --- routage déterministe ------------------------------------------------

    def _handle(self, user_id: str, message: str, ctx: MemoryContext) -> str:
        low = message.lower()

        if "oubli" in low:
            target = self._forget_target(low)
            if target:
                self.memory.forget(user_id, target)
                return f"C'est oublié : je ne conserve plus votre {target}."

        order = ORDER_RE.search(message)
        order_id = order.group(0) if order else None
        confirmed = any(c in low for c in _CONFIRM)

        if order_id and "annul" in low:
            return self._confirm_or_act(
                confirmed, "annuler", order_id,
                lambda: tools.cancel_order(self.session, order_id, user_id),
            )
        if order_id and "adresse" in low:
            return self._confirm_or_act(
                confirmed, "modifier l'adresse de", order_id,
                lambda: tools.update_shipping_address(
                    self.session, order_id, user_id, {"line1": "(à préciser)"}
                ),
            )
        if order_id and "taille" in low and any(w in low for w in ("chang", "modif", "tromp", "erreur")):
            size = SIZE_RE.search(message)
            new_size = size.group(1) if size else "M"
            return self._confirm_or_act(
                confirmed, f"changer la taille (vers {new_size}) de", order_id,
                lambda: tools.update_order_item(self.session, order_id, user_id, new_size),
            )
        if order_id and any(w in low for w in ("retour", "échange", "echange", "renvoyer")):
            return self._confirm_or_act(
                confirmed, "ouvrir un retour pour", order_id,
                lambda: tools.create_return(self.session, order_id, user_id, "Demande client"),
            )
        if order_id and "rembours" in low:
            amount_match = AMOUNT_RE.search(message)
            amount = float(amount_match.group(1).replace(",", ".")) if amount_match else 0.0
            return self._confirm_or_act(
                confirmed, f"rembourser {amount:.0f}€ sur", order_id,
                lambda: tools.trigger_refund(self.session, order_id, user_id, amount, "Demande client"),
            )

        if order_id and any(w in low for w in ("suivi", "colis", "livr", "transport", "track")):
            return self._format_tracking(tools.track_shipment(self.session, order_id, user_id))
        if order_id:
            return self._format_order(tools.get_order(self.session, order_id, user_id))

        if any(w in low for w in ("dispo", "stock", "reste", "en taille")):
            return self._handle_stock(message, low)

        if any(k in low for k in _FAQ_KEYWORDS):
            return self._format_kb(tools.search_kb(self.kb, message))

        return self.llm.invoke(SYSTEM_PROMPT, ctx.render(), message)

    def _confirm_or_act(self, confirmed: bool, label: str, order_id: str, action) -> str:
        if not confirmed:
            return (
                f"Pour {label} la commande {order_id}, pouvez-vous confirmer ? "
                "Répondez « je confirme »."
            )
        result = action()
        if result.get("error"):
            return f"Je ne trouve pas la commande {order_id} à votre nom."
        if result.get("action") == "escalate":
            return (
                f"Cette demande sur la commande {order_id} dépasse ce que je peux faire seul "
                "(commande déjà partie ou montant trop élevé). Je transmets à un conseiller."
            )
        return f"C'est fait pour la commande {order_id} ({result.get('action')})."

    def _handle_stock(self, message: str, low: str) -> str:
        ref = self._find_ref(low)
        size = SIZE_RE.search(message)
        if not ref or not size:
            return "Pouvez-vous préciser la référence du maillot et la taille souhaitée ?"
        result = tools.check_stock(self.session, ref, size.group(1))
        if result.get("error"):
            return "Je ne connais pas cette référence dans notre catalogue."
        if result["available"]:
            return f"Le maillot {result['title']} en taille {result['size']} est disponible."
        return f"Le maillot {ref} en taille {result['size']} est indisponible (épuisé)."

    def _find_ref(self, low: str) -> str | None:
        for alias, ref in _ALIASES.items():
            if alias in low:
                return ref
        if self.session is not None:
            from .db import Product
            from .tools._common import select

            for (ref,) in self.session.execute(select(Product.ref)).all():
                if ref.lower() in low:
                    return ref
        return None

    @staticmethod
    def _forget_target(low: str) -> str | None:
        """Cible d'un ordre d'oubli : une cible reconnue si elle est citée, sinon
        le mot qui suit « mon/ma/mes » (repli libre — R5 reste ouvert)."""
        for target in _FORGET_TARGETS:
            if target in low:
                return target
        match = _FORGET_FALLBACK_RE.search(low)
        return match.group(1) if match else None

    @staticmethod
    def _format_order(result: dict) -> str:
        if result.get("error"):
            return "Je ne trouve pas cette commande à votre nom."
        return f"Votre commande {result['order_id']} est au statut « {result['status']} »."

    @staticmethod
    def _format_tracking(result: dict) -> str:
        if result.get("error"):
            return "Je ne trouve pas cette commande à votre nom."
        if not result.get("tracking_number"):
            return f"La commande {result['order_id']} n'est pas encore expédiée."
        return (
            f"Votre colis {result['tracking_number']} ({result['carrier']}) est attendu vers "
            f"{result['estimated_delivery']}."
        )

    @staticmethod
    def _format_kb(result: dict) -> str:
        if not result.get("found"):
            return "Je n'ai pas trouvé cette information dans notre FAQ."
        top = result["results"][0]
        return f"D'après notre FAQ ({top['source']}) : {top['snippet']}"


def build_default_agent(session=None, kb=None) -> Agent:
    """Assemble un agent avec composants par défaut, base et FAQ."""
    from .db import fresh_sqlite_session, session_factory
    from .kb_store import get_kb

    if session is None:
        if db_backend() == "postgres":
            session = session_factory()()  # Postgres réel (données seedées via make seed)
        else:
            from .sampledata import seed

            session = fresh_sqlite_session()  # SQLite en mémoire…
            seed(session)  # …peuplé du jeu de référence, pour un CLI démo-able hors-ligne
    if kb is None:
        kb = get_kb()
    return Agent(
        llm=get_llm(),
        memory=MemoryManager(),
        guardrails=GuardrailEngine(),
        session=session,
        kb=kb,
    )
