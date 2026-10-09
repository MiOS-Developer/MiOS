# AI-hint: Verb argument validation and synonym mapping helper (WS-DEBT / TD-5 / task_251). Extracted from mios_dispatch.py. Pure helper, must NOT import server.py or...
# AI-doc: usr/share/doc/mios/manual/agent-pipe.md

from typing import Optional, Any

_VERB_CATALOG = {}
_VERB_ARG_SYNONYMS = {}

def configure(verb_catalog=None, verb_arg_synonyms=None) -> None:
    global _VERB_CATALOG, _VERB_ARG_SYNONYMS
    if verb_catalog is not None:
        _VERB_CATALOG = verb_catalog
    if verb_arg_synonyms is not None:
        _VERB_ARG_SYNONYMS = verb_arg_synonyms

def _arg_with_synonyms(tool: str, canonical: str, args: dict) -> Any:
    """Resolve an arg by canonical name first, then by mios.toml-
    declared synonyms for the verb. Returns the first non-empty string,
    list, or tuple found, or '' if none match. SSOT: mios.toml
    [verbs.<tool>.synonyms]."""
    v = args.get(canonical)
    if v is not None:
        if isinstance(v, (list, tuple)):
            if len(v) > 0:
                return v
        elif str(v).strip():
            return str(v)
    for alias in (_VERB_ARG_SYNONYMS.get(tool, {}).get(canonical) or []):
        v = args.get(alias)
        if v is not None:
            if isinstance(v, (list, tuple)):
                if len(v) > 0:
                    return v
            elif str(v).strip():
                return str(v)
    return ""

def _validate_enum_args(tool: str, args: dict) -> Optional[str]:
    if not isinstance(args, dict) or not args:
        return None
    vcfg = _VERB_CATALOG.get(tool)
    if not vcfg:
        return None
    params = vcfg.get("params")
    if not isinstance(params, dict):
        return None
    for argname, argcfg in params.items():
        if not isinstance(argcfg, dict):
            continue
        enum = argcfg.get("enum")
        if not isinstance(enum, list) or not enum:
            continue
        val = _arg_with_synonyms(tool, str(argname), args)
        if val == "":
            continue  # not supplied -> default applies; not our concern
        allowed = [str(e) for e in enum]
        if val not in allowed:
            return (
                f"verb {tool!r} arg {argname!r}={val!r} is not allowed "
                f"(mios.toml [verbs.{tool}.params.{argname}].enum). "
                f"Re-issue with one of: {allowed}."
            )
    return None
