TRIGGER_TYPE = "contains"
TRIGGERS = [
    "lucid status",
    "veganismo",
    "dissertacao",
    "conferencia",
    "ativistas",
    "videos",
    "anarquismo",
    "veganismo e anarquismo",
]


def get_veganismo_info():
    return (
        "Veganismo é uma filosofia que rejeita a exploração e opressão de seres vivos, "
        "promovendo uma vida alinhada com a justiça ecológica e social. "
        "A convergência com o anarquismo critica sistemas capitalistas de dominação e "
        "exploração, enfatizando ações diretas e organização de base."
    )


def get_dissertacao_info():
    return (
        "A dissertação 'Exploração Animal, Veganismo Popular e Capitalismo: Efeitos de "
        "Sentidos dos Ativistas Digitais' de Williams da Silva Rodrigues analisa como "
        "ativistas veganos usam mídia digital para criticar capitalismo e promover "
        "alternativas. Supervisionada por Prof. Dr. Marcelo Burgos Pimentel."
    )


def get_conferencia_info():
    return (
        "A Conferência 'Interrogar o Capitalismo' ocorre online na próxima semana, "
        "discutindo a interseção entre veganismo, anarquismo e resistência contra "
        "sistemas opressivos. Foca em estratégias de organização de base e ações "
        "diretas para desafiar estruturas de poder."
    )


def get_ativistas_info():
    return (
        "Ativistas veganos como Vegano Vitor e Vegetal Vermelho promovem ações diretas "
        "e educação comunitária. Eles destacam a importância de desmantelar sistemas de "
        "opressão, incluindo exploração animal e capitalismo, por meio de organização "
        "local e internacional."
    )


def get_video_analysis_info():
    return (
        "Análise de 22 vídeos de ativistas veganos revela como eles criticam capitalismo "
        "e promovem alternativas sustentáveis. Essa pesquisa, parte da dissertação de "
        "Williams da Silva Rodrigues, destaca o papel da mídia digital na disseminação "
        "de ideias veganas e anarcossocialistas."
    )


def get_anarquismo_veganismo_info():
    return (
        "A convergência entre veganismo e anarquismo critica sistemas capitalistas de "
        "dominação e exploração. Essa abordagem enfatiza ações diretas, organização de "
        "base e resistência contra estruturas opressivas, alinhando-se com teorias "
        "marxistas para construir alternativas sociais."
    )


def handle_query(query):
    query_lower = query.lower()
    if "lucid status" in query_lower:
        return "Lucid status: Ativo e preparado para análise de sistemas complexos."
    elif "veganismo" in query_lower:
        return get_veganismo_info()
    elif "dissertacao" in query_lower:
        return get_dissertacao_info()
    elif "conferencia" in query_lower:
        return get_conferencia_info()
    elif "ativistas" in query_lower:
        return get_ativistas_info()
    elif "videos" in query_lower:
        return get_video_analysis_info()
    elif "anarquismo" in query_lower or "veganismo e anarquismo" in query_lower:
        return get_anarquismo_veganismo_info()
    else:
        return "Consulta não corresponde a nenhum trigger conhecido. Especifique sua solicitação."


def handle(user_prompt_lower, user_prompt_full):
    """Adaptador para a interface legacy padrão (handle_query + triggers)."""
    return handle_query(user_prompt_full)
