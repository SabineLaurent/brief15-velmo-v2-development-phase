# frontend — coquille de démo Chainlit (scope B, niveau 1)

But : **voir l'agent parler** dans un navigateur, pour un GIF de 20 s au README.
UI **assumée non-prod** (cf. `docs/roadmap-frontend.md`).

Ce membre ne connaît **rien** de LangGraph : il parle à l'agent uniquement par la
couture `stream_reply` (paquet `support_agent`). C'est ce qui le rend **jetable** —
on pourra remplacer Chainlit par un vrai front sans toucher au cerveau.

## Lancer

```bash
chainlit run packages/frontend/src/frontend/app.py -w
```

`-w` = rechargement à chaud (watch) pendant le développement. Une porte d'entrée
`make ui` viendra en phase B1.6.
