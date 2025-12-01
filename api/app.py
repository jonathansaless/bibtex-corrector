import io
import re
from flask import Flask, render_template, request, send_file, abort
import bibtexparser
from bibtexparser.bparser import BibTexParser

app = Flask(__name__)


# -----------------------------------------------------------
#  UTIL: gerar ID a partir do TÍTULO + ANO
# -----------------------------------------------------------

def gerar_id_titulo_ano(titulo: str, ano: str) -> str:
    # Remove chaves do BibTeX e espaços extras
    titulo = titulo.replace("{", "").replace("}", "").strip()

    # Pega a primeira palavra do título
    primeira = re.split(r"\s+", titulo)[0]
    primeira = re.sub(r"[^A-Za-z0-9]", "", primeira)

    if not primeira:
        primeira = "Entry"

    if ano:
        return f"{primeira}{ano}"
    return primeira


# -----------------------------------------------------------
#  NOVO: CORRIGIR ESPAÇOS NO ID (Raw Regex)
# -----------------------------------------------------------

def corrigir_espacos_ids_raw(conteudo: str) -> str:
    """
    Localiza IDs que possuem espaços (ex: @article{Dal Maso2025,)
    e substitui os espaços por underlines (ex: @article{Dal_Maso2025,).
    Faz trim (remove espaços nas pontas) antes de substituir os internos.
    """
    
    # Regex Breakdown:
    # (@\w+\s*{\s*)  -> Grupo 1: Captura "@tipo{" e possíveis espaços iniciais
    # ([^,]+?)       -> Grupo 2: Captura o ID (tudo até a primeira vírgula, modo non-greedy)
    # (\s*,)         -> Grupo 3: Captura a vírgula e espaços anteriores
    pattern = r"(@\w+\s*{\s*)([^,]+?)(\s*,)"

    def replacer(match):
        prefix = match.group(1)  # ex: @ARTICLE{
        raw_id = match.group(2)  # ex: Dal Maso2025
        suffix = match.group(3)  # ex: ,

        # Se não tiver espaço, retorna como está
        if ' ' not in raw_id:
            return match.group(0)

        # 1. Remove espaços das bordas (trim) para não criar _Dal_Maso_
        # 2. Substitui espaços internos por _
        clean_id = raw_id.strip()
        
        # Se após o strip o ID ficar vazio, deixa para a função de ID vazio tratar depois
        if not clean_id:
            return match.group(0)

        new_id = re.sub(r"\s+", "_", clean_id)
        
        return f"{prefix}{new_id}{suffix}"

    return re.sub(pattern, replacer, conteudo)


# -----------------------------------------------------------
#  CORRIGIR @tipo{, ... } (IDs Vazios) Raw Regex
# -----------------------------------------------------------

def corrigir_ids_vazios_raw(conteudo: str) -> str:
    """
    Corrige entradas com ID vazio diretamente no texto,
    antes de passar pelo bibtexparser.
    """
    pattern = r"@(\w+)\s*{\s*,(.*?)}\s*(?=@\w+|$)"

    def replacer(match):
        tipo = match.group(1)
        body = match.group(2)

        # Extrair título
        titulo_match = re.search(r"title\s*=\s*{(.+?)}", body, re.DOTALL | re.IGNORECASE)
        titulo = titulo_match.group(1).strip() if titulo_match else "Entry"

        # Extrair ano
        ano_match = re.search(r"year\s*=\s*{(.+?)}", body, re.DOTALL | re.IGNORECASE)
        ano = ano_match.group(1).strip() if ano_match else ""

        # Gerar novo ID
        novo_id = gerar_id_titulo_ano(titulo, ano)

        return f"@{tipo}{{{novo_id},{body}}}"

    return re.sub(pattern, replacer, conteudo, flags=re.DOTALL)


# -----------------------------------------------------------
#  CORRIGIR IDs RESTANTES usando bibtexparser
# -----------------------------------------------------------

def gerar_id_unico(entry, existing_ids, fallback_index):
    year = (entry.get("year") or "").strip()
    title = (entry.get("title") or "").strip()

    base = gerar_id_titulo_ano(title, year)
    candidate = base

    suffix = 2
    while candidate in existing_ids:
        candidate = f"{base}_{suffix}"
        suffix += 1

    existing_ids.add(candidate)
    return candidate


def corrigir_bibtex(conteudo_bib):
    parser = BibTexParser()
    parser.ignore_nonstandard_types = False
    
    try:
        bib_db = bibtexparser.loads(conteudo_bib, parser=parser)
    except Exception as e:
        # Fallback simples se falhar parsing complexo
        bib_db = bibtexparser.loads(conteudo_bib)

    existing_ids = set(e.get("ID") for e in bib_db.entries if e.get("ID"))
    total_entradas = len(bib_db.entries)
    total_corrigidas = 0

    for idx, entry in enumerate(bib_db.entries, start=1):
        entry_id = entry.get("ID", "")
        # Verifica se está vazio ou None
        if not entry_id or entry_id.strip() == "":
            entry["ID"] = gerar_id_unico(entry, existing_ids, idx)
            total_corrigidas += 1

    texto_corrigido = bibtexparser.dumps(bib_db)

    comentario = (
        f"% Processamento completo.\n"
        f"% IDs vazios preenchidos: {total_corrigidas}\n"
        f"% IDs com espaços ajustados previamente.\n"
        f"% Gerado por BibTeX ID Fixer (Flask).\n\n"
    )

    return comentario + texto_corrigido, total_entradas, total_corrigidas


# -----------------------------------------------------------
#  ROTAS
# -----------------------------------------------------------

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    if "bibfile" not in request.files:
        abort(400, description="Nenhum arquivo enviado.")

    file = request.files["bibfile"]

    if file.filename == "":
        abort(400, description="Nenhum arquivo selecionado.")

    raw = file.read()
    try:
        conteudo = raw.decode("utf-8")
    except UnicodeDecodeError:
        conteudo = raw.decode("latin-1")

    # -----------------------------------------------------------
    # 1) Corrigir IDs com ESPAÇOS no TEXTO BRUTO
    #    (Executado primeiro para garantir formato válido)
    # -----------------------------------------------------------
    conteudo = corrigir_espacos_ids_raw(conteudo)

    # -----------------------------------------------------------
    # 2) Corrigir IDs vazios no TEXTO BRUTO (regex)
    # -----------------------------------------------------------
    conteudo = corrigir_ids_vazios_raw(conteudo)

    # -----------------------------------------------------------
    # 3) Carregar no bibtexparser e corrigir IDs faltantes remanescentes
    # -----------------------------------------------------------
    bib_corrigido, total, corrigidas = corrigir_bibtex(conteudo)

    # Criar arquivo de saída
    output = io.BytesIO()
    output.write(bib_corrigido.encode("utf-8"))
    output.seek(0)

    base = file.filename.rsplit(".", 1)[0]
    nome_saida = f"{base}_corrigido.bib"

    response = send_file(
        output,
        mimetype="application/x-bibtex",
        as_attachment=True,
        download_name=nome_saida,
    )

    response.headers["X-Bibtex-Total"] = str(total)
    response.headers["X-Bibtex-Corrigidas"] = str(corrigidas)

    return response


if __name__ == "__main__":
    app.run(debug=True)