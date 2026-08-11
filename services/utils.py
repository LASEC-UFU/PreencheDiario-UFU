from __future__ import annotations
import re
from pathlib import Path
from typing import Dict, Tuple

# URL inicial do portal (ajuste conforme necessário)
GET_URL = "https://www.portaldocente.ufu.br"  # TODO: coloque a URL de entrada correta do portal UFU
SEI_URL = "https://sei.ufu.br"

DADOS_DEFAULT_PATH = Path("assets/dados_exemplo.json")
OUT_DIR = Path("out_portal")
OUT_DIR.mkdir(parents=True, exist_ok=True)

DASHES = { "\u2013": "-", "\u2014": "-", "\u2212": "-" }  # – — −

def normalize_label(label: str) -> str:
    """Normaliza a chave do JSON para o padrão 'DD/MM/AAAA -P'.
    - troca traços estranhos por '-'
    - remove espaços duplicados
    - tenta forçar o formato de data DD/MM/AAAA + ' -P' sufixo (mantém sufixo literal fornecido)
    """
    if not isinstance(label, str):
        return ""

    for k, v in { "\u2013": "-", "\u2014": "-", "\u2212": "-" }.items():
        label = label.replace(k, v)
    label = re.sub(r"\s+", " ", label).strip()

    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{2,4})\s*-\s*(.*)$", label)
    if m:
        d, mth, y, suffix = m.groups()
        y = y.zfill(4) if len(y) == 4 else ("20" + y.zfill(2))
        suffix = suffix.strip() or "P"
        return f"{d.zfill(2)}/{mth.zfill(2)}/{y} -{suffix}"
    else:
        md = re.match(r"(\d{1,2})/(\d{1,2})/(\d{2,4})$", label)
        if md:
            d, mth, y = md.groups()
            y = y.zfill(4) if len(y) == 4 else ("20" + y.zfill(2))
            return f"{d.zfill(2)}/{mth.zfill(2)}/{y} -P"
    return label


def preview_text(text: str, maxlen: int = 48) -> str:
    t = (text or "").strip().replace("\n", " ")
    return (t[:maxlen] + "…") if len(t) > maxlen else t


def validate_value_map(value_map: Dict[str, str]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Valida e normaliza chaves; retorna (norm_map, errors)
    - norm_map: chaves normalizadas -> valor
    - errors: chave_original -> motivo
    """
    norm: Dict[str, str] = {}
    errors: Dict[str, str] = {}

    if not isinstance(value_map, dict):
        errors["__root__"] = "JSON precisa ser objeto {label: texto}."
        return {}, errors

    for k, v in value_map.items():
        if not isinstance(v, str):
            errors[str(k)] = "Valor precisa ser texto (string)."
            continue
        nk = normalize_label(str(k))
        if not re.match(r"^\d{2}/\d{2}/\d{4} -.+$", nk):
            errors[str(k)] = "Chave não segue padrão 'DD/MM/AAAA -X'."
            continue
        if nk in norm:
            errors[str(k)] = f"Chave duplicada após normalização: {nk!r}."
            continue
        norm[nk] = v

    return norm, errors
