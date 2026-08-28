# -*- coding: utf-8 -*-
"""Leitura das fichas HTML e automação visual do cadastro no SEI.

O login e a abertura do processo continuam sendo feitos pelo usuário. A
automação começa na tela do processo já aberto e não armazena credenciais.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import time
import unicodedata
from typing import Callable, Iterable, Optional

from bs4 import BeautifulSoup, NavigableString
from selenium.common.exceptions import NoAlertPresentException
from selenium.webdriver.remote.webdriver import WebDriver


TIPO_DOCUMENTO = "Ficha de Componente Curricular"
LIMITE_NOME_ARVORE = 50
STATUS_CRIADA = "criada"
STATUS_ATUALIZADA = "atualizada"
STATUS_SEM_ALTERACAO = "sem_alteracao"
TIMEOUT_CONTEUDO_DOCUMENTO = 40.0
TENTATIVAS_CONTEUDO_DOCUMENTO = 2


def truncar_nome_arvore(nome: str, limite: int = LIMITE_NOME_ARVORE) -> str:
    nome = re.sub(r"\s+", " ", nome or "").strip().upper()
    if len(nome) <= limite:
        return nome
    trecho = nome[:limite].rstrip()
    # Evita cortar uma palavra no meio quando existe um espaço útil no trecho.
    if len(nome) > limite and not nome[limite].isspace() and " " in trecho:
        trecho = trecho.rsplit(" ", 1)[0]
    return trecho


_MESES_PT = (
    "janeiro|fevereiro|mar[cç]o|abril|maio|junho|julho|agosto|setembro|"
    "outubro|novembro|dezembro"
)
_PADRAO_CODIGO_VERIFICADOR = re.compile(
    r"c[oó]digo verificador\s*[:\-]?\s*(\d{4,})", re.IGNORECASE
)
_PADRAO_SEI_NUMERO = re.compile(r"SEI\s*n[ºo°]?\s*(\d{4,})", re.IGNORECASE)
_PADRAO_DATA_EXTENSO = re.compile(
    rf"\d{{1,2}}\s+de\s+(?:{_MESES_PT})\s+de\s+\d{{4}}", re.IGNORECASE
)
_PADRAO_DATA_CURTA = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
_TIPOS_DOCUMENTO_SEI = (
    r"OF[IÍ]CIO|MEMORANDO|DESPACHO|PARECER|RESOLU[ÇC][AÃ]O|PORTARIA|RELAT[OÓ]RIO|"
    r"ATA|DECIS[AÃ]O|NOTA T[EÉ]CNICA|MINUTA(?: DE RESOLU[ÇC][AÃ]O)?|CERTID[AÃ]O|"
    r"DECRETO|EDITAL|INSTRU[ÇC][AÃ]O NORMATIVA|CONTRATO|CONV[EÊ]NIO"
)
_PADRAO_TIPO_NUMERO = re.compile(rf"^(?:{_TIPOS_DOCUMENTO_SEI})\b.*", re.IGNORECASE)


def extrair_codigo_sei(texto: str) -> str:
    """Busca o código verificador SEI no texto visível de um documento."""
    encontrado = _PADRAO_CODIGO_VERIFICADOR.search(texto or "") or _PADRAO_SEI_NUMERO.search(
        texto or ""
    )
    return encontrado.group(1) if encontrado else ""


def extrair_data(texto: str) -> str:
    """Busca a primeira data (por extenso ou DD/MM/AAAA) no texto do documento."""
    encontrado = _PADRAO_DATA_EXTENSO.search(texto or "") or _PADRAO_DATA_CURTA.search(
        texto or ""
    )
    return encontrado.group(0) if encontrado else ""


def extrair_tipo_numero(texto: str) -> str:
    """Retorna a linha que identifica o tipo e o número do documento.

    A tela de visualização do SEI não expõe um campo estruturado para tipo e
    número, e o cabeçalho institucional (nome da universidade, endereço) também
    aparece em maiúsculas antes do título. Por isso a busca usa os tipos de
    documento comuns em processos SEI para achar a linha certa, em vez de só
    procurar a primeira linha em maiúsculas.
    """
    for linha in (texto or "").splitlines():
        linha = linha.strip()
        if _PADRAO_TIPO_NUMERO.match(linha):
            return linha
    return ""


def extrair_resumo(texto: str, tipo_numero: str = "", limite: int = 220) -> str:
    """Aproxima o resumo pelo primeiro parágrafo substancial após o título."""
    linhas = [linha.strip() for linha in (texto or "").splitlines() if linha.strip()]
    depois_do_titulo = not tipo_numero
    for linha in linhas:
        if not depois_do_titulo:
            if linha == tipo_numero:
                depois_do_titulo = True
            continue
        if len(linha) < 40:
            continue
        return (linha[:limite].rstrip() + "…") if len(linha) > limite else linha
    return ""


@dataclass(frozen=True)
class DocumentoArvore:
    """Um documento identificado na árvore do processo do SEI."""

    ordem: int
    nome_arvore: str
    codigo_sei: str
    tipo_numero: str
    data: str
    resumo: str
    href: str = ""


def formatar_relatorio_arvore(documentos: Iterable[DocumentoArvore]) -> str:
    """Formata o inventário de documentos no estilo de um relatório de parecer.

    Os campos são heurísticos (lidos do texto visível de cada documento);
    revise tipo, código, data e resumo antes de usar em um parecer real.
    """
    linhas = []
    for documento in documentos:
        identificacao = documento.tipo_numero or documento.nome_arvore
        codigo = f" ({documento.codigo_sei})" if documento.codigo_sei else ""
        data = f", de {documento.data}" if documento.data else ""
        resumo = f" – {documento.resumo}" if documento.resumo else ""
        linhas.append(f"{documento.ordem}. {identificacao}{codigo}{data}{resumo}")
    return "\n".join(linhas)


@dataclass(frozen=True)
class FichaDisciplina:
    caminho: Path
    codigo: str
    nome: str
    html: str
    conteudo_editor: str

    @property
    def descricao(self) -> str:
        return f"Ficha de Componente Curricular - {self.nome.upper()}"

    @property
    def nome_arvore(self) -> str:
        return truncar_nome_arvore(self.nome)


def _sem_acentos(valor: str) -> str:
    return "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", valor or "")
        if not unicodedata.combining(caractere)
    )


def normalizar_codigo(valor: str) -> str:
    """Normaliza caixa/espacos do codigo sem descartar sua pontuacao."""
    return re.sub(r"\s+", " ", _sem_acentos(valor or "")).strip().upper()


def _texto_depois_do_rotulo(soup: BeautifulSoup, rotulo: str) -> str:
    alvo = _sem_acentos(rotulo).upper().rstrip(":")
    for texto in soup.find_all(string=True):
        atual = _sem_acentos(str(texto)).strip().upper().rstrip(":")
        if atual != alvo:
            continue
        celula = texto.find_parent(["td", "th"])
        if celula:
            partes = [p.strip() for p in celula.stripped_strings if p.strip()]
            for indice, parte in enumerate(partes):
                if _sem_acentos(parte).upper().rstrip(":") == alvo and indice + 1 < len(partes):
                    return partes[indice + 1]
    return ""


def _fallback_nome_arquivo(caminho: Path) -> tuple[str, str]:
    """Extrai código/nome de ``NN_CODIGO_NOME_60h_...html``."""
    match = re.match(r"^\d+_(.+?)(?:_\d+h(?:_|$)|$)", caminho.stem, re.IGNORECASE)
    if not match:
        return "", ""
    partes = match.group(1).split("_")
    if len(partes) < 2:
        return "", ""
    # Os códigos novos da FEELT aparecem como FEELT_AS1, FEELT_DCA etc.;
    # os códigos numéricos antigos (FEMEC39101, FAGEN32411...) ocupam um token.
    if partes[0].upper() == "FEELT" and len(partes) >= 3:
        codigo = "_".join(partes[:2])
        nome = "_".join(partes[2:])
    else:
        codigo = partes[0]
        nome = "_".join(partes[1:])
    return codigo.strip(), re.sub(r"[_\s]+", " ", nome).strip()


def _extrair_conteudo_editor(soup: BeautifulSoup) -> str:
    """Retorna somente a seção editável da ficha.

    O CKEditor 5 do SEI separa o documento em raízes: cabeçalho, título,
    conteúdo principal e rodapé. Cabeçalho/título/rodapé são bloqueados, então
    o HTML a inserir começa depois do título "Ficha de Componente Curricular".
    """
    if not soup.body:
        raise ValueError("o HTML não possui elemento body")

    marcador = None
    for elemento in soup.body.find_all(["p", "h1", "h2", "div"]):
        texto = _sem_acentos(elemento.get_text(" ", strip=True)).casefold()
        if texto == "ficha de componente curricular":
            marcador = elemento
            break
    if marcador is None:
        raise ValueError("não foi localizado o título 'Ficha de Componente Curricular'")

    bloco = marcador
    while bloco.parent is not soup.body:
        bloco = bloco.parent
        if bloco is None:
            raise ValueError("estrutura inesperada no corpo da ficha")

    conteudo = "".join(str(item) for item in bloco.next_siblings).strip()
    if not conteudo:
        raise ValueError("a ficha não possui conteúdo depois do título")
    return conteudo


def normalizar_html_comparacao(html: str) -> str:
    """Canonicaliza HTML para comparar conteudo, sem alterar seu significado.

    O navegador e o CKEditor podem mudar aspas, ordem de atributos e espacos entre
    tags ao salvar. Esses detalhes nao devem provocar uma atualizacao da ficha.
    """
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup.find_all(True):
        atributos = {}
        for nome, valor in sorted(tag.attrs.items()):
            nome_lower = nome.lower()
            if (
                nome_lower.startswith("data-cke-")
                or nome_lower in {"contenteditable", "spellcheck", "tabindex"}
            ):
                continue
            if nome_lower == "class" and isinstance(valor, list):
                valor = sorted(valor)
            atributos[nome] = valor
        tag.attrs = atributos

    for texto in list(soup.find_all(string=True)):
        if not isinstance(texto, NavigableString):
            continue
        pai = texto.parent.name if texto.parent else ""
        if pai in {"pre", "style", "script", "textarea"}:
            continue
        normalizado = re.sub(r"\s+", " ", str(texto))
        if not normalizado.strip():
            texto.extract()
        elif normalizado != str(texto):
            texto.replace_with(normalizado)

    raiz = soup.body or soup
    return "".join(str(item) for item in raiz.contents).strip()


def html_equivalente(atual: str, novo: str) -> bool:
    return normalizar_html_comparacao(atual) == normalizar_html_comparacao(novo)


def ler_ficha(caminho: str | Path) -> FichaDisciplina:
    caminho = Path(caminho)
    try:
        html = caminho.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        html = caminho.read_text(encoding="latin-1")

    soup = BeautifulSoup(html, "lxml")
    codigo = _texto_depois_do_rotulo(soup, "CÓDIGO")
    nome = _texto_depois_do_rotulo(soup, "COMPONENTE CURRICULAR")
    codigo_fallback, nome_fallback = _fallback_nome_arquivo(caminho)
    codigo = (codigo or codigo_fallback).strip()
    nome = (nome or nome_fallback).strip()

    # Algumas fichas geradas têm um nome truncado na célula (por exemplo,
    # apenas "Engenharia de"), enquanto o filename contém o nome completo.
    nome_norm = _sem_acentos(nome).casefold()
    fallback_norm = _sem_acentos(nome_fallback).casefold()
    if nome and nome_fallback and fallback_norm.startswith(nome_norm + " "):
        nome = nome_fallback

    if not nome and soup.title:
        nome = re.sub(
            r"^\s*Ficha de Componente Curricular\s*-\s*",
            "",
            soup.title.get_text(" ", strip=True),
            flags=re.IGNORECASE,
        ).strip()
    if not codigo or not nome:
        ausentes = [rotulo for rotulo, valor in (("código", codigo), ("nome", nome)) if not valor]
        raise ValueError(f"não foi possível identificar {' e '.join(ausentes)}")
    conteudo_editor = _extrair_conteudo_editor(soup)
    return FichaDisciplina(
        caminho=caminho,
        codigo=codigo,
        nome=nome,
        html=html,
        conteudo_editor=conteudo_editor,
    )


def _ordem_natural(caminho: Path) -> tuple[int, str]:
    match = re.match(r"^(\d+)", caminho.name)
    return (int(match.group(1)) if match else 10**9, caminho.name.casefold())


def ler_pasta_fichas(pasta: str | Path) -> tuple[list[FichaDisciplina], list[str]]:
    pasta = Path(pasta)
    if not pasta.is_dir():
        raise ValueError(f"pasta inexistente: {pasta}")
    fichas: list[FichaDisciplina] = []
    erros: list[str] = []
    arquivos_por_codigo: dict[str, Path] = {}
    for caminho in sorted(pasta.glob("*.html"), key=_ordem_natural):
        try:
            ficha = ler_ficha(caminho)
            chave_codigo = normalizar_codigo(ficha.codigo)
            anterior = arquivos_por_codigo.get(chave_codigo)
            if anterior is not None:
                erros.append(
                    f"{caminho.name}: código {ficha.codigo!r} duplicado "
                    f"(já usado por {anterior.name})"
                )
                continue
            arquivos_por_codigo[chave_codigo] = caminho
            fichas.append(ficha)
        except Exception as exc:
            erros.append(f"{caminho.name}: {exc}")
    return fichas, erros


class AutomacaoSEI:
    def __init__(
        self, driver: WebDriver, logger: Callable[[str], None], *, timeout: float = 20.0
    ):
        self.driver = driver
        self.log = logger
        self.timeout = timeout
        self.estatisticas = {
            STATUS_CRIADA: 0,
            STATUS_ATUALIZADA: 0,
            STATUS_SEM_ALTERACAO: 0,
        }

    def _esperar(self, funcao, descricao: str, timeout: Optional[float] = None):
        limite = time.monotonic() + (timeout if timeout is not None else self.timeout)
        ultimo_erro: Optional[Exception] = None
        while time.monotonic() < limite:
            try:
                resultado = funcao()
                if resultado:
                    return resultado
            except Exception as exc:
                ultimo_erro = exc
            time.sleep(0.25)
        detalhe = f" ({ultimo_erro})" if ultimo_erro else ""
        raise RuntimeError(f"tempo esgotado aguardando {descricao}{detalhe}")

    def _buscar_em_frames(self, script: str, *args, profundidade: int = 0):
        """Executa o script no documento e em seus frames; deixa o frame achado ativo."""
        try:
            resultado = self.driver.execute_script(script, *args)
            if resultado:
                return resultado
        except Exception:
            pass
        if profundidade >= 5:
            return None
        try:
            frames = self.driver.find_elements("css selector", "iframe, frame")
        except Exception:
            return None
        for frame in frames:
            entrou = False
            try:
                self.driver.switch_to.frame(frame)
                entrou = True
                resultado = self._buscar_em_frames(script, *args, profundidade=profundidade + 1)
                if resultado:
                    return resultado
            except Exception:
                pass
            if entrou:
                try:
                    self.driver.switch_to.parent_frame()
                except Exception:
                    self.driver.switch_to.default_content()
        return None

    def _achar(self, script: str, *args):
        self.driver.switch_to.default_content()
        return self._buscar_em_frames(script, *args)

    def _abrir_todas_pastas(self) -> None:
        """Carrega no DOM os documentos de todas as pastas da arvore do SEI.

        Em processos grandes o SEI deixa somente a pasta corrente carregada. Uma
        busca direta nesse estado nao enxerga documentos guardados nas demais
        pastas e pode concluir, incorretamente, que a ficha ainda nao existe.
        """
        script_estado = r"""
const abrir = document.querySelector('a[id^="anchorAP"]');
if (!abrir) return null;
const pastas = document.querySelectorAll('a[id^="anchorPASTA"]');
if (!pastas.length || (location.href || '').includes('abrir_pastas=1')) return 'abertas';
return 'fechadas';
"""
        estado = self._achar(script_estado)
        if estado in (None, "abertas"):
            return

        script_abrir = r"""
const abrir = document.querySelector('a[id^="anchorAP"]');
if (!abrir) return null;
abrir.click();
return true;
"""
        if not self._achar(script_abrir):
            raise RuntimeError("nao foi possivel abrir todas as pastas da arvore")
        self._esperar(
            lambda: self._achar(script_estado) == "abertas",
            "o carregamento de todas as pastas da arvore",
            timeout=20,
        )

    def _localizar_documentos_existentes(self, ficha: FichaDisciplina) -> list[dict]:
        """Localiza na arvore documentos cujo numero seja o codigo da ficha."""
        script = r"""
const norm = s => (' ' + (s || '').normalize('NFD')
  .replace(/[\u0300-\u036f]/g, '').toUpperCase()
  .replace(/[^A-Z0-9]+/g, ' ').replace(/\s+/g, ' ').trim() + ' ');
// A pontuacao faz parte do codigo: FEELT!MRII e FEELT_MRII nao sao
// identificadores iguais. Remover !/_ aqui provoca falsos duplicados.
const ident = s => (s || '').normalize('NFD')
  .replace(/[\u0300-\u036f]/g, '').toUpperCase()
  .replace(/\s+/g, ' ').trim();
const codigo = ident(arguments[0]);
const tipo = norm(arguments[1]);
const nome = norm(arguments[2]);
const candidatos = Array.from(document.querySelectorAll('a[href], a[id^="anchor"]'));
const vistos = new Set();
const achados = [];
for (const link of candidatos) {
  const href = link.getAttribute('href') || '';
  const id = link.id || '';
  const target = link.getAttribute('target') || '';
  if (!href.includes('documento_visualizar') &&
      !/visualizacao/i.test(target) && !/^anchor\d+$/i.test(id)) continue;
  const textoOriginal = [
    link.textContent, link.title, link.getAttribute('aria-label'),
    link.querySelector('img') && link.querySelector('img').alt
  ].filter(Boolean).join(' ');
  const texto = norm(textoOriginal);
  if (!((' ' + ident(textoOriginal) + ' ').includes(' ' + codigo + ' '))) continue;
  const chave = href + '|' + id;
  if (vistos.has(chave)) continue;
  vistos.add(chave);
  let pontos = 100;
  if (texto.includes(tipo)) pontos += 20;
  const palavrasNome = nome.trim().split(/\s+/).filter(p => p.length > 2).slice(0, 4);
  pontos += palavrasNome.filter(p => texto.includes(' ' + p + ' ')).length;
  // Nao devolva o WebElement ao Python. A arvore do SEI e atualizada de
  // forma assincrona e qualquer referencia guardada aqui pode ficar stale
  // antes da comparacao. href/id sao relocalizados no instante do clique.
  // Uma ficha cancelada ainda fica visivel na arvore e pode ter o mesmo
  // numero de uma nova versao. O estado costuma estar no titulo/classe do
  // proprio no (ou de seu item de lista), nao necessariamente no texto.
  const item = link.closest('li');
  const estado = [
    textoOriginal, link.className, link.getAttribute('data-status'),
    link.parentElement && link.parentElement.textContent,
    item && item.className, item && item.title, item && item.getAttribute('data-status')
  ].filter(Boolean).join(' ');
  const cancelado = /\bcancelad[oa]\b/i.test(estado);
  achados.push({texto: texto.trim(), href, id, pontos, cancelado});
}
return achados.sort((a, b) => b.pontos - a.pontos);
"""
        return self._achar(script, ficha.codigo, TIPO_DOCUMENTO, ficha.nome) or []

    @staticmethod
    def _documento_cancelado(documento: dict) -> bool:
        """Indica se o no da arvore representa um documento cancelado."""
        if documento.get("cancelado"):
            return True
        # Mantem compatibilidade com arvores antigas, nas quais o marcador
        # vinha somente junto do texto do no.
        return bool(re.search(r"\bcancelad[oa]\b", documento.get("texto", ""), re.I))

    def _documentos_ativos(self, documentos: Iterable[dict]) -> list[dict]:
        """Remove versoes canceladas: elas nao devem ser comparadas nem reutilizadas."""
        return [doc for doc in documentos if not self._documento_cancelado(doc)]

    def _listar_nos_documento(self) -> list[dict]:
        """Lista, na ordem da árvore, todos os nós que representam documentos.

        Guarda apenas identificadores (texto/href/id), nunca o elemento do DOM:
        o elemento pertence ao frame da árvore e fica inválido assim que o
        código troca de frame para ler o documento selecionado. Cada nó é
        relocalizado sob demanda por ``_selecionar_no_arvore`` no momento do
        clique.
        """
        script = r"""
const norm = s => (s || '').replace(/\s+/g, ' ').trim();
const candidatos = Array.from(document.querySelectorAll('a[href], a[id^="anchor"]'));
const vistos = new Set();
const resultado = [];
for (const link of candidatos) {
  const href = link.getAttribute('href') || '';
  const id = link.id || '';
  const target = link.getAttribute('target') || '';
  const ehDocumento = href.includes('documento_visualizar') ||
    /visualizacao/i.test(target) || /^anchor\d+$/i.test(id);
  if (!ehDocumento) continue;
  const chave = href + '|' + id;
  if (vistos.has(chave)) continue;
  vistos.add(chave);
  const texto = norm([link.textContent, link.title, link.getAttribute('aria-label')]
    .filter(Boolean).join(' '));
  if (!texto) continue;
  resultado.push({texto, titulo: norm(link.title || ''), href, id});
}
return resultado;
"""
        return self._achar(script) or []

    def _selecionar_no_arvore(self, href: str, id_: str) -> bool:
        """Relocaliza e clica no nó da árvore com o href/id indicados.

        A busca e o clique acontecem na mesma chamada de script, dentro do
        frame em que ``_achar`` encontrou o elemento, evitando o uso de uma
        referência de elemento capturada em outro momento/frame.
        """
        script = r"""
const href = arguments[0], id = arguments[1];
const candidatos = document.querySelectorAll('a[href], a[id^="anchor"]');
for (const link of candidatos) {
  if ((link.getAttribute('href') || '') !== href || (link.id || '') !== id) continue;
  link.scrollIntoView({block:'center'});
  link.click();
  return true;
}
return false;
"""
        resultado = self._achar(script, href, id_)
        self.driver.switch_to.default_content()
        return bool(resultado)

    def _ler_texto_documento_atual(self) -> Optional[dict]:
        """Lê o texto visível do documento atualmente exibido na tela.

        Retorna ``None`` para frames com pouco texto (menus, cabeçalhos), de
        forma a deixar a busca recursiva em ``_achar`` continuar até achar o
        frame que realmente contém o conteúdo do documento.
        """
        script = r"""
const corpo = document.body;
if (!corpo) return null;
const texto = (corpo.innerText || '').replace(/[ \t]+/g, ' ').trim();
if (texto.length < 80) return null;
return {texto};
"""
        return self._achar(script)

    def extrair_arvore_processo(
        self,
        *,
        capturar_conteudo: bool = True,
        progresso: Optional[Callable[[int, int, str], None]] = None,
    ) -> list[DocumentoArvore]:
        """Percorre a árvore do processo aberto e monta um inventário dos documentos.

        Os campos código/tipo/data/resumo de cada item são heurísticos, lidos
        do texto visível de cada documento (não há campos estruturados
        disponíveis na tela de visualização do SEI); revise o resultado antes
        de usar em um parecer.
        """
        if not self.driver.window_handles:
            raise RuntimeError("o navegador foi fechado")
        janela_principal = self.driver.current_window_handle
        self.driver.switch_to.default_content()
        self._abrir_todas_pastas()

        nos = self._listar_nos_documento()
        if not nos:
            raise RuntimeError("nenhum documento foi encontrado na árvore do processo")

        documentos: list[DocumentoArvore] = []
        total = len(nos)
        for indice, no in enumerate(nos, start=1):
            nome_arvore = no.get("texto", "").strip()
            if progresso:
                progresso(indice, total, nome_arvore)

            self.driver.switch_to.window(janela_principal)
            self.driver.switch_to.default_content()
            texto_documento = ""
            if capturar_conteudo:
                try:
                    if not self._selecionar_no_arvore(no.get("href", ""), no.get("id", "")):
                        raise RuntimeError("não foi possível localizar o documento na árvore")
                    info = self._esperar(
                        self._ler_texto_documento_atual,
                        "o conteúdo do documento",
                        timeout=10,
                    )
                    texto_documento = info.get("texto", "")
                except Exception as exc:
                    # Um documento problemático (bloqueado, com erro do SEI, etc.) não
                    # deve interromper o inventário dos demais — é leitura, não escrita.
                    self.log(f"   aviso: não foi possível ler '{nome_arvore}' ({exc})")

            tipo_numero = extrair_tipo_numero(texto_documento)
            documentos.append(
                DocumentoArvore(
                    ordem=indice,
                    nome_arvore=nome_arvore,
                    codigo_sei=extrair_codigo_sei(texto_documento),
                    tipo_numero=tipo_numero,
                    data=extrair_data(texto_documento),
                    resumo=extrair_resumo(texto_documento, tipo_numero),
                    href=no.get("href", ""),
                )
            )

        self.driver.switch_to.window(janela_principal)
        self.driver.switch_to.default_content()
        return documentos

    @staticmethod
    def _nome_arvore_corresponde(documento: dict, ficha: FichaDisciplina) -> bool:
        def normalizar(valor: str) -> str:
            valor = _sem_acentos(valor).upper()
            return " " + re.sub(r"[^A-Z0-9]+", " ", valor).strip() + " "

        return normalizar(ficha.nome_arvore) in normalizar(documento.get("texto", ""))

    def _clicar_acao_documento(self, acao: str) -> bool:
        """Relocaliza e clica uma acao sem conservar um WebElement do SEI."""
        script = r"""
const desejada = arguments[0];
const norm = s => (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g,'')
  .toLowerCase().replace(/\s+/g,' ').trim();
const vis = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
for (const el of document.querySelectorAll('a[href],button,input[type=button],input[type=submit]')) {
  if (!vis(el)) continue;
  const href = (el.getAttribute('href') || '').toLowerCase();
  const onclick = (el.getAttribute('onclick') || '').toLowerCase();
  const texto = norm([
    el.textContent, el.title, el.value, el.getAttribute('aria-label'),
    el.querySelector('img') && el.querySelector('img').alt
  ].filter(Boolean).join(' '));
  if (desejada === 'editar' &&
      (href.includes('documento_editar_conteudo') || onclick.includes('editarconteudo') ||
       texto.includes('editar conteudo'))) {
    el.click();
    return true;
  }
  if (desejada === 'alterar' &&
      (href.includes('documento_alterar') || texto.includes('consultar alterar documento') ||
       texto.includes('alterar documento'))) {
    el.click();
    return true;
  }
}
return false;
"""
        return bool(self._achar(script, acao))

    def _atualizar_metadados_existente(self, ficha: FichaDisciplina) -> None:
        self._esperar(
            lambda: self._clicar_acao_documento("alterar"),
            "a ação Consultar/Alterar Documento",
            timeout=7,
        )
        self._esperar(
            self._esta_no_formulario_metadados,
            "o formulário de metadados do documento",
            timeout=15,
        )
        self._preencher_metadados(ficha)
        self._clicar_salvar_metadados()
        self._dispensar_alerta_se_houver()
        self.driver.switch_to.default_content()

    def _abrir_editor_existente(self, janela_principal: str):
        try:
            self._esperar(
                lambda: self._clicar_acao_documento("editar"),
                "a acao Editar Conteudo",
                timeout=7,
            )
        except RuntimeError:
            return None
        return self._esperar(
            lambda: self._achar_editor(janela_principal),
            "o editor do documento existente",
            timeout=30,
        )

    def _ler_conteudo_editor(self, editor_info: dict, janela_editor: str) -> tuple[str, str]:
        self.driver.switch_to.window(janela_editor)
        self.driver.switch_to.default_content()
        if editor_info["kind"] == "infraeditor":
            conteudo = self.driver.execute_script(
                r"""
const infra = window.infraEditor;
if (!infra || !infra.isReady || !infra.editor) return null;
return infra.editor.getData({rootName:arguments[0]});
""",
                editor_info["rootName"],
            )
            if conteudo is None:
                raise RuntimeError("nao foi possivel ler o conteudo atual do CKEditor")
            return str(conteudo), "conteudo"
        resultado = self._achar(
            r"""
const info = arguments[0];
const textarea = (info.id && document.getElementById(info.id)) ||
  (info.name && Array.from(document.querySelectorAll('textarea'))
    .find(el => el.name === info.name));
return textarea ? {encontrada:true, conteudo:textarea.value || ''} : null;
""",
            editor_info,
        )
        if not resultado:
            raise RuntimeError("nao foi possivel relocalizar o HTML do editor")
        return str(resultado.get("conteudo", "")), "documento"

    def _ler_documento_visualizado(
        self, ficha: FichaDisciplina, href_esperado: str = ""
    ) -> Optional[str]:
        """Le o HTML renderizado quando o SEI nao oferece a acao de edicao."""
        script = r"""
const norm = s => (' ' + (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g,'')
  .toUpperCase().replace(/[^A-Z0-9]+/g,' ').replace(/\s+/g,' ').trim() + ' ');
if (document.readyState !== 'complete') return null;
const hrefEsperado = arguments[1] || '';
let idConfirmadoPelaUrl = false;
if (hrefEsperado) {
  try {
    const alvo = new URL(hrefEsperado, location.href);
    const idAlvo = alvo.searchParams.get('id_documento');
    // O HTML da ficha costuma ficar em um iframe filho. Nesse iframe a URL
    // pode nao conter id_documento, embora a URL do frame pai contenha. Subir
    // pela cadeia confirma o documento certo sem rejeitar o conteudo interno.
    if (idAlvo) {
      let janela = window;
      for (let nivel = 0; nivel < 8; nivel++) {
        const idFrame = new URL(janela.location.href, location.href)
          .searchParams.get('id_documento');
        if (idFrame) {
          // Evita comparar a versao anterior enquanto o frame ainda navega.
          if (idFrame !== idAlvo) return null;
          idConfirmadoPelaUrl = true;
          break;
        }
        if (janela === janela.parent) break;
        janela = janela.parent;
      }
    }
  } catch (_) {}
}
const texto = norm(document.body && document.body.innerText);
const codigo = norm(arguments[0]);
// Quando a cadeia de frames ja confirmou o id_documento, o SEI pode omitir
// tanto o titulo quanto o numero no HTML interno. Sem essa confirmacao pela
// URL, mantenha as duas exigencias para nao capturar outro frame da pagina.
if (!idConfirmadoPelaUrl &&
    (!texto.includes(' FICHA DE COMPONENTE CURRICULAR ') || !texto.includes(codigo))) {
  return null;
}
if (document.querySelector('#txtDescricao,#txtNomeArvore')) return null;
if (!document.querySelector('table') || texto.length < 200) return null;
return document.documentElement.outerHTML;
"""
        return self._achar(script, ficha.codigo, href_esperado)

    @staticmethod
    def _conteudo_visual_comparavel(html: str) -> str:
        soup = BeautifulSoup(html, "lxml")
        try:
            return _extrair_conteudo_editor(soup)
        except ValueError:
            return html

    def _aguardar_conteudo_visualizado(
        self,
        ficha: FichaDisciplina,
        href: str,
        id_: str,
        janela_principal: str,
    ) -> str:
        """Aguarda o frame lento do SEI e o reabre uma vez se necessario."""
        timeout = max(TIMEOUT_CONTEUDO_DOCUMENTO, self.timeout)
        ultimo_erro: Optional[RuntimeError] = None
        for tentativa in range(1, TENTATIVAS_CONTEUDO_DOCUMENTO + 1):
            try:
                return self._esperar(
                    lambda: self._ler_documento_visualizado(ficha, href),
                    "o conteudo da ficha existente para comparacao",
                    timeout=timeout,
                )
            except RuntimeError as exc:
                ultimo_erro = exc
                if tentativa >= TENTATIVAS_CONTEUDO_DOCUMENTO:
                    break
                self.log(
                    "   a visualizacao demorou; relocalizando a ficha e tentando novamente"
                )
                self.driver.switch_to.window(janela_principal)
                self.driver.switch_to.default_content()
                if not self._selecionar_no_arvore(href, id_):
                    raise RuntimeError(
                        "a ficha deixou de aparecer na arvore durante a comparacao"
                    ) from exc

        raise RuntimeError(
            "tempo esgotado aguardando o conteudo da ficha existente para comparacao "
            f"apos {TENTATIVAS_CONTEUDO_DOCUMENTO} tentativas de ate "
            f"{timeout:g} segundos"
        ) from ultimo_erro

    def _documento_corresponde_ficha(
        self, documento: dict, ficha: FichaDisciplina
    ) -> bool:
        """Abre uma versao existente e compara metadados e conteudo."""
        # O nome na arvore faz parte da identidade da versao. Se ele mudou,
        # ja existe uma diferenca suficiente para criar outra ficha; nao e
        # necessario abrir/ler o documento antigo (que pode estar bloqueado).
        if not self._nome_arvore_corresponde(documento, ficha):
            self.log("   nome diferente na versao existente")
            return False

        janela_principal = self.driver.current_window_handle
        href = documento.get("href", "")
        id_ = documento.get("id", "")
        if not self._selecionar_no_arvore(href, id_):
            raise RuntimeError("não foi possível abrir uma ficha existente na árvore")

        # Para documentos editaveis, o editor fornece exatamente o HTML salvo
        # no SEI e e a fonte mais confiavel para a comparacao. Nada e alterado
        # ou salvo: o editor e aberto somente para leitura e fechado em seguida.
        editor_existente = self._abrir_editor_existente(janela_principal)
        if editor_existente:
            editor_info, janela_editor = editor_existente
            try:
                atual, formato = self._ler_conteudo_editor(editor_info, janela_editor)
                novo = ficha.conteudo_editor if formato == "conteudo" else ficha.html
                return html_equivalente(atual, novo)
            finally:
                if janela_editor != janela_principal:
                    self._finalizar_janela_editor(janela_editor, janela_principal)
                else:
                    # Algumas versoes do SEI abrem o editor na mesma aba.
                    # Volta para o processo sem submeter o formulario.
                    self.driver.back()
                    self._esperar(
                        lambda: not self._achar_editor(janela_principal),
                        "o retorno do editor para o processo",
                        timeout=10,
                    )
                    self.driver.switch_to.window(janela_principal)
                    self.driver.switch_to.default_content()

        # Documentos assinados/bloqueados podem nao oferecer Editar Conteudo.
        # Neles, usa a representacao renderizada apenas como alternativa.
        visualizado = self._aguardar_conteudo_visualizado(
            ficha,
            href,
            id_,
            janela_principal,
        )
        conteudo_atual = self._conteudo_visual_comparavel(visualizado)
        return html_equivalente(conteudo_atual, ficha.conteudo_editor)

    def _clicar_incluir_documento(self) -> None:
        script = r"""
const norm = s => (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g,'')
  .toLowerCase().replace(/\s+/g,' ').trim();
for (const el of document.querySelectorAll('a, button, input, img')) {
  const alvo = el.closest('a,button') || el;
  const texto = norm([el.textContent, el.title, el.alt, el.value,
    alvo.textContent, alvo.title, alvo.getAttribute('aria-label')].filter(Boolean).join(' '));
  const href = (alvo.href || '').toLowerCase();
  if (href.includes('documento_escolher_tipo') ||
      texto.includes('incluir documento') || texto.includes('gerar documento')) {
    alvo.click(); return true;
  }
}
return false;
"""
        self._esperar(lambda: self._achar(script), "o botão Incluir/Gerar Documento")

    def _esta_na_escolha_de_tipo(self) -> bool:
        script = r"""
const norm = s => (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g,'')
  .toLowerCase().replace(/\s+/g,' ').trim();
return norm(document.body && document.body.innerText).includes('escolha o tipo do documento');
"""
        try:
            return bool(self._achar(script))
        except Exception:
            return False

    def _esta_no_formulario_metadados(self) -> bool:
        script = r"""
const porIdOuNome = id => document.getElementById(id) || document.querySelector(`[name="${id}"]`);
return !!(porIdOuNome('txtDescricao') && porIdOuNome('txtNumero') && porIdOuNome('txtNomeArvore'));
"""
        try:
            return bool(self._achar(script))
        except Exception:
            return False

    def _escolher_tipo(self, tipo: str) -> None:
        script_tipo = r"""
const norm = s => (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g,'')
  .toLowerCase().replace(/\s+/g,' ').trim();
const textoPagina = norm(document.body && document.body.innerText);
if (!textoPagina.includes('escolha o tipo do documento')) return false;
const tipo = norm(arguments[0]);
const vis = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
const seletores = 'a, button, [onclick], tr, li, td, span, div, p';
const candidatos = Array.from(document.querySelectorAll(seletores))
  .filter(el => vis(el) && norm(el.textContent || el.value || el.title) === tipo)
  .sort((a, b) => a.childElementCount - b.childElementCount);
if (!candidatos.length) return false;
const item = candidatos[0];
const alvo = item.closest('a,button,[onclick]') || item.querySelector('a,button,[onclick]') || item;
alvo.scrollIntoView({block:'center'});
alvo.click();
return true;
"""

        # Na lista completa o tipo costuma estar visível sem filtrar. Isso é
        # mais seguro do que preencher algum campo de pesquisa do cabeçalho.
        if self._achar(script_tipo, tipo):
            return

        script_filtro = r"""
const norm = s => (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g,'')
  .toLowerCase().replace(/\s+/g,' ').trim();
const textoPagina = norm(document.body && document.body.innerText);
if (!textoPagina.includes('escolha o tipo do documento')) return false;
const vis = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
const entradas = Array.from(document.querySelectorAll('input[type=text], input:not([type])')).filter(vis);
const campo = entradas[0];
if (!campo) return false;
campo.focus();
const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
setter.call(campo, arguments[0]);
campo.dispatchEvent(new Event('input', {bubbles:true}));
campo.dispatchEvent(new KeyboardEvent('keyup', {bubbles:true, key:'a'}));
campo.dispatchEvent(new Event('change', {bubbles:true}));
return true;
"""
        self._esperar(lambda: self._achar(script_filtro, tipo), "o filtro de tipos de documento")
        time.sleep(0.5)
        self._esperar(lambda: self._achar(script_tipo, tipo), f"o tipo '{tipo}'")

    def _preencher_metadados(self, ficha: FichaDisciplina) -> None:
        script = r"""
const valores = arguments[0];
const norm = s => (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g,'')
  .toLowerCase().replace(/\s+/g,' ').trim();
function emitir(el) {
  el.dispatchEvent(new Event('input', {bubbles:true}));
  el.dispatchEvent(new Event('change', {bubbles:true}));
}
function campo(ids, rotulo) {
  for (const id of ids) {
    const exato = document.getElementById(id) || document.querySelector(`[name="${id}"]`);
    if (exato) return exato;
  }
  for (const label of document.querySelectorAll('label')) {
    if (!norm(label.textContent).includes(norm(rotulo))) continue;
    if (label.htmlFor && document.getElementById(label.htmlFor)) return document.getElementById(label.htmlFor);
    const perto = label.parentElement && label.parentElement.querySelector('input,textarea');
    if (perto) return perto;
  }
  return null;
}
const descricao = campo(['txtDescricao'], 'descricao');
const numero = campo(['txtNumero'], 'numero');
const arvore = campo(['txtNomeArvore'], 'nome na arvore');
if (!descricao || !numero || !arvore) return false;
descricao.value = valores.descricao; emitir(descricao);
numero.value = valores.numero; emitir(numero);
arvore.value = valores.arvore; emitir(arvore);
let publico = document.querySelector('#rdoNivelAcesso0, input[name="rdoNivelAcesso"][value="0"]');
if (!publico) {
  const label = Array.from(document.querySelectorAll('label')).find(l => norm(l.textContent) === 'publico');
  if (label) publico = document.getElementById(label.htmlFor) || label.querySelector('input');
}
if (!publico) return false;
if (!publico.checked) publico.click();
return true;
"""
        valores = {
            "descricao": ficha.descricao,
            "numero": ficha.codigo,
            "arvore": ficha.nome_arvore,
        }
        nome_completo = re.sub(r"\s+", " ", ficha.nome or "").strip().upper()
        if ficha.nome_arvore != nome_completo:
            self.log(
                f"   Nome na Árvore truncado para {len(ficha.nome_arvore)} caracteres: "
                f"{ficha.nome_arvore}"
            )
        self._esperar(lambda: self._achar(script, valores), "o formulário de metadados")

    def _clicar_salvar_metadados(self) -> None:
        script = r"""
const norm = s => (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g,'')
  .toLowerCase().replace(/\s+/g,' ').trim();
const vis = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
let botao = document.querySelector('#btnSalvar');
if (!botao) botao = Array.from(document.querySelectorAll('button,input[type=submit],input[type=button],a'))
  .filter(vis).find(el => ['salvar','confirmar'].includes(norm(el.textContent || el.value || el.title)));
if (!botao) return false;
botao.click(); return true;
"""
        self._esperar(lambda: self._achar(script), "o botão Salvar dos metadados")

    def _dispensar_alerta_se_houver(self) -> None:
        try:
            alerta = self.driver.switch_to.alert
            mensagem = alerta.text
            alerta.accept()
            if mensagem:
                self.log(f"   aviso do SEI: {mensagem}")
        except NoAlertPresentException:
            pass
        except Exception:
            pass

    def _achar_editor(self, janela_principal: str):
        self._dispensar_alerta_se_houver()
        script = r"""
if (window.infraEditor && window.infraEditor.isReady && window.infraEditor.editor) {
  const editor = window.infraEditor.editor;
  const principal = document.querySelector(
    '.infra-editor__secao-principal[id][contenteditable="true"], ' +
    'div[id^="txaEditor_"][contenteditable="true"]'
  );
  if (principal) {
    const raiz = Array.from(editor.model.document.getRoots())
      .find(root => root.rootName === principal.id);
    if (raiz) return {kind:'infraeditor', rootName:raiz.rootName};
  }
}
const textarea = document.querySelector(
  'textarea[name^="txaEditor_"], textarea[id^="txaEditor_"]'
);
return textarea ? {kind:'textarea', id:textarea.id || '', name:textarea.name || ''} : null;
"""
        for handle in list(self.driver.window_handles):
            try:
                self.driver.switch_to.window(handle)
                editor = self._achar(script)
                if editor:
                    return editor, handle
            except Exception:
                continue
        try:
            self.driver.switch_to.window(janela_principal)
        except Exception:
            pass
        return None

    def _salvar_ckeditor(
        self,
        ficha: FichaDisciplina,
        editor_info: dict,
        janela_editor: str,
    ) -> None:
        self.driver.switch_to.window(janela_editor)
        self.driver.switch_to.default_content()
        raiz = editor_info["rootName"]
        resultado = self._achar(
            r"""
const html = arguments[0], rootName = arguments[1];
const infra = window.infraEditor;
if (!infra || !infra.isReady || !infra.editor) return null;
const editor = infra.editor;
const dados = {};
for (const root of editor.model.document.getRoots()) {
  dados[root.rootName] = editor.getData({rootName:root.rootName});
}
if (!(rootName in dados)) return null;
dados[rootName] = html;
editor.setData(dados);
const salvar = editor.plugins.has('Salvar') ? editor.plugins.get('Salvar') : null;
return {rootName, dirty:salvar ? salvar.isDirty : null};
""",
            ficha.conteudo_editor,
            raiz,
        )
        if not resultado:
            raise RuntimeError("não foi possível atualizar a seção principal do CKEditor")

        script_salvar = r"""
const norm = s => (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g,'')
  .toLowerCase().replace(/\s+/g,' ').trim();
const botao = Array.from(document.querySelectorAll('button,input[type=button],input[type=submit],a'))
  .find(el => norm(el.innerText || el.value || el.title) === 'salvar');
if (!botao || botao.disabled || botao.getAttribute('aria-disabled') === 'true' ||
    botao.classList.contains('ck-disabled')) return false;
botao.click();
return true;
"""
        self._esperar(
            lambda: self.driver.execute_script(script_salvar),
            "o botão Salvar habilitar no editor",
            timeout=10,
        )

        def salvamento_concluido():
            if janela_editor not in self.driver.window_handles:
                return True
            self.driver.switch_to.window(janela_editor)
            return bool(
                self.driver.execute_script(
                    r"""
const infra = window.infraEditor;
if (!infra || !infra.editor || !infra.editor.plugins.has('Salvar')) return false;
const salvar = infra.editor.plugins.get('Salvar');
return !salvar.isSaving && !salvar.isDirty;
"""
                )
            )

        self._esperar(
            salvamento_concluido,
            "a confirmação de gravação do CKEditor",
            timeout=30,
        )

    def _finalizar_janela_editor(self, janela_editor: str, janela_principal: str) -> None:
        time.sleep(0.5)
        if janela_editor in self.driver.window_handles:
            self.driver.switch_to.window(janela_editor)
            self._dispensar_alerta_se_houver()
        if janela_editor != janela_principal and janela_editor in self.driver.window_handles:
            try:
                self.driver.switch_to.window(janela_editor)
                self.driver.close()
            except Exception:
                pass
        self.driver.switch_to.window(janela_principal)
        self.driver.switch_to.default_content()

    def _salvar_html_no_editor(
        self,
        ficha: FichaDisciplina,
        janela_principal: str,
        editor_encontrado=None,
    ) -> None:
        editor_info, janela_editor = editor_encontrado or self._esperar(
            lambda: self._achar_editor(janela_principal), "o editor do documento", timeout=30
        )
        if editor_info["kind"] == "infraeditor":
            self._salvar_ckeditor(ficha, editor_info, janela_editor)
            self._finalizar_janela_editor(janela_editor, janela_principal)
            return

        url_editor = self.driver.current_url
        # A URL editor_montar contém uma textarea txaEditor_*. O POST nativo
        # preserva o documento completo, inclusive <head>, CSS e imagens base64.
        resultado = self._achar(
            r"""
const info = arguments[0], html = arguments[1];
const ta = (info.id && document.getElementById(info.id)) ||
  (info.name && Array.from(document.querySelectorAll('textarea'))
    .find(el => el.name === info.name));
if (!ta) return null;
ta.value = html; ta.textContent = html;
ta.dispatchEvent(new Event('input', {bubbles:true}));
ta.dispatchEvent(new Event('change', {bubbles:true}));
const form = ta.form || ta.closest('form');
if (!form) return false;
HTMLFormElement.prototype.submit.call(form);
return {enviado:true, timeOrigin:performance.timeOrigin};
""",
            editor_info,
            ficha.html,
        )
        if not resultado:
            raise RuntimeError("o editor não possui formulário para salvar o HTML")

        def envio_concluido():
            if janela_editor not in self.driver.window_handles:
                return True
            self.driver.switch_to.window(janela_editor)
            if self.driver.current_url != url_editor:
                return True
            # O POST pode recarregar a mesma URL. Nesse caso o timeOrigin do
            # frame do editor muda, sem depender de uma referencia stale para
            # detectar que a navegacao terminou.
            return bool(
                self._achar(
                    r"""
const info = arguments[0];
const textarea = (info.id && document.getElementById(info.id)) ||
  (info.name && Array.from(document.querySelectorAll('textarea'))
    .find(el => el.name === info.name));
return textarea ? performance.timeOrigin !== arguments[1] : null;
""",
                    editor_info,
                    resultado["timeOrigin"],
                )
            )

        self._esperar(envio_concluido, "a gravação do HTML no editor", timeout=20)
        time.sleep(0.5)
        if janela_editor in self.driver.window_handles:
            self.driver.switch_to.window(janela_editor)
            self._dispensar_alerta_se_houver()
        if janela_editor != janela_principal and janela_editor in self.driver.window_handles:
            try:
                self.driver.switch_to.window(janela_editor)
                self.driver.close()
            except Exception:
                pass
        self.driver.switch_to.window(janela_principal)
        self.driver.switch_to.default_content()

    def _criar_ficha(self, ficha: FichaDisciplina, janela_principal: str) -> None:
        self.driver.switch_to.window(janela_principal)
        self.driver.switch_to.default_content()
        if self._esta_no_formulario_metadados():
            self.log("   retomando da tela de metadados")
        elif self._esta_na_escolha_de_tipo():
            self.log("   retomando da tela de escolha do tipo de documento")
            self._escolher_tipo(TIPO_DOCUMENTO)
        else:
            self._clicar_incluir_documento()
            self._escolher_tipo(TIPO_DOCUMENTO)
        self._preencher_metadados(ficha)
        self._clicar_salvar_metadados()
        self._dispensar_alerta_se_houver()
        self._salvar_html_no_editor(ficha, janela_principal)

    def carregar_ficha(self, ficha: FichaDisciplina) -> str:
        if not self.driver.window_handles:
            raise RuntimeError("o navegador foi fechado")
        janela_principal = self.driver.current_window_handle
        self.driver.switch_to.default_content()

        # Fecha um editor deixado por tentativa anterior. O documento já aparece
        # na árvore e será identificado novamente pelo código, evitando aplicar
        # por engano a primeira ficha do novo lote no editor que ficou aberto.
        editor_aberto = self._achar_editor(janela_principal)
        houve_editor_remanescente = bool(editor_aberto)
        if editor_aberto:
            _, janela_editor = editor_aberto
            if janela_editor == janela_principal:
                for handle in self.driver.window_handles:
                    if handle == janela_editor:
                        continue
                    try:
                        self.driver.switch_to.window(handle)
                        if "acao=editor_montar" not in self.driver.current_url:
                            janela_principal = handle
                            break
                    except Exception:
                        continue
            self.log("   fechando editor remanescente para revalidar a ficha pelo código")
            self._finalizar_janela_editor(janela_editor, janela_principal)

        self.driver.switch_to.window(janela_principal)
        self.driver.switch_to.default_content()
        self._abrir_todas_pastas()
        documentos = self._localizar_documentos_existentes(ficha)
        if not documentos and houve_editor_remanescente:
            try:
                documentos = self._esperar(
                    lambda: self._localizar_documentos_existentes(ficha),
                    "a atualização da árvore após fechar o editor anterior",
                    timeout=7,
                )
            except RuntimeError:
                documentos = []
        canceladas = len(documentos) - len(self._documentos_ativos(documentos))
        documentos = self._documentos_ativos(documentos)
        if canceladas:
            self.log(
                f"   {canceladas} ficha(s) cancelada(s) localizada(s); "
                "desconsiderando-as para inserir a nova ficha"
            )
        if not documentos:
            self.log("   ficha ainda não existe; criando documento")
            self._criar_ficha(ficha, janela_principal)
            return STATUS_CRIADA

        self.log(
            f"   {len(documentos)} versão(ões) localizada(s) pelo código "
            f"{ficha.codigo}; comparando"
        )
        for indice, documento in enumerate(documentos, start=1):
            self.driver.switch_to.window(janela_principal)
            self.driver.switch_to.default_content()
            if self._documento_corresponde_ficha(documento, ficha):
                self.log(
                    f"   conteúdo idêntico à versão {indice}; "
                    "nenhuma alteração necessária"
                )
                return STATUS_SEM_ALTERACAO

        self.driver.switch_to.window(janela_principal)
        self.driver.switch_to.default_content()
        self.log("   diferenças encontradas; criando uma nova versão da ficha")
        self._criar_ficha(ficha, janela_principal)
        return STATUS_CRIADA

    def _prevalidar_lote(self, fichas: list[FichaDisciplina]) -> tuple[int, int]:
        """Mapeia o processo antes de criar novas versoes de documentos."""
        self.log("[SEI] Pente-fino inicial: carregando todas as pastas da árvore...")
        self._abrir_todas_pastas()
        vistos: dict[str, FichaDisciplina] = {}
        existentes = 0
        ausentes = 0
        for ficha in fichas:
            chave = normalizar_codigo(ficha.codigo)
            anterior = vistos.get(chave)
            if anterior is not None:
                raise RuntimeError(
                    f"o código '{ficha.codigo}' aparece em mais de um arquivo local: "
                    f"{anterior.caminho.name} e {ficha.caminho.name}"
                )
            vistos[chave] = ficha

            achados = self._documentos_ativos(
                self._localizar_documentos_existentes(ficha)
            )
            if achados:
                existentes += 1
            else:
                ausentes += 1
        self.log(
            f"[SEI] Pente-fino inicial: {existentes} existente(s) e "
            f"{ausentes} nova(s)."
        )
        return existentes, ausentes

    def _verificar_resultado_lote(self, fichas: list[FichaDisciplina]) -> None:
        """Confirma que cada codigo local aparece ao menos uma vez no processo."""
        self._abrir_todas_pastas()
        faltantes: list[str] = []
        for ficha in fichas:
            quantidade = len(
                self._documentos_ativos(self._localizar_documentos_existentes(ficha))
            )
            if quantidade == 0:
                faltantes.append(ficha.codigo)
        if faltantes:
            raise RuntimeError("ausentes: " + ", ".join(faltantes))
        self.log("[SEI] Pente-fino final: todos os códigos estão presentes.")

    def carregar_lote(self, fichas: Iterable[FichaDisciplina]) -> tuple[int, list[str]]:
        fichas = list(fichas)
        concluidas = 0
        erros: list[str] = []
        self.estatisticas = {
            STATUS_CRIADA: 0,
            STATUS_ATUALIZADA: 0,
            STATUS_SEM_ALTERACAO: 0,
        }
        try:
            self._prevalidar_lote(fichas)
        except Exception as exc:
            erro = f"pré-varredura do processo: {exc}"
            erros.append(erro)
            self.log(f"[SEI] ERRO: {erro}")
            self.log("[SEI] Nenhum documento foi criado, alterado ou excluído.")
            return concluidas, erros

        for indice, ficha in enumerate(fichas, start=1):
            self.log(f"[SEI] {indice}/{len(fichas)} — {ficha.codigo} — {ficha.nome}")
            try:
                status = self.carregar_ficha(ficha)
                concluidas += 1
                self.estatisticas[status] += 1
                mensagens = {
                    STATUS_CRIADA: "documento criado e HTML salvo",
                    STATUS_ATUALIZADA: "documento existente atualizado",
                    STATUS_SEM_ALTERACAO: "documento já estava atualizado",
                }
                self.log(f"   {mensagens[status]}")
            except Exception as exc:
                erros.append(f"{ficha.caminho.name}: {exc}")
                self.log(f"   ERRO: {exc}")
                self.log("[SEI] Lote interrompido para preservar a consistência do processo.")
                break
        if not erros:
            try:
                self._verificar_resultado_lote(fichas)
            except Exception as exc:
                erro = f"pente-fino final: {exc}"
                erros.append(erro)
                self.log(f"[SEI] ERRO: {erro}")
        return concluidas, erros
