from app.topics import classify_topics
from app.entities import match_entities


def test_default_taxonomy_classifies_article9_and_okinawa():
    topics = classify_topics("憲法九条と沖縄", "Article 9 and Okinawa US military base peace movement")
    names = {item["topic_name"] for item in topics}
    assert "宪法九条/平和主義" in names
    assert "米軍基地/沖縄" in names


def test_default_taxonomy_does_not_label_unrelated_sport():
    matches = classify_topics("Football match", "The match ended with two goals.")
    assert [item["topic_name"] for item in matches] == ["未分类/待研判"]


def test_default_entity_configuration_is_loadable():
    assert match_entities("Liberal Democratic Party and Kishida Fumio")
