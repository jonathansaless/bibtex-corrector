import io
import re
from flask import Flask, render_template, request, send_file, abort
import bibtexparser

app = Flask(__name__)


# -----------------------------------------------------------
#  UTIL: gerar ID a partir do TÍTULO + ANO
# -----------------------------------------------------------

def gerar_id_titulo_ano(titulo: str, ano: str) -> str:
    # Remove chaves do BibTeX
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
#  CORRIGIR @tipo{, ... } ANTES do bibtexparser
# -----------------------------------------------------------

def corrigir_ids_vazios_raw(conteudo: str) -> str:
    """
    Corrige entradas com ID vazio diretamente no texto,
    antes de passar pelo bibtexparser.
    Isso evita que o parser converta a entrada em @comment{...}.
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
    """
    Agora esta função só corrige IDs vazios remanescentes,
    já que a maioria foi tratada pelo pré-processador raw.
    """
    bib_db = bibtexparser.loads(conteudo_bib)

    existing_ids = set(e.get("ID") for e in bib_db.entries if e.get("ID"))
    total_entradas = len(bib_db.entries)
    total_corrigidas = 0

    for idx, entry in enumerate(bib_db.entries, start=1):
        entry_id = entry.get("ID", "")
        if not entry_id or entry_id.strip() == "":
            entry["ID"] = gerar_id_unico(entry, existing_ids, idx)
            total_corrigidas += 1

    texto_corrigido = bibtexparser.dumps(bib_db)

    comentario = (
        f"% Corrigido automaticamente: {total_corrigidas} de {total_entradas} entradas sem ID.\n"
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
    # 1) Corrigir IDs vazios no TEXTO BRUTO (regex)
    # -----------------------------------------------------------
    conteudo = corrigir_ids_vazios_raw(conteudo)

    # -----------------------------------------------------------
    # 2) Carregar no bibtexparser e corrigir IDs faltantes
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


# # Execução local
if __name__ == "__main__":
    app.run(debug=True)
