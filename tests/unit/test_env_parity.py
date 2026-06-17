"""Garante que os perfis de ambiente versionados não divirjam em CHAVES.

O drift entre `.env.local.example` e `.env.prod.example` foi a causa-raiz de o
`.env.local` ficar desatualizado (faltando flags de RAG/idiomas/matemática/LGPD).
Este teste roda no gate (`make ship` / CI) e falha se alguém adicionar uma chave
a um perfil e esquecer do outro — mesma filosofia da regra de IaC no AGENTS.md
(uma fonte da verdade, sem edição manual que diverge).
"""
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_KEY = re.compile(r"^([A-Z_][A-Z0-9_]*)=", re.MULTILINE)


def _keys(name: str) -> set[str]:
    return set(_KEY.findall((_ROOT / name).read_text(encoding="utf-8")))


def test_env_example_profiles_have_identical_keys():
    local = _keys(".env.local.example")
    prod = _keys(".env.prod.example")
    only_local = local - prod
    only_prod = prod - local
    assert not (only_local or only_prod), (
        "Perfis .env.*.example divergiram em chaves — sincronize-os.\n"
        f"  só em .env.local.example: {sorted(only_local)}\n"
        f"  só em .env.prod.example:  {sorted(only_prod)}"
    )
