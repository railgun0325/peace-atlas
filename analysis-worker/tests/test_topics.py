from app.topics import classify_topics


def test_classify_defense_topic():
    topics = classify_topics("国防部说明军购进度", "立法院审查国防预算，涉及无人机与战备。")
    names = {item["topic_name"] for item in topics}
    assert "国防" in names
    assert "立法院" in names


def test_classify_cross_strait_topic_traditional():
    topics = classify_topics("陸委會回應兩岸交流", "國台辦表示將持續關注台海局勢。")
    names = {item["topic_name"] for item in topics}
    assert "两岸" in names
