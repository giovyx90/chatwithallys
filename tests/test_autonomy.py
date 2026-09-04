import random

from allys.autonomy import ChatterState, decide, parse_level, profile, prompt_for, urge_score

VIVA = ChatterState(
    seconds_since_last_human=30,
    seconds_since_last_allys=10_000,
    new_messages_since_allys=40,
    unique_speakers=5,
    energy=2.0,
)


def test_off_non_parla_mai() -> None:
    assert decide(VIVA, "off").speak is False


def test_zittita_non_parla_mai() -> None:
    zitta = ChatterState(**{**VIVA.__dict__, "quiet": True})
    assert decide(zitta, "alta").speak is False


def test_non_sveglia_una_chat_morta() -> None:
    morta = ChatterState(**{**VIVA.__dict__, "seconds_since_last_human": 4000})
    assert decide(morta, "alta").speak is False


def test_rispetta_la_quota_giornaliera() -> None:
    esaurita = ChatterState(**{**VIVA.__dict__, "interventions_today": 99})
    assert decide(esaurita, "alta").speak is False


def test_rispetta_il_cooldown() -> None:
    appena_parlato = ChatterState(**{**VIVA.__dict__, "seconds_since_last_allys": 60})
    assert decide(appena_parlato, "alta").speak is False


def test_livelli_piu_alti_parlano_di_piu() -> None:
    def quante(level: str) -> int:
        return sum(decide(VIVA, level, random.Random(seed)).speak for seed in range(300))

    assert quante("alta") > quante("media") >= quante("bassa")


def test_un_movimento_di_borsa_diventa_un_commento_di_borsa() -> None:
    mosso = ChatterState(**{**VIVA.__dict__, "market_move": 0.4})
    parlanti = [decide(mosso, "alta", random.Random(seed)) for seed in range(50)]
    detto = [d for d in parlanti if d.speak]
    assert detto and all(d.kind == "borsa" for d in detto)


def test_urge_cresce_con_la_conversazione() -> None:
    calma = ChatterState(new_messages_since_allys=2)
    assert urge_score(VIVA) > urge_score(calma)


def test_parse_level_e_prompt() -> None:
    assert parse_level("Alta") == "alta"
    assert parse_level("fortissimo") is None
    assert "borsa" in prompt_for("borsa").lower()
    assert profile("media")["daily_cap"] > 0
