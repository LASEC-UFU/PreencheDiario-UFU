"""Cadastro estruturado e geração do formulário padrão de Plano de Ensino do SEI."""
from __future__ import annotations

import csv
import html
import io
import json
import re
import shutil
import sqlite3
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
TIPO_PLANO = "Plano de Ensino"
SCHEMA_VERSION = 3

COLUNAS_CAMPOS = {
    "nome": ("identificacao", "nome"), "codigo": ("identificacao", "codigo"),
    "unidade_ofertante": ("identificacao", "unidade_ofertante"),
    "periodo_serie": ("identificacao", "periodo_serie"), "turma": ("identificacao", "turma"),
    "carga_teorica": ("identificacao", "carga_teorica"), "carga_pratica": ("identificacao", "carga_pratica"),
    "carga_total": ("identificacao", "carga_total"), "natureza": ("identificacao", "natureza"),
    "professor": ("identificacao", "professor"), "ano_semestre": ("identificacao", "ano_semestre"),
    "observacoes": ("identificacao", "observacoes"), "ementa": (None, "ementa"),
    "justificativa": (None, "justificativa"), "objetivo_geral": (None, "objetivo_geral"),
    "objetivos_especificos": (None, "objetivos_especificos"), "programa_teorico": (None, "programa_teorico"),
    "programa_pratico": (None, "programa_pratico"), "metodologia_tecnicas": (None, "metodologia_tecnicas"),
    "metodologia_recursos": (None, "metodologia_recursos"), "metodologia_audiovisuais": (None, "metodologia_audiovisuais"),
    "avaliacao_geral": (None, "avaliacao_geral"), "avaliacao_provas": (None, "avaliacao_provas"),
    "horario_atendimento": (None, "horario_atendimento"), "atividade_recuperacao": (None, "atividade_recuperacao"),
    "atendimento_especial": (None, "atendimento_especial"),
}


def campo_vazio() -> dict:
    """Todos os campos editáveis do plano; aprovação é propositalmente omitida."""
    return {
        "identificacao": {
            "nome": "", "unidade_ofertante": "Faculdade de Engenharia Elétrica", "codigo": "",
            "periodo_serie": "", "turma": "", "carga_teorica": "", "carga_pratica": "",
            "carga_total": "", "natureza": "Obrigatória", "professor": "",
            "ano_semestre": "", "observacoes": "",
        },
        "ementa": "", "justificativa": "", "objetivo_geral": "", "objetivos_especificos": "",
        "programa_teorico": "", "programa_pratico": "", "metodologia_tecnicas": "",
        "metodologia_recursos": "", "metodologia_audiovisuais": "",
        "cronograma_teorico": [], "cronograma_pratico": [],
        "avaliacao_geral": "", "avaliacao_provas": "", "horario_atendimento": "",
        "atividade_recuperacao": "", "atendimento_especial": "",
        "bibliografia_basica": [], "bibliografia_complementar": [],
    }


def _normalizar(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (texto or "").casefold())
                   if unicodedata.category(c) != "Mn")


def _limpar(texto: str) -> str:
    return re.sub(r"[ \t]+", " ", (texto or "")).strip()


def _esc(texto: str) -> str:
    return html.escape(texto or "", quote=False).replace("\r\n", "\n").replace("\n", "<br>")


def _texto_xml(node: ET.Element) -> str:
    """Extrai texto preservando limites de parágrafo e quebras do DOCX."""
    paragrafos = node.findall("./w:p", NS) if not node.tag.endswith("}p") else [node]
    linhas: list[str] = []
    for paragrafo in paragrafos:
        partes = []
        for filho in paragrafo.iter():
            if filho.tag.endswith("}t") and filho.text:
                partes.append(filho.text)
            elif filho.tag.endswith("}tab"):
                partes.append(" ")
            elif filho.tag.endswith("}br"):
                partes.append("\n")
        valor = "".join(partes).strip()
        if valor:
            linhas.append(valor)
    return "\n".join(linhas)


def _celulas_tabela(tabela: ET.Element) -> list[list[str]]:
    return [[_texto_xml(c) for c in linha.findall("./w:tc", NS)]
            for linha in tabela.findall("./w:tr", NS)]


def _valor_apos_rotulo(linhas: list[list[str]], rotulo: str) -> str:
    alvo = _normalizar(rotulo).rstrip(":")
    for linha in linhas:
        for i, celula in enumerate(linha):
            if _normalizar(celula).strip().rstrip(":") == alvo and i + 1 < len(linha):
                return _limpar(linha[i + 1])
    return ""


def _extrair_identificacao(linhas: list[list[str]]) -> dict:
    dados = campo_vazio()["identificacao"]
    rotulos = {
        "nome": "Componente Curricular", "unidade_ofertante": "Unidade Ofertante",
        "codigo": "Código", "periodo_serie": "Período/Série", "turma": "Turma",
        "professor": "Professor(A)", "ano_semestre": "Ano/Semestre", "observacoes": "Observações",
    }
    for chave, rotulo in rotulos.items():
        dados[chave] = _valor_apos_rotulo(linhas, rotulo)

    plano = " ".join(c for row in linhas for c in row)
    rotulos_norm = _normalizar(plano)
    # A carga horária aparece em algumas versões do DOCX como rótulo e em
    # outras como células mescladas sem o rótulo "Teórica". Usa os valores
    # explícitos e não infere a partir do total.
    duracoes = re.findall(r"\b\d+(?:[,.]\d+)?\s*h(?:\s*\([^)]*\))?", plano, re.I)
    teorica = re.search(r"te[oó]rica\s*:?\s*(\d+(?:[,.]\d+)?\s*h(?:\s*\([^)]*\))?)", plano, re.I)
    pratica = re.search(r"pr[aá]tica\s*:?\s*(\d+(?:[,.]\d+)?\s*h(?:\s*\([^)]*\))?)", plano, re.I)
    total = re.search(r"total\s*:?\s*(\d+(?:[,.]\d+)?\s*h(?:\s*\([^)]*\))?)", plano, re.I)
    dados["carga_teorica"] = teorica.group(1) if teorica else (duracoes[0] if duracoes else "")
    dados["carga_pratica"] = pratica.group(1) if pratica else ""
    dados["carga_total"] = total.group(1) if total else ""
    if "optativa" in rotulos_norm and re.search(r"optativa\s*:?\s*\(?\s*x", plano, re.I):
        dados["natureza"] = "Optativa"
    elif "obrigatoria" in rotulos_norm and re.search(r"obrigat[oó]ria\s*:?\s*\(?\s*x", plano, re.I):
        dados["natureza"] = "Obrigatória"
    return dados


def campos_de_docx(caminho_docx: str | Path) -> dict:
    """Lê o DOCX de referência para preencher campos estruturados (migração/importação)."""
    with zipfile.ZipFile(caminho_docx) as arquivo:
        root = ET.fromstring(arquivo.read("word/document.xml"))
    body = root.find("w:body", NS)
    if body is None:
        raise ValueError("O DOCX não contém corpo de documento.")
    campos = campo_vazio()
    secao = ""
    dimensao_programa = "teorico"
    subsecao_bibliografia = "basica"
    avaliacao_partes: list[str] = []
    cronogramas_lidos = 0
    tabelas = [x for x in list(body) if x.tag.endswith("}tbl")]
    if tabelas:
        campos["identificacao"] = _extrair_identificacao(_celulas_tabela(tabelas[0]))

    for bloco in list(body):
        if bloco.tag.endswith("}p"):
            valor = _limpar(_texto_xml(bloco))
            if not valor:
                continue
            chave = _normalizar(valor).strip(" .:")
            if chave == "plano de ensino":
                continue
            if chave in {"identificacao", "ementa", "justificativa", "objetivo", "programa",
                         "metodologia", "avaliacao", "bibliografia", "aprovacao"}:
                secao = chave
                continue
            if chave == "dimensao teorica":
                dimensao_programa = "teorico"
                continue
            if chave == "dimensao pratica":
                dimensao_programa = "pratico"
                continue
            if chave.startswith("bibliografia basica"):
                subsecao_bibliografia = "basica"
                continue
            if chave.startswith("bibliografia complementar"):
                subsecao_bibliografia = "complementar"
                continue
            if chave.startswith("cronograma das atividades"):
                continue
            if secao == "aprovacao":
                continue  # Manual do SEI: aprovação permanece sem preenchimento.
            if secao == "programa":
                campos[f"programa_{dimensao_programa}"] = _append_texto(
                    campos[f"programa_{dimensao_programa}"], valor)
            elif secao == "bibliografia":
                chave_lista = f"bibliografia_{subsecao_bibliografia}"
                campos[chave_lista].append(valor)
            elif secao == "metodologia":
                _extrair_metodologia(campos, valor)
            elif secao == "avaliacao":
                avaliacao_partes.append(valor)
            elif secao == "ementa":
                campos["ementa"] = _append_texto(campos["ementa"], valor)
            elif secao == "justificativa":
                campos["justificativa"] = _append_texto(campos["justificativa"], valor)

        elif bloco.tag.endswith("}tbl"):
            matriz = _celulas_tabela(bloco)
            texto_tabela = "\n".join(c for row in matriz for c in row if c)
            normal = _normalizar(texto_tabela)
            if "objetivo geral" in normal or "objetivos especificos" in normal:
                _extrair_objetivos(campos, texto_tabela)
            elif matriz and len(matriz[0]) >= 2 and _normalizar(matriz[0][0]) == "data" \
                    and _normalizar(matriz[0][1]).startswith("atividades"):
                chave_lista = "cronograma_teorico" if cronogramas_lidos == 0 else "cronograma_pratico"
                cronogramas_lidos += 1
                campos[chave_lista] = [
                    {"data": _limpar(row[0]), "atividade": _limpar(row[1])}
                    for row in matriz[1:] if len(row) >= 2 and _limpar(row[0])
                ]

    _separar_avaliacao(campos, avaliacao_partes)
    if not campos["identificacao"]["nome"]:
        campos["identificacao"]["nome"] = Path(caminho_docx).stem
    if not campos["identificacao"]["codigo"]:
        raise ValueError("Não encontrei o código da disciplina na identificação do DOCX.")
    return campos


def _append_texto(atual: str, novo: str) -> str:
    novo = _limpar(novo)
    return "\n".join(x for x in (atual.strip(), novo) if x)


def _extrair_objetivos(campos: dict, texto: str) -> None:
    geral = re.search(r"Objetivo Geral\s*:\s*(.*?)(?=Objetivos Específicos\s*:|$)", texto, re.I | re.S)
    especificos = re.search(r"Objetivos Específicos\s*:\s*(.*)$", texto, re.I | re.S)
    campos["objetivo_geral"] = _limpar(geral.group(1)) if geral else ""
    campos["objetivos_especificos"] = _limpar(especificos.group(1)) if especificos else ""


def _extrair_metodologia(campos: dict, texto: str) -> None:
    regras = (
        ("metodologia_tecnicas", r"Técnicas de ensino(?: que serão utilizadas)?\s*:\s*(.*?)(?=Recursos didáticos\s*:|Recursos audiovisuais\s*:|$)"),
        ("metodologia_recursos", r"Recursos didáticos\s*:\s*(.*?)(?=Recursos audiovisuais\s*:|$)"),
        ("metodologia_audiovisuais", r"Recursos audiovisuais\s*:\s*(.*)$"),
    )
    achou = False
    for campo, padrao in regras:
        match = re.search(padrao, texto, re.I | re.S)
        if match:
            campos[campo] = _append_texto(campos[campo], match.group(1))
            achou = True
    if not achou:
        campos["metodologia_tecnicas"] = _append_texto(campos["metodologia_tecnicas"], texto)


def _separar_avaliacao(campos: dict, partes: list[str]) -> None:
    modo = "geral"
    gerais, provas, recuperacao, especial, horario = [], [], [], [], []
    for texto in partes:
        chave = _normalizar(texto)
        if chave.startswith("quanto as provas"):
            modo = "provas"
        elif chave.startswith("horario de atendimento"):
            modo = "horario"
            texto = re.sub(r"^Horário de Atendimento\s*:\s*", "", texto, flags=re.I)
        elif chave.startswith("atividade de recuperacao"):
            modo = "recuperacao"
            texto = re.sub(r"^Atividade de Recuperação\s*:\s*", "", texto, flags=re.I)
        elif chave.startswith("atendimento especial"):
            modo = "especial"
            texto = re.sub(r"^Atendimento Especial\s*:\s*", "", texto, flags=re.I)
        if modo == "geral": gerais.append(texto)
        elif modo == "provas": provas.append(texto)
        elif modo == "horario": horario.append(texto)
        elif modo == "recuperacao": recuperacao.append(texto)
        else: especial.append(texto)
    campos["avaliacao_geral"] = "\n".join(gerais).strip()
    campos["avaliacao_provas"] = "\n".join(provas).strip()
    campos["horario_atendimento"] = "\n".join(horario).strip()
    campos["atividade_recuperacao"] = "\n".join(recuperacao).strip()
    campos["atendimento_especial"] = "\n".join(especial).strip()


def _linhas_tabela_colada(texto: str) -> tuple[list[str], list[list[str]]]:
    """Lê TSV colado, recompondo quebras de linha em células de Moodle."""
    rows = list(csv.reader(io.StringIO((texto or "").lstrip("\ufeff"), newline=""), delimiter="\t"))
    if not rows:
        raise ValueError("Cole uma tabela com cabeçalhos e linhas de cronograma.")
    cabecalho = [c.strip() for c in rows[0]]
    cab = [_normalizar(re.sub(r"\s+", " ", c)) for c in cabecalho]
    idx = {v: i for i, v in enumerate(cab)}
    if "data" not in idx or "modalidade" not in idx:
        raise ValueError("A tabela precisa conter as colunas Data e Modalidade.")
    descricao_idx = next((i for i, nome in enumerate(cab)
                          if "moodle" in nome and nome.startswith("descri")), None)
    if descricao_idx is None and any("moodle" in nome for nome in cab):
        descricao_idx = next(i for i, nome in enumerate(cab) if "moodle" in nome)

    dados: list[list[str]] = []
    atual: list[str] | None = None
    for row in rows[1:]:
        data = _limpar(row[idx["data"]]) if len(row) > idx["data"] else ""
        modalidade = _limpar(row[idx["modalidade"]]) if len(row) > idx["modalidade"] else ""
        if re.fullmatch(r"\d{2}/\d{2}/\d{4}", data) and modalidade:
            if atual is not None:
                dados.append(atual)
            atual = list(row[:len(cabecalho)])
            atual.extend([""] * (len(cabecalho) - len(atual)))
            if len(row) > len(cabecalho):
                destino = descricao_idx if descricao_idx is not None else len(cabecalho) - 1
                atual[destino] = "\t".join(row[destino:])
        elif atual is not None:
            continuacao = "\t".join(row).strip()
            if continuacao:
                destino = descricao_idx if descricao_idx is not None else len(atual) - 1
                atual[destino] = "\n".join(x for x in (atual[destino].rstrip(), continuacao) if x)
    if atual is not None:
        dados.append(atual)
    return cabecalho, dados


def cronograma_colado(texto: str) -> dict[str, list[dict[str, str]] | str]:
    """Lê cronogramas e a descrição Moodle, inclusive com quebras de linha."""
    cabecalho, rows = _linhas_tabela_colada(texto)
    cab = [_normalizar(re.sub(r"\s+", " ", c.strip())) for c in cabecalho]
    idx = {v: i for i, v in enumerate(cab)}
    grupos = {"cronograma_teorico": [], "cronograma_pratico": []}
    descricao_idx = next((i for i, nome in enumerate(cab)
                          if "moodle" in nome and nome.startswith("descri")), None)
    if descricao_idx is None and any("moodle" in nome for nome in cab):
        descricao_idx = next(i for i, nome in enumerate(cab) if "moodle" in nome)
    descricoes = []
    for row in rows:
        data = _limpar(row[idx["data"]])
        modalidade = _normalizar(_limpar(row[idx["modalidade"]]))
        if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", data):
            continue
        chave = "cronograma_teorico" if "teor" in modalidade else (
            "cronograma_pratico" if "prat" in modalidade else ""
        )
        if not chave:
            continue
        partes = []
        for nome_coluna in ("materia lecionada", "atividades"):
            i = idx.get(nome_coluna)
            if i is not None and i < len(row) and _limpar(row[i]):
                partes.append(_limpar(row[i]))
        grupos[chave].append({"data": data, "atividade": " — ".join(partes)})
        if descricao_idx is not None and row[descricao_idx].strip():
            descricoes.append(row[descricao_idx].strip())
    if not any(grupos.values()):
        raise ValueError("Não encontrei linhas teóricas ou práticas com datas válidas.")
    grupos["descricao_moodle"] = "\n".join(descricoes)
    return grupos


def ler_cronograma_editor(texto: str) -> list[dict[str, str]]:
    """Lê o editor de uma modalidade: uma data e uma atividade por linha."""
    linhas = list(csv.reader(io.StringIO((texto or "").strip(), newline=""), delimiter="\t"))
    eventos = []
    for linha in linhas:
        if len(linha) < 2:
            continue
        data = _limpar(linha[0])
        if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", data):
            continue
        eventos.append({"data": data, "atividade": " — ".join(_limpar(x) for x in linha[1:] if _limpar(x))})
    return eventos


def formatar_cronograma_editor(eventos: list[dict[str, str]]) -> str:
    return "\n".join(f"{x.get('data', '')}\t{x.get('atividade', '')}" for x in eventos)


def _data_provas(campos: dict) -> dict[tuple[str, int], str]:
    resultado = {}
    for grupo, nome in (("teorica", "cronograma_teorico"), ("pratica", "cronograma_pratico")):
        for linha in campos.get(nome, []):
            atividade = _normalizar(linha.get("atividade", ""))
            if not any(termo in atividade for termo in ("prov", "avaliacao")):
                continue
            ordinais = atividade.translate(str.maketrans({"\u00aa": "a", "\u00ba": "o", "\u00b0": "o"}))
            ordinal = 1 if "primeira" in ordinais or re.search(r"\b1(?:a|o)?\b", ordinais) else (
                2 if "segunda" in ordinais or re.search(r"\b2(?:a|o)?\b", ordinais) else 0
            )
            if ordinal:
                resultado[(grupo, ordinal)] = linha["data"]
    return resultado


def _extrair_calendario_avaliacoes(texto: str) -> str:
    """Retém do texto Moodle o calendário, sem repetir a avaliação geral."""
    linhas = (texto or "").splitlines()
    inicio = fim = None
    posicao = 0
    for linha in linhas:
        normal = _normalizar(linha)
        eh_data_avaliacao = (
            re.search(r"\bdia\s+\d{1,2}\s+de\s+", normal)
            and any(p in normal for p in ("avaliacao", "prova", "trabalho"))
        )
        if inicio is None and ("quanto as provas" in normal or eh_data_avaliacao):
            inicio = posicao
        if inicio is not None and fim is None and any(
            p in normal for p in ("horario de atendimento", "links importantes")
        ):
            fim = posicao
        posicao += len(linha) + 1
    if inicio is None:
        return ""
    return "\n".join(linhas)[inicio:fim or len("\n".join(linhas))].strip()


def aplicar_cronograma(campos: dict, texto: str) -> dict:
    atualizados = json.loads(json.dumps(campos, ensure_ascii=False))
    dados_colados = cronograma_colado(texto)
    descricao_moodle = dados_colados.pop("descricao_moodle", "")
    atualizados.update(dados_colados)
    if descricao_moodle:
        trecho = _extrair_calendario_avaliacoes(descricao_moodle)
        if trecho.strip():
            atualizados["avaliacao_provas"] = trecho.strip()

    mapa = _data_provas(atualizados)
    meses = ("janeiro", "fevereiro", "mar\u00e7o", "abril", "maio", "junho",
             "julho", "agosto", "setembro", "outubro", "novembro", "dezembro")
    linhas_avaliacao = []
    for linha in atualizados.get("avaliacao_provas", "").splitlines():
        normal = _normalizar(linha)
        chave = None
        if "primeira" in normal and "avaliacao" in normal:
            chave = ("pratica" if "pratica" in normal else "teorica", 1)
        elif "segunda" in normal and "avaliacao" in normal:
            chave = ("pratica" if "pratica" in normal else "teorica", 2)
        if chave in mapa:
            dia, mes, ano = mapa[chave].split("/")
            extenso = f"Dia {int(dia)} de {meses[int(mes)-1]} de {ano}"
            linha = re.sub(r"Dia\s+\d{1,2}\s+de\s+[^:]+(?=\s*:)", extenso, linha,
                           count=1, flags=re.I)
        linhas_avaliacao.append(linha)
    atualizados["avaliacao_provas"] = "\n".join(linhas_avaliacao)
    return atualizados


def _bloco(texto: str, *, label: str | None = None) -> str:
    if not (texto or "").strip():
        return ""
    prefixo = f"<strong>{html.escape(label)} </strong>" if label else ""
    return f'<div style="margin:4px 0;text-align:justify;line-height:1.25;">{prefixo}{_esc(texto)}</div>'


def _secao(numero: int, nome: str, conteudo: str) -> str:
    cab = (f'<table style="width:100%;border-collapse:collapse;margin:10px 0 5px;">'
           f'<tr><td style="width:9%;background-color:#dedede;padding:5px 7px;font-weight:bold;">{numero}.</td>'
           f'<th style="background-color:#dedede;padding:5px 7px;text-align:center;font-weight:bold;">{nome}</th></tr></table>')
    return cab + conteudo


def _tabela_cronograma(linhas: list[dict[str, str]]) -> str:
    if not linhas:
        return ""
    corpo = "".join(
        f'<tr><td style="width:18%;border:1px solid #777;padding:4px;vertical-align:top;">{_esc(r.get("data", ""))}</td>'
        f'<td style="border:1px solid #777;padding:4px;vertical-align:top;text-align:justify;">{_esc(r.get("atividade", ""))}</td></tr>'
        for r in linhas
    )
    return ('<table border="1" style="width:100%;border-collapse:collapse;margin:4px 0 9px;font-size:10pt;">'
            '<thead><tr><th style="background:#eee;padding:4px;">Data</th>'
            '<th style="background:#eee;padding:4px;">Atividades</th></tr></thead>'
            f'<tbody>{corpo}</tbody></table>')


def renderizar_plano(campos: dict) -> str:
    """Gera o corpo seguindo o modelo SEI 1–9 a partir dos campos armazenados."""
    c = campo_vazio()
    c.update(campos or {})
    ident = c["identificacao"]
    linhas_id = [
        (("Componente Curricular:", ident.get("nome", "")),),
        (("Unidade Ofertante:", ident.get("unidade_ofertante", "")),),
        (("Código:", ident.get("codigo", "")), ("Período/Série:", ident.get("periodo_serie", "")), ("Turma:", ident.get("turma", ""))),
        (("Carga Horária:", ""), ("Natureza:", "")),
        (("Teórica:", ident.get("carga_teorica", "")), ("Prática:", ident.get("carga_pratica", "")), ("Total:", ident.get("carga_total", "")),
         ("Obrigatória:", "( X )" if ident.get("natureza") == "Obrigatória" else "(   )"),
         ("Optativa:", "( X )" if ident.get("natureza") == "Optativa" else "(   )")),
        (("Professor(A):", ident.get("professor", "")), ("Ano/Semestre:", ident.get("ano_semestre", ""))),
        (("Observações:", ident.get("observacoes", "")),),
    ]
    tabela_id = ['<table border="1" style="width:100%;border-collapse:collapse;table-layout:fixed;font-size:10pt;">']
    for linha in linhas_id:
        tds = []
        for rotulo, valor in linha:
            tds.append(f'<th style="width:18%;background:#dedede;border:1px solid #888;padding:4px;text-align:left;">{_esc(rotulo)}</th>')
            tds.append(f'<td style="border:1px solid #888;padding:4px;vertical-align:top;">{_esc(valor)}</td>')
        tabela_id.append("<tr>" + "".join(tds) + "</tr>")
    tabela_id.append("</table>")
    objetivo = _bloco(c.get("objetivo_geral", ""), label="Objetivo Geral:")
    objetivo += _bloco(c.get("objetivos_especificos", ""), label="Objetivos Específicos:")
    programa = _bloco(c.get("programa_teorico", ""), label="Dimensão Teórica:")
    programa += _bloco(c.get("programa_pratico", ""), label="Dimensão Prática:")
    metodologia = _bloco(c.get("metodologia_tecnicas", ""), label="Técnicas de ensino:")
    metodologia += _bloco(c.get("metodologia_recursos", ""), label="Recursos didáticos:")
    metodologia += _bloco(c.get("metodologia_audiovisuais", ""), label="Recursos audiovisuais:")
    avaliacao = _bloco(c.get("avaliacao_geral", ""))
    avaliacao += _bloco(c.get("avaliacao_provas", ""))
    avaliacao += _bloco(c.get("horario_atendimento", ""))
    avaliacao += _bloco(c.get("atividade_recuperacao", ""), label="Atividade de Recuperação:")
    avaliacao += _bloco(c.get("atendimento_especial", ""), label="Atendimento Especial:")
    bibliografia = "".join(_bloco(x) for x in c.get("bibliografia_basica", []))
    bibliografia += "".join(_bloco(x) for x in c.get("bibliografia_complementar", []))
    partes = [
        _secao(1, "IDENTIFICAÇÃO", "".join(tabela_id)),
        _secao(2, "EMENTA", _bloco(c.get("ementa", ""))),
        _secao(3, "JUSTIFICATIVA", _bloco(c.get("justificativa", ""))),
        _secao(4, "OBJETIVO", objetivo),
        _secao(5, "PROGRAMA", programa),
        _secao(6, "METODOLOGIA", metodologia),
        _secao(7, "AVALIAÇÃO", avaliacao),
        _secao(8, "BIBLIOGRAFIA", bibliografia),
        _secao(9, "APROVAÇÃO", ""),
    ]
    tem_cronograma = c.get("cronograma_teorico") or c.get("cronograma_pratico")
    cronograma = ""
    if tem_cronograma:
        cronograma = '<p><strong>Cronograma das atividades curriculares:</strong></p>'
        cronograma += '<p><strong>Dimensão Teórica</strong></p>'
        cronograma += _tabela_cronograma(c.get("cronograma_teorico", []))
        cronograma += '<p><strong>Dimensão Prática</strong></p>'
        cronograma += _tabela_cronograma(c.get("cronograma_pratico", []))
    return '<!doctype html><html><head><meta charset="utf-8"></head><body>' + "".join(partes) + cronograma + "</body></html>"


def _html_com_quebras(soup, texto: str) -> list:
    resultado = []
    for i, linha in enumerate((texto or "").splitlines()):
        if i:
            resultado.append(soup.new_tag("br"))
        resultado.append(soup.new_string(linha))
    if not resultado and texto:
        resultado.append(soup.new_string(texto))
    return resultado


def _substituir_conteudo(elemento, soup, texto: str) -> None:
    elemento.clear()
    for node in _html_com_quebras(soup, texto):
        elemento.append(node)


def _adicionar_paragrafo_formatado(soup, destino, texto: str, rotulo: str = "") -> None:
    texto = (texto or "").strip()
    if not texto and not rotulo:
        return
    p = soup.new_tag("p")
    p["style"] = ("font-family:Arial,sans-serif;font-size:10.5pt;line-height:1.2;"
                  "margin:3px 0 6px;text-align:justify;overflow-wrap:anywhere;")
    if rotulo:
        strong = soup.new_tag("strong")
        strong.string = rotulo
        p.append(strong)
        if texto:
            p.append(soup.new_string(" "))
    for node in _html_com_quebras(soup, texto):
        p.append(node)
    destino.append(p)


def _separar_itens_programa(texto: str) -> list[str]:
    texto = (texto or "").replace("\u00a0", " ").replace("\r", "")
    # O DOCX armazena unidades e subunidades dentro do mesmo parágrafo,
    # separadas por espaços não separáveis; recupere cada item numerado.
    texto = re.sub(r"(?<=\S)\s+(?=\d+\.\d+\.\s)", "\n", texto)
    return [re.sub(r"\s+", " ", x).strip() for x in texto.split("\n") if x.strip()]


def _adicionar_lista_numerada(soup, destino, linhas: list[str]) -> None:
    # O CKEditor/SEI reaplica marcadores padrão a <ol> e <ul> e pode descartar
    # list-style:none. Como o original já traz marcadores decimais (1.1, 1.2),
    # cada unidade vira um parágrafo indentado com o marcador original, sem
    # deixar o editor acrescentar uma segunda numeração automática.
    for linha in linhas:
        match = re.match(r"^(\d+(?:\.\d+)*\.?)\s+(.*)$", linha.strip())
        if not match:
            _adicionar_paragrafo_formatado(soup, destino, linha)
            continue
        marcador, conteudo = match.groups()
        nivel = max(0, marcador.rstrip(".").count("."))
        p = soup.new_tag("p")
        p["style"] = ("font-family:Arial,sans-serif;font-size:10.5pt;line-height:1.2;"
                      f"margin:2px 0 3px {18 + nivel * 20}px;text-align:justify;")
        p.string = f"{marcador} {conteudo.strip()}"
        destino.append(p)


def _adicionar_conteudo_formatado(soup, destino, texto: str, modo: str = "paragrafos",
                                 rotulo: str = "", destaques: list[str] | None = None) -> None:
    bruto = (texto or "").replace("\r\n", "\n").replace("\r", "\n")
    if modo == "programa":
        linhas = _separar_itens_programa(bruto)
        if linhas and all(re.match(r"^\d+(?:\.\d+)*\.?\s+", x) for x in linhas):
            _adicionar_lista_numerada(soup, destino, linhas)
        else:
            for linha in linhas:
                _adicionar_paragrafo_formatado(soup, destino, linha)
        return
    linhas = [x.strip() for x in bruto.split("\n") if x.strip()]
    if modo == "avaliacao":
        termos = [x.strip() for x in (destaques or []) if len(x.strip()) >= 3]
        termos.extend(["Horário de Atendimento:", "Atividade de Recuperação:", "Atendimento Especial:"])
        padroes = [r"\s+".join(re.escape(p) for p in termo.split())
                   for termo in sorted(set(termos), key=len, reverse=True)]
        padroes.append(r"\b(?:(?:PRIMEIRA|SEGUNDA|TERCEIRA)\s+AVALIAÇÃO|AVALIAÇÃO\s+SUBSTITUTIVA)\s+[A-ZÀ-Ú\s]+(?=\s+(?:no valor|\()|[.,;]|$)")
        padrao = re.compile("|".join(f"(?:{p})" for p in padroes), re.I)
        for linha in linhas:
            p = soup.new_tag("p")
            p["style"] = ("font-family:Arial,sans-serif;font-size:10.5pt;line-height:1.2;"
                          "margin:3px 0 6px;text-align:justify;")
            posicao = 0
            for match in padrao.finditer(linha):
                p.append(soup.new_string(linha[posicao:match.start()]))
                forte = soup.new_tag("strong")
                forte.string = match.group()
                p.append(forte)
                posicao = match.end()
            p.append(soup.new_string(linha[posicao:]))
            destino.append(p)
        return
    if modo == "objetivos" and linhas:
        _adicionar_paragrafo_formatado(soup, destino, linhas[0], rotulo)
        itens = linhas[1:]
        if itens:
            ul = soup.new_tag("ul")
            ul["style"] = "margin:2px 0 7px;padding-left:28px;"
            for item in itens:
                item = re.sub(r"^\s*[-*•·]\s*", "", item)
                li = soup.new_tag("li")
                li["style"] = "margin:2px 0;text-align:justify;line-height:1.2;"
                li.string = item
                ul.append(li)
            destino.append(ul)
        return
    for linha in linhas:
        if re.match(r"^\d+(?:\.\d+)*\.?\s+", linha):
            _adicionar_lista_numerada(soup, destino, linhas)
            return
    if linhas:
        grupo = []
        modo_lista = None
        def descarregar_lista():
            nonlocal grupo, modo_lista
            if not grupo:
                return
            lista = soup.new_tag("ol" if modo_lista == "ol" else "ul")
            lista["style"] = "margin:2px 0 7px;padding-left:28px;"
            for item in grupo:
                li = soup.new_tag("li")
                li["style"] = "margin:2px 0;text-align:justify;line-height:1.2;"
                li.string = re.sub(r"^\s*[-*•·]\s*", "", item)
                lista.append(li)
            destino.append(lista)
            grupo, modo_lista = [], None
        for linha in linhas:
            marcador = "ol" if re.match(r"^\d+\.\s+", linha) else (
                "ul" if re.match(r"^\s*[-*•·]\s+", linha) else None
            )
            if marcador:
                if modo_lista and modo_lista != marcador:
                    descarregar_lista()
                modo_lista = marcador
                grupo.append(re.sub(r"^\s*(?:\d+\.|[-*•·])\s*", "", linha))
            else:
                descarregar_lista()
                _adicionar_paragrafo_formatado(soup, destino, linha)
        descarregar_lista()


def _adicionar_cronograma_final(soup, titulo_avaliacao, teoria: list[dict[str, str]],
                               pratica: list[dict[str, str]]) -> None:
    if not teoria and not pratica:
        return
    destino = soup.body or soup
    ponto_insercao = titulo_avaliacao if titulo_avaliacao is not None else None
    if ponto_insercao is not None:
        tabela_titulo = ponto_insercao.find_parent("table")
        if tabela_titulo is not None and len(tabela_titulo.get_text(" ", strip=True)) <= 200:
            ponto_insercao = tabela_titulo

    def inserir_no_documento(elemento) -> None:
        if ponto_insercao is not None and ponto_insercao.parent is not None:
            ponto_insercao.insert_before(elemento)
        else:
            destino.append(elemento)

    def novo_rotulo(texto: str):
        paragrafo = soup.new_tag("p")
        paragrafo["style"] = ("font-family:Arial,sans-serif;font-size:10.5pt;line-height:1.15;"
                              "font-weight:bold;margin:7px 0 4px;text-align:left;")
        paragrafo.string = texto
        return paragrafo

    def adicionar_tabela(modalidade: str, linhas: list[dict[str, str]]) -> None:
        inserir_no_documento(novo_rotulo(modalidade))
        if not linhas:
            return
        tabela = soup.new_tag("table", attrs={"border": "1"})
        tabela["style"] = ("width:100%;border-collapse:collapse;table-layout:fixed;"
                           "margin:3px 0 10px;font-family:Arial,sans-serif;font-size:9.5pt;")
        colgroup = soup.new_tag("colgroup")
        for largura in ("15%", "85%"):
            coluna = soup.new_tag("col")
            coluna["width"] = largura
            coluna["style"] = f"width:{largura};"
            colgroup.append(coluna)
        tabela.append(colgroup)
        thead = soup.new_tag("thead")
        thead["style"] = "display:table-header-group;"
        tr_cabecalho = soup.new_tag("tr")
        for nome in ("Data", "Atividades"):
            th = soup.new_tag("th")
            th["width"] = "15%" if nome == "Data" else "85%"
            th["style"] = "background:#eee;padding:5px;text-align:center;border:1px solid #777;"
            th["style"] += "width:15%;" if nome == "Data" else "width:85%;"
            th.string = nome
            tr_cabecalho.append(th)
        thead.append(tr_cabecalho)
        tbody = soup.new_tag("tbody")
        for item in linhas:
            tr = soup.new_tag("tr")
            tr["style"] = "page-break-inside:avoid;"
            for indice, chave in enumerate(("data", "atividade")):
                td = soup.new_tag("td")
                largura = "15%" if indice == 0 else "85%"
                td["width"] = largura
                td["style"] = ("border:1px solid #777;padding:4px;vertical-align:top;"
                               f"overflow-wrap:anywhere;word-break:break-word;width:{largura};")
                td.string = item.get(chave, "")
                tr.append(td)
            tbody.append(tr)
        tabela.extend([thead, tbody])
        inserir_no_documento(tabela)

    inserir_no_documento(novo_rotulo("Cronograma das atividades curriculares:"))
    adicionar_tabela("Dimensão Teórica", teoria)
    adicionar_tabela("Dimensão Prática", pratica)


def _substituir_tabela_identificacao(soup, identificacao: dict) -> bool:
    """Recria apenas a grade de identificação com larguras legíveis e estáveis."""
    chaves = ("componente curricular", "unidade ofertante", "codigo", "observacoes")
    candidatas = []
    for tabela in soup.find_all("table"):
        texto = _normalizar(tabela.get_text(" ", strip=True))
        if all(chave in texto for chave in chaves):
            candidatas.append(tabela)
    if not candidatas:
        return False
    original = min(candidatas, key=lambda tag: len(tag.get_text(" ", strip=True)))
    tabela = soup.new_tag("table", attrs={"border": "1"})
    tabela["style"] = ("width:100%;border-collapse:collapse;table-layout:fixed;"
                       "font-size:10pt;margin:4px 0 10px;")
    grupo = soup.new_tag("colgroup")
    for _ in range(5):
        coluna = soup.new_tag("col")
        coluna["style"] = "width:20%;"
        grupo.append(coluna)
    tabela.append(grupo)

    def adicionar_linha(celulas):
        tr = soup.new_tag("tr")
        for tag_nome, rotulo, valor, colspan in celulas:
            cel = soup.new_tag(tag_nome)
            if colspan > 1:
                cel["colspan"] = str(colspan)
            cel["style"] = ("border:1px solid #888;padding:5px;vertical-align:top;"
                            "font-family:Arial,sans-serif;font-size:10pt;"
                            "overflow-wrap:anywhere;word-break:break-word;"
                            + ("background:#dedede;font-weight:bold;" if tag_nome == "th" else ""))
            if tag_nome == "th":
                cel.string = rotulo
            else:
                if rotulo:
                    forte = soup.new_tag("strong")
                    forte.string = rotulo
                    cel.append(forte)
                    cel.append(soup.new_tag("br"))
                for node in _html_com_quebras(soup, valor or ""):
                    cel.append(node)
            tr.append(cel)
        tabela.append(tr)

    adicionar_linha([("th", "Componente Curricular", "", 1),
                     ("td", "", identificacao.get("nome", ""), 4)])
    adicionar_linha([("th", "Unidade Ofertante", "", 1),
                     ("td", "", identificacao.get("unidade_ofertante", ""), 4)])
    adicionar_linha([("td", "Código:", identificacao.get("codigo", ""), 2),
                     ("td", "Período/Série:", identificacao.get("periodo_serie", ""), 1),
                     ("td", "Turma:", identificacao.get("turma", ""), 2)])
    adicionar_linha([("th", "Carga Horária", "", 3), ("th", "Natureza", "", 2)])
    adicionar_linha([
        ("td", "Teórica:", identificacao.get("carga_teorica", ""), 1),
        ("td", "Prática:", identificacao.get("carga_pratica", ""), 1),
        ("td", "Total:", identificacao.get("carga_total", ""), 1),
        ("td", "Obrigatória:", "( X )" if identificacao.get("natureza", "").casefold() == "obrigatória" else "(   )", 1),
        ("td", "Optativa:", "( X )" if identificacao.get("natureza", "").casefold() == "optativa" else "(   )", 1),
    ])
    adicionar_linha([("td", "Professor(a):", identificacao.get("professor", ""), 3),
                     ("td", "Ano/Semestre:", identificacao.get("ano_semestre", ""), 2)])
    adicionar_linha([("th", "Observações", "", 1), ("td", "", identificacao.get("observacoes", ""), 4)])
    original.replace_with(tabela)
    return True


def _destaques_avaliacao_docx(caminho_docx: str | Path | None) -> list[str]:
    """Obtém trechos em negrito do modelo para aplicá-los ao texto cadastrado."""
    if not caminho_docx or not Path(caminho_docx).is_file():
        return []
    try:
        with zipfile.ZipFile(caminho_docx) as arquivo:
            body = ET.fromstring(arquivo.read("word/document.xml")).find("w:body", NS)
        if body is None:
            return []
        na_avaliacao = False
        destaques = []
        for paragrafo in body.findall("w:p", NS):
            titulo = _normalizar(_texto_xml(paragrafo)).strip(" .:")
            if titulo == "avaliacao":
                na_avaliacao = True
                continue
            if na_avaliacao and titulo in {"bibliografia", "aprovacao"}:
                break
            if not na_avaliacao:
                continue
            trecho = ""
            for run in paragrafo.findall(".//w:r", NS):
                texto = "".join(t.text or "" for t in run.findall(".//w:t", NS))
                negrito = run.find("w:rPr/w:b", NS)
                ativo = negrito is not None and negrito.get(f"{{{NS['w']}}}val", "1") not in {"0", "false", "off"}
                if ativo:
                    trecho += texto
                elif trecho:
                    destaques.append(trecho.strip())
                    trecho = ""
            if trecho:
                destaques.append(trecho.strip())
        return [x for x in destaques if x]
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError):
        return []


def mesclar_campos_no_modelo(modelo_html: str, campos: dict,
                             caminho_docx: str | Path | None = None) -> tuple[str, int]:
    """Preenche os prompts no modelo nativo do SEI, preservando sua estrutura.

    Não cria um documento do zero: atualiza as células de identificação e troca
    os comentários/instruções pelos valores correspondentes do plano.
    """
    from bs4 import BeautifulSoup
    from bs4.element import Comment

    soup = BeautifulSoup(modelo_html or "", "html.parser")
    texto_modelo = soup.get_text(" ", strip=True)
    if not texto_modelo or not any(t in _normalizar(texto_modelo) for t in ("identificacao", "ementa", "aprovacao")):
        raise ValueError("O editor do SEI não carregou o modelo padrão de Plano de Ensino; nada foi alterado.")
    c = _juntar_campos(campos)
    destaques_avaliacao = _destaques_avaliacao_docx(caminho_docx)
    substituidos = 0

    # O modelo pode conter instruções entre parênteses. Remova-as antes de
    # inserir os dados, preservando observações legítimas e marcadores (X)/( ).
    # Aplicar isso somente ao HTML recebido evita alterar referências e textos
    # que o usuário cadastrou nos campos.
    for trecho in list(soup.find_all(string=True)):
        if isinstance(trecho, Comment):
            continue
        def limpar_instrucao_parenteses(match: re.Match) -> str:
            interior = _normalizar(match.group(1))
            palavras_instrucao = (
                "instrucao", "orientacao", "preencher", "copiar", "descrever",
                "informar", "explicitar", "utilizar", "no minimo", "seguir o art",
            )
            return "" if any(p in interior for p in palavras_instrucao) else match.group(0)

        limpo = re.sub(r"\(([^()]*)\)", limpar_instrucao_parenteses, str(trecho))
        if limpo != str(trecho):
            trecho.replace_with(limpo)

    # A tabela original do SEI tem células estreitas para a carga horária e os
    # marcadores de natureza. Recrie somente essa grade com larguras legíveis.
    if _substituir_tabela_identificacao(soup, c["identificacao"]):
        substituidos += 8

    def encontrar_prompts(fragmento: str) -> list:
        needle = _normalizar(fragmento)
        achados = []
        for tag in soup.find_all(["p", "div", "td", "th", "li"]):
            texto = _normalizar(tag.get_text(" ", strip=True))
            if needle in texto and not any(tag is pai for pai in achados):
                achados.append(tag)
        # Evita escolher um contêiner externo quando ele contém o próprio prompt.
        return [tag for tag in achados if not any(outro in tag.find_all(True) for outro in achados if outro is not tag)]

    def substituir_prompt(fragmento: str, conteudo: str, *, indice: int = 0,
                          modo: str = "paragrafos", rotulo: str = "") -> object | None:
        nonlocal substituidos
        alvos = encontrar_prompts(fragmento)
        if indice >= len(alvos):
            return None
        alvo = alvos[indice]
        if alvo.name == "p":
            container = soup.new_tag("div")
            _adicionar_conteudo_formatado(soup, container, conteudo, modo=modo, rotulo=rotulo,
                                         destaques=destaques_avaliacao)
            for node in list(container.contents):
                alvo.insert_before(node)
            alvo.decompose()
        else:
            alvo.clear()
            _adicionar_conteudo_formatado(soup, alvo, conteudo, modo=modo, rotulo=rotulo,
                                         destaques=destaques_avaliacao)
        substituidos += 1
        return alvo

    def localizar_secao(numero: int, nome: str):
        nome_normal = _normalizar(nome)
        padrao = re.compile(rf"^{numero}\s*\.?\s*{re.escape(nome_normal)}(?:\s|$)")
        ancora = None
        for tag in soup.find_all("table"):
            texto = _normalizar(tag.get_text(" ", strip=True))
            if len(texto) <= 100 and padrao.match(texto):
                ancora = tag
                break
        if ancora is None:
            # Alguns editores deixam o número da seção em um elemento e o
            # título em outro; aceite também cabeçalhos sem número visível.
            for tag in soup.find_all(["p", "h1", "h2", "h3", "div", "td", "th", "strong", "b"]):
                texto = _normalizar(tag.get_text(" ", strip=True))
                corresponde = padrao.match(texto) or texto.strip(" .:") == nome_normal
                if len(texto) <= 100 and corresponde:
                    tabela = tag if tag.name == "table" else tag.find_parent("table")
                    ancora = tabela if tabela and len(_normalizar(tabela.get_text(" ", strip=True))) <= 160 else tag
                    if ancora.name in {"td", "th", "strong", "b"}:
                        ancora = ancora.find_parent(["p", "div", "td", "th"]) or ancora
                    break
        return ancora

    def inserir_apos_secao(numero: int, nome: str, valores: list[tuple],
                           *, remover_objetivos_antigos: bool = False) -> bool:
        """Insere conteúdo após o cabeçalho quando o modelo já não tem prompt."""
        nonlocal substituidos
        ancora = localizar_secao(numero, nome)
        if ancora is None:
            return False
        if remover_objetivos_antigos:
            # O modelo pode ter uma tabela para cada objetivo. Limite a
            # remoção ao trecho entre os cabeçalhos Objetivo e Programa.
            fim = localizar_secao(5, "programa")
            if fim is None:
                raise ValueError("Não encontrei o início do Programa para delimitar os Objetivos.")
            nos = list(soup.descendants)
            posicoes = {id(no): i for i, no in enumerate(nos)}
            inicio_idx, fim_idx = posicoes[id(ancora)], posicoes[id(fim)]
            if fim_idx <= inicio_idx:
                raise ValueError("As seções Objetivo e Programa estão fora da ordem esperada.")
            candidatas = []
            for tabela in soup.find_all("table"):
                pos = posicoes[id(tabela)]
                texto = re.sub(r"\s+", " ", _normalizar(tabela.get_text(" ", strip=True)))
                if (inicio_idx < pos < fim_idx and len(texto) <= 1600
                        and ("objetivo geral" in texto or "objetivos especificos" in texto)):
                    candidatas.append(tabela)
            for tabela in candidatas:
                if tabela.parent is not None and not any(p in candidatas for p in tabela.parents):
                    tabela.decompose()

        def adicionar_valor(rotulo: str, valor: str, modo: str) -> None:
            nonlocal ancora
            if modo == "objetivos":
                cab = soup.new_tag("p")
                cab["style"] = ("font-family:Arial,sans-serif;font-size:10.5pt;line-height:1.2;"
                                "font-weight:bold;margin:4px 0 2px;text-align:left;")
                cab.string = rotulo
                ancora.insert_after(cab)
                destino = soup.new_tag("div")
                cab.insert_after(destino)
                _adicionar_conteudo_formatado(soup, destino, valor, modo="objetivos")
                ancora = destino
                return
            if rotulo:
                rotulo_p = soup.new_tag("p")
                rotulo_p["style"] = ("font-family:Arial,sans-serif;font-size:10.5pt;line-height:1.2;"
                                      "font-weight:bold;margin:5px 0 2px;text-align:left;")
                rotulo_p.string = rotulo
                ancora.insert_after(rotulo_p)
                destino = soup.new_tag("div")
                rotulo_p.insert_after(destino)
            else:
                destino = soup.new_tag("div")
                destino["style"] = ("font-family:Arial,sans-serif;font-size:10.5pt;line-height:1.2;"
                                     "margin:3px 0 6px;text-align:justify;overflow-wrap:anywhere;")
                ancora.insert_after(destino)
            _adicionar_conteudo_formatado(soup, destino, valor, modo=modo,
                                         destaques=destaques_avaliacao)
            ancora = destino

        for item in valores:
            rotulo, valor = item[:2]
            modo = item[2] if len(item) > 2 else "paragrafos"
            if not valor.strip():
                continue
            adicionar_valor(rotulo, valor, modo)
            substituidos += 1
        return True

    def preencher_rotulo_vazio(fragmento: str, valor: str) -> bool:
        needle = _normalizar(fragmento)
        candidatos = []
        for tag in soup.find_all(["p", "div", "td", "th", "li"]):
            texto = _normalizar(tag.get_text(" ", strip=True)).strip(" :.")
            if texto == needle:
                candidatos.append(tag)
        alvo = min(candidatos, key=lambda tag: len(tag.get_text(" ", strip=True))) if candidatos else None
        if alvo is None:
            return False
        linha = alvo if alvo.name == "tr" else alvo.find_parent("tr")
        if linha is not None:
            celulas = linha.find_all(["td", "th"], recursive=False)
            cel_alvo = alvo if alvo.name in {"td", "th"} else alvo.find_parent(["td", "th"])
            if cel_alvo in celulas and celulas.index(cel_alvo) + 1 < len(celulas):
                proxima = celulas[celulas.index(cel_alvo) + 1]
                if not proxima.get_text(" ", strip=True):
                    _substituir_conteudo(proxima, soup, valor)
                    return True
        paragrafo = soup.new_tag("p")
        paragrafo["style"] = ("font-family:Arial,sans-serif;font-size:10.5pt;line-height:1.2;"
                              "margin:3px 0 6px;text-align:justify;overflow-wrap:anywhere;")
        for node in _html_com_quebras(soup, valor):
            paragrafo.append(node)
        if alvo.name in {"td", "th", "li"}:
            alvo.append(paragrafo)
        else:
            alvo.insert_after(paragrafo)
        return True

    def titulo_secao(fragmento: str) -> object | None:
        needle = _normalizar(fragmento)
        candidatos = []
        for tag in soup.find_all(["p", "div", "td", "th", "h1", "h2", "h3"]):
            texto = _normalizar(tag.get_text(" ", strip=True))
            if needle in texto and len(texto) <= len(needle) + 24:
                candidatos.append(tag)
        titulo = next((tag for tag in candidatos
                       if not any(outro in tag.find_all(True) for outro in candidatos if outro is not tag)), None)
        if titulo is not None:
            return titulo
        # Alguns modelos usam apenas <strong>/<b> para o título. Se estiver
        # dentro de um parágrafo, o parágrafo é o limite correto para editar.
        for tag in soup.find_all(["strong", "b"]):
            texto = _normalizar(tag.get_text(" ", strip=True))
            if needle in texto and len(texto) <= len(needle) + 24:
                return tag.find_parent(["p", "div", "td", "th"]) or tag
        return None

    def substituir_conteudo_secao(titulo: object | None, proximo_titulo: object | None,
                                  referencias: list[str]) -> bool:
        """Apaga todo conteúdo antigo entre dois títulos e insere as referências."""
        nonlocal substituidos
        if titulo is None or titulo.parent is None:
            return False
        # A estrutura nativa do SEI mantém esses títulos e seus parágrafos no
        # mesmo contêiner. Falha fechada se o próximo título estiver em outro.
        irmaos = list(titulo.next_siblings)
        limite = None
        for irmao in irmaos:
            if proximo_titulo is not None and irmao is proximo_titulo:
                limite = irmao
                break
        if proximo_titulo is not None and limite is None:
            return False
        remover = irmaos if limite is None else irmaos[:irmaos.index(limite)]
        for item in remover:
            if getattr(item, "parent", None) is titulo.parent:
                item.extract()
        ancora = titulo
        for referencia in referencias:
            if not referencia.strip():
                continue
            paragrafo = soup.new_tag("p")
            paragrafo["style"] = ("font-family:Arial,sans-serif;font-size:10.5pt;line-height:1.2;"
                                  "margin:3px 0 6px;text-align:justify;overflow-wrap:anywhere;")
            paragrafo.string = referencia.strip()
            ancora.insert_after(paragrafo)
            ancora = paragrafo
        substituidos += 1
        return True

    secoes_nao_localizadas = []
    if substituir_prompt("ementa aprovada", c["ementa"]) is None:
        if not inserir_apos_secao(2, "ementa", [("", c["ementa"], "paragrafos")]):
            secoes_nao_localizadas.append("Ementa")
    if substituir_prompt("importancia dos conteudos", c["justificativa"]) is None:
        if not inserir_apos_secao(3, "justificativa", [("", c["justificativa"], "paragrafos")]):
            secoes_nao_localizadas.append("Justificativa")

    # No modelo do SEI, os objetivos vêm em uma grade vazia. Remova apenas essa
    # grade e recrie os dois blocos como texto e lista, sem bordas de tabela.
    if not inserir_apos_secao(4, "objetivo", [
        ("Objetivo Geral:", c["objetivo_geral"], "paragrafos"),
        ("Objetivos Específicos:", c["objetivos_especificos"], "objetivos"),
    ], remover_objetivos_antigos=True):
        secoes_nao_localizadas.append("Objetivos")

    # O programa recupera a numeração das unidades e subunidades do texto do DOCX.
    if not inserir_apos_secao(5, "programa", [
        ("Dimensão Teórica:", c["programa_teorico"], "programa"),
        ("Dimensão Prática:", c["programa_pratico"], "programa"),
    ]):
        secoes_nao_localizadas.append("Programa")
    metodologia = "Técnicas de ensino: " + c["metodologia_tecnicas"]
    metodologia += "\nRecursos didáticos: " + c["metodologia_recursos"]
    metodologia += "\nRecursos audiovisuais: " + c["metodologia_audiovisuais"]
    if substituir_prompt("descrever a forma de organizacao", metodologia) is None:
        if not inserir_apos_secao(6, "metodologia", [("", metodologia, "paragrafos")]):
            secoes_nao_localizadas.append("Metodologia")
    titulo_avaliacao = localizar_secao(7, "avaliacao")
    calendario = _extrair_calendario_avaliacoes(c["avaliacao_provas"]) or c["avaliacao_provas"]
    def com_rotulo(rotulo: str, valor: str) -> str:
        valor = (valor or "").strip()
        if not valor:
            return ""
        if _normalizar(valor).startswith(_normalizar(rotulo).rstrip(":")):
            return valor
        return f"{rotulo}\n{valor}"

    avaliacao = "\n".join(x for x in (
        c["avaliacao_geral"], calendario,
        com_rotulo("Horário de Atendimento:", c["horario_atendimento"]),
        com_rotulo("Atividade de Recuperação:", c["atividade_recuperacao"]),
        com_rotulo("Atendimento Especial:", c["atendimento_especial"]),
    ) if x.strip())
    if substituir_prompt("descrever o tipo/modalidade de avaliacao", avaliacao, modo="avaliacao") is None:
        if not inserir_apos_secao(7, "avaliacao", [("", avaliacao, "avaliacao")]):
            secoes_nao_localizadas.append("Avaliação")
    # Bibliografia pode já ter conteúdo de uma versão anterior, sem o texto de
    # instrução do modelo. Substituímos a seção inteira, limitada pelo próximo
    # título, para não deixar referências antigas misturadas às novas.
    titulo_basica = titulo_secao("bibliografia basica")
    titulo_complementar = titulo_secao("bibliografia complementar")
    titulo_aprovacao = titulo_secao("aprovacao")
    basica_ok = substituir_conteudo_secao(
        titulo_basica, titulo_complementar, c["bibliografia_basica"]
    )
    complementar_ok = substituir_conteudo_secao(
        titulo_complementar, titulo_aprovacao, c["bibliografia_complementar"]
    )
    if not basica_ok:
        substituir_prompt("no minimo 3", "\n".join(c["bibliografia_basica"]))
    if not complementar_ok:
        substituir_prompt("no minimo 5", "\n".join(c["bibliografia_complementar"]))

    # Remova instruções do template que tenham sido quebradas em mais de um
    # parágrafo pelo editor/PDF (por exemplo, o aviso longo do item Avaliação).
    instrucoes_antigas = (
        "copiar da ficha de disciplina", "explicitar a importancia dos conteudos",
        "objetivos propostos", "programa organizado em unidades", "descrever a forma de organizacao",
        "descrever o tipo/modalidade de avaliacao", "instrumentos/formas a serem empregados",
        "deve-se seguir o art. 163", "sera utilizada no decorrer das aulas",
        "cada titulo citado deve ter um exemplar", "para enriquecimento dos estudos",
        "no minimo 3", "no minimo 5",
    )
    for tag in list(soup.find_all(["p", "div", "li"])):
        valor = _normalizar(tag.get_text(" ", strip=True))
        if valor and any(p in valor for p in instrucoes_antigas):
            tag.decompose()

    if secoes_nao_localizadas:
        raise ValueError(
            "Não encontrei os campos no modelo do SEI: " + ", ".join(secoes_nao_localizadas)
            + ". Nenhum documento foi preenchido; confira se o editor está no tipo Plano de Ensino."
        )

    # Como no DOCX de referência, as duas tabelas encerram a Metodologia.
    if (c["cronograma_teorico"] or c["cronograma_pratico"]) and titulo_avaliacao is None:
        raise ValueError("Não encontrei Avaliação para posicionar o cronograma ao final da Metodologia.")
    _adicionar_cronograma_final(
        soup, titulo_avaliacao, c["cronograma_teorico"], c["cronograma_pratico"]
    )

    if substituidos < 8:
        raise ValueError(
            f"O modelo do SEI não apresentou os campos esperados (apenas {substituidos} correspondências). "
            "Nenhuma versão genérica foi gravada; confira se o tipo escolhido foi Plano de Ensino."
        )
    return str(soup), substituidos


@dataclass(frozen=True)
class PlanoEnsino:
    caminho: Path
    campos: dict

    @property
    def nome(self) -> str:
        return self.campos.get("identificacao", {}).get("nome", "")

    @property
    def codigo(self) -> str:
        return self.campos.get("identificacao", {}).get("codigo", "")

    @property
    def descricao(self) -> str:
        return f"{self.codigo} - {self.nome}"

    @property
    def nome_arvore(self) -> str:
        nome = re.sub(r"\s+", " ", self.nome).strip()
        if len(nome) <= 50:
            return nome
        recorte = nome[:50].rstrip()
        return recorte.rsplit(" ", 1)[0] if " " in recorte and not nome[50].isspace() else recorte

    @property
    def html(self) -> str:
        return renderizar_plano(self.campos)

    @property
    def conteudo_editor(self) -> str:
        return self.html


def _db_path(caminho: str | Path | None = None) -> Path:
    local = Path.home() / "AppData" / "Local" / "PreencheDiarioUFU"
    destino = Path(caminho) if caminho else local / "planos_ensino.sqlite3"
    destino.parent.mkdir(parents=True, exist_ok=True)
    return destino


def _criar_schema(db: sqlite3.Connection) -> None:
    colunas = ",\n        ".join(f'"{nome}" TEXT NOT NULL DEFAULT \'\'' for nome in COLUNAS_CAMPOS)
    db.execute(f"""CREATE TABLE IF NOT EXISTS planos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        {colunas},
        caminho_docx TEXT NOT NULL DEFAULT '',
        atualizado_em TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS cronogramas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plano_id INTEGER NOT NULL REFERENCES planos(id) ON DELETE CASCADE,
        modalidade TEXT NOT NULL CHECK(modalidade IN ('teorico','pratico')),
        ordem INTEGER NOT NULL, data TEXT NOT NULL, atividade TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS bibliografias (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plano_id INTEGER NOT NULL REFERENCES planos(id) ON DELETE CASCADE,
        categoria TEXT NOT NULL CHECK(categoria IN ('basica','complementar')),
        ordem INTEGER NOT NULL, referencia TEXT NOT NULL
    )""")
    db.execute("CREATE TABLE IF NOT EXISTS schema_info (versao INTEGER NOT NULL)")


def _set_versao(db: sqlite3.Connection) -> None:
    db.execute("DELETE FROM schema_info")
    db.execute("INSERT INTO schema_info(versao) VALUES(?)", (SCHEMA_VERSION,))


def _backup_banco(db: sqlite3.Connection, destino: Path, versao: int) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = destino.with_name(f"{destino.stem}_backup_v{versao}_{stamp}{destino.suffix}")
    copia = sqlite3.connect(backup)
    try:
        db.backup(copia)
    finally:
        copia.close()
    return backup


def _juntar_campos(campos: dict) -> dict:
    base = campo_vazio()
    for chave, valor in (campos or {}).items():
        if chave == "identificacao" and isinstance(valor, dict):
            base[chave].update(valor)
        elif chave in base:
            base[chave] = valor
    return base


def _inserir_campos(db: sqlite3.Connection, campos: dict, caminho_docx: str = "",
                    registro_id: int | None = None, atualizado_em: str | None = None) -> int:
    dados = _juntar_campos(campos)
    valores = {col: ((dados[grupo][campo] if grupo else dados[campo]) or "")
               for col, (grupo, campo) in COLUNAS_CAMPOS.items()}
    valores.update(caminho_docx=caminho_docx or "",
                   atualizado_em=atualizado_em or datetime.now().isoformat(timespec="seconds"))
    if registro_id is not None:
        valores["id"] = registro_id
    nomes = list(valores)
    quoted = ",".join(f'"{nome}"' for nome in nomes)
    placeholders = ",".join("?" for _ in nomes)
    cursor = db.execute(f"INSERT INTO planos ({quoted}) VALUES ({placeholders})", [valores[n] for n in nomes])
    plano_id = registro_id if registro_id is not None else int(cursor.lastrowid)
    for modalidade, chave in (("teorico", "cronograma_teorico"), ("pratico", "cronograma_pratico")):
        for ordem, linha in enumerate(dados.get(chave, []), start=1):
            if linha.get("data") and linha.get("atividade"):
                db.execute("INSERT INTO cronogramas(plano_id,modalidade,ordem,data,atividade) VALUES(?,?,?,?,?)",
                           (plano_id, modalidade, ordem, linha["data"], linha["atividade"]))
    for categoria in ("basica", "complementar"):
        for ordem, referencia in enumerate(dados.get(f"bibliografia_{categoria}", []), start=1):
            if str(referencia).strip():
                db.execute("INSERT INTO bibliografias(plano_id,categoria,ordem,referencia) VALUES(?,?,?,?)",
                           (plano_id, categoria, ordem, str(referencia).strip()))
    return plano_id


def inicializar_banco(caminho: str | Path | None = None) -> Path:
    destino = _db_path(caminho)
    db = sqlite3.connect(destino)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        tabelas = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "planos" not in tabelas:
            _criar_schema(db)
            _set_versao(db)
            db.commit()
            return destino
        colunas = {r[1] for r in db.execute("PRAGMA table_info(planos)")}
        if "campos_json" in colunas:
            antigos = [dict(r) for r in db.execute("SELECT * FROM planos")]
            _backup_banco(db, destino, 2)
            nome_legado = "planos_campos_v2"
            if nome_legado in tabelas:
                nome_legado += "_" + datetime.now().strftime("%Y%m%d_%H%M%S")
            db.execute("BEGIN IMMEDIATE")
            db.execute(f'ALTER TABLE planos RENAME TO "{nome_legado}"')
            _criar_schema(db)
            for antigo in antigos:
                _inserir_campos(db, json.loads(antigo["campos_json"]), antigo.get("caminho_docx", ""),
                                antigo["id"], antigo.get("atualizado_em"))
            _set_versao(db)
            db.commit()
        elif "conteudo_html" in colunas:
            antigos = [dict(r) for r in db.execute("SELECT * FROM planos")]
            _backup_banco(db, destino, 1)
            nome_legado = "planos_html_v1"
            if nome_legado in tabelas:
                nome_legado += "_" + datetime.now().strftime("%Y%m%d_%H%M%S")
            db.execute("BEGIN IMMEDIATE")
            db.execute(f'ALTER TABLE planos RENAME TO "{nome_legado}"')
            _criar_schema(db)
            for antigo in antigos:
                origem = Path(antigo.get("caminho_docx", ""))
                if not origem.is_file():
                    continue
                campos = campos_de_docx(origem)
                if antigo.get("cronograma_tsv"):
                    try:
                        campos = aplicar_cronograma(campos, antigo["cronograma_tsv"])
                    except ValueError:
                        pass
                _inserir_campos(db, campos, str(origem), antigo["id"], antigo.get("atualizado_em"))
            _set_versao(db)
            db.commit()
        elif "ementa" not in colunas or "codigo" not in colunas:
            raise RuntimeError("O banco de planos tem um formato n?o reconhecido; uma c?pia foi preservada.")
        else:
            _criar_schema(db)
            if not db.execute("SELECT 1 FROM schema_info LIMIT 1").fetchone():
                _set_versao(db)
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return destino

def ler_docx(caminho_docx: str | Path) -> PlanoEnsino:
    path = Path(caminho_docx)
    return PlanoEnsino(path, campos_de_docx(path))


def _campos_registro(db: sqlite3.Connection, row: sqlite3.Row) -> dict:
    campos = campo_vazio()
    for coluna, (grupo, campo) in COLUNAS_CAMPOS.items():
        if grupo:
            campos[grupo][campo] = row[coluna]
        else:
            campos[campo] = row[coluna]
    for item in db.execute("SELECT modalidade,data,atividade FROM cronogramas WHERE plano_id=? ORDER BY modalidade,ordem", (row["id"],)):
        chave = "cronograma_teorico" if item["modalidade"] == "teorico" else "cronograma_pratico"
        campos[chave].append({"data": item["data"], "atividade": item["atividade"]})
    for item in db.execute("SELECT categoria,referencia FROM bibliografias WHERE plano_id=? ORDER BY categoria,ordem", (row["id"],)):
        campos[f"bibliografia_{item['categoria']}"].append(item["referencia"])
    return campos


def _registro(db: sqlite3.Connection, row: sqlite3.Row) -> dict:
    campos = _campos_registro(db, row)
    return {"id": row["id"], "nome": campos["identificacao"]["nome"],
            "codigo": campos["identificacao"]["codigo"], "caminho_docx": row["caminho_docx"],
            "atualizado_em": row["atualizado_em"], "campos": campos}


def listar_planos(caminho_db: str | Path | None = None) -> list[dict]:
    path = inicializar_banco(caminho_db)
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT * FROM planos ORDER BY nome COLLATE NOCASE, id").fetchall()
        return [_registro(db, row) for row in rows]


def obter_plano(plano_id: int, caminho_db: str | Path | None = None) -> dict:
    path = inicializar_banco(caminho_db)
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM planos WHERE id=?", (plano_id,)).fetchone()
        if row is None:
            raise KeyError("Plano n?o encontrado no cadastro local.")
        return _registro(db, row)


def salvar_plano(campos: dict, caminho_docx: str = "", plano_id: int | None = None,
                 caminho_db: str | Path | None = None) -> int:
    campos = _juntar_campos(campos)
    ident = campos["identificacao"]
    if not _limpar(ident.get("nome", "")) or not _limpar(ident.get("codigo", "")):
        raise ValueError("Preencha Componente Curricular e C?digo na identifica??o.")
    path = inicializar_banco(caminho_db)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        if plano_id is None:
            return _inserir_campos(db, campos, caminho_docx)
        if not db.execute("SELECT 1 FROM planos WHERE id=?", (plano_id,)).fetchone():
            raise KeyError("Plano n?o encontrado no cadastro local.")
        valores = {col: ((campos[grupo][chave] if grupo else campos[chave]) or "")
                   for col, (grupo, chave) in COLUNAS_CAMPOS.items()}
        valores.update(caminho_docx=caminho_docx or "",
                       atualizado_em=datetime.now().isoformat(timespec="seconds"))
        sets = ",".join(f'"{col}"=?' for col in valores)
        db.execute(f"UPDATE planos SET {sets} WHERE id=?", [*valores.values(), plano_id])
        db.execute("DELETE FROM cronogramas WHERE plano_id=?", (plano_id,))
        db.execute("DELETE FROM bibliografias WHERE plano_id=?", (plano_id,))
        for modalidade, chave in (("teorico", "cronograma_teorico"), ("pratico", "cronograma_pratico")):
            for ordem, linha in enumerate(campos.get(chave, []), start=1):
                if linha.get("data") and linha.get("atividade"):
                    db.execute("INSERT INTO cronogramas(plano_id,modalidade,ordem,data,atividade) VALUES(?,?,?,?,?)",
                               (plano_id, modalidade, ordem, linha["data"], linha["atividade"]))
        for categoria in ("basica", "complementar"):
            for ordem, referencia in enumerate(campos.get(f"bibliografia_{categoria}", []), start=1):
                if str(referencia).strip():
                    db.execute("INSERT INTO bibliografias(plano_id,categoria,ordem,referencia) VALUES(?,?,?,?)",
                               (plano_id, categoria, ordem, str(referencia).strip()))
        return plano_id


def remover_plano(plano_id: int, caminho_db: str | Path | None = None) -> None:
    path = inicializar_banco(caminho_db)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys=ON")
        cur = db.execute("DELETE FROM planos WHERE id=?", (plano_id,))
        if cur.rowcount == 0:
            raise KeyError("Plano n?o encontrado no cadastro local.")
