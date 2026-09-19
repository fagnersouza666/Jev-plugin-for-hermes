"""Tool schemas exposed to Hermes' model tool registry."""

_STATE = {
    "anyOf": [
        {"type": "string"},
        {"type": "object"},
        {"type": "array"},
    ],
    "description": "Text or a JSON-compatible object/array containing the evidence to evaluate.",
}

_QUESTION = {
    "type": "object",
    "description": "A Jev question: noul, choice, or score plus instructions and criteria when needed.",
    "properties": {
        "type": {"type": "string", "enum": ["noul", "choice", "score"]},
        "instructions": {"type": "string"},
        "criteria": {
            "anyOf": [
                {"type": "object"},
                {"type": "array", "items": {"type": "string"}},
            ]
        },
    },
    "required": ["type", "instructions"],
    "additionalProperties": True,
}

JEV_EVALUATE = {
    "name": "jev_evaluate",
    "description": (
        "Evaluate text or structured evidence with TypeSafe Jev. Use Noul for a probability "
        "that a statement is true, Choice for a category, and Score for a position on a rubric. "
        "Use this for classification or confidence estimation, not for irreversible authorization."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "state": _STATE,
            "questions": {
                "type": "object",
                "description": "Named Jev questions evaluated in one request.",
                "additionalProperties": _QUESTION,
            },
            "model": {
                "type": "string",
                "description": "Optional model alias; defaults to the plugin setting jev-latest.",
            },
        },
        "required": ["state", "questions"],
        "additionalProperties": False,
    },
}

JEV_PRICE_ASSESS = {
    "name": "jev_price_assess",
    "description": (
        "Assess an e-commerce offer against a target product with Jev. This is advisory: "
        "it does not buy, publish, or send an alert by itself. Combine the result with deterministic "
        "price, identity, freshness, and seller policies."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "Exact product or component the offer must match.",
            },
            "offer": {
                "type": "object",
                "description": "Structured listing data: title, price, seller, condition, URL, shipping, and evidence.",
                "additionalProperties": True,
            },
            "model": {
                "type": "string",
                "description": "Optional model alias; defaults to the plugin setting.",
            },
        },
        "required": ["target", "offer"],
        "additionalProperties": False,
    },
}
