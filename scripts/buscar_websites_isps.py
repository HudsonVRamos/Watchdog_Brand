"""
Script para buscar websites oficiais de ISPs a partir da planilha de comercializadores.

Usa a API Serper (Google Search) para encontrar os sites oficiais.
Versão inteligente com:
- Pesquisa em português, espanhol e inglês
- Priorização de domínios regionais (.com, .com.co, .com.ar, .cl, .pe, .mx)
- Filtro de agregadores e redes sociais
- Validação do domínio (acesso à página inicial)
- Detecção do nome da empresa no <title> ou conteúdo
- Cache para evitar buscas repetidas
- Múltiplas tentativas quando a primeira busca falha

Uso:
    $env:SERPER_API_KEY="SUA_CHAVE"
    python scripts/buscar_websites_isps.py
"""

import os
import sys
import time
import json
import logging
import hashlib
import requests
import pandas as pd
from pathlib import Path
from urllib.parse import urlparse
from tqdm import tqdm

# Configuração de logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("scripts/buscar_websites.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Configurações
# ─────────────────────────────────────────────────────────────────────────────

API_KEY = os.getenv("SERPER_API_KEY")

INPUT_FILE = Path("watchdog_rules/Comercializadores DGO y Amazon Prime.xlsx")
OUTPUT_FILE = Path("watchdog_rules/Comercializadores DGO y Amazon Prime - Websites.xlsx")
CACHE_FILE = Path("scripts/.cache_websites.json")

# Domínios que devem ser ignorados (redes sociais, agregadores, etc.)
BAD_DOMAINS = {
    "facebook.com",
    "linkedin.com",
    "instagram.com",
    "x.com",
    "twitter.com",
    "youtube.com",
    "wikipedia.org",
    "mapcarta.com",
    "crunchbase.com",
    "bloomberg.com",
    "glassdoor.com",
    "yelp.com",
    "yellowpages.com",
    "paginasamarillas.com",
    "trustpilot.com",
    "bbb.org",
    "zoominfo.com",
    "dnb.com",
    "hoovers.com",
    "manta.com",
    "opencorporates.com",
    "tiktok.com",
    "pinterest.com",
    "reddit.com",
    "quora.com",
    "medium.com",
    "blogspot.com",
    "wordpress.com",
    "wix.com",
    "weebly.com",
    "google.com",
    "bing.com",
    "yahoo.com",
    "amazon.com",
    "mercadolibre.com",
    "olx.com",
    "ebay.com",
}

# Domínios regionais priorizados (LATAM)
PREFERRED_TLDS = [
    ".com.co", ".com.ar", ".com.mx", ".com.pe", ".com.ec",
    ".com.ve", ".com.br", ".com.cl", ".com.uy", ".com.py",
    ".com.bo", ".com.pa", ".com.gt", ".com.hn", ".com.sv",
    ".com.ni", ".com.cr", ".com.do", ".com.cu",
    ".co", ".ar", ".mx", ".pe", ".ec", ".ve", ".br",
    ".cl", ".uy", ".py", ".bo", ".pa", ".gt", ".hn",
    ".sv", ".ni", ".cr", ".do", ".cu",
    ".com", ".net", ".org",
]

# Templates de busca em múltiplos idiomas
SEARCH_QUERIES = [
    '{company} ISP sitio web oficial',
    '{company} proveedor internet sitio oficial',
    '{company} ISP official website',
    '{company} provedor internet site oficial',
]


# ─────────────────────────────────────────────────────────────────────────────
# Funções auxiliares
# ─────────────────────────────────────────────────────────────────────────────

def load_cache() -> dict:
    """Carrega o cache de resultados anteriores."""
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_cache(cache: dict) -> None:
    """Salva o cache de resultados."""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def cache_key(company: str) -> str:
    """Gera uma chave de cache normalizada para a empresa."""
    normalized = company.strip().lower()
    return hashlib.md5(normalized.encode()).hexdigest()


def clean_domain(url: str) -> str:
    """Extrai o domínio limpo de uma URL."""
    try:
        domain = urlparse(url).netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


def is_bad_domain(domain: str) -> bool:
    """Verifica se o domínio está na lista de exclusão."""
    for bad in BAD_DOMAINS:
        if domain.endswith(bad):
            return True
    return False


def score_domain(domain: str) -> int:
    """Pontua o domínio com base na preferência regional."""
    for i, tld in enumerate(PREFERRED_TLDS):
        if domain.endswith(tld):
            return len(PREFERRED_TLDS) - i
    return 0


def validate_website(url: str, company: str) -> bool:
    """
    Valida se o website é acessível e se o nome da empresa aparece
    no título ou conteúdo da página.
    """
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        response = requests.get(
            url,
            headers=headers,
            timeout=10,
            allow_redirects=True,
        )

        if response.status_code >= 400:
            return False

        content_lower = response.text.lower()
        company_lower = company.strip().lower()

        # Verifica palavras significativas do nome da empresa no conteúdo
        company_words = [w for w in company_lower.split() if len(w) > 3]

        if not company_words:
            # Se o nome é muito curto, verifica presença direta
            return company_lower in content_lower

        # Se pelo menos metade das palavras significativas aparece, é válido
        matches = sum(1 for w in company_words if w in content_lower)
        return matches >= max(1, len(company_words) // 2)

    except Exception as e:
        logger.debug(f"Erro ao validar {url}: {e}")
        # Se não consegue acessar, ainda aceita o resultado da busca
        return True


def search_serper(query: str) -> list[dict]:
    """Executa uma busca na API Serper e retorna os resultados orgânicos."""
    headers = {
        "X-API-KEY": API_KEY,
        "Content-Type": "application/json",
    }

    payload = {"q": query}

    try:
        response = requests.post(
            "https://google.serper.dev/search",
            headers=headers,
            json=payload,
            timeout=20,
        )

        if response.status_code != 200:
            logger.warning(f"API retornou status {response.status_code}: {response.text}")
            return []

        data = response.json()
        return data.get("organic", [])

    except Exception as e:
        logger.error(f"Erro na busca Serper: {e}")
        return []


def find_best_result(results: list[dict], company: str) -> str:
    """
    Encontra o melhor resultado entre os orgânicos.
    Prioriza domínios regionais e valida o conteúdo.
    """
    candidates = []

    for result in results:
        link = result.get("link", "")
        domain = clean_domain(link)

        if not domain or is_bad_domain(domain):
            continue

        score = score_domain(domain)
        candidates.append((score, link, domain))

    # Ordena por pontuação (maior primeiro)
    candidates.sort(key=lambda x: x[0], reverse=True)

    # Tenta validar os top candidatos
    for score, link, domain in candidates[:5]:
        if validate_website(link, company):
            return link

    # Se nenhum passou na validação mas há candidatos, retorna o primeiro
    if candidates:
        return candidates[0][1]

    return ""


def search_company(company: str) -> str:
    """
    Busca o website oficial de uma empresa usando múltiplas queries.
    Retorna a URL do site oficial ou string vazia.
    """
    if not company or not company.strip():
        return ""

    all_results = []

    for query_template in SEARCH_QUERIES:
        query = query_template.format(company=company.strip())
        results = search_serper(query)

        if results:
            best = find_best_result(results, company)
            if best:
                return best

            all_results.extend(results)

        # Pausa entre queries para não exceder rate limit
        time.sleep(0.5)

    # Tenta encontrar nos resultados acumulados
    if all_results:
        return find_best_result(all_results, company)

    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Execução principal
# ─────────────────────────────────────────────────────────────────────────────

def main():
    if not API_KEY:
        logger.error("SERPER_API_KEY não definida. Configure a variável de ambiente.")
        sys.exit(1)

    if not INPUT_FILE.exists():
        logger.error(f"Arquivo de entrada não encontrado: {INPUT_FILE}")
        sys.exit(1)

    logger.info(f"Lendo planilha: {INPUT_FILE}")
    df = pd.read_excel(INPUT_FILE, header=3)

    # Remove colunas totalmente vazias (ex: coluna 0 sem nome)
    df = df.dropna(axis=1, how="all")

    if "NOMBRE ISPs" not in df.columns:
        logger.error("Coluna 'NOMBRE ISPs' não encontrada na planilha.")
        logger.info(f"Colunas disponíveis: {list(df.columns)}")
        sys.exit(1)

    # Remove linhas onde NOMBRE ISPs está vazio
    df = df.dropna(subset=["NOMBRE ISPs"])
    df = df.reset_index(drop=True)

    # Carrega cache
    cache = load_cache()
    logger.info(f"Cache carregado com {len(cache)} entradas")

    # Busca websites
    website_col = []
    total = len(df)
    hits_cache = 0
    hits_api = 0
    misses = 0

    logger.info(f"Iniciando busca para {total} empresas...")

    for company in tqdm(df["NOMBRE ISPs"], desc="Buscando websites"):
        company_str = str(company).strip()

        if not company_str or company_str.lower() == "nan":
            website_col.append("")
            continue

        key = cache_key(company_str)

        # Verifica cache
        if key in cache:
            website_col.append(cache[key])
            hits_cache += 1
            continue

        # Busca na API
        website = search_company(company_str)

        if website:
            hits_api += 1
            logger.info(f"  ✓ {company_str} → {website}")
        else:
            misses += 1
            logger.warning(f"  ✗ {company_str} → não encontrado")

        # Salva no cache (mesmo se vazio, para evitar rebusca)
        cache[key] = website
        website_col.append(website)

        # Pausa entre empresas para respeitar rate limit
        time.sleep(1)

    # Adiciona coluna e salva
    df["Website"] = website_col
    df.to_excel(OUTPUT_FILE, index=False)

    # Salva cache atualizado
    save_cache(cache)

    # Relatório final
    logger.info("=" * 60)
    logger.info("RELATÓRIO FINAL")
    logger.info("=" * 60)
    logger.info(f"Total de empresas: {total}")
    logger.info(f"Encontrados (cache): {hits_cache}")
    logger.info(f"Encontrados (API):   {hits_api}")
    logger.info(f"Não encontrados:     {misses}")
    logger.info(f"Taxa de sucesso:     {((hits_cache + hits_api) / max(total, 1)) * 100:.1f}%")
    logger.info(f"Arquivo salvo em:    {OUTPUT_FILE}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
