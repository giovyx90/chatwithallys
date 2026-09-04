from allys.learning import (
    extract_lexicon,
    format_style_block,
    is_explicit_shutup,
    looks_like_shutup,
    reaction_delta,
    reactions_delta,
    reply_delta,
    select_style_examples,
    tone_adjustment,
)


def test_lexicon_needs_more_than_one_persona() -> None:
    """Uno che ripete la stessa parola non detta il lessico del gruppo."""
    spam = [{"username": "mario", "text": "banane banane"} for _ in range(10)]
    assert extract_lexicon(spam, min_count=2) == []

    condiviso = [
        {"username": "mario", "text": "che skill issue clamoroso"},
        {"username": "anna", "text": "skill issue proprio"},
        {"username": "luca", "text": "skill issue anche stavolta"},
    ]
    assert "skill issue" in extract_lexicon(condiviso, min_count=2)


def test_lexicon_ignora_i_messaggi_di_allys() -> None:
    rows = [{"username": "Allys", "text": "tormentone tormentone"} for _ in range(6)]
    assert extract_lexicon(rows, min_count=2) == []


def test_reaction_delta_ha_segno_giusto() -> None:
    assert reaction_delta("😂") > 0
    assert reaction_delta("💩") < 0
    assert reaction_delta("🫠") == 0
    # Togliere un pollice in su e' un peggioramento.
    assert reactions_delta(["👍"], []) < 0
    assert reactions_delta([], ["🔥"]) > 0


def test_reply_delta_premia_engagement_e_punisce_lo_zitta() -> None:
    assert reply_delta("ahahah grandissima") > 0
    assert reply_delta("allys stai zitta") < 0
    assert reply_delta("") == 0


def test_shutup_distingue_ordine_e_malumore() -> None:
    assert is_explicit_shutup("allys stai zitta")
    assert not is_explicit_shutup("che palle questa riunione")
    # Resta comunque un segnale negativo per il punteggio.
    assert looks_like_shutup("che palle")


def test_select_style_examples_scarta_i_flop_e_i_doppioni() -> None:
    rows = [
        {"prompt_text": "che si fa", "reply_text": "niente, come sempre", "score": 1.4},
        {"prompt_text": "che si fa stasera", "reply_text": "niente, come sempre", "score": 1.2},
        {"prompt_text": "x", "reply_text": "risposta fiacca", "score": 0.1},
    ]
    examples = select_style_examples(rows)
    assert len(examples) == 1
    assert examples[0].reply == "niente, come sempre"


def test_format_style_block_e_vuoto_senza_materiale() -> None:
    assert format_style_block([], []) == ""
    block = format_style_block(select_style_examples(
        [{"prompt_text": "ciao", "reply_text": "ehila", "score": 2.0}]
    ), ["skill issue"])
    assert "skill issue" in block
    assert "ehila" in block


def test_tone_adjustment_accorcia_se_la_bastonano() -> None:
    neutro = tone_adjustment({"samples": 2, "average": -3})
    assert neutro["length_factor"] == 1.0  # pochi voti: non si muove

    punita = tone_adjustment({"samples": 30, "average": -0.9})
    amata = tone_adjustment({"samples": 30, "average": 1.2})
    assert punita["length_factor"] < amata["length_factor"]
    assert punita["helpful_bonus"] > amata["helpful_bonus"]


def test_lexicon_non_ripete_lo_stesso_concetto() -> None:
    """Niente tormentoni sovrapposti: mai "skill issue" e "issue clamoroso" insieme."""
    rows = [{"username": u, "text": "skill issue clamoroso"} for u in ("a", "b", "c")]
    lexicon = extract_lexicon(rows, min_count=2)
    assert "skill issue" in lexicon
    assert "issue clamoroso" not in lexicon
    parole = [set(voce.split()) for voce in lexicon]
    for indice, parole_voce in enumerate(parole):
        for altre in parole[indice + 1:]:
            assert not (parole_voce & altre)
