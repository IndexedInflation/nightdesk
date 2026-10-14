"""The headline reader may block a buy. These tests pin down exactly when."""
from nightdesk import news as N

HOUR = 3600.0


def _h(label, conf, hours_ago, now=1000 * HOUR, title="x"):
    return {"label": label, "conf": conf, "ts": now - hours_ago * HOUR, "title": title}


def test_company_name_is_trimmed_to_the_part_people_actually_say():
    assert N.name_key("Albemarle Corporation") == "albemarle"
    assert N.name_key("Western Digital") == "western digital"
    assert N.name_key("Alphabet Inc. (Class A)") == "alphabet"
    assert N.name_key("The Home Depot") == "home depot"
    assert N.name_key("Franklin Resources, Inc.") == "franklin resources"


def test_a_headline_must_be_about_this_company_not_a_round_up():
    name, sym = "Albemarle Corporation", "ALB"
    assert N.is_specific("Albemarle cuts full-year outlook as lithium prices slide", name, sym, ["ALB"])
    assert N.is_specific("Barclays initiates Albemarle at Equalweight", name, sym, ["ALB", "^GSPC"])
    assert not N.is_specific("Top analyst calls: AT&T, Adobe, Albemarle, American Electric Power", name, sym, ["T", "ADBE", "ALB", "AEP", "PSH"])
    assert not N.is_specific("Stocks slide as bond yields jump", name, sym, ["ALB"])           # company not named
    assert N.is_specific("Why shares of lithium giant (NYSE: ALB) fell today", name, sym, ["ALB"])


def test_confident_fresh_bad_news_blocks_a_buy():
    hit = N.veto_decision([_h("bad", 0.93, 2, title="Outlook cut")], now=1000 * HOUR)
    assert hit is not None and hit["title"] == "Outlook cut"


def test_unsure_or_stale_bad_news_does_not_block():
    assert N.veto_decision([_h("bad", 0.60, 2)], now=1000 * HOUR) is None      # the reader wasn't sure enough
    assert N.veto_decision([_h("bad", 0.95, 30)], now=1000 * HOUR) is None     # more than a day old
    assert N.veto_decision([_h("nothing", 0.99, 1), _h("good", 0.99, 1)], now=1000 * HOUR) is None
    assert N.veto_decision([], now=1000 * HOUR) is None


def test_confident_good_news_after_the_bad_news_lifts_the_block():
    items = [_h("bad", 0.9, 5), _h("good", 0.9, 1)]
    assert N.veto_decision(items, now=1000 * HOUR) is None
    earlier_good = [_h("good", 0.9, 8), _h("bad", 0.9, 5)]
    assert N.veto_decision(earlier_good, now=1000 * HOUR) is not None          # the good news came first: still blocked


def test_search_results_are_turned_into_clean_headline_records():
    raw = [{"title": "  Albemarle cuts outlook  ", "publisher": "Reuters", "providerPublishTime": 1791892803, "link": "https://x/y", "relatedTickers": ["ALB"]},
           {"title": "", "publisher": "Nobody"}, {"publisher": "No title at all"}]
    out = N.parse_search_items(raw, "ALB", "Albemarle Corporation")
    assert len(out) == 1
    assert out[0] == {"symbol": "ALB", "title": "Albemarle cuts outlook", "publisher": "Reuters", "ts": 1791892803.0, "url": "https://x/y", "specific": True}


def test_reader_answers_are_matched_back_to_their_headlines():
    answers = [{"p": [0.02, 0.96, 0.02]}, {"p": [0.92, 0.06, 0.02]}, {"p": [0.1, 0.2, 0.7]}]
    out = N.label_all([{"title": "a"}, {"title": "b"}, {"title": "c"}], answers)
    assert [(o["label"], o["conf"]) for o in out] == [("bad", 0.96), ("good", 0.92), ("nothing", 0.7)]


def test_a_headline_that_only_restates_a_price_move_is_not_news():
    assert N.is_price_recap("Albemarle (ALB) Dips More Than Broader Market: What You Should Know")
    assert N.is_price_recap("Micron Technology Shares Sink for Fourth Straight Session")
    assert N.is_price_recap("Why Is Lululemon Stock Down Today?")
    assert not N.is_price_recap("Western Digital Shares Fall 3.6% as UBS Cuts Price Target Amid HDD Supply Concerns")   # gives a cause
    assert not N.is_price_recap("Albemarle cuts full-year outlook as lithium prices slide")
    assert not N.is_price_recap("Regulator opens investigation into bank's lending practices")


def test_price_recaps_never_count_towards_a_block():
    raw = [{"title": "Albemarle (ALB) Dips More Than Broader Market: What You Should Know", "relatedTickers": ["ALB"], "providerPublishTime": 5},
           {"title": "Albemarle cuts full-year outlook as lithium prices slide", "relatedTickers": ["ALB"], "providerPublishTime": 6}]
    out = N.parse_search_items(raw, "ALB", "Albemarle Corporation")
    assert [o["specific"] for o in out] == [False, True]


def test_a_weekday_is_not_a_reason():
    assert N.is_price_recap("Why Sandisk Stock Dropped Again on Tuesday")
    assert N.is_price_recap("Nvidia stock slips on Monday")
    assert not N.is_price_recap("Nvidia stock slips on weak guidance")


def test_names_that_are_also_everyday_words_need_the_ticker_or_full_name():
    assert not N.is_specific("Dow Rises 400 Points as Banks Report Earnings", "Dow Inc.", "DOW", ["DOW"])       # that's the index
    assert N.is_specific("Dow Inc. to idle a Texas plant", "Dow Inc.", "DOW", ["DOW"])
    assert N.is_specific("Chemicals maker (NYSE: DOW) cuts dividend", "Dow Inc.", "DOW", ["DOW"])
    assert not N.is_specific("Retailers hit their sales target early", "Target Corporation", "TGT", ["TGT"])
    assert N.is_specific("Target Corporation names new chief executive", "Target Corporation", "TGT", ["TGT"])


def test_a_distinctive_first_name_is_enough_but_a_generic_one_is_not():
    assert N.is_specific("Micron Stocks Fall 4.64% as China's DRAM Push Tests Pricing Power", "Micron Technology", "MU", ["MU"])
    assert N.is_specific("Lululemon lifts its sales forecast", "Lululemon Athletica", "LULU", ["LULU"])
    assert not N.is_specific("Western Union raises its dividend", "Western Digital", "WDC", ["WDC"])
    assert not N.is_specific("American shoppers pull back", "American Express", "AXP", ["AXP"])
    assert not N.is_specific("Micron, Nvidia and AMD lead chip rally", "Micron Technology", "MU", ["MU", "NVDA", "AMD"])   # three companies: a round-up


def test_reader_labels_must_match_the_original_three_class_semantics():
    import pytest
    from nightdesk.reader_sidecar import validate_labels
    validate_labels({"0": "good", "1": "bad", "2": "nothing"})
    for mapping in ({0: "positive", 1: "negative", 2: "neutral"},
                    {0: "bad", 1: "good", 2: "nothing"},
                    {0: "good", 1: "bad", 2: "nothing", 3: "other"}):
        with pytest.raises(ValueError):
            validate_labels(mapping)
