from app.japan_geo import search


def names(text):
    return {entry['canonical_name'] for entry in search(text)}


def test_publisher_domains_and_urls_are_not_location_mentions():
    assert names('9条を守れ 川崎・登戸の長念寺 - tokyo-np.co.jp') == set()
    assert names('公開ニュース https://example.org/Tokyo/Hiroshima') == set()
    assert '東京都' in names('Tokyo peace rally reported by tokyo-np.co.jp')


def test_english_place_aliases_require_word_boundaries():
    assert names('The premier introduced a nationalist peace proposal.') == set()
    assert '三重県' in names('A peace event in Mie.')
    assert '沖縄県' in names('Okinawa peace movement')


def test_china_is_not_automatically_japans_chugoku_region():
    assert names('中国政府と日本政府の対話') == {'日本'}
    assert '中国地方' in names('中国地方の平和運動')
    assert '中国地方' in names('Chūgoku peace activities')
