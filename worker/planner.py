import json
import os
import re
import urllib.error
import urllib.request


class Clarification(Exception):
    pass


def validate_goal(value, vendors):
    if not isinstance(value, dict) or set(value) != {"operation", "vendor", "selection"}:
        raise Clarification("The planner did not return a valid goal. Please rephrase the task.")
    if value["operation"] != "import_invoice" or value["selection"] not in ("latest", "all_unpaid"):
        raise Clarification("This worker can import the latest invoice or all unpaid invoices for one vendor.")
    if not isinstance(value["vendor"], str):
        raise Clarification("Please specify a vendor.")
    match = [v for v in vendors if v.casefold() == value["vendor"].casefold()]
    if len(match) != 1:
        raise Clarification("Please specify one known vendor: " + ", ".join(vendors) + ".")
    return {**value, "vendor": match[0]}


def plan(request, vendors, mode):
    text = request.casefold().replace("\u2019", "'").replace("\u2018", "'")
    if re.search(r"\b(pay|payment|delete|remove|email|send|transfer)\b", text):
        raise Clarification("This sandbox imports invoices into a ledger. It cannot pay, delete, or send them.")
    if re.search(r"\b(don't|dont|do not|never|not|except|excluding|exclude|only if|unless|below|above|less than|greater than|before|after|over|under|without|dry[ -]run|preview|simulate|simulation|overdue|past due|convert|usd|eur|gbp|january|february|march|april|may|june|july|august|september|october|november|december|today|yesterday|tomorrow)\b", text) or re.search(r"\b\d+\b", text):
        raise Clarification("This request includes a restriction that this prototype cannot safely interpret. Specify an unconditional latest-invoice or all-unpaid import for one vendor.")
    if re.search(r"\b(latest|newest|most recent)\b", text) and re.search(r"\b(paid|unpaid|outstanding)\b", text):
        raise Clarification("This worker supports the latest invoice regardless of payment status, or all unpaid invoices. Please choose one of those goals.")
    if mode == "llm":
        goal = model_plan(request, vendors)
    else:
        if not re.search(r"\b(import|record|enter|add|log|save)\b", text) or "invoice" not in text:
            raise Clarification("Try: Find the latest invoice from Acme and enter it into the ledger.")
        matches = [v for v in vendors if re.search(r"\b" + re.escape(v.casefold()) + r"\b", text)]
        if len(matches) != 1:
            raise Clarification("Please specify one vendor: " + ", ".join(vendors) + ".")
        if re.search(r"\b(all|every)\b", text) and re.search(r"\b(unpaid|outstanding)\b", text):
            selection = "all_unpaid"
        elif re.search(r"\b(latest|newest|most recent)\b", text) and not re.search(r"\b(all|every)\b", text):
            selection = "latest"
        else:
            raise Clarification("Should I import the latest invoice or all unpaid invoices for that vendor?")
        goal = {"operation": "import_invoice", "vendor": matches[0], "selection": selection}
    return validate_goal(goal, vendors)


def model_plan(request, vendors):
    """A real OpenAI-compatible chat completion; no dependency or fallback masquerading as AI."""
    key = os.environ.get("WORKER_API_KEY", "")
    if not key:
        raise Clarification("LLM mode requires WORKER_API_KEY on the server. Offline mode is available.")
    base = os.environ.get("WORKER_API_BASE", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("WORKER_MODEL", "")
    if not model:
        raise Clarification("Set WORKER_MODEL to the model available through your provider.")
    body = {"model": model, "messages": [
        {"role": "system", "content": (
            "Translate the user's task to JSON only, with exactly keys operation, vendor, selection. "
            "Supported operation: import_invoice. selection: latest or all_unpaid. "
            "Known vendors: " + ", ".join(vendors) + ". "
            "If anything is ambiguous or unsupported, return {\"clarification\":\"your question\"}. "
            "Never reinterpret payment/deletion/email requests as imports. No tools or execution here.")},
        {"role": "user", "content": request}],
        "response_format": {"type": "json_object"}, "max_completion_tokens": 500}
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            payload = json.load(response)
        if not isinstance(payload, dict) or not isinstance(payload.get("choices"), list):
            raise ValueError("Invalid provider response")
        choice = payload["choices"][0]
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            raise ValueError("Invalid provider choice")
        if choice.get("finish_reason") != "stop" or choice["message"].get("refusal"):
            raise ValueError("Incomplete or refused model output")
        value = json.loads(choice["message"]["content"])
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        # Do not log a provider response that may contain keys or other sensitive content.
        raise Clarification("The model request failed or returned invalid JSON. Check server configuration or use offline mode.") from exc
    if isinstance(value, dict) and "clarification" in value:
        raise Clarification(str(value["clarification"])[:500])
    return value
